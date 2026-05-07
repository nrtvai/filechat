from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import get_settings
from .database import connect, init_db
from .notion_client import NotionPublishError, publish_artifact
from .notion_export import notion_import_bundle
from .openrouter import OpenRouterClient, OpenRouterMissingKey
from .settings_store import set_setting
from .utils import json_loads

DEFAULT_MODELS = [
    "openrouter/owl-alpha",
    "x-ai/grok-4.3",
    "qwen/qwen3.6-flash",
    "deepseek/deepseek-v4-flash",
    "tencent/hy3-preview:free",
    "xiaomi/mimo-v2.5-pro",
    "deepseek/deepseek-v4-pro",
    "moonshotai/kimi-k2.6",
]

PROMPT_MATRIX = [
    ("grounded_qa", "Compare inventory, sales demand, and purchase-order lead times for SKUs at reorder risk."),
    ("bar_chart", "Create a bar chart of units sold by region."),
    ("line_chart", "Create a line chart of monthly revenue and gross margin."),
    ("pie_chart", "Create a pie chart of revenue by channel."),
    ("table", "Create a comparison table of SKU, units on hand, reorder point, supplier, and lead time."),
    ("file_draft", "Draft an executive Markdown report about replenishment priorities and regional demand."),
    ("summary_panel", "Create a summary panel for operational risks."),
    ("decision_cards", "Show decision cards for reorder, discounting, and supplier-expedite options."),
    ("mermaid", "Create a Mermaid flowchart for inventory replenishment workflow."),
    ("timeline", "Create a JSON-rendered timeline from the operations roadmap."),
    ("notion_export", "Draft a Notion-ready operations report."),
]


def normalize_model_id(value: str) -> str:
    cleaned = value.strip()
    match = re.search(r"openrouter\.ai/(?:models/)?(.+)$", cleaned)
    if match:
        cleaned = match.group(1)
    cleaned = re.sub(r"^https?://", "", cleaned)
    return cleaned.strip("/")


class SmokeRunner:
    def __init__(self, *, data_dir: Path, models: list[str] | None = None, prompt_timeout: float = 45.0) -> None:
        self.data_dir = data_dir
        self.models = models or DEFAULT_MODELS
        self.prompt_timeout = prompt_timeout

    async def run(self) -> dict[str, Any]:
        fixture_paths = create_correlated_business_fixtures(self.data_dir / "correlated_business")
        available, metadata_error = await self._available_models()
        report: dict[str, Any] = {
            "status": "completed",
            "fixture_paths": [str(path) for path in fixture_paths],
            "runs": [],
            "metadata_error": metadata_error,
        }
        for model in self.models:
            normalized = normalize_model_id(model)
            if not normalized.startswith("local-") and available is not None and normalized not in available:
                report["runs"].append({"model_id": normalized, "status": "failed", "error": "Model was not present in OpenRouter metadata."})
                continue
            set_setting("chat_model", normalized)
            result = await self._run_model(normalized, fixture_paths)
            report["runs"].append(result)
        if any(run.get("status") == "failed" for run in report["runs"]):
            report["status"] = "completed_with_failures"
        return report

    async def publish_fixture(self, fixture: str, title: str) -> dict[str, Any]:
        if fixture != "correlated_business":
            raise RuntimeError(f"Unknown fixture: {fixture}")
        paths = create_correlated_business_fixtures(self.data_dir / fixture)
        result = await self._run_model("local-notion-publish", paths, prompts=[("file_draft", "Draft a Notion-ready operations report with a replenishment table.")])
        session_id = result.get("session_id")
        if not session_id:
            raise RuntimeError("Smoke fixture did not produce a session.")
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE session_id = ? AND kind = 'file_draft' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            table_row = conn.execute(
                "SELECT * FROM artifacts WHERE session_id = ? AND kind = 'table' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            raise RuntimeError("No file_draft artifact was created for Notion publishing.")
        spec = json_loads(row["spec_json"], {})
        try:
            notion = await publish_artifact(dict(row), spec, title=title)
            if table_row and not notion.get("database_id"):
                table_notion = await publish_artifact(dict(table_row), json_loads(table_row["spec_json"], {}), title=f"{title} Data")
                notion["table_page_id"] = table_notion.get("page_id")
                notion["database_id"] = table_notion.get("database_id")
                notion["row_page_ids"] = table_notion.get("row_page_ids", [])
        except NotionPublishError as exc:
            raise RuntimeError(str(exc)) from exc
        return {"status": "published", "fixture": fixture, "session_id": session_id, "notion": notion}

    async def _run_model(
        self,
        model: str,
        fixture_paths: list[Path],
        prompts: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        previous_fake = os.environ.get("FILECHAT_ALLOW_FAKE_OPENROUTER")
        if model.startswith("local-"):
            os.environ["FILECHAT_ALLOW_FAKE_OPENROUTER"] = "true"
            get_settings.cache_clear()
        from .cli import export_message_artifacts, upload_paths
        from .retrieval import answer
        from .utils import new_id, now

        init_db()
        session_id = new_id("ses")
        stamp = now()
        with connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, organization_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, f"Smoke {model}", "org_single", "usr_single", stamp, stamp),
            )
        try:
            files = await upload_paths(session_id, fixture_paths)
            ready = all(file.get("status") == "ready" for file in files)
            runs: list[dict[str, Any]] = []
            for prompt_id, prompt in prompts or PROMPT_MATRIX:
                item: dict[str, Any] = {"prompt_id": prompt_id, "prompt": prompt}
                try:
                    message_id = await asyncio.wait_for(answer(session_id, prompt), timeout=self.prompt_timeout)
                    with connect() as conn:
                        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
                        artifacts = conn.execute("SELECT * FROM artifacts WHERE message_id = ?", (message_id,)).fetchall()
                        citations = conn.execute("SELECT * FROM citations WHERE message_id = ?", (message_id,)).fetchall()
                    artifact_payloads = [dict(row) for row in artifacts]
                    for artifact in artifact_payloads:
                        artifact["spec"] = json_loads(artifact.pop("spec_json"), {})
                        artifact["source_chunk_ids"] = json_loads(artifact.get("source_chunk_ids"), [])
                    exports = export_message_artifacts(
                        {
                            "artifacts": [
                                {
                                    "id": artifact["id"],
                                    "session_id": artifact["session_id"],
                                }
                                for artifact in artifact_payloads
                            ]
                        },
                        self.data_dir / "exports" / model.replace("/", "_") / prompt_id,
                    )
                    item.update(
                        {
                            "status": "passed",
                            "message_id": message_id,
                            "artifact_kinds": [artifact["kind"] for artifact in artifact_payloads],
                            "chart_types": [artifact["spec"].get("chart_type") for artifact in artifact_payloads if artifact["kind"] == "chart"],
                            "citation_count": len(citations),
                            "exported_file_paths": exports,
                        }
                    )
                    if prompt_id == "notion_export" and artifact_payloads:
                        draft = next((artifact for artifact in artifact_payloads if artifact["kind"] == "file_draft"), artifact_payloads[0])
                        item["notion_import_bundle"] = notion_import_bundle(draft, draft["spec"])["metadata"]
                    if message and not artifacts and prompt_id not in {"grounded_qa"}:
                        item["status"] = "failed"
                        item["error"] = "Prompt did not produce an artifact."
                except TimeoutError:
                    item.update({"status": "failed", "error": f"Timed out after {self.prompt_timeout:g} seconds."})
                except asyncio.TimeoutError:
                    item.update({"status": "failed", "error": f"Timed out after {self.prompt_timeout:g} seconds."})
                except Exception as exc:
                    item.update({"status": "failed", "error": str(exc)})
                runs.append(item)
            status = "passed" if ready and all(item["status"] == "passed" for item in runs) else "failed"
            return {"model_id": model, "session_id": session_id, "status": status, "files_ready": ready, "runs": runs}
        finally:
            if model.startswith("local-"):
                if previous_fake is None:
                    os.environ.pop("FILECHAT_ALLOW_FAKE_OPENROUTER", None)
                else:
                    os.environ["FILECHAT_ALLOW_FAKE_OPENROUTER"] = previous_fake
                get_settings.cache_clear()

    async def _available_models(self) -> tuple[set[str] | None, str | None]:
        if get_settings().filechat_allow_fake_openrouter:
            return None, None
        try:
            models = await OpenRouterClient().models("chat")
        except OpenRouterMissingKey as exc:
            return None, str(exc)
        except Exception as exc:
            return None, f"Could not fetch OpenRouter metadata: {exc}"
        return {str(item.get("id")) for item in models}, None


def create_correlated_business_fixtures(target_dir: Path) -> list[Path]:
    """Create the standard deterministic inventory-ops smoke fixture."""

    return _write_correlated_business_fixture(target_dir, variant="default")


def create_correlated_business_extreme_fixtures(target_dir: Path) -> list[Path]:
    """Create the larger deterministic inventory-ops fixture for manual limit tests."""

    return _write_correlated_business_fixture(target_dir, variant="extreme")


def _write_correlated_business_fixture(target_dir: Path, *, variant: str) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    files = _correlated_business_files(variant)
    paths: list[Path] = []
    for name, content in files:
        path = target_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def _correlated_business_files(variant: str) -> list[tuple[str, str]]:
    scale = _fixture_scale(variant)
    skus = _fixture_skus(scale["sku_count"])
    warehouses = _fixture_warehouses(scale["warehouse_count"])
    regions = ["North", "Central", "South", "West", "Online"]
    channels = ["Retail", "Marketplace", "Wholesale", "Distributor", "Subscription"]

    return [
        ("warehouse_stock_units.csv", _warehouse_stock_units_csv(skus, warehouses)),
        ("sales_orders.csv", _sales_orders_csv(skus, regions, channels, scale["sales_months"], scale["orders_per_sku_month"])),
        ("purchase_orders.csv", _purchase_orders_csv(skus, scale["po_cycles"])),
        ("stock_movements.tsv", _stock_movements_tsv(skus, warehouses, scale["movement_days"])),
        ("customer_feedback.csv", _customer_feedback_csv(skus, regions, channels, scale["feedback_cycles"])),
        ("monthly_financials.csv", _monthly_financials_csv(skus, channels, scale["sales_months"])),
        ("supplier_scorecards.csv", _supplier_scorecards_csv(skus, scale["supplier_periods"])),
        ("regional_demand_forecast.csv", _regional_demand_forecast_csv(skus, regions, scale["forecast_months"])),
        ("promotion_calendar.csv", _promotion_calendar_csv(skus, regions, channels, scale["promotion_cycles"])),
        ("product_catalog.md", _product_catalog_md(skus, variant)),
        ("operations_roadmap.txt", _operations_roadmap_txt(variant)),
        ("expected_findings.md", _expected_findings_md(variant)),
    ]


def _fixture_scale(variant: str) -> dict[str, int]:
    if variant == "default":
        return {
            "sku_count": 8,
            "warehouse_count": 5,
            "sales_months": 12,
            "orders_per_sku_month": 3,
            "po_cycles": 8,
            "movement_days": 28,
            "feedback_cycles": 8,
            "supplier_periods": 6,
            "forecast_months": 6,
            "promotion_cycles": 2,
        }
    if variant == "extreme":
        return {
            "sku_count": 24,
            "warehouse_count": 12,
            "sales_months": 18,
            "orders_per_sku_month": 7,
            "po_cycles": 22,
            "movement_days": 90,
            "feedback_cycles": 24,
            "supplier_periods": 18,
            "forecast_months": 12,
            "promotion_cycles": 5,
        }
    raise ValueError(f"Unknown correlated business fixture variant: {variant}")


def _fixture_skus(count: int) -> list[dict[str, Any]]:
    base = [
        {
            "sku": "HB-100",
            "name": "Core Hydration Beverage",
            "category": "Beverage",
            "supplier": "Han River Bottling",
            "unit_cost": 2.40,
            "list_price": 3.80,
            "base_units": 92,
            "risk": "stockout",
            "action": "Expedite 900 units and reserve North retail allocation",
            "expiry": "2026-07-31",
        },
        {
            "sku": "HB-500",
            "name": "Seasonal Citrus Beverage",
            "category": "Beverage",
            "supplier": "Daegu Beverage Lab",
            "unit_cost": 2.85,
            "list_price": 4.10,
            "base_units": 78,
            "risk": "expiry",
            "action": "Run controlled South marketplace promotion before June expiry",
            "expiry": "2026-05-30",
        },
        {
            "sku": "KT-400",
            "name": "Campaign Assembly Kit",
            "category": "Kit",
            "supplier": "Incheon Assembly",
            "unit_cost": 8.75,
            "list_price": 12.80,
            "base_units": 34,
            "risk": "supplier_delay",
            "action": "Split build with secondary assembler and protect wholesale commitments",
            "expiry": "2026-12-31",
        },
        {
            "sku": "SN-300",
            "name": "Premium Seaweed Snack",
            "category": "Snack",
            "supplier": "Busan Fresh Co",
            "unit_cost": 3.10,
            "list_price": 5.10,
            "base_units": 58,
            "risk": "freshness",
            "action": "Replenish Busan-Cold and rotate lots by expiry date",
            "expiry": "2026-06-20",
        },
        {
            "sku": "HB-200",
            "name": "Value Hydration Beverage",
            "category": "Beverage",
            "supplier": "West Pack Foods",
            "unit_cost": 1.90,
            "list_price": 3.35,
            "base_units": 66,
            "risk": "margin_pressure",
            "action": "Reduce blanket discounts and move demand to subscription bundles",
            "expiry": "2026-09-15",
        },
        {
            "sku": "SN-700",
            "name": "Protein Trail Snack",
            "category": "Snack",
            "supplier": "Jeju Nutri Foods",
            "unit_cost": 4.20,
            "list_price": 6.90,
            "base_units": 42,
            "risk": "regional_demand",
            "action": "Shift inventory from West to Central distributor accounts",
            "expiry": "2026-08-25",
        },
        {
            "sku": "CP-250",
            "name": "Cold Brew Concentrate Pack",
            "category": "Beverage",
            "supplier": "Seoul Cold Chain",
            "unit_cost": 5.60,
            "list_price": 8.95,
            "base_units": 36,
            "risk": "cold_chain",
            "action": "Keep Online channel on two-day delivery windows",
            "expiry": "2026-06-10",
        },
        {
            "sku": "KT-900",
            "name": "Enterprise Launch Kit",
            "category": "Kit",
            "supplier": "Incheon Assembly",
            "unit_cost": 12.30,
            "list_price": 18.75,
            "base_units": 24,
            "risk": "churn",
            "action": "Prioritize subscription customers with renewal risk",
            "expiry": "2027-02-28",
        },
    ]
    extra_categories = ["Beverage", "Snack", "Kit", "Accessory"]
    extra_suppliers = ["Han River Bottling", "West Pack Foods", "Busan Fresh Co", "Incheon Assembly", "Daegu Beverage Lab", "Jeju Nutri Foods"]
    while len(base) < count:
        idx = len(base) + 1
        category = extra_categories[idx % len(extra_categories)]
        prefix = {"Beverage": "HB", "Snack": "SN", "Kit": "KT", "Accessory": "AC"}[category]
        base.append(
            {
                "sku": f"{prefix}-{800 + idx * 7}",
                "name": f"Extended {category} Item {idx}",
                "category": category,
                "supplier": extra_suppliers[idx % len(extra_suppliers)],
                "unit_cost": round(1.7 + (idx % 9) * 0.65, 2),
                "list_price": round(3.2 + (idx % 11) * 1.05, 2),
                "base_units": 25 + (idx % 8) * 9,
                "risk": ["stockout", "supplier_delay", "margin_pressure", "regional_demand", "churn"][idx % 5],
                "action": "Use forecasted cover and supplier scorecard to rebalance before the next campaign",
                "expiry": f"2026-{((idx % 8) + 5):02d}-{((idx * 3) % 20) + 8:02d}",
            }
        )
    return base[:count]


def _fixture_warehouses(count: int) -> list[dict[str, str]]:
    base = [
        {"warehouse": "Seoul-East", "region": "North", "temperature_zone": "Ambient"},
        {"warehouse": "Seoul-West", "region": "Central", "temperature_zone": "Ambient"},
        {"warehouse": "Busan-Cold", "region": "South", "temperature_zone": "Cold"},
        {"warehouse": "Incheon-Dry", "region": "North", "temperature_zone": "Dry"},
        {"warehouse": "Daegu-Fast", "region": "South", "temperature_zone": "Ambient"},
        {"warehouse": "Daejeon-Crossdock", "region": "Central", "temperature_zone": "Dry"},
        {"warehouse": "Gwangju-Flex", "region": "West", "temperature_zone": "Ambient"},
        {"warehouse": "Jeju-Cold", "region": "South", "temperature_zone": "Cold"},
        {"warehouse": "Suwon-Returns", "region": "North", "temperature_zone": "Dry"},
        {"warehouse": "Online-Microhub", "region": "Online", "temperature_zone": "Ambient"},
        {"warehouse": "Ulsan-Bulk", "region": "South", "temperature_zone": "Dry"},
        {"warehouse": "Wonju-Reserve", "region": "Central", "temperature_zone": "Ambient"},
    ]
    return base[:count]


def _warehouse_stock_units_csv(skus: list[dict[str, Any]], warehouses: list[dict[str, str]]) -> str:
    rows = [
        "SKU,warehouse,region,category,units_on_hand,reorder_point,avg_daily_demand,days_of_cover,stockout_risk,unit_cost,expiry_date,recommended_action"
    ]
    risk_order = ["HB-100", "HB-500", "KT-400", "SN-300", "HB-200"]
    ordered_skus = sorted(skus, key=lambda item: risk_order.index(item["sku"]) if item["sku"] in risk_order else len(risk_order))
    for sku_index, sku in enumerate(ordered_skus):
        for warehouse_index, warehouse in enumerate(warehouses):
            base = int(sku["base_units"])
            demand = max(6, int(base * (1.25 if warehouse["region"] in {"North", "South"} else 0.95)) + warehouse_index * 2)
            risk_multiplier = 0.62 if sku["sku"] in {"HB-100", "HB-500", "KT-400"} and warehouse_index < 3 else 1.8
            units_on_hand = int(demand * risk_multiplier) + (sku_index % 4) * 9
            reorder_point = int(demand * (4.2 if sku["sku"] in {"HB-100", "HB-500", "KT-400"} else 3.0))
            days_of_cover = round(units_on_hand / demand, 1)
            stockout_risk = "high" if days_of_cover < 3.0 or units_on_hand < reorder_point else "medium" if days_of_cover < 5.0 else "low"
            rows.append(
                ",".join(
                    [
                        sku["sku"],
                        warehouse["warehouse"],
                        warehouse["region"],
                        sku["category"],
                        str(units_on_hand),
                        str(reorder_point),
                        str(demand),
                        f"{days_of_cover:.1f}",
                        stockout_risk,
                        f"{sku['unit_cost']:.2f}",
                        sku["expiry"],
                        _csv_text(sku["action"]),
                    ]
                )
            )
    return "\n".join(rows) + "\n"


def _sales_orders_csv(skus: list[dict[str, Any]], regions: list[str], channels: list[str], month_count: int, orders_per_sku_month: int) -> str:
    rows = ["order_id,SKU,channel,region,units_sold,revenue,discount_rate,gross_margin_rate,order_date,promotion_code"]
    start = date(2025, 7, 1)
    order_number = 1001
    for month_index in range(month_count):
        month_start = _add_months(start, month_index)
        for sku_index, sku in enumerate(skus):
            for order_index in range(orders_per_sku_month):
                channel = channels[(sku_index + order_index + month_index) % len(channels)]
                region = regions[(sku_index * 2 + order_index + month_index) % len(regions)]
                demand_lift = 1.45 if sku["sku"] in {"HB-100", "HB-500"} and region in {"North", "South"} else 1.0
                promo_lift = 1.25 if sku["sku"] == "HB-500" and channel == "Marketplace" else 1.0
                units = int((sku["base_units"] + 8 * order_index + month_index * 3) * demand_lift * promo_lift)
                discount = 0.16 if sku["sku"] in {"HB-500", "HB-200"} and channel in {"Marketplace", "Retail"} else 0.05 + ((sku_index + order_index) % 4) * 0.02
                revenue = units * float(sku["list_price"]) * (1 - discount)
                gross_margin_rate = (revenue - units * float(sku["unit_cost"])) / revenue
                promotion_code = "SPRING-PUSH" if discount >= 0.14 else "BASELINE"
                rows.append(
                    ",".join(
                        [
                            f"SO-{order_number}",
                            sku["sku"],
                            channel,
                            region,
                            str(units),
                            f"{revenue:.2f}",
                            f"{discount:.2f}",
                            f"{gross_margin_rate:.3f}",
                            (month_start + timedelta(days=(order_index * 7 + sku_index) % 27)).isoformat(),
                            promotion_code,
                        ]
                    )
                )
                order_number += 1
    return "\n".join(rows) + "\n"


def _purchase_orders_csv(skus: list[dict[str, Any]], po_cycles: int) -> str:
    rows = ["purchase_order_id,SKU,supplier,lead_time_days,lead_time_variance_days,units_ordered,expected_arrival,delay_risk,expedite_required"]
    start = date(2026, 1, 7)
    number = 5001
    for cycle in range(po_cycles):
        for sku_index, sku in enumerate(skus):
            baseline = 7 + (sku_index % 5) * 3
            variance = 8 if sku["sku"] == "KT-400" and cycle % 2 == 0 else 5 if sku["sku"] in {"HB-100", "HB-500"} and cycle % 3 == 0 else (cycle + sku_index) % 4
            lead_time = baseline + variance
            units = int(sku["base_units"]) * (8 + (cycle % 4)) + sku_index * 15
            risk = "high" if lead_time >= 18 or variance >= 6 else "medium" if lead_time >= 13 else "low"
            rows.append(
                ",".join(
                    [
                        f"PO-{number}",
                        sku["sku"],
                        _csv_text(sku["supplier"]),
                        str(lead_time),
                        str(variance),
                        str(units),
                        (start + timedelta(days=cycle * 14 + lead_time)).isoformat(),
                        risk,
                        "yes" if sku["sku"] in {"HB-100", "KT-400"} and risk == "high" else "no",
                    ]
                )
            )
            number += 1
    return "\n".join(rows) + "\n"


def _stock_movements_tsv(skus: list[dict[str, Any]], warehouses: list[dict[str, str]], movement_days: int) -> str:
    rows = ["SKU\tdate\tmovement_type\tquantity\twarehouse\tregion\treason_code"]
    start = date(2026, 3, 1)
    movement_types = ["shipment", "receipt", "shipment", "return", "adjustment"]
    for day_index in range(movement_days):
        for sku_index, sku in enumerate(skus):
            warehouse = warehouses[(day_index + sku_index) % len(warehouses)]
            movement = movement_types[(day_index + sku_index) % len(movement_types)]
            quantity = int(sku["base_units"]) + (day_index % 9) * 4
            if movement in {"shipment", "adjustment"}:
                quantity = -quantity
            reason = "promo_drawdown" if sku["sku"] == "HB-500" and movement == "shipment" else "routine_cycle"
            rows.append(
                "\t".join(
                    [
                        sku["sku"],
                        (start + timedelta(days=day_index)).isoformat(),
                        movement,
                        str(quantity),
                        warehouse["warehouse"],
                        warehouse["region"],
                        reason,
                    ]
                )
            )
    return "\n".join(rows) + "\n"


def _customer_feedback_csv(skus: list[dict[str, Any]], regions: list[str], channels: list[str], feedback_cycles: int) -> str:
    rows = ["feedback_id,SKU,customer_segment,channel,region,rating,churn_risk,feedback_text"]
    segment_by_channel = {
        "Retail": "Retail buyers",
        "Marketplace": "Marketplace subscribers",
        "Wholesale": "Wholesale operators",
        "Distributor": "Regional distributors",
        "Subscription": "Subscription customers",
    }
    number = 7001
    for cycle in range(feedback_cycles):
        for sku_index, sku in enumerate(skus):
            channel = channels[(cycle + sku_index) % len(channels)]
            region = regions[(cycle * 2 + sku_index) % len(regions)]
            high_risk = sku["sku"] in {"KT-400", "KT-900"} and channel in {"Wholesale", "Subscription"}
            rating = 2 if high_risk else 3 if sku["risk"] in {"stockout", "expiry", "margin_pressure"} and cycle % 3 == 0 else 4
            churn = "high" if rating <= 2 else "medium" if rating == 3 else "low"
            issue = _feedback_issue(sku["sku"], sku["risk"], region)
            rows.append(
                ",".join(
                    [
                        f"FB-{number}",
                        sku["sku"],
                        _csv_text(segment_by_channel[channel]),
                        channel,
                        region,
                        str(rating),
                        churn,
                        _csv_text(issue),
                    ]
                )
            )
            number += 1
    return "\n".join(rows) + "\n"


def _monthly_financials_csv(skus: list[dict[str, Any]], channels: list[str], month_count: int) -> str:
    rows = ["month,channel,revenue,cogs,gross_margin,operating_expense,return_rate,margin_pressure_flag"]
    start = date(2025, 7, 1)
    for month_index in range(month_count):
        month = _add_months(start, month_index).strftime("%Y-%m")
        for channel_index, channel in enumerate(channels):
            revenue = 28000 + month_index * 1700 + channel_index * 3900
            if channel == "Marketplace":
                revenue += 8500 + month_index * 650
            cogs = int(revenue * (0.56 + (0.05 if channel == "Marketplace" and (month_index % 4 == 0 or month_index >= month_count - 4) else 0.0)))
            expense = 6900 + channel_index * 700 + month_index * 180
            returns = 0.028 + channel_index * 0.006 + (0.015 if channel == "Subscription" and month_index >= month_count - 3 else 0.0)
            margin = revenue - cogs
            flag = "yes" if margin / revenue < 0.38 or returns > 0.055 or (channel == "Marketplace" and month_index % 4 == 0) else "no"
            rows.append(",".join([month, channel, str(revenue), str(cogs), str(margin), str(expense), f"{returns:.3f}", flag]))
    return "\n".join(rows) + "\n"


def _supplier_scorecards_csv(skus: list[dict[str, Any]], periods: int) -> str:
    rows = ["period,supplier,SKU,on_time_rate,avg_delay_days,quality_incidents,fill_rate,scorecard_action"]
    seen: set[tuple[str, str]] = set()
    supplier_skus = []
    for sku in skus:
        key = (sku["supplier"], sku["sku"])
        if key not in seen:
            supplier_skus.append(sku)
            seen.add(key)
    for period in range(periods):
        label = f"2026-W{period + 5:02d}"
        for sku_index, sku in enumerate(supplier_skus):
            delay = 8 if sku["sku"] == "KT-400" and period % 2 == 0 else 5 if sku["sku"] == "HB-100" and period % 3 == 0 else (period + sku_index) % 3
            on_time = max(0.62, 0.96 - delay * 0.035)
            incidents = 2 if sku["sku"] in {"SN-300", "CP-250"} and period % 4 == 0 else 0
            fill_rate = max(0.74, 0.98 - delay * 0.02 - incidents * 0.03)
            action = "expedite and qualify backup supplier" if delay >= 6 else "monitor" if delay >= 3 else "standard cadence"
            rows.append(",".join([label, _csv_text(sku["supplier"]), sku["sku"], f"{on_time:.3f}", str(delay), str(incidents), f"{fill_rate:.3f}", _csv_text(action)]))
    return "\n".join(rows) + "\n"


def _regional_demand_forecast_csv(skus: list[dict[str, Any]], regions: list[str], months: int) -> str:
    rows = ["forecast_month,SKU,region,baseline_units,forecast_units,promotion_lift,stockout_probability,recommended_allocation_units"]
    start = date(2026, 5, 1)
    for month_index in range(months):
        month = _add_months(start, month_index).strftime("%Y-%m")
        for sku_index, sku in enumerate(skus):
            for region_index, region in enumerate(regions):
                baseline = int(sku["base_units"]) * (9 + region_index)
                lift = 0.28 if sku["sku"] == "HB-500" and region == "South" else 0.22 if sku["sku"] == "HB-100" and region == "North" else 0.06 + (month_index % 3) * 0.02
                forecast = int(baseline * (1 + lift) + month_index * 12)
                stockout_probability = min(0.82, 0.18 + lift + (0.20 if sku["sku"] in {"HB-100", "KT-400"} else 0.0))
                allocation = int(forecast * (1.18 if stockout_probability > 0.5 else 1.05))
                rows.append(",".join([month, sku["sku"], region, str(baseline), str(forecast), f"{lift:.2f}", f"{stockout_probability:.2f}", str(allocation)]))
    return "\n".join(rows) + "\n"


def _promotion_calendar_csv(skus: list[dict[str, Any]], regions: list[str], channels: list[str], cycles: int) -> str:
    rows = ["promotion_id,SKU,channel,region,start_date,end_date,discount_rate,expected_lift,margin_watch,notes"]
    start = date(2026, 4, 1)
    number = 9001
    promoted = [sku for sku in skus if sku["risk"] in {"expiry", "margin_pressure", "regional_demand", "stockout"}]
    for cycle in range(cycles):
        for sku_index, sku in enumerate(promoted):
            channel = channels[(cycle + sku_index) % len(channels)]
            region = regions[(sku_index + cycle * 2) % len(regions)]
            discount = 0.18 if sku["sku"] in {"HB-500", "HB-200"} else 0.08 + (sku_index % 3) * 0.03
            lift = 0.35 if sku["sku"] == "HB-500" and region == "South" else 0.18 + (cycle % 3) * 0.04
            rows.append(
                ",".join(
                    [
                        f"PR-{number}",
                        sku["sku"],
                        channel,
                        region,
                        (start + timedelta(days=cycle * 21 + sku_index)).isoformat(),
                        (start + timedelta(days=cycle * 21 + sku_index + 10)).isoformat(),
                        f"{discount:.2f}",
                        f"{lift:.2f}",
                        "yes" if discount >= 0.16 else "no",
                        _csv_text("Promotion should clear risk inventory without starving high-demand stores"),
                    ]
                )
            )
            number += 1
    return "\n".join(rows) + "\n"


def _product_catalog_md(skus: list[dict[str, Any]], variant: str) -> str:
    lines = [
        "# Product Catalog",
        "",
        "Inventory Ops catalog linking SKU strategy, supplier ownership, demand pattern, and operational risk.",
        "",
        "| SKU | Product | Category | Supplier | Risk signal | Recommended action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sku in skus:
        lines.append(
            f"| {sku['sku']} | {sku['name']} | {sku['category']} | {sku['supplier']} | {sku['risk']} | {sku['action']} |"
        )
    if variant == "extreme":
        lines.extend(
            [
                "",
                "## Extended Assortment Notes",
                "",
                "The extreme fixture repeats the same schema family with more SKUs, warehouses, incident cycles, and promotion windows.",
                "Retrieval should still surface HB-100 stockout risk, HB-500 expiry promotion effects, KT-400 supplier delay, and HB-200 margin pressure.",
            ]
        )
        for i in range(1, 25):
            lines.append(f"- Executive memo {i}: compare forecasted allocation, supplier scorecards, returns, and margin flags before committing campaign inventory.")
    return "\n".join(lines) + "\n"


def _operations_roadmap_txt(variant: str) -> str:
    lines = [
        "2026-05-06: Lock reorder decisions for HB-100, HB-500, SN-300 based on days of cover and regional forecast.",
        "2026-05-10: Confirm expedited supplier slots for KT-400 campaign kits and qualify a backup assembler.",
        "2026-05-16: Review inbound HB-500 inventory against South marketplace promotion demand and expiry risk.",
        "2026-05-20: Reduce HB-200 blanket marketplace discounts if gross margin remains below the 38 percent watch line.",
        "2026-05-28: Complete Incheon KT-400 arrival and wholesale allocation.",
        "2026-06-03: Present gross-margin, churn-risk, and stockout-risk review to leadership.",
    ]
    if variant == "extreme":
        for week in range(7, 31):
            lines.append(
                f"2026-{5 + week // 4:02d}-{(week * 3) % 27 + 1:02d}: Extreme-fixture memo week {week} requires reconciling supplier incidents, promotion lift, returns, and regional allocation exceptions."
            )
    return "\n".join(lines) + "\n"


def _expected_findings_md(variant: str) -> str:
    lines = [
        "# Expected Findings",
        "",
        "These are the canonical conclusions for validating plausible reports, charts, tables, and summary panels.",
        "",
        "- HB-100 is the primary stockout-risk SKU: North demand is elevated, days of cover is below reorder policy in the first warehouse rows, and Han River Bottling needs expedited replenishment.",
        "- HB-500 has a combined expiry and promotion-effect story: South marketplace demand rises under SPRING-PUSH, but discounting must clear inventory before the 2026-05-30 expiry date.",
        "- KT-400 has supplier-delay exposure: Incheon Assembly shows high lead-time variance and wholesale customers carry high churn risk when campaign kits miss delivery windows.",
        "- HB-200 is the margin-pressure SKU: marketplace discounts and higher COGS push the gross-margin watch flag in monthly financials.",
        "- SN-300 is freshness-sensitive: Busan-Cold replenishment and lot rotation should appear in operational recommendations.",
        "- Charts should be able to show units sold by region, revenue by channel, monthly revenue and gross margin, supplier delay by SKU, and stockout probability by forecast month.",
        "- Recommended actions should include expediting HB-100, controlled promotion for HB-500, backup assembly capacity for KT-400, discount discipline for HB-200, and freshness rotation for SN-300.",
    ]
    if variant == "extreme":
        lines.extend(
            [
                "",
                "Extreme variant retrieval anchors:",
                "- The same five canonical risks remain intentionally repeated in narrative documents so retrieval can validate conclusions even when large tables exceed preview limits.",
                "- Thousands of sales, movement, feedback, and purchase-order rows are expected; reports should cite the expected findings or narrative files when table chunks are too large.",
            ]
        )
    return "\n".join(lines) + "\n"


def _feedback_issue(sku: str, risk: str, region: str) -> str:
    if sku == "HB-100":
        return f"{sku} sells through quickly in {region}, but stockouts are frequent before replenishment lands."
    if sku == "HB-500":
        return f"{sku} discounts helped demand in {region}, but buyers notice short expiry windows."
    if sku == "KT-400":
        return f"{sku} lead times are too long for campaign bundles and may trigger wholesale churn."
    if sku == "HB-200":
        return f"{sku} price promotions are visible, but margin pressure is limiting service investment."
    return f"{sku} has a {risk} signal that operations should monitor in {region}."


def _csv_text(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"' if "," in escaped or " " in escaped else escaped


def _add_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    return date(year, month, min(value.day, 28))
