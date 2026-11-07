from __future__ import annotations

import hmac
import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from .access_control import ADMIN_ROLES, FINANCE_ROLES, require_roles
from .cora_client import CoraError, cora_client
from .settings import settings
from .system_state import get_system_state, save_system_state


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cora", tags=["Cora"])


def _now() -> str:
    return datetime.now(ZoneInfo(settings.app_timezone)).isoformat()


def _connect() -> sqlite3.Connection:
    path = Path(settings.cora_webhook_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_cora_webhooks() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cora_webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL,
                internal_invoice_id TEXT,
                details_json TEXT,
                error TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cora_events_resource
                ON cora_webhook_events(resource_id, received_at DESC);
            """
        )
        connection.commit()


def _event_status(event_id: str) -> str | None:
    init_cora_webhooks()
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT status FROM cora_webhook_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
    return str(row["status"]) if row else None


def _save_event(
    *,
    event_id: str,
    event_type: str,
    resource_id: str,
    event_status: str,
    internal_invoice_id: Any = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    init_cora_webhooks()
    timestamp = _now()
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO cora_webhook_events (
                event_id, event_type, resource_id, status,
                internal_invoice_id, details_json, error,
                received_at, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                status=excluded.status,
                internal_invoice_id=excluded.internal_invoice_id,
                details_json=excluded.details_json,
                error=excluded.error,
                processed_at=excluded.processed_at
            """,
            (
                event_id,
                event_type,
                resource_id,
                event_status,
                str(internal_invoice_id) if internal_invoice_id is not None else None,
                json.dumps(details, ensure_ascii=False) if details else None,
                error,
                timestamp,
                timestamp if event_status in {"processed", "unmatched", "ignored"} else None,
            ),
        )
        connection.commit()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _customer_document(details: dict[str, Any]) -> str:
    document = (details.get("customer") or {}).get("document")
    if isinstance(document, dict):
        return _digits(document.get("identity"))
    return _digits(document)


def _find_internal_invoice(
    data: dict[str, Any],
    details: dict[str, Any],
) -> dict[str, Any] | None:
    invoices = data.get("invoices")
    if not isinstance(invoices, list):
        return None

    resource_id = str(details.get("id") or "")
    direct = [item for item in invoices if str(item.get("coraInvoiceId") or "") == resource_id]
    if len(direct) == 1:
        return direct[0]

    code = str(details.get("code") or "").strip()
    if code:
        by_code = [
            item
            for item in invoices
            if code
            in {
                str(item.get("id") or ""),
                str(item.get("invoiceNumber") or "").strip(),
                str(item.get("coraCode") or "").strip(),
                f"portfolio-invoice-{item.get('id')}",
            }
        ]
        if len(by_code) == 1:
            return by_code[0]

    amount = int(details.get("total_amount") or 0)
    due_date = str((details.get("payment_terms") or {}).get("due_date") or "")[:10]
    customer_document = _customer_document(details)
    entities = {
        str(item.get("id")): item
        for item in data.get("billingEntities", [])
        if isinstance(item, dict)
    }
    candidates: list[dict[str, Any]] = []
    for item in invoices:
        entity = entities.get(str(item.get("billingEntityId"))) or {}
        same_amount = round(float(item.get("total") or 0) * 100) == amount
        same_due_date = not due_date or str(item.get("dueDate") or "") == due_date
        same_document = not customer_document or _digits(entity.get("cnpj")) == customer_document
        if same_amount and same_due_date and same_document:
            candidates.append(item)
    return candidates[0] if len(candidates) == 1 else None


def apply_cora_invoice(details: dict[str, Any], *, event_id: str) -> dict[str, Any]:
    state = get_system_state()
    data = state.get("data")
    if not isinstance(data, dict):
        return {"matched": False, "reason": "system_state_empty"}

    invoice = _find_internal_invoice(data, details)
    if invoice is None:
        return {"matched": False, "reason": "invoice_not_found"}

    cora_status = str(details.get("status") or "").upper()
    total_paid_cents = int(details.get("total_paid") or 0)
    occurrence_date = str(details.get("occurrence_date") or "")[:10]
    invoice["coraInvoiceId"] = str(details.get("id") or "")
    invoice["coraCode"] = str(details.get("code") or "")
    invoice["coraStatus"] = cora_status
    invoice["coraUpdatedAt"] = _now()
    invoice["coraEventId"] = event_id

    if cora_status == "PAID":
        paid_amount = total_paid_cents / 100 if total_paid_cents else float(invoice.get("total") or 0)
        invoice["status"] = "Paga"
        invoice["paidAmount"] = paid_amount
        invoice["paymentDate"] = occurrence_date or _now()[:10]
        invoice["paymentSource"] = "Cora"
    elif cora_status in {"CANCELLED", "CANCELED"}:
        invoice["status"] = "Cancelada"

    result = save_system_state(
        data,
        source="cora-webhook",
        updated_by="cora",
    )
    return {
        "matched": True,
        "invoice_id": invoice.get("id"),
        "cora_status": cora_status,
        "state_revision": result.get("revision"),
    }


async def _confirm_and_apply(
    *,
    event_id: str,
    event_type: str,
    resource_id: str,
) -> dict[str, Any]:
    details = await cora_client.invoice(resource_id)
    if str(details.get("id") or "") != resource_id:
        raise CoraError("A confirmação da Cora retornou outro boleto.")
    expected_status = event_type.partition(".")[2].upper()
    actual_status = str(details.get("status") or "").upper()
    if expected_status == "CANCELED":
        expected_status = "CANCELLED"
    if expected_status and actual_status != expected_status:
        raise CoraError("O estado informado no evento ainda não foi confirmado pela Cora.")

    result = apply_cora_invoice(details, event_id=event_id)
    event_status = "processed" if result.get("matched") else "unmatched"
    _save_event(
        event_id=event_id,
        event_type=event_type,
        resource_id=resource_id,
        event_status=event_status,
        internal_invoice_id=result.get("invoice_id"),
        details={
            "id": details.get("id"),
            "status": details.get("status"),
            "code": details.get("code"),
            "total_amount": details.get("total_amount"),
            "total_paid": details.get("total_paid"),
            "occurrence_date": details.get("occurrence_date"),
        },
    )
    return result


@router.post("/webhook")
async def receive_cora_webhook(
    token: str = Query(default=""),
    webhook_event_id: str | None = Header(default=None, alias="webhook-event-id"),
    webhook_event_type: str | None = Header(default=None, alias="webhook-event-type"),
    webhook_resource_id: str | None = Header(default=None, alias="webhook-resource-id"),
) -> dict[str, Any]:
    configured_token = settings.cora_webhook_token.strip()
    if not configured_token or not hmac.compare_digest(token, configured_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Webhook da Cora não autorizado.")

    event_id = str(webhook_event_id or "").strip()
    event_type = str(webhook_event_type or "").strip().lower()
    resource_id = str(webhook_resource_id or "").strip()
    if not event_id or len(event_id) > 160:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evento da Cora sem identificador.")
    if event_type not in {"invoice.paid", "invoice.canceled"}:
        _save_event(
            event_id=event_id,
            event_type=event_type or "unknown",
            resource_id=resource_id or "unknown",
            event_status="ignored",
        )
        return {"success": True, "processed": False, "reason": "event_not_used"}
    if not resource_id.startswith("inv_") or len(resource_id) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Boleto da Cora inválido.")
    if _event_status(event_id) == "processed":
        return {"success": True, "processed": True, "duplicate": True}

    try:
        result = await _confirm_and_apply(
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
        )
    except CoraError as exc:
        logger.warning("Falha ao processar webhook da Cora: %s", exc)
        _save_event(
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
            event_status="failed",
            error=str(exc),
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"success": True, "processed": bool(result.get("matched")), **result}


@router.post("/invoices/{invoice_id}/sync")
async def sync_cora_invoice(invoice_id: str, request: Request) -> dict[str, Any]:
    require_roles(request, FINANCE_ROLES)
    event_id = f"manual-{uuid4()}"
    try:
        details = await cora_client.invoice(invoice_id)
    except CoraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    result = apply_cora_invoice(details, event_id=event_id)
    if not result.get("matched"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Nenhuma cobrança interna corresponde a este boleto da Cora.",
        )
    return {"ok": True, **result}


@router.post("/webhook/register")
async def register_cora_webhook(request: Request) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    public_url = settings.cora_webhook_public_url.strip()
    webhook_token = settings.cora_webhook_token.strip()
    if not public_url.startswith("https://") or not webhook_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A URL pública ou o segredo do webhook da Cora não está configurado.",
        )
    endpoint_url = f"{public_url}?token={quote(webhook_token, safe='')}"
    idempotency_key = str(uuid5(NAMESPACE_URL, f"{endpoint_url}|invoice|paid"))
    try:
        result = await cora_client.create_endpoint(
            url=endpoint_url,
            resource="invoice",
            trigger="paid",
            idempotency_key=idempotency_key,
        )
    except CoraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {
        "ok": True,
        "resource": "invoice",
        "trigger": "paid",
        "endpoint_id": result.get("id"),
        "active": result.get("active", True),
    }


@router.get("/health")
def cora_health(request: Request) -> dict[str, Any]:
    require_roles(request, FINANCE_ROLES)
    init_cora_webhooks()
    with closing(_connect()) as connection:
        counts = {
            str(row["status"]): int(row["total"])
            for row in connection.execute(
                "SELECT status, COUNT(*) AS total FROM cora_webhook_events GROUP BY status"
            ).fetchall()
        }
    return {
        "ok": True,
        "enabled": settings.cora_enabled,
        "environment": settings.cora_environment,
        "webhook_token_configured": bool(settings.cora_webhook_token.strip()),
        "events": counts,
    }
