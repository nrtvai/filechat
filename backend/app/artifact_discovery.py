from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import re
from typing import Any

from .survey import ParsedTable, parse_table


DISCOVERY_PATTERNS = (
    "what charts",
    "what chart",
    "what docs",
    "what documents",
    "what can you make",
    "can you make with this",
    "available outputs",
    "available artifacts",
)

TIMELINE_PATTERNS = (
    "timeline",
    "roadmap",
    "gantt",
    "milestone",
    "schedule",
    "phases",
    "로드맵",
    "타임라인",
    "간트",
    "마일스톤",
    "일정",
    "단계",
    "추진",
)

TIMELINE_MARKER_RE = re.compile(
    r"(20\d{2}[./-]\s*\d{1,2}|20\d{2}\s*년\s*\d{1,2}\s*월|\d{1,2}\s*월|Q[1-4]|[1-4]\s*분기|\d+\s*주)",
    re.IGNORECASE,
)


def is_artifact_discovery_request(question: str, task_contract: dict[str, Any]) -> bool:
    normalized = question.lower()
    if any(pattern in normalized for pattern in DISCOVERY_PATTERNS):
        return True
    options = task_contract.get("question_options")
    if isinstance(options, list) and options and any(term in normalized for term in ("charts", "docs", "documents", "artifacts")):
        return True
    return False


def is_timeline_request(question: str, task_contract: dict[str, Any]) -> bool:
    normalized = question.lower()
    selected = str((task_contract.get("user_direction") or {}).get("selected_option") or "").lower()
    deliverable = str(task_contract.get("deliverable") or "").lower()
    haystack = " ".join([normalized, selected, deliverable])
    return any(pattern in haystack for pattern in TIMELINE_PATTERNS)


def discovery_answer(task_contract: dict[str, Any]) -> str:
    language = str(task_contract.get("language") or "")
    if language == "ko":
        return "만들 수 있는 차트와 문서 옵션을 정리했습니다. 원하는 항목을 선택해 다시 요청하면 JSON 렌더 산출물로 생성합니다."
    return "I mapped the charts and docs FileChat can create from this source. Choose one and ask for it to generate a JSON-rendered artifact."


def timeline_answer(task_contract: dict[str, Any], sources: list[dict[str, Any]] | None = None) -> str:
    language = str(task_contract.get("language") or "")
    subject = _source_subject(sources or [])
    if language == "ko":
        return f"{subject} 근거에서 타임라인 패널과 요약 패널을 만들었습니다. 두 패널 모두 출처 청크에 연결했습니다."
    return f"I created two source-grounded panels from {subject}: a timeline panel and a concise summary panel. Both panels are linked to source chunks."


def build_artifact_options_artifact(question: str, task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    options = _option_rows(task_contract, sources)
    source = sources[0] if sources else {}
    source_id = int(source.get("source_id") or 1)
    source_chunk_id = str(source.get("chunk_id") or "")
    language = str(task_contract.get("language") or "")
    title = "생성 가능한 차트와 문서" if language == "ko" else "Available Charts And Docs"
    caption = "첨부 문서에서 바로 만들 수 있는 산출물 후보입니다." if language == "ko" else "Artifact options grounded in the attached source."

    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": title, "caption": caption},
            "children": ["intro", "options"],
        },
        "intro": {
            "type": "TextBlock",
            "props": {
                "text": "요청을 그대로 장문 답변으로 저장하지 않고, 선택 가능한 산출물 후보를 구조화했습니다."
                if language == "ko"
                else "FileChat will generate the selected item as a structured artifact instead of a long prose answer.",
                "tone": "muted",
            },
            "children": [],
        },
        "options": {"type": "Stack", "props": {"gap": "sm"}, "children": []},
    }
    for index, option in enumerate(options, start=1):
        option_id = f"option_{index}"
        badge_id = f"badge_{index}"
        title_id = f"title_{index}"
        desc_id = f"description_{index}"
        elements["options"]["children"].append(option_id)
        elements[option_id] = {"type": "Stack", "props": {"gap": "xs"}, "children": [badge_id, title_id, desc_id]}
        elements[badge_id] = {"type": "Badge", "props": {"label": option["kind"], "tone": "accent"}, "children": []}
        elements[title_id] = {"type": "TextBlock", "props": {"text": option["label"], "tone": "strong"}, "children": []}
        elements[desc_id] = {"type": "TextBlock", "props": {"text": option["description"], "tone": "muted"}, "children": []}
    if source_chunk_id:
        elements["card"]["children"].append("source")
        elements["source"] = {"type": "SourceButton", "props": {"label": "Open source", "chunkId": source_chunk_id}, "children": []}
    return {
        "kind": "decision_cards",
        "title": title,
        "caption": caption,
        "display_mode": "primary",
        "source_ids": [source_id],
        "decision_options": [_decision_option_for_row(option) for option in options],
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def build_timeline_artifacts(question: str, task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = build_timeline_artifact(question, task_contract, sources)
    if not timeline:
        return []
    summary = build_timeline_summary_artifact(question, task_contract, sources)
    return [timeline, summary] if summary else [timeline]


def build_timeline_artifact(question: str, task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sources:
        return None
    items = _timeline_items(sources)
    if not items:
        source = sources[0]
        items = [
            {
                "date": "1",
                "label": "Discovery",
                "description": _fallback_timeline_description(source),
                "status": "planned",
                "sourceChunkId": str(source.get("chunk_id") or ""),
            }
        ]
    language = str(task_contract.get("language") or "")
    title = _timeline_title(task_contract, language, sources)
    caption = "문서에서 확인되는 일정/단계 표현을 구조화했습니다." if language == "ko" else "Structured from schedule and phase signals in the source."
    elements = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": title, "caption": caption},
            "children": ["timeline"],
        },
        "timeline": {"type": "Timeline", "props": {"items": items[:8]}, "children": []},
    }
    return {
        "kind": "summary_panel",
        "title": title,
        "caption": caption,
        "display_mode": "primary",
        "source_ids": list(dict.fromkeys(int(source.get("source_id") or 1) for source in sources[:4])),
        "source_chunk_ids": [chunk_id for chunk_id in _source_chunk_ids(sources)[:4] if chunk_id],
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def build_timeline_summary_artifact(question: str, task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sources:
        return None
    language = str(task_contract.get("language") or "")
    subject = _source_subject(sources)
    for source in sources:
        table = parse_table(
            str(source.get("content") or ""),
            str(source.get("file_id") or source.get("source_id") or ""),
            str(source.get("file_name") or "source.csv"),
        )
        if table:
            sections = _table_summary_sections(table, language)
            if sections:
                return _summary_panel_from_sections(
                    title=f"{subject} summary",
                    caption="Concise summary grounded in the attached source rows.",
                    sections=sections,
                    sources=[source],
                )
    source = sources[0]
    body = _fallback_timeline_description(source)
    if not body:
        return None
    return _summary_panel_from_sections(
        title=f"{subject} summary",
        caption="Concise summary grounded in the attached source.",
        sections=[{"heading": "Source Summary", "body": body}],
        sources=[source],
    )


def timeline_contract(task_contract: dict[str, Any]) -> dict[str, Any]:
    updated = dict(task_contract)
    adjustments = list(updated.get("contract_adjustments") or [])
    adjustment = "Rendered roadmap/timeline as a JSON summary artifact because native charts only support numeric bar, line, and pie data."
    if adjustment not in adjustments:
        adjustments.append(adjustment)
    summary_adjustment = "Included a companion summary panel so timeline requests expose both chronology and concise source context."
    if summary_adjustment not in adjustments:
        adjustments.append(summary_adjustment)
    updated["required_outputs"] = ["summary_panel"]
    updated["primary_outputs"] = ["summary_panel"]
    updated["supporting_outputs"] = []
    updated["contract_adjustments"] = adjustments
    executable = dict(updated.get("executable_contract") or {})
    if executable:
        executable["required_outputs"] = ["summary_panel"]
        executable["primary_outputs"] = ["summary_panel"]
        executable["supporting_outputs"] = []
        executable["contract_adjustments"] = adjustments
        updated["executable_contract"] = executable
    return updated


def _option_rows(task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_options = task_contract.get("question_options")
    rows: list[dict[str, Any]] = []
    if isinstance(raw_options, list):
        for item in raw_options[:4]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("id") or "").strip()
            description = str(item.get("description") or "").strip()
            option_id = str(item.get("id") or label).strip()
            if not label or not _looks_like_artifact_option(label, description):
                continue
            kind = _kind_for_option(option_id, label)
            artifact_kind = _artifact_kind_for_kind(kind)
            rows.append(
                {
                    "id": _option_id(option_id, label),
                    "kind": _kind_for_option(option_id, label),
                    "label": label,
                    "description": description or "Grounded artifact option",
                    "artifact_kind": artifact_kind,
                    "chart_type": "bar" if artifact_kind == "chart" else None,
                    "produce_payload": {
                        "artifact_kind": artifact_kind,
                        "label": label,
                        "description": description or "Grounded artifact option",
                        "instruction": _prompt_for_option(option_id, label),
                    },
                }
            )
    if rows:
        return rows
    return _fallback_option_rows(sources)


def _fallback_option_rows(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subject = _source_subject(sources)
    rows: list[dict[str, Any]] = []
    if _source_has_timeline_signals(sources):
        rows.append(
            _row(
                option_id="source_timeline",
                kind="Timeline",
                label=f"{subject} timeline",
                description="A JSON-rendered timeline of dates, phases, or milestones found in the source.",
                artifact_kind="summary_panel",
                instruction=f"Create a JSON-rendered timeline artifact grounded in {subject}.",
            )
        )
    rows.extend(
        [
            _row(
                option_id="grounded_summary",
                kind="Summary",
                label=f"{subject} summary",
                description="A concise summary grounded in the attached source.",
                artifact_kind="summary_panel",
                instruction=f"Create a grounded summary artifact from {subject}.",
            ),
            _row(
                option_id="source_draft",
                kind="Draft",
                label=f"{subject} draft",
                description="A Markdown draft that uses only the attached source material.",
                artifact_kind="file_draft",
                instruction=f"Create a grounded Markdown draft from {subject}.",
            ),
        ]
    )
    return rows[:4]


def _row(
    *,
    option_id: str,
    kind: str,
    label: str,
    description: str,
    artifact_kind: str,
    instruction: str,
) -> dict[str, Any]:
    return {
        "id": option_id,
        "kind": kind,
        "label": label,
        "description": description,
        "artifact_kind": artifact_kind,
        "chart_type": "bar" if artifact_kind == "chart" else None,
        "produce_payload": {
            "artifact_kind": artifact_kind,
            "label": label,
            "description": description,
            "instruction": instruction,
        },
    }


def _decision_option_for_row(row: dict[str, Any]) -> dict[str, Any]:
    option = {
        "id": str(row.get("id") or _option_id(str(row.get("kind") or ""), str(row.get("label") or ""))),
        "label": str(row.get("label") or "Artifact"),
        "description": str(row.get("description") or ""),
        "artifact_kind": str(row.get("artifact_kind") or "summary_panel"),
        "produce_payload": row.get("produce_payload") if isinstance(row.get("produce_payload"), dict) else {},
    }
    chart_type = str(row.get("chart_type") or "").strip()
    if chart_type:
        option["chart_type"] = chart_type
    return option


def _looks_like_artifact_option(label: str, description: str) -> bool:
    normalized = f"{label} {description}".lower()
    return any(
        term in normalized
        for term in (
            "chart",
            "graph",
            "timeline",
            "roadmap",
            "workflow",
            "comparison",
            "draft",
            "document",
            "summary",
            "report",
            "차트",
            "그래프",
            "로드맵",
            "타임라인",
            "프로세스",
            "비교",
            "계획서",
            "보고서",
            "문서",
            "요약",
            "초안",
        )
    )


def _kind_for_option(option_id: str, label: str) -> str:
    normalized = f"{option_id} {label}".lower()
    if is_timeline_request(normalized, {}):
        return "Timeline"
    if any(term in normalized for term in ("summary", "요약")):
        return "Summary"
    if any(term in normalized for term in ("draft", "doc", "summary", "plan", "보고서", "문서", "초안", "계획")):
        return "Draft"
    if any(term in normalized for term in ("comparison", "workflow", "프로세스", "비교")):
        return "Comparison"
    return "Chart"


def _artifact_kind_for_kind(kind: str) -> str:
    if kind == "Timeline":
        return "summary_panel"
    if kind == "Summary":
        return "summary_panel"
    if kind == "Draft":
        return "file_draft"
    if kind == "Comparison":
        return "comparison"
    return "chart"


def _option_id(option_id: str, label: str) -> str:
    raw = option_id or label
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return slug or "artifact_option"


def _prompt_for_option(option_id: str, label: str) -> str:
    if is_timeline_request(f"{option_id} {label}", {}):
        return f"Create `{label}` as a JSON-rendered timeline artifact."
    return f"Create `{label}` as a grounded FileChat artifact."


def _source_subject(sources: list[dict[str, Any]]) -> str:
    source = sources[0] if sources else {}
    file_name = str(source.get("file_name") or source.get("source_label") or "Source").strip()
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", file_name).strip()
    words = re.sub(r"[_-]+", " ", stem or "Source").strip()
    return words[:1].upper() + words[1:] if words else "Source"


def _source_has_timeline_signals(sources: list[dict[str, Any]]) -> bool:
    for source in sources[:4]:
        content = str(source.get("content") or source.get("excerpt") or "")
        if TIMELINE_MARKER_RE.search(content):
            return True
        if any(pattern in content.lower() for pattern in TIMELINE_PATTERNS):
            return True
    return False


def _timeline_title(task_contract: dict[str, Any], language: str, sources: list[dict[str, Any]] | None = None) -> str:
    direction = task_contract.get("user_direction") if isinstance(task_contract.get("user_direction"), dict) else {}
    selected = str(direction.get("selected_option") or "")
    options = task_contract.get("question_options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and str(option.get("id") or "") == selected:
                label = str(option.get("label") or "").strip()
                if label:
                    return label
    subject = _source_subject(sources or [])
    return f"{subject} 타임라인" if language == "ko" else f"{subject} timeline"


def _timeline_items(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for source in sources:
        for item in _table_timeline_items(source):
            if any(existing["date"] == item["date"] and existing["description"] == item["description"] for existing in items):
                continue
            items.append(item)
            if len(items) >= 8:
                return items
    if items:
        return items
    for source in sources:
        content = str(source.get("content") or source.get("excerpt") or "")
        chunk_id = str(source.get("chunk_id") or "")
        for sentence in _sentences(content):
            match = TIMELINE_MARKER_RE.search(sentence)
            if not match:
                continue
            label = match.group(1).replace(" ", "")
            description = _timeline_sentence_description(sentence)
            if any(existing["date"] == label and existing["description"] == description for existing in items):
                continue
            items.append(
                {
                    "date": label,
                    "label": label,
                    "description": description,
                    "status": "planned",
                    "sourceChunkId": chunk_id,
                }
            )
            if len(items) >= 8:
                return items
    return items


def _table_timeline_items(source: dict[str, Any]) -> list[dict[str, str]]:
    table = parse_table(
        str(source.get("content") or ""),
        str(source.get("file_id") or source.get("source_id") or ""),
        str(source.get("file_name") or "source.csv"),
    )
    if not table:
        return []
    date_column = _date_column(table.columns)
    if not date_column:
        return []
    dated_rows: list[tuple[date | None, str, dict[str, str]]] = []
    for row in table.rows:
        raw_date = str(row.get(date_column) or "").strip()
        if not raw_date:
            continue
        parsed = _parse_date(raw_date)
        dated_rows.append((parsed, raw_date, row))
    if not dated_rows:
        return []
    parsed_months = {(parsed.year, parsed.month) for parsed, _, _ in dated_rows if parsed}
    group_by_quarter = len(parsed_months) > 8
    grouped: dict[tuple[Any, ...], list[tuple[date | None, str, dict[str, str]]]] = defaultdict(list)
    for parsed, raw_date, row in dated_rows:
        if parsed:
            key = ("quarter", parsed.year, (parsed.month - 1) // 3 + 1) if group_by_quarter else ("month", parsed.year, parsed.month)
        else:
            key = ("raw", raw_date)
        grouped[key].append((parsed, raw_date, row))
    items: list[dict[str, str]] = []
    chunk_id = str(source.get("chunk_id") or "")
    for key in sorted(grouped, key=_timeline_group_sort_key):
        rows = grouped[key]
        label = _timeline_group_label(key)
        items.append(
            {
                "date": label,
                "label": f"{label} source records",
                "description": _timeline_group_description(table, rows),
                "status": "source",
                "sourceChunkId": chunk_id,
            }
        )
    return items


def _timeline_group_sort_key(key: tuple[Any, ...]) -> tuple[int, int, str]:
    if len(key) == 3 and key[0] in {"month", "quarter"}:
        return (int(key[1]), int(key[2]), str(key[0]))
    return (9999, 99, str(key[-1]))


def _timeline_group_label(key: tuple[Any, ...]) -> str:
    if len(key) == 3 and key[0] == "quarter":
        return f"{key[1]} Q{key[2]}"
    if len(key) == 3 and key[0] == "month":
        return f"{key[1]}-{int(key[2]):02d}"
    return str(key[-1])


def _timeline_group_description(table: ParsedTable, rows: list[tuple[date | None, str, dict[str, str]]]) -> str:
    row_dicts = [row for _, _, row in rows]
    count = len(row_dicts)
    parsed_dates = sorted(parsed for parsed, _, _ in rows if parsed)
    range_text = ""
    if parsed_dates:
        start = parsed_dates[0].isoformat()
        end = parsed_dates[-1].isoformat()
        range_text = f" from {start} to {end}" if start != end else f" on {start}"
    units = _sum_column(row_dicts, _first_column(table.columns, ("units_sold", "units", "quantity", "count")))
    revenue = _sum_column(row_dicts, _first_column(table.columns, ("revenue", "sales", "amount", "total")))
    top_item = _top_value(row_dicts, _first_column(table.columns, ("product", "item", "record", "id")))
    parts = [f"{count:,} source record{'s' if count != 1 else ''}{range_text}"]
    if units is not None:
        parts.append(f"{_format_number(units)} units")
    if revenue is not None:
        parts.append(f"${revenue:,.0f} revenue")
    if top_item:
        parts.append(f"top item by record count: {top_item}")
    return "; ".join(parts) + "."


def _table_summary_sections(table: ParsedTable, language: str) -> list[dict[str, str]]:
    rows = table.rows
    if not rows:
        return []
    date_col = _date_column(table.columns)
    dates = sorted(parsed for parsed in (_parse_date(str(row.get(date_col) or "")) for row in rows) if parsed) if date_col else []
    units = _sum_column(rows, _first_column(table.columns, ("units_sold", "units", "quantity", "count")))
    revenue = _sum_column(rows, _first_column(table.columns, ("revenue", "sales", "amount", "total")))
    margin = _mean_column(rows, _first_column(table.columns, ("gross_margin_rate", "gross_margin", "margin_rate", "margin")))
    top_item = _top_value(rows, _first_column(table.columns, ("product", "item", "record", "id")))
    top_region = _top_value(rows, _first_column(table.columns, ("region", "market", "territory")))
    top_channel = _top_value(rows, _first_column(table.columns, ("channel", "segment")))

    if language == "ko":
        scope = f"{len(rows):,}개 행과 {len(table.columns):,}개 열을 기준으로 요약했습니다."
        if dates:
            scope += f" 날짜 범위는 {dates[0].isoformat()}부터 {dates[-1].isoformat()}까지입니다."
        totals = []
        if units is not None:
            totals.append(f"{_format_number(units)}개 판매")
        if revenue is not None:
            totals.append(f"매출 ${revenue:,.0f}")
        if margin is not None:
            totals.append(f"평균 마진율 {margin:.1%}")
        mix = ", ".join(part for part in [f"상위 항목 {top_item}" if top_item else "", f"상위 지역 {top_region}" if top_region else "", f"상위 채널 {top_channel}" if top_channel else ""] if part)
        return [
            {"heading": "범위", "body": scope},
            {"heading": "합계", "body": ", ".join(totals) if totals else "첨부 데이터에서 확인되는 주문 행을 요약했습니다."},
            {"heading": "구성", "body": mix or "항목, 지역, 채널 구성은 첨부 데이터 행 기준입니다."},
            {"heading": "해석 주의", "body": "집계 요약은 항목, 지역, 채널별 세부 변동을 가릴 수 있습니다."},
        ]

    scope = f"Summarized {len(rows):,} rows across {len(table.columns):,} columns."
    if dates:
        scope += f" Order dates run from {dates[0].isoformat()} through {dates[-1].isoformat()}."
    totals = []
    if units is not None:
        totals.append(f"{_format_number(units)} units sold")
    if revenue is not None:
        totals.append(f"${revenue:,.0f} revenue")
    if margin is not None:
        totals.append(f"{margin:.1%} average gross margin")
    mix = ", ".join(part for part in [f"top item {top_item}" if top_item else "", f"top region {top_region}" if top_region else "", f"top channel {top_channel}" if top_channel else ""] if part)
    return [
        {"heading": "Scope", "body": scope},
        {"heading": "Totals", "body": ", ".join(totals) if totals else "Summarized the retrieved source rows."},
        {"heading": "Mix", "body": mix or "Item, region, and channel mix are summarized from the attached rows."},
        {"heading": "Caveat", "body": "Aggregated summaries may hide item, region, or channel-level variation."},
    ]


def _summary_panel_from_sections(
    *,
    title: str,
    caption: str,
    sections: list[dict[str, str]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    elements: dict[str, Any] = {
        "card": {
            "type": "ArtifactCard",
            "props": {"title": title, "caption": caption},
            "children": [],
        }
    }
    for index, section in enumerate(sections[:6], start=1):
        element_id = f"section_{index}"
        elements[element_id] = {
            "type": "TextBlock",
            "props": {"text": f"{section['heading']}: {section['body']}"},
            "children": [],
        }
        elements["card"]["children"].append(element_id)
    chunk_ids = _source_chunk_ids(sources)
    if chunk_ids:
        elements["source"] = {"type": "SourceButton", "props": {"label": "Open source", "chunkId": chunk_ids[0]}, "children": []}
        elements["card"]["children"].append("source")
    return {
        "kind": "summary_panel",
        "title": title,
        "caption": caption,
        "display_mode": "supporting",
        "source_ids": _source_ids(sources),
        "source_chunk_ids": chunk_ids,
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def _date_column(columns: list[str]) -> str:
    for column in columns:
        normalized = column.lower()
        if "date" in normalized or "month" in normalized or "arrival" in normalized:
            return column
    return ""


def _parse_date(value: str) -> date | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m", "%Y/%m", "%Y.%m"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def _first_column(columns: list[str], candidates: tuple[str, ...]) -> str:
    normalized = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    for column in columns:
        lowered = column.lower()
        if any(candidate.lower() in lowered for candidate in candidates):
            return column
    return ""


def _sum_column(rows: list[dict[str, str]], column: str) -> float | None:
    if not column:
        return None
    total = 0.0
    found = False
    for row in rows:
        number = _number(row.get(column, ""))
        if number is not None:
            total += number
            found = True
    return total if found else None


def _mean_column(rows: list[dict[str, str]], column: str) -> float | None:
    if not column:
        return None
    values = [_number(row.get(column, "")) for row in rows]
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _top_value(rows: list[dict[str, str]], column: str) -> str:
    if not column:
        return ""
    counts = Counter(str(row.get(column) or "").strip() for row in rows if str(row.get(column) or "").strip())
    if not counts:
        return ""
    value, count = counts.most_common(1)[0]
    return f"{value} ({count:,} orders)"


def _number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_number(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"


def _source_ids(sources: list[dict[str, Any]]) -> list[int]:
    return list(dict.fromkeys(int(source.get("source_id") or 1) for source in sources[:4]))


def _source_chunk_ids(sources: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(source.get("chunk_id") or "") for source in sources[:4] if str(source.get("chunk_id") or "")))


def _timeline_sentence_description(sentence: str) -> str:
    if _looks_like_delimited_row(sentence):
        return _delimited_row_description(sentence)
    return _compact_text(sentence, 180)


def _fallback_timeline_description(source: dict[str, Any]) -> str:
    content = str(source.get("content") or source.get("excerpt") or "")
    table = parse_table(
        content,
        str(source.get("file_id") or source.get("source_id") or ""),
        str(source.get("file_name") or "source.csv"),
    )
    if table:
        return f"Structured {len(table.rows):,} rows from {source.get('file_name') or 'the attached source'}; no finer timeline milestones were isolated."
    if _looks_like_delimited_row(content):
        return "Structured source rows were detected, but no concise timeline milestones were isolated."
    return _compact_text(content or "Review source material.", 160)


def _looks_like_delimited_row(text: str) -> bool:
    return text.count(",") >= 4 or text.count("\t") >= 4


def _delimited_row_description(text: str) -> str:
    parts = [part.strip() for part in re.split(r",|\t", text) if part.strip()]
    if len(parts) >= 9 and re.match(r"^[A-Za-z]{1,4}-?\d+", parts[0]):
        record_id, item = parts[0], parts[1]
        units = parts[4] if len(parts) > 4 else ""
        revenue = parts[5] if len(parts) > 5 else ""
        order_date = parts[8] if len(parts) > 8 else ""
        detail = f"Structured record {record_id} for item {item}"
        if order_date:
            detail += f" on {order_date}"
        if units:
            detail += f"; {units} units"
        if revenue:
            detail += f" and ${revenue} revenue"
        return detail + "."
    return _compact_text("Structured row with timeline date from the attached source.", 180)


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|[;\n]+", normalized)
    return [part.strip(" -•\t") for part in parts if part.strip(" -•\t")]


def _compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."
