from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .audit import record_audit_event
from .agent_runs import (
    answer_run_question,
    create_agent_run,
    get_agent_run,
    get_current_question,
    list_agent_runs,
    list_run_events,
    list_workspace_items,
    mark_run_awaiting_approval,
    mark_run_needs_setup,
    update_run_preflight,
)
from .agent_runtime import ensure_provider_ready, verify_openrouter_provider
from .auth import Principal, current_principal, require_log_exporter, require_settings_admin
from .config import get_settings
from .database import connect, init_db
from .ingest import process_file
from .models import (
    AgentRunEventOut,
    AgentRunOut,
    AgentRunQuestionOut,
    AgentRunWorkspaceItemOut,
    AnswerRunQuestionRequest,
    AuditEventOut,
    ContextProfileOut,
    ContextProfilePatch,
    CurrentUserOut,
    AskRequest,
    CreateSession,
    FileRecord,
    MessageOut,
    MetaIssueCreate,
    MetaIssueOut,
    MetaIssueUpdate,
    ModelInfo,
    RetryRunRequest,
    SessionOut,
    SettingsOut,
    SettingsPatch,
    UsageSummary,
)
from .openrouter import OpenRouterClient
from .orchestration import build_preflight, model_recommendations
from .prompt_context import context_profile, patch_context_profile, refresh_session_context, session_context
from .providers import provider_registry
from .meta_issues import capture_internal_issue, create_meta_issue, list_meta_issues, update_meta_issue_status
from .retrieval import answer, execute_agent_run
from .security import sanitize_metadata
from .settings_store import clear_saved_openrouter_key, current_app_settings, get_openrouter_key, set_openrouter_key, set_setting
from .usage import usage_for_file, usage_for_message, usage_summary
from .utils import extension, json_loads, new_id, now, sha256_bytes


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="FileChat API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def capture_unhandled_exception(request: Request, exc: Exception):
    capture_internal_issue(
        organization_id="org_single",
        created_by=None,
        source="runtime",
        severity="error",
        title=exc.__class__.__name__,
        body=str(exc),
        metadata={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def settings_admin(principal: Principal = Depends(current_principal)) -> Principal:
    return require_settings_admin(principal)


def log_exporter(principal: Principal = Depends(current_principal)) -> Principal:
    return require_log_exporter(principal)


def current_user_out(principal: Principal) -> CurrentUserOut:
    return CurrentUserOut(
        id=principal.user_id,
        display_name=principal.display_name,
        email=principal.email,
        role=principal.role,
        organization_id=principal.organization_id,
        edition=principal.edition,
        enterprise_enabled=principal.enterprise_enabled,
        auth_test_mode=principal.auth_test_mode,
        auth_mode=principal.auth_mode,
        capabilities=principal.capabilities,
    )


def ensure_session(session_id: str, principal: Principal) -> None:
    with connect() as conn:
        if not conn.execute(
            "SELECT id FROM sessions WHERE id = ? AND organization_id = ?",
            (session_id, principal.organization_id),
        ).fetchone():
            raise HTTPException(status_code=404, detail="Session not found")


def file_out(row, session_id: str | None = None) -> FileRecord:
    file_usage = usage_for_file(session_id, row["id"]) if session_id else None
    return FileRecord(
        id=row["id"],
        hash=row["hash"],
        name=row["name"],
        type=row["type"],
        size=row["size"],
        status=row["status"],
        progress=row["progress"],
        page_count=row["page_count"],
        chunk_count=row["chunk_count"],
        error=row["error"],
        indexing_prompt_tokens=file_usage.prompt_tokens if file_usage else 0,
        indexing_total_cost=file_usage.total_cost if file_usage else 0.0,
    )


def queue_file_for_processing(conn, file_id: str) -> None:
    conn.execute(
        "UPDATE files SET status = ?, progress = ?, error = NULL, updated_at = ? WHERE id = ?",
        ("queued", 0, now(), file_id),
    )


def citations_for(message_id: str):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM citations WHERE message_id = ? ORDER BY ordinal",
            (message_id,),
        ).fetchall()


def artifacts_for(message_id: str):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM artifacts WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        ).fetchall()


def message_out(row) -> MessageOut:
    message_usage = usage_for_message(row["id"])
    return MessageOut(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        unavailable_file_ids=json_loads(row["unavailable_file_ids"], []),
        created_at=row["created_at"],
        citations=[
            {
                "id": c["id"],
                "message_id": c["message_id"],
                "file_id": c["file_id"],
                "chunk_id": c["chunk_id"],
                "source_label": c["source_label"],
                "location": c["location"],
                "excerpt": c["excerpt"],
                "score": c["score"],
                "ordinal": c["ordinal"],
            }
            for c in citations_for(row["id"])
        ],
        artifacts=[
            {
                "id": a["id"],
                "session_id": a["session_id"],
                "message_id": a["message_id"],
                "kind": a["kind"],
                "title": a["title"],
                "caption": a["caption"],
                "display_mode": a["display_mode"],
                "source_chunk_ids": json_loads(a["source_chunk_ids"], []),
                "spec": json_loads(a["spec_json"], {}),
                "created_at": a["created_at"],
            }
            for a in artifacts_for(row["id"])
        ],
        prompt_tokens=message_usage.prompt_tokens,
        completion_tokens=message_usage.completion_tokens,
        total_tokens=message_usage.total_tokens,
        prompt_cost=message_usage.prompt_cost,
        completion_cost=message_usage.completion_cost,
        total_cost=message_usage.total_cost,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/me", response_model=CurrentUserOut)
def get_current_user(principal: Principal = Depends(current_principal)):
    return current_user_out(principal)


@app.get("/api/settings", response_model=SettingsOut)
def get_app_settings():
    return current_app_settings()


@app.patch("/api/settings", response_model=SettingsOut)
def patch_settings(patch: SettingsPatch, principal: Principal = Depends(settings_admin)):
    apply_settings_patch(patch, principal)
    return current_app_settings()


@app.get("/api/admin/settings", response_model=SettingsOut)
def get_admin_settings(_: Principal = Depends(settings_admin)):
    return current_app_settings()


@app.patch("/api/admin/settings", response_model=SettingsOut)
def patch_admin_settings(patch: SettingsPatch, principal: Principal = Depends(settings_admin)):
    apply_settings_patch(patch, principal)
    return current_app_settings()


@app.delete("/api/admin/settings/openrouter-key", response_model=SettingsOut)
def clear_openrouter_key(principal: Principal = Depends(settings_admin)):
    _, source = get_openrouter_key()
    if source == "env":
        raise HTTPException(status_code=409, detail="Environment OpenRouter keys cannot be cleared in FileChat.")
    clear_saved_openrouter_key()
    record_audit_event(
        principal,
        action="settings.openrouter_key_cleared",
        target_type="settings",
        metadata={"changed": ["openrouter_api_key"]},
    )
    return current_app_settings()


def apply_settings_patch(patch: SettingsPatch, principal: Principal) -> None:
    changed: list[str] = []
    if patch.openrouter_api_key is not None and patch.openrouter_api_key.strip():
        set_openrouter_key(patch.openrouter_api_key.strip())
        changed.append("openrouter_api_key")
    if patch.chat_model is not None:
        set_setting("chat_model", patch.chat_model.strip())
        changed.append("chat_model")
    if patch.orchestrator_model is not None:
        set_setting("orchestrator_model", patch.orchestrator_model.strip())
        changed.append("orchestrator_model")
    if patch.analysis_model is not None:
        set_setting("analysis_model", patch.analysis_model.strip())
        changed.append("analysis_model")
    if patch.writing_model is not None:
        set_setting("writing_model", patch.writing_model.strip())
        changed.append("writing_model")
    if patch.repair_model is not None:
        set_setting("repair_model", patch.repair_model.strip())
        changed.append("repair_model")
    if patch.embedding_model is not None:
        set_setting("embedding_model", patch.embedding_model.strip())
        changed.append("embedding_model")
    if patch.ocr_model is not None:
        set_setting("ocr_model", patch.ocr_model.strip())
        changed.append("ocr_model")
    if patch.retrieval_depth is not None:
        set_setting("retrieval_depth", str(patch.retrieval_depth))
        changed.append("retrieval_depth")
    if patch.strict_grounding is not None:
        set_setting("strict_grounding", "true" if patch.strict_grounding else "false")
        changed.append("strict_grounding")
    if patch.web_search_enabled is not None:
        set_setting("web_search_enabled", "true" if patch.web_search_enabled else "false")
        changed.append("web_search_enabled")
    if patch.web_search_engine is not None:
        set_setting("web_search_engine", patch.web_search_engine)
        changed.append("web_search_engine")
    if patch.reasoning_effort is not None:
        set_setting("reasoning_effort", patch.reasoning_effort)
        changed.append("reasoning_effort")
    if patch.model_routing_mode is not None:
        set_setting("model_routing_mode", patch.model_routing_mode)
        changed.append("model_routing_mode")
    if patch.high_cost_confirmation is not None:
        set_setting("high_cost_confirmation", "true" if patch.high_cost_confirmation else "false")
        changed.append("high_cost_confirmation")
    if changed:
        record_audit_event(
            principal,
            action="settings.updated",
            target_type="settings",
            metadata={"changed": changed},
        )


@app.get("/api/admin/audit-events", response_model=list[AuditEventOut])
def list_audit_events(principal: Principal = Depends(log_exporter)):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_events
            WHERE organization_id = ?
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (principal.organization_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "organization_id": row["organization_id"],
            "actor_user_id": row["actor_user_id"],
            "actor_role": row["actor_role"],
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "metadata": sanitize_metadata(json_loads(row["metadata"], {})),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@app.post("/api/meta-issues", response_model=MetaIssueOut)
async def create_meta_issue_endpoint(payload: MetaIssueCreate, principal: Principal = Depends(current_principal)):
    return await create_meta_issue(
        principal,
        source=payload.source,
        severity=payload.severity,
        title=payload.title,
        body=payload.body,
        metadata=payload.metadata,
    )


@app.get("/api/admin/meta-issues", response_model=list[MetaIssueOut])
def list_meta_issue_endpoint(principal: Principal = Depends(settings_admin)):
    return list_meta_issues(principal.organization_id)


@app.patch("/api/admin/meta-issues/{issue_id}", response_model=MetaIssueOut)
def update_meta_issue_endpoint(issue_id: str, payload: MetaIssueUpdate, principal: Principal = Depends(settings_admin)):
    issue = update_meta_issue_status(principal.organization_id, issue_id, payload.status)
    if not issue:
        raise HTTPException(status_code=404, detail="Meta issue not found")
    record_audit_event(
        principal,
        action="meta_issue.updated",
        target_type="meta_issue",
        target_id=issue_id,
        metadata={"status": payload.status},
    )
    return issue


@app.post("/api/settings/openrouter/verify", response_model=SettingsOut)
async def verify_openrouter_settings(_: Principal = Depends(settings_admin)):
    await verify_openrouter_provider()
    return current_app_settings()


@app.get("/api/context/profile", response_model=ContextProfileOut)
def get_context_profile():
    return context_profile()


@app.patch("/api/context/profile", response_model=ContextProfileOut)
def update_context_profile(patch: ContextProfilePatch):
    return patch_context_profile(patch.model_dump(exclude_none=True))


@app.get("/api/models", response_model=list[ModelInfo])
async def list_openrouter_models(
    kind: str = Query(default="chat", pattern="^(chat|embedding)$"),
    _: Principal = Depends(settings_admin),
):
    try:
        return await provider_registry().active().models(kind)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/models/recommendations")
def get_model_recommendations(task: str = Query(default="")):
    return model_recommendations(task)


@app.get("/api/sessions", response_model=list[SessionOut])
def list_sessions(principal: Principal = Depends(current_principal)):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*,
              COUNT(DISTINCT sf.file_id) file_count,
              (
                SELECT content FROM messages m
                WHERE m.session_id = s.id
                ORDER BY m.created_at DESC
                LIMIT 1
              ) latest_message_preview
            FROM sessions s
            LEFT JOIN session_files sf ON sf.session_id = s.id
            WHERE s.organization_id = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """,
            (principal.organization_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/sessions", response_model=SessionOut)
def create_session(payload: CreateSession, principal: Principal = Depends(current_principal)):
    session_id = new_id("ses")
    created = now()
    title = payload.title or "New reading session"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, title, organization_id, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, title, principal.organization_id, principal.user_id, created, created),
        )
    return SessionOut(id=session_id, title=title, created_at=created, updated_at=created, file_count=0)


@app.get("/api/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, principal: Principal = Depends(current_principal)):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, COUNT(DISTINCT sf.file_id) file_count, NULL latest_message_preview
            FROM sessions s
            LEFT JOIN session_files sf ON sf.session_id = s.id
            WHERE s.id = ? AND s.organization_id = ?
            GROUP BY s.id
            """,
            (session_id, principal.organization_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return dict(row)


@app.post("/api/sessions/{session_id}/context/refresh")
def refresh_context(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    return refresh_session_context(session_id)


@app.get("/api/sessions/{session_id}/context")
def get_session_context(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    return session_context(session_id)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, principal: Principal = Depends(current_principal)):
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND organization_id = ?",
            (session_id, principal.organization_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/files", response_model=list[FileRecord])
async def upload_files(
    session_id: str,
    background: BackgroundTasks,
    uploads: list[UploadFile] = File(...),
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)

    out: list[FileRecord] = []
    for upload in uploads:
        body = await upload.read()
        digest = sha256_bytes(body)
        ext = extension(upload.filename or "document.txt")
        uploads_dir = get_settings().resolved_data_dir / "uploads"
        stored_path = uploads_dir / f"{digest}.{ext}"
        if not stored_path.exists():
            with stored_path.open("wb") as handle:
                handle.write(body)

        created_file = False
        with connect() as conn:
            existing = conn.execute(
                "SELECT * FROM files WHERE hash = ? AND organization_id = ?",
                (digest, principal.organization_id),
            ).fetchone()
            if existing:
                file_id = existing["id"]
            else:
                file_id = new_id("fil")
                conn.execute(
                    """
                    INSERT INTO files
                    (id, hash, organization_id, created_by, name, type, size, path, status, progress, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        digest,
                        principal.organization_id,
                        principal.user_id,
                        upload.filename or stored_path.name,
                        ext.upper(),
                        len(body),
                        str(stored_path),
                        "queued",
                        0,
                        now(),
                        now(),
                    ),
                )
                created_file = True
            conn.execute(
                """
                INSERT OR IGNORE INTO session_files (session_id, file_id, attached_at)
                VALUES (?, ?, ?)
                """,
                (session_id, file_id, now()),
            )
            row = conn.execute(
                "SELECT * FROM files WHERE id = ? AND organization_id = ?",
                (file_id, principal.organization_id),
            ).fetchone()
        if created_file or row["status"] in {"failed", "queued"}:
            with connect() as conn:
                queue_file_for_processing(conn, file_id)
                row = conn.execute(
                    "SELECT * FROM files WHERE id = ? AND organization_id = ?",
                    (file_id, principal.organization_id),
                ).fetchone()
            background.add_task(process_file, file_id, session_id)
        out.append(file_out(row, session_id))
    return out


@app.get("/api/sessions/{session_id}/files", response_model=list[FileRecord])
def list_session_files(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.* FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            WHERE sf.session_id = ? AND f.organization_id = ?
            ORDER BY sf.attached_at
            """,
            (session_id, principal.organization_id),
        ).fetchall()
    return [file_out(r, session_id) for r in rows]


@app.delete("/api/sessions/{session_id}/files/{file_id}")
def detach_file(session_id: str, file_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    with connect() as conn:
        cursor = conn.execute("DELETE FROM session_files WHERE session_id = ? AND file_id = ?", (session_id, file_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="File attachment not found")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/files/{file_id}/retry", response_model=FileRecord)
def retry_file(
    session_id: str,
    file_id: str,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT f.* FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            WHERE sf.session_id = ? AND f.id = ? AND f.organization_id = ?
            """,
            (session_id, file_id, principal.organization_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File attachment not found")
        if row["status"] == "ready":
            return file_out(row, session_id)
        queue_file_for_processing(conn, file_id)
        row = conn.execute(
            "SELECT * FROM files WHERE id = ? AND organization_id = ?",
            (file_id, principal.organization_id),
        ).fetchone()
    background.add_task(process_file, file_id, session_id)
    return file_out(row, session_id)


@app.get("/api/files/{file_id}/status", response_model=FileRecord)
def file_status(file_id: str, principal: Principal = Depends(current_principal)):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE id = ? AND organization_id = ?",
            (file_id, principal.organization_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    return file_out(row)


@app.post("/api/sessions/{session_id}/messages", response_model=MessageOut)
async def ask(session_id: str, payload: AskRequest, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    try:
        message_id = await answer(session_id, payload.content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return message_out(row)


@app.post("/api/sessions/{session_id}/runs", response_model=AgentRunOut)
async def start_agent_run(
    session_id: str,
    payload: AskRequest,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = create_agent_run(session_id, payload.content)
    preflight = build_preflight(session_id, payload.content)
    update_run_preflight(run.id, **preflight)
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        mark_run_needs_setup(run.id, str(provider.get("message") or "OpenRouter provider is not verified."))
        return get_agent_run(run.id) or run
    if preflight["execution_plan"].get("requires_approval"):
        mark_run_awaiting_approval(run.id)
    else:
        background.add_task(execute_agent_run, run.id)
    refreshed = get_agent_run(run.id)
    return refreshed or run


@app.get("/api/sessions/{session_id}/runs", response_model=list[AgentRunOut])
def list_runs(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    return list_agent_runs(session_id)


@app.get("/api/sessions/{session_id}/runs/{run_id}", response_model=AgentRunOut)
def get_run(session_id: str, run_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@app.post("/api/sessions/{session_id}/runs/{run_id}/approve-plan", response_model=AgentRunOut)
async def approve_run_plan(
    session_id: str,
    run_id: str,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if run.status != "awaiting_approval":
        return run
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        mark_run_needs_setup(run_id, str(provider.get("message") or "OpenRouter provider is not verified."))
        return get_agent_run(run_id) or run
    background.add_task(execute_agent_run, run_id)
    return get_agent_run(run_id) or run


@app.post("/api/sessions/{session_id}/runs/{run_id}/retry", response_model=AgentRunOut)
async def retry_run(
    session_id: str,
    run_id: str,
    payload: RetryRunRequest,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    question = run.question if payload.mode == "rerun" else f"{run.question}\n\nRepair the requested artifact if possible."
    next_run = create_agent_run(session_id, question, kind=run.kind)
    preflight = build_preflight(session_id, question)
    update_run_preflight(next_run.id, **preflight)
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        mark_run_needs_setup(next_run.id, str(provider.get("message") or "OpenRouter provider is not verified."))
        return get_agent_run(next_run.id) or next_run
    background.add_task(execute_agent_run, next_run.id)
    return get_agent_run(next_run.id) or next_run


@app.post("/api/sessions/{session_id}/runs/{run_id}/resume", response_model=AgentRunOut)
async def resume_run(
    session_id: str,
    run_id: str,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        mark_run_needs_setup(run_id, str(provider.get("message") or "OpenRouter provider is not verified."))
        return get_agent_run(run_id) or run
    background.add_task(execute_agent_run, run_id)
    return get_agent_run(run_id) or run


@app.get("/api/sessions/{session_id}/runs/{run_id}/contract")
def get_run_contract(session_id: str, run_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return {
        "planner_contract": run.task_contract.get("planner_contract", {}) if isinstance(run.task_contract, dict) else {},
        "executable_contract": run.task_contract.get("executable_contract", {}) if isinstance(run.task_contract, dict) else {},
        "task_contract": run.task_contract,
        "provider_status": run.provider_status,
        "agent_actions": run.agent_actions,
        "review_scores": run.review_scores,
        "revision_required": run.revision_required,
        "prompt_context": run.prompt_context,
    }


@app.get("/api/sessions/{session_id}/runs/{run_id}/questions/current", response_model=AgentRunQuestionOut | None)
def get_current_run_question(session_id: str, run_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return get_current_question(run_id)


@app.post("/api/sessions/{session_id}/runs/{run_id}/questions/{question_id}/answer", response_model=AgentRunOut)
async def answer_current_run_question(
    session_id: str,
    run_id: str,
    question_id: str,
    payload: AnswerRunQuestionRequest,
    background: BackgroundTasks,
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    answer = dict(payload.answer)
    if payload.selected_option is not None:
        answer["selected_option"] = payload.selected_option
    if payload.free_text is not None:
        answer["free_text"] = payload.free_text
    answered = answer_run_question(run_id, question_id, answer)
    if not answered:
        raise HTTPException(status_code=404, detail="Run question not found")
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        mark_run_needs_setup(run_id, str(provider.get("message") or "OpenRouter provider is not verified."))
        return get_agent_run(run_id) or run
    background.add_task(execute_agent_run, run_id)
    return get_agent_run(run_id) or run


@app.get("/api/sessions/{session_id}/runs/{run_id}/events", response_model=list[AgentRunEventOut])
def get_run_events(
    session_id: str,
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return list_run_events(run_id, after_seq=after_seq)


@app.get("/api/sessions/{session_id}/runs/{run_id}/workspace", response_model=list[AgentRunWorkspaceItemOut])
def get_run_workspace(session_id: str, run_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return list_workspace_items(run_id)


@app.get("/api/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [message_out(r) for r in rows]


@app.get("/api/sessions/{session_id}/artifacts/{artifact_id}/export")
def export_artifact(
    session_id: str,
    artifact_id: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    principal: Principal = Depends(current_principal),
):
    ensure_session(session_id, principal)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = ? AND session_id = ?",
            (artifact_id, session_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found")

    spec = json_loads(row["spec_json"], {})
    if format == "json":
        filename = str(spec.get("filename") or f"{row['title'] or 'artifact'}.json")
        if not filename.endswith(".json"):
            filename = f"{filename}.json"
        return Response(
            content=json.dumps(spec, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if row["kind"] == "file_draft":
        content = spec.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        filename = str(spec.get("filename") or "draft.md")
    elif row["kind"] == "chart":
        values = spec.get("values") if isinstance(spec, dict) else []
        lines = [f"# {row['title']}", ""]
        if row["caption"]:
            lines.extend([row["caption"], ""])
        lines.append("| Label | Value |")
        lines.append("| --- | ---: |")
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    lines.append(f"| {item.get('label', '')} | {item.get('value', '')} |")
        content = "\n".join(lines)
        filename = f"{row['title'] or 'chart'}.md"
    else:
        content = f"# {row['title']}\n\n{row['caption']}\n"
        filename = f"{row['title'] or 'artifact'}.md"
    if not filename.endswith(".md"):
        filename = f"{filename}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sessions/{session_id}/usage", response_model=UsageSummary)
def get_usage_summary(session_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    return usage_summary(session_id)


@app.get("/api/sessions/{session_id}/citations/{message_id}")
def get_citations(session_id: str, message_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    with connect() as conn:
        owner = conn.execute(
            "SELECT id FROM messages WHERE id = ? AND session_id = ?",
            (message_id, session_id),
        ).fetchone()
    if not owner:
        raise HTTPException(status_code=404, detail="Message not found")
    return [
        {
            "id": c["id"],
            "message_id": c["message_id"],
            "file_id": c["file_id"],
            "chunk_id": c["chunk_id"],
            "source_label": c["source_label"],
            "location": c["location"],
            "excerpt": c["excerpt"],
            "score": c["score"],
            "ordinal": c["ordinal"],
        }
        for c in citations_for(message_id)
    ]
