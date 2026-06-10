"""Database persistence for chat messages, citations, and artifacts."""

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
