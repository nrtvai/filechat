from __future__ import annotations

import re
from typing import Any

from .agent_runtime import build_capability_snapshot
from .database import connect
from .settings_store import current_app_settings


CAPABILITY_REGISTRY: dict[str, dict[str, Any]] = {
    "survey_analyzer": {
        "description": "Profiles survey rows and groups open-text responses into grounded themes.",
        "input_modes": ["text/csv", "text/tab-separated-values", "text/plain"],
        "output_modes": ["chart", "summary_panel"],
    },
    "table_extractor": {
        "description": "Parses CSV/TSV/table-shaped source text and computes deterministic aggregates.",
        "input_modes": ["text/csv", "text/tab-separated-values", "text/plain"],
        "output_modes": ["table", "chart"],
    },
    "chart_builder": {
        "description": "Turns typed label/value data into safe chart artifacts.",
        "input_modes": ["application/json"],
        "output_modes": ["chart"],
    },
    "draft_writer": {
        "description": "Creates grounded Markdown or JSON drafts from cited source facts.",
        "input_modes": ["text/plain"],
        "output_modes": ["file_draft"],
    },
    "artifact_repairer": {
        "description": "Repairs generated artifacts using validator errors and safe schemas.",
        "input_modes": ["application/json"],
        "output_modes": ["chart", "table", "summary_panel", "file_draft"],
    },
    "citation_reviewer": {
        "description": "Checks answer and artifact source coverage.",
        "input_modes": ["text/plain", "application/json"],
        "output_modes": ["review"],
    },
}

CREATE_REQUEST_PATTERNS = (
    "make",
    "create",
    "draft",
    "write",
    "generate",
    "chart",
    "graph",
    "table",
    "report",
    "new file",
    "만들",
    "작성",
    "제작",
    "생성",
    "차트",
    "그래프",
    "표",
    "보고서",
    "문서",
    "자료",
)
WEB_SEARCH_PATTERNS = ("latest", "current", "today", "recent", "web search", "internet", "online")


def classify_request(question: str) -> str:
    normalized = question.lower()
    return "create" if any(pattern in normalized for pattern in CREATE_REQUEST_PATTERNS) else "ask"


def requested_outputs(question: str) -> list[str]:
    normalized = question.lower()
    outputs: list[str] = []
    if any(word in normalized for word in ("chart", "graph", "plot", "survey result")):
        outputs.append("chart")
    if any(word in normalized for word in ("draft", "new file", "write a file", "document", "report")) or any(
        word in task_text(question) for word in ("초안", "보고서", "문서", "자료", "작성", "제작")
    ):
        outputs.append("file_draft")
    if any(word in normalized for word in ("table", "comparison")) or "표" in question:
        outputs.append("table")
    return outputs or ["answer"]


def task_text(question: str) -> str:
    return question.lower()


def is_broad_create_request(question: str, outputs: list[str] | None = None) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower()).strip()
    compact = question.strip()
    selected_outputs = outputs or requested_outputs(question)
    broad_terms = (
        "analysis material",
        "analysis materials",
        "analysis deck",
        "make analysis",
        "create analysis",
        "report",
        "insight",
        "분석 자료",
        "분석자료",
        "자료 제작",
        "자료작성",
        "보고서",
        "인사이트",
    )
    has_broad_term = any(term in normalized for term in broad_terms) or any(term in compact for term in broad_terms)
    has_specific_output = any(
        word in normalized
        for word in ("chart", "graph", "table", "markdown", "json", "slides", "ppt", "csv")
    ) or any(word in compact for word in ("차트", "그래프", "표", "마크다운", "슬라이드"))
    return classify_request(question) == "create" and has_broad_term and ("file_draft" in selected_outputs) and not has_specific_output


def requires_web_search(question: str) -> bool:
    normalized = question.lower()
    return any(pattern in normalized for pattern in WEB_SEARCH_PATTERNS)


def _ready_files(session_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.type, f.status, f.size, f.chunk_count
            FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            WHERE sf.session_id = ?
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _model_assignment(model: str, role: str, *, reasoning_effort: str = "none", required_capabilities: list[str] | None = None) -> dict[str, Any]:
    return {
        "model": model,
        "role": role,
        "reasoning_effort": reasoning_effort,
        "required_capabilities": required_capabilities or [],
    }


def build_preflight(session_id: str, question: str) -> dict[str, Any]:
    settings = current_app_settings()
    files = _ready_files(session_id)
    ready_files = [file for file in files if file["status"] == "ready"]
    file_types = sorted({str(file["type"]).lower() for file in ready_files})
    outputs = requested_outputs(question)
    intent = classify_request(question)
    web_needed = requires_web_search(question)
    is_chart = "chart" in outputs
    is_survey = is_chart and (
        "survey" in question.lower()
        or "설문" in question
        or any(file_type in {"csv", "tsv"} for file_type in file_types)
    )
    is_ambiguous_analysis = bool(re.search(r"\b(analy[sz]e|insight|implication|compare)\b", question.lower())) or "분석" in question
    routing_mode = settings["model_routing_mode"]
    reasoning_effort = settings["reasoning_effort"]
    needs_reasoning = routing_mode == "deep" or is_ambiguous_analysis or is_survey

    tools = ["local_chunk_retrieval", "citation_reviewer"]
    subagents = ["citation_reviewer"]
    if is_survey:
        tools.extend(["csv_parser", "survey_profiler", "chart_builder"])
        subagents.extend(["table_extractor", "survey_analyzer", "chart_builder"])
    elif is_chart:
        tools.append("chart_builder")
        subagents.append("chart_builder")
    if "file_draft" in outputs:
        subagents.append("draft_writer")
    subagents.append("artifact_repairer")

    cost_tier = "low"
    if routing_mode == "deep" or (needs_reasoning and len(ready_files) > 2):
        cost_tier = "high"
    elif needs_reasoning or len(ready_files) > 1:
        cost_tier = "medium"

    requires_approval = bool(
        settings["high_cost_confirmation"]
        and (
            routing_mode == "deep"
            or cost_tier == "high"
            or (web_needed and settings["web_search_enabled"])
        )
    )

    model_assignments = {
        "orchestrator": _model_assignment(
            settings["orchestrator_model"],
            "Preflight planning and model routing",
            reasoning_effort=reasoning_effort if needs_reasoning else "none",
            required_capabilities=["reasoning"] if needs_reasoning else [],
        ),
        "analysis": _model_assignment(
            settings["analysis_model"],
            "Semantic survey clustering and multi-source synthesis",
            reasoning_effort=reasoning_effort if needs_reasoning else "none",
            required_capabilities=["reasoning"] if needs_reasoning else [],
        ),
        "writing": _model_assignment(
            settings["writing_model"],
            "Grounded answer and semantic artifact contract generation",
            required_capabilities=["response_format"],
        ),
        "repair": _model_assignment(
            settings["repair_model"],
            "Artifact schema repair",
            required_capabilities=["structured_outputs", "response_format"],
        ),
        "embedding": _model_assignment(settings["embedding_model"], "Local retrieval embeddings"),
        "ocr": _model_assignment(settings["ocr_model"], "Document extraction OCR"),
    }

    execution_plan = {
        "intent": intent,
        "requested_outputs": outputs,
        "file_types": file_types,
        "ready_file_count": len(ready_files),
        "total_file_count": len(files),
        "cost_tier": cost_tier,
        "routing_mode": routing_mode,
        "reasoning_required": needs_reasoning,
        "web_search_required": web_needed,
        "web_search_enabled": settings["web_search_enabled"],
        "requires_approval": requires_approval,
        "subagents": [
            {
                "id": subagent,
                "capability": CAPABILITY_REGISTRY[subagent]["description"],
                "output_modes": CAPABILITY_REGISTRY[subagent]["output_modes"],
            }
            for subagent in dict.fromkeys(subagents)
        ],
        "tools": list(dict.fromkeys(tools)),
    }
    execution_plan["capability_snapshot"] = build_capability_snapshot(
        question=question,
        execution_plan=execution_plan,
        planner_contract={"required_outputs": outputs, "intent": intent},
    )
    warnings: list[str] = []
    if is_survey and not any(file_type in {"csv", "tsv", "txt"} for file_type in file_types):
        warnings.append("Survey chart requested, but no ready CSV/TSV/plain-text survey source was detected.")
    return {
        "execution_plan": execution_plan,
        "model_assignments": model_assignments,
        "quality_warnings": warnings,
    }


def model_recommendations(task: str) -> dict[str, Any]:
    settings = current_app_settings()
    outputs = requested_outputs(task)
    needs_reasoning = "chart" in outputs or any(word in task.lower() for word in ("analysis", "insight", "compare", "report"))
    return {
        "task": task,
        "routing_mode": settings["model_routing_mode"],
        "recommendations": {
            "orchestrator_model": settings["orchestrator_model"],
            "analysis_model": settings["analysis_model"] if needs_reasoning else settings["writing_model"],
            "writing_model": settings["writing_model"],
            "repair_model": settings["repair_model"],
            "reasoning_effort": settings["reasoning_effort"] if needs_reasoning else "none",
        },
        "notes": [
            "Use deterministic parsers for counts and schema validation.",
            "Use reasoning only for ambiguous synthesis or open-text survey clustering.",
        ],
    }
