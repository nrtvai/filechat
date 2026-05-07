from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .agent_runs import (
    add_quality_warning,
    answered_question_value,
    attach_run_messages,
    complete_run,
    create_agent_run,
    create_run_question,
    fail_run,
    get_agent_run,
    get_current_question,
    list_run_questions,
    list_workspace_items,
    mark_run_needs_revision,
    mark_run_needs_setup,
    record_agent_action,
    record_artifact_version,
    record_repair_attempt,
    record_run_event,
    record_tool_call,
    set_action,
    start_run,
    update_run_kind,
    update_run_contract,
    update_run_preflight,
    upsert_workspace_item,
    mark_run_awaiting_user_input,
)
from .agent_runtime import (
    build_summary_panel_artifact,
    ensure_provider_ready,
    file_manifest,
    normalize_task_contract,
    reconcile_task_contract,
    review_contract_result,
    update_contract_user_direction,
)
from .analysis_engine import build_insight_brief
from .artifact_engine import (
    ArtifactEngineFailure,
    ArtifactEngineInput,
    build_retrieval_artifacts,
    is_artifact_discovery_question,
    profile_sources,
    selected_artifact_options,
)
from .artifact_discovery import (
    build_artifact_options_artifact,
    build_timeline_artifacts,
    discovery_answer,
    is_artifact_discovery_request,
    is_timeline_request,
    timeline_answer,
    timeline_contract,
)
from .artifact_advisor import (
    build_artifact_advice,
    build_recommendation_cards_artifact,
    build_recommended_artifact,
    recommendation_options,
    requested_chart_type,
    selected_recommendation,
)
from .artifacts import ValidatedArtifact, validate_artifacts_with_report
from .database import connect
from .models import CitationOut
from .openrouter import ChatResult, OpenRouterClient, OpenRouterMissingKey, OpenRouterResponseError
from .orchestration import build_preflight, is_broad_create_request
from .prompt_context import context_profile, refresh_session_context
from .providers import provider_registry
from .review_checks import analysis_check, artifact_check, plan_check, source_check, writing_check
from .settings_store import current_app_settings
from .survey import build_survey_artifacts, read_extracted_file_texts
from .usage import UsageInfo, record_usage_event
from .utils import cosine, excerpt, json_dumps, new_id, now

SUMMARY_REQUEST_PATTERNS = (
    "what is this about",
    "what's this about",
    "what is this",
    "what does this say",
    "what does this file say",
    "tell me what this says",
    "summarize",
    "summary",
)
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
WEB_SEARCH_PATTERNS = (
    "latest",
    "current",
    "today",
    "recent",
    "web search",
    "internet",
    "online",
)


@dataclass
class SourceAcquisitionResult:
    sources: list[dict[str, Any]]
    file_texts: list[dict[str, Any]]
    unavailable: list[str]
    source_warnings: list[str] = field(default_factory=list)
    vector_search_status: str = "not_attempted"
    vector_search_error: str = ""
    used_vector_search: bool = False


@dataclass
class ToolFailure:
    status: str
    user_message: str
    technical_detail: str


WORK_ACTION_KIND = {
    "plan": "plan_task",
    "search": "load_sources",
    "analysis": "build_evidence",
    "writing": "write",
    "review": "validate",
    "implement": "persist_response",
}


def _record_work_action(
    run_id: str,
    work: str,
    status: str,
    *,
    summary: str = "",
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if status == "skipped":
        return
    kind = WORK_ACTION_KIND.get(work, "reason")
    set_action(
        run_id,
        kind,  # type: ignore[arg-type]
        "failed" if status == "failed" else ("running" if status == "running" else "completed"),
        input_summary=summary if status == "running" else "",
        output_summary=summary if status != "running" else "",
        error_summary=error,
        output_json=detail or {},
    )


def _prior_action_summaries(run_id: str) -> list[dict[str, Any]]:
    run = get_agent_run(run_id)
    if not run:
        return []
    return [
        {
            "kind": action.kind,
            "status": action.status,
            "summary": action.output_summary or action.input_summary,
        }
        for action in run.actions[-12:]
    ]


def _follow_up_context_for_run(run_id: str) -> dict[str, Any]:
    for item in list_workspace_items(run_id):
        if item.path == "/follow-up/context.json" and isinstance(item.content, dict):
            return item.content
    return {}


def _selected_follow_up_file_ids(context: dict[str, Any]) -> list[str]:
    file_ids = context.get("attached_file_ids") if isinstance(context, dict) else []
    if not isinstance(file_ids, list):
        return []
    return [str(file_id) for file_id in file_ids if str(file_id).strip()]


def _compact_follow_up_context(context: dict[str, Any]) -> dict[str, Any]:
    if not context:
        return {}
    parent_artifact = context.get("parent_artifact") if isinstance(context.get("parent_artifact"), dict) else {}
    spec = parent_artifact.get("spec") if isinstance(parent_artifact, dict) and isinstance(parent_artifact.get("spec"), dict) else {}
    return {
        "parent_run_id": context.get("parent_run_id"),
        "trigger_question_id": context.get("trigger_question_id"),
        "question": context.get("question"),
        "answer": context.get("answer") if isinstance(context.get("answer"), dict) else {},
        "attached_file_ids": _selected_follow_up_file_ids(context),
        "source_filter_mode": context.get("source_filter_mode"),
        "selected_artifact_options": context.get("selected_artifact_options")
        if isinstance(context.get("selected_artifact_options"), list)
        else [],
        "parent_chart_spec": spec,
        "parent_insight": spec.get("insight_narrative") if isinstance(spec, dict) else {},
    }


def _selected_decision_artifacts(context: dict[str, Any]) -> list[dict[str, Any]]:
    selected_options = context.get("selected_artifact_options") if isinstance(context, dict) else []
    if not isinstance(selected_options, list):
        return []
    artifacts: list[dict[str, Any]] = []
    for option in selected_options:
        if not isinstance(option, dict):
            continue
        payload = option.get("produce_payload")
        artifact = payload.get("artifact") if isinstance(payload, dict) else None
        if isinstance(artifact, dict):
            artifacts.append(dict(artifact))
    return artifacts


def _discovery_only_contract(task_contract: dict[str, Any]) -> dict[str, Any]:
    updated = dict(task_contract)
    updated["required_outputs"] = ["decision_cards"]
    updated["primary_outputs"] = ["decision_cards"]
    updated["supporting_outputs"] = []
    adjustments = list(updated.get("contract_adjustments") or [])
    adjustment = "Treated chart/document discovery as selectable decision cards, not as production artifact generation."
    if adjustment not in adjustments:
        adjustments.append(adjustment)
    updated["contract_adjustments"] = adjustments
    executable_contract = dict(updated.get("executable_contract") or {})
    if executable_contract:
        executable_contract["required_outputs"] = ["decision_cards"]
        executable_contract["primary_outputs"] = ["decision_cards"]
        executable_contract["supporting_outputs"] = []
        executable_contract["guaranteed_outputs"] = ["decision_cards"]
        executable_contract["optional_outputs"] = []
        executable_contract["repairable_outputs"] = []
        executable_contract["contract_adjustments"] = adjustments
        capability_snapshot = dict(executable_contract.get("capability_snapshot") or {})
        if capability_snapshot:
            capability_snapshot["requested_outputs"] = ["decision_cards"]
            capability_snapshot["guaranteed_outputs"] = ["decision_cards"]
            capability_snapshot["optional_outputs"] = []
            capability_snapshot["repairable_outputs"] = []
            executable_contract["capability_snapshot"] = capability_snapshot
        updated["executable_contract"] = executable_contract
    capability_snapshot = dict(updated.get("capability_snapshot") or {})
    if capability_snapshot:
        capability_snapshot["requested_outputs"] = ["decision_cards"]
        capability_snapshot["guaranteed_outputs"] = ["decision_cards"]
        capability_snapshot["optional_outputs"] = []
        capability_snapshot["repairable_outputs"] = []
        updated["capability_snapshot"] = capability_snapshot
    return updated


def _apply_follow_up_source_filter(
    *,
    run_id: str,
    follow_up_context: dict[str, Any],
    retrieved: list[dict[str, Any]],
    file_texts: list[dict[str, Any]],
    unavailable: list[str],
    source_packet: SourceAcquisitionResult | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    selected_file_ids = set(_selected_follow_up_file_ids(follow_up_context))
    if not selected_file_ids:
        if follow_up_context and follow_up_context.get("source_filter_mode") == "all_session_sources":
            record_run_event(
                run_id,
                type="follow_up_context_applied",
                summary="Using all session sources for this follow-up run",
                detail={"selected_file_ids": [], "source_count": len(retrieved), "full_text_file_count": len(file_texts)},
            )
            return retrieved, file_texts, unavailable
        if follow_up_context:
            record_run_event(
                run_id,
                type="follow_up_context_applied",
                summary="No reference files were selected for this follow-up run",
                detail={"selected_file_ids": [], "source_count": 0, "full_text_file_count": 0},
            )
            return [], [], []
        return retrieved, file_texts, unavailable
    filtered_sources = [source for source in retrieved if str(source.get("file_id")) in selected_file_ids]
    filtered_texts = [item for item in file_texts if str(item.get("file_id")) in selected_file_ids]
    filtered_unavailable = [file_id for file_id in unavailable if str(file_id) in selected_file_ids]
    if source_packet:
        source_packet.sources = filtered_sources
        source_packet.file_texts = filtered_texts
        source_packet.unavailable = filtered_unavailable
    record_run_event(
        run_id,
        type="follow_up_context_applied",
        summary="Limited follow-up run to selected reference files",
        detail={
            "selected_file_ids": sorted(selected_file_ids),
            "source_count": len(filtered_sources),
            "full_text_file_count": len(filtered_texts),
        },
    )
    return filtered_sources, filtered_texts, filtered_unavailable


def _controller_packet(
    *,
    run_id: str,
    session_id: str,
    question: str,
    history: list[dict[str, str]],
    task_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "packet_kind": "controller",
        "current_request": question,
        "file_manifest": file_manifest(session_id),
        "task_contract": task_contract or {},
        "prior_action_summaries": _prior_action_summaries(run_id),
        "conversation_tail": history[-4:],
        "user_preferences": context_profile(),
        "follow_up_context": _compact_follow_up_context(_follow_up_context_for_run(run_id)),
    }


def _writer_packet(
    *,
    question: str,
    task_contract: dict[str, Any],
    evidence_packet: dict[str, Any] | None = None,
    follow_up_context: dict[str, Any] | None = None,
    selected_source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "packet_kind": "writer",
        "current_request": question,
        "evidence_packet": evidence_packet or {},
        "follow_up_context": _compact_follow_up_context(follow_up_context or {}),
        "selected_source_refs": selected_source_refs or [],
        "output_contract": {
            "intent": task_contract.get("intent"),
            "required_outputs": task_contract.get("required_outputs", []),
            "supporting_outputs": task_contract.get("supporting_outputs", []),
            "success_criteria": task_contract.get("success_criteria", []),
            "artifact_recommendations": task_contract.get("artifact_recommendations", []),
            "selected_artifact_recommendation": task_contract.get("selected_artifact_recommendation", {}),
        },
        "style_constraints": context_profile(),
    }


def _persist_checker_report(run_id: str, path: str, report: dict[str, Any]) -> dict[str, Any]:
    upsert_workspace_item(run_id, path=path, kind="review", content=report)
    record_run_event(
        run_id,
        type="checker_report",
        summary=f"{report.get('phase', 'review')} {'passed' if report.get('passed') else 'flagged issues'}",
        detail={"path": path, "severity": report.get("severity", "none")},
    )
    return report


def _source_refs(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": source.get("source_id"),
            "file_name": source.get("file_name"),
            "location": source.get("location"),
            "chunk_id": source.get("chunk_id"),
        }
        for source in sources[:12]
    ]


def _artifact_specs(artifacts: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": getattr(artifact, "kind", ""),
            "title": getattr(artifact, "title", ""),
            "spec": getattr(artifact, "spec", {}),
        }
        for artifact in artifacts[:8]
    ]


def _first_insight_narrative(artifacts: list[Any]) -> dict[str, Any]:
    for artifact in artifacts:
        spec = getattr(artifact, "spec", {})
        if isinstance(spec, dict) and isinstance(spec.get("insight_narrative"), dict):
            return spec["insight_narrative"]
    return {}


def _create_follow_up_questions_from_artifacts(
    run_id: str,
    assistant_message_id: str,
    artifact_ids: list[str],
    artifacts: list[Any],
) -> None:
    for artifact_id, artifact in zip(artifact_ids, artifacts):
        spec = getattr(artifact, "spec", {})
        if getattr(artifact, "kind", "") == "decision_cards" and isinstance(spec, dict):
            options = _decision_card_options(spec)
            if not options:
                continue
            create_run_question(
                run_id,
                action_kind="write",
                kind="artifact_choice",
                question="Select one or more artifacts to produce.",
                options=[
                    {
                        "id": option["id"],
                        "label": option["label"],
                        "description": option.get("description", ""),
                    }
                    for option in options
                ],
                default_option="",
                blocking=False,
                phase="artifact_choice",
                card={
                    "title": getattr(artifact, "title", "") or "Available Charts And Docs",
                    "prompt": "Select one or more artifacts to produce.",
                    "group": "business",
                    "options": options,
                    "allow_free_text": False,
                    "allow_file_reference": False,
                    "allow_multi_select": True,
                    "submit_label": "Produce selected",
                },
                parent_message_id=assistant_message_id,
                parent_artifact_id=artifact_id,
            )
            continue
        if getattr(artifact, "kind", "") != "chart" or not isinstance(spec, dict):
            continue
        narrative = spec.get("insight_narrative")
        if not isinstance(narrative, dict):
            continue
        questions = narrative.get("follow_up_questions")
        if not isinstance(questions, list):
            continue
        for item in questions[:4]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            options = item.get("options") if isinstance(item.get("options"), list) else []
            create_run_question(
                run_id,
                action_kind="write",
                kind="choice",
                question=question,
                options=[
                    {
                        "id": str(option.get("id") or option.get("label") or "answer"),
                        "label": str(option.get("label") or option.get("id") or "Answer"),
                        "description": str(option.get("description") or ""),
                    }
                    for option in options
                    if isinstance(option, dict)
                ],
                default_option=str(item.get("default_option") or ""),
                blocking=False,
                phase="follow_up",
                card={
                    "title": "Question to answer next",
                    "prompt": question,
                    "group": str(item.get("group") or "business"),
                    "options": options,
                    "allow_free_text": True,
                    "allow_file_reference": bool(item.get("requires_reference")),
                },
                parent_message_id=assistant_message_id,
                parent_artifact_id=artifact_id,
            )


def _decision_card_options(spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw_options = spec.get("decision_options")
    if not isinstance(raw_options, list):
        return []
    options: list[dict[str, Any]] = []
    for item in raw_options:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not option_id or not label:
            continue
        option: dict[str, Any] = {
            "id": option_id,
            "label": label,
            "description": str(item.get("description") or ""),
            "artifact_kind": str(item.get("artifact_kind") or "summary_panel"),
            "produce_payload": item.get("produce_payload") if isinstance(item.get("produce_payload"), dict) else {},
        }
        chart_type = str(item.get("chart_type") or "").strip()
        if chart_type:
            option["chart_type"] = chart_type
        options.append(option)
    return options


async def _maybe_red_team_review(
    *,
    run_id: str,
    model: str,
    phase_goal: str,
    task_contract: dict[str, Any],
    evidence_packet: dict[str, Any],
    sources: list[dict[str, Any]],
    artifacts: list[Any],
    answer_content: str,
    checker_reports: list[dict[str, Any]],
    deterministic_reports: list[dict[str, Any]],
) -> dict[str, Any] | None:
    risk = any(report.get("severity") in {"medium", "high"} for report in deterministic_reports)
    output_kinds = {str(getattr(artifact, "kind", "")) for artifact in artifacts}
    should_review = risk or bool(output_kinds & {"chart", "file_draft", "summary_panel", "table"})
    if not should_review:
        return None
    try:
        report = await provider_registry().active().review_phase(
            model=model,
            phase="red_team",
            phase_goal=phase_goal,
            task_contract=task_contract,
            evidence_packet=evidence_packet,
            source_refs=_source_refs(sources),
            artifact_specs=_artifact_specs(artifacts),
            answer_draft=answer_content,
            prior_checker_reports=checker_reports,
        )
    except Exception as exc:
        warning = f"Red-team review unavailable; deterministic checks were used. {exc}"
        add_quality_warning(run_id, warning)
        report = {
            "phase": "red_team",
            "passed": True,
            "severity": "low",
            "findings": [warning],
            "required_fixes": [],
            "suggested_followups": [],
            "confidence": "low",
        }
    return _persist_checker_report(run_id, "/review/red-team.json", report)


async def _proofread_reviewed_output(
    *,
    run_id: str,
    model: str,
    answer_content: str,
    insight_narrative: dict[str, Any],
    red_team_report: dict[str, Any] | None,
    evidence_packet: dict[str, Any],
) -> dict[str, Any]:
    try:
        reviewed_output = await provider_registry().active().proofread_output(
            model=model,
            answer_draft=answer_content,
            insight_narrative=insight_narrative,
            red_team_findings=list(red_team_report.get("findings", [])) if isinstance(red_team_report, dict) else [],
            evidence_packet=evidence_packet,
        )
    except Exception as exc:
        warning = f"Proofread unavailable; raw reviewed draft was kept. {exc}"
        add_quality_warning(run_id, warning)
        reviewed_output = {"answer": answer_content, "insight_narrative": insight_narrative, "warning": warning}
    return _persist_checker_report(
        run_id,
        "/review/proofread.json",
        {
            "phase": "proofread",
            "passed": True,
            "severity": "none",
            "findings": [],
            "required_fixes": [],
            "suggested_followups": [],
            "reviewed_output": reviewed_output,
            "confidence": "medium",
        },
    )


def is_summary_request(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower()).strip(" ?!.")
    return any(pattern in normalized for pattern in SUMMARY_REQUEST_PATTERNS)


def classify_request(question: str) -> str:
    normalized = question.lower()
    return "create" if any(pattern in normalized for pattern in CREATE_REQUEST_PATTERNS) else "ask"


def requested_outputs(question: str) -> list[str]:
    normalized = question.lower()
    outputs = []
    if any(word in normalized for word in ("chart", "graph", "plot", "survey result")):
        outputs.append("chart")
    if any(word in normalized for word in ("draft", "new file", "write a file", "document", "report")) or any(
        word in question for word in ("초안", "보고서", "문서", "자료", "작성", "제작")
    ):
        outputs.append("file_draft")
    if any(word in normalized for word in ("table", "comparison")) or "표" in question:
        outputs.append("table")
    return outputs or ["answer"]


def requires_web_search(question: str) -> bool:
    normalized = question.lower()
    return any(pattern in normalized for pattern in WEB_SEARCH_PATTERNS)


def recent_history(session_id: str, limit: int = 8) -> list[dict[str, str]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def contextual_retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    if not history:
        return question
    recent = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    return f"{recent}\nuser: {question}"


def source_from_row(row, *, score: float) -> dict:
    return {
        "chunk_id": row["chunk_id"],
        "file_id": row["file_id"],
        "file_name": row["file_name"],
        "ordinal": row["ordinal"],
        "content": row["content"],
        "location": row["location"],
        "score": score,
        "excerpt": excerpt(row["content"]),
    }


def _provider_tool_failure(exc: Exception) -> ToolFailure:
    raw = str(exc) or exc.__class__.__name__
    if isinstance(exc, OpenRouterMissingKey) or "OpenRouter API key is not configured" in raw:
        return ToolFailure(
            status="unavailable_missing_key",
            user_message="OpenRouter API key is missing; FileChat used local file analysis where possible.",
            technical_detail=raw,
        )
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
        return ToolFailure(
            status="unavailable_auth",
            user_message="OpenRouter key needs attention; FileChat used local file analysis instead.",
            technical_detail=raw,
        )
    if "401 Unauthorized" in raw and "openrouter.ai" in raw:
        return ToolFailure(
            status="unavailable_auth",
            user_message="OpenRouter key needs attention; FileChat used local file analysis instead.",
            technical_detail=raw,
        )
    if isinstance(exc, OpenRouterResponseError):
        return ToolFailure(
            status="unavailable_provider",
            user_message="OpenRouter did not return usable model output; FileChat used local file analysis where possible.",
            technical_detail=raw,
        )
    return ToolFailure(
        status="unavailable_provider",
        user_message="Model provider access was unavailable; FileChat used local file analysis where possible.",
        technical_detail=raw,
    )


def _is_local_artifact_request(kind: str, outputs: list[str]) -> bool:
    return kind == "create" and any(output in outputs for output in ("chart", "table", "file_draft", "summary_panel", "decision_cards"))


def _is_ai_artifact_request(kind: str, outputs: list[str], question: str, follow_up_context: dict[str, Any]) -> bool:
    if selected_artifact_options(follow_up_context):
        return True
    if is_artifact_discovery_question(question):
        return True
    return kind == "create" and any(output in outputs for output in ("chart", "table", "file_draft", "summary_panel", "decision_cards"))


def _ai_discovery_contract(task_contract: dict[str, Any]) -> dict[str, Any]:
    updated = _discovery_only_contract(task_contract)
    executable = dict(updated.get("executable_contract") or {})
    if executable:
        executable["required_outputs"] = ["decision_cards"]
        executable["primary_outputs"] = ["decision_cards"]
        updated["executable_contract"] = executable
    return updated


def _selected_artifact_contract(task_contract: dict[str, Any], selected_options: list[dict[str, Any]]) -> dict[str, Any]:
    selected_kinds = [str(option.get("artifact_kind")) for option in selected_options if str(option.get("artifact_kind") or "") in {"chart", "table", "summary_panel", "file_draft", "comparison", "mermaid"}]
    updated = dict(task_contract)
    updated["required_outputs"] = selected_kinds or list(updated.get("required_outputs") or ["answer"])
    updated["primary_outputs"] = list(updated["required_outputs"])
    updated["selected_artifact_options"] = [
        {key: value for key, value in option.items() if key != "produce_payload"}
        for option in selected_options
    ]
    executable = dict(updated.get("executable_contract") or {})
    if executable:
        executable["required_outputs"] = list(updated["required_outputs"])
        executable["primary_outputs"] = list(updated["required_outputs"])
        updated["executable_contract"] = executable
    return updated


def _artifact_provider_degraded_answer(provider: dict[str, Any], source_profile: dict[str, Any]) -> str:
    status = str(provider.get("status") or "unavailable")
    message = str(provider.get("message") or "The model provider is not available.")
    summary = str(source_profile.get("summary") or "No source profile could be prepared.")
    table_lines: list[str] = []
    tables = source_profile.get("tables") if isinstance(source_profile.get("tables"), list) else []
    for table in tables[:2]:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns") if isinstance(table.get("columns"), list) else []
        names = [str(column.get("name")) for column in columns[:8] if isinstance(column, dict) and column.get("name")]
        table_lines.append(
            f"- {table.get('file_name')}: {table.get('row_count')} row(s), columns: {', '.join(names)}"
        )
    details = "\n".join(table_lines) if table_lines else f"- {summary}"
    return (
        f"Artifact generation needs a verified model provider. Provider status: {status}. {message}\n\n"
        f"Limited source-profile facts I could verify locally:\n{details}"
    )


def _tool_failure_from_warning(warning: str) -> ToolFailure:
    if "OpenRouter authentication failed" in warning or "401 Unauthorized" in warning:
        return ToolFailure(
            status="unavailable_auth",
            user_message="OpenRouter key needs attention; FileChat used local file analysis instead.",
            technical_detail=warning,
        )
    if "OpenRouter API key is missing" in warning or "OpenRouter API key is not configured" in warning:
        return ToolFailure(
            status="unavailable_missing_key",
            user_message="OpenRouter API key is missing; FileChat used local file analysis where possible.",
            technical_detail=warning,
        )
    return ToolFailure(
        status="unavailable_provider",
        user_message="OpenRouter did not return usable model output; FileChat used local file analysis where possible.",
        technical_detail=warning,
    )


def _artifact_validation_failure_message(warnings: list[str]) -> str:
    detail = " ".join(warnings)
    if "timeline chart" in detail:
        return "The model proposed a timeline chart, but FileChat supports timelines only as JSON-render roadmap artifacts. Retry as a roadmap/timeline artifact."
    return "The model returned an artifact shape that FileChat could not safely render. Retry the artifact or choose one of the structured options."


def _has_ready_embeddings(session_id: str, model: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM chunks c
            JOIN embeddings e ON e.chunk_id = c.id
            JOIN files f ON f.id = c.file_id
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ? AND f.status = 'ready' AND e.model = ?
            LIMIT 1
            """,
            (session_id, model),
        ).fetchone()
    return row is not None


def load_ready_sources(session_id: str) -> SourceAcquisitionResult:
    settings = current_app_settings()
    with connect() as conn:
        ready = conn.execute(
            """
            SELECT f.id, f.error FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ? AND f.status = 'ready'
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
        unavailable = conn.execute(
            """
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ? AND f.status != 'ready'
            """,
            (session_id,),
        ).fetchall()
        if not ready:
            return SourceAcquisitionResult(sources=[], file_texts=[], unavailable=[r["id"] for r in unavailable])
        file_ids = [r["id"] for r in ready]
        source_warnings = [str(r["error"]) for r in ready if r["error"]]
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(
            f"""
            SELECT c.id chunk_id, c.file_id, c.ordinal, c.content, c.location,
                   f.name file_name
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            JOIN session_files sf ON sf.file_id = f.id AND sf.session_id = ?
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE c.file_id IN ({placeholders})
            ORDER BY sf.attached_at, f.id, c.ordinal
            LIMIT ?
            """,
            (session_id, *file_ids, max(settings["retrieval_depth"], len(file_ids))),
        ).fetchall()
    return SourceAcquisitionResult(
        sources=[source_from_row(row, score=1.0) for row in rows],
        file_texts=read_extracted_file_texts(session_id),
        unavailable=[r["id"] for r in unavailable],
        source_warnings=source_warnings,
    )


async def semantic_retrieve(
    session_id: str,
    question: str,
    message_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> tuple[list[dict], list[str]]:
    settings = current_app_settings()
    model = settings["embedding_model"]
    retrieval_query = contextual_retrieval_query(question, history or [])
    with connect() as conn:
        ready = conn.execute(
            """
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ? AND f.status = 'ready'
            """,
            (session_id,),
        ).fetchall()
        unavailable = conn.execute(
            """
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ? AND f.status != 'ready'
            """,
            (session_id,),
        ).fetchall()
        if not ready:
            return [], [r["id"] for r in unavailable]
        file_ids = [r["id"] for r in ready]
        placeholders = ",".join("?" for _ in file_ids)
        if is_summary_request(question):
            rows = conn.execute(
                f"""
                SELECT c.id chunk_id, c.file_id, c.ordinal, c.content, c.location,
                       f.name file_name
                FROM chunks c
                JOIN files f ON f.id = c.file_id
                JOIN session_files sf ON sf.file_id = f.id AND sf.session_id = ?
                JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
                WHERE c.file_id IN ({placeholders})
                ORDER BY sf.attached_at, f.id, c.ordinal
                LIMIT ?
                """,
                (session_id, *file_ids, settings["retrieval_depth"]),
            ).fetchall()
            return [source_from_row(row, score=1.0) for row in rows], [r["id"] for r in unavailable]

        rows = conn.execute(
            f"""
            SELECT c.id chunk_id, c.file_id, c.ordinal, c.content, c.location,
                   e.vector, f.name file_name
            FROM chunks c
            JOIN embeddings e ON e.chunk_id = c.id
            JOIN files f ON f.id = c.file_id
            WHERE c.file_id IN ({placeholders}) AND e.model = ?
            """,
            (*file_ids, model),
        ).fetchall()
        if not rows:
            fallback_rows = conn.execute(
                f"""
                SELECT c.id chunk_id, c.file_id, c.ordinal, c.content, c.location,
                       f.name file_name
                FROM chunks c
                JOIN files f ON f.id = c.file_id
                JOIN session_files sf ON sf.file_id = f.id AND sf.session_id = ?
                JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
                WHERE c.file_id IN ({placeholders})
                ORDER BY sf.attached_at, f.id, c.ordinal
                LIMIT ?
                """,
                (session_id, *file_ids, settings["retrieval_depth"]),
            ).fetchall()
            return [source_from_row(row, score=1.0) for row in fallback_rows], [r["id"] for r in unavailable]

    embedding = await provider_registry().active().embedding_result([retrieval_query], model)
    query_vector = embedding.vectors[0]
    if message_id:
        record_usage_event(
            session_id=session_id,
            message_id=message_id,
            kind="query_embedding",
            model=embedding.model,
            usage=embedding.usage,
        )
    scored = []
    for row in rows:
        vector = json.loads(row["vector"])
        score = cosine(query_vector, vector)
        scored.append(source_from_row(row, score=score))
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: settings["retrieval_depth"]], [r["id"] for r in unavailable]


async def retrieve(
    session_id: str,
    question: str,
    message_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> tuple[list[dict], list[str]]:
    return await semantic_retrieve(session_id, question, message_id, history)


def grounded_refusal(session_id: str, unavailable: list[str]) -> str:
    base = "I could not find that answer in the attached sources."
    if unavailable:
        base += f" {len(unavailable)} attached file(s) were still processing or unavailable for this answer."
    return base


def insert_message(session_id: str, role: str, content: str, unavailable: list[str] | None = None) -> str:
    message_id = new_id("msg")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (id, session_id, role, content, unavailable_file_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, json_dumps(unavailable or []), now()),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now(), session_id))
    return message_id


def insert_citations(message_id: str, sources: list[dict], cited_source_ids: list[int]) -> list[CitationOut]:
    by_source_id = {source["source_id"]: source for source in sources}
    citation_rows = []
    with connect() as conn:
        for ordinal, source_id in enumerate(cited_source_ids, start=1):
            source = by_source_id.get(source_id)
            if not source:
                continue
            citation_id = new_id("cit")
            conn.execute(
                """
                INSERT INTO citations
                (id, message_id, file_id, chunk_id, source_label, location, excerpt, score, ordinal, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    citation_id,
                    message_id,
                    source["file_id"],
                    source["chunk_id"],
                    source["file_name"],
                    source["location"],
                    source["excerpt"],
                    source["score"],
                    ordinal,
                    now(),
                ),
            )
            citation_rows.append(
                CitationOut(
                    id=citation_id,
                    message_id=message_id,
                    file_id=source["file_id"],
                    chunk_id=source["chunk_id"],
                    source_label=source["file_name"],
                    location=source["location"],
                    excerpt=source["excerpt"],
                    score=source["score"],
                    ordinal=ordinal,
                )
            )
    return citation_rows


def insert_artifacts(
    session_id: str,
    message_id: str,
    artifacts: list[ValidatedArtifact],
) -> list[str]:
    if not artifacts:
        return []
    artifact_ids: list[str] = []
    with connect() as conn:
        for artifact in artifacts:
            artifact_id = new_id("art")
            conn.execute(
                """
                INSERT INTO artifacts
                (id, session_id, message_id, kind, title, caption, display_mode, source_chunk_ids, spec_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    session_id,
                    message_id,
                    artifact.kind,
                    artifact.title,
                    artifact.caption,
                    artifact.display_mode,
                    json_dumps(artifact.source_chunk_ids),
                    json_dumps(artifact.spec),
                    now(),
                ),
            )
            artifact_ids.append(artifact_id)
    return artifact_ids


def _answer_selected_option(answer: dict[str, object] | None) -> str:
    if not isinstance(answer, dict):
        return ""
    return str(answer.get("selected_option") or "").strip()


def _answer_free_text(answer: dict[str, object] | None) -> str:
    if not isinstance(answer, dict):
        return ""
    return str(answer.get("free_text") or "").strip()


def _broad_planning_options() -> list[dict[str, str]]:
    return [
        {
            "id": "automatic",
            "label": "Handle automatically",
            "description": "Infer the best grounded deliverable from the attached files.",
        },
        {
            "id": "interview",
            "label": "Interview me",
            "description": "Ask a few focused questions before producing the result.",
        },
    ]


def _deliverable_options(outputs: list[str]) -> list[dict[str, str]]:
    options = [
        {
            "id": "brief_with_chart",
            "label": "Brief + chart",
            "description": "Create a concise analysis brief with the strongest chart or table.",
        },
        {
            "id": "insight_report",
            "label": "Insight report",
            "description": "Prioritize written insights, implications, and recommendations.",
        },
        {
            "id": "data_first",
            "label": "Data-first",
            "description": "Prioritize tables, counts, and source-backed evidence over prose.",
        },
    ]
    return options if "file_draft" in outputs else options[:2]


def _planning_answer_suffix(run_id: str) -> str:
    clarification = answered_question_value(run_id, "clarification")
    if not clarification:
        return ""
    selected = _answer_selected_option(clarification)
    free_text = _answer_free_text(clarification)
    parts = []
    if selected:
        parts.append(f"preferred deliverable: {selected}")
    if free_text:
        parts.append(f"user clarification: {free_text}")
    return "\n\nPlanning clarification: " + "; ".join(parts) if parts else ""


def _should_offer_interview(run_id: str, question: str, outputs: list[str]) -> bool:
    if not is_broad_create_request(question, outputs):
        return False
    if answered_question_value(run_id, "interview_offer"):
        return False
    return get_current_question(run_id) is None


def _planning_question_options(task_contract: dict[str, Any], outputs: list[str]) -> list[dict[str, Any]]:
    options = task_contract.get("question_options")
    if isinstance(options, list) and options:
        return options
    return _deliverable_options(outputs)


def _answer_from_artifacts(task_contract: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    language = str(task_contract.get("language") or "")
    deliverable = str(task_contract.get("deliverable") or "")
    draft = next((item for item in artifacts if item.get("kind") == "file_draft"), None)
    chart = next((item for item in artifacts if item.get("kind") == "chart"), None)
    if language == "ko" or deliverable == "insight_report":
        lines = ["분석 자료를 만들었습니다."]
        if draft:
            lines.append("- Markdown 초안에는 데이터 개요, 핵심 인사이트, 근거 데이터, 차트 요약, 후속 액션을 포함했습니다.")
        if chart:
            lines.append(f"- 차트는 `{chart.get('title', 'Survey chart')}` 기준으로 집계했습니다.")
        lines.append("- 모든 산출물은 첨부 파일에서 확인 가능한 값과 출처 청크에 연결했습니다.")
        return "\n".join(lines)
    lines = ["I created grounded analysis materials from the attached source data."]
    if draft:
        lines.append("- The draft includes overview, evidence, findings, and next actions.")
    if chart:
        lines.append(f"- The chart uses `{chart.get('title', 'Survey chart')}`.")
    lines.append("- Artifacts are linked to source chunks from the attached files.")
    return "\n".join(lines)


def _replace_draft_artifact(artifacts: list[dict[str, Any]], draft: dict[str, Any]) -> list[dict[str, Any]]:
    without_draft = [artifact for artifact in artifacts if artifact.get("kind") != "file_draft"]
    chart = [artifact for artifact in without_draft if artifact.get("kind") == "chart"]
    supporting = [artifact for artifact in without_draft if artifact.get("kind") != "chart"]
    return [*chart, draft, *supporting]


async def _chat_with_optional_context(chat_kwargs: dict[str, Any]) -> ChatResult:
    provider = provider_registry().active()
    try:
        return await provider.chat(**chat_kwargs)
    except TypeError as exc:
        if "prompt_context" not in str(exc):
            raise
        legacy_kwargs = dict(chat_kwargs)
        legacy_kwargs.pop("prompt_context", None)
        return await provider.chat(**legacy_kwargs)


async def answer(session_id: str, question: str) -> str:
    run = create_agent_run(session_id, question)
    message_id = await execute_agent_run(run.id)
    if message_id:
        return message_id
    latest = get_agent_run(run.id)
    if latest and latest.assistant_message_id:
        return latest.assistant_message_id
    current_question = get_current_question(run.id)
    if current_question:
        content = current_question.question
    elif latest and latest.status == "needs_revision":
        content = latest.error or "I stopped before saving a result because the semantic quality review did not pass. Please revise the request or retry with clearer direction."
    elif latest and latest.status == "needs_setup":
        content = latest.error or "OpenRouter setup needs attention before FileChat can run the model-led workflow."
    else:
        content = "FileChat needs a planning choice before it can continue."
    assistant_id = insert_message(session_id, "assistant", content, [])
    attach_run_messages(run.id, assistant_message_id=assistant_id)
    return assistant_id


async def execute_agent_run(run_id: str) -> str | None:
    run = get_agent_run(run_id)
    if not run:
        raise RuntimeError("Agent run not found.")
    session_id = run.session_id
    question = run.question
    current_work = "plan"
    start_run(run_id)
    record_run_event(run_id, type="run_started", summary="Agent run started", detail={"question": question})
    checker_reports: list[dict[str, Any]] = []
    follow_up_context = _follow_up_context_for_run(run_id)

    try:
        if not run.execution_plan:
            preflight = build_preflight(session_id, question)
            update_run_preflight(run_id, **preflight)
            run = get_agent_run(run_id) or run

        history = recent_history(session_id)
        if run.user_message_id:
            user_id = run.user_message_id
        else:
            user_id = insert_message(session_id, "user", question, [])
            attach_run_messages(run_id, user_message_id=user_id)

        set_action(run_id, "verify_provider", "running", input_summary="Checking OpenRouter provider status")
        provider = await ensure_provider_ready()
        update_run_contract(run_id, provider_status=provider)
        record_agent_action(
            run_id,
            {
                "action": "verify_provider",
                "status": provider.get("status"),
                "summary": provider.get("message") or "",
            },
        )
        if provider.get("status") != "verified":
            degraded_kind = classify_request(question)
            degraded_outputs = requested_outputs(question)
            if _is_ai_artifact_request(degraded_kind, degraded_outputs, question, follow_up_context):
                update_run_kind(run_id, "create")
                minimal_contract = {
                    "intent": "create",
                    "deliverable": degraded_outputs[0] if degraded_outputs else "artifact",
                    "language": "ko" if any("\uac00" <= char <= "\ud7a3" for char in question) else "en",
                    "required_outputs": degraded_outputs,
                    "primary_outputs": degraded_outputs,
                    "success_criteria": ["Artifact generation requires a verified model provider."],
                    "provider_degraded": True,
                    "executable_contract": {
                        "required_outputs": degraded_outputs,
                        "primary_outputs": degraded_outputs,
                        "supporting_outputs": [],
                    },
                }
                update_run_contract(run_id, task_contract=minimal_contract, provider_status=provider)
                set_action(
                    run_id,
                    "verify_provider",
                    "completed",
                    output_summary="Provider unavailable; degraded artifact response prepared",
                    output_json=provider,
                )
                set_action(
                    run_id,
                    "classify_request",
                    "completed",
                    output_summary="Classified as create",
                    output_json={"intent": "create", "requested_outputs": degraded_outputs, "provider_degraded": True},
                )
                source_packet = load_ready_sources(session_id)
                degraded_sources = [{**item, "source_id": source_id} for source_id, item in enumerate(source_packet.sources, start=1)]
                source_profile = profile_sources(source_packet.file_texts, degraded_sources)
                upsert_workspace_item(run_id, path="/analysis/source-profile.json", kind="analysis", content=source_profile)
                set_action(
                    run_id,
                    "load_sources",
                    "completed",
                    output_summary=f"Loaded {len(degraded_sources)} ready source chunk{'' if len(degraded_sources) == 1 else 's'}",
                    output_json={
                        "local_source_count": len(degraded_sources),
                        "full_text_file_count": len(source_packet.file_texts),
                        "unavailable_file_ids": source_packet.unavailable,
                        "vector_search_status": "skipped_provider_unavailable",
                    },
                )
                set_action(
                    run_id,
                    "persist_response",
                    "running",
                    input_summary="Saving provider-unavailable artifact response",
                )
                assistant_id = insert_message(
                    session_id,
                    "assistant",
                    _artifact_provider_degraded_answer(provider, source_profile),
                    source_packet.unavailable,
                )
                cited_ids = [degraded_sources[0]["source_id"]] if degraded_sources else []
                if cited_ids:
                    insert_citations(assistant_id, degraded_sources, cited_ids)
                attach_run_messages(run_id, assistant_message_id=assistant_id)
                set_action(
                    run_id,
                    "persist_response",
                    "completed",
                    output_summary="Saved degraded provider-unavailable response",
                    output_json={"artifact_count": 0, "provider_status": provider.get("status")},
                )
                complete_run(run_id, assistant_message_id=assistant_id)
                return assistant_id
            message = str(provider.get("message") or "OpenRouter key must be verified before model-backed runs can start.")
            set_action(
                run_id,
                "verify_provider",
                "failed",
                output_summary="OpenRouter provider needs setup",
                error_summary=message,
                output_json=provider,
            )
            assistant_id = insert_message(
                session_id,
                "assistant",
                f"OpenRouter setup needs attention before I can run the model-led workflow. {message}",
                [],
            )
            attach_run_messages(run_id, assistant_message_id=assistant_id)
            mark_run_needs_setup(run_id, message)
            return assistant_id
        set_action(run_id, "verify_provider", "completed", output_summary=provider.get("message") or "Provider verified", output_json=provider)

        current_work = "plan"
        kind = classify_request(question)
        outputs = requested_outputs(question)
        web_needed = requires_web_search(question)
        selected_options_for_contract = selected_artifact_options(follow_up_context)
        discovery_contract_requested = is_artifact_discovery_question(question) and not selected_options_for_contract
        set_action(
            run_id,
            "classify_request",
            "completed",
            output_summary=f"Classified as {kind}",
            output_json={"intent": kind, "requested_outputs": outputs, "web_search_required": web_needed},
        )
        settings = current_app_settings()
        update_run_kind(run_id, kind)
        model_assignments = run.model_assignments or build_preflight(session_id, question)["model_assignments"]
        _record_work_action(run_id, current_work, "running", summary="Planning the request")

        pending_question = get_current_question(run_id)
        if pending_question:
            _record_work_action(
                run_id,
                current_work,
                "running",
                summary="Waiting for your planning answer",
                detail={"question_id": pending_question.id, "question_kind": pending_question.kind},
            )
            mark_run_awaiting_user_input(run_id)
            return None

        answered_questions = [question.model_dump() for question in list_run_questions(run_id) if question.status == "answered"]
        prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history)
        if not run.task_contract and (selected_options_for_contract or discovery_contract_requested):
            base_contract = {
                "intent": "create",
                "deliverable": "selected source-grounded artifacts" if selected_options_for_contract else "artifact discovery decision cards",
                "language": "en",
                "required_outputs": outputs,
                "primary_outputs": outputs,
                "supporting_outputs": [],
                "success_criteria": ["Use source-grounded artifacts with citations."],
                "needs_user_question": False,
            }
            base_contract["executable_contract"] = dict(base_contract)
            task_contract = (
                _selected_artifact_contract(base_contract, selected_options_for_contract)
                if selected_options_for_contract
                else _ai_discovery_contract(base_contract)
            )
            outputs = list(task_contract.get("required_outputs") or outputs)
            kind = "create"
            update_run_kind(run_id, kind)
            update_run_contract(run_id, task_contract=task_contract, revision_required=False)
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
            upsert_workspace_item(run_id, path="/plan/task-contract.json", kind="planning", content=task_contract)
            record_agent_action(
                run_id,
                {
                    "action": "plan_task",
                    "model": "server_artifact_contract",
                    "outputs": task_contract.get("primary_outputs", task_contract.get("required_outputs", [])),
                    "needs_user_question": False,
                },
            )
            _record_work_action(
                run_id,
                current_work,
                "completed",
                summary="Used server-owned artifact contract",
                detail={"required_outputs": outputs},
            )
            run = get_agent_run(run_id) or run
        if not run.task_contract:
            raw_contract = await provider_registry().active().plan_task(
                model=model_assignments.get("orchestrator", {}).get("model") or settings["orchestrator_model"],
                question=question,
                file_manifest=file_manifest(session_id),
                prior_answers=answered_questions,
                prompt_context=prompt_context,
                reasoning_effort=model_assignments.get("orchestrator", {}).get("reasoning_effort") or settings["reasoning_effort"],
            )
            planner_contract = normalize_task_contract(raw_contract, question=question, fallback_outputs=outputs)
            task_contract = reconcile_task_contract(
                question=question,
                planner_contract=planner_contract,
                execution_plan=run.execution_plan or build_preflight(session_id, question)["execution_plan"],
            )
            update_run_contract(run_id, task_contract=task_contract, revision_required=False)
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
            upsert_workspace_item(run_id, path="/plan/task-contract.json", kind="planning", content=task_contract)
            record_agent_action(
                run_id,
                {
                    "action": "plan_task",
                    "model": model_assignments.get("orchestrator", {}).get("model") or settings["orchestrator_model"],
                    "outputs": task_contract.get("primary_outputs", task_contract.get("required_outputs", [])),
                    "needs_user_question": task_contract.get("needs_user_question", False),
                },
            )
            record_run_event(
                run_id,
                type="contract_reconciled",
                summary="Reconciled planner intent against available local capabilities",
                detail={
                    "planner_contract": task_contract.get("planner_contract", {}),
                    "executable_contract": task_contract.get("executable_contract", {}),
                    "contract_adjustments": task_contract.get("contract_adjustments", []),
                },
            )
            run = get_agent_run(run_id) or run

        task_contract = run.task_contract
        if task_contract:
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
        contract_outputs = list(task_contract.get("required_outputs") or outputs)
        interview_answer = answered_question_value(run_id, "interview_offer")
        interview_mode = _answer_selected_option(interview_answer)
        if task_contract.get("needs_user_question") and _should_offer_interview(run_id, question, contract_outputs):
            options = _broad_planning_options()
            created = create_run_question(
                run_id,
                action_kind="ask_user",
                kind="interview_offer",
                question="Do you want a short interview for a better result, or should FileChat handle it automatically?",
                options=options,
                default_option="automatic",
            )
            upsert_workspace_item(
                run_id,
                path="/plan/ambiguity.json",
                kind="planning",
                content={
                    "ambiguity": "broad_create_request",
                    "requested_outputs": contract_outputs,
                    "default_option": "automatic",
                    "question_id": created.id,
                },
            )
            _record_work_action(
                run_id,
                current_work,
                "running",
                summary="Waiting for interview or automatic planning choice",
                detail={"question_id": created.id, "question_kind": created.kind, "options": [item["id"] for item in options]},
            )
            mark_run_awaiting_user_input(run_id)
            return None

        if interview_mode == "automatic":
            updates = update_contract_user_direction(
                task_contract,
                {"selected_option": "automatic", "free_text": "", "mode": "automatic"},
            )
            update_run_contract(run_id, task_contract=updates)
            task_contract = updates
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
            upsert_workspace_item(run_id, path="/plan/task-contract.json", kind="planning", content=task_contract)
            upsert_workspace_item(
                run_id,
                path="/plan/inferred-plan.json",
                kind="planning",
                content={
                    "selected_mode": "automatic",
                    "output_type": task_contract.get("required_outputs", contract_outputs),
                    "source_files": file_manifest(session_id),
                    "tools": run.execution_plan.get("tools", []) if isinstance(run.execution_plan, dict) else [],
                    "fallback_path": "deterministic survey/table artifacts when source data permits",
                },
            )
        elif interview_mode == "interview" and task_contract.get("needs_user_question") and not answered_question_value(run_id, "clarification"):
            options = _planning_question_options(task_contract, contract_outputs)
            created = create_run_question(
                run_id,
                action_kind="ask_user",
                kind="clarification",
                question=str(task_contract.get("user_question") or "What should this deliverable optimize for?"),
                options=options,
                default_option=str(task_contract.get("default_option") or (options[0]["id"] if options else "")),
            )
            upsert_workspace_item(
                run_id,
                path="/plan/ambiguity.json",
                kind="planning",
                content={
                    "ambiguity": "interview_requested_user_direction",
                    "requested_outputs": contract_outputs,
                    "default_option": task_contract.get("default_option", ""),
                    "question_id": created.id,
                },
            )
            _record_work_action(
                run_id,
                current_work,
                "running",
                summary="Waiting for your planning clarification",
                detail={"question_id": created.id, "question_kind": created.kind, "options": [item["id"] for item in options]},
            )
            mark_run_awaiting_user_input(run_id)
            return None
        elif task_contract.get("needs_user_question") and not is_broad_create_request(question, contract_outputs) and not answered_question_value(run_id, "choice"):
            options = _planning_question_options(task_contract, contract_outputs)
            created = create_run_question(
                run_id,
                action_kind="ask_user",
                kind="choice",
                question=str(task_contract.get("user_question") or "What should this deliverable optimize for?"),
                options=options,
                default_option=str(task_contract.get("default_option") or (options[0]["id"] if options else "")),
            )
            upsert_workspace_item(
                run_id,
                path="/plan/ambiguity.json",
                kind="planning",
                content={
                    "ambiguity": "planner_requested_user_direction",
                    "requested_outputs": task_contract.get("required_outputs", outputs),
                    "default_option": task_contract.get("default_option", ""),
                    "question_id": created.id,
                },
            )
            _record_work_action(
                run_id,
                current_work,
                "running",
                summary="Waiting for your planning choice",
                detail={"question_id": created.id, "question_kind": created.kind, "options": [item["id"] for item in options]},
            )
            mark_run_awaiting_user_input(run_id)
            return None

        planning_suffix = _planning_answer_suffix(run_id)
        choice_answer = answered_question_value(run_id, "choice")
        clarification_answer = answered_question_value(run_id, "clarification")
        direction_answer = clarification_answer or choice_answer
        if direction_answer:
            selected = _answer_selected_option(direction_answer)
            free_text = _answer_free_text(direction_answer)
            user_direction = {"selected_option": selected, "free_text": free_text}
            if clarification_answer:
                user_direction["mode"] = "interview"
            updates = update_contract_user_direction(
                task_contract,
                user_direction,
            )
            update_run_contract(run_id, task_contract=updates)
            task_contract = updates
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
            if choice_answer:
                planning_suffix += "\n\nPlanning direction: " + "; ".join(part for part in [selected, free_text] if part)
            upsert_workspace_item(run_id, path="/plan/task-contract.json", kind="planning", content=task_contract)
        outputs = list(task_contract.get("required_outputs") or outputs)
        kind = str(task_contract.get("intent") or kind)
        update_run_kind(run_id, kind if kind in {"ask", "create"} else classify_request(question))
        effective_question = question + planning_suffix
        if planning_suffix:
            upsert_workspace_item(
                run_id,
                path="/plan/user-clarification.json",
                kind="planning",
                content={"clarification": planning_suffix.strip()},
            )
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary=f"Planned a {kind} request",
            detail={
                "intent": kind,
                "requested_outputs": outputs,
                "execution_plan": run.execution_plan,
                "model_assignments": model_assignments,
                "task_contract": task_contract,
                "web_search_required": web_needed,
                "web_search_enabled": settings["web_search_enabled"],
                "planning_mode": "model_contract",
            },
        )
        checker_reports.append(_persist_checker_report(run_id, "/review/plan-check.json", plan_check(task_contract)))

        current_work = "search"
        local_artifact_request = _is_local_artifact_request(kind, outputs)
        search_start = "Loading ready source files" if local_artifact_request else "Searching local source chunks"
        _record_work_action(run_id, current_work, "running", summary=search_start)
        source_packet: SourceAcquisitionResult | None = None
        vector_failure: ToolFailure | None = None
        retrieved: list[dict[str, Any]] = []
        unavailable: list[str] = []
        file_texts: list[dict[str, Any]] = []

        if local_artifact_request:
            source_packet = load_ready_sources(session_id)
            retrieved = source_packet.sources
            unavailable = source_packet.unavailable
            file_texts = source_packet.file_texts
            record_run_event(
                run_id,
                type="tool_completed",
                summary=f"Loaded {len(retrieved)} ready source chunk{'' if len(retrieved) == 1 else 's'}",
                detail={
                    "tool": "load_sources",
                    "source_count": len(retrieved),
                    "full_text_file_count": len(file_texts),
                    "unavailable_file_ids": unavailable,
                },
            )
            if retrieved and _has_ready_embeddings(session_id, settings["embedding_model"]):
                try:
                    semantic_sources, semantic_unavailable = await semantic_retrieve(session_id, effective_question, user_id, history)
                    if semantic_sources:
                        retrieved = semantic_sources
                        unavailable = semantic_unavailable
                        source_packet.used_vector_search = True
                        source_packet.vector_search_status = "available"
                        record_run_event(
                            run_id,
                            type="tool_completed",
                            summary="Vector search ranked local source chunks",
                            detail={"tool": "embedding_search", "source_count": len(semantic_sources)},
                        )
                        set_action(
                            run_id,
                            "rank_sources",
                            "completed",
                            output_summary="Vector search ranked local source chunks",
                            output_json={"tool": "embedding_search", "source_count": len(semantic_sources)},
                        )
                except Exception as exc:
                    vector_failure = _provider_tool_failure(exc)
                    source_packet.vector_search_status = vector_failure.status
                    source_packet.vector_search_error = vector_failure.technical_detail
                    record_run_event(
                        run_id,
                        type="tool_failed",
                        summary=vector_failure.user_message,
                        detail={
                            "tool": "embedding_search",
                            "status": vector_failure.status,
                            "technical_detail": vector_failure.technical_detail,
                        },
                    )
                    add_quality_warning(run_id, vector_failure.user_message)
            elif retrieved and source_packet.source_warnings:
                vector_failure = _tool_failure_from_warning(source_packet.source_warnings[0])
                source_packet.vector_search_status = vector_failure.status
                source_packet.vector_search_error = vector_failure.technical_detail
                record_run_event(
                    run_id,
                    type="tool_failed",
                    summary=vector_failure.user_message,
                    detail={
                        "tool": "embedding_search",
                        "status": vector_failure.status,
                        "technical_detail": vector_failure.technical_detail,
                    },
                )
                add_quality_warning(run_id, vector_failure.user_message)
            elif retrieved:
                source_packet.vector_search_status = "skipped_no_vectors"
                record_run_event(
                    run_id,
                    type="tool_skipped",
                    summary="Vector search skipped because no local vectors are available yet",
                    detail={"tool": "embedding_search", "reason": "no_ready_embeddings"},
                )
            else:
                record_run_event(
                    run_id,
                    type="tool_skipped",
                    summary="Vector search skipped because no ready local sources were available",
                    detail={"tool": "embedding_search", "unavailable_file_ids": unavailable},
                )
        else:
            try:
                retrieved, unavailable = await semantic_retrieve(session_id, effective_question, user_id, history)
                source_packet = SourceAcquisitionResult(
                    sources=retrieved,
                    file_texts=[],
                    unavailable=unavailable,
                    vector_search_status="available" if retrieved else "not_needed",
                    used_vector_search=bool(retrieved),
                )
                if retrieved:
                    record_run_event(
                        run_id,
                        type="tool_completed",
                        summary="Vector search retrieved source chunks",
                        detail={"tool": "embedding_search", "source_count": len(retrieved)},
                    )
                    set_action(
                        run_id,
                        "rank_sources",
                        "completed",
                        output_summary="Vector search retrieved source chunks",
                        output_json={"tool": "embedding_search", "source_count": len(retrieved)},
                    )
            except Exception as exc:
                vector_failure = _provider_tool_failure(exc)
                source_packet = load_ready_sources(session_id)
                retrieved = source_packet.sources
                unavailable = source_packet.unavailable
                file_texts = source_packet.file_texts
                source_packet.vector_search_status = vector_failure.status
                source_packet.vector_search_error = vector_failure.technical_detail
                record_run_event(
                    run_id,
                    type="tool_failed",
                    summary=vector_failure.user_message,
                    detail={
                        "tool": "embedding_search",
                        "status": vector_failure.status,
                        "technical_detail": vector_failure.technical_detail,
                    },
                )
                add_quality_warning(run_id, vector_failure.user_message)
                if retrieved:
                    record_run_event(
                        run_id,
                        type="fallback_used",
                        summary="Used ready local source chunks after vector search became unavailable",
                        detail={"fallback": "local_source_load", "source_count": len(retrieved)},
                    )

        retrieved, file_texts, unavailable = _apply_follow_up_source_filter(
            run_id=run_id,
            follow_up_context=follow_up_context,
            retrieved=retrieved,
            file_texts=file_texts,
            unavailable=unavailable,
            source_packet=source_packet,
        )

        use_web_search = bool(web_needed and settings["web_search_enabled"])
        if source_packet and source_packet.used_vector_search:
            search_summary = f"Loaded and ranked {len(retrieved)} local source chunk{'' if len(retrieved) == 1 else 's'}"
        elif vector_failure and retrieved:
            search_summary = "Loaded ready source files; vector search unavailable"
        elif local_artifact_request and retrieved:
            search_summary = "Loaded ready source files; vector search skipped for structured local analysis"
            record_run_event(
                run_id,
                type="tool_skipped",
                summary="Vector search skipped because local structured tools can satisfy the request",
                detail={"tool": "embedding_search", "reason": "structured_artifact_request"},
            )
        else:
            search_summary = f"Found {len(retrieved)} local source chunk{'' if len(retrieved) == 1 else 's'}"
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary=search_summary,
            detail={
                "local_source_count": len(retrieved),
                "full_text_file_count": len(file_texts),
                "unavailable_file_ids": unavailable,
                "vector_search_status": source_packet.vector_search_status if source_packet else "not_attempted",
                "vector_search_error": source_packet.vector_search_error if source_packet else "",
                "web_search": "enabled" if use_web_search else ("skipped_disabled" if web_needed else "not_required"),
                "web_search_engine": settings["web_search_engine"],
            },
        )

        checker_reports.append(_persist_checker_report(run_id, "/review/source-check.json", source_check(retrieved, unavailable)))

        if not retrieved:
            current_work = "implement"
            _record_work_action(run_id, current_work, "running", summary="Saving response")
            assistant_id = insert_message(session_id, "assistant", grounded_refusal(session_id, unavailable), unavailable)
            attach_run_messages(run_id, assistant_message_id=assistant_id)
            _record_work_action(run_id, current_work, "completed", summary="Saved grounded refusal")
            complete_run(run_id, assistant_message_id=assistant_id)
            return assistant_id

        sources = []
        for source_id, item in enumerate(retrieved, start=1):
            sources.append({**item, "source_id": source_id})

        selected_options = selected_artifact_options(follow_up_context)
        discovery_only = is_artifact_discovery_question(effective_question) and not selected_options
        if selected_options:
            task_contract = _selected_artifact_contract(task_contract, selected_options)
            outputs = list(task_contract.get("required_outputs") or outputs)
            kind = "create"
            update_run_kind(run_id, "create")
            update_run_contract(run_id, task_contract=task_contract)
        elif discovery_only:
            task_contract = _ai_discovery_contract(task_contract)
            outputs = ["decision_cards"]
            kind = "create"
            update_run_kind(run_id, "create")
            update_run_contract(run_id, task_contract=task_contract)

        if _is_ai_artifact_request(kind, outputs, effective_question, follow_up_context):
            current_work = "analysis"
            if not file_texts:
                file_texts = read_extracted_file_texts(session_id)
                selected_follow_up_files = set(_selected_follow_up_file_ids(follow_up_context))
                if selected_follow_up_files:
                    file_texts = [item for item in file_texts if str(item.get("file_id")) in selected_follow_up_files]
            set_action(
                run_id,
                "profile_table",
                "running",
                input_summary="Profiling source files generically",
            )
            prompt_context = _writer_packet(
                question=effective_question,
                task_contract=task_contract,
                evidence_packet={},
                follow_up_context=follow_up_context,
                selected_source_refs=_source_refs(sources) if follow_up_context else [],
            )
            try:
                artifact_result = await build_retrieval_artifacts(
                    ArtifactEngineInput(
                        run_id=run_id,
                        session_id=session_id,
                        question=effective_question,
                        task_contract=task_contract,
                        sources=sources,
                        file_texts=file_texts,
                        unavailable=unavailable,
                        history=history,
                        prompt_context=prompt_context,
                        settings=settings,
                        follow_up_context=follow_up_context,
                    )
                )
            except ArtifactEngineFailure as exc:
                diagnostics = exc.diagnostics
                for item in diagnostics.get("workspace_items", []) if isinstance(diagnostics, dict) else []:
                    if isinstance(item, dict) and item.get("path") and isinstance(item.get("content"), dict):
                        upsert_workspace_item(run_id, path=str(item["path"]), kind=str(item.get("kind") or "review"), content=item["content"])
                for attempt in diagnostics.get("repair_attempts", []) if isinstance(diagnostics, dict) else []:
                    if isinstance(attempt, dict):
                        record_repair_attempt(run_id, attempt)
                failure_stage = str(diagnostics.get("stage") or "") if isinstance(diagnostics, dict) else ""
                user_message = str(diagnostics.get("user_message") or "").strip() if isinstance(diagnostics, dict) else ""
                if not user_message:
                    user_message = (
                        f"I could not generate the requested artifacts safely. {exc} "
                        "I saved diagnostics for this run instead of showing partial or unvalidated artifacts."
                    )
                assistant_id = insert_message(session_id, "assistant", user_message, unavailable)
                cited_ids = [int(sources[0]["source_id"])] if sources and sources[0].get("source_id") is not None else []
                if cited_ids:
                    insert_citations(assistant_id, sources, cited_ids)
                attach_run_messages(run_id, assistant_message_id=assistant_id)
                set_action(
                    run_id,
                    "profile_table",
                    "failed",
                    output_summary="Artifact source profiling or planning failed"
                    if failure_stage in {"", "plan"}
                    else "Artifact source profiling completed before a later artifact failure",
                    error_summary=str(exc),
                    output_json={"diagnostics": diagnostics},
                )
                _record_work_action(
                    run_id,
                    current_work,
                    "failed",
                    summary="Artifact engine failed",
                    detail={"diagnostics": diagnostics},
                    error=str(exc),
                )
                set_action(
                    run_id,
                    "validate",
                    "failed",
                    output_summary="Artifact planning failed before validation"
                    if failure_stage == "plan"
                    else "Artifact validation failed after repair attempts",
                    error_summary=str(exc),
                    output_json={"diagnostics": diagnostics},
                )
                set_action(
                    run_id,
                    "persist_response",
                    "completed",
                    output_summary="Saved artifact failure explanation",
                    output_json={"assistant_message_id": assistant_id},
                )
                fail_run(run_id, str(exc))
                return assistant_id

            for item in artifact_result.workspace_items:
                upsert_workspace_item(run_id, path=item["path"], kind=item.get("kind", "analysis"), content=item.get("content", {}))
            set_action(
                run_id,
                "profile_table",
                "completed",
                output_summary="Profiled sources for AI artifact planning",
                output_json={
                    "source_profile": {
                        "summary": artifact_result.source_profile.get("summary"),
                        "diagnostics": artifact_result.source_profile.get("diagnostics", {}),
                    },
                    "artifact_plan": {
                        "mode": artifact_result.artifact_plan.get("mode"),
                        "artifact_count": len(artifact_result.artifact_plan.get("artifacts", [])),
                    },
                },
            )
            record_tool_call(
                run_id,
                {
                    "tool": "source_profiler",
                    "table_count": artifact_result.source_profile.get("diagnostics", {}).get("table_count", 0),
                    "text_count": artifact_result.source_profile.get("diagnostics", {}).get("text_count", 0),
                    "artifact_count": len(artifact_result.artifacts),
                },
            )
            if artifact_result.artifacts:
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized AI-planned artifacts",
                    detail={"artifact_count": len(artifact_result.artifacts), "artifact_kinds": [artifact.kind for artifact in artifact_result.artifacts]},
                )
            set_action(
                run_id,
                "build_evidence",
                "completed",
                output_summary="Prepared source profile and artifact plan",
                output_json={
                    "source_profile_path": "/analysis/source-profile.json",
                    "artifact_plan_path": "/plan/artifact-plan.json",
                    "source_count": len(sources),
                    "full_text_file_count": len(file_texts),
                    "artifact_plan_count": len(artifact_result.artifact_plan.get("artifacts", [])),
                },
            )

            current_work = "writing"
            _record_work_action(
                run_id,
                current_work,
                "completed",
                summary="Built artifact draft",
                detail={"model": artifact_result.model, "artifact_count": len(artifact_result.raw_artifacts)},
            )

            current_work = "review"
            _record_work_action(run_id, current_work, "running", summary="Validating AI-built artifacts")
            for attempt in artifact_result.repair_attempts:
                record_repair_attempt(run_id, attempt)
            if artifact_result.repair_attempts:
                set_action(
                    run_id,
                    "repair",
                    "completed",
                    output_summary=f"Completed {len(artifact_result.repair_attempts)} artifact repair attempt(s)",
                    output_json={"attempts": artifact_result.repair_attempts},
                )
            checker_reports.append(
                _persist_checker_report(
                    run_id,
                    "/review/analysis-check.json",
                    analysis_check({}, {"source_profile": artifact_result.source_profile, "artifact_plan": artifact_result.artifact_plan}),
                )
            )
            artifact_checker = artifact_check(artifact_result.artifacts, artifact_result.warnings, outputs)
            checker_reports.append(_persist_checker_report(run_id, "/review/artifact-check.json", artifact_checker))
            writing_checker = writing_check(artifact_result.answer, artifact_result.artifacts)
            checker_reports.append(_persist_checker_report(run_id, "/review/writing-check.json", writing_checker))
            upsert_workspace_item(
                run_id,
                path="/review/raw-draft.json",
                kind="review",
                content={"answer": artifact_result.answer, "artifact_count": len(artifact_result.artifacts)},
            )
            for report in artifact_result.review_reports:
                if isinstance(report, dict) and report.get("phase") == "red_team":
                    _persist_checker_report(run_id, "/review/red-team.json", report)
                    checker_reports.append(report)
                    break
            proofread = await _proofread_reviewed_output(
                run_id=run_id,
                model=settings["repair_model"],
                answer_content=(
                    f"{artifact_result.answer}\n\nNote: {vector_failure.user_message}"
                    if vector_failure
                    else artifact_result.answer
                ),
                insight_narrative=_first_insight_narrative(artifact_result.artifacts),
                red_team_report=artifact_result.review_reports[-1] if artifact_result.review_reports else None,
                evidence_packet={"source_profile": artifact_result.source_profile, "artifact_plan": artifact_result.artifact_plan},
            )
            answer_content = (
                f"{artifact_result.answer}\n\nNote: {vector_failure.user_message}"
                if vector_failure
                else artifact_result.answer
            )
            if isinstance(proofread.get("reviewed_output"), dict):
                reviewed_answer = proofread["reviewed_output"].get("answer")
                if isinstance(reviewed_answer, str) and reviewed_answer.strip():
                    answer_content = reviewed_answer.strip()
            cited_ids = [source_id for source_id in artifact_result.cited_source_ids if any(source["source_id"] == source_id for source in sources)]
            if artifact_result.artifacts and not cited_ids and sources:
                cited_ids = [sources[0]["source_id"]]
            contract_review = review_contract_result(
                task_contract=task_contract,
                answer=answer_content,
                artifacts=artifact_result.artifacts,
                cited_source_ids=cited_ids,
            )
            update_run_contract(run_id, review_scores=contract_review, revision_required=not contract_review["passed"])
            record_run_event(
                run_id,
                type="review_completed",
                summary="Semantic quality review passed" if contract_review["passed"] else "Semantic quality review needs revision",
                detail=contract_review,
            )
            if not contract_review["passed"]:
                _record_work_action(
                    run_id,
                    current_work,
                    "failed",
                    summary="Semantic quality review needs revision",
                    detail={"review": contract_review, "warnings": artifact_result.warnings},
                    error="; ".join(contract_review["failures"]),
                )
                mark_run_needs_revision(run_id, "; ".join(contract_review["failures"]))
                return None
            _record_work_action(
                run_id,
                current_work,
                "completed",
                summary="Semantic quality review passed",
                detail={"citation_count": len(cited_ids), "artifact_count": len(artifact_result.artifacts), "review": contract_review},
            )

            current_work = "implement"
            _record_work_action(run_id, current_work, "running", summary="Saving answer, sources, and artifacts")
            assistant_id = insert_message(session_id, "assistant", answer_content, unavailable)
            insert_citations(assistant_id, sources, cited_ids)
            artifact_ids = insert_artifacts(session_id, assistant_id, artifact_result.artifacts)
            _create_follow_up_questions_from_artifacts(run_id, assistant_id, artifact_ids, artifact_result.artifacts)
            for artifact_id, artifact in zip(artifact_ids, artifact_result.artifacts):
                record_artifact_version(
                    run_id,
                    {
                        "artifact_id": artifact_id,
                        "kind": artifact.kind,
                        "source": "artifact_engine",
                        "status": "persisted",
                    },
                )
            attach_run_messages(run_id, assistant_message_id=assistant_id)
            _record_work_action(
                run_id,
                current_work,
                "completed",
                summary="Saved answer with warnings" if contract_review["warnings"] else "Saved answer",
                detail={"artifact_ids": artifact_ids, "outcome": contract_review.get("outcome", "completed")},
            )
            complete_run(run_id, assistant_message_id=assistant_id, status=contract_review.get("outcome", "completed"))
            return assistant_id

        current_work = "analysis"
        if not file_texts and any(output in outputs for output in ("chart", "table", "file_draft")):
            file_texts = read_extracted_file_texts(session_id)
            selected_follow_up_files = set(_selected_follow_up_file_ids(follow_up_context))
            if selected_follow_up_files:
                file_texts = [item for item in file_texts if str(item.get("file_id")) in selected_follow_up_files]
        insight_brief = build_insight_brief(effective_question, file_texts, sources) if file_texts else {}
        analysis_evidence_packet: dict[str, Any] = {}
        if insight_brief and (insight_brief.get("insights") or insight_brief.get("tables")):
            upsert_workspace_item(run_id, path="/analysis/insight-brief.json", kind="analysis", content=insight_brief)
            record_tool_call(
                run_id,
                {
                    "tool": "analysis_engine",
                    "frameworks_run": insight_brief.get("frameworks_run", []),
                    "insight_count": len(insight_brief.get("insights", [])),
                    "table_count": len(insight_brief.get("tables", [])),
                    "quality_review": insight_brief.get("quality_review", {}),
                },
            )
            analysis_evidence_packet = {"insight_brief": insight_brief}
            task_contract = dict(task_contract)
            capability_snapshot = dict(task_contract.get("capability_snapshot") or {})
            capability_snapshot["insight_brief_available"] = True
            capability_snapshot["analysis_framework_count"] = len(insight_brief.get("frameworks_run", []))
            task_contract["capability_snapshot"] = capability_snapshot
            executable_contract = dict(task_contract.get("executable_contract") or {})
            if executable_contract:
                executable_contract["capability_snapshot"] = capability_snapshot
                task_contract["executable_contract"] = executable_contract
            update_run_contract(run_id, task_contract=task_contract)
        artifact_advice = build_artifact_advice(effective_question, file_texts, sources, task_contract, insight_brief=insight_brief) if file_texts else {}
        selected_artifact = None
        if artifact_advice and artifact_advice.get("recommendations"):
            public_advice = {
                "request": artifact_advice.get("request"),
                "discovery_only": artifact_advice.get("discovery_only"),
                "should_ask": artifact_advice.get("should_ask"),
                "explicit_chart_type": artifact_advice.get("explicit_chart_type"),
                "auto_select": artifact_advice.get("auto_select"),
                "insight_summary": artifact_advice.get("insight_summary", ""),
                "recommendations": artifact_advice.get("recommendations", []),
                "table_profiles": artifact_advice.get("table_profiles", []),
            }
            task_contract = dict(task_contract)
            task_contract["artifact_recommendations"] = public_advice["recommendations"]
            update_run_contract(run_id, task_contract=task_contract)
            prompt_context = _controller_packet(run_id=run_id, session_id=session_id, question=question, history=history, task_contract=task_contract)
            upsert_workspace_item(run_id, path="/analysis/artifact-recommendations.json", kind="analysis", content=public_advice)
            artifact_answer = answered_question_value(run_id, "artifact_choice")
            if artifact_advice.get("should_ask") and not artifact_answer:
                set_action(
                    run_id,
                    "profile_table",
                    "completed",
                    output_summary="Profiled source tables and ranked artifact recommendations",
                    output_json={
                        "artifact_recommendations": public_advice["recommendations"],
                        "table_profiles": public_advice["table_profiles"],
                    },
                )
                options = recommendation_options(artifact_advice)
                created = create_run_question(
                    run_id,
                    action_kind="ask_user",
                    kind="artifact_choice",
                    question="Choose an artifact to create from this file.",
                    options=options,
                    default_option=options[0]["id"] if options else "",
                )
                _record_work_action(
                    run_id,
                    current_work,
                    "running",
                    summary="Waiting for your artifact choice",
                    detail={"question_id": created.id, "question_kind": created.kind, "options": [item["id"] for item in options]},
                )
                mark_run_awaiting_user_input(run_id)
                return None
            if artifact_answer:
                selected_artifact = selected_recommendation(artifact_advice, _answer_selected_option(artifact_answer))
            elif requested_chart_type(effective_question) or artifact_advice.get("auto_select"):
                selected_artifact = selected_recommendation(artifact_advice, None)
            if selected_artifact or artifact_advice.get("discovery_only"):
                set_action(
                    run_id,
                    "profile_table",
                    "completed",
                    output_summary="Profiled source tables and ranked artifact recommendations",
                    output_json={
                        "artifact_recommendations": public_advice["recommendations"],
                        "table_profiles": public_advice["table_profiles"],
                    },
                )
            if selected_artifact:
                task_contract = dict(task_contract)
                task_contract["selected_artifact_recommendation"] = {
                    key: value for key, value in selected_artifact.items() if key != "artifact"
                }
                selected_kind = selected_artifact.get("artifact_kind")
                required_outputs = [selected_kind] if selected_kind in {"chart", "table", "summary_panel", "file_draft"} else outputs
                task_contract["required_outputs"] = required_outputs
                executable_contract = dict(task_contract.get("executable_contract") or {})
                if executable_contract:
                    executable_contract["required_outputs"] = required_outputs
                    executable_contract["primary_outputs"] = required_outputs
                    task_contract["executable_contract"] = executable_contract
                update_run_contract(run_id, task_contract=task_contract)
                prompt_context = _writer_packet(
                    question=effective_question,
                    task_contract=task_contract,
                    evidence_packet={**analysis_evidence_packet, "artifact_recommendation": task_contract["selected_artifact_recommendation"]},
                    follow_up_context=follow_up_context,
                    selected_source_refs=_source_refs(sources) if follow_up_context else [],
                )

        discovery_artifacts: list[dict[str, Any]] = []
        if artifact_advice and artifact_advice.get("discovery_only") and artifact_advice.get("recommendations"):
            discovery_artifacts = [build_recommendation_cards_artifact(artifact_advice, task_contract, sources)]
            task_contract = _discovery_only_contract(task_contract)
            outputs = ["decision_cards"]
            update_run_contract(run_id, task_contract=task_contract)
        selected_decision_artifacts = _selected_decision_artifacts(follow_up_context)
        if selected_decision_artifacts:
            selected_kinds = [
                str(artifact.get("kind"))
                for artifact in selected_decision_artifacts
                if str(artifact.get("kind") or "") in {"chart", "table", "summary_panel", "file_draft", "comparison", "mermaid"}
            ]
            if selected_kinds:
                task_contract = dict(task_contract)
                task_contract["required_outputs"] = selected_kinds
                task_contract["primary_outputs"] = selected_kinds
                executable_contract = dict(task_contract.get("executable_contract") or {})
                if executable_contract:
                    executable_contract["required_outputs"] = selected_kinds
                    executable_contract["primary_outputs"] = selected_kinds
                    task_contract["executable_contract"] = executable_contract
                update_run_contract(run_id, task_contract=task_contract)
        selected_artifact_payload = build_recommended_artifact(selected_artifact)
        survey_result = (
            None
            if discovery_artifacts or selected_artifact_payload or selected_decision_artifacts
            else build_survey_artifacts(effective_question, file_texts, sources)
            if file_texts
            else None
        )
        deterministic_artifacts = (
            discovery_artifacts
            or selected_decision_artifacts
            or ([selected_artifact_payload] if selected_artifact_payload else (survey_result.artifacts if survey_result else []))
        )
        if survey_result and survey_result.tool_call:
            record_tool_call(run_id, survey_result.tool_call)
            upsert_workspace_item(run_id, path="/analysis/survey-profile.json", kind="analysis", content=survey_result.tool_call)
            set_action(
                run_id,
                "profile_table",
                "completed",
                output_summary=survey_result.summary or "Profiled structured source table",
                output_json={
                    "tool_call": survey_result.tool_call,
                    "artifact_count": len(deterministic_artifacts),
                    "artifact_recommendations": artifact_advice.get("recommendations", []) if artifact_advice else [],
                },
            )
        if survey_result and survey_result.evidence_packet:
            upsert_workspace_item(run_id, path="/analysis/evidence-packet.json", kind="analysis", content=survey_result.evidence_packet)
            task_contract = dict(task_contract)
            capability_snapshot = dict(task_contract.get("capability_snapshot") or {})
            capability_snapshot["evidence_packet_available"] = True
            capability_snapshot["source_count"] = len(sources)
            task_contract["capability_snapshot"] = capability_snapshot
            executable_contract = dict(task_contract.get("executable_contract") or {})
            if executable_contract:
                executable_contract["capability_snapshot"] = capability_snapshot
                task_contract["executable_contract"] = executable_contract
            update_run_contract(run_id, task_contract=task_contract)
            prompt_context = _writer_packet(
                question=effective_question,
                task_contract=task_contract,
                evidence_packet=survey_result.evidence_packet,
                follow_up_context=follow_up_context,
                selected_source_refs=_source_refs(sources) if follow_up_context else [],
            )
        evidence_packet = survey_result.evidence_packet if survey_result and survey_result.evidence_packet else analysis_evidence_packet
        if evidence_packet and not survey_result:
            upsert_workspace_item(run_id, path="/analysis/evidence-packet.json", kind="analysis", content=evidence_packet)
        checker_reports.append(_persist_checker_report(run_id, "/review/analysis-check.json", analysis_check(insight_brief, evidence_packet)))
        analysis_summary = (
            survey_result.summary
            if survey_result
            else str(insight_brief.get("summary") or "Prepared deterministic insight brief")
            if insight_brief
            else "Prepared grounded source packet"
        )
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary=analysis_summary or "Prepared grounded source packet",
            detail={
                "source_count": len(sources),
                "files": list(dict.fromkeys(source["file_name"] for source in sources)),
                "full_text_file_count": len(file_texts),
                "deterministic_artifact_count": len(deterministic_artifacts),
                "tool_call": survey_result.tool_call if survey_result else {"tool": "analysis_engine", "insight_count": len(insight_brief.get("insights", [])) if insight_brief else 0},
            },
        )

        current_work = "writing"
        _record_work_action(run_id, current_work, "running", summary="Writing grounded answer and artifacts")
        prompt_context = _writer_packet(
            question=effective_question,
            task_contract=task_contract,
            evidence_packet=evidence_packet if evidence_packet else None,
            follow_up_context=follow_up_context,
            selected_source_refs=_source_refs(sources) if follow_up_context else [],
        )
        chat_kwargs = {
            "model": settings["writing_model"],
            "question": effective_question,
            "sources": sources,
            "unavailable": unavailable,
            "history": history,
            "prompt_context": prompt_context,
        }
        if use_web_search:
            chat_kwargs["use_web_search"] = True
            chat_kwargs["web_search_engine"] = settings["web_search_engine"]
        if settings["model_routing_mode"] == "deep" and settings["reasoning_effort"] != "none":
            chat_kwargs["reasoning_effort"] = settings["reasoning_effort"]
        writing_failure: ToolFailure | None = None
        used_evidence_draft = False
        local_structured_output = False
        try:
            profile = context_profile()
            can_polish_draft = bool(
                survey_result
                and survey_result.evidence_packet
                and any(artifact.get("kind") == "file_draft" for artifact in deterministic_artifacts)
                and profile.get("drafting_policy") == "model_polished_evidence"
            )
            artifact_discovery_request = is_artifact_discovery_request(effective_question, task_contract)
            timeline_request = is_timeline_request(effective_question, task_contract)
            if discovery_artifacts:
                chat = ChatResult(
                    answer=discovery_answer(task_contract),
                    cited_source_ids=[sources[0]["source_id"]],
                    artifacts=[],
                    model="local-artifact-advisor",
                    usage=UsageInfo(),
                )
                local_structured_output = True
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized advisor-backed artifact option cards",
                    detail={"artifact": "decision_cards"},
                )
            elif selected_artifact_payload:
                chart_type = selected_artifact_payload.get("chart_type") if selected_artifact_payload.get("kind") == "chart" else None
                chat = ChatResult(
                    answer="I created the selected grounded artifact from the attached source data.",
                    cited_source_ids=[sources[0]["source_id"]],
                    artifacts=[],
                    model="local-artifact-advisor",
                    usage=UsageInfo(),
                )
                local_structured_output = True
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized the selected advisor artifact",
                    detail={"artifact": selected_artifact_payload.get("kind"), "chart_type": chart_type},
                )
            elif selected_decision_artifacts:
                chat = ChatResult(
                    answer="I created the selected artifacts from the current session sources.",
                    cited_source_ids=[sources[0]["source_id"]],
                    artifacts=[],
                    model="local-artifact-producer",
                    usage=UsageInfo(),
                )
                local_structured_output = True
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized the selected decision-card artifacts",
                    detail={"artifact_count": len(selected_decision_artifacts)},
                )
            elif not deterministic_artifacts and artifact_discovery_request:
                task_contract = _discovery_only_contract(task_contract)
                outputs = ["decision_cards"]
                update_run_contract(run_id, task_contract=task_contract)
                deterministic_artifacts = [build_artifact_options_artifact(effective_question, task_contract, sources)]
                chat = ChatResult(
                    answer=discovery_answer(task_contract),
                    cited_source_ids=[sources[0]["source_id"]],
                    artifacts=[],
                    model="local-artifact-discovery",
                    usage=UsageInfo(),
                )
                local_structured_output = True
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized a JSON-rendered artifact option panel",
                    detail={"artifact": "decision_cards"},
                )
            elif not deterministic_artifacts and timeline_request:
                timeline_artifacts = build_timeline_artifacts(effective_question, task_contract, sources)
                if timeline_artifacts:
                    task_contract = timeline_contract(task_contract)
                    update_run_contract(run_id, task_contract=task_contract)
                    prompt_context = _writer_packet(
                        question=effective_question,
                        task_contract=task_contract,
                        follow_up_context=follow_up_context,
                        selected_source_refs=_source_refs(sources) if follow_up_context else [],
                    )
                    deterministic_artifacts = timeline_artifacts
                    chat = ChatResult(
                        answer=timeline_answer(task_contract, sources),
                        cited_source_ids=[sources[0]["source_id"]],
                        artifacts=[],
                        model="local-timeline-builder",
                        usage=UsageInfo(),
                    )
                    local_structured_output = True
                    record_run_event(
                        run_id,
                        type="artifact_synthesized",
                        summary="Synthesized JSON-rendered timeline and summary panels",
                        detail={"artifact": "summary_panel", "component": "Timeline", "artifact_count": len(timeline_artifacts)},
                    )
                else:
                    chat = await _chat_with_optional_context(chat_kwargs)
            elif can_polish_draft:
                draft_chat = await provider_registry().active().write_draft_from_evidence(
                    model=settings["writing_model"],
                    question=effective_question,
                    prompt_context=prompt_context,
                    evidence_packet=survey_result.evidence_packet,
                    sources=sources,
                    reasoning_effort=settings["reasoning_effort"] if settings["model_routing_mode"] == "deep" else "none",
                )
                if draft_chat.artifacts:
                    deterministic_artifacts = _replace_draft_artifact(deterministic_artifacts, draft_chat.artifacts[0])
                chat = ChatResult(
                    answer=draft_chat.answer,
                    cited_source_ids=draft_chat.cited_source_ids,
                    artifacts=[],
                    model=draft_chat.model,
                    usage=draft_chat.usage,
                )
                used_evidence_draft = True
                record_agent_action(
                    run_id,
                    {
                        "action": "write_draft_from_evidence",
                        "model": draft_chat.model or settings["writing_model"],
                        "evidence_packet": "/analysis/evidence-packet.json",
                    },
                )
            else:
                chat = await _chat_with_optional_context(chat_kwargs)
        except Exception as exc:
            if not deterministic_artifacts:
                raise
            writing_failure = _provider_tool_failure(exc)
            record_run_event(
                run_id,
                type="tool_failed",
                summary=writing_failure.user_message,
                detail={
                    "tool": "chat_writing",
                    "status": writing_failure.status,
                    "technical_detail": writing_failure.technical_detail,
                },
            )
            record_run_event(
                run_id,
                type="fallback_used",
                summary="Used deterministic artifacts after the writing model became unavailable",
                detail={"fallback": "deterministic_artifacts", "artifact_count": len(deterministic_artifacts)},
            )
            add_quality_warning(run_id, writing_failure.user_message)
            chat = ChatResult(
                answer="I prepared grounded analysis materials from the attached source data.",
                cited_source_ids=[sources[0]["source_id"]],
                artifacts=[],
                model=settings["writing_model"],
                usage=UsageInfo(),
            )
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary="Generated answer draft",
            detail={
                "model": chat.model or settings["writing_model"],
                "artifact_count": len(chat.artifacts),
                "deterministic_artifact_count": len(deterministic_artifacts),
                "web_search_enabled": use_web_search,
            },
        )

        if chat.usage.prompt_tokens or chat.usage.prompt_cost:
            record_usage_event(
                session_id=session_id,
                message_id=user_id,
                kind="chat_prompt",
                model=chat.model,
                usage=UsageInfo(
                    prompt_tokens=chat.usage.prompt_tokens,
                    total_tokens=chat.usage.prompt_tokens,
                    prompt_cost=chat.usage.prompt_cost,
                    total_cost=chat.usage.prompt_cost,
                ),
            )

        current_work = "review"
        _record_work_action(run_id, current_work, "running", summary="Validating grounding and artifacts")
        cited_ids = [source_id for source_id in chat.cited_source_ids if any(source["source_id"] == source_id for source in sources)]
        raw_artifacts = list(chat.artifacts or deterministic_artifacts)
        supporting_outputs = set(task_contract.get("supporting_outputs") or [])
        if "summary_panel" in supporting_outputs and not any(artifact.get("kind") == "summary_panel" for artifact in raw_artifacts):
            synthesized_panel = build_summary_panel_artifact(survey_result.evidence_packet if survey_result else {})
            if synthesized_panel:
                raw_artifacts.append(synthesized_panel)
                record_repair_attempt(
                    run_id,
                    {
                        "strategy": "supporting_artifact_synthesis",
                        "artifact": "summary_panel",
                        "result": "synthesized from evidence packet",
                    },
                )
                record_run_event(
                    run_id,
                    type="artifact_synthesized",
                    summary="Synthesized a supporting summary panel from the evidence packet",
                    detail={"artifact": "summary_panel"},
                )
            else:
                record_run_event(
                    run_id,
                    type="artifact_downgraded",
                    summary="Skipped a supporting summary panel because the draft already carries the summary function",
                    detail={"artifact": "summary_panel", "reason": "no_synthesis_source"},
                )
        artifact_report = validate_artifacts_with_report(raw_artifacts, sources, default_source_ids=cited_ids)
        review_warnings = list(artifact_report.warnings)
        if chat.artifacts and artifact_report.warnings and not artifact_report.artifacts and deterministic_artifacts:
            record_repair_attempt(
                run_id,
                {
                    "strategy": "deterministic_fallback",
                    "warnings": artifact_report.warnings,
                    "result": "using parsed survey artifact",
                },
            )
            artifact_report = validate_artifacts_with_report(deterministic_artifacts, sources, default_source_ids=cited_ids)
            review_warnings.extend(artifact_report.warnings)
        elif deterministic_artifacts and not local_structured_output and not any(artifact.kind == "chart" for artifact in artifact_report.artifacts):
            fallback_report = validate_artifacts_with_report(deterministic_artifacts, sources, default_source_ids=cited_ids)
            artifact_report.artifacts.extend(fallback_report.artifacts)
            artifact_report.warnings.extend(fallback_report.warnings)
            review_warnings.extend(fallback_report.warnings)
        if artifact_report.artifacts and not cited_ids and sources:
            cited_ids = [sources[0]["source_id"]]
        answer_content = chat.answer
        if deterministic_artifacts and not chat.artifacts and not used_evidence_draft and not local_structured_output:
            answer_content = _answer_from_artifacts(task_contract, deterministic_artifacts)
        degradation_notes = []
        if vector_failure:
            degradation_notes.append(vector_failure.user_message)
        if writing_failure:
            degradation_notes.append(writing_failure.user_message)
        degradation_notes = list(dict.fromkeys(degradation_notes))
        if degradation_notes and artifact_report.artifacts:
            answer_content = f"{answer_content}\n\nNote: {' '.join(degradation_notes)}"
        if chat.artifacts and artifact_report.warnings and not artifact_report.artifacts:
            record_repair_attempt(
                run_id,
                {
                    "strategy": "schema_validation",
                    "warnings": artifact_report.warnings,
                    "result": "no valid artifact",
                },
            )
            answer_content = _artifact_validation_failure_message(artifact_report.warnings)
        artifact_checker = artifact_check(artifact_report.artifacts, review_warnings, outputs)
        checker_reports.append(_persist_checker_report(run_id, "/review/artifact-check.json", artifact_checker))
        writing_checker = writing_check(answer_content, artifact_report.artifacts)
        checker_reports.append(_persist_checker_report(run_id, "/review/writing-check.json", writing_checker))
        upsert_workspace_item(
            run_id,
            path="/review/raw-draft.json",
            kind="review",
            content={"answer": answer_content, "artifact_count": len(artifact_report.artifacts)},
        )
        red_team_report = await _maybe_red_team_review(
            run_id=run_id,
            model=settings["repair_model"],
            phase_goal="Review final answer and artifacts before persistence.",
            task_contract=task_contract,
            evidence_packet=evidence_packet if isinstance(evidence_packet, dict) else {},
            sources=sources,
            artifacts=artifact_report.artifacts,
            answer_content=answer_content,
            checker_reports=checker_reports,
            deterministic_reports=[artifact_checker, writing_checker],
        )
        if red_team_report:
            checker_reports.append(red_team_report)
            if red_team_report.get("severity") == "high":
                record_repair_attempt(
                    run_id,
                    {
                        "strategy": "red_team_review",
                        "phase": red_team_report.get("phase", "red_team"),
                        "findings": red_team_report.get("findings", []),
                        "result": "needs_revision",
                    },
                )
                update_run_contract(run_id, review_scores=red_team_report, revision_required=True)
                _record_work_action(
                    run_id,
                    current_work,
                    "failed",
                    summary="Red-team review found high-severity issues",
                    detail=red_team_report,
                    error="; ".join(red_team_report.get("required_fixes", []) or red_team_report.get("findings", [])),
                )
                mark_run_needs_revision(run_id, "; ".join(red_team_report.get("required_fixes", []) or red_team_report.get("findings", [])))
                return None
        proofread = await _proofread_reviewed_output(
            run_id=run_id,
            model=settings["repair_model"],
            answer_content=answer_content,
            insight_narrative=_first_insight_narrative(artifact_report.artifacts),
            red_team_report=red_team_report,
            evidence_packet=evidence_packet if isinstance(evidence_packet, dict) else {},
        )
        if isinstance(proofread.get("reviewed_output"), dict):
            reviewed_answer = proofread["reviewed_output"].get("answer")
            if isinstance(reviewed_answer, str) and reviewed_answer.strip():
                answer_content = reviewed_answer.strip()
        contract_review = review_contract_result(
            task_contract=task_contract,
            answer=answer_content,
            artifacts=artifact_report.artifacts,
            cited_source_ids=cited_ids,
        )
        if chat.artifacts and artifact_report.warnings and not artifact_report.artifacts:
            contract_review = {
                **contract_review,
                "passed": False,
                "score": 0.0,
                "failures": list(dict.fromkeys([*contract_review.get("failures", []), answer_content])),
                "outcome": "needs_revision",
            }
        update_run_contract(run_id, review_scores=contract_review, revision_required=not contract_review["passed"])
        record_run_event(
            run_id,
            type="review_completed",
            summary="Semantic quality review passed" if contract_review["passed"] else "Semantic quality review needs revision",
            detail=contract_review,
        )
        if not contract_review["passed"]:
            review_warnings.extend(contract_review["failures"])
            _record_work_action(
                run_id,
                current_work,
                "failed",
                summary="Semantic quality review needs revision",
                detail={
                    "citation_count": len(cited_ids),
                    "artifact_count": len(artifact_report.artifacts),
                    "warnings": review_warnings,
                    "review": contract_review,
                },
                error="; ".join(contract_review["failures"]),
            )
            mark_run_needs_revision(run_id, "; ".join(contract_review["failures"]))
            return None
        review_summary = "Semantic quality review passed"
        if contract_review["warnings"]:
            review_summary = "Completed with supporting artifact adjustments"
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary=review_summary,
            detail={
                "citation_count": len(cited_ids),
                "artifact_count": len(artifact_report.artifacts),
                "warnings": review_warnings,
                "review": contract_review,
            },
        )

        current_work = "implement"
        _record_work_action(run_id, current_work, "running", summary="Saving answer, sources, and artifacts")
        assistant_id = insert_message(session_id, "assistant", answer_content, unavailable)
        if chat.usage.completion_tokens or chat.usage.completion_cost:
            record_usage_event(
                session_id=session_id,
                message_id=assistant_id,
                kind="chat_completion",
                model=chat.model,
                usage=UsageInfo(
                    completion_tokens=chat.usage.completion_tokens,
                    total_tokens=chat.usage.completion_tokens,
                    completion_cost=chat.usage.completion_cost,
                    total_cost=chat.usage.completion_cost,
                ),
            )
        insert_citations(assistant_id, sources, cited_ids)
        artifact_ids = insert_artifacts(session_id, assistant_id, artifact_report.artifacts)
        _create_follow_up_questions_from_artifacts(run_id, assistant_id, artifact_ids, artifact_report.artifacts)
        for artifact_id, artifact in zip(artifact_ids, artifact_report.artifacts):
            record_artifact_version(
                run_id,
                {
                    "artifact_id": artifact_id,
                    "kind": artifact.kind,
                    "source": "deterministic_tool" if deterministic_artifacts else "model_output",
                    "status": "persisted",
                },
            )
        attach_run_messages(run_id, assistant_message_id=assistant_id)
        _record_work_action(
            run_id,
            current_work,
            "completed",
            summary="Saved answer with warnings" if contract_review["warnings"] else "Saved answer",
            detail={"artifact_ids": artifact_ids, "outcome": contract_review.get("outcome", "completed")},
        )
        if contract_review["warnings"]:
            record_run_event(
                run_id,
                type="completed_with_warning",
                summary="Run completed with supporting artifact adjustments",
                detail={"warnings": contract_review["warnings"]},
            )
        complete_run(
            run_id,
            assistant_message_id=assistant_id,
            status=contract_review.get("outcome", "completed"),
        )
        return assistant_id
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        _record_work_action(run_id, current_work, "failed", summary="Action failed", error=message)
        fail_run(run_id, message)
        raise


async def answer_legacy(session_id: str, question: str) -> str:
    history = recent_history(session_id)
    user_id = insert_message(session_id, "user", question, [])
    retrieved, unavailable = await retrieve(session_id, question, user_id, history)
    if not retrieved:
        return insert_message(session_id, "assistant", grounded_refusal(session_id, unavailable), unavailable)

    sources = []
    for source_id, item in enumerate(retrieved, start=1):
        sources.append({**item, "source_id": source_id})
    settings = current_app_settings()
    chat = await provider_registry().active().chat(
        model=settings["chat_model"],
        question=question,
        sources=sources,
        unavailable=unavailable,
        history=history,
    )
    if chat.usage.prompt_tokens or chat.usage.prompt_cost:
        record_usage_event(
            session_id=session_id,
            message_id=user_id,
            kind="chat_prompt",
            model=chat.model,
            usage=UsageInfo(
                prompt_tokens=chat.usage.prompt_tokens,
                total_tokens=chat.usage.prompt_tokens,
                prompt_cost=chat.usage.prompt_cost,
                total_cost=chat.usage.prompt_cost,
            ),
        )
    assistant_id = insert_message(session_id, "assistant", chat.answer, unavailable)
    if chat.usage.completion_tokens or chat.usage.completion_cost:
        record_usage_event(
            session_id=session_id,
            message_id=assistant_id,
            kind="chat_completion",
            model=chat.model,
            usage=UsageInfo(
                completion_tokens=chat.usage.completion_tokens,
                total_tokens=chat.usage.completion_tokens,
                completion_cost=chat.usage.completion_cost,
                total_cost=chat.usage.completion_cost,
            ),
        )
    report = validate_artifacts_with_report(chat.artifacts, sources)
    insert_citations(assistant_id, sources, chat.cited_source_ids)
    insert_artifacts(session_id, assistant_id, report.artifacts)
    return assistant_id
