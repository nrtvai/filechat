from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Header, HTTPException

from .config import get_settings


Role = Literal["owner", "admin", "member"]
Edition = Literal["community", "enterprise"]

ROLE_RANK: dict[Role, int] = {
    "member": 1,
    "admin": 2,
    "owner": 3,
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    email: str | None
    role: Role
    organization_id: str
    edition: Edition
    auth_test_mode: bool
    auth_mode: str

    @property
    def enterprise_enabled(self) -> bool:
        return self.edition == "enterprise"

    def has_role(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]

    @property
    def capabilities(self) -> dict[str, bool]:
        can_manage_settings = not self.enterprise_enabled or self.has_role("admin")
        return {
            "use_sessions": True,
            "manage_settings": can_manage_settings,
            "manage_provider_keys": can_manage_settings,
            "export_logs": not self.enterprise_enabled or self.role == "owner",
            "use_admin_console": self.enterprise_enabled and can_manage_settings,
        }


def _role(value: str | None, default: Role) -> Role:
    normalized = (value or default).strip().lower()
    if normalized == "super-admin":
        normalized = "owner"
    if normalized in ROLE_RANK:
        return normalized  # type: ignore[return-value]
    return default


def current_principal(
    x_filechat_test_role: str | None = Header(default=None, alias="X-FileChat-Test-Role"),
    x_filechat_user_role: str | None = Header(default=None, alias="X-FileChat-User-Role"),
    x_filechat_user_id: str | None = Header(default=None, alias="X-FileChat-User-Id"),
    x_filechat_user_email: str | None = Header(default=None, alias="X-FileChat-User-Email"),
    x_filechat_org_id: str | None = Header(default=None, alias="X-FileChat-Org-Id"),
) -> Principal:
    settings = get_settings()
    organization_id = (x_filechat_org_id or "org_single").strip() or "org_single"

    if settings.filechat_edition == "community":
        return Principal(
            user_id="usr_single",
            display_name="Local user",
            email="local@filechat.dev",
            role="owner",
            organization_id="org_single",
            edition="community",
            auth_test_mode=False,
            auth_mode="single_user",
        )

    if settings.filechat_auth_test_mode:
        role = _role(x_filechat_test_role, "owner")
        return Principal(
            user_id=f"usr_test_{role}",
            display_name=f"Test {role}",
            email=f"{role}@filechat.test",
            role=role,
            organization_id=organization_id,
            edition="enterprise",
            auth_test_mode=True,
            auth_mode="test_impersonation",
        )

    if not settings.filechat_trusted_auth_headers:
        return Principal(
            user_id="usr_enterprise_unconfigured",
            display_name="Enterprise user",
            email=None,
            role="member",
            organization_id="org_single",
            edition="enterprise",
            auth_test_mode=False,
            auth_mode="auth_required",
        )

    role = _role(x_filechat_user_role, "member")
    user_id = (x_filechat_user_id or f"usr_{role}").strip() or f"usr_{role}"
    return Principal(
        user_id=user_id,
        display_name=x_filechat_user_email or user_id,
        email=x_filechat_user_email,
        role=role,
        organization_id=organization_id,
        edition="enterprise",
        auth_test_mode=False,
        auth_mode="trusted_header",
    )


def require_settings_admin(principal: Principal) -> Principal:
    if not principal.capabilities["manage_settings"]:
        raise HTTPException(status_code=403, detail="Admin role required to manage FileChat settings.")
    return principal


def require_log_exporter(principal: Principal) -> Principal:
    if not principal.capabilities["export_logs"]:
        raise HTTPException(status_code=403, detail="Owner role required to export audit logs.")
    return principal
