from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request


ADMIN_ROLES = frozenset({"admin", "director"})
HR_ROLES = frozenset({"admin", "director", "financial"})
FINANCE_ROLES = frozenset({"admin", "director", "financial"})
FLEET_ROLES = frozenset({"admin", "director", "financial"})
ALINE_ROLES = frozenset({"admin", "director", "helena", "aline"})
PROJECTIST_ROLES = frozenset({"admin", "director", "helena"})
SYSTEM_STATE_ROLES = frozenset(
    {"admin", "director", "financial", "helena", "aline"}
)
WHATSAPP_ROLES = frozenset(
    {"admin", "director", "financial", "helena", "aline"}
)


MODULE_ROLES: dict[str, frozenset[str]] = {
    "admin": ADMIN_ROLES,
    "hr": HR_ROLES,
    "finance": FINANCE_ROLES,
    "fleet": FLEET_ROLES,
    "aline": ALINE_ROLES,
    "projectist": PROJECTIST_ROLES,
    "system_state": SYSTEM_STATE_ROLES,
    "workspace": SYSTEM_STATE_ROLES,
    "whatsapp": WHATSAPP_ROLES,
}


FLEET_PREFIXES = (
    "/api/vehicles",
    "/api/routes",
    "/api/reports/daily",
    "/reports/daily",
    "/api/tracker-health",
    "/api/location",
    "/api/history",
    "/api/trips",
    "/api/stops",
    "/api/alerts",
    "/api/geofences",
    "/api/usage",
    "/api/driving-score",
    "/api/fuel",
    "/api/maintenance",
    "/api/expenses",
    "/api/vehicle-debts",
    "/api/documents",
    "/api/stored-documents",
    "/api/costs",
    "/api/provider-dashboard",
    "/api/reports/vehicle.csv",
    "/api/dashboard/summary",
    "/api/connection-test",
)


def normalized_role(user: dict[str, Any] | None) -> str:
    """Return the canonical role. Authorization never falls back to email/legacy role."""
    return str((user or {}).get("role_code") or "").strip().lower()


def role_is_allowed(
    user: dict[str, Any] | None,
    allowed_roles: Iterable[str],
) -> bool:
    return normalized_role(user) in set(allowed_roles)


def require_roles(
    request: Request,
    allowed_roles: Iterable[str],
    *,
    detail: str = "Este perfil não possui acesso a este recurso.",
) -> dict[str, Any]:
    user = getattr(request.state, "system_user", None)
    if not role_is_allowed(user, allowed_roles):
        raise HTTPException(status_code=403, detail=detail)
    return user


def require_module(request: Request, module: str) -> dict[str, Any]:
    allowed = MODULE_ROLES.get(module, frozenset())
    return require_roles(request, allowed)


def module_for_path(path: str) -> str | None:
    normalized = str(path or "")
    if normalized in {"/", "/workspace"}:
        return "workspace"
    if normalized.startswith("/agenda-tablet") or normalized.startswith("/api/tablet-agenda"):
        return "system_state"
    if normalized == "/whatsapp" or normalized.startswith("/api/whatsapp"):
        return "whatsapp"
    if normalized.startswith("/admin/users"):
        return "admin"
    if normalized.startswith("/api/admin/"):
        return "admin"
    if normalized.startswith("/api/works/bulk"):
        return "admin"
    if normalized.startswith("/rh/"):
        return "hr"
    if normalized.startswith("/finance/") or normalized.startswith(
        "/api/financial-calendar"
    ):
        return "finance"
    if normalized == "/fleet" or normalized.startswith(FLEET_PREFIXES):
        return "fleet"
    if normalized.startswith("/api/employee-certificates"):
        return "hr"
    if normalized.startswith(
        ("/api/employees", "/api/exams", "/api/vacations")
    ):
        return "hr"
    if normalized.startswith("/api/employee-portal/admin"):
        return "system_state"
    if normalized.startswith("/api/aline"):
        return "aline"
    if normalized.startswith("/api/system-state"):
        return "system_state"
    if normalized.startswith("/api/cora"):
        return "finance"
    return None


def path_is_allowed(user: dict[str, Any] | None, path: str) -> bool:
    module = module_for_path(path)
    if module is None:
        return True
    return role_is_allowed(user, MODULE_ROLES[module])
