from __future__ import annotations

from typing import Any

from .database import connect
from .models import AgentActionKind, AgentActionStatus, AgentRunActionOut, AgentRunEventOut, AgentRunOut, AgentRunQuestionOut, AgentRunWorkspaceItemOut
from .security import sanitize_metadata
from .utils import json_dumps, json_loads, new_id, now


def _redacted(value: Any) -> Any:
    return sanitize_metadata(value)


def _action_out(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "ordinal": row["ordinal"],
        "kind": row["kind"],
        "label": row["label"],
        "status": row["status"],
        "input_summary": row["input_summary"],
        "output_summary": row["output_summary"],
        "error_summary": row["error_summary"],
        "input_json": json_loads(row["input_json"], {}),
        "output_json": json_loads(row["output_json"], {}),
        "validation_json": json_loads(row["validation_json"], {}),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _question_out(row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "action_kind": row["action_kind"] if "action_kind" in keys else row["phase"],
        "kind": row["kind"],
        "question": row["question"],
        "options": json_loads(row["options_json"], []),
        "default_option": row["default_option"],
        "blocking": bool(row["blocking"]) if "blocking" in keys else True,
        "phase": row["phase"] if "phase" in keys else (row["action_kind"] if "action_kind" in keys else ""),
        "card": json_loads(row["card_json"], {}) if "card_json" in keys else {},
        "parent_message_id": row["parent_message_id"] if "parent_message_id" in keys else None,
        "parent_artifact_id": row["parent_artifact_id"] if "parent_artifact_id" in keys else None,
        "answer": json_loads(row["answer_json"], None),
        "answer_file_ids": json_loads(row["answer_file_ids_json"], []) if "answer_file_ids_json" in keys else [],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "answered_at": row["answered_at"],
    }


def _event_out(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "seq": row["seq"],
        "type": row["type"],
        "summary": row["summary"],
        "detail": json_loads(row["detail_json"], {}),
        "created_at": row["created_at"],
    }


def _workspace_out(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "path": row["path"],
        "kind": row["kind"],
        "content": json_loads(row["content_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _actions_for(conn, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_run_actions WHERE run_id = ? ORDER BY ordinal",
        (run_id,),
    ).fetchall()
    return [_action_out(row) for row in rows]


def _current_question_for(conn, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM agent_run_questions
        WHERE run_id = ? AND status = 'pending' AND blocking = 1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    return _question_out(row) if row else None


def _follow_up_questions_for(conn, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM agent_run_questions
        WHERE run_id = ? AND status = 'pending' AND blocking = 0
        ORDER BY created_at
        """,
        (run_id,),
    ).fetchall()
    return [_question_out(row) for row in rows]


def run_out(row) -> AgentRunOut:
    with connect() as conn:
        actions = _actions_for(conn, row["id"])
        current_question = _current_question_for(conn, row["id"])
        follow_up_questions = _follow_up_questions_for(conn, row["id"])
    return AgentRunOut(
        id=row["id"],
        session_id=row["session_id"],
        user_message_id=row["user_message_id"],
        assistant_message_id=row["assistant_message_id"],
        parent_run_id=row["parent_run_id"] if "parent_run_id" in row.keys() else None,
        trigger_question_id=row["trigger_question_id"] if "trigger_question_id" in row.keys() else None,
        kind=row["kind"],
        status=row["status"],
        question=row["question"],
        error=row["error"],
        execution_plan=json_loads(row["execution_plan_json"], {}),
        task_contract=json_loads(row["task_contract_json"], {}),
        provider_status=json_loads(row["provider_status_json"], {}),
        agent_actions=json_loads(row["agent_actions_json"], []),
        review_scores=json_loads(row["review_scores_json"], {}),
        revision_required=bool(row["revision_required"]),
        model_assignments=json_loads(row["model_assignments_json"], {}),
        tool_calls=json_loads(row["tool_calls_json"], []),
        artifact_versions=json_loads(row["artifact_versions_json"], []),
        repair_attempts=json_loads(row["repair_attempts_json"], []),
        quality_warnings=json_loads(row["quality_warnings_json"], []),
        current_question=current_question,
        follow_up_questions=follow_up_questions,
        actions=actions,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def create_agent_run(
    session_id: str,
    question: str,
    *,
    kind: str = "ask",
    parent_run_id: str | None = None,
    trigger_question_id: str | None = None,
) -> AgentRunOut:
    run_id = new_id("run")
    created = now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs
            (id, session_id, parent_run_id, trigger_question_id, kind, status, question, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, session_id, parent_run_id, trigger_question_id, kind, "queued", question, created, created),
        )
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return run_out(row)


def get_agent_run(run_id: str) -> AgentRunOut | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return run_out(row) if row else None


def list_agent_runs(session_id: str, *, limit: int = 20) -> list[AgentRunOut]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM agent_runs
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [run_out(row) for row in rows]


def start_run(run_id: str) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ?",
            ("running", stamp, run_id),
        )


def mark_run_awaiting_approval(run_id: str) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ?",
            ("awaiting_approval", stamp, run_id),
        )


def mark_run_awaiting_user_input(run_id: str) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ?",
            ("awaiting_user_input", stamp, run_id),
        )


def mark_run_needs_setup(run_id: str, error: str | None = None) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            ("needs_setup", error, stamp, run_id),
        )


def mark_run_needs_revision(run_id: str, error: str | None = None) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, error = ?, revision_required = ?, updated_at = ? WHERE id = ?",
            ("needs_revision", error, 1, stamp, run_id),
        )


def mark_run_queued(run_id: str) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ?",
            ("queued", stamp, run_id),
        )


def update_run_kind(run_id: str, kind: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET kind = ?, updated_at = ? WHERE id = ?",
            (kind, now(), run_id),
        )


def update_run_contract(
    run_id: str,
    *,
    task_contract: dict[str, Any] | None = None,
    provider_status: dict[str, Any] | None = None,
    review_scores: dict[str, Any] | None = None,
    revision_required: bool | None = None,
) -> None:
    updates: list[str] = []
    values: list[Any] = []
    if task_contract is not None:
        updates.append("task_contract_json = ?")
        values.append(json_dumps(task_contract))
    if provider_status is not None:
        updates.append("provider_status_json = ?")
        values.append(json_dumps(provider_status))
    if review_scores is not None:
        updates.append("review_scores_json = ?")
        values.append(json_dumps(review_scores))
    if revision_required is not None:
        updates.append("revision_required = ?")
        values.append(1 if revision_required else 0)
    if not updates:
        return
    updates.append("updated_at = ?")
    values.append(now())
    values.append(run_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE agent_runs SET {', '.join(updates)} WHERE id = ?",
            tuple(values),
        )


def update_run_prompt_context(run_id: str, prompt_context: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET prompt_context_json = ?, updated_at = ? WHERE id = ?",
            (json_dumps(_redacted(prompt_context)), now(), run_id),
        )


def record_agent_action(run_id: str, item: dict[str, Any]) -> None:
    _append_run_list(run_id, "agent_actions_json", _redacted(item))


def update_run_preflight(
    run_id: str,
    *,
    execution_plan: dict[str, Any],
    model_assignments: dict[str, Any],
    quality_warnings: list[str] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE agent_runs
            SET execution_plan_json = ?, model_assignments_json = ?, quality_warnings_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json_dumps(execution_plan),
                json_dumps(model_assignments),
                json_dumps(quality_warnings or []),
                now(),
                run_id,
            ),
        )


def _append_run_list(run_id: str, column: str, item: dict[str, Any] | str) -> None:
    with connect() as conn:
        row = conn.execute(f"SELECT {column} FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        values = json_loads(row[column], []) if row else []
        if not isinstance(values, list):
            values = []
        values.append(item)
        conn.execute(
            f"UPDATE agent_runs SET {column} = ?, updated_at = ? WHERE id = ?",
            (json_dumps(values), now(), run_id),
        )


def record_tool_call(run_id: str, item: dict[str, Any]) -> None:
    _append_run_list(run_id, "tool_calls_json", _redacted(item))


def record_artifact_version(run_id: str, item: dict[str, Any]) -> None:
    _append_run_list(run_id, "artifact_versions_json", _redacted(item))


def record_repair_attempt(run_id: str, item: dict[str, Any]) -> None:
    _append_run_list(run_id, "repair_attempts_json", _redacted(item))


def add_quality_warning(run_id: str, warning: str) -> None:
    _append_run_list(run_id, "quality_warnings_json", warning)


def list_run_questions(run_id: str) -> list[AgentRunQuestionOut]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_run_questions WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
    return [AgentRunQuestionOut(**_question_out(row)) for row in rows]


def get_current_question(run_id: str) -> AgentRunQuestionOut | None:
    with connect() as conn:
        question = _current_question_for(conn, run_id)
    return AgentRunQuestionOut(**question) if question else None


def create_run_question(
    run_id: str,
    *,
    action_kind: AgentActionKind,
    kind: str,
    question: str,
    options: list[dict[str, Any]] | None = None,
    default_option: str | None = None,
    blocking: bool = True,
    phase: str | None = None,
    card: dict[str, Any] | None = None,
    parent_message_id: str | None = None,
    parent_artifact_id: str | None = None,
) -> AgentRunQuestionOut:
    stamp = now()
    with connect() as conn:
        if blocking:
            existing = conn.execute(
                """
                SELECT * FROM agent_run_questions
                WHERE run_id = ? AND status = 'pending' AND blocking = 1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if existing:
                return AgentRunQuestionOut(**_question_out(existing))
        question_id = new_id("ques")
        card_payload = card or {
            "title": "Planning needs a choice" if blocking else "Question to answer next",
            "prompt": question,
            "group": "business",
            "options": options or [],
            "allow_free_text": kind == "clarification" or not blocking,
            "allow_file_reference": False,
        }
        conn.execute(
            """
            INSERT INTO agent_run_questions
            (id, run_id, action_kind, blocking, phase, kind, question, options_json, default_option,
             card_json, answer_file_ids_json, parent_message_id, parent_artifact_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                run_id,
                action_kind,
                1 if blocking else 0,
                phase or action_kind,
                kind,
                question,
                json_dumps(_redacted(options or [])),
                default_option,
                json_dumps(_redacted(card_payload)),
                json_dumps([]),
                parent_message_id,
                parent_artifact_id,
                "pending",
                stamp,
                stamp,
            ),
        )
        row = conn.execute("SELECT * FROM agent_run_questions WHERE id = ?", (question_id,)).fetchone()
    record_run_event(
        run_id,
        type="question_created",
        summary=question,
        detail={
            "kind": kind,
            "action_kind": action_kind,
            "blocking": blocking,
            "phase": phase or action_kind,
            "default_option": default_option,
            "options": options or [],
        },
    )
    return AgentRunQuestionOut(**_question_out(row))


def answer_run_question(run_id: str, question_id: str, answer: dict[str, Any]) -> AgentRunQuestionOut | None:
    stamp = now()
    answer_file_ids = answer.get("attached_file_ids") if isinstance(answer.get("attached_file_ids"), list) else []
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_run_questions WHERE id = ? AND run_id = ?",
            (question_id, run_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] == "pending":
            conn.execute(
                """
                UPDATE agent_run_questions
                SET status = ?, answer_json = ?, answer_file_ids_json = ?, updated_at = ?, answered_at = ?
                WHERE id = ?
                """,
                ("answered", json_dumps(answer), json_dumps([str(file_id) for file_id in answer_file_ids]), stamp, stamp, question_id),
            )
            if bool(row["blocking"]):
                conn.execute("UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ?", ("queued", stamp, run_id))
        updated = conn.execute("SELECT * FROM agent_run_questions WHERE id = ?", (question_id,)).fetchone()
    record_run_event(
        run_id,
        type="question_answered",
        summary=f"Answered planning question: {row['kind']}",
        detail={"question_id": question_id, "answer": _redacted(answer)},
    )
    return AgentRunQuestionOut(**_question_out(updated))


def answered_question_value(run_id: str, kind: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM agent_run_questions
            WHERE run_id = ? AND kind = ? AND status = 'answered'
            ORDER BY answered_at DESC
            LIMIT 1
            """,
            (run_id, kind),
        ).fetchone()
    if not row:
        return None
    answer = json_loads(row["answer_json"], {})
    return answer if isinstance(answer, dict) else {}


def record_run_event(run_id: str, *, type: str, summary: str, detail: dict[str, Any] | None = None) -> AgentRunEventOut:
    stamp = now()
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 next_seq FROM agent_run_events WHERE run_id = ?", (run_id,)).fetchone()
        seq = int(row["next_seq"])
        event_id = new_id("evt")
        conn.execute(
            """
            INSERT INTO agent_run_events (id, run_id, seq, type, summary, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, run_id, seq, type, summary, json_dumps(_redacted(detail or {})), stamp),
        )
        saved = conn.execute("SELECT * FROM agent_run_events WHERE id = ?", (event_id,)).fetchone()
    return AgentRunEventOut(**_event_out(saved))


def list_run_events(run_id: str, *, after_seq: int = 0) -> list[AgentRunEventOut]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM agent_run_events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq
            """,
            (run_id, after_seq),
        ).fetchall()
    return [AgentRunEventOut(**_event_out(row)) for row in rows]


def upsert_workspace_item(run_id: str, *, path: str, kind: str, content: dict[str, Any]) -> AgentRunWorkspaceItemOut:
    stamp = now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM agent_run_workspace WHERE run_id = ? AND path = ?",
            (run_id, path),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE agent_run_workspace
                SET kind = ?, content_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (kind, json_dumps(_redacted(content)), stamp, existing["id"]),
            )
            item_id = existing["id"]
        else:
            item_id = new_id("ws")
            conn.execute(
                """
                INSERT INTO agent_run_workspace
                (id, run_id, path, kind, content_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, run_id, path, kind, json_dumps(_redacted(content)), stamp, stamp),
            )
        row = conn.execute("SELECT * FROM agent_run_workspace WHERE id = ?", (item_id,)).fetchone()
    record_run_event(run_id, type="workspace_updated", summary=f"Updated {path}", detail={"path": path, "kind": kind})
    return AgentRunWorkspaceItemOut(**_workspace_out(row))


def list_workspace_items(run_id: str) -> list[AgentRunWorkspaceItemOut]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_run_workspace WHERE run_id = ? ORDER BY path",
            (run_id,),
        ).fetchall()
    return [AgentRunWorkspaceItemOut(**_workspace_out(row)) for row in rows]


def attach_run_messages(
    run_id: str,
    *,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> None:
    updates = []
    values: list[Any] = []
    if user_message_id is not None:
        updates.append("user_message_id = ?")
        values.append(user_message_id)
    if assistant_message_id is not None:
        updates.append("assistant_message_id = ?")
        values.append(assistant_message_id)
    if not updates:
        return
    updates.append("updated_at = ?")
    values.append(now())
    values.append(run_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE agent_runs SET {', '.join(updates)} WHERE id = ?",
            tuple(values),
        )


def set_action(
    run_id: str,
    kind: AgentActionKind,
    status: AgentActionStatus,
    *,
    label: str | None = None,
    input_summary: str = "",
    output_summary: str = "",
    error_summary: str | None = None,
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
    validation_json: dict[str, Any] | None = None,
) -> AgentRunActionOut:
    stamp = now()
    default_labels = {
        "verify_provider": "Verify provider",
        "classify_request": "Classify request",
        "plan_task": "Plan task",
        "ask_user": "Ask user",
        "load_sources": "Load sources",
        "rank_sources": "Rank sources",
        "profile_table": "Profile table",
        "build_evidence": "Build evidence",
        "reason": "Reason",
        "write": "Write",
        "validate": "Validate",
        "repair": "Repair",
        "persist_response": "Persist response",
        "publish_notion": "Publish to Notion",
    }
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM agent_run_actions
            WHERE run_id = ? AND kind = ? AND completed_at IS NULL
            ORDER BY ordinal DESC
            LIMIT 1
            """,
            (run_id, kind),
        ).fetchone()
        completed_at = stamp if status in {"completed", "failed"} else None
        if existing:
            action_id = existing["id"]
            conn.execute(
                """
                UPDATE agent_run_actions
                SET status = ?, label = ?, input_summary = ?, output_summary = ?, error_summary = ?,
                    input_json = ?, output_json = ?, validation_json = ?, updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (
                    status,
                    label or existing["label"] or default_labels[kind],
                    input_summary or existing["input_summary"],
                    output_summary or existing["output_summary"],
                    error_summary,
                    json_dumps(_redacted(input_json if input_json is not None else json_loads(existing["input_json"], {}))),
                    json_dumps(_redacted(output_json if output_json is not None else json_loads(existing["output_json"], {}))),
                    json_dumps(_redacted(validation_json if validation_json is not None else json_loads(existing["validation_json"], {}))),
                    stamp,
                    completed_at,
                    action_id,
                ),
            )
        else:
            row = conn.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 next_ordinal FROM agent_run_actions WHERE run_id = ?", (run_id,)).fetchone()
            action_id = new_id("act")
            conn.execute(
                """
                INSERT INTO agent_run_actions
                (id, run_id, ordinal, kind, label, status, input_summary, output_summary, error_summary,
                 input_json, output_json, validation_json, started_at, completed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    run_id,
                    int(row["next_ordinal"]),
                    kind,
                    label or default_labels[kind],
                    status,
                    input_summary,
                    output_summary,
                    error_summary,
                    json_dumps(_redacted(input_json or {})),
                    json_dumps(_redacted(output_json or {})),
                    json_dumps(_redacted(validation_json or {})),
                    stamp,
                    completed_at,
                    stamp,
                    stamp,
                ),
            )
        conn.execute("UPDATE agent_runs SET updated_at = ? WHERE id = ?", (stamp, run_id))
        saved = conn.execute("SELECT * FROM agent_run_actions WHERE id = ?", (action_id,)).fetchone()
    return AgentRunActionOut(**_action_out(saved))


def complete_run(
    run_id: str,
    *,
    assistant_message_id: str | None = None,
    status: str = "completed",
) -> None:
    stamp = now()
    with connect() as conn:
        if assistant_message_id:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, assistant_message_id = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, assistant_message_id, stamp, stamp, run_id),
            )
        else:
            conn.execute(
                "UPDATE agent_runs SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (status, stamp, stamp, run_id),
            )


def fail_run(run_id: str, error: str) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            ("failed", error, stamp, stamp, run_id),
        )
