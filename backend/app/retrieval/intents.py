"""Request classification: pattern constants and intent/output predicates."""

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
