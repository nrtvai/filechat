from __future__ import annotations

import re
from typing import Any


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


def timeline_answer(task_contract: dict[str, Any]) -> str:
    language = str(task_contract.get("language") or "")
    if language == "ko":
        return "문서 근거에서 확인되는 일정 신호를 JSON 렌더 로드맵으로 구성했습니다."
    return "I built a JSON-rendered roadmap from the timeline signals in the source."


def build_artifact_options_artifact(question: str, task_contract: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    options = _option_rows(task_contract)
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
        action_id = f"action_{index}"
        elements["options"]["children"].append(option_id)
        elements[option_id] = {"type": "Stack", "props": {"gap": "xs"}, "children": [badge_id, title_id, desc_id, action_id]}
        elements[badge_id] = {"type": "Badge", "props": {"label": option["kind"], "tone": "accent"}, "children": []}
        elements[title_id] = {"type": "TextBlock", "props": {"text": option["label"], "tone": "strong"}, "children": []}
        elements[desc_id] = {"type": "TextBlock", "props": {"text": option["description"], "tone": "muted"}, "children": []}
        elements[action_id] = {
            "type": "ActionButton",
            "props": {"label": option["action_label"], "action": "copy", "value": option["prompt"]},
            "children": [],
        }
    if source_chunk_id:
        elements["card"]["children"].append("source")
        elements["source"] = {"type": "SourceButton", "props": {"label": "Open source", "chunkId": source_chunk_id}, "children": []}
    return {
        "kind": "decision_cards",
        "title": title,
        "caption": caption,
        "display_mode": "primary",
        "source_ids": [source_id],
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


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
                "description": _compact_text(str(source.get("excerpt") or source.get("content") or "Review source material."), 160),
                "status": "planned",
                "sourceChunkId": str(source.get("chunk_id") or ""),
            }
        ]
    language = str(task_contract.get("language") or "")
    title = _timeline_title(task_contract, language)
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
        "jsonRenderSpec": {"root": "card", "elements": elements},
    }


def timeline_contract(task_contract: dict[str, Any]) -> dict[str, Any]:
    updated = dict(task_contract)
    adjustments = list(updated.get("contract_adjustments") or [])
    adjustment = "Rendered roadmap/timeline as a JSON summary artifact because native charts only support numeric bar, line, and pie data."
    if adjustment not in adjustments:
        adjustments.append(adjustment)
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


def _option_rows(task_contract: dict[str, Any]) -> list[dict[str, str]]:
    raw_options = task_contract.get("question_options")
    rows: list[dict[str, str]] = []
    if isinstance(raw_options, list):
        for item in raw_options[:4]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("id") or "").strip()
            description = str(item.get("description") or "").strip()
            option_id = str(item.get("id") or label).strip()
            if not label or not _looks_like_artifact_option(label, description):
                continue
            rows.append(
                {
                    "kind": _kind_for_option(option_id, label),
                    "label": label,
                    "description": description or "Grounded artifact option",
                    "action_label": "Copy request",
                    "prompt": _prompt_for_option(option_id, label),
                }
            )
    if rows:
        return rows
    return [
        {
            "kind": "Timeline",
            "label": "AI adoption roadmap",
            "description": "A JSON-rendered timeline of phases, dates, and milestones found in the source.",
            "action_label": "Copy request",
            "prompt": "Create the AI adoption roadmap as a JSON-rendered timeline artifact.",
        },
        {
            "kind": "Comparison",
            "label": "Groupware operating process comparison",
            "description": "A structured comparison of current and proposed operating workflows.",
            "action_label": "Copy request",
            "prompt": "Create a JSON-rendered comparison of the groupware operating process.",
        },
        {
            "kind": "Draft",
            "label": "Execution plan draft",
            "description": "A grounded Markdown draft with owners, schedule, risks, and next actions.",
            "action_label": "Copy request",
            "prompt": "Create a grounded execution plan draft from this document.",
        },
        {
            "kind": "Summary",
            "label": "Executive summary",
            "description": "A concise leadership-ready summary of strategy, impact, and validation plan.",
            "action_label": "Copy request",
            "prompt": "Create a grounded executive summary draft from this document.",
        },
    ]


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
    if any(term in normalized for term in ("draft", "doc", "summary", "plan", "보고서", "문서", "초안", "계획")):
        return "Draft"
    if any(term in normalized for term in ("comparison", "workflow", "프로세스", "비교")):
        return "Comparison"
    return "Chart"


def _prompt_for_option(option_id: str, label: str) -> str:
    if is_timeline_request(f"{option_id} {label}", {}):
        return f"Create `{label}` as a JSON-rendered timeline artifact."
    return f"Create `{label}` as a grounded FileChat artifact."


def _timeline_title(task_contract: dict[str, Any], language: str) -> str:
    direction = task_contract.get("user_direction") if isinstance(task_contract.get("user_direction"), dict) else {}
    selected = str(direction.get("selected_option") or "")
    options = task_contract.get("question_options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and str(option.get("id") or "") == selected:
                label = str(option.get("label") or "").strip()
                if label:
                    return label
    return "AI 도입 로드맵" if language == "ko" else "AI Adoption Roadmap"


def _timeline_items(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for source in sources:
        content = str(source.get("content") or source.get("excerpt") or "")
        chunk_id = str(source.get("chunk_id") or "")
        for sentence in _sentences(content):
            match = TIMELINE_MARKER_RE.search(sentence)
            if not match:
                continue
            label = match.group(1).replace(" ", "")
            description = _compact_text(sentence, 180)
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


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|[;\n]+", normalized)
    return [part.strip(" -•\t") for part in parts if part.strip(" -•\t")]


def _compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."
