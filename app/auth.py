from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .settings import settings
from .supabase_client import create_admin_supabase, create_public_supabase


router = APIRouter(tags=["Autenticação"])
COOKIE_NAME = "portfolio_session"

_DEMO_SESSIONS: dict[str, dict[str, Any]] = {}


ROLE_TO_LEGACY = {
    "admin": "adm",
    "director": "diretor",
    "financial": "financeiro",
    "helena": "helena",
    "aline": "aline",
    "employee": "funcionario",
    "client": "cliente",
    "pending": "pending",
}

ROLE_AREAS = {
    "diretor": ["admin", "finance"],
    "adm": ["admin", "finance"],
    "helena": ["projectist"],
    "aline": ["aline"],
    "financeiro": ["finance", "admin"],
    "funcionario": ["employee"],
    "cliente": ["client"],
}

DEMAND_DISPATCH_TARGETS = {
    "diretor": ("adm", "helena", "aline", "financeiro"),
    "adm": ("diretor", "helena", "aline", "financeiro"),
    "helena": ("adm",),
    "aline": ("adm",),
    "financeiro": ("adm",),
}


class LoginPayload(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    username: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    remember_tablet: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _first(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list) and data:
        item = data[0]
        return item if isinstance(item, dict) else None

    if isinstance(data, dict):
        return data

    return None


def _profile_by_auth_user(auth_user_id: str) -> dict[str, Any] | None:
    client = create_admin_supabase()
    try:
        result = (
            client.table("profiles")
            .select(
                "id,auth_user_id,organization_id,"
                "role_code,full_name,email,status,avatar_path,avatar_updated_at"
            )
            .eq("auth_user_id", auth_user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        # Mantém o login disponível durante a janela entre o deploy do código e a
        # execução da migração aditiva das fotos.
        result = (
            client.table("profiles")
            .select(
                "id,auth_user_id,organization_id,role_code,full_name,email,status"
            )
            .eq("auth_user_id", auth_user_id)
            .limit(1)
            .execute()
        )

    return _first(result.data)


def _profile_by_id(profile_id: str) -> dict[str, Any] | None:
    client = create_admin_supabase()
    try:
        result = (
            client.table("profiles")
            .select(
                "id,auth_user_id,organization_id,"
                "role_code,full_name,email,status,avatar_path,avatar_updated_at"
            )
            .eq("id", profile_id)
            .limit(1)
            .execute()
        )
    except Exception:
        result = (
            client.table("profiles")
            .select(
                "id,auth_user_id,organization_id,role_code,full_name,email,status"
            )
            .eq("id", profile_id)
            .limit(1)
            .execute()
        )

    return _first(result.data)


def _public_user(profile: dict[str, Any]) -> dict[str, Any]:
    role_code = str(profile.get("role_code") or "pending")
    role = ROLE_TO_LEGACY.get(role_code, role_code)

    email = str(profile.get("email") or "")
    display_name = str(
        profile.get("full_name")
        or email.split("@", 1)[0]
        or "Usuário"
    )

    return {
        "username": email,
        "email": email,
        "display_name": display_name,
        "role": role,
        "role_code": role_code,
        "areas": ROLE_AREAS.get(role, []),
        "can_dispatch_demands": bool(
            DEMAND_DISPATCH_TARGETS.get(role)
        ),
        "dispatch_targets": list(
            DEMAND_DISPATCH_TARGETS.get(role, ())
        ),
        "profile_id": str(profile["id"]),
        "auth_user_id": str(profile["auth_user_id"]),
        "organization_id": str(profile["organization_id"]),
        "avatar_url": (
            f"/api/profile-photos/{profile['id']}?v={profile.get('avatar_updated_at') or ''}"
            if profile.get("avatar_path")
            else None
        ),
    }


def authenticate(
    email: str,
    password: str,
) -> dict[str, Any] | None:
    if settings.demo_mode:
        if email.strip().lower() != settings.demo_email.strip().lower():
            return None
        if password != settings.demo_password:
            return None
        return {
            "username": settings.demo_email,
            "email": settings.demo_email,
            "display_name": settings.demo_display_name,
            "role": "adm",
            "role_code": "admin",
            "areas": ["admin", "finance"],
            "can_dispatch_demands": True,
            "dispatch_targets": ["diretor", "helena", "aline", "financeiro"],
            "profile_id": "demo-profile",
            "auth_user_id": "demo-user",
            "organization_id": "demo-organization",
            "avatar_url": None,
        }

    try:
        result = create_public_supabase().auth.sign_in_with_password(
            {
                "email": email.strip().lower(),
                "password": password,
            }
        )
    except Exception:
        return None

    if not result.user:
        return None

    profile = _profile_by_auth_user(str(result.user.id))

    if not profile:
        return None

    if profile.get("status") != "active":
        return None

    if profile.get("role_code") == "pending":
        return None

    return _public_user(profile)


def create_session(
    profile_id: str,
    request: Request,
    *,
    hours: int | None = None,
) -> tuple[str, str]:
    token = secrets.token_urlsafe(48)

    created_at = _now()
    expires_at = created_at + timedelta(
        hours=hours or settings.auth_session_hours
    )

    user_agent = str(
        request.headers.get("user-agent") or ""
    )[:500]

    if settings.demo_mode:
        _DEMO_SESSIONS[_token_hash(token)] = {
            "profile_id": profile_id,
            "created_at": created_at.isoformat(),
            "last_seen_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "user_agent": user_agent,
        }
        return token, expires_at.isoformat()

    create_admin_supabase().table("web_sessions").insert(
        {
            "token_hash": _token_hash(token),
            "profile_id": profile_id,
            "created_at": created_at.isoformat(),
            "last_seen_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "user_agent": user_agent,
        }
    ).execute()

    return token, expires_at.isoformat()


def user_from_session(
    token: str | None,
) -> dict[str, Any] | None:
    if not token:
        return None

    token_digest = _token_hash(token)

    if settings.demo_mode:
        session = _DEMO_SESSIONS.get(token_digest)
        if not session:
            return None
        expires_at = _parse_datetime(session.get("expires_at"))
        if expires_at is None or expires_at <= _now():
            _DEMO_SESSIONS.pop(token_digest, None)
            return None
        return authenticate(settings.demo_email, settings.demo_password)

    result = (
        create_admin_supabase()
        .table("web_sessions")
        .select(
            "profile_id,last_seen_at,expires_at,revoked_at"
        )
        .eq("token_hash", token_digest)
        .limit(1)
        .execute()
    )

    session = _first(result.data)

    if not session:
        return None

    if session.get("revoked_at"):
        return None

    expires_at = _parse_datetime(session.get("expires_at"))

    if expires_at is None or expires_at <= _now():
        return None

    profile = _profile_by_id(str(session["profile_id"]))

    if not profile:
        return None

    if profile.get("status") != "active":
        return None

    if profile.get("role_code") == "pending":
        return None

    last_seen_at = _parse_datetime(session.get("last_seen_at"))

    if (
        last_seen_at is None
        or _now() - last_seen_at > timedelta(minutes=5)
    ):
        (
            create_admin_supabase()
            .table("web_sessions")
            .update({"last_seen_at": _now().isoformat()})
            .eq("token_hash", token_digest)
            .execute()
        )

    return _public_user(profile)


def delete_session(token: str | None) -> None:
    if not token:
        return

    if settings.demo_mode:
        _DEMO_SESSIONS.pop(_token_hash(token), None)
        return

    (
        create_admin_supabase()
        .table("web_sessions")
        .update({"revoked_at": _now().isoformat()})
        .eq("token_hash", _token_hash(token))
        .execute()
    )


def init_auth() -> None:
    # Mantida para compatibilidade com o lifespan do main.py.
    # Usuários e senhas agora são administrados pelo Supabase Auth.
    return None


@router.post("/api/auth/login")
def login(
    payload: LoginPayload,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    email = str(payload.email or payload.username or "").strip()

    if not email:
        raise HTTPException(
            status_code=422,
            detail="Informe o e-mail.",
        )

    user = authenticate(email, payload.password)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha inválidos.",
        )

    session_hours = settings.tablet_session_days * 24 if payload.remember_tablet else settings.auth_session_hours
    token, expires_at = create_session(
        user["profile_id"],
        request,
        hours=session_hours,
    )

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )

    return {
        "ok": True,
        "user": user,
        "expires_at": expires_at,
    }


@router.post("/api/auth/logout")
def logout(
    response: Response,
    portfolio_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    delete_session(portfolio_session)

    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )

    return {"ok": True}


@router.get("/api/auth/me")
def me(
    portfolio_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    user = user_from_session(portfolio_session)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida ou expirada.",
        )

    return user
