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
    mark_run_needs_revision,
    mark_run_needs_setup,
    record_agent_action,
    record_artifact_version,
    record_repair_attempt,
    record_run_event,
    record_tool_call,
    set_step,
    start_run,
    update_run_kind,
    update_run_contract,
    update_run_prompt_context,
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
from .artifacts import ValidatedArtifact, validate_artifacts_with_report
from .database import connect
from .models import AgentPhase
from .models import CitationOut
from .openrouter import ChatResult, OpenRouterClient, OpenRouterMissingKey, OpenRouterResponseError
from .orchestration import build_preflight, is_broad_create_request
from .prompt_context import build_prompt_context, context_profile, refresh_session_context
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
    return kind == "create" and any(output in outputs for output in ("chart", "table", "file_draft"))


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
            WHERE sf.session_id = ? AND f.status = 'ready'
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
        unavailable = conn.execute(
            """
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
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
            WHERE sf.session_id = ? AND f.status = 'ready'
            """,
            (session_id,),
        ).fetchall()
        unavailable = conn.execute(
            """
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
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
                WHERE c.file_id IN ({placeholders})
                ORDER BY sf.attached_at, f.id, c.ordinal
                LIMIT ?
                """,
                (session_id, *file_ids, settings["retrieval_depth"]),
            ).fetchall()
            return [source_from_row(row, score=1.0) for row in fallback_rows], [r["id"] for r in unavailable]

    embedding = await OpenRouterClient().embedding_result([retrieval_query], model)
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
    try:
        return await OpenRouterClient().chat(**chat_kwargs)
    except TypeError as exc:
        if "prompt_context" not in str(exc):
            raise
        legacy_kwargs = dict(chat_kwargs)
        legacy_kwargs.pop("prompt_context", None)
        return await OpenRouterClient().chat(**legacy_kwargs)


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
        content = "I stopped before saving a result because the semantic quality review did not pass. Please revise the request or retry with clearer direction."
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
    current_phase: AgentPhase = "plan"
    start_run(run_id)
    record_run_event(run_id, type="run_started", summary="Agent run started", detail={"question": question})

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
            message = str(provider.get("message") or "OpenRouter key must be verified before model-backed runs can start.")
            current_phase = "plan"
            set_step(run_id, current_phase, "failed", summary="OpenRouter provider needs setup", error=message)
            for phase in ("search", "analysis", "writing", "review", "implement"):
                set_step(run_id, phase, "skipped", summary="Skipped until OpenRouter setup is verified")
            assistant_id = insert_message(
                session_id,
                "assistant",
                f"OpenRouter setup needs attention before I can run the model-led workflow. {message}",
                [],
            )
            attach_run_messages(run_id, assistant_message_id=assistant_id)
            mark_run_needs_setup(run_id, message)
            return assistant_id

        current_phase = "plan"
        set_step(run_id, current_phase, "running", summary="Planning the request")
        kind = classify_request(question)
        outputs = requested_outputs(question)
        web_needed = requires_web_search(question)
        settings = current_app_settings()
        update_run_kind(run_id, kind)
        model_assignments = run.model_assignments or build_preflight(session_id, question)["model_assignments"]

        pending_question = get_current_question(run_id)
        if pending_question:
            set_step(
                run_id,
                current_phase,
                "running",
                summary="Waiting for your planning answer",
                detail={"question_id": pending_question.id, "question_kind": pending_question.kind},
            )
            mark_run_awaiting_user_input(run_id)
            return None

        answered_questions = [question.model_dump() for question in list_run_questions(run_id) if question.status == "answered"]
        prompt_context = build_prompt_context(session_id=session_id, question=question, history=history)
        update_run_prompt_context(run_id, prompt_context)
        if not run.task_contract:
            raw_contract = await OpenRouterClient().plan_task(
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
            prompt_context = build_prompt_context(session_id=session_id, question=question, task_contract=task_contract, history=history)
            update_run_prompt_context(run_id, prompt_context)
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
            prompt_context = build_prompt_context(session_id=session_id, question=question, task_contract=task_contract, history=history)
            update_run_prompt_context(run_id, prompt_context)
        if task_contract.get("needs_user_question") and not answered_question_value(run_id, "choice"):
            options = task_contract.get("question_options") if isinstance(task_contract.get("question_options"), list) else []
            created = create_run_question(
                run_id,
                phase="plan",
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
            set_step(
                run_id,
                current_phase,
                "running",
                summary="Waiting for your planning choice",
                detail={"question_id": created.id, "question_kind": created.kind, "options": [item["id"] for item in options]},
            )
            mark_run_awaiting_user_input(run_id)
            return None

        planning_suffix = _planning_answer_suffix(run_id)
        choice_answer = answered_question_value(run_id, "choice")
        if choice_answer:
            selected = _answer_selected_option(choice_answer)
            free_text = _answer_free_text(choice_answer)
            updates = update_contract_user_direction(
                task_contract,
                {"selected_option": selected, "free_text": free_text},
            )
            update_run_contract(run_id, task_contract=updates)
            task_contract = updates
            prompt_context = build_prompt_context(session_id=session_id, question=question, task_contract=task_contract, history=history)
            update_run_prompt_context(run_id, prompt_context)
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
        set_step(
            run_id,
            current_phase,
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

        current_phase = "search"
        local_artifact_request = _is_local_artifact_request(kind, outputs)
        search_start = "Loading ready source files" if local_artifact_request else "Searching local source chunks"
        set_step(run_id, current_phase, "running", summary=search_start)
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
        set_step(
            run_id,
            current_phase,
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

        if not retrieved:
            current_phase = "analysis"
            set_step(
                run_id,
                current_phase,
                "skipped",
                summary="No ready source chunks were available",
                detail={"unavailable_file_ids": unavailable},
            )
            current_phase = "writing"
            set_step(run_id, current_phase, "completed", summary="Prepared grounded refusal")
            current_phase = "review"
            set_step(run_id, current_phase, "completed", summary="No generated artifacts to validate")
            current_phase = "implement"
            set_step(run_id, current_phase, "running", summary="Saving response")
            assistant_id = insert_message(session_id, "assistant", grounded_refusal(session_id, unavailable), unavailable)
            attach_run_messages(run_id, assistant_message_id=assistant_id)
            set_step(run_id, current_phase, "completed", summary="Saved grounded refusal")
            complete_run(run_id, assistant_message_id=assistant_id)
            return assistant_id

        sources = []
        for source_id, item in enumerate(retrieved, start=1):
            sources.append({**item, "source_id": source_id})

        current_phase = "analysis"
        if not file_texts and any(output in outputs for output in ("chart", "table", "file_draft")):
            file_texts = read_extracted_file_texts(session_id)
        survey_result = build_survey_artifacts(effective_question, file_texts, sources) if file_texts else None
        deterministic_artifacts = survey_result.artifacts if survey_result else []
        if survey_result and survey_result.tool_call:
            record_tool_call(run_id, survey_result.tool_call)
            upsert_workspace_item(run_id, path="/analysis/survey-profile.json", kind="analysis", content=survey_result.tool_call)
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
            prompt_context = build_prompt_context(
                session_id=session_id,
                question=effective_question,
                task_contract=task_contract,
                evidence_packet=survey_result.evidence_packet,
                history=history,
            )
            update_run_prompt_context(run_id, prompt_context)
        analysis_summary = survey_result.summary if survey_result else "Prepared grounded source packet"
        set_step(
            run_id,
            current_phase,
            "completed",
            summary=analysis_summary or "Prepared grounded source packet",
            detail={
                "source_count": len(sources),
                "files": list(dict.fromkeys(source["file_name"] for source in sources)),
                "full_text_file_count": len(file_texts),
                "deterministic_artifact_count": len(deterministic_artifacts),
                "tool_call": survey_result.tool_call if survey_result else {},
            },
        )

        current_phase = "writing"
        set_step(run_id, current_phase, "running", summary="Writing grounded answer and artifacts")
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
        try:
            profile = context_profile()
            can_polish_draft = bool(
                survey_result
                and survey_result.evidence_packet
                and any(artifact.get("kind") == "file_draft" for artifact in deterministic_artifacts)
                and profile.get("drafting_policy") == "model_polished_evidence"
            )
            if can_polish_draft:
                draft_chat = await OpenRouterClient().write_draft_from_evidence(
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
        set_step(
            run_id,
            current_phase,
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

        current_phase = "review"
        set_step(run_id, current_phase, "running", summary="Validating grounding and artifacts")
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
        elif deterministic_artifacts and not any(artifact.kind == "chart" for artifact in artifact_report.artifacts):
            fallback_report = validate_artifacts_with_report(deterministic_artifacts, sources, default_source_ids=cited_ids)
            artifact_report.artifacts.extend(fallback_report.artifacts)
            artifact_report.warnings.extend(fallback_report.warnings)
            review_warnings.extend(fallback_report.warnings)
        if artifact_report.artifacts and not cited_ids and sources:
            cited_ids = [sources[0]["source_id"]]
        answer_content = chat.answer
        if deterministic_artifacts and not chat.artifacts and not used_evidence_draft:
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
            answer_content = (
                f"{answer_content}\n\n"
                "I could not render the requested artifact from the available structured data."
            )
        contract_review = review_contract_result(
            task_contract=task_contract,
            answer=answer_content,
            artifacts=artifact_report.artifacts,
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
            review_warnings.extend(contract_review["failures"])
            set_step(
                run_id,
                current_phase,
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
            for phase in ("implement",):
                set_step(run_id, phase, "skipped", summary="Skipped because review did not pass")
            mark_run_needs_revision(run_id, "; ".join(contract_review["failures"]))
            return None
        review_summary = "Semantic quality review passed"
        if contract_review["warnings"]:
            review_summary = "Completed with supporting artifact adjustments"
        set_step(
            run_id,
            current_phase,
            "completed",
            summary=review_summary,
            detail={
                "citation_count": len(cited_ids),
                "artifact_count": len(artifact_report.artifacts),
                "warnings": review_warnings,
                "review": contract_review,
            },
        )

        current_phase = "implement"
        set_step(run_id, current_phase, "running", summary="Saving answer, sources, and artifacts")
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
        set_step(
            run_id,
            current_phase,
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
        set_step(run_id, current_phase, "failed", summary="Phase failed", error=message)
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
    chat = await OpenRouterClient().chat(
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
