from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from .access_control import FINANCE_ROLES, require_roles
from .audit import write_audit
from .calendar_service import aggregate_calendar
from .private_storage import (
    FINANCIAL_ATTACHMENTS_BUCKET,
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
from .time_utils import local_today


router = APIRouter(prefix="/api/financial-calendar", tags=["Calendário financeiro/RH"])

CalendarStatus = Literal["pending", "scheduled", "paid", "overdue", "cancelled"]
CalendarCategory = Literal[
    "vehicle",
    "employee",
    "tax",
    "supplier",
    "payroll",
    "document",
    "maintenance",
    "other",
]


class AttachmentPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: str | None = Field(default=None, max_length=160)
    content_base64: str = Field(min_length=1)


class RecurrencePayload(BaseModel):
    frequency: Literal["weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=1, le=12)
    count: int = Field(ge=2, le=36)


class ManualEntryData(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    category: CalendarCategory = "other"
    due_date: date
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    supplier: str | None = Field(default=None, max_length=240)
    payment_method: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)
    status: CalendarStatus = "pending"
    employee_id: int | None = Field(default=None, gt=0)
    vehicle_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def normalize_overdue(self) -> "ManualEntryData":
        if self.status == "overdue" and self.due_date >= local_today():
            self.status = "pending"
        return self


class ManualEntryCreatePayload(BaseModel):
    data: ManualEntryData
    recurrence: RecurrencePayload | None = None
    attachment: AttachmentPayload | None = None


class ManualEntryUpdatePayload(BaseModel):
    data: ManualEntryData


class StatusPayload(BaseModel):
    status: Literal["paid", "cancelled"]


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _occurrence_date(start: date, recurrence: RecurrencePayload, index: int) -> date:
    step = recurrence.interval * index
    if recurrence.frequency == "weekly":
        return start + timedelta(weeks=step)
    if recurrence.frequency == "monthly":
        return _add_months(start, step)
    return _add_months(start, 12 * step)


def _status_timestamps(
    status: CalendarStatus,
    timestamp: str,
    current: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    current = current or {}
    return {
        "paid_at": (
            (current.get("paid_at") or timestamp) if status == "paid" else None
        ),
        "cancelled_at": (
            (current.get("cancelled_at") or timestamp)
            if status == "cancelled"
            else None
        ),
    }


def _public(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["has_attachment"] = bool(result.get("attachment_path"))
    result.pop("attachment_path", None)
    result.pop("attachment_bucket", None)
    result["attachment_download_url"] = (
        f"/api/financial-calendar/entries/{result['id']}/attachment"
        if result["has_attachment"]
        else None
    )
    return result


def _entry_for_user(entry_id: UUID | str, user: dict[str, Any]) -> dict[str, Any]:
    try:
        response = (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .select("*")
            .eq("id", str(entry_id))
            .eq("organization_id", user["organization_id"])
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="A migração do calendário financeiro ainda não está disponível no Supabase.",
        ) from exc
    rows = _rows(response.data)
    if not rows:
        raise HTTPException(status_code=404, detail="Compromisso não encontrado.")
    return rows[0]


@router.get("")
def calendar_events(
    request: Request,
    date_from: date = Query(),
    date_to: date = Query(),
) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="A data final não pode ser anterior à inicial.")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="Consulte no máximo 367 dias por vez.")
    return aggregate_calendar(
        organization_id=user["organization_id"],
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/entries")
def list_manual_entries(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    category: CalendarCategory | None = None,
    status: CalendarStatus | None = None,
) -> list[dict[str, Any]]:
    user = require_roles(request, FINANCE_ROLES)
    try:
        query = (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .select("*")
            .eq("organization_id", user["organization_id"])
            .is_("deleted_at", "null")
        )
        if date_from:
            query = query.gte("due_date", date_from.isoformat())
        if date_to:
            query = query.lte("due_date", date_to.isoformat())
        if category:
            query = query.eq("category", category)
        if status:
            query = query.eq("status", status)
        response = query.order("due_date", desc=False).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível consultar os compromissos.") from exc
    return [_public(row) for row in _rows(response.data)]


@router.post("/entries", status_code=201)
def create_manual_entry(
    payload: ManualEntryCreatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    recurrence = payload.recurrence
    count = recurrence.count if recurrence else 1
    group_id = str(uuid4()) if recurrence else None
    ids = [uuid4() for _ in range(count)]
    attachment_values: dict[str, Any] = {}
    uploaded_path: str | None = None
    if payload.attachment:
        try:
            content, original_name, extension, mime_type = decode_private_file(
                filename=payload.attachment.filename,
                content_base64=payload.attachment.content_base64,
                declared_mime=payload.attachment.mime_type,
                max_bytes=settings.financial_attachment_file_max_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        uploaded_path = random_object_path(
            organization_id=user["organization_id"],
            namespace="entries",
            owner_id=str(ids[0]),
            extension=extension,
        )
        try:
            upload_private_object(
                bucket=FINANCIAL_ATTACHMENTS_BUCKET,
                object_path=uploaded_path,
                content=content,
                mime_type=mime_type,
            )
        except PrivateStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # A recurring series has one source attachment on its first occurrence.
        # This prevents multiplying large private files up to the 36-item bound.
        attachment_values = {
            "attachment_bucket": FINANCIAL_ATTACHMENTS_BUCKET,
            "attachment_path": uploaded_path,
            "attachment_original_filename": original_name,
            "attachment_mime_type": mime_type,
            "attachment_size": len(content),
        }

    timestamp = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    for index, entry_id in enumerate(ids):
        due_date = (
            _occurrence_date(payload.data.due_date, recurrence, index)
            if recurrence
            else payload.data.due_date
        )
        record = {
            "id": str(entry_id),
            "organization_id": user["organization_id"],
            **payload.data.model_dump(mode="json"),
            "due_date": due_date.isoformat(),
            "amount": float(payload.data.amount) if payload.data.amount is not None else None,
            "created_by_user_id": user["auth_user_id"],
            "created_at": timestamp,
            "updated_at": timestamp,
            **_status_timestamps(payload.data.status, timestamp),
            "recurrence_group_id": group_id,
            "recurrence_sequence": index + 1 if recurrence else None,
            "recurrence_frequency": recurrence.frequency if recurrence else None,
            "recurrence_interval": recurrence.interval if recurrence else None,
            "recurrence_count": count if recurrence else None,
        }
        if index == 0:
            record.update(attachment_values)
        records.append(record)
    try:
        response = (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .insert(records)
            .execute()
        )
        saved = _rows(response.data) or records
    except Exception as exc:
        if uploaded_path:
            try:
                delete_private_object(
                    bucket=FINANCIAL_ATTACHMENTS_BUCKET,
                    object_path=uploaded_path,
                )
            except PrivateStorageError:
                pass
        raise HTTPException(status_code=503, detail="Não foi possível criar o compromisso.") from exc
    write_audit(
        action="financial_calendar.create",
        target=group_id or str(ids[0]),
        success=True,
        request_payload={"count": count, "category": payload.data.category},
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return {"ok": True, "entries": [_public(row) for row in saved], "count": len(saved)}


@router.put("/entries/{entry_id}")
def update_manual_entry(
    entry_id: UUID,
    payload: ManualEntryUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    current = _entry_for_user(entry_id, user)
    timestamp = datetime.now(timezone.utc).isoformat()
    values = {
        **payload.data.model_dump(mode="json"),
        "amount": float(payload.data.amount) if payload.data.amount is not None else None,
        "updated_at": timestamp,
        **_status_timestamps(payload.data.status, timestamp, current),
    }
    try:
        response = (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .update(values)
            .eq("id", str(entry_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível editar o compromisso.") from exc
    write_audit(
        action="financial_calendar.update",
        target=str(entry_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    saved = (_rows(response.data) or [{**_entry_for_user(entry_id, user), **values}])[0]
    return _public(saved)


@router.post("/entries/{entry_id}/status")
def change_manual_entry_status(
    entry_id: UUID,
    payload: StatusPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    current = _entry_for_user(entry_id, user)
    timestamp = datetime.now(timezone.utc).isoformat()
    values = {
        "status": payload.status,
        "updated_at": timestamp,
        **_status_timestamps(payload.status, timestamp, current),
    }
    try:
        response = (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .update(values)
            .eq("id", str(entry_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível atualizar o status.") from exc
    write_audit(
        action=f"financial_calendar.{payload.status}",
        target=str(entry_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return _public((_rows(response.data) or [{**current, **values}])[0])


@router.get("/entries/{entry_id}/attachment")
def download_manual_attachment(entry_id: UUID, request: Request) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    item = _entry_for_user(entry_id, user)
    if not item.get("attachment_path"):
        raise HTTPException(status_code=404, detail="Este compromisso não possui anexo.")
    try:
        bucket, object_path = validate_private_object_reference(
            bucket=str(item.get("attachment_bucket") or FINANCIAL_ATTACHMENTS_BUCKET),
            object_path=str(item["attachment_path"]),
            expected_bucket=FINANCIAL_ATTACHMENTS_BUCKET,
            organization_id=user["organization_id"],
            namespace="entries",
            owner_id=str(item["id"]),
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
        "filename": item.get("attachment_original_filename"),
        "mime_type": item.get("attachment_mime_type"),
    }


@router.delete("/entries/{entry_id}")
def delete_manual_entry(entry_id: UUID, request: Request) -> dict[str, Any]:
    user = require_roles(request, FINANCE_ROLES)
    item = _entry_for_user(entry_id, user)
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        (
            create_admin_supabase()
            .table("financial_calendar_entries")
            .update(
                {
                    "deleted_at": timestamp,
                    "deleted_by_user_id": user["auth_user_id"],
                    "updated_at": timestamp,
                }
            )
            .eq("id", str(entry_id))
            .eq("organization_id", user["organization_id"])
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Não foi possível excluir o compromisso.") from exc
    cleanup_pending = False
    try:
        if item.get("attachment_path"):
            bucket, object_path = validate_private_object_reference(
                bucket=str(item.get("attachment_bucket") or FINANCIAL_ATTACHMENTS_BUCKET),
                object_path=str(item["attachment_path"]),
                expected_bucket=FINANCIAL_ATTACHMENTS_BUCKET,
                organization_id=user["organization_id"],
                namespace="entries",
                owner_id=str(item["id"]),
            )
            delete_private_object(
                bucket=bucket,
                object_path=object_path,
            )
    except PrivateStorageError as exc:
        cleanup_pending = True
        write_audit(
            action="financial_calendar.delete_attachment",
            target=str(entry_id),
            success=False,
            error_message=str(exc),
            actor_user_id=user["auth_user_id"],
            actor_role_code=user["role_code"],
        )
    write_audit(
        action="financial_calendar.delete",
        target=str(entry_id),
        success=True,
        actor_user_id=user["auth_user_id"],
        actor_role_code=user["role_code"],
    )
    return {"ok": True, "file_cleanup_pending": cleanup_pending}
