from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from .config import get_settings
from .security import sanitize_metadata


MAX_CLOCK_SKEW_SECONDS = 60 * 5


def verify_slack_signature(*, body: bytes, timestamp: str | None, signature: str | None) -> tuple[bool, str]:
    secret = get_settings().filechat_slack_signing_secret
    if not secret:
        return False, "Slack signing secret is not configured."
    if not timestamp or not signature:
        return False, "Slack signature headers are missing."
    try:
        stamp = int(timestamp)
    except ValueError:
        return False, "Slack timestamp is invalid."
    if abs(time.time() - stamp) > MAX_CLOCK_SKEW_SECONDS:
        return False, "Slack timestamp is outside the allowed window."
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False, "Slack signature mismatch."
    return True, ""


def verify_telegram_secret(secret_header: str | None) -> tuple[bool, str]:
    secret = get_settings().filechat_telegram_webhook_secret
    if not secret:
        return False, "Telegram webhook secret is not configured."
    if not secret_header:
        return False, "Telegram webhook secret header is missing."
    if not hmac.compare_digest(secret, secret_header):
        return False, "Telegram webhook secret mismatch."
    return True, ""


def _attachment_from_item(item: dict[str, Any], fallback_name: str) -> dict[str, Any] | None:
    content = item.get("content")
    if content is None and item.get("content_base64") is not None:
        try:
            body = base64.b64decode(str(item["content_base64"]), validate=True)
        except Exception:
            return None
    elif isinstance(content, str):
        body = content.encode("utf-8")
    elif isinstance(content, bytes):
        body = content
    else:
        return None
    name = str(item.get("name") or item.get("file_name") or item.get("filename") or fallback_name)
    return {
        "name": name,
        "content_type": str(item.get("mimetype") or item.get("mime_type") or item.get("content_type") or "text/plain"),
        "body": body,
        "metadata": sanitize_metadata({key: value for key, value in item.items() if key not in {"content", "content_base64"}}),
    }


def slack_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    raw_files = payload.get("filechat_attachments") or event.get("files") or []
    if not isinstance(raw_files, list):
        return []
    return [
        attachment
        for index, item in enumerate(raw_files, start=1)
        if isinstance(item, dict)
        for attachment in [_attachment_from_item(item, f"slack-attachment-{index}.txt")]
        if attachment
    ]


def telegram_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("filechat_attachments")
    if isinstance(raw, list):
        return [
            attachment
            for index, item in enumerate(raw, start=1)
            if isinstance(item, dict)
            for attachment in [_attachment_from_item(item, f"telegram-attachment-{index}.txt")]
            if attachment
        ]
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    document = message.get("document") if isinstance(message.get("document"), dict) else {}
    if not document:
        return []
    attachment = _attachment_from_item(document, "telegram-document.txt")
    return [attachment] if attachment else []
