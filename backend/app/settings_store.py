from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .config import get_settings
from .database import connect

KEYRING_SERVICE = "filechat-openrouter"
KEYRING_USERNAME = "local-user"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keyring_get() -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        return None


def _keyring_set(value: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value)
        return True
    except Exception:
        return False


def _keyring_delete() -> bool:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        return True
    except Exception:
        return False


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now()),
        )


def delete_setting(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def get_openrouter_key() -> tuple[str | None, str]:
    settings = get_settings()
    if settings.openrouter_api_key:
        return settings.openrouter_api_key, "env"
    key = _keyring_get()
    if key:
        return key, "local"
    fallback = get_setting("openrouter_api_key")
    if fallback:
        return fallback, "local"
    return None, "missing"


def set_openrouter_key(value: str) -> None:
    if not _keyring_set(value):
        set_setting("openrouter_api_key", value)
    set_provider_verification("unverified", "Saved key has not been verified yet.")


def clear_saved_openrouter_key() -> None:
    _keyring_delete()
    delete_setting("openrouter_api_key")
    set_provider_verification("missing", "OpenRouter API key is missing.")


def _fingerprint(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def set_provider_verification(status: str, message: str = "") -> None:
    key, _ = get_openrouter_key()
    set_setting("openrouter_provider_status", status)
    set_setting("openrouter_provider_message", message)
    set_setting("openrouter_provider_fingerprint", _fingerprint(key))
    set_setting("openrouter_verified_at", now() if status == "verified" else "")


def current_provider_verification() -> dict[str, str | None]:
    settings = get_settings()
    key, _ = get_openrouter_key()
    if settings.filechat_allow_fake_openrouter:
        return {
            "status": "verified",
            "message": "Fake OpenRouter mode is enabled for local development.",
            "verified_at": now(),
        }
    if not key:
        return {"status": "missing", "message": "OpenRouter API key is missing.", "verified_at": None}
    status = get_setting("openrouter_provider_status", "unverified") or "unverified"
    fingerprint = get_setting("openrouter_provider_fingerprint", "") or ""
    if fingerprint != _fingerprint(key):
        return {"status": "unverified", "message": "OpenRouter key has not been verified.", "verified_at": None}
    return {
        "status": status,
        "message": get_setting("openrouter_provider_message", "") or "",
        "verified_at": get_setting("openrouter_verified_at", "") or None,
    }


def current_app_settings():
    settings = get_settings()
    key, source = get_openrouter_key()
    chat_model = get_setting("chat_model", settings.filechat_chat_model)
    provider = current_provider_verification()
    return {
        "openrouter_key_configured": bool(key) or settings.filechat_allow_fake_openrouter,
        "openrouter_key_source": source if key else "missing",
        "edition": settings.filechat_edition,
        "settings_scope": "organization" if settings.filechat_edition == "enterprise" else "single_user",
        "openrouter_provider_status": provider["status"],
        "openrouter_provider_message": provider["message"] or "",
        "openrouter_verified_at": provider["verified_at"],
        "chat_model": chat_model,
        "orchestrator_model": get_setting("orchestrator_model", chat_model),
        "analysis_model": get_setting("analysis_model", chat_model),
        "writing_model": get_setting("writing_model", chat_model),
        "repair_model": get_setting("repair_model", chat_model),
        "embedding_model": get_setting("embedding_model", settings.filechat_embedding_model),
        "ocr_model": get_setting("ocr_model", settings.filechat_ocr_model),
        "retrieval_depth": int(get_setting("retrieval_depth", "8") or "8"),
        "strict_grounding": (get_setting("strict_grounding", "true") or "true").lower() == "true",
        "web_search_enabled": (get_setting("web_search_enabled", "false") or "false").lower() == "true",
        "web_search_engine": get_setting("web_search_engine", "auto") or "auto",
        "reasoning_effort": get_setting("reasoning_effort", "medium") or "medium",
        "model_routing_mode": get_setting("model_routing_mode", "auto") or "auto",
        "high_cost_confirmation": (get_setting("high_cost_confirmation", "true") or "true").lower() == "true",
    }
