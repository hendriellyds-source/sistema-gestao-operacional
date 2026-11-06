from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from .access_control import HR_ROLES, require_roles
from .audit import write_audit
from .private_storage import (
    EMPLOYEE_CERTIFICATES_BUCKET,
    PrivateStorageError,
    decode_private_file,
    delete_private_object,
    random_object_path,
    signed_private_url,
    upload_private_object,
    validate_private_object_reference,
)
from .settings import settings
from .supabase_client import create_admin_supabase
from .system_state import get_system_state
from .time_utils import local_today


router = APIRouter(
    prefix="/api/employee-certificates",
    tags=["Certificados de funcionários"],
)


class CertificateData(BaseModel):
    course_name: str = Field(min_length=1, max_length=240)
    issuing_institution: str | None = Field(default=None, max_length=240)
    issue_date: date | None = None
    expiration_date: date | None = None
    no_expiration: bool = False
    certificate_number: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def validate_expiration(self) -> "CertificateData":
        if not self.no_expiration and self.expiration_date is None:
            raise ValueError("Informe a validade ou marque 'sem validade'.")
        if self.no_expiration:
            self.expiration_date = None
        if (
            self.issue_date
            and self.expiration_date
            and self.expiration_date < self.issue_date
        ):
            raise ValueError("A validade não pode ser anterior à emissão.")
        return self


class PrivateFilePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: str | None = Field(default=None, max_length=160)
    content_base64: str = Field(min_length=1)


class CertificateCreatePayload(BaseModel):
    employee_id: int = Field(gt=0)
    data: CertificateData
    file: PrivateFilePayload


class CertificateUpdatePayload(BaseModel):
    data: CertificateData


def certificate_status(item: dict[str, Any], *, today: date | None = None) -> str:
    if bool(item.get("no_expiration")):
        return "no_expiration"
    raw = item.get("expiration_date")
    if not raw:
        return "expired"
    try:
        expiry = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return "expired"
    current = today or local_today()
    days = (expiry - current).days
    if days < 0:
        return "expired"
    if days <= 30:
        return "expiring"
    return "valid"


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _employee_exists(employee_id: int) -> bool:
    state = get_system_state().get("data") or {}
    employees = state.get("employees") if isinstance(state, dict) else []
    return any(
        isinstance(item, dict) and int(item.get("id") or 0) == int(employee_id)
        for item in (employees or [])
    )


def _public(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["status"] = certificate_status(result)
    result["has_file"] = bool(result.get("storage_path"))
    result.pop("storage_path", None)
    result["download_url"] = (
        f"/api/employee-certificates/{result['id']}/download"
    )
    return result


def _certificate_for_user(
    certificate_id: UUID | str,
    user: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = (
            create_admin_supabase()
            .table("employee_certificates")
            .select("*")
            .eq("id", str(certificate_id))
            .eq("organization_id", user["organization_id"])
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A migração de certificados ainda não está disponível no Supabase.",
        ) from exc
    rows = _rows(response.data)
    if not rows:
        raise HTTPException(status_code=404, detail="Certificado não encontrado.")
    return rows[0]


@router.get("")
def list_certificates(
    request: Request,
    employee_id: int | None = Query(default=None, gt=0),
) -> list[dict[str, Any]]:
    user = require_roles(request, HR_ROLES)
    try:
        query = (
            create_admin_supabase()
            .table("employee_certificates")
            .select("*")
            .eq("organization_id", user["organization_id"])
            .is_("deleted_at", "null")
        )
        if employee_id is not None:
            query = query.eq("employee_id", employee_id)
        response = query.order("expiration_date", desc=False).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A migração de certificados ainda não está disponível no Supabase.",
        ) from exc
    return [_public(row) for row in _rows(response.data)]


@router.post("", status_code=201)
def create_certificate(
    payload: CertificateCreatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, HR_ROLES)
    if not _employee_exists(payload.employee_id):
        raise HTTPException(status_code=404, detail="Funcionário não encontrado.")
    try:
        content, original_name, extension, mime_type = decode_private_file(
            filename=payload.file.filename,
            content_base64=payload.file.content_base64,
            declared_mime=payload.file.mime_type,
            max_bytes=settings.employee_certificate_file_max_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    certificate_id = uuid4()
    object_path = random_object_path(
        organization_id=user["organization_id"],
        namespace="employees",
        owner_id=str(payload.employee_id),
        extension=extension,
    )
    try:
        upload_private_object(
            bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            object_path=object_path,
            content=content,
            mime_type=mime_type,
        )
    except PrivateStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "id": str(certificate_id),
        "organization_id": user["organization_id"],
        "employee_id": payload.employee_id,
        **payload.data.model_dump(mode="json"),
        "storage_bucket": EMPLOYEE_CERTIFICATES_BUCKET,
        "storage_path": object_path,
        "original_filename": original_name,
        "mime_type": mime_type,
        "file_size": len(content),
        "registered_by_user_id": user["auth_user_id"],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        response = (
            create_admin_supabase()
            .table("employee_certificates")
            .insert(record)
            .execute()
        )
        saved = (_rows(response.data) or [record])[0]
    except Exception as exc:
        try:
            delete_private_object(
                bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                object_path=object_path,
            )
        except PrivateStorageError:
            pass
        raise HTTPException(
            status_code=503,
            detail="Não foi possível cadastrar o certificado no Supabase.",
        ) from exc
    write_audit(
        action="employee_certificate.upload",
        target=str(certificate_id),
        success=True,
        request_payload={"employee_id": payload.employee_id, "mime_type": mime_type},
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return _public(saved)


@router.put("/{certificate_id}")
def update_certificate(
    certificate_id: UUID,
    payload: CertificateUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, HR_ROLES)
    _certificate_for_user(certificate_id, user)
    values = {
        **payload.data.model_dump(mode="json"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = (
            create_admin_supabase()
            .table("employee_certificates")
            .update(values)
            .eq("id", str(certificate_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível editar o certificado.") from exc
    write_audit(
        action="employee_certificate.update",
        target=str(certificate_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    saved = (_rows(response.data) or [{**_certificate_for_user(certificate_id, user), **values}])[0]
    return _public(saved)


@router.put("/{certificate_id}/file")
def replace_certificate_file(
    certificate_id: UUID,
    payload: PrivateFilePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, HR_ROLES)
    current = _certificate_for_user(certificate_id, user)
    try:
        content, original_name, extension, mime_type = decode_private_file(
            filename=payload.filename,
            content_base64=payload.content_base64,
            declared_mime=payload.mime_type,
            max_bytes=settings.employee_certificate_file_max_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    new_path = random_object_path(
        organization_id=user["organization_id"],
        namespace="employees",
        owner_id=str(current["employee_id"]),
        extension=extension,
    )
    try:
        upload_private_object(
            bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            object_path=new_path,
            content=content,
            mime_type=mime_type,
        )
        values = {
            "storage_bucket": EMPLOYEE_CERTIFICATES_BUCKET,
            "storage_path": new_path,
            "original_filename": original_name,
            "mime_type": mime_type,
            "file_size": len(content),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        response = (
            create_admin_supabase()
            .table("employee_certificates")
            .update(values)
            .eq("id", str(certificate_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        try:
            delete_private_object(
                bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                object_path=new_path,
            )
        except PrivateStorageError:
            pass
        detail = (
            str(exc)
            if isinstance(exc, PrivateStorageError)
            else "Não foi possível substituir o arquivo."
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    if current.get("storage_path"):
        try:
            old_bucket, old_path = validate_private_object_reference(
                bucket=str(current.get("storage_bucket") or EMPLOYEE_CERTIFICATES_BUCKET),
                object_path=str(current["storage_path"]),
                expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
                organization_id=user["organization_id"],
                namespace="employees",
                owner_id=str(current["employee_id"]),
            )
            delete_private_object(
                bucket=old_bucket,
                object_path=old_path,
            )
        except PrivateStorageError:
            # The database already points to the new valid object. The orphan can be
            # removed manually from the private bucket without breaking the record.
            pass
    write_audit(
        action="employee_certificate.replace_file",
        target=str(certificate_id),
        success=True,
        request_payload={"mime_type": mime_type},
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    saved = (_rows(response.data) or [{**current, **values}])[0]
    return _public(saved)


@router.get("/{certificate_id}/download")
def download_certificate(certificate_id: UUID, request: Request) -> dict[str, Any]:
    user = require_roles(request, HR_ROLES)
    item = _certificate_for_user(certificate_id, user)
    try:
        bucket, object_path = validate_private_object_reference(
            bucket=str(item.get("storage_bucket") or EMPLOYEE_CERTIFICATES_BUCKET),
            object_path=str(item.get("storage_path") or ""),
            expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            organization_id=user["organization_id"],
            namespace="employees",
            owner_id=str(item["employee_id"]),
        )
        url = signed_private_url(
            bucket=bucket,
            object_path=object_path,
            expires_in=settings.private_download_url_seconds,
        )
    except PrivateStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "url": url,
        "expires_in": settings.private_download_url_seconds,
        "filename": item.get("original_filename"),
        "mime_type": item.get("mime_type"),
    }


@router.delete("/{certificate_id}")
def delete_certificate(certificate_id: UUID, request: Request) -> dict[str, Any]:
    user = require_roles(request, HR_ROLES)
    item = _certificate_for_user(certificate_id, user)
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        (
            create_admin_supabase()
            .table("employee_certificates")
            .update(
                {
                    "deleted_at": timestamp,
                    "deleted_by_user_id": user["auth_user_id"],
                    "updated_at": timestamp,
                }
            )
            .eq("id", str(certificate_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível excluir o certificado.") from exc
    cleanup_pending = False
    try:
        bucket, object_path = validate_private_object_reference(
            bucket=str(item.get("storage_bucket") or EMPLOYEE_CERTIFICATES_BUCKET),
            object_path=str(item.get("storage_path") or ""),
            expected_bucket=EMPLOYEE_CERTIFICATES_BUCKET,
            organization_id=user["organization_id"],
            namespace="employees",
            owner_id=str(item["employee_id"]),
        )
        delete_private_object(
            bucket=bucket,
            object_path=object_path,
        )
    except PrivateStorageError as exc:
        cleanup_pending = True
        write_audit(
            action="employee_certificate.delete_file",
            target=str(certificate_id),
            success=False,
            error_message=str(exc),
            actor_user_id=user["auth_user_id"],
            actor_role_code=user["role_code"],
        )
    write_audit(
        action="employee_certificate.delete",
        target=str(certificate_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return {"ok": True, "file_cleanup_pending": cleanup_pending}
