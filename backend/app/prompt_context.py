from __future__ import annotations

import json
from typing import Any

from .database import connect
from .settings_store import get_setting, set_setting
from .utils import json_dumps, json_loads, now


PROMPT_PACK_VERSION = "2026-04-context-artifact-ux"

DEFAULT_CONTEXT_PROFILE: dict[str, str] = {
    "artifact_policy": "chart+draft",
    "citation_display": "minimized",
    "drafting_policy": "model_polished_evidence",
    "title_style": "localized_subject_first",
}

PRODUCT_POLICY = {
    "identity": "FileChat turns attached source files into grounded answers and artifacts.",
    "output_rules": [
        "Prefer useful deliverables over schema-shaped filler.",
        "Use deterministic tools for parsing, counting, validation, citations, and rendering.",
        "Use the model for planning, interpretation, wording, and synthesis.",
        "Do not show raw table previews in the main transcript unless explicitly requested.",
        "Use subject-first localized titles and filenames for created files.",
    ],
}

VERSION_NOTES = {
    "prompt_pack_version": PROMPT_PACK_VERSION,
    "changes": [
        "Artifacts are split into primary and supporting display modes.",
        "Survey drafts are written from an evidence packet instead of raw column metadata.",
        "Message citations are minimized in the transcript and expanded in the side panel.",
    ],
}


def context_profile() -> dict[str, str]:
    raw = get_setting("context_profile_json", "{}") or "{}"
    loaded = json_loads(raw, {})
    profile = dict(DEFAULT_CONTEXT_PROFILE)
    if isinstance(loaded, dict):
        for key, value in loaded.items():
            if key in profile and isinstance(value, str) and value:
                profile[key] = value
    return profile


def patch_context_profile(patch: dict[str, Any]) -> dict[str, str]:
    profile = context_profile()
    for key in DEFAULT_CONTEXT_PROFILE:
        value = patch.get(key)
        if isinstance(value, str) and value:
            profile[key] = value
    set_setting("context_profile_json", json_dumps(profile))
    return profile


def session_context(session_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT title, context_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return {}
    context = json_loads(row["context_json"], {})
    if not isinstance(context, dict):
        context = {}
    context.setdefault("title", row["title"])
    return context


def refresh_session_context(session_id: str) -> dict[str, Any]:
    with connect() as conn:
        session = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            return {}
        files = conn.execute(
            """
            SELECT f.name, f.type, f.status, f.chunk_count
            FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ?
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
        artifacts = conn.execute(
            """
            SELECT a.kind, a.title, a.display_mode
            FROM artifacts a
            JOIN messages m ON m.id = a.message_id
            WHERE m.session_id = ?
            ORDER BY a.created_at DESC
            LIMIT 12
            """,
            (session_id,),
        ).fetchall()
        runs = conn.execute(
            """
            SELECT question, task_contract_json
            FROM agent_runs
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 3
            """,
            (session_id,),
        ).fetchall()

    file_names = [row["name"] for row in files]
    latest_contract = {}
    for run in runs:
        loaded = json_loads(run["task_contract_json"], {})
        if isinstance(loaded, dict) and loaded:
            latest_contract = loaded
            break
    context = {
        "title": session["title"],
        "topic": _topic_from_files(file_names) or session["title"],
        "file_count": len(files),
        "ready_file_count": sum(1 for row in files if row["status"] == "ready"),
        "recent_files": [{"name": row["name"], "type": row["type"], "status": row["status"], "chunks": row["chunk_count"]} for row in files[-5:]],
        "recent_artifacts": [dict(row) for row in artifacts],
        "latest_task_contract": latest_contract,
        "updated_at": now(),
    }
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET context_json = ?, updated_at = ? WHERE id = ?",
            (json_dumps(context), now(), session_id),
        )
    return context


def file_intelligence(session_id: str) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.type, f.status, f.size, f.chunk_count, f.error
            FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            JOIN sessions s ON s.id = sf.session_id AND s.organization_id = f.organization_id
            WHERE sf.session_id = ?
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
    return {
        "files": [dict(row) for row in rows],
        "ready_count": sum(1 for row in rows if row["status"] == "ready"),
        "table_like_count": sum(1 for row in rows if str(row["type"]).upper() in {"CSV", "TSV", "TXT", "XLS", "XLSX"}),
    }


def build_prompt_context(
    *,
    session_id: str,
    question: str,
    task_contract: dict[str, Any] | None = None,
    evidence_packet: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    session = session_context(session_id) or refresh_session_context(session_id)
    return {
        "prompt_pack_version": PROMPT_PACK_VERSION,
        "product_policy": PRODUCT_POLICY,
        "version_notes": VERSION_NOTES,
        "user_preferences": context_profile(),
        "session_brief": session,
        "file_intelligence": file_intelligence(session_id),
        "task_contract": task_contract or {},
        "evidence_packet": evidence_packet or {},
        "conversation_tail": (history or [])[-6:],
        "current_request": question,
    }


def prompt_pack(name: str, context: dict[str, Any], *, inputs: dict[str, Any] | None = None) -> tuple[str, str]:
    compact_context = json.dumps(context, ensure_ascii=False, indent=2)
    payload = json.dumps(inputs or {}, ensure_ascii=False, indent=2)
    if name == "planner":
        system = (
            "You are FileChat's planning controller. Build a task contract for grounded file work. "
            "Use the layered context to respect user preferences, session memory, and current file state. "
            "Return strict JSON only and do not create final content."
        )
        user = (
            f"Layered prompt context:\n{compact_context}\n\n"
            f"Planning inputs:\n{payload}\n\n"
            "Return JSON with keys: intent ('ask' or 'create'), deliverable, language, required_outputs, "
            "analysis_focus, success_criteria, needs_user_question, user_question, question_options, default_option. "
            "question_options is an array of {id,label,description}. required_outputs may include answer, chart, table, summary_panel, file_draft."
        )
        return system, user
    if name == "draft_writer":
        system = (
            "You are FileChat's evidence-grounded draft writer. Write polished Markdown from the evidence packet only. "
            "Use a subject-first localized title, synthesize implications, avoid raw column-profile repetition, and keep citations grounded. "
            "Return strict JSON only."
        )
        user = (
            f"Layered prompt context:\n{compact_context}\n\n"
            f"Draft inputs:\n{payload}\n\n"
            "Return JSON with keys: answer, cited_source_ids, draft. draft must contain title, filename, caption, content. "
            "The content must be Markdown, must not start with a generic '# 분석 자료', and must include synthesized insights and next actions."
        )
        return system, user
    system = (
        "You are FileChat, a strict grounded reading assistant. Use layered context only to understand the user, session, and output preferences. "
        "Answer facts only from supplied sources. Return valid JSON."
    )
    user = f"Layered prompt context:\n{compact_context}\n\nInputs:\n{payload}"
    return system, user


def _topic_from_files(file_names: list[str]) -> str:
    if not file_names:
        return ""
    name = file_names[0]
    for marker in ("(Responses)", "- Form Responses", ".csv", ".tsv", ".txt"):
        name = name.replace(marker, " ")
    return " ".join(name.split()).strip(" -_")
