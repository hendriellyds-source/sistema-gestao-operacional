from __future__ import annotations

from datetime import date
from typing import Any

from .document_storage import list_documents as list_stored_documents
from .operations import (
    list_documents as list_vehicle_documents,
    list_calendar_maintenance,
    list_vehicle_debts,
)
from .supabase_client import create_admin_supabase
from .system_state import get_system_state
from .time_utils import local_today
from .demo_cleanup import is_demo_identifier, is_demo_vehicle_record


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _date_in_range(value: Any, date_from: date, date_to: date) -> str | None:
    text = str(value or "")[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return text if date_from <= parsed <= date_to else None


def _deadline_status(
    value: str,
    current_status: Any = None,
    *,
    today: date | None = None,
) -> str:
    normalized = str(current_status or "").strip().lower()
    aliases = {
        "pendente": "pending",
        "pending": "pending",
        "agendada": "scheduled",
        "agendado": "scheduled",
        "scheduled": "scheduled",
        "paga": "paid",
        "pago": "paid",
        "paid": "paid",
        "cancelada": "cancelled",
        "cancelado": "cancelled",
        "cancelled": "cancelled",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"paid", "cancelled"}:
        return normalized
    try:
        current = today or local_today()
        return (
            "overdue"
            if date.fromisoformat(value) < current
            else (normalized or "pending")
        )
    except ValueError:
        return normalized or "pending"


def _event(
    *,
    source_type: str,
    source_id: Any,
    title: str,
    event_date: str,
    category: str,
    status: str,
    url: str,
    amount: Any = None,
    employee_id: Any = None,
    vehicle_id: Any = None,
    automatic: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    item = {
        "id": f"{source_type}:{source_id}:{event_date}",
        "source_type": source_type,
        "source_id": str(source_id),
        "title": title,
        "date": event_date,
        "category": category,
        "status": status,
        "amount": amount,
        "employee_id": employee_id,
        "vehicle_id": vehicle_id,
        "url": url,
        "automatic": automatic,
    }
    item.update(extra)
    return item


def _system_state_events(
    state: dict[str, Any],
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    employees = {
        int(item.get("id") or 0): str(item.get("name") or "Funcionário")
        for item in state.get("employees", [])
        if isinstance(item, dict)
    }
    vehicles: dict[int, str] = {}
    demo_vehicle_ids: set[int] = set()
    for item in state.get("vehicles", []):
        if not isinstance(item, dict):
            continue
        try:
            vehicle_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if is_demo_vehicle_record(item):
            demo_vehicle_ids.add(vehicle_id)
            continue
        vehicles[vehicle_id] = str(
            item.get("plate") or item.get("identifier") or "Veículo"
        )

    for item in state.get("exams", []):
        if not isinstance(item, dict):
            continue
        event_date = _date_in_range(item.get("due"), date_from, date_to)
        if not event_date:
            continue
        employee_id = item.get("employeeId")
        events.append(
            _event(
                source_type="employee_exam",
                source_id=item.get("id"),
                title=f"Exame: {employees.get(int(employee_id or 0), 'Funcionário')}",
                event_date=event_date,
                category="employee",
                status=_deadline_status(event_date, item.get("status")),
                employee_id=employee_id,
                url=f"/rh/employees?employee_id={employee_id}&tab=exams",
            )
        )

    for item in state.get("maintenances", []):
        if not isinstance(item, dict) or str(item.get("status") or "").lower() in {
            "cancelada",
            "cancelado",
        } or is_demo_vehicle_record(item):
            continue
        event_date = _date_in_range(item.get("scheduledDate"), date_from, date_to)
        if not event_date:
            continue
        vehicle_id = item.get("vehicleId")
        if str(vehicle_id or "").isdigit() and int(vehicle_id) in demo_vehicle_ids:
            continue
        events.append(
            _event(
                source_type="maintenance",
                source_id=item.get("id"),
                title=f"Manutenção: {vehicles.get(int(vehicle_id or 0), 'Veículo')} — {item.get('type') or 'Programada'}",
                event_date=event_date,
                category="maintenance",
                status=_deadline_status(event_date, item.get("status")),
                amount=item.get("cost"),
                vehicle_id=vehicle_id,
                url="/fleet?section=costs",
            )
        )

    fine_dates = (
        ("driverDeadline", "Indicação de condutor"),
        ("defenseDeadline", "Defesa de multa"),
        ("appealDeadline", "Recurso de multa"),
        ("dueDate", "Vencimento de multa"),
    )
    for item in state.get("fines", []):
        if not isinstance(item, dict) or is_demo_vehicle_record(item):
            continue
        vehicle_id = item.get("vehicleId")
        if str(vehicle_id or "").isdigit() and int(vehicle_id) in demo_vehicle_ids:
            continue
        vehicle_name = vehicles.get(int(vehicle_id or 0), "Veículo")
        for key, label in fine_dates:
            event_date = _date_in_range(item.get(key), date_from, date_to)
            if not event_date:
                continue
            events.append(
                _event(
                    source_type="vehicle_fine",
                    source_id=f"{item.get('id')}:{key}",
                    title=f"{label}: {vehicle_name} — {item.get('aitNumber') or 'multa'}",
                    event_date=event_date,
                    category="vehicle",
                    status=_deadline_status(event_date, item.get("status")),
                    amount=item.get("originalAmount"),
                    vehicle_id=vehicle_id,
                    url="/fleet?section=debts",
                )
            )

    for item in state.get("fleetDocuments", []):
        if not isinstance(item, dict) or is_demo_vehicle_record(item):
            continue
        event_date = _date_in_range(
            item.get("expiry_date") or item.get("expiryDate"), date_from, date_to
        )
        if event_date:
            identifier = item.get("identifier") or item.get("vehicleId")
            if is_demo_identifier(identifier):
                continue
            events.append(
                _event(
                    source_type="vehicle_document",
                    source_id=item.get("id"),
                    title=f"Documento: {identifier} — {item.get('document_type') or item.get('documentType') or 'Vencimento'}",
                    event_date=event_date,
                    category="document",
                    status=_deadline_status(event_date, item.get("status")),
                    vehicle_id=identifier,
                    url="/fleet?section=documents",
                )
            )

    for item in state.get("invoices", []):
        if not isinstance(item, dict):
            continue
        event_date = _date_in_range(item.get("dueDate"), date_from, date_to)
        if event_date:
            events.append(
                _event(
                    source_type="financial_invoice",
                    source_id=item.get("id"),
                    title=f"Cobrança: {item.get('invoiceNumber') or item.get('description') or 'vencimento'}",
                    event_date=event_date,
                    category="supplier",
                    status=_deadline_status(event_date, item.get("status")),
                    amount=item.get("total"),
                    url="/finance/calendar?origin=invoices",
                )
            )

    # Forward-compatible aggregation for payable/scheduled-payment arrays that may
    # already exist in a production system_state created by a later minor version.
    for key, source_type, title_prefix in (
        ("accountsPayable", "account_payable", "Conta a pagar"),
        ("scheduledPayments", "scheduled_payment", "Pagamento agendado"),
        ("payments", "payment", "Pagamento"),
    ):
        for item in state.get(key, []):
            if not isinstance(item, dict):
                continue
            event_date = _date_in_range(
                item.get("dueDate") or item.get("date") or item.get("scheduledDate"),
                date_from,
                date_to,
            )
            if event_date:
                events.append(
                    _event(
                        source_type=source_type,
                        source_id=item.get("id"),
                        title=f"{title_prefix}: {item.get('title') or item.get('supplier') or item.get('description') or 'compromisso'}",
                        event_date=event_date,
                        category=str(item.get("category") or "supplier").lower(),
                        status=_deadline_status(event_date, item.get("status")),
                        amount=item.get("amount") or item.get("value"),
                        url="/finance/calendar",
                    )
                )
    return events


def _sqlite_fleet_events(date_from: date, date_to: date) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_vehicle_debts(include_paid=True):
        if is_demo_vehicle_record(item):
            continue
        event_date = _date_in_range(item.get("due_date"), date_from, date_to)
        if event_date:
            events.append(
                _event(
                    source_type="vehicle_debt",
                    source_id=item.get("id"),
                    title=f"Débito: {item.get('identifier')} — {item.get('description')}",
                    event_date=event_date,
                    category="vehicle",
                    status=_deadline_status(event_date, item.get("status")),
                    amount=item.get("amount"),
                    vehicle_id=item.get("identifier"),
                    url=f"/fleet?section=debts&vehicle={item.get('identifier')}",
                )
            )
    for item in list_vehicle_documents():
        if is_demo_vehicle_record(item):
            continue
        event_date = _date_in_range(item.get("expiry_date"), date_from, date_to)
        if event_date:
            events.append(
                _event(
                    source_type="vehicle_document",
                    source_id=item.get("id"),
                    title=f"Documento: {item.get('identifier')} — {item.get('document_type')}",
                    event_date=event_date,
                    category="document",
                    status=_deadline_status(event_date, item.get("status")),
                    amount=item.get("value"),
                    vehicle_id=item.get("identifier"),
                    url=f"/fleet?section=documents&vehicle={item.get('identifier')}",
                )
            )
    for item in list_stored_documents(owner_type="vehicle"):
        if is_demo_identifier(item.get("owner_key")) or is_demo_vehicle_record(item):
            continue
        event_date = _date_in_range(item.get("expiry_date"), date_from, date_to)
        if event_date:
            events.append(
                _event(
                    source_type="stored_vehicle_document",
                    source_id=item.get("id"),
                    title=f"Arquivo: {item.get('owner_name') or item.get('owner_key')} — {item.get('document_type')}",
                    event_date=event_date,
                    category="document",
                    status=_deadline_status(event_date),
                    vehicle_id=item.get("owner_key"),
                    url=f"/fleet?section=documents&vehicle={item.get('owner_key')}",
                )
            )
    # Query by entry_date to retain scheduled maintenance records already modeled
    # in the existing operational database. next_date is also emitted when present.
    for item in list_calendar_maintenance(date_from.isoformat(), date_to.isoformat()):
        if is_demo_vehicle_record(item):
            continue
        for key, label in (("entry_date", "Manutenção"), ("next_date", "Próxima manutenção")):
            event_date = _date_in_range(item.get(key), date_from, date_to)
            if event_date:
                events.append(
                    _event(
                        source_type="maintenance",
                        source_id=f"{item.get('id')}:{key}",
                        title=f"{label}: {item.get('identifier')} — {item.get('description')}",
                        event_date=event_date,
                        category="maintenance",
                        status=_deadline_status(event_date, item.get("status")),
                        amount=item.get("value"),
                        vehicle_id=item.get("identifier"),
                        url=f"/fleet?section=costs&vehicle={item.get('identifier')}",
                    )
                )
    return events


def _supabase_events(
    organization_id: str,
    date_from: date,
    date_to: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        client = create_admin_supabase()
    except Exception:
        return events, [
            "Certificados e compromissos manuais indisponíveis: configure o Supabase e aplique a migração."
        ]
    try:
        response = (
            client.table("employee_certificates")
            .select("id,employee_id,course_name,expiration_date,no_expiration")
            .eq("organization_id", organization_id)
            .eq("no_expiration", False)
            .is_("deleted_at", "null")
            .gte("expiration_date", date_from.isoformat())
            .lte("expiration_date", date_to.isoformat())
            .execute()
        )
        for item in _rows(response.data):
            event_date = str(item["expiration_date"])[:10]
            events.append(
                _event(
                    source_type="employee_certificate",
                    source_id=item["id"],
                    title=f"Certificado: {item.get('course_name')}",
                    event_date=event_date,
                    category="employee",
                    status=_deadline_status(event_date),
                    employee_id=item.get("employee_id"),
                    url=f"/rh/employees?employee_id={item.get('employee_id')}&tab=certificates",
                )
            )
    except Exception:
        warnings.append("Certificados indisponíveis até aplicar a migração do Supabase.")

    try:
        response = (
            client.table("financial_calendar_entries")
            .select("*")
            .eq("organization_id", organization_id)
            .is_("deleted_at", "null")
            .gte("due_date", date_from.isoformat())
            .lte("due_date", date_to.isoformat())
            .execute()
        )
        for item in _rows(response.data):
            event_date = str(item["due_date"])[:10]
            status = _deadline_status(event_date, item.get("status"))
            events.append(
                _event(
                    source_type="manual",
                    source_id=item["id"],
                    title=str(item.get("title") or "Compromisso"),
                    event_date=event_date,
                    category=str(item.get("category") or "other"),
                    status=status,
                    amount=item.get("amount"),
                    employee_id=item.get("employee_id"),
                    vehicle_id=item.get("vehicle_id"),
                    url=f"/finance/calendar?entry_id={item['id']}",
                    automatic=False,
                    supplier=item.get("supplier"),
                    payment_method=item.get("payment_method"),
                    notes=item.get("notes"),
                    has_attachment=bool(item.get("attachment_path")),
                    recurrence_group_id=item.get("recurrence_group_id"),
                    recurrence_sequence=item.get("recurrence_sequence"),
                )
            )
    except Exception:
        warnings.append("Compromissos manuais indisponíveis até aplicar a migração do Supabase.")
    return events, warnings


def aggregate_calendar(
    *,
    organization_id: str,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    state = get_system_state().get("data") or {}
    events = _system_state_events(state, date_from, date_to)
    events.extend(_sqlite_fleet_events(date_from, date_to))
    supabase_events, warnings = _supabase_events(
        organization_id, date_from, date_to
    )
    events.extend(supabase_events)
    # Multiple views can refer to the same SQLite source. Keep the unified ID stable
    # and de-duplicate without copying automatic events into another table.
    unique: dict[str, dict[str, Any]] = {}
    for item in events:
        unique.setdefault(str(item["id"]), item)
    return {
        "events": sorted(
            unique.values(), key=lambda item: (item["date"], item["title"])
        ),
        "warnings": warnings,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }
