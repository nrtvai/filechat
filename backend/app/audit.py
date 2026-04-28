from __future__ import annotations

from typing import Any

from .auth import Principal
from .database import connect
from .security import sanitize_metadata
from .utils import json_dumps, new_id, now


def record_audit_event(
    principal: Principal,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    event_id = new_id("aud")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events
            (id, organization_id, actor_user_id, actor_role, action, target_type, target_id, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                principal.organization_id,
                principal.user_id,
                principal.role,
                action,
                target_type,
                target_id,
                json_dumps(sanitize_metadata(metadata or {})),
                now(),
            ),
        )
    return event_id
