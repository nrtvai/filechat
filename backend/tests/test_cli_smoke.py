from pathlib import Path

from backend.app.notion_client import normalize_notion_id
from backend.app.notion_export import notion_import_bundle
from backend.app.openrouter import _fallback_draft_content
from backend.app.survey import build_survey_artifacts
from backend.app.smoke_runner import create_correlated_business_extreme_fixtures, create_correlated_business_fixtures, normalize_model_id


def test_normalize_model_id_accepts_openrouter_urls():
    assert normalize_model_id("https://openrouter.ai/qwen/qwen3.6-flash") == "qwen/qwen3.6-flash"
    assert normalize_model_id("https://openrouter.ai/models/deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    assert normalize_model_id("moonshotai/kimi-k2.6") == "moonshotai/kimi-k2.6"


def test_normalize_notion_id_accepts_page_urls():
    assert (
        normalize_notion_id("https://www.notion.so/30cfd7d510af807ea1aaf1dc2e18958a")
        == "30cfd7d5-10af-807e-a1aa-f1dc2e18958a"
    )
    assert normalize_notion_id("30cfd7d5-10af-807e-a1aa-f1dc2e18958a") == "30cfd7d5-10af-807e-a1aa-f1dc2e18958a"


def test_correlated_business_fixtures_share_business_keys(tmp_path):
    paths = create_correlated_business_fixtures(tmp_path / "correlated_business")
    names = {path.name for path in paths}

    assert {
        "warehouse_stock_units.csv",
        "sales_orders.csv",
        "purchase_orders.csv",
        "stock_movements.tsv",
        "customer_feedback.csv",
        "monthly_financials.csv",
        "product_catalog.md",
        "operations_roadmap.txt",
    }.issubset(names)
    assert "expected_findings.md" in names
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for key in ("SKU", "HB-100", "HB-500", "KT-400", "Beverage", "South"):
        assert key in combined
    for key in ("stockout-risk", "expected findings"):
        assert key in combined.lower()


def test_correlated_business_fixture_has_multi_period_inventory_ops_data(tmp_path):
    paths = create_correlated_business_fixtures(tmp_path / "correlated_business")
    by_name = {path.name: path for path in paths}

    warehouse_lines = by_name["warehouse_stock_units.csv"].read_text(encoding="utf-8").splitlines()
    sales_lines = by_name["sales_orders.csv"].read_text(encoding="utf-8").splitlines()
    financial_lines = by_name["monthly_financials.csv"].read_text(encoding="utf-8").splitlines()
    first_preview = "\n".join(warehouse_lines[:25])
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()

    assert len(warehouse_lines) > 25
    assert len(sales_lines) > 200
    assert len(financial_lines) > 25
    assert "days_of_cover" in warehouse_lines[0]
    assert "stockout_risk" in warehouse_lines[0]
    assert "high" in first_preview
    assert "survey" not in by_name["expected_findings.md"].read_text(encoding="utf-8").lower()
    assert "personal proficiency" not in combined


def test_correlated_business_extreme_fixture_is_materially_larger(tmp_path):
    default_paths = create_correlated_business_fixtures(tmp_path / "default" / "correlated_business")
    extreme_paths = create_correlated_business_extreme_fixtures(tmp_path / "extreme" / "correlated_business_extreme")

    default_bytes = sum(path.stat().st_size for path in default_paths)
    extreme_bytes = sum(path.stat().st_size for path in extreme_paths)
    default_sales_rows = (tmp_path / "default" / "correlated_business" / "sales_orders.csv").read_text(encoding="utf-8").count("\n")
    extreme_sales_rows = (tmp_path / "extreme" / "correlated_business_extreme" / "sales_orders.csv").read_text(encoding="utf-8").count("\n")

    assert {path.name for path in default_paths} == {path.name for path in extreme_paths}
    assert extreme_bytes > default_bytes * 5
    assert extreme_sales_rows > default_sales_rows * 5


def test_business_fixture_draft_does_not_use_survey_boilerplate(tmp_path):
    [warehouse, *_] = create_correlated_business_fixtures(tmp_path / "correlated_business")
    result = build_survey_artifacts(
        "Draft a Notion-ready operations report with a replenishment table.",
        [{"file_id": "fil_warehouse", "file_name": warehouse.name, "text": warehouse.read_text(encoding="utf-8")}],
        [
            {
                "source_id": 1,
                "file_id": "fil_warehouse",
                "file_name": warehouse.name,
                "chunk_id": "chk_warehouse",
                "location": "chunk 1",
                "content": warehouse.read_text(encoding="utf-8"),
                "excerpt": "HB-100,Seoul-East,Beverage,240,300",
            }
        ],
    )

    draft = next(artifact for artifact in result.artifacts if artifact["kind"] == "file_draft")
    content = draft["content"]

    assert result.tool_call["tool"] == "structured_table_profiler"
    assert "운영 데이터" in draft["caption"]
    assert "공통 키" in content
    assert "개인의 숙련도" not in content
    assert "주관식 응답" not in content


def test_business_evidence_fallback_draft_does_not_use_survey_boilerplate(tmp_path):
    [warehouse, *_] = create_correlated_business_fixtures(tmp_path / "correlated_business")
    result = build_survey_artifacts(
        "Draft a Notion-ready operations report with a replenishment table.",
        [{"file_id": "fil_warehouse", "file_name": warehouse.name, "text": warehouse.read_text(encoding="utf-8")}],
        [
            {
                "source_id": 1,
                "file_id": "fil_warehouse",
                "file_name": warehouse.name,
                "chunk_id": "chk_warehouse",
                "location": "chunk 1",
                "content": warehouse.read_text(encoding="utf-8"),
                "excerpt": "HB-100,Seoul-East,Beverage,240,300",
            }
        ],
    )

    content = _fallback_draft_content(result.evidence_packet)

    assert "첨부 원자료" in content
    assert "공통 키" in content
    assert "개인의 숙련도" not in content
    assert "반복 업무" not in content


def test_notion_import_bundle_includes_datatable_for_chart():
    row = {
        "id": "art_chart",
        "kind": "chart",
        "title": "Revenue by Region",
        "caption": "Grounded chart",
        "source_chunk_ids": ["chk_1"],
    }
    spec = {"values": [{"label": "North", "value": 1470}, {"label": "South", "value": 840}]}

    bundle = notion_import_bundle(row, spec)

    assert bundle["metadata"]["source_artifact_id"] == "art_chart"
    assert bundle["datatable"]["columns"] == ["Label", "Value"]
    assert "North,1470" in bundle["datatable"]["csv"]
    assert "# Revenue by Region" in bundle["markdown"]


def test_notion_import_bundle_extracts_json_render_datatable():
    row = {
        "id": "art_table",
        "kind": "table",
        "title": "Reorder Table",
        "caption": "Grounded table",
        "source_chunk_ids": ["chk_1"],
    }
    spec = {
        "root": "card",
        "elements": {
            "card": {"type": "ArtifactCard", "props": {"title": "Reorder Table"}, "children": ["table"]},
            "table": {
                "type": "DataTable",
                "props": {"columns": ["SKU", "Action"], "rows": [["HB-100", "Reorder"]]},
                "children": [],
            },
        },
    }

    bundle = notion_import_bundle(row, spec)

    assert bundle["datatable"]["columns"] == ["SKU", "Action"]
    assert "HB-100,Reorder" in bundle["datatable"]["csv"]
