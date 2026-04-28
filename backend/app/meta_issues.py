from __future__ import annotations

import hashlib
from typing import Any

import httpx

from .auth import Principal
from .config import get_settings
from .database import connect
from .security import redact_text, sanitize_metadata
from .utils import excerpt, json_dumps, json_loads, new_id, now


VALID_STATUSES = {"open", "triaged", "resolved", "ignored"}


def issue_fingerprint(source: str, title: str, body: str) -> str:
    normalized = f"{source.strip().lower()}|{title.strip().lower()}|{body.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _sanitize_title(value: str) -> str:
    return excerpt(redact_text(value), 180) or "Runtime issue"


def _sanitize_body(value: str | None) -> str:
    return excerpt(redact_text(value or ""), 2000)


def _row_to_issue(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "created_by": row["created_by"],
        "source": row["source"],
        "severity": row["severity"],
        "status": row["status"],
        "title": row["title"],
        "body": row["body"],
        "metadata": sanitize_metadata(json_loads(row["metadata"], {})),
        "fingerprint": row["fingerprint"],
        "external_url": row["external_url"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_meta_issue(
    principal: Principal,
    *,
    source: str,
    severity: str,
    title: str,
    body: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue = capture_internal_issue(
        organization_id=principal.organization_id,
        created_by=principal.user_id,
        source=source,
        severity=severity,
        title=title,
        body=body,
        metadata=metadata,
    )
    external_url = await maybe_create_github_issue(issue)
    if external_url:
        with connect() as conn:
            conn.execute(
                "UPDATE meta_issues SET external_url = ?, updated_at = ? WHERE id = ?",
                (external_url, now(), issue["id"]),
            )
        issue["external_url"] = external_url
    return issue


def capture_internal_issue(
    *,
    organization_id: str,
    created_by: str | None,
    source: str,
    severity: str,
    title: str,
    body: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue_id = new_id("iss")
    stamp = now()
    safe_title = _sanitize_title(title)
    safe_body = _sanitize_body(body)
    safe_metadata = sanitize_metadata(metadata or {})
    fingerprint = issue_fingerprint(source, safe_title, safe_body)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO meta_issues
            (id, organization_id, created_by, source, severity, status, title, body, metadata, fingerprint, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id,
                organization_id,
                created_by,
                source,
                severity,
                "open",
                safe_title,
                safe_body,
                json_dumps(safe_metadata),
                fingerprint,
                stamp,
                stamp,
            ),
        )
        row = conn.execute("SELECT * FROM meta_issues WHERE id = ?", (issue_id,)).fetchone()
    return _row_to_issue(row)


async def maybe_create_github_issue(issue: dict[str, Any]) -> str | None:
    settings = get_settings()
    repo = (settings.filechat_meta_issues_github_repo or "").strip()
    token = (settings.filechat_meta_issues_github_token or "").strip()
    if not settings.filechat_meta_issues_github_enabled or not repo or not token:
        return None
    payload = {
        "title": f"[FileChat] {issue['title']}",
        "body": f"{issue['body']}\n\nFingerprint: `{issue['fingerprint']}`\nSource: `{issue['source']}`",
        "labels": ["filechat-meta-issue", f"severity:{issue['severity']}"],
    }
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"https://api.github.com/repos/{repo}/issues", headers=headers, json=payload)
            response.raise_for_status()
        url = response.json().get("html_url")
        return str(url) if url else None
    except Exception:
        return None


def list_meta_issues(organization_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meta_issues
            WHERE organization_id = ?
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (organization_id,),
        ).fetchall()
    return [_row_to_issue(row) for row in rows]


def update_meta_issue_status(organization_id: str, issue_id: str, status: str) -> dict[str, Any] | None:
    if status not in VALID_STATUSES:
        raise ValueError("Invalid meta issue status.")
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE meta_issues
            SET status = ?, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (status, now(), issue_id, organization_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM meta_issues WHERE id = ? AND organization_id = ?", (issue_id, organization_id)).fetchone()
    return _row_to_issue(row)
