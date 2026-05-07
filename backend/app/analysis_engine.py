from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .survey import ParsedTable, parse_table


@dataclass
class ColumnProfile:
    name: str
    normalized: str
    role: str
    semantic: str
    non_empty: int
    unique: int
    numeric_count: int
    sample_values: list[str]


@dataclass
class AnalyzedTable:
    table: ParsedTable
    source: dict[str, Any]
    columns: list[ColumnProfile]


IDENTIFIER_TERMS = ("id", "uuid", "identifier", "sku", "email", "phone", "postal", "zip")
DATE_TERMS = ("date", "time", "month", "quarter", "year", "week", "period", "day")
RATE_TERMS = ("rate", "ratio", "percent", "percentage", "probability", "risk", "lift")
TARGET_TERMS = ("target", "goal", "plan", "budget", "quota", "baseline", "benchmark", "reorder_point")
VALUE_TERMS = (
    "revenue",
    "sales",
    "gross_margin",
    "margin",
    "profit",
    "amount",
    "units",
    "forecast",
    "demand",
    "allocation",
    "stock",
    "cost",
    "score",
    "count",
    "quantity",
    "qty",
)
TEXT_THEME_TERMS = ("comment", "feedback", "note", "notes", "description", "text", "message", "response")
STAGE_TERMS = ("stage", "status", "step", "phase", "funnel")


def build_insight_brief(question: str, file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic, consulting-style analytical brief from table-like sources."""
    analyzed_tables = _analyzed_tables(file_texts, sources)
    insights: list[dict[str, Any]] = []
    for analyzed in analyzed_tables:
        insights.extend(_trend_insights(analyzed))
        insights.extend(_variance_bridge_insights(analyzed))
        insights.extend(_pareto_insights(analyzed))
        insights.extend(_segmentation_insights(analyzed))
        insights.extend(_outlier_insights(analyzed))
        insights.extend(_correlation_portfolio_insights(analyzed))
        insights.extend(_heatmap_insights(analyzed))
        insights.extend(_funnel_insights(analyzed))
        insights.extend(_target_progress_insights(analyzed))
        insights.extend(_cohort_like_insights(analyzed))
        insights.extend(_text_theme_insights(analyzed))

    reviewed = [_review_insight(insight) for insight in insights]
    valid_insights = [insight for insight in reviewed if insight is not None]
    valid_insights = sorted(
        valid_insights,
        key=lambda item: (float(item.get("confidence") or 0) + _framework_priority(str(item.get("framework") or "")), float(item.get("impact_score") or 0)),
        reverse=True,
    )
    for rank, insight in enumerate(valid_insights, start=1):
        insight["rank"] = rank

    warnings = []
    if insights and not valid_insights:
        warnings.append("Deterministic analysis rejected all candidate insights during quality review.")
    elif not insights and analyzed_tables:
        warnings.append("Tables were profiled, but no high-confidence analytical pattern was detected.")

    return {
        "version": 1,
        "request": question,
        "summary": _brief_summary(valid_insights, analyzed_tables),
        "tables": [_public_table(table) for table in analyzed_tables],
        "metric_catalog": _metric_catalog(analyzed_tables),
        "frameworks_run": [
            "trend",
            "variance_bridge",
            "pareto",
            "segmentation",
            "outliers",
            "correlation_portfolio",
            "heatmap_matrix",
            "funnel_dropoff",
            "target_progress",
            "cohort_retention",
            "text_themes",
        ],
        "insights": valid_insights[:12],
        "quality_review": {
            "passed": bool(valid_insights) or not analyzed_tables,
            "warnings": warnings,
            "rejected_rules": [
                "identifier_as_measure",
                "unsupported_causality",
                "generic_summary",
                "chart_measure_mismatch",
            ],
        },
    }


def _analyzed_tables(file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[AnalyzedTable]:
    out: list[AnalyzedTable] = []
    for item in file_texts:
        table = parse_table(str(item.get("text") or ""), str(item.get("file_id") or ""), str(item.get("file_name") or "table.csv"))
        if not table:
            continue
        source = _source_for_file(table.file_id, sources)
        if not source:
            continue
        out.append(AnalyzedTable(table=table, source=source, columns=[_profile_column(table, column) for column in table.columns]))
    return out


def _profile_column(table: ParsedTable, column: str) -> ColumnProfile:
    values = [row.get(column, "").strip() for row in table.rows]
    non_empty_values = [value for value in values if value]
    normalized = _normalize(column)
    numeric_count = sum(1 for value in non_empty_values if _number(value) is not None)
    unique = len(set(non_empty_values))
    role = "empty"
    semantic = "unknown"
    if non_empty_values:
        if _looks_date(normalized, non_empty_values):
            role = "date"
            semantic = "date"
        elif _looks_identifier(normalized, non_empty_values, numeric_count):
            role = "identifier"
            semantic = "identifier"
        elif numeric_count / max(1, len(non_empty_values)) >= 0.75:
            role = "measure"
            semantic = _measure_semantic(normalized, non_empty_values)
        elif unique <= max(3, min(16, len(non_empty_values) // 2)) and _avg_len(non_empty_values) <= 56:
            role = "category"
            semantic = "stage" if any(term in normalized for term in STAGE_TERMS) else "category"
        else:
            role = "text"
            semantic = "text_theme" if any(term in normalized for term in TEXT_THEME_TERMS) else "text"
    return ColumnProfile(
        name=column,
        normalized=normalized,
        role=role,
        semantic=semantic,
        non_empty=len(non_empty_values),
        unique=unique,
        numeric_count=numeric_count,
        sample_values=non_empty_values[:5],
    )


def _trend_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    date_column = _first_column(analyzed, "date")
    measure = _best_measure(analyzed, prefer=("forecast", "revenue", "units", "sales", "margin", "demand", "count"))
    if not date_column or not measure:
        return []
    points = _aggregate(analyzed.table, date_column.name, measure.name)
    if len(points) < 3:
        return []
    values = [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in points[:36]]
    first = values[0]["value"]
    last = values[-1]["value"]
    delta = last - first
    pct = delta / abs(first) if first else 0.0
    direction = "rise" if delta > 0 else "fall" if delta < 0 else "stay flat"
    subject = _subject(analyzed.table.file_name)
    confidence = 0.9 if abs(pct) >= 0.03 else 0.78
    measure_label = _label(measure.name).capitalize()
    return [
        _insight(
            analyzed,
            framework="trend",
            intent="trend",
            headline=f"{measure_label} {direction} {abs(pct) * 100:.1f}% from {values[0]['label']} to {values[-1]['label']}",
            so_what=f"A time trend is the clearest chart because {date_column.name} orders the business question and {measure.name} is a real measure, not an identifier.",
            evidence={
                "x_column": date_column.name,
                "y_column": measure.name,
                "values": values,
                "start_value": first,
                "end_value": last,
                "absolute_change": delta,
                "percent_change": pct,
            },
            evidence_rows=_sample_rows(analyzed.table, [date_column.name, measure.name], limit=6),
            confidence=confidence,
            recommended_visual={
                "artifact_kind": "chart",
                "chart_type": "line",
                "visual_form": "line",
                "why": "A line chart preserves temporal order and makes the slope of the aggregate measure visible.",
                "alternates": ["heatmap", "bar"],
            },
            caveats=["Aggregated duplicate periods across all categories before calculating the trend."]
            if len(values) < len(analyzed.table.rows)
            else [],
            next_action=f"Check the category or region mix behind the {values[-1]['label']} endpoint before committing capacity.",
            impact_score=abs(pct),
            title=f"{subject} trend",
        )
    ]


def _variance_bridge_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    measures = _measure_columns(analyzed)
    baseline = _first_named(measures, ("baseline", "budget", "target", "plan", "prior"))
    actual = _first_named(measures, ("forecast", "actual", "current", "revenue", "units", "sales"))
    category = _best_category(analyzed, exclude={baseline.name if baseline else "", actual.name if actual else ""})
    if not baseline or not actual or baseline.name == actual.name or not category:
        return []
    deltas: dict[str, float] = {}
    for row in analyzed.table.rows:
        label = row.get(category.name, "").strip()
        left = _number(row.get(baseline.name, ""))
        right = _number(row.get(actual.name, ""))
        if label and left is not None and right is not None:
            deltas[label] = deltas.get(label, 0.0) + (right - left)
    if len(deltas) < 2:
        return []
    ranked = sorted(deltas.items(), key=lambda item: abs(item[1]), reverse=True)[:8]
    total = sum(deltas.values())
    top_label, top_delta = ranked[0]
    return [
        _insight(
            analyzed,
            framework="variance_bridge",
            intent="contribution",
            headline=f"{top_label} contributes the largest {actual.name} versus {baseline.name} variance ({top_delta:,.0f})",
            so_what=f"The variance is concentrated enough to explain the bridge from {baseline.name} to {actual.name} by {category.name}.",
            evidence={
                "category_column": category.name,
                "baseline_column": baseline.name,
                "actual_column": actual.name,
                "total_delta": total,
                "values": [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in ranked],
            },
            evidence_rows=_sample_rows(analyzed.table, [category.name, baseline.name, actual.name], limit=6),
            confidence=0.84,
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "waterfall",
                "why": "A waterfall isolates the contribution of each category to the total variance.",
                "alternates": ["bar", "table"],
            },
            caveats=["This is variance decomposition, not causal attribution."],
            next_action=f"Validate drivers for {top_label} before treating it as a controllable lever.",
            impact_score=abs(top_delta) / max(1.0, abs(total)) if total else abs(top_delta),
            title=f"{_subject(analyzed.table.file_name)} variance bridge",
        )
    ]


def _pareto_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    category = _best_category(analyzed)
    measure = _best_measure(analyzed, prefer=("revenue", "units", "forecast", "sales", "count", "amount"))
    if not category or not measure:
        return []
    totals = _category_totals(analyzed.table, category.name, measure.name)
    positive = [(label, value) for label, value in totals.items() if value > 0]
    if len(positive) < 4:
        return []
    ranked = sorted(positive, key=lambda item: item[1], reverse=True)
    total = sum(value for _, value in ranked)
    running = 0.0
    cutoff_count = 0
    for _, value in ranked:
        running += value
        cutoff_count += 1
        if running / total >= 0.8:
            break
    top_share = ranked[0][1] / total if total else 0
    return [
        _insight(
            analyzed,
            framework="pareto",
            intent="contribution",
            headline=f"Top {cutoff_count} {category.name} values explain 80% of {_label(measure.name)}",
            so_what="The distribution is concentrated enough for a Pareto exhibit to focus management attention.",
            evidence={
                "category_column": category.name,
                "measure_column": measure.name,
                "top_share": top_share,
                "values": [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in ranked[:12]],
            },
            evidence_rows=_sample_rows(analyzed.table, [category.name, measure.name], limit=6),
            confidence=0.79 + min(0.12, top_share / 2),
            recommended_visual={
                "artifact_kind": "chart",
                "chart_type": "bar",
                "visual_form": "bar",
                "why": "A sorted bar chart makes concentration and rank order legible without clutter.",
                "alternates": ["treemap", "table"],
            },
            caveats=[],
            next_action=f"Audit the top {category.name} contributors before optimizing the long tail.",
            impact_score=top_share,
            title=f"{_subject(analyzed.table.file_name)} Pareto",
        )
    ]


def _segmentation_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    category = _best_category(analyzed)
    measure = _best_measure(analyzed)
    if not category or not measure:
        return []
    totals = _category_totals(analyzed.table, category.name, measure.name)
    if len(totals) < 3:
        return []
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    spread = ranked[0][1] - ranked[-1][1]
    if spread <= 0:
        return []
    return [
        _insight(
            analyzed,
            framework="segmentation",
            intent="comparison",
            headline=f"{ranked[0][0]} leads {ranked[-1][0]} by {spread:,.0f} {_label(measure.name)}",
            so_what=f"{category.name} segments show meaningful separation on {measure.name}.",
            evidence={
                "category_column": category.name,
                "measure_column": measure.name,
                "values": [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in ranked[:12]],
            },
            evidence_rows=_sample_rows(analyzed.table, [category.name, measure.name], limit=6),
            confidence=0.74,
            recommended_visual={
                "artifact_kind": "chart",
                "chart_type": "bar",
                "visual_form": "bar",
                "why": "A bar chart supports precise comparison across discrete segments.",
                "alternates": ["heatmap", "table"],
            },
            caveats=[],
            next_action=f"Compare the top and bottom {category.name} segments for operational differences.",
            impact_score=spread / max(1.0, abs(ranked[0][1])),
            title=f"{_subject(analyzed.table.file_name)} segment comparison",
        )
    ]


def _outlier_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    label = _best_category(analyzed)
    measure = _best_measure(analyzed, prefer=("risk", "probability", "delay", "variance", "rate", "margin", "cost"))
    if not label or not measure:
        return []
    totals = _category_totals(analyzed.table, label.name, measure.name)
    if len(totals) < 5:
        return []
    values = list(totals.values())
    mean = sum(values) / len(values)
    stdev = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) or 0.0
    if stdev == 0:
        return []
    outlier_label, outlier_value = max(totals.items(), key=lambda item: abs(item[1] - mean))
    z_score = (outlier_value - mean) / stdev
    if abs(z_score) < 1.5:
        return []
    return [
        _insight(
            analyzed,
            framework="outliers",
            intent="risk",
            headline=f"{outlier_label} is an outlier on {_label(measure.name)} ({outlier_value:,.2f})",
            so_what="The outlier is far enough from the peer average to warrant exception handling.",
            evidence={
                "label_column": label.name,
                "measure_column": measure.name,
                "mean": mean,
                "z_score": z_score,
                "values": [{"label": item_label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for item_label, value in sorted(totals.items(), key=lambda item: abs(item[1] - mean), reverse=True)[:10]],
            },
            evidence_rows=_sample_rows(analyzed.table, [label.name, measure.name], limit=6),
            confidence=0.77,
            recommended_visual={
                "artifact_kind": "chart",
                "chart_type": "bar",
                "visual_form": "bar",
                "why": "A sorted bar chart highlights the exception against peers.",
                "alternates": ["scatter", "table"],
            },
            caveats=["Outlier detection uses a simple standard-deviation screen."],
            next_action=f"Review source rows for {outlier_label} and determine whether the exception is data quality or operational risk.",
            impact_score=abs(z_score) / 3,
            title=f"{_subject(analyzed.table.file_name)} outlier",
        )
    ]


def _correlation_portfolio_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    measures = _measure_columns(analyzed)
    category = _best_category(analyzed)
    if len(measures) < 2 or not category:
        return []
    best: tuple[float, ColumnProfile, ColumnProfile] | None = None
    for i, left in enumerate(measures):
        for right in measures[i + 1 :]:
            pairs = _numeric_pairs(analyzed.table, left.name, right.name)
            if len(pairs) < 6:
                continue
            corr = _correlation(pairs)
            if corr is None:
                continue
            if best is None or abs(corr) > abs(best[0]):
                best = (corr, left, right)
    if not best or abs(best[0]) < 0.5:
        return []
    corr, left, right = best
    points = []
    seen: set[str] = set()
    for row in analyzed.table.rows:
        label = row.get(category.name, "").strip()
        x = _number(row.get(left.name, ""))
        y = _number(row.get(right.name, ""))
        if label and x is not None and y is not None and label not in seen:
            points.append({"label": label, "x": x, "y": y, "value": y, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]})
            seen.add(label)
        if len(points) >= 24:
            break
    return [
        _insight(
            analyzed,
            framework="correlation_portfolio",
            intent="relationship",
            headline=f"{_label(left.name)} and {_label(right.name)} move together (r={corr:.2f})",
            so_what="The relationship is strong enough to frame a portfolio view, but it does not prove causality.",
            evidence={"x_column": left.name, "y_column": right.name, "label_column": category.name, "correlation": corr, "values": points},
            evidence_rows=_sample_rows(analyzed.table, [category.name, left.name, right.name], limit=6),
            confidence=0.74 + min(0.16, abs(corr) / 10),
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "bubble",
                "why": "A bubble/scatter plot shows portfolio position across two metrics without implying a time sequence.",
                "alternates": ["heatmap", "table"],
            },
            caveats=["Correlation is descriptive and should not be worded as causal."],
            next_action=f"Segment the relationship by {category.name} before turning it into a driver recommendation.",
            impact_score=abs(corr),
            title=f"{_subject(analyzed.table.file_name)} portfolio",
        )
    ]


def _heatmap_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    categories = _category_columns(analyzed)
    measure = _best_measure(analyzed)
    if len(categories) < 2 or not measure:
        return []
    rows = _matrix(analyzed.table, categories[0].name, categories[1].name, measure.name)
    if len(rows) < 2:
        return []
    flat = [cell for row in rows for cell in row.get("cells", [])]
    if not flat:
        return []
    hottest = max(flat, key=lambda item: item["value"])
    return [
        _insight(
            analyzed,
            framework="heatmap_matrix",
            intent="matrix",
            headline=f"{hottest['row']} x {hottest['column']} is the highest {_label(measure.name)} cell",
            so_what="The signal depends on the intersection of two dimensions, so a matrix is more useful than a one-dimensional ranking.",
            evidence={"row_column": categories[0].name, "column_column": categories[1].name, "measure_column": measure.name, "matrix": rows, "values": flat[:80]},
            evidence_rows=_sample_rows(analyzed.table, [categories[0].name, categories[1].name, measure.name], limit=6),
            confidence=0.73,
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "heatmap",
                "why": "A heatmap shows concentration across two categorical dimensions.",
                "alternates": ["bar", "table"],
            },
            caveats=[],
            next_action=f"Use the hot cells to prioritize follow-up by {categories[0].name} and {categories[1].name}.",
            impact_score=hottest["value"] / max(1.0, sum(item["value"] for item in flat)),
            title=f"{_subject(analyzed.table.file_name)} heatmap",
        )
    ]


def _funnel_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    stage = next((column for column in _category_columns(analyzed) if column.semantic == "stage" or any(term in column.normalized for term in STAGE_TERMS)), None)
    measure = _best_measure(analyzed, prefer=("count", "units", "volume", "customers", "orders"))
    if not stage:
        return []
    totals = _category_totals(analyzed.table, stage.name, measure.name if measure else "")
    if len(totals) < 3:
        return []
    ordered = sorted(totals.items(), key=lambda item: _order_key(item[0]))
    start = ordered[0][1]
    end = ordered[-1][1]
    if start <= 0:
        return []
    drop = 1 - (end / start)
    return [
        _insight(
            analyzed,
            framework="funnel_dropoff",
            intent="flow",
            headline=f"{stage.name} retains {end / start * 100:.1f}% from first to final stage",
            so_what="A funnel view is appropriate because the rows describe an ordered flow with measurable drop-off.",
            evidence={
                "stage_column": stage.name,
                "measure_column": measure.name if measure else "row_count",
                "dropoff_rate": drop,
                "values": [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in ordered],
            },
            evidence_rows=_sample_rows(analyzed.table, [stage.name] + ([measure.name] if measure else []), limit=6),
            confidence=0.72,
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "funnel",
                "why": "A funnel chart makes sequential conversion and drop-off visible.",
                "alternates": ["bar", "table"],
            },
            caveats=["Stage order is inferred from labels if no explicit sequence column exists."],
            next_action="Inspect the largest stage-to-stage drop and assign an owner for root-cause review.",
            impact_score=drop,
            title=f"{_subject(analyzed.table.file_name)} funnel",
        )
    ]


def _target_progress_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    measures = _measure_columns(analyzed)
    actual = _first_named(measures, ("actual", "current", "forecast", "units", "revenue", "sales"))
    target = _first_named(measures, ("target", "goal", "quota", "budget", "plan", "reorder_point"))
    label = _best_category(analyzed)
    if not actual or not target or actual.name == target.name:
        return []
    total_actual = sum(value for value in (_number(row.get(actual.name, "")) for row in analyzed.table.rows) if value is not None)
    total_target = sum(value for value in (_number(row.get(target.name, "")) for row in analyzed.table.rows) if value is not None)
    if total_target <= 0:
        return []
    progress = total_actual / total_target
    grouped = _progress_values(analyzed, label.name if label else "", actual.name, target.name)
    return [
        _insight(
            analyzed,
            framework="target_progress",
            intent="target",
            headline=f"{_label(actual.name)} is at {progress * 100:.1f}% of {_label(target.name)}",
            so_what="The target comparison is a status question, so progress/bullet treatment is clearer than a generic bar chart.",
            evidence={"actual_column": actual.name, "target_column": target.name, "progress": progress, "values": grouped},
            evidence_rows=_sample_rows(analyzed.table, ([label.name] if label else []) + [actual.name, target.name], limit=6),
            confidence=0.78,
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "progress",
                "why": "A progress/bullet view communicates achievement against target directly.",
                "alternates": ["bar", "table"],
            },
            caveats=[],
            next_action="Separate over-target and under-target segments before choosing corrective actions.",
            impact_score=abs(1 - progress),
            title=f"{_subject(analyzed.table.file_name)} target progress",
        )
    ]


def _cohort_like_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    date_column = _first_column(analyzed, "date")
    cohort = next((column for column in _category_columns(analyzed) if "cohort" in column.normalized or "segment" in column.normalized), None)
    rate = _best_measure(analyzed, prefer=("retention", "renewal", "repeat", "rate"))
    if not date_column or not cohort or not rate:
        return []
    matrix = _matrix(analyzed.table, cohort.name, date_column.name, rate.name)
    if len(matrix) < 2:
        return []
    return [
        _insight(
            analyzed,
            framework="cohort_retention",
            intent="cohort",
            headline=f"{cohort.name} retention varies by {date_column.name}",
            so_what="Cohort-like data needs a matrix to preserve both start group and time period.",
            evidence={"cohort_column": cohort.name, "period_column": date_column.name, "measure_column": rate.name, "matrix": matrix},
            evidence_rows=_sample_rows(analyzed.table, [cohort.name, date_column.name, rate.name], limit=6),
            confidence=0.72,
            recommended_visual={
                "artifact_kind": "summary_panel",
                "chart_type": None,
                "visual_form": "heatmap",
                "why": "A retention heatmap makes period-by-cohort decay patterns visible.",
                "alternates": ["line", "table"],
            },
            caveats=["Cohort semantics are inferred from column names."],
            next_action="Confirm cohort definitions before interpreting retention movement.",
            impact_score=0.5,
            title=f"{_subject(analyzed.table.file_name)} cohort matrix",
        )
    ]


def _text_theme_insights(analyzed: AnalyzedTable) -> list[dict[str, Any]]:
    text_columns = [column for column in analyzed.columns if column.role == "text"]
    if not text_columns:
        return []
    counts: dict[str, int] = {}
    for row in analyzed.table.rows:
        text = " ".join(row.get(column.name, "") for column in text_columns).lower()
        for label, terms in (
            ("risk", ("risk", "delay", "stockout", "issue", "problem", "concern")),
            ("margin pressure", ("margin", "discount", "cost", "expensive", "pressure")),
            ("demand signal", ("demand", "regional", "growth", "forecast")),
            ("supplier delay", ("supplier", "lead time", "late", "delay")),
            ("promotion effect", ("promotion", "lift", "campaign", "discount")),
        ):
            if any(term in text for term in terms):
                counts[label] = counts.get(label, 0) + 1
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [
        _insight(
            analyzed,
            framework="text_themes",
            intent="themes",
            headline=f"{ranked[0][0].title()} is the most repeated text theme",
            so_what="Repeated language in free-text fields provides a qualitative signal to pair with the quantitative exhibits.",
            evidence={
                "text_columns": [column.name for column in text_columns],
                "values": [{"label": label, "value": value, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]} for label, value in ranked],
            },
            evidence_rows=_sample_rows(analyzed.table, [column.name for column in text_columns], limit=6),
            confidence=0.68,
            recommended_visual={
                "artifact_kind": "chart",
                "chart_type": "bar",
                "visual_form": "bar",
                "why": "A short ranked bar chart shows repeated qualitative themes without over-claiming precision.",
                "alternates": ["table"],
            },
            caveats=["Theme detection is keyword-based in v1."],
            next_action="Read representative comments before turning themes into executive claims.",
            impact_score=ranked[0][1] / max(1, sum(counts.values())),
            title=f"{_subject(analyzed.table.file_name)} text themes",
        )
    ]


def _review_insight(insight: dict[str, Any]) -> dict[str, Any] | None:
    evidence = insight.get("evidence") if isinstance(insight.get("evidence"), dict) else {}
    measure_names = [
        str(evidence.get(key) or "")
        for key in ("y_column", "measure_column", "actual_column", "target_column", "baseline_column")
        if evidence.get(key)
    ]
    if any(_is_identifier_name(name) for name in measure_names):
        return None
    headline = str(insight.get("headline") or "").lower()
    if any(term in headline for term in ("because", "caused", "causes", "drives ")) and insight.get("framework") != "variance_bridge":
        insight.setdefault("caveats", []).append("Causality is not supported by deterministic analysis.")
    if headline in {"data summary", "summary", "table summary"}:
        return None
    visual = insight.get("recommended_visual") if isinstance(insight.get("recommended_visual"), dict) else {}
    if visual.get("visual_form") == "line" and not evidence.get("x_column"):
        return None
    return insight


def _insight(
    analyzed: AnalyzedTable,
    *,
    framework: str,
    intent: str,
    headline: str,
    so_what: str,
    evidence: dict[str, Any],
    evidence_rows: list[dict[str, str]],
    confidence: float,
    recommended_visual: dict[str, Any],
    caveats: list[str],
    next_action: str,
    impact_score: float,
    title: str,
) -> dict[str, Any]:
    return {
        "id": f"{framework}_{_slug(analyzed.table.file_name)}_{_slug(headline)}",
        "framework": framework,
        "intent": intent,
        "title": title,
        "headline": headline,
        "so_what": so_what,
        "evidence": {
            **evidence,
            "source_file": analyzed.table.file_name,
            "source_id": analyzed.source["source_id"],
            "source_chunk_id": analyzed.source["chunk_id"],
            "row_count": len(analyzed.table.rows),
            "dimension_columns": [
                column.name
                for column in analyzed.columns
                if column.role in {"category", "date"}
            ],
            "excluded_identifier_columns": [
                column.name
                for column in analyzed.columns
                if column.role == "identifier"
            ],
        },
        "evidence_rows": evidence_rows,
        "confidence": round(max(0.0, min(confidence, 0.99)), 3),
        "caveats": caveats,
        "recommended_visual": recommended_visual,
        "next_action": next_action,
        "impact_score": round(max(0.0, min(impact_score, 9.99)), 3),
    }


def _metric_catalog(tables: list[AnalyzedTable]) -> dict[str, list[dict[str, Any]]]:
    catalog: dict[str, list[dict[str, Any]]] = {
        "volumes": [],
        "revenue": [],
        "margin": [],
        "rates": [],
        "probabilities": [],
        "dates": [],
        "categories": [],
        "stages": [],
        "targets": [],
        "identifiers_excluded": [],
    }
    for table in tables:
        for column in table.columns:
            item = {"file_name": table.table.file_name, "column": column.name, "role": column.role, "semantic": column.semantic}
            if column.role == "date":
                catalog["dates"].append(item)
            elif column.role == "category":
                catalog["stages" if column.semantic == "stage" else "categories"].append(item)
            elif column.role == "identifier":
                catalog["identifiers_excluded"].append(item)
            elif column.role == "measure":
                if column.semantic == "revenue":
                    catalog["revenue"].append(item)
                elif column.semantic == "margin":
                    catalog["margin"].append(item)
                elif column.semantic == "probability":
                    catalog["probabilities"].append(item)
                elif column.semantic in {"rate", "lift"}:
                    catalog["rates"].append(item)
                elif column.semantic == "target":
                    catalog["targets"].append(item)
                else:
                    catalog["volumes"].append(item)
    return catalog


def _public_table(analyzed: AnalyzedTable) -> dict[str, Any]:
    return {
        "file_id": analyzed.table.file_id,
        "file_name": analyzed.table.file_name,
        "row_count": len(analyzed.table.rows),
        "columns": [
            {
                "name": column.name,
                "role": column.role,
                "semantic": column.semantic,
                "non_empty": column.non_empty,
                "unique": column.unique,
                "sample_values": column.sample_values,
            }
            for column in analyzed.columns
        ],
    }


def _brief_summary(insights: list[dict[str, Any]], tables: list[AnalyzedTable]) -> str:
    if insights:
        top = insights[0]
        visual = top.get("recommended_visual", {}).get("visual_form", "artifact")
        return f"Top insight: {top.get('headline')}. Recommended visual: {visual}."
    if tables:
        return f"Profiled {len(tables)} structured table(s), but no strong deterministic insight was detected."
    return "No structured table was detected for deterministic analysis."


def _framework_priority(framework: str) -> float:
    return {
        "trend": 0.08,
        "target_progress": 0.04,
        "variance_bridge": 0.035,
        "heatmap_matrix": 0.025,
        "pareto": 0.015,
    }.get(framework, 0.0)


def _source_for_file(file_id: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    for source in sources:
        if source.get("file_id") == file_id:
            return source
    return sources[0] if sources else None


def _measure_columns(analyzed: AnalyzedTable) -> list[ColumnProfile]:
    return [column for column in analyzed.columns if column.role == "measure" and not _is_identifier_name(column.name)]


def _category_columns(analyzed: AnalyzedTable) -> list[ColumnProfile]:
    return [column for column in analyzed.columns if column.role == "category"]


def _first_column(analyzed: AnalyzedTable, role: str) -> ColumnProfile | None:
    return next((column for column in analyzed.columns if column.role == role), None)


def _first_named(columns: list[ColumnProfile], terms: tuple[str, ...]) -> ColumnProfile | None:
    return next((column for column in columns if any(term in column.normalized for term in terms)), None)


def _best_measure(analyzed: AnalyzedTable, prefer: tuple[str, ...] = VALUE_TERMS) -> ColumnProfile | None:
    candidates = _measure_columns(analyzed)
    if not candidates:
        return None

    def score(column: ColumnProfile) -> tuple[int, int, int]:
        normalized = column.normalized
        preferred = max((len(prefer) - index for index, term in enumerate(prefer) if term in normalized), default=0)
        semantic_boost = 3 if column.semantic in {"revenue", "margin", "volume", "probability", "rate"} else 0
        target_penalty = -4 if column.semantic == "target" or "baseline" in normalized else 0
        return (preferred + semantic_boost + target_penalty, column.numeric_count, -column.unique)

    return max(candidates, key=score)


def _best_category(analyzed: AnalyzedTable, exclude: set[str] | None = None) -> ColumnProfile | None:
    exclude = exclude or set()
    candidates = [column for column in _category_columns(analyzed) if column.name not in exclude]
    if not candidates:
        candidates = [column for column in analyzed.columns if column.role == "identifier" and column.unique <= 32 and column.name not in exclude]
    if not candidates:
        return None
    preferred = ("region", "channel", "category", "segment", "supplier", "warehouse", "product", "sku")
    return sorted(candidates, key=lambda column: (not any(term in column.normalized for term in preferred), column.unique, column.name))[0]


def _aggregate(table: ParsedTable, x_column: str, y_column: str) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for row in table.rows:
        label = row.get(x_column, "").strip()
        value = _number(row.get(y_column, ""))
        if label and value is not None:
            totals[label] = totals.get(label, 0.0) + value
    return sorted(totals.items(), key=lambda item: _order_key(item[0]))


def _category_totals(table: ParsedTable, category_column: str, measure_column: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in table.rows:
        label = row.get(category_column, "").strip()
        if not label:
            continue
        value = _number(row.get(measure_column, "")) if measure_column else 1.0
        if value is not None:
            totals[label] = totals.get(label, 0.0) + value
    return totals


def _progress_values(analyzed: AnalyzedTable, label_column: str, actual_column: str, target_column: str) -> list[dict[str, Any]]:
    if not label_column:
        actual = sum(value for value in (_number(row.get(actual_column, "")) for row in analyzed.table.rows) if value is not None)
        target = sum(value for value in (_number(row.get(target_column, "")) for row in analyzed.table.rows) if value is not None)
        return [{"label": "Total", "actual": actual, "target": target, "value": actual / target if target else 0, "source_id": analyzed.source["source_id"], "source_chunk_id": analyzed.source["chunk_id"]}]
    grouped: dict[str, dict[str, float]] = {}
    for row in analyzed.table.rows:
        label = row.get(label_column, "").strip()
        actual = _number(row.get(actual_column, ""))
        target = _number(row.get(target_column, ""))
        if label and actual is not None and target is not None:
            bucket = grouped.setdefault(label, {"actual": 0.0, "target": 0.0})
            bucket["actual"] += actual
            bucket["target"] += target
    return [
        {
            "label": label,
            "actual": values["actual"],
            "target": values["target"],
            "value": values["actual"] / values["target"] if values["target"] else 0,
            "source_id": analyzed.source["source_id"],
            "source_chunk_id": analyzed.source["chunk_id"],
        }
        for label, values in sorted(grouped.items(), key=lambda item: item[1]["actual"] / item[1]["target"] if item[1]["target"] else 0, reverse=True)[:12]
    ]


def _matrix(table: ParsedTable, row_column: str, column_column: str, measure_column: str) -> list[dict[str, Any]]:
    row_labels = []
    column_labels = []
    totals: dict[tuple[str, str], float] = {}
    for row in table.rows:
        row_label = row.get(row_column, "").strip()
        column_label = row.get(column_column, "").strip()
        value = _number(row.get(measure_column, ""))
        if not row_label or not column_label or value is None:
            continue
        if row_label not in row_labels:
            row_labels.append(row_label)
        if column_label not in column_labels:
            column_labels.append(column_label)
        totals[(row_label, column_label)] = totals.get((row_label, column_label), 0.0) + value
    return [
        {
            "label": row_label,
            "cells": [{"row": row_label, "column": column_label, "label": f"{row_label} / {column_label}", "value": totals.get((row_label, column_label), 0.0)} for column_label in column_labels[:12]],
        }
        for row_label in row_labels[:12]
    ]


def _numeric_pairs(table: ParsedTable, left_column: str, right_column: str) -> list[tuple[float, float]]:
    pairs = []
    for row in table.rows:
        left = _number(row.get(left_column, ""))
        right = _number(row.get(right_column, ""))
        if left is not None and right is not None:
            pairs.append((left, right))
    return pairs


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs = [left for left, _ in pairs]
    ys = [right for _, right in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if not den_x or not den_y:
        return None
    return num / (den_x * den_y)


def _sample_rows(table: ParsedTable, columns: list[str], limit: int = 6) -> list[dict[str, str]]:
    clean_columns = [column for column in columns if column]
    rows = []
    for row in table.rows:
        sample = {column: row.get(column, "") for column in clean_columns}
        if any(sample.values()):
            rows.append(sample)
        if len(rows) >= limit:
            break
    return rows


def _looks_date(name: str, values: list[str]) -> bool:
    if any(term in name for term in DATE_TERMS):
        return True
    sample = values[:24]
    return sum(1 for value in sample if _order_key(value)[0] < 3) / max(1, len(sample)) >= 0.7


def _looks_identifier(name: str, values: list[str], numeric_count: int) -> bool:
    if _is_identifier_name(name):
        return True
    unique_ratio = len(set(values)) / max(1, len(values))
    numeric_ratio = numeric_count / max(1, len(values))
    if unique_ratio > 0.9 and numeric_ratio >= 0.75 and not any(term in name for term in VALUE_TERMS + TARGET_TERMS):
        return True
    if unique_ratio > 0.9 and _avg_len(values) <= 24 and any(re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", value) for value in values[:20]):
        return True
    return False


def _is_identifier_name(name: str) -> bool:
    normalized = _normalize(name)
    return normalized in {"id", "uuid", "sku"} or normalized.endswith("_id") or any(term in normalized for term in ("identifier", "email"))


def _measure_semantic(name: str, values: list[str]) -> str:
    if "probability" in name or "risk" in name:
        return "probability"
    if "margin" in name or "profit" in name:
        return "margin"
    if "revenue" in name or "sales" in name or "amount" in name or "price" in name or "cost" in name:
        return "revenue" if "cost" not in name else "cost"
    if any(term in name for term in TARGET_TERMS):
        return "target"
    if any(term in name for term in RATE_TERMS) or any("%" in value for value in values[:20]):
        return "lift" if "lift" in name else "rate"
    if any(term in name for term in ("unit", "qty", "quantity", "count", "stock", "allocation", "demand", "forecast")):
        return "volume"
    return "measure"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _order_key(value: str) -> tuple[int, Any]:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            return (0, datetime.strptime(text, fmt))
        except ValueError:
            pass
    quarter = re.search(r"(20\d{2})?[-\s]?Q([1-4])", text, re.IGNORECASE)
    if quarter:
        return (1, int(quarter.group(1) or 0) * 10 + int(quarter.group(2)))
    month = text.lower()[:3]
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    if month in months:
        return (2, months[month])
    number = _number(text)
    if number is not None:
        return (3, number)
    return (9, text)


def _avg_len(values: list[str]) -> float:
    return sum(len(value) for value in values) / max(1, len(values))


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "_", value.lower()).strip("_")


def _slug(value: str) -> str:
    return _normalize(value)[:56] or "insight"


def _subject(file_name: str) -> str:
    return re.sub(r"\.[a-z0-9]+$", "", file_name, flags=re.IGNORECASE).replace("_", " ").replace("-", " ").strip() or "Source"


def _label(name: str) -> str:
    return name.replace("_", " ").strip()
