from __future__ import annotations

import re
from typing import Any


REDACTED = "[redacted]"
SENSITIVE_METADATA_KEYS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "excerpt",
    "file_name",
    "filename",
    "key",
    "name",
    "path",
    "raw",
    "secret",
    "text",
    "token",
}
SECRET_PATTERNS = (
    re.compile(r"sk-or-[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
)


def redact_text(value: str) -> str:
    clean = value
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub(REDACTED, clean)
    return clean


def sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in SENSITIVE_METADATA_KEYS or any(part in normalized for part in ("secret", "token", "key")):
                sanitized[str(key)] = REDACTED
            else:
                sanitized[str(key)] = sanitize_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_file_metadata(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "hash": row["hash"],
        "type": row["type"],
        "size": row["size"],
        "status": row["status"],
    }
