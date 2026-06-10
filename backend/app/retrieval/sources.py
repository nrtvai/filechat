"""Source acquisition and retrieval: ready-source loading, semantic search, provider failure mapping."""

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
from .intents import is_summary_request



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
