from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings


def db_path() -> Path:
    return get_settings().resolved_data_dir / "filechat.sqlite3"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY,
              hash TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              size INTEGER NOT NULL,
              path TEXT NOT NULL,
              artifact_path TEXT,
              status TEXT NOT NULL,
              progress REAL NOT NULL DEFAULT 0,
              page_count INTEGER NOT NULL DEFAULT 0,
              chunk_count INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_files (
              session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              attached_at TEXT NOT NULL,
              PRIMARY KEY (session_id, file_id)
            );

            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              ordinal INTEGER NOT NULL,
              content TEXT NOT NULL,
              location TEXT NOT NULL,
              token_count INTEGER NOT NULL,
              hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (file_id, ordinal)
            );

            CREATE TABLE IF NOT EXISTS embeddings (
              chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
              model TEXT NOT NULL,
              dimensions INTEGER NOT NULL,
              vector TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (chunk_id, model)
            );

            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              unavailable_file_ids TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS citations (
              id TEXT PRIMARY KEY,
              message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
              source_label TEXT NOT NULL,
              location TEXT NOT NULL,
              excerpt TEXT NOT NULL,
              score REAL NOT NULL,
              ordinal INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              caption TEXT NOT NULL DEFAULT '',
              display_mode TEXT NOT NULL DEFAULT 'primary',
              source_chunk_ids TEXT NOT NULL DEFAULT '[]',
              spec_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS artifacts_message_idx
              ON artifacts(message_id, created_at);

            CREATE TABLE IF NOT EXISTS agent_runs (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
              assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
              kind TEXT NOT NULL DEFAULT 'ask',
              status TEXT NOT NULL,
              question TEXT NOT NULL,
              error TEXT,
              execution_plan_json TEXT NOT NULL DEFAULT '{}',
              task_contract_json TEXT NOT NULL DEFAULT '{}',
              prompt_context_json TEXT NOT NULL DEFAULT '{}',
              provider_status_json TEXT NOT NULL DEFAULT '{}',
              agent_actions_json TEXT NOT NULL DEFAULT '[]',
              review_scores_json TEXT NOT NULL DEFAULT '{}',
              revision_required INTEGER NOT NULL DEFAULT 0,
              model_assignments_json TEXT NOT NULL DEFAULT '{}',
              tool_calls_json TEXT NOT NULL DEFAULT '[]',
              artifact_versions_json TEXT NOT NULL DEFAULT '[]',
              repair_attempts_json TEXT NOT NULL DEFAULT '[]',
              quality_warnings_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS agent_runs_session_idx
              ON agent_runs(session_id, created_at);

            CREATE TABLE IF NOT EXISTS agent_run_steps (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
              phase TEXT NOT NULL,
              ordinal INTEGER NOT NULL,
              status TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '',
              detail_json TEXT NOT NULL DEFAULT '{}',
              error TEXT,
              started_at TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (run_id, phase)
            );

            CREATE INDEX IF NOT EXISTS agent_run_steps_run_idx
              ON agent_run_steps(run_id, ordinal);

            CREATE TABLE IF NOT EXISTS agent_run_questions (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
              phase TEXT NOT NULL,
              kind TEXT NOT NULL,
              question TEXT NOT NULL,
              options_json TEXT NOT NULL DEFAULT '[]',
              default_option TEXT,
              answer_json TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              answered_at TEXT
            );

            CREATE INDEX IF NOT EXISTS agent_run_questions_run_idx
              ON agent_run_questions(run_id, status, created_at);

            CREATE TABLE IF NOT EXISTS agent_run_events (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
              seq INTEGER NOT NULL,
              type TEXT NOT NULL,
              summary TEXT NOT NULL,
              detail_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE (run_id, seq)
            );

            CREATE INDEX IF NOT EXISTS agent_run_events_run_idx
              ON agent_run_events(run_id, seq);

            CREATE TABLE IF NOT EXISTS agent_run_workspace (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              kind TEXT NOT NULL,
              content_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (run_id, path)
            );

            CREATE INDEX IF NOT EXISTS agent_run_workspace_run_idx
              ON agent_run_workspace(run_id, path);

            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              status TEXT NOT NULL,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_events (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
              file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              model TEXT NOT NULL,
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              prompt_cost REAL NOT NULL DEFAULT 0,
              completion_cost REAL NOT NULL DEFAULT 0,
              total_cost REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS usage_events_session_idx
              ON usage_events(session_id, created_at);

            CREATE INDEX IF NOT EXISTS usage_events_message_idx
              ON usage_events(message_id);

            CREATE INDEX IF NOT EXISTS usage_events_file_idx
              ON usage_events(file_id);
            """
        )
        _ensure_columns(
            conn,
            "sessions",
            {
                "context_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            conn,
            "artifacts",
            {
                "display_mode": "TEXT NOT NULL DEFAULT 'primary'",
            },
        )
        _ensure_columns(
            conn,
            "agent_runs",
            {
                "execution_plan_json": "TEXT NOT NULL DEFAULT '{}'",
                "task_contract_json": "TEXT NOT NULL DEFAULT '{}'",
                "prompt_context_json": "TEXT NOT NULL DEFAULT '{}'",
                "provider_status_json": "TEXT NOT NULL DEFAULT '{}'",
                "agent_actions_json": "TEXT NOT NULL DEFAULT '[]'",
                "review_scores_json": "TEXT NOT NULL DEFAULT '{}'",
                "revision_required": "INTEGER NOT NULL DEFAULT 0",
                "model_assignments_json": "TEXT NOT NULL DEFAULT '{}'",
                "tool_calls_json": "TEXT NOT NULL DEFAULT '[]'",
                "artifact_versions_json": "TEXT NOT NULL DEFAULT '[]'",
                "repair_attempts_json": "TEXT NOT NULL DEFAULT '[]'",
                "quality_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
            },
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def rows(query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, tuple(params)).fetchall()


def row(query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, tuple(params)).fetchone()


def execute(query: str, params: Iterable[Any] = ()) -> None:
    with connect() as conn:
        conn.execute(query, tuple(params))
