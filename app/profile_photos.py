from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .access_control import ADMIN_ROLES, require_roles
from .audit import write_audit
from .private_storage import (
    PROFILE_PHOTOS_BUCKET,
    PrivateStorageError,
    decode_private_file,
    delete_private_object,
    random_object_path,
    signed_private_url,
    upload_private_object,
)
from .settings import settings
from .supabase_client import create_admin_supabase


router = APIRouter(tags=["Fotos dos perfis"])


class ProfilePhotoPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: str | None = Field(default=None, max_length=160)
    content_base64: str = Field(min_length=1)


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _profile(profile_id: UUID | str, organization_id: str) -> dict[str, Any]:
    try:
        response = (
            create_admin_supabase()
            .table("profiles")
            .select(
                "id,auth_user_id,organization_id,role_code,full_name,email,status,"
                "avatar_bucket,avatar_path,avatar_mime_type,avatar_updated_at"
            )
            .eq("id", str(profile_id))
            .eq("organization_id", organization_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A migração de fotos dos perfis ainda não foi aplicada no Supabase.",
        ) from exc
    rows = _rows(response.data)
    if not rows:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    return rows[0]


def _public(profile: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: profile.get(key)
        for key in ("id", "role_code", "full_name", "email", "status", "avatar_updated_at")
    }
    result["has_photo"] = bool(profile.get("avatar_path"))
    result["photo_url"] = (
        f"/api/profile-photos/{profile['id']}?v={profile.get('avatar_updated_at') or ''}"
        if profile.get("avatar_path")
        else None
    )
    return result


@router.get("/api/admin/profiles")
def list_profiles(request: Request) -> list[dict[str, Any]]:
    user = require_roles(request, ADMIN_ROLES)
    try:
        response = (
            create_admin_supabase()
            .table("profiles")
            .select(
                "id,organization_id,role_code,full_name,email,status,"
                "avatar_path,avatar_updated_at"
            )
            .eq("organization_id", user["organization_id"])
            .order("full_name", desc=False)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A migração de fotos dos perfis ainda não foi aplicada no Supabase.",
        ) from exc
    return [_public(row) for row in _rows(response.data)]


@router.put("/api/admin/profiles/{profile_id}/photo")
def update_profile_photo(
    profile_id: UUID,
    payload: ProfilePhotoPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, ADMIN_ROLES)
    profile = _profile(profile_id, user["organization_id"])
    try:
        content, _original_name, extension, mime_type = decode_private_file(
            filename=payload.filename,
            content_base64=payload.content_base64,
            declared_mime=payload.mime_type,
            max_bytes=settings.profile_photo_file_max_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if mime_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=422, detail="Envie a foto em JPG ou PNG.")

    object_path = random_object_path(
        organization_id=user["organization_id"],
        namespace="profiles",
        owner_id=str(profile_id),
        extension=extension,
    )
    try:
        upload_private_object(
            bucket=PROFILE_PHOTOS_BUCKET,
            object_path=object_path,
            content=content,
            mime_type=mime_type,
        )
    except PrivateStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        response = (
            create_admin_supabase()
            .table("profiles")
            .update(
                {
                    "avatar_bucket": PROFILE_PHOTOS_BUCKET,
                    "avatar_path": object_path,
                    "avatar_mime_type": mime_type,
                    "avatar_updated_at": timestamp,
                }
            )
            .eq("id", str(profile_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        try:
            delete_private_object(bucket=PROFILE_PHOTOS_BUCKET, object_path=object_path)
        except PrivateStorageError:
            pass
        raise HTTPException(status_code=503, detail="Não foi possível vincular a foto ao perfil.") from exc

    previous_path = profile.get("avatar_path")
    if previous_path and previous_path != object_path:
        try:
            delete_private_object(
                bucket=str(profile.get("avatar_bucket") or PROFILE_PHOTOS_BUCKET),
                object_path=str(previous_path),
            )
        except PrivateStorageError:
            pass
    rows = _rows(response.data)
    updated = rows[0] if rows else {**profile, "avatar_path": object_path, "avatar_updated_at": timestamp}
    write_audit(
        action="profile_photo.updated",
        target=str(profile_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
        request_payload={"mime_type": mime_type, "file_size": len(content)},
    )
    return _public(updated)


@router.delete("/api/admin/profiles/{profile_id}/photo")
def remove_profile_photo(profile_id: UUID, request: Request) -> dict[str, bool]:
    user = require_roles(request, ADMIN_ROLES)
    profile = _profile(profile_id, user["organization_id"])
    path = profile.get("avatar_path")
    try:
        (
            create_admin_supabase()
            .table("profiles")
            .update(
                {
                    "avatar_bucket": None,
                    "avatar_path": None,
                    "avatar_mime_type": None,
                    "avatar_updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", str(profile_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível remover a foto do perfil.") from exc
    if path:
        try:
            delete_private_object(
                bucket=str(profile.get("avatar_bucket") or PROFILE_PHOTOS_BUCKET),
                object_path=str(path),
            )
        except PrivateStorageError:
            pass
    write_audit(
        action="profile_photo.removed",
        target=str(profile_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return {"ok": True}


@router.get("/api/profile-photos/{profile_id}")
def profile_photo(profile_id: UUID, request: Request) -> RedirectResponse:
    user = request.state.system_user
    profile = _profile(profile_id, user["organization_id"])
    if not profile.get("avatar_path"):
        raise HTTPException(status_code=404, detail="Este perfil ainda não possui foto.")
    try:
        url = signed_private_url(
            bucket=str(profile.get("avatar_bucket") or PROFILE_PHOTOS_BUCKET),
            object_path=str(profile["avatar_path"]),
            expires_in=settings.private_download_url_seconds,
        )
    except PrivateStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=307)
