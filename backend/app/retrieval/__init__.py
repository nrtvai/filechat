"""Retrieval package facade.

Re-exports the full public surface of the former backend/app/retrieval.py module
so existing imports and test monkeypatch paths keep working unchanged.
"""

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

from .intents import (
    CREATE_REQUEST_PATTERNS,
    SUMMARY_REQUEST_PATTERNS,
    WEB_SEARCH_PATTERNS,
    classify_request,
    is_summary_request,
    requested_outputs,
    requires_web_search,
)
from .sources import (
    SourceAcquisitionResult,
    ToolFailure,
    _has_ready_embeddings,
    _provider_tool_failure,
    _tool_failure_from_warning,
    contextual_retrieval_query,
    grounded_refusal,
    load_ready_sources,
    recent_history,
    retrieve,
    semantic_retrieve,
    source_from_row,
)
from .persistence import (
    insert_artifacts,
    insert_citations,
    insert_message,
)
from .run_support import (
    WORK_ACTION_KIND,
    _ai_discovery_contract,
    _answer_free_text,
    _answer_from_artifacts,
    _answer_selected_option,
    _apply_follow_up_source_filter,
    _artifact_draft_content,
    _artifact_provider_degraded_answer,
    _artifact_specs,
    _artifact_validation_failure_message,
    _broad_planning_options,
    _chat_with_optional_context,
    _compact_follow_up_context,
    _controller_packet,
    _create_follow_up_questions_from_artifacts,
    _decision_card_options,
    _deliverable_options,
    _discovery_only_contract,
    _first_insight_narrative,
    _follow_up_context_for_run,
    _is_ai_artifact_request,
    _is_local_artifact_request,
    _maybe_red_team_review,
    _persist_checker_report,
    _planning_answer_suffix,
    _planning_question_options,
    _prior_action_summaries,
    _proofread_reviewed_output,
    _record_work_action,
    _replace_draft_artifact,
    _selected_artifact_contract,
    _selected_decision_artifacts,
    _selected_follow_up_file_ids,
    _should_offer_interview,
    _source_refs,
    _writer_packet,
)
from .orchestrator import (
    _try_excel_workflow_answer,
    answer,
    answer_legacy,
    execute_agent_run,
)
