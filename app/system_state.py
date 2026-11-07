from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .settings import settings
from .sqlite_connection import ClosingConnection
from .access_control import ADMIN_ROLES, SYSTEM_STATE_ROLES, normalized_role, require_roles
from .demo_cleanup import cleanup_system_state_data


router = APIRouter(prefix="/api/system-state", tags=["Estado central do sistema"])


FINANCIAL_STATE_KEYS = frozenset(
    {
        "settings",
        "employees",
        "employeeImportVersion",
        "clinics",
        "exams",
        "vacations",
        "leaves",
        "vehicles",
        "maintenances",
        "maintenanceHistory",
        "maintenanceDrafts",
        "fines",
        "companies",
        "works",
        "workTeams",
        "initialWorkTeamsCreated",
        "workScheduleEntries",
        "billingEntities",
        "invoices",
        "events",
        "fleetVehicles",
        "fleetHiddenVehicles",
        "fleetGeofences",
        "fleetTrips",
        "fleetStops",
        "fleetDrivingEvents",
        "fleetFuel",
        "fleetExpenses",
        "fleetDocuments",
        "fleetDebts",
        "fleetSettings",
    }
)
HELENA_STATE_KEYS = frozenset(
    {
        "settings",
        "employees",
        "vacations",
        "companies",
        "works",
        "workTeams",
        "workScheduleEntries",
        "projectClients",
        "projectScheduleRequests",
        "employeeAppFiles",
    }
)
ALINE_STATE_KEYS = frozenset(
    {"settings", "companies", "works", "alineWorks", "alineProcesses"}
)


def allowed_state_keys(role_code: str) -> frozenset[str] | None:
    if role_code in {"admin", "director"}:
        return None
    if role_code == "financial":
        return FINANCIAL_STATE_KEYS
    if role_code == "helena":
        return HELENA_STATE_KEYS
    if role_code == "aline":
        return ALINE_STATE_KEYS
    return frozenset()


def filter_state_for_user(
    state: dict[str, Any],
    user: dict[str, Any],
) -> dict[str, Any]:
    keys = allowed_state_keys(normalized_role(user))
    result = dict(state)
    data = result.get("data")
    if keys is not None and isinstance(data, dict):
        result["data"] = {key: value for key, value in data.items() if key in keys}
    return result


def merge_state_for_user(
    incoming: dict[str, Any],
    user: dict[str, Any],
) -> dict[str, Any]:
    current = get_system_state().get("data") or {}
    incoming = _preserve_cora_confirmations(incoming, current)
    keys = allowed_state_keys(normalized_role(user))
    if keys is None:
        return incoming
    merged = dict(current) if isinstance(current, dict) else {}
    writable_keys = keys - {"settings"}
    for key in writable_keys:
        if key in incoming:
            merged[key] = incoming[key]
    return merged


def _preserve_cora_confirmations(
    incoming: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Impede que uma aba antiga desfaça pagamentos confirmados pela Cora."""
    if not isinstance(incoming, dict) or not isinstance(current, dict):
        return incoming
    incoming_invoices = incoming.get("invoices")
    current_invoices = current.get("invoices")
    if not isinstance(incoming_invoices, list) or not isinstance(current_invoices, list):
        return incoming

    by_id = {str(item.get("id")): item for item in incoming_invoices if isinstance(item, dict)}
    by_cora_id = {
        str(item.get("coraInvoiceId")): item
        for item in incoming_invoices
        if isinstance(item, dict) and item.get("coraInvoiceId")
    }
    protected_fields = (
        "status",
        "paidAmount",
        "paymentDate",
        "paymentSource",
        "coraInvoiceId",
        "coraCode",
        "coraStatus",
        "coraUpdatedAt",
        "coraEventId",
    )
    for confirmed in current_invoices:
        if not isinstance(confirmed, dict):
            continue
        if confirmed.get("paymentSource") != "Cora" or confirmed.get("status") != "Paga":
            continue
        target = by_cora_id.get(str(confirmed.get("coraInvoiceId"))) or by_id.get(
            str(confirmed.get("id"))
        )
        if not target:
            incoming_invoices.append(dict(confirmed))
            continue
        for field in protected_fields:
            target[field] = confirmed.get(field)
    return incoming


class SystemStatePayload(BaseModel):
    data: dict[str, Any]
    client_revision: int | None = None
    source: str = "navegador"


class SystemStateRestorePayload(BaseModel):
    revision: int
    expected_current_revision: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = Path(settings.system_state_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def init_system_state() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                revision INTEGER NOT NULL,
                source TEXT,
                updated_by TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS system_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                revision INTEGER NOT NULL,
                source TEXT,
                updated_by TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_system_state_history_revision
                ON system_state_history(revision DESC);
            """
        )
        connection.commit()


def get_system_state() -> dict[str, Any]:
    init_system_state()
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if not row:
            return {
                "has_state": False,
                "revision": 0,
                "updated_at": None,
                "data": None,
            }
        try:
            data = json.loads(row["state_json"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("O estado central do sistema está inválido.") from exc
        return {
            "has_state": True,
            "revision": int(row["revision"]),
            "updated_at": row["updated_at"],
            "source": row["source"],
            "data": data,
        }


def _state_summary(data: dict[str, Any]) -> dict[str, int]:
    keys = (
        "employees",
        "companies",
        "works",
        "projectClients",
        "invoices",
        "exams",
        "fleetVehicles",
        "workScheduleEntries",
    )
    return {
        key: len(data.get(key) or [])
        for key in keys
        if isinstance(data.get(key), list)
    }


def list_system_state_versions(limit: int = 15) -> dict[str, Any]:
    init_system_state()
    safe_limit = max(1, min(int(limit), 15))
    with closing(_connect()) as connection:
        current = connection.execute(
            "SELECT revision, source, updated_by, updated_at, state_json FROM system_state WHERE id=1"
        ).fetchone()
        rows = connection.execute(
            """
            SELECT revision, source, updated_by, created_at, state_json
            FROM system_state_history
            ORDER BY revision DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    def metadata(row: sqlite3.Row, timestamp_key: str) -> dict[str, Any]:
        try:
            parsed = json.loads(row["state_json"])
        except (TypeError, ValueError):
            parsed = {}
        return {
            "revision": int(row["revision"]),
            "source": row["source"],
            "updated_by": row["updated_by"],
            "created_at": row[timestamp_key],
            "summary": _state_summary(parsed if isinstance(parsed, dict) else {}),
        }

    return {
        "current": metadata(current, "updated_at") if current else None,
        "history": [metadata(row, "created_at") for row in rows],
    }


def restore_system_state_revision(
    *,
    revision: int,
    expected_current_revision: int,
    updated_by: str,
) -> dict[str, Any]:
    """Atomically restore an archived state while retaining the current state as backup."""
    init_system_state()
    timestamp = _now()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if not current:
            raise ValueError("O sistema ainda não possui uma versão atual para restaurar.")
        current_revision = int(current["revision"])
        if current_revision != int(expected_current_revision):
            raise RuntimeError(
                f"A versão atual mudou de {expected_current_revision} para {current_revision}. Atualize o histórico antes de restaurar."
            )
        target = connection.execute(
            "SELECT * FROM system_state_history WHERE revision=? ORDER BY id DESC LIMIT 1",
            (int(revision),),
        ).fetchone()
        if not target:
            raise LookupError("A versão solicitada não está mais disponível no histórico.")
        try:
            target_data = json.loads(target["state_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError("A versão selecionada está inválida.") from exc
        if not isinstance(target_data, dict) or not target_data:
            raise ValueError("A versão selecionada não contém dados válidos.")

        new_revision = current_revision + 1
        connection.execute(
            """
            INSERT INTO system_state_history
                (state_json, state_hash, revision, source, updated_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                current["state_json"],
                current["state_hash"],
                current_revision,
                current["source"],
                current["updated_by"],
                current["updated_at"],
            ),
        )
        connection.execute(
            """
            UPDATE system_state
            SET state_json=?, state_hash=?, revision=?, source=?, updated_by=?, updated_at=?
            WHERE id=1
            """,
            (
                target["state_json"],
                target["state_hash"],
                new_revision,
                f"manual-history-restore:{int(revision)}",
                updated_by[:120],
                timestamp,
            ),
        )
        connection.execute(
            """
            DELETE FROM system_state_history
            WHERE id NOT IN (
                SELECT id FROM system_state_history ORDER BY revision DESC LIMIT 15
            )
            """
        )
        connection.commit()
    return {
        "ok": True,
        "revision": new_revision,
        "restored_from_revision": int(revision),
        "updated_at": timestamp,
        "summary": _state_summary(target_data),
    }


def save_system_state(
    data: dict[str, Any],
    *,
    source: str,
    updated_by: str,
    client_revision: int | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        raise ValueError("O estado do sistema não pode ficar vazio.")
    from .historical_pricing import refresh_historical_prices
    refresh_historical_prices(data)
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    timestamp = _now()
    init_system_state()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if current:
            # An open workspace tab may have loaded its full state before the
            # tablet published a new appointment. Preserve that appointment.
            previous = json.loads(current["state_json"])
            incoming_entries = data.get("workScheduleEntries")
            previous_entries = previous.get("workScheduleEntries")
            if isinstance(incoming_entries, list) and isinstance(previous_entries, list):
                has_tombstones = connection.execute("""SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='tablet_schedule_tombstone'""").fetchone()
                deleted = {row["review_id"] for row in connection.execute(
                    "SELECT review_id FROM tablet_schedule_tombstone")} if has_tombstones else set()
                incoming_entries[:] = [entry for entry in incoming_entries
                                       if not isinstance(entry, dict) or
                                       str(entry.get("tabletAgendaId") or "") not in deleted]
                previous_tablet = {str(entry["tabletAgendaId"]): entry for entry in previous_entries
                                   if isinstance(entry, dict) and entry.get("tabletAgendaId")}
                stale = client_revision is None or client_revision < int(current["revision"])
                if stale:
                    incoming_entries[:] = [previous_tablet.get(str(entry.get("tabletAgendaId")), entry)
                                           if isinstance(entry, dict) and entry.get("tabletAgendaId")
                                           else entry for entry in incoming_entries]
                known = {str(x.get("tabletAgendaId")) for x in incoming_entries
                         if isinstance(x, dict) and x.get("tabletAgendaId")}
                for entry in previous_entries:
                    if (isinstance(entry, dict) and entry.get("tabletAgendaId")
                            and str(entry["tabletAgendaId"]) not in known
                            and str(entry["tabletAgendaId"]) not in deleted and stale):
                        incoming_entries.append(entry)
                        known.add(str(entry["tabletAgendaId"]))
                if not stale and has_tombstones:
                    removed = set(previous_tablet) - known - deleted
                    for review_id in removed:
                        connection.execute("INSERT OR REPLACE INTO tablet_schedule_tombstone(review_id,deleted_at) VALUES (?,?)",
                                           (review_id, timestamp))
                        connection.execute("UPDATE tablet_review SET schedule_id=NULL,text_original='',unregistered_work_text='' WHERE id=?",
                                           (review_id,))
                serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if current and current["state_hash"] == digest:
            return {
                "ok": True,
                "changed": False,
                "revision": int(current["revision"]),
                "updated_at": current["updated_at"],
            }
        revision = int(current["revision"]) + 1 if current else 1
        if current:
            connection.execute(
                """
                INSERT INTO system_state_history
                    (state_json, state_hash, revision, source, updated_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    current["state_json"],
                    current["state_hash"],
                    current["revision"],
                    current["source"],
                    current["updated_by"],
                    current["updated_at"],
                ),
            )
        connection.execute(
            """
            INSERT INTO system_state
                (id, state_json, state_hash, revision, source, updated_by, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state_json=excluded.state_json,
                state_hash=excluded.state_hash,
                revision=excluded.revision,
                source=excluded.source,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
            """,
            (serialized, digest, revision, source[:80], updated_by[:120], timestamp),
        )
        connection.execute(
            """
            DELETE FROM system_state_history
            WHERE id NOT IN (
                SELECT id FROM system_state_history ORDER BY revision DESC LIMIT 15
            )
            """
        )
        connection.commit()
    return {
        "ok": True,
        "changed": True,
        "revision": revision,
        "updated_at": timestamp,
    }


@router.get("")
def read_state(request: Request) -> dict[str, Any]:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    return filter_state_for_user(get_system_state(), user)


@router.put("")
def write_state(
    payload: SystemStatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    try:
        return save_system_state(
            merge_state_for_user(payload.data, user),
            source=payload.source,
            updated_by=str(user.get("auth_user_id") or user.get("profile_id") or ""),
            client_revision=payload.client_revision,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/history")
def read_state_history(request: Request) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    return list_system_state_versions()


@router.post("/restore")
def restore_state(payload: SystemStateRestorePayload, request: Request) -> dict[str, Any]:
    user = require_roles(request, ADMIN_ROLES)
    updated_by = str(user.get("auth_user_id") or user.get("profile_id") or "")
    try:
        return restore_system_state_revision(
            revision=payload.revision,
            expected_current_revision=payload.expected_current_revision,
            updated_by=updated_by,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def state_health() -> dict[str, Any]:
    state = get_system_state()
    return {
        "ok": True,
        "has_state": state["has_state"],
        "revision": state["revision"],
        "updated_at": state["updated_at"],
        "history_limit": 15,
    }


def purge_demo_system_state() -> dict[str, int]:
    """Remove apenas os registros fictícios conhecidos, preservando o histórico."""
    state = get_system_state()
    current = state.get("data")
    if not isinstance(current, dict):
        return {}
    cleaned, stats = cleanup_system_state_data(current)
    if stats:
        save_system_state(
            cleaned,
            source="migration-demo-cleanup-v1",
            updated_by="system",
        )
    return stats


@router.get("/health")
def state_health_endpoint(request: Request) -> dict[str, Any]:
    require_roles(request, SYSTEM_STATE_ROLES)
    return state_health()
