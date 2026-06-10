"""Agent-run support helpers: work actions, follow-up context, contracts, packets, planning, and review helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..agent_runs import (
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
from ..agent_runtime import (
    build_summary_panel_artifact,
    ensure_provider_ready,
    file_manifest,
    is_instruction_only_draft,
    normalize_task_contract,
    reconcile_task_contract,
    review_contract_result,
    update_contract_user_direction,
)
from ..analysis_engine import build_insight_brief
from ..artifact_engine import (
    ArtifactEngineFailure,
    ArtifactEngineInput,
    build_retrieval_artifacts,
    is_artifact_discovery_question,
    profile_sources,
    selected_artifact_options,
)
from ..artifact_discovery import (
    build_artifact_options_artifact,
    build_timeline_artifacts,
    discovery_answer,
    is_artifact_discovery_request,
    is_timeline_request,
    timeline_answer,
    timeline_contract,
)
from ..artifact_advisor import (
    build_artifact_advice,
    build_recommendation_cards_artifact,
    build_recommended_artifact,
    recommendation_options,
    requested_chart_type,
    selected_recommendation,
)
from ..artifacts import ValidatedArtifact, validate_artifacts_with_report
from ..database import connect
from ..excel_workflow import build_excel_workflow_answer, build_excel_workflow_html_app
from ..models import CitationOut
from ..openrouter import ChatResult, OpenRouterClient, OpenRouterMissingKey, OpenRouterResponseError
from ..orchestration import build_preflight, is_broad_create_request
from ..prompt_context import context_profile, refresh_session_context
from ..providers import provider_registry
from ..review_checks import analysis_check, artifact_check, plan_check, source_check, writing_check
from ..settings_store import current_app_settings
from ..survey import build_survey_artifacts, read_extracted_file_texts
from ..usage import UsageInfo, record_usage_event
from ..utils import cosine, excerpt, json_dumps, new_id, now
from .sources import SourceAcquisitionResult



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


def _artifact_validation_failure_message(warnings: list[str]) -> str:
    detail = " ".join(warnings)
    if "timeline chart" in detail:
        return "The model proposed a timeline chart, but FileChat supports timelines only as JSON-render roadmap artifacts. Retry as a roadmap/timeline artifact."
    return "The model returned an artifact shape that FileChat could not safely render. Retry the artifact or choose one of the structured options."


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


def _artifact_draft_content(artifact: dict[str, Any]) -> str:
    spec = artifact.get("spec") if isinstance(artifact.get("spec"), dict) else {}
    return str(artifact.get("content") or spec.get("content") or "")


def _replace_draft_artifact(artifacts: list[dict[str, Any]], draft: dict[str, Any]) -> list[dict[str, Any]]:
    if is_instruction_only_draft(_artifact_draft_content(draft)):
        return artifacts
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
