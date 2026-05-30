from __future__ import annotations

import json
from typing import Any
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
    set_action,
    update_run_preflight,
    upsert_workspace_item,
)
from .agent_runtime import ensure_provider_ready, verify_openrouter_provider
from .auth import Principal, current_principal, require_log_exporter, require_settings_admin
from .bot_integrations import slack_attachments, telegram_attachments, verify_slack_signature, verify_telegram_secret
from .config import get_settings
from .database import connect, init_db
from .excel_workflow import build_excel_workflow_html_app, is_excel_workflow_request
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
    WikiEdgeCreate,
    WikiEdgeOut,
    WikiEdgePatch,
    WikiNodeCreate,
    WikiNodeOut,
    WikiNodePatch,
)
from .notion_export import markdown_for_artifact, notion_import_bundle, pdf_for_artifact, slugify_filename, table_payload_for_artifact
from .open_design import open_design_bundle_for_artifact
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
from .wiki import (
    create_edge,
    create_node,
    delete_edge,
    delete_node,
    get_edge,
    get_node,
    list_edges,
    list_nodes,
    update_edge,
    update_node,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="FileChat API", lifespan=lifespan)


def _mark_provider_setup_failure(run_id: str, provider: dict) -> None:
    message = str(provider.get("message") or "OpenRouter provider is not verified.")
    set_action(
        run_id,
        "verify_provider",
        "failed",
        input_summary="Checking OpenRouter provider status",
        output_summary="OpenRouter provider needs setup",
        error_summary=message,
        output_json=provider,
    )
    mark_run_needs_setup(run_id, message)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WorkflowFileText(BaseModel):
    file_id: str | None = None
    file_name: str
    text: str


class WorkflowRequest(BaseModel):
    description: str = Field(min_length=1)
    file_texts: list[WorkflowFileText] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)


def spreadsheet_workflow_questions(request: WorkflowRequest) -> list[str]:
    questions: list[str] = []
    description = request.description.lower()

    if len(request.file_texts) < 2:
        questions.append("Which source spreadsheet files are required for this recurring workflow?")
    if not any(word in description for word in ("copy", "paste", "edit", "manual", "reconcile", "join", "match")):
        questions.append("Which manual copy/paste/edit steps should be replaced with deterministic automation?")
    if not any(word in description for word in ("key", "sku", "match", "join", "reconcile", "reconciliation")):
        questions.append("What matching key, row rule, or column relationship connects the dependent spreadsheets?")
    if not is_excel_workflow_request(request.description):
        questions.append("What final local HTML spreadsheet workflow app should be generated?")

    return questions


def workflow_interview_payload(request: WorkflowRequest) -> dict[str, Any]:
    questions = spreadsheet_workflow_questions(request)
    return {
        "status": "needs_interview" if questions else "ready_to_generate",
        "ready_to_generate": not questions,
        "required_questions": questions,
    }


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


def settings_for_principal(principal: Principal) -> dict:
    payload = current_app_settings()
    payload["edition"] = principal.edition
    payload["settings_scope"] = "organization" if principal.enterprise_enabled else "single_user"
    return payload


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


def integration_principal(service: str, organization_id: str = "org_single") -> Principal:
    return Principal(
        user_id=f"usr_integration_{service}",
        display_name=f"{service.title()} integration",
        email=None,
        role="member",
        organization_id=organization_id,
        edition="community",
        auth_test_mode=False,
        auth_mode=f"{service}_webhook",
    )


def reject_bot_webhook(service: str, reason: str, status_code: int = 401):
    principal = integration_principal(service)
    capture_internal_issue(
        organization_id=principal.organization_id,
        created_by=principal.user_id,
        source="bot",
        severity="warning",
        title=f"{service.title()} webhook rejected",
        body=reason,
        metadata={"service": service, "reason": reason},
    )
    record_audit_event(
        principal,
        action="bot.webhook_rejected",
        target_type="integration",
        target_id=service,
        metadata={"service": service, "reason": reason},
    )
    raise HTTPException(status_code=status_code, detail=reason)


def queue_integration_attachments(
    *,
    service: str,
    attachments: list[dict[str, object]],
    background: BackgroundTasks,
):
    principal = integration_principal(service)
    session_id = new_id("ses")
    stamp = now()
    title = f"{service.title()} attachment intake"
    accepted: list[FileRecord] = []
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, title, organization_id, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, title, principal.organization_id, principal.user_id, stamp, stamp),
        )
    for attachment in attachments:
        body = attachment["body"]
        if not isinstance(body, bytes):
            continue
        name = str(attachment.get("name") or f"{service}-attachment.txt")
        digest = sha256_bytes(body)
        ext = extension(name)
        stored_path = get_settings().resolved_data_dir / "uploads" / f"{digest}.{ext}"
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
                        name,
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
                "INSERT OR IGNORE INTO session_files (session_id, file_id, attached_at) VALUES (?, ?, ?)",
                (session_id, file_id, now()),
            )
            row = conn.execute(
                "SELECT * FROM files WHERE id = ? AND organization_id = ?",
                (file_id, principal.organization_id),
            ).fetchone()
            if created_file or row["status"] in {"failed", "queued"}:
                queue_file_for_processing(conn, file_id)
                row = conn.execute(
                    "SELECT * FROM files WHERE id = ? AND organization_id = ?",
                    (file_id, principal.organization_id),
                ).fetchone()
        background.add_task(process_file, file_id, session_id)
        accepted.append(file_out(row, session_id))
    return {"ok": True, "service": service, "session_id": session_id, "accepted": len(accepted), "files": accepted}


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
    citations = [
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
    ]
    grounding = {"status": "not_applicable", "notice": "", "detail": ""}
    if row["role"] == "assistant":
        grounding = (
            {
                "status": "cited",
                "notice": "Citations attached.",
                "detail": f"This answer includes {len(citations)} retrieved source snippet(s).",
            }
            if citations
            else {
                "status": "no_citations",
                "notice": "No citations attached to this answer.",
                "detail": "FileChat is being explicit that this response has no retrieved source snippets.",
            }
        )
    return MessageOut(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        unavailable_file_ids=json_loads(row["unavailable_file_ids"], []),
        created_at=row["created_at"],
        grounding=grounding,
        citations=citations,
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


@app.post("/api/workflows/interview")
def interview_spreadsheet_workflow(request: WorkflowRequest) -> dict[str, Any]:
    return workflow_interview_payload(request)


@app.post("/api/workflows/generate")
def generate_spreadsheet_workflow(request: WorkflowRequest) -> dict[str, Any]:
    interview = workflow_interview_payload(request)
    if not interview["ready_to_generate"]:
        return interview

    html = build_excel_workflow_html_app(
        request.description,
        [item.model_dump() for item in request.file_texts],
        request.sources,
    )
    if html is None:
        return {
            "status": "needs_interview",
            "ready_to_generate": False,
            "required_questions": [
                "Which concrete spreadsheet source files, matching rules, and manual copy/paste/edit steps should be automated?",
            ],
        }

    return {
        "status": "generated",
        "ready_to_generate": True,
        "filename": "spreadsheet-workflow-automator.html",
        "content_type": "text/html",
        "html": html,
    }


@app.get("/api/me", response_model=CurrentUserOut)
def get_current_user(principal: Principal = Depends(current_principal)):
    return current_user_out(principal)


@app.get("/api/settings", response_model=SettingsOut)
def get_app_settings(principal: Principal = Depends(current_principal)):
    return settings_for_principal(principal)


@app.patch("/api/settings", response_model=SettingsOut)
def patch_settings(patch: SettingsPatch, principal: Principal = Depends(settings_admin)):
    apply_settings_patch(patch, principal)
    return settings_for_principal(principal)


@app.get("/api/admin/settings", response_model=SettingsOut)
def get_admin_settings(principal: Principal = Depends(settings_admin)):
    return settings_for_principal(principal)


@app.patch("/api/admin/settings", response_model=SettingsOut)
def patch_admin_settings(patch: SettingsPatch, principal: Principal = Depends(settings_admin)):
    apply_settings_patch(patch, principal)
    return settings_for_principal(principal)


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
    return settings_for_principal(principal)


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
async def verify_openrouter_settings(principal: Principal = Depends(settings_admin)):
    await verify_openrouter_provider()
    return settings_for_principal(principal)


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


@app.post("/api/integrations/slack/events")
async def slack_events(request: Request, background: BackgroundTasks):
    body = await request.body()
    ok, reason = verify_slack_signature(
        body=body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
    )
    if not ok:
        reject_bot_webhook("slack", reason)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        reject_bot_webhook("slack", "Slack payload was not valid JSON.", status_code=400)
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    attachments = slack_attachments(payload)
    return queue_integration_attachments(service="slack", attachments=attachments, background=background)


@app.post("/api/integrations/telegram/webhook")
async def telegram_webhook(request: Request, background: BackgroundTasks):
    ok, reason = verify_telegram_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token"))
    if not ok:
        reject_bot_webhook("telegram", reason)
    try:
        payload = await request.json()
    except Exception:
        reject_bot_webhook("telegram", "Telegram payload was not valid JSON.", status_code=400)
    attachments = telegram_attachments(payload if isinstance(payload, dict) else {})
    return queue_integration_attachments(service="telegram", attachments=attachments, background=background)


@app.get("/api/wiki/nodes", response_model=list[WikiNodeOut])
def list_wiki_nodes(
    scope: str | None = Query(default=None, pattern="^(organization|user)$"),
    type: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    return list_nodes(principal, scope=scope, node_type=type)


@app.post("/api/wiki/nodes", response_model=WikiNodeOut)
def create_wiki_node(payload: WikiNodeCreate, principal: Principal = Depends(current_principal)):
    return create_node(principal, payload.model_dump())


@app.get("/api/wiki/nodes/{node_id}", response_model=WikiNodeOut)
def get_wiki_node(node_id: str, principal: Principal = Depends(current_principal)):
    node = get_node(principal, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Wiki node not found")
    return node


@app.patch("/api/wiki/nodes/{node_id}", response_model=WikiNodeOut)
def update_wiki_node(node_id: str, payload: WikiNodePatch, principal: Principal = Depends(current_principal)):
    node = update_node(principal, node_id, payload.model_dump(exclude_none=True))
    if not node:
        raise HTTPException(status_code=404, detail="Wiki node not found")
    return node


@app.delete("/api/wiki/nodes/{node_id}")
def delete_wiki_node(node_id: str, principal: Principal = Depends(current_principal)):
    if not delete_node(principal, node_id):
        raise HTTPException(status_code=404, detail="Wiki node not found")
    return {"ok": True}


@app.get("/api/wiki/edges", response_model=list[WikiEdgeOut])
def list_wiki_edges(principal: Principal = Depends(current_principal)):
    return list_edges(principal)


@app.post("/api/wiki/edges", response_model=WikiEdgeOut)
def create_wiki_edge(payload: WikiEdgeCreate, principal: Principal = Depends(current_principal)):
    edge = create_edge(principal, payload.model_dump())
    if not edge:
        raise HTTPException(status_code=404, detail="Wiki edge endpoint node not found")
    return edge


@app.get("/api/wiki/edges/{edge_id}", response_model=WikiEdgeOut)
def get_wiki_edge(edge_id: str, principal: Principal = Depends(current_principal)):
    edge = get_edge(principal, edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Wiki edge not found")
    return edge


@app.patch("/api/wiki/edges/{edge_id}", response_model=WikiEdgeOut)
def update_wiki_edge(edge_id: str, payload: WikiEdgePatch, principal: Principal = Depends(current_principal)):
    edge = update_edge(principal, edge_id, payload.model_dump(exclude_none=True))
    if not edge:
        raise HTTPException(status_code=404, detail="Wiki edge not found")
    return edge


@app.delete("/api/wiki/edges/{edge_id}")
def delete_wiki_edge(edge_id: str, principal: Principal = Depends(current_principal)):
    if not delete_edge(principal, edge_id):
        raise HTTPException(status_code=404, detail="Wiki edge not found")
    return {"ok": True}


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
        if any(output in {"chart", "table", "file_draft", "summary_panel", "decision_cards"} for output in preflight["execution_plan"].get("requested_outputs", [])):
            background.add_task(execute_agent_run, run.id)
            return get_agent_run(run.id) or run
        _mark_provider_setup_failure(run.id, provider)
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
        _mark_provider_setup_failure(run_id, provider)
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
        _mark_provider_setup_failure(next_run.id, provider)
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
        _mark_provider_setup_failure(run_id, provider)
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
    }


@app.get("/api/sessions/{session_id}/runs/{run_id}/questions/current", response_model=AgentRunQuestionOut | None)
def get_current_run_question(session_id: str, run_id: str, principal: Principal = Depends(current_principal)):
    ensure_session(session_id, principal)
    run = get_agent_run(run_id)
    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return get_current_question(run_id)


def _ready_file_ids_for_session(session_id: str, organization_id: str, file_ids: list[str]) -> list[str]:
    if not file_ids:
        return []
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT f.id FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            WHERE sf.session_id = ? AND f.organization_id = ? AND f.status = 'ready'
              AND f.id IN ({','.join('?' for _ in file_ids)})
            """,
            (session_id, organization_id, *file_ids),
        ).fetchall()
    return [row["id"] for row in rows]


def _run_question_state(run_id: str, question_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, blocking, status FROM agent_run_questions WHERE run_id = ? AND id = ?",
            (run_id, question_id),
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "blocking": bool(row["blocking"]), "status": row["status"]}


def _artifact_choice_option_map(question: AgentRunQuestionOut) -> dict[str, dict]:
    raw_options = question.card.options if isinstance(question.card.options, list) else []
    options: dict[str, dict] = {}
    for item in raw_options:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or "").strip()
        if option_id:
            options[option_id] = item
    if options:
        return options
    for item in question.options:
        option_id = str(item.id or "").strip()
        if option_id:
            options[option_id] = item.model_dump()
    return options


def _server_artifact_choice_answer(question: AgentRunQuestionOut, payload: AnswerRunQuestionRequest) -> tuple[dict, list[dict]]:
    raw_selected = payload.answer.get("selected_options") if isinstance(payload.answer, dict) else None
    if not isinstance(raw_selected, list) or not raw_selected:
        raise HTTPException(status_code=400, detail="Select at least one artifact option.")
    selected_ids: list[str] = []
    for item in raw_selected:
        option_id = str(item).strip()
        if option_id and option_id not in selected_ids:
            selected_ids.append(option_id)
    if not selected_ids:
        raise HTTPException(status_code=400, detail="Select at least one artifact option.")
    option_map = _artifact_choice_option_map(question)
    selected_options: list[dict] = []
    for option_id in selected_ids:
        option = option_map.get(option_id)
        if not option:
            raise HTTPException(status_code=400, detail=f"Invalid artifact option: {option_id}")
        selected_options.append(
            {
                "id": str(option.get("id") or option_id),
                "label": str(option.get("label") or option_id),
                "description": str(option.get("description") or ""),
                "artifact_kind": str(option.get("artifact_kind") or "summary_panel"),
                "chart_type": str(option.get("chart_type") or ""),
                "produce_payload": option.get("produce_payload") if isinstance(option.get("produce_payload"), dict) else {},
            }
        )
    return {"selected_options": selected_ids}, selected_options


def _follow_up_context(
    session_id: str,
    run: AgentRunOut,
    question_id: str,
    answer: dict[str, Any],
    attached_file_ids: list[str],
    selected_artifact_options: list[dict] | None = None,
) -> dict[str, Any]:
    question = next((item for item in run.follow_up_questions if item.id == question_id), None)
    parent_artifact: dict[str, Any] = {}
    if question and question.parent_artifact_id:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ? AND session_id = ?",
                (question.parent_artifact_id, session_id),
            ).fetchone()
        if row:
            spec = json_loads(row["spec_json"], {})
            parent_artifact = {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "spec": spec,
                "insight_narrative": spec.get("insight_narrative") if isinstance(spec, dict) else {},
            }
    return {
        "parent_run_id": run.id,
        "trigger_question_id": question_id,
        "parent_message_id": question.parent_message_id if question else None,
        "parent_artifact_id": question.parent_artifact_id if question else None,
        "question": question.question if question else "",
        "answer": sanitize_metadata(answer),
        "attached_file_ids": attached_file_ids,
        "source_filter_mode": "selected_files" if question and question.card.allow_file_reference else "all_session_sources",
        "selected_artifact_options": sanitize_metadata(selected_artifact_options or []),
        "parent_artifact": sanitize_metadata(parent_artifact),
    }


def _child_question_from_follow_up(context: dict[str, Any]) -> str:
    answer = context.get("answer") if isinstance(context.get("answer"), dict) else {}
    selected_artifact_options = context.get("selected_artifact_options") if isinstance(context.get("selected_artifact_options"), list) else []
    if selected_artifact_options:
        pieces = [
            "Produce selected artifacts from the current session sources.",
            f"Question: {context.get('question')}",
            "Selected artifacts:",
        ]
        for option in selected_artifact_options:
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or option.get("id") or "Artifact").strip()
            artifact_kind = str(option.get("artifact_kind") or "artifact").replace("_", " ")
            chart_type = str(option.get("chart_type") or "").strip()
            kind_label = f"{chart_type} chart" if chart_type else artifact_kind
            description = str(option.get("description") or "").strip()
            line = f"- {label} ({kind_label})"
            if description:
                line = f"{line}: {description}"
            pieces.append(line)
        return "\n".join(pieces)
    selected = str(answer.get("selected_option") or "").strip()
    free_text = str(answer.get("free_text") or "").strip()
    pieces = [
        "Follow up on the completed chart insight.",
        f"Question: {context.get('question')}",
    ]
    if selected:
        pieces.append(f"Selected option: {selected}")
    if free_text:
        pieces.append(f"Additional context: {free_text}")
    attached = context.get("attached_file_ids") if isinstance(context.get("attached_file_ids"), list) else []
    if attached:
        pieces.append(f"Use only these selected reference file ids where possible: {', '.join(str(item) for item in attached)}")
    return "\n".join(pieces)


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
    question_state = _run_question_state(run_id, question_id)
    if not question_state:
        raise HTTPException(status_code=404, detail="Run question not found")
    if question_state["status"] != "pending":
        raise HTTPException(status_code=409, detail="Run question has already been answered.")
    pending_follow_up = next((item for item in run.follow_up_questions if item.id == question_id), None)
    selected_artifact_options: list[dict] = []
    if pending_follow_up and pending_follow_up.kind == "artifact_choice" and pending_follow_up.card.allow_multi_select:
        answer, selected_artifact_options = _server_artifact_choice_answer(pending_follow_up, payload)
    else:
        answer = dict(payload.answer)
        if payload.selected_option is not None:
            answer["selected_option"] = payload.selected_option
        if payload.free_text is not None:
            answer["free_text"] = payload.free_text
    if pending_follow_up and pending_follow_up.card.allow_file_reference and not payload.attached_file_ids:
        raise HTTPException(status_code=400, detail="This follow-up needs at least one ready reference file.")
    if payload.attached_file_ids:
        answer["attached_file_ids"] = payload.attached_file_ids
        ready_ids = _ready_file_ids_for_session(session_id, principal.organization_id, payload.attached_file_ids)
        if set(ready_ids) != set(payload.attached_file_ids):
            raise HTTPException(status_code=400, detail="One or more attached files are not ready in this session.")
    answered = answer_run_question(run_id, question_id, answer)
    if not answered:
        raise HTTPException(status_code=404, detail="Run question not found")
    if not answered.blocking:
        context = _follow_up_context(
            session_id,
            run,
            question_id,
            answer,
            payload.attached_file_ids,
            selected_artifact_options=selected_artifact_options,
        )
        child = create_agent_run(
            session_id,
            _child_question_from_follow_up(context),
            kind="ask",
            parent_run_id=run_id,
            trigger_question_id=question_id,
        )
        upsert_workspace_item(child.id, path="/follow-up/context.json", kind="follow_up", content=context)
        preflight = build_preflight(session_id, child.question)
        update_run_preflight(child.id, **preflight)
        provider = await ensure_provider_ready()
        if provider.get("status") != "verified":
            if selected_artifact_options:
                background.add_task(execute_agent_run, child.id)
                return get_agent_run(child.id) or child
            _mark_provider_setup_failure(child.id, provider)
            return get_agent_run(child.id) or child
        background.add_task(execute_agent_run, child.id)
        return get_agent_run(child.id) or child
    provider = await ensure_provider_ready()
    if provider.get("status") != "verified":
        _mark_provider_setup_failure(run_id, provider)
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
    format: str = Query(default="md", pattern="^(md|json|notion|csv|pdf|od)$"),
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
    row_dict = dict(row)
    row_dict["source_chunk_ids"] = json_loads(row["source_chunk_ids"], [])
    if format == "od":
        try:
            content, filename = open_design_bundle_for_artifact(row_dict, spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "csv":
        table = table_payload_for_artifact(spec)
        if not table:
            raise HTTPException(status_code=400, detail="Artifact does not contain exportable table data")
        filename = slugify_filename(str(row["title"] or "artifact"), ".csv")
        return Response(
            content=table["csv"],
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "json":
        filename = str(spec.get("filename") or f"{row['title'] or 'artifact'}.json")
        if not filename.endswith(".json"):
            filename = f"{filename}.json"
        return Response(
            content=json.dumps(spec, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "notion":
        filename = str(spec.get("filename") or f"{row['title'] or 'artifact'}-notion.json")
        if not filename.endswith(".json"):
            filename = f"{filename}.json"
        return Response(
            content=json.dumps(notion_import_bundle(row_dict, spec), ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "pdf":
        content, filename = pdf_for_artifact(row_dict, spec)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    content, filename = markdown_for_artifact(row_dict, spec)
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
