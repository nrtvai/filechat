from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .survey import ParsedTable, parse_table


@dataclass
class TableProfile:
    table: ParsedTable
    source: dict[str, Any]
    columns: list[dict[str, Any]]
    business_record: bool


BUSINESS_COLUMNS = {
    "item",
    "record",
    "warehouse",
    "vendor",
    "supplier",
    "units_on_hand",
    "threshold",
    "unit_cost",
    "lead_time",
    "record_id",
}

EXPLICIT_CHART_TYPES = {
    "line": ("line chart", "line graph", "trend", "over time", "time series"),
    "bar": ("bar chart", "bar graph", "ranking"),
    "pie": ("pie chart", "share", "breakdown", "part-to-whole", "part to whole"),
}
AGGREGATION_CAVEAT = "Rows may be aggregated before charting, so segment-level variation can be hidden."


def build_artifact_advice(
    question: str,
    file_texts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    task_contract: dict[str, Any],
    insight_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    table_profiles = _table_profiles(file_texts, sources)
    recommendations: list[dict[str, Any]] = []
    explicit_chart_type = requested_chart_type(question)
    recommendations.extend(_recommend_for_insight_brief(insight_brief, explicit_chart_type=explicit_chart_type))
    for profile in table_profiles:
        recommendations.extend(_recommend_for_table(profile, explicit_chart_type=explicit_chart_type))
    if not recommendations:
        recommendations.extend(_fallback_recommendations(question, sources))
    recommendations = _rank_recommendations(recommendations, explicit_chart_type=explicit_chart_type)
    recommendations = recommendations[:4]
    discovery_only = _is_discovery_only(question)
    auto_select = bool(insight_brief and insight_brief.get("insights") and not discovery_only and _should_auto_select_insight(question, task_contract))
    should_ask = bool(recommendations and not auto_select and not discovery_only and is_broad_artifact_request(question, task_contract))
    return {
        "request": question,
        "discovery_only": discovery_only,
        "should_ask": should_ask,
        "auto_select": auto_select,
        "explicit_chart_type": explicit_chart_type,
        "recommendations": [_public_recommendation(item) for item in recommendations],
        "table_profiles": [_public_table_profile(profile) for profile in table_profiles],
        "insight_summary": insight_brief.get("summary") if isinstance(insight_brief, dict) else "",
        "_recommendations": recommendations,
    }


def requested_chart_type(question: str) -> str | None:
    normalized = question.lower()
    for chart_type, patterns in EXPLICIT_CHART_TYPES.items():
        if any(pattern in normalized for pattern in patterns):
            return chart_type
    return None


def is_broad_artifact_request(question: str, task_contract: dict[str, Any]) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower()).strip(" ?!.")
    if requested_chart_type(question):
        return False
    if _is_discovery_only(question):
        return False
    broad_patterns = (
        "best graph",
        "best chart",
        "best visualization",
        "recommend artifact",
        "recommend artifacts",
        "recommend a chart",
        "recommend charts",
        "what should i make",
        "analyze this",
        "analyse this",
    )
    if any(pattern in normalized for pattern in broad_patterns):
        return True
    deliverable = str(task_contract.get("deliverable") or "").lower()
    return deliverable in {"artifact_recommendations", "best_artifact"}


def _should_auto_select_insight(question: str, task_contract: dict[str, Any]) -> bool:
    if requested_chart_type(question):
        return True
    normalized = re.sub(r"\s+", " ", question.lower()).strip(" ?!.")
    chart_intent_patterns = (
        "best graph",
        "best chart",
        "best visualization",
        "recommend a chart",
        "recommend chart",
        "make a chart",
        "create a chart",
        "analyze this",
        "analyse this",
    )
    if any(pattern in normalized for pattern in chart_intent_patterns):
        return True
    required = set(task_contract.get("required_outputs") or [])
    primary = set(task_contract.get("primary_outputs") or [])
    deliverable = str(task_contract.get("deliverable") or "").lower()
    return bool((required | primary) == {"chart"} or deliverable == "best_artifact")


def selected_recommendation(advice: dict[str, Any], selected_id: str | None) -> dict[str, Any] | None:
    recommendations = advice.get("_recommendations")
    if not isinstance(recommendations, list):
        return None
    if selected_id:
        for item in recommendations:
            if isinstance(item, dict) and item.get("id") == selected_id:
                return item
    return recommendations[0] if recommendations and isinstance(recommendations[0], dict) else None


def recommendation_options(advice: dict[str, Any]) -> list[dict[str, str]]:
    options = []
    for item in advice.get("recommendations", [])[:4]:
        if not isinstance(item, dict):
            continue
        options.append(
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("title") or item.get("artifact_kind") or "Artifact"),
                "description": str(item.get("reason") or ""),
            }
        )
    return [option for option in options if option["id"] and option["label"]]


def decision_options(advice: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations = advice.get("_recommendations")
    if not isinstance(recommendations, list):
        recommendations = advice.get("recommendations") if isinstance(advice.get("recommendations"), list) else []
    options = [_decision_option(item) for item in recommendations[:4] if isinstance(item, dict)]
    return [option for option in options if option]


def build_recommended_artifact(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
    artifact = recommendation.get("artifact") if isinstance(recommendation, dict) else None
    return dict(artifact) if isinstance(artifact, dict) else None


def build_recommendation_cards_artifact(
    advice: dict[str, Any],
    task_contract: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    language = str(task_contract.get("language") or "")
    title = "생성 가능한 차트와 문서" if language == "ko" else "Available Charts And Docs"
    caption = "첨부 파일 구조에서 바로 만들 수 있는 산출물 후보입니다." if language == "ko" else "Artifact options grounded in the attached file structure."
    source = sources[0] if sources else {}
    source_id = int(source.get("source_id") or 1)
    source_chunk_id = str(source.get("chunk_id") or "")
    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": title, "caption": caption},
            "children": ["intro", "options"],
        },
        "intro": {
            "type": "TextBlock",
            "props": {"text": "Choose one of these grounded artifacts to create next.", "tone": "muted"},
            "children": [],
        },
        "options": {"type": "Stack", "props": {"gap": "sm"}, "children": []},
    }
    recommendations = advice.get("recommendations", []) if isinstance(advice.get("recommendations"), list) else []
    for index, recommendation in enumerate(recommendations[:4], start=1):
        option_id = f"option_{index}"
        badge_id = f"badge_{index}"
        title_id = f"title_{index}"
        desc_id = f"description_{index}"
        elements["options"]["children"].append(option_id)
        elements[option_id] = {"type": "Stack", "props": {"gap": "xs"}, "children": [badge_id, title_id, desc_id]}
        label = str(recommendation.get("artifact_kind") or "Artifact").replace("_", " ").title()
        if recommendation.get("chart_type"):
            label = f"{str(recommendation['chart_type']).title()} Chart"
        elements[badge_id] = {"type": "Badge", "props": {"label": label, "tone": "accent"}, "children": []}
        elements[title_id] = {"type": "TextBlock", "props": {"text": str(recommendation.get("title") or label), "tone": "strong"}, "children": []}
        elements[desc_id] = {"type": "TextBlock", "props": {"text": _option_description(recommendation), "tone": "muted"}, "children": []}
    if source_chunk_id:
        elements["card"]["children"].append("source")
        elements["source"] = {"type": "SourceButton", "props": {"label": "Open source", "chunkId": source_chunk_id}, "children": []}
    return {
        "kind": "decision_cards",
        "title": title,
        "caption": caption,
        "display_mode": "primary",
        "source_ids": [source_id],
        "decision_options": decision_options(advice),
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def _decision_option(recommendation: dict[str, Any]) -> dict[str, Any]:
    option_id = str(recommendation.get("id") or "").strip()
    title = str(recommendation.get("title") or "").strip()
    artifact_kind = str(recommendation.get("artifact_kind") or "summary_panel").strip()
    if not option_id or not title:
        return {}
    produce_payload: dict[str, Any] = {
        "artifact_kind": artifact_kind,
        "title": title,
        "description": _option_description(recommendation),
        "source_columns": [str(column) for column in recommendation.get("source_columns", []) if str(column).strip()]
        if isinstance(recommendation.get("source_columns"), list)
        else [],
        "source_facts": [str(fact) for fact in recommendation.get("source_facts", []) if str(fact).strip()]
        if isinstance(recommendation.get("source_facts"), list)
        else [],
    }
    chart_type = str(recommendation.get("chart_type") or "").strip()
    if chart_type:
        produce_payload["chart_type"] = chart_type
    if isinstance(recommendation.get("artifact"), dict):
        produce_payload["artifact"] = recommendation["artifact"]
    option = {
        "id": option_id,
        "label": title,
        "description": _option_description(recommendation),
        "artifact_kind": artifact_kind,
        "produce_payload": produce_payload,
    }
    if chart_type:
        option["chart_type"] = chart_type
    return option


def _option_description(recommendation: dict[str, Any]) -> str:
    description = str(recommendation.get("reason") or "").strip()
    if "segment-level variation" in description:
        return description
    return f"{description} Caveat: {AGGREGATION_CAVEAT}".strip()


def _table_profiles(file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[TableProfile]:
    profiles: list[TableProfile] = []
    for item in file_texts:
        table = parse_table(str(item.get("text") or ""), str(item.get("file_id") or ""), str(item.get("file_name") or "table.csv"))
        if not table:
            continue
        source = _source_for_file(table.file_id, sources)
        if not source:
            continue
        columns = [_profile_column(table, column) for column in table.columns]
        normalized_columns = {_normalize_name(column) for column in table.columns}
        business_record = bool(normalized_columns & BUSINESS_COLUMNS) or any("sku" in column for column in normalized_columns)
        profiles.append(TableProfile(table=table, source=source, columns=columns, business_record=business_record))
    return profiles


def _profile_column(table: ParsedTable, column: str) -> dict[str, Any]:
    values = [row.get(column, "") for row in table.rows]
    non_empty = [value.strip() for value in values if value.strip()]
    unique = len(set(non_empty))
    numeric_values = [_number(value) for value in non_empty]
    numeric_count = sum(1 for value in numeric_values if value is not None)
    role = "sparse_unusable"
    normalized = _normalize_name(column)
    if non_empty and len(non_empty) / max(1, len(values)) < 0.35:
        role = "sparse_unusable"
    elif _looks_temporal_or_ordinal(normalized, non_empty):
        role = "temporal_ordinal"
    elif _looks_id_like(normalized, non_empty, numeric_count):
        role = "id_like"
    elif numeric_count / max(1, len(non_empty)) >= 0.75:
        role = "percentage" if any("%" in value for value in non_empty) or "percent" in normalized or "rate" in normalized else "currency" if _looks_currency(normalized, non_empty) else "numeric"
    elif unique <= max(3, min(12, len(non_empty) // 2)) and _average_length(non_empty) <= 48:
        role = "categorical"
    else:
        role = "free_text"
    return {
        "name": column,
        "role": role,
        "non_empty": len(non_empty),
        "unique": unique,
        "avg_length": round(_average_length(non_empty), 1),
    }


def _recommend_for_table(profile: TableProfile, *, explicit_chart_type: str | None) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    table_rec = _table_recommendation(profile)
    line_rec = _line_recommendation(profile)
    bar_rec = _bar_recommendation(profile)
    pie_rec = _pie_recommendation(profile)
    if profile.business_record and table_rec:
        recommendations.append(table_rec)
    if explicit_chart_type == "line" and line_rec:
        recommendations.append(line_rec)
    elif explicit_chart_type == "bar" and bar_rec:
        recommendations.append(bar_rec)
    elif explicit_chart_type == "pie" and pie_rec:
        recommendations.append(pie_rec)
    else:
        for item in (line_rec, bar_rec, pie_rec):
            if item:
                recommendations.append(item)
    if not profile.business_record and table_rec:
        recommendations.append(table_rec)
    if profile.business_record:
        for item in (bar_rec, pie_rec):
            if item and item["id"] not in {rec["id"] for rec in recommendations}:
                recommendations.append(item)
    return recommendations


def _recommend_for_insight_brief(insight_brief: dict[str, Any] | None, *, explicit_chart_type: str | None) -> list[dict[str, Any]]:
    if not isinstance(insight_brief, dict):
        return []
    insights = insight_brief.get("insights")
    if not isinstance(insights, list):
        return []
    recommendations = []
    for insight in insights[:6]:
        if not isinstance(insight, dict):
            continue
        recommendation = _recommend_for_insight(insight, explicit_chart_type=explicit_chart_type)
        if recommendation:
            recommendations.append(recommendation)
    return recommendations


def _recommend_for_insight(insight: dict[str, Any], *, explicit_chart_type: str | None) -> dict[str, Any] | None:
    visual = insight.get("recommended_visual") if isinstance(insight.get("recommended_visual"), dict) else {}
    evidence = insight.get("evidence") if isinstance(insight.get("evidence"), dict) else {}
    source_id = int(evidence.get("source_id") or 1)
    source_chunk_id = str(evidence.get("source_chunk_id") or "")
    visual_form = str(visual.get("visual_form") or visual.get("chart_type") or "table")
    chart_type = str(visual.get("chart_type") or "")
    if explicit_chart_type and chart_type and explicit_chart_type != chart_type:
        chart_type = explicit_chart_type
    title = str(insight.get("title") or insight.get("headline") or "Recommended insight")
    headline = str(insight.get("headline") or title)
    so_what = str(insight.get("so_what") or "")
    why = str(visual.get("why") or "This visual matches the analytical message.")
    caveats = [str(item) for item in insight.get("caveats", []) if str(item).strip()] if isinstance(insight.get("caveats"), list) else []
    next_action = str(insight.get("next_action") or "")
    values = _insight_values(evidence)
    source_columns = _insight_source_columns(evidence)
    artifact_kind = str(visual.get("artifact_kind") or ("chart" if chart_type in {"line", "bar", "pie"} else "summary_panel"))
    if chart_type in {"line", "bar", "pie"} and values:
        artifact_kind = "chart"
        insight_narrative = _insight_narrative(
            title=title,
            headline=headline,
            so_what=so_what,
            why=why,
            caveats=caveats,
            next_action=next_action,
            evidence=evidence,
            source_columns=source_columns,
            confidence=float(insight.get("confidence") or 0.7),
        )
        artifact = {
            "kind": "chart",
            "title": title,
            "caption": headline,
            "display_mode": "primary",
            "source_ids": [source_id],
            "source_chunk_ids": [source_chunk_id] if source_chunk_id else [],
            "chart_type": chart_type,
            "x_label": str(evidence.get("x_column") or evidence.get("category_column") or evidence.get("label_column") or "Category"),
            "y_label": str(evidence.get("y_column") or evidence.get("measure_column") or "Value"),
            "x_column": str(evidence.get("x_column") or evidence.get("category_column") or evidence.get("label_column") or ""),
            "y_column": str(evidence.get("y_column") or evidence.get("measure_column") or ""),
            "source_columns": source_columns,
            "source_facts": _insight_facts(headline, so_what, why, caveats, next_action),
            "insight_narrative": insight_narrative,
            "values": values,
        }
    elif visual_form in {"waterfall", "heatmap", "progress", "funnel", "treemap", "mekko", "bubble"}:
        artifact_kind = "summary_panel"
        artifact = _json_visual_artifact(
            title=title,
            headline=headline,
            so_what=so_what,
            why=why,
            caveats=caveats,
            next_action=next_action,
            visual_form=visual_form,
            evidence=evidence,
            source_id=source_id,
            source_chunk_id=source_chunk_id,
        )
    else:
        artifact_kind = "table"
        artifact = _insight_table_artifact(
            title=title,
            headline=headline,
            so_what=so_what,
            why=why,
            caveats=caveats,
            next_action=next_action,
            evidence_rows=insight.get("evidence_rows", []),
            source_id=source_id,
            source_chunk_id=source_chunk_id,
        )
    return {
        "id": f"insight_{_slug(str(insight.get('id') or title))}",
        "artifact_kind": artifact_kind,
        "chart_type": chart_type if artifact_kind == "chart" else None,
        "visual_form": visual_form,
        "title": title,
        "reason": f"{headline} {why}".strip(),
        "source_columns": source_columns,
        "source_facts": _insight_facts(headline, so_what, why, caveats, next_action),
        "confidence": float(insight.get("confidence") or 0.7) + 0.2,
        "follow_up_prompt": f"Create the {visual_form} exhibit for: {headline}",
        "insight": {key: value for key, value in insight.items() if key != "evidence_rows"},
        "artifact": artifact,
    }


def _insight_values(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    values = evidence.get("values")
    if not isinstance(values, list):
        return []
    out = []
    for item in values:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("category") or "").strip()
        value = _number(str(item.get("value") if item.get("value") is not None else ""))
        if not label or value is None:
            continue
        point = {"label": label, "value": value}
        if item.get("source_id") is not None:
            point["source_id"] = item.get("source_id")
        if item.get("source_chunk_id"):
            point["source_chunk_id"] = str(item.get("source_chunk_id"))
        out.append(point)
    return out


def _insight_source_columns(evidence: dict[str, Any]) -> list[str]:
    columns = []
    for key in (
        "x_column",
        "y_column",
        "category_column",
        "measure_column",
        "row_column",
        "column_column",
        "actual_column",
        "target_column",
        "baseline_column",
        "label_column",
    ):
        value = str(evidence.get(key) or "").strip()
        if value and value not in columns:
            columns.append(value)
    return columns


def _insight_facts(headline: str, so_what: str, why: str, caveats: list[str], next_action: str) -> list[str]:
    facts = [f"Headline: {headline}", f"So what: {so_what}", f"Why this visual: {why}"]
    if caveats:
        facts.append(f"Caveats: {'; '.join(caveats)}")
    if next_action:
        facts.append(f"Next action: {next_action}")
    return [fact for fact in facts if fact.strip()]


def _json_visual_artifact(
    *,
    title: str,
    headline: str,
    so_what: str,
    why: str,
    caveats: list[str],
    next_action: str,
    visual_form: str,
    evidence: dict[str, Any],
    source_id: int,
    source_chunk_id: str,
) -> dict[str, Any]:
    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": title, "caption": headline},
            "children": ["headline", "so_what", "visual", "why", "caveats", "next_action", "copy_md", "copy_json"],
        },
        "headline": {"type": "TextBlock", "props": {"text": f"Headline: {headline}", "tone": "strong"}, "children": []},
        "so_what": {"type": "TextBlock", "props": {"text": f"So what: {so_what}"}, "children": []},
        "why": {"type": "TextBlock", "props": {"text": f"Why this visual: {why}", "tone": "muted"}, "children": []},
        "caveats": {"type": "TextBlock", "props": {"text": "Caveats: " + ("; ".join(caveats) if caveats else "None flagged by deterministic review."), "tone": "muted"}, "children": []},
        "next_action": {"type": "TextBlock", "props": {"text": f"Next action: {next_action}", "tone": "strong"}, "children": []},
        "copy_md": {"type": "ActionButton", "props": {"label": "Copy insight", "action": "copy", "value": _insight_markdown(headline, so_what, why, caveats, next_action)}, "children": []},
        "copy_json": {"type": "ActionButton", "props": {"label": "Copy JSON", "action": "copy", "value": json_safe(evidence)}, "children": []},
    }
    visual_props = _visual_props(visual_form, evidence)
    elements["visual"] = {"type": _component_for_visual(visual_form), "props": visual_props, "children": []}
    return {
        "kind": "summary_panel",
        "title": title,
        "caption": headline,
        "display_mode": "primary",
        "source_ids": [source_id],
        "source_chunk_ids": [source_chunk_id] if source_chunk_id else [],
        "jsonRenderSpec": {"root": "card", "elements": elements},
        "sections": [
            {"heading": "Headline", "body": headline},
            {"heading": "So what", "body": so_what},
            {"heading": "Why this visual", "body": why},
            {"heading": "Caveats", "body": "; ".join(caveats) if caveats else "None flagged."},
            {"heading": "Next action", "body": next_action},
        ],
    }


def _insight_table_artifact(
    *,
    title: str,
    headline: str,
    so_what: str,
    why: str,
    caveats: list[str],
    next_action: str,
    evidence_rows: Any,
    source_id: int,
    source_chunk_id: str,
) -> dict[str, Any]:
    rows = [row for row in evidence_rows if isinstance(row, dict)] if isinstance(evidence_rows, list) else []
    columns = list(rows[0].keys()) if rows else ["Insight", "Detail"]
    table_rows = rows if rows else [
        {"Insight": "Headline", "Detail": headline},
        {"Insight": "So what", "Detail": so_what},
        {"Insight": "Why this visual", "Detail": why},
        {"Insight": "Caveats", "Detail": "; ".join(caveats) if caveats else "None flagged."},
        {"Insight": "Next action", "Detail": next_action},
    ]
    return {
        "kind": "table",
        "title": title,
        "caption": headline,
        "display_mode": "primary",
        "source_ids": [source_id],
        "source_chunk_ids": [source_chunk_id] if source_chunk_id else [],
        "columns": columns,
        "rows": table_rows,
    }


def _component_for_visual(visual_form: str) -> str:
    return {
        "waterfall": "WaterfallChart",
        "heatmap": "HeatmapMatrix",
        "progress": "ProgressBars",
        "funnel": "FunnelChart",
        "treemap": "TreemapChart",
        "mekko": "MekkoChart",
        "bubble": "BubbleChart",
    }.get(visual_form, "DataTable")


def _visual_props(visual_form: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if visual_form == "heatmap":
        return {
            "rows": evidence.get("matrix") if isinstance(evidence.get("matrix"), list) else [],
            "rowLabel": str(evidence.get("row_column") or "Rows"),
            "columnLabel": str(evidence.get("column_column") or "Columns"),
            "valueLabel": str(evidence.get("measure_column") or "Value"),
        }
    if visual_form == "progress":
        return {"values": evidence.get("values") if isinstance(evidence.get("values"), list) else [], "label": "Progress"}
    if visual_form == "bubble":
        return {
            "values": evidence.get("values") if isinstance(evidence.get("values"), list) else [],
            "xLabel": str(evidence.get("x_column") or "X"),
            "yLabel": str(evidence.get("y_column") or "Y"),
        }
    return {"values": evidence.get("values") if isinstance(evidence.get("values"), list) else [], "label": str(evidence.get("measure_column") or "Value")}


def _insight_markdown(headline: str, so_what: str, why: str, caveats: list[str], next_action: str) -> str:
    return "\n".join(
        [
            f"## Headline\n{headline}",
            f"## So what\n{so_what}",
            f"## Why this visual\n{why}",
            "## Caveats\n" + ("; ".join(caveats) if caveats else "None flagged."),
            f"## Next action\n{next_action}",
        ]
    )


def _insight_narrative(
    *,
    title: str,
    headline: str,
    so_what: str,
    why: str,
    caveats: list[str],
    next_action: str,
    evidence: dict[str, Any],
    source_columns: list[str],
    confidence: float,
) -> dict[str, Any]:
    x_column = str(
        evidence.get("x_column")
        or evidence.get("category_column")
        or evidence.get("label_column")
        or (source_columns[0] if source_columns else "Category")
    )
    y_column = str(
        evidence.get("y_column")
        or evidence.get("measure_column")
        or evidence.get("actual_column")
        or (source_columns[-1] if source_columns else "Value")
    )
    dimension_columns = (
        [str(column) for column in evidence.get("dimension_columns", []) if str(column).strip()]
        if isinstance(evidence.get("dimension_columns"), list)
        else []
    )
    excluded_identifiers = (
        [str(column) for column in evidence.get("excluded_identifier_columns", []) if str(column).strip()]
        if isinstance(evidence.get("excluded_identifier_columns"), list)
        else []
    )
    row_count = int(evidence.get("row_count") or 0)
    value_count = len(evidence.get("values", [])) if isinstance(evidence.get("values"), list) else 0
    is_aggregated = row_count > value_count > 0

    evidence_lines = [
        f"x-axis is {x_column}.",
        f"measure is {'aggregated ' if is_aggregated else ''}{y_column}.",
    ]
    for identifier in excluded_identifiers[:3]:
        evidence_lines.append(f"{identifier} is an identifier/dimension, not a measure.")
    if dimension_columns:
        evidence_lines.append(f"Dimensions available for drill-down: {', '.join(dimension_columns[:5])}.")
    if why:
        evidence_lines.append(why)

    recommended_actions: list[str] = []
    if _is_regional_forecast_narrative(x_column, y_column, dimension_columns, excluded_identifiers):
        recommended_actions.append("Inspect regional mix and validate planning assumptions before action.")
    if next_action:
        recommended_actions.append(next_action)
    if not recommended_actions:
        recommended_actions.append("Inspect the dimension mix behind the chart before taking action.")

    narrative_caveats = list(caveats)
    if is_aggregated:
        narrative_caveats.append("Rows were aggregated before charting, so segment-level variation can be hidden.")
    for identifier in excluded_identifiers[:3]:
        caveat = f"{identifier} is an identifier/dimension, not a measure."
        if caveat not in narrative_caveats:
            narrative_caveats.append(caveat)

    return {
        "headline": headline or title,
        "meaning": f"The chart reads {x_column} on the x-axis against {'aggregated ' if is_aggregated else ''}{y_column}; it describes a pattern, not a causal driver.",
        "evidence": evidence_lines,
        "so_what": so_what or "Use this chart to decide where the next diagnostic pass should focus.",
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
        "follow_up_questions": [
            {
                "id": "data_mix",
                "group": "data",
                "question": f"Which {('/'.join(_preferred_dimensions(dimension_columns, excluded_identifiers)) or 'dimension')} combinations explain the movement in {y_column}?",
                "options": [
                    {"id": "inspect_mix", "label": "Inspect mix"},
                    {"id": "compare_segments", "label": "Compare segments"},
                ],
                "default_option": "inspect_mix",
                "requires_reference": True,
            },
            {
                "id": "business_assumptions",
                "group": "business",
                "question": "Which planning assumption should be validated before action?",
                "options": [
                    {"id": "stockout_allocation", "label": "Stockout/allocation"},
                    {"id": "capacity_timing", "label": "Capacity timing"},
                ],
                "default_option": "stockout_allocation",
                "requires_reference": True,
            },
        ],
        "caveats": list(dict.fromkeys(narrative_caveats)),
        "confidence": "high" if confidence >= 0.85 else "medium" if confidence >= 0.65 else "low",
        "source_columns": [column for column in source_columns if column in {x_column, y_column}] or source_columns[:2],
    }


def _preferred_dimensions(dimension_columns: list[str], excluded_identifiers: list[str]) -> list[str]:
    ordered = []
    for candidate in ["region", "item", *dimension_columns, *excluded_identifiers]:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered[:2]


def _is_regional_forecast_narrative(
    x_column: str,
    y_column: str,
    dimension_columns: list[str],
    excluded_identifiers: list[str],
) -> bool:
    return (
        x_column == "forecast_month"
        and y_column == "forecast_units"
        and "region" in dimension_columns
        and bool(excluded_identifiers)
    )


def json_safe(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _line_recommendation(profile: TableProfile) -> dict[str, Any] | None:
    x_column = next((column for column in profile.columns if column["role"] == "temporal_ordinal"), None)
    y_column = _best_numeric_column(profile)
    if not x_column or not y_column:
        return None
    points = []
    for row in profile.table.rows:
        label = row.get(x_column["name"], "").strip()
        value = _number(row.get(y_column["name"], ""))
        if label and value is not None:
            points.append((label, value, _order_key(label)))
    if len(points) < 3:
        return None
    points.sort(key=lambda item: item[2])
    values = [
        {"label": label, "value": value, "source_id": profile.source["source_id"], "source_chunk_id": profile.source["chunk_id"]}
        for label, value, _ in points[:24]
    ]
    return _chart_recommendation(
        profile,
        chart_type="line",
        title=f"{_subject(profile.table.file_name)} trend",
        reason=f"{x_column['name']} gives an ordered x-axis and {y_column['name']} is numeric across {len(values)} points.",
        source_columns=[x_column["name"], y_column["name"]],
        values=values,
        x_label=x_column["name"],
        y_label=y_column["name"],
        confidence=0.92,
    )


def _bar_recommendation(profile: TableProfile) -> dict[str, Any] | None:
    label_column = _best_label_column(profile)
    value_column = _best_numeric_column(profile)
    if not label_column:
        return None
    totals: dict[str, float] = {}
    for row in profile.table.rows:
        label = row.get(label_column["name"], "").strip()
        if not label:
            continue
        if value_column:
            value = _number(row.get(value_column["name"], ""))
            if value is None:
                continue
            totals[label] = totals.get(label, 0) + value
        else:
            totals[label] = totals.get(label, 0) + 1
    if not totals:
        return None
    values = [
        {"label": label, "value": value, "source_id": profile.source["source_id"], "source_chunk_id": profile.source["chunk_id"]}
        for label, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]
    ]
    measure = value_column["name"] if value_column else "row count"
    return _chart_recommendation(
        profile,
        chart_type="bar",
        title=f"{_subject(profile.table.file_name)} by {label_column['name']}",
        reason=f"{label_column['name']} provides categories and {measure} provides a comparable measure.",
        source_columns=[label_column["name"], measure],
        values=values,
        x_label=label_column["name"],
        y_label=measure,
        confidence=0.84,
    )


def _pie_recommendation(profile: TableProfile) -> dict[str, Any] | None:
    label_column = _best_label_column(profile)
    value_column = _best_numeric_column(profile)
    if not label_column or not value_column or label_column["unique"] > 6:
        return None
    totals: dict[str, float] = {}
    for row in profile.table.rows:
        label = row.get(label_column["name"], "").strip()
        value = _number(row.get(value_column["name"], ""))
        if label and value is not None and value >= 0:
            totals[label] = totals.get(label, 0) + value
    if len(totals) < 2 or len(totals) > 6:
        return None
    values = [
        {"label": label, "value": value, "source_id": profile.source["source_id"], "source_chunk_id": profile.source["chunk_id"]}
        for label, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
    return _chart_recommendation(
        profile,
        chart_type="pie",
        title=f"{_subject(profile.table.file_name)} share by {label_column['name']}",
        reason=f"{label_column['name']} has {len(totals)} categories and {value_column['name']} can be shown as part-to-whole shares.",
        source_columns=[label_column["name"], value_column["name"]],
        values=values,
        x_label=label_column["name"],
        y_label=value_column["name"],
        confidence=0.78,
    )


def _chart_recommendation(
    profile: TableProfile,
    *,
    chart_type: str,
    title: str,
    reason: str,
    source_columns: list[str],
    values: list[dict[str, Any]],
    x_label: str,
    y_label: str,
    confidence: float,
) -> dict[str, Any]:
    rec_id = f"chart_{chart_type}_{_slug('_'.join(source_columns))}"
    return {
        "id": rec_id,
        "artifact_kind": "chart",
        "chart_type": chart_type,
        "title": title,
        "reason": reason,
        "source_columns": source_columns,
        "source_facts": [f"{len(profile.table.rows)} rows", profile.table.file_name],
        "confidence": confidence,
        "follow_up_prompt": f"Create a {chart_type} chart using {source_columns[0]} and {source_columns[-1]}.",
        "artifact": {
            "kind": "chart",
            "title": title,
            "caption": reason,
            "display_mode": "primary",
            "source_ids": [profile.source["source_id"]],
            "source_chunk_ids": [profile.source["chunk_id"]],
            "chart_type": chart_type,
            "x_label": x_label,
            "y_label": y_label,
            "x_column": source_columns[0],
            "y_column": source_columns[-1],
            "source_columns": source_columns,
            "source_facts": [f"{len(profile.table.rows)} rows", profile.table.file_name],
            "values": values,
        },
    }


def _table_recommendation(profile: TableProfile) -> dict[str, Any] | None:
    if not profile.table.columns or not profile.table.rows:
        return None
    title = "Record comparison table" if profile.business_record else f"{_subject(profile.table.file_name)} table"
    reason = (
        "Structured record columns are easier to compare in a table."
        if profile.business_record
        else f"{len(profile.table.columns)} columns and {len(profile.table.rows)} rows are better preserved as a table."
    )
    columns = profile.table.columns[:8]
    return {
        "id": f"table_{_slug(profile.table.file_name)}",
        "artifact_kind": "table",
        "chart_type": None,
        "title": title,
        "reason": reason,
        "source_columns": columns,
        "source_facts": [f"{len(profile.table.rows)} rows", profile.table.file_name],
        "confidence": 0.9 if profile.business_record else 0.72,
        "follow_up_prompt": f"Create a comparison table from {profile.table.file_name}.",
        "artifact": {
            "kind": "table",
            "title": title,
            "caption": reason,
            "display_mode": "primary",
            "source_ids": [profile.source["source_id"]],
            "source_chunk_ids": [profile.source["chunk_id"]],
            "columns": columns,
            "rows": [{column: row.get(column, "") for column in columns} for row in profile.table.rows[:24]],
        },
    }


def _fallback_recommendations(question: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = sources[0] if sources else {"source_id": 1, "chunk_id": ""}
    return [
        {
            "id": "summary_grounded",
            "artifact_kind": "summary_panel",
            "chart_type": None,
            "title": "Grounded summary",
            "reason": "The available source does not expose reliable chart columns, so a summary is safer than forcing a chart.",
            "source_columns": [],
            "source_facts": [str(source.get("file_name") or "source")],
            "confidence": 0.66,
            "follow_up_prompt": "Create a grounded summary from this file.",
        }
    ]


def _rank_recommendations(recommendations: list[dict[str, Any]], *, explicit_chart_type: str | None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique = []
    for item in recommendations:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)

    def score(item: dict[str, Any]) -> tuple[float, float]:
        boost = 0.0
        if item.get("insight"):
            boost += 0.9
        if item.get("visual_form") == "line":
            boost += 0.15
        if explicit_chart_type and item.get("chart_type") == explicit_chart_type:
            boost += 1.0
        if not item.get("insight") and item.get("artifact_kind") == "table" and "item" in item.get("source_columns", []):
            boost += 0.6
        return (boost, float(item.get("confidence") or 0))

    return sorted(unique, key=score, reverse=True)


def _public_recommendation(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "artifact"}


def _public_table_profile(profile: TableProfile) -> dict[str, Any]:
    return {
        "file_id": profile.table.file_id,
        "file_name": profile.table.file_name,
        "row_count": len(profile.table.rows),
        "columns": profile.columns,
        "business_record": profile.business_record,
    }


def _source_for_file(file_id: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    for source in sources:
        if source.get("file_id") == file_id:
            return source
    return sources[0] if sources else None


def _is_discovery_only(question: str) -> bool:
    normalized = question.lower()
    return any(pattern in normalized for pattern in ("what charts", "what docs", "what documents", "what can you make", "available artifacts"))


def _best_numeric_column(profile: TableProfile) -> dict[str, Any] | None:
    candidates = [column for column in profile.columns if column["role"] in {"numeric", "currency", "percentage"} and not _looks_measure_bad(column["name"])]
    if not candidates:
        return None
    preferred = ("revenue", "sales", "amount", "count", "units", "margin", "cost", "score", "rate")
    return sorted(candidates, key=lambda item: (not any(term in _normalize_name(item["name"]) for term in preferred), -item["non_empty"]))[0]


def _best_label_column(profile: TableProfile) -> dict[str, Any] | None:
    candidates = [column for column in profile.columns if column["role"] == "categorical"]
    if not candidates:
        candidates = [column for column in profile.columns if column["role"] == "id_like" and column["unique"] <= 24]
    if not candidates:
        return None
    preferred = ("category", "region", "warehouse", "channel", "answer", "status", "sku")
    return sorted(candidates, key=lambda item: (not any(term in _normalize_name(item["name"]) for term in preferred), item["unique"]))[0]


def _looks_measure_bad(name: str) -> bool:
    normalized = _normalize_name(name)
    return any(term in normalized for term in ("id", "uuid", "timestamp", "date", "time", "email"))


def _looks_id_like(name: str, values: list[str], numeric_count: int) -> bool:
    if name in {"id", "uuid"} or name.endswith("_id") or "identifier" in name or name == "sku":
        return True
    if not values:
        return False
    unique_ratio = len(set(values)) / len(values)
    return unique_ratio > 0.9 and numeric_count / max(1, len(values)) >= 0.75 and not any(term in name for term in ("count", "total", "amount", "score", "revenue"))


def _looks_temporal_or_ordinal(name: str, values: list[str]) -> bool:
    if any(term in name for term in ("date", "time", "month", "quarter", "year", "period", "week", "ordinal", "sequence")):
        return True
    if not values:
        return False
    sample = values[:20]
    temporal = sum(1 for value in sample if _order_key(value)[0] < 3)
    return temporal / max(1, len(sample)) >= 0.7


def _looks_currency(name: str, values: list[str]) -> bool:
    return any(term in name for term in ("revenue", "cost", "price", "amount", "sales", "cogs", "expense")) or any("$" in value or "₩" in value for value in values[:20])


def _number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _order_key(value: str) -> tuple[int, Any]:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            return (0, datetime.strptime(text, fmt))
        except ValueError:
            pass
    quarter = re.search(r"(20\d{2})?[-\s]?Q([1-4])", text, re.IGNORECASE)
    if quarter:
        year = int(quarter.group(1) or 0)
        return (1, year * 10 + int(quarter.group(2)))
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    lowered = text.lower()[:3]
    if lowered in months:
        return (2, months[lowered])
    numeric = _number(text)
    if numeric is not None:
        return (3, numeric)
    return (9, text)


def _average_length(values: list[str]) -> float:
    return sum(len(value) for value in values) / max(1, len(values))


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "_", value.lower()).strip("_")


def _subject(file_name: str) -> str:
    return re.sub(r"\.[a-z0-9]+$", "", file_name, flags=re.IGNORECASE).replace("_", " ").replace("-", " ").strip() or "Source"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9가-힣]+", "_", value.lower()).strip("_")
    return slug[:48] or "artifact"
