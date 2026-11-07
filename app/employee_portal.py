from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .settings import settings
from .sqlite_connection import ClosingConnection


_LOCK = threading.RLock()
_ALLOWED_SYNC_KEYS = {
    "employees",
    "workTeams",
    "companies",
    "works",
    "clinics",
    "workScheduleEntries",
    "projectClients",
    "exams",
    "employeeAppFiles",
}


def _db_path() -> Path:
    return Path(getattr(settings, "employee_portal_db_path", "data/employee_portal.db"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utc_now().isoformat()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    iterations = 210_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iteration_text),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _normalize_username(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _username_for_employee(employee: dict[str, Any]) -> str:
    explicit = _normalize_username(str(employee.get("appLogin") or ""))
    if explicit:
        return explicit
    parts = [part for part in str(employee.get("name") or "").strip().lower().split() if part]
    if not parts:
        return f"funcionario.{employee.get('id', 'novo')}"
    return ".".join(parts[:2])


def _plus_days(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def default_portal_state() -> dict[str, Any]:
    if settings.demo_mode:
        from .demo_seed import build_demo_state
        return build_demo_state()
    return {key: [] for key in ("employees", "workTeams", "companies", "works", "clinics", "workScheduleEntries", "projectClients", "exams", "employeeAppFiles")}



def _strip_passwords(state: dict[str, Any]) -> None:
    employees = state.get("employees") if isinstance(state.get("employees"), list) else []
    for employee in employees:
        if isinstance(employee, dict):
            employee.pop("appPassword", None)


def init_employee_portal_db() -> None:
    with _LOCK, _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS portal_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employee_credentials (
                employee_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portal_sessions (
                token TEXT PRIMARY KEY,
                employee_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_portal_sessions_employee
                ON portal_sessions(employee_id);
            """
        )
        existing = connection.execute("SELECT id FROM portal_state WHERE id = 1").fetchone()
        if existing is None:
            state = default_portal_state()
            _sync_credentials(connection, state.get("employees", []))
            _strip_passwords(state)
            connection.execute(
                "INSERT INTO portal_state(id, payload_json, updated_at) VALUES(1, ?, ?)",
                (json.dumps(state, ensure_ascii=False), _now_iso()),
            )
        else:
            state = _load_state(connection)
            before = json.dumps(state, ensure_ascii=False, sort_keys=True)
            _strip_passwords(state)
            if json.dumps(state, ensure_ascii=False, sort_keys=True) != before:
                _save_state(connection, state)
        connection.commit()


def _load_state(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT payload_json FROM portal_state WHERE id = 1").fetchone()
    if row is None:
        return default_portal_state()
    try:
        result = json.loads(row["payload_json"])
        return result if isinstance(result, dict) else default_portal_state()
    except json.JSONDecodeError:
        return default_portal_state()


def load_portal_state() -> dict[str, Any]:
    with _LOCK, _connect() as connection:
        return _load_state(connection)


def _save_state(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    state["serverUpdatedAt"] = _now_iso()
    connection.execute(
        "INSERT INTO portal_state(id, payload_json, updated_at) VALUES(1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
        (json.dumps(state, ensure_ascii=False), state["serverUpdatedAt"]),
    )


def _sync_credentials(connection: sqlite3.Connection, employees: list[Any]) -> None:
    active_ids: set[int] = set()
    for raw in employees:
        if not isinstance(raw, dict):
            continue
        try:
            employee_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        active_ids.add(employee_id)
        username = _username_for_employee(raw)
        password = str(raw.get("appPassword") or "")
        status_active = str(raw.get("status") or "Ativo").strip().lower() not in {
            "inativo",
            "desligado",
            "demitido",
        }
        existing = connection.execute(
            "SELECT password_hash FROM employee_credentials WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if password:
            password_hash = _hash_password(password)
        elif existing is not None:
            password_hash = existing["password_hash"]
        else:
            password_hash = _hash_password("1234")
        connection.execute(
            """
            INSERT INTO employee_credentials(employee_id, username, password_hash, active, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                username=excluded.username,
                password_hash=excluded.password_hash,
                active=excluded.active,
                updated_at=excluded.updated_at
            """,
            (employee_id, username, password_hash, 1 if status_active else 0, _now_iso()),
        )
    if active_ids:
        placeholders = ",".join("?" for _ in active_ids)
        connection.execute(
            f"UPDATE employee_credentials SET active = 0 WHERE employee_id NOT IN ({placeholders})",
            tuple(sorted(active_ids)),
        )


def _merge_message_lists(incoming: list[Any], current: list[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source in (incoming, current):
        for raw in source or []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("id") or f"{raw.get('createdAt')}|{raw.get('senderName')}|{raw.get('text')}")
            if key not in merged:
                merged[key] = dict(raw)
            else:
                old_read = merged[key].get("readBy") if isinstance(merged[key].get("readBy"), list) else []
                new_read = raw.get("readBy") if isinstance(raw.get("readBy"), list) else []
                merged[key].update(raw)
                merged[key]["readBy"] = list(dict.fromkeys([*old_read, *new_read]))
    return sorted(merged.values(), key=lambda item: str(item.get("createdAt") or ""))


def _merge_state(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key in _ALLOWED_SYNC_KEYS:
        value = incoming.get(key)
        if isinstance(value, list):
            result[key] = value

    current_clients = {
        str(item.get("id")): item
        for item in current.get("projectClients", [])
        if isinstance(item, dict)
    }
    for client in result.get("projectClients", []):
        if not isinstance(client, dict):
            continue
        existing = current_clients.get(str(client.get("id")), {})
        client["teamMessages"] = _merge_message_lists(
            client.get("teamMessages") if isinstance(client.get("teamMessages"), list) else [],
            existing.get("teamMessages") if isinstance(existing.get("teamMessages"), list) else [],
        )
        if not isinstance(client.get("teamFiles"), list):
            client["teamFiles"] = existing.get("teamFiles", []) if isinstance(existing.get("teamFiles"), list) else []

    incoming_files = result.get("employeeAppFiles", []) if isinstance(result.get("employeeAppFiles"), list) else []
    current_files = current.get("employeeAppFiles", []) if isinstance(current.get("employeeAppFiles"), list) else []
    file_map: dict[str, dict[str, Any]] = {}
    for source in (incoming_files, current_files):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("id") or f"{raw.get('createdAt')}|{raw.get('employeeId')}|{raw.get('name')}")
            if key not in file_map:
                file_map[key] = dict(raw)
            else:
                file_map[key].update(raw)
    result["employeeAppFiles"] = sorted(file_map.values(), key=lambda item: str(item.get("createdAt") or ""))
    return result


def sync_portal_state(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = {key: payload.get(key) for key in _ALLOWED_SYNC_KEYS if isinstance(payload.get(key), list)}
    with _LOCK, _connect() as connection:
        current = _load_state(connection)
        merged = _merge_state(safe_payload, current)
        _sync_credentials(connection, merged.get("employees", []))
        _strip_passwords(merged)
        _save_state(connection, merged)
        connection.commit()
        return admin_state_summary(merged)


def admin_state_summary(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or load_portal_state()
    return {
        "ok": True,
        "updatedAt": state.get("serverUpdatedAt"),
        "employees": len(state.get("employees", [])),
        "teams": len(state.get("workTeams", [])),
        "scheduleEntries": len(state.get("workScheduleEntries", [])),
        "messages": sum(
            len(client.get("teamMessages", []))
            for client in state.get("projectClients", [])
            if isinstance(client, dict)
        ),
        "files": len(state.get("employeeAppFiles", [])),
    }


def authenticate_employee(username: str, password: str) -> tuple[str, int] | None:
    username = _normalize_username(username)
    if not username or not password:
        return None
    with _LOCK, _connect() as connection:
        connection.execute("DELETE FROM portal_sessions WHERE expires_at <= ?", (_now_iso(),))
        row = connection.execute(
            "SELECT employee_id, password_hash, active FROM employee_credentials WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None or not row["active"] or not _verify_password(password, row["password_hash"]):
            connection.commit()
            return None
        token = secrets.token_urlsafe(32)
        expires = _utc_now() + timedelta(hours=int(getattr(settings, "employee_session_hours", 24)))
        connection.execute(
            "INSERT INTO portal_sessions(token, employee_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
            (token, int(row["employee_id"]), _now_iso(), expires.isoformat()),
        )
        connection.commit()
        return token, int(row["employee_id"])


def employee_for_token(token: str) -> int | None:
    if not token:
        return None
    with _LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT employee_id, expires_at FROM portal_sessions WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            return None
        if str(row["expires_at"]) <= _now_iso():
            connection.execute("DELETE FROM portal_sessions WHERE token = ?", (token,))
            connection.commit()
            return None
        return int(row["employee_id"])


def logout_token(token: str) -> None:
    if not token:
        return
    with _LOCK, _connect() as connection:
        connection.execute("DELETE FROM portal_sessions WHERE token = ?", (token,))
        connection.commit()


def _employee_by_id(state: dict[str, Any], employee_id: int) -> dict[str, Any] | None:
    for employee in state.get("employees", []):
        if isinstance(employee, dict) and str(employee.get("id")) == str(employee_id):
            clean = dict(employee)
            clean.pop("appPassword", None)
            return clean
    return None


def _employee_team_ids(state: dict[str, Any], employee_id: int, employee: dict[str, Any]) -> set[int]:
    team_ids: set[int] = set()
    employee_name = " ".join(str(employee.get("name") or "").lower().split())
    employee_parts = set(employee_name.split())
    for team in state.get("workTeams", []):
        if not isinstance(team, dict):
            continue
        members = team.get("employeeIds") if isinstance(team.get("employeeIds"), list) else []
        linked = any(str(member) == str(employee_id) for member in members)
        if not linked and not members:
            team_name = " ".join(str(team.get("name") or "").lower().split())
            team_parts = set(team_name.split())
            linked = bool(team_parts and employee_parts and (team_parts <= employee_parts or employee_parts <= team_parts))
        if linked:
            try:
                team_ids.add(int(team.get("id")))
            except (TypeError, ValueError):
                pass
    return team_ids


def employee_view(employee_id: int) -> dict[str, Any] | None:
    state = load_portal_state()
    employee = _employee_by_id(state, employee_id)
    if employee is None:
        return None
    team_ids = _employee_team_ids(state, employee_id, employee)
    entries = [
        dict(entry)
        for entry in state.get("workScheduleEntries", [])
        if isinstance(entry, dict) and _as_int(entry.get("teamId")) in team_ids
    ]
    entry_ids = {_as_int(entry.get("id")) for entry in entries}
    client_ids = {_as_int(entry.get("projectClientId")) for entry in entries if entry.get("projectClientId") not in (None, "")}
    for client in state.get("projectClients", []):
        if not isinstance(client, dict):
            continue
        if _as_int(client.get("scheduledTeamId")) in team_ids:
            client_ids.add(_as_int(client.get("id")))
    clients = [
        dict(client)
        for client in state.get("projectClients", [])
        if isinstance(client, dict) and _as_int(client.get("id")) in client_ids
    ]
    files = [
        dict(file)
        for file in state.get("employeeAppFiles", [])
        if isinstance(file, dict) and _as_int(file.get("scheduleEntryId")) in entry_ids
    ]
    exams = [
        dict(exam)
        for exam in state.get("exams", [])
        if isinstance(exam, dict) and _as_int(exam.get("employeeId")) == employee_id
    ]
    teams = [
        dict(team)
        for team in state.get("workTeams", [])
        if isinstance(team, dict) and _as_int(team.get("id")) in team_ids
    ]
    referenced_company_ids = {_as_int(entry.get("companyId")) for entry in entries if entry.get("companyId") not in (None, "")}
    referenced_work_ids = {_as_int(entry.get("workId")) for entry in entries if entry.get("workId") not in (None, "")}
    referenced_clinic_ids = {_as_int(exam.get("clinicId")) for exam in exams if exam.get("clinicId") not in (None, "")}
    return {
        "employee": employee,
        "data": {
            "employees": [employee],
            "workTeams": teams,
            "companies": [
                dict(company)
                for company in state.get("companies", [])
                if isinstance(company, dict) and _as_int(company.get("id")) in referenced_company_ids
            ],
            "works": [
                dict(work)
                for work in state.get("works", [])
                if isinstance(work, dict) and _as_int(work.get("id")) in referenced_work_ids
            ],
            "clinics": [
                dict(clinic)
                for clinic in state.get("clinics", [])
                if isinstance(clinic, dict) and _as_int(clinic.get("id")) in referenced_clinic_ids
            ],
            "workScheduleEntries": entries,
            "projectClients": clients,
            "exams": exams,
            "employeeAppFiles": files,
            "serverUpdatedAt": state.get("serverUpdatedAt"),
        },
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _find_entry_and_client(state: dict[str, Any], employee_id: int, schedule_entry_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    view = employee_view(employee_id)
    if view is None:
        return None
    allowed_entry_ids = {_as_int(entry.get("id")) for entry in view["data"].get("workScheduleEntries", [])}
    if schedule_entry_id not in allowed_entry_ids:
        return None
    entry = next(
        (item for item in state.get("workScheduleEntries", []) if isinstance(item, dict) and _as_int(item.get("id")) == schedule_entry_id),
        None,
    )
    if entry is None:
        return None
    client_id = _as_int(entry.get("projectClientId"))
    client = next(
        (item for item in state.get("projectClients", []) if isinstance(item, dict) and _as_int(item.get("id")) == client_id),
        None,
    )
    if client is None:
        return None
    return entry, client


def append_employee_message(employee_id: int, schedule_entry_id: int, text: str) -> dict[str, Any] | None:
    clean_text = str(text or "").strip()
    if not clean_text:
        return None
    with _LOCK, _connect() as connection:
        state = _load_state(connection)
        found = _find_entry_and_client(state, employee_id, schedule_entry_id)
        if found is None:
            return None
        entry, client = found
        employee = _employee_by_id(state, employee_id)
        if employee is None:
            return None
        messages = client.get("teamMessages") if isinstance(client.get("teamMessages"), list) else []
        numeric_ids = [_as_int(message.get("id")) for message in messages if isinstance(message, dict)]
        message = {
            "id": max(numeric_ids or [0]) + 1,
            "clientId": client.get("id"),
            "teamId": entry.get("teamId"),
            "senderRole": "Funcionário",
            "senderName": employee.get("name"),
            "senderEmployeeId": employee_id,
            "text": clean_text[:4000],
            "createdAt": _now_iso(),
            "readBy": [employee_id],
        }
        messages.append(message)
        client["teamMessages"] = messages
        _save_state(connection, state)
        connection.commit()
        return message


def mark_entry_messages_read(employee_id: int, schedule_entry_id: int) -> bool:
    with _LOCK, _connect() as connection:
        state = _load_state(connection)
        found = _find_entry_and_client(state, employee_id, schedule_entry_id)
        if found is None:
            return False
        _, client = found
        changed = False
        for message in client.get("teamMessages", []):
            if not isinstance(message, dict):
                continue
            read_by = message.get("readBy") if isinstance(message.get("readBy"), list) else []
            if employee_id not in [_as_int(item) for item in read_by]:
                read_by.append(employee_id)
                message["readBy"] = read_by
                changed = True
        if changed:
            _save_state(connection, state)
            connection.commit()
        return True


def add_employee_file(
    employee_id: int,
    schedule_entry_id: int,
    name: str,
    mime_type: str,
    size: int,
    data_url: str,
) -> dict[str, Any] | None:
    if not name or not data_url.startswith("data:") or "," not in data_url:
        return None
    maximum = int(getattr(settings, "employee_file_max_bytes", 5_242_880))
    try:
        encoded = data_url.split(",", 1)[1]
        actual_size = len(base64.b64decode(encoded, validate=False))
    except (ValueError, TypeError):
        raise ValueError("Conteúdo do arquivo inválido.")
    if size > maximum or actual_size > maximum:
        raise ValueError("Arquivo acima do limite permitido.")
    size = actual_size
    with _LOCK, _connect() as connection:
        state = _load_state(connection)
        found = _find_entry_and_client(state, employee_id, schedule_entry_id)
        if found is None:
            return None
        entry, client = found
        employee = _employee_by_id(state, employee_id)
        files = state.get("employeeAppFiles") if isinstance(state.get("employeeAppFiles"), list) else []
        numeric_ids = [_as_int(file.get("id")) for file in files if isinstance(file, dict)]
        record = {
            "id": max(numeric_ids or [0]) + 1,
            "scheduleEntryId": schedule_entry_id,
            "projectClientId": client.get("id"),
            "employeeId": employee_id,
            "employeeName": employee.get("name") if employee else "Funcionário",
            "name": str(name)[:240],
            "type": str(mime_type or "application/octet-stream")[:160],
            "size": int(size or 0),
            "dataUrl": data_url,
            "createdAt": _now_iso(),
        }
        files.append(record)
        state["employeeAppFiles"] = files
        client_files = client.get("teamFiles") if isinstance(client.get("teamFiles"), list) else []
        client_files.append(record["id"])
        client["teamFiles"] = list(dict.fromkeys(client_files))
        _save_state(connection, state)
        connection.commit()
        return record


def get_employee_file(employee_id: int, file_id: int) -> dict[str, Any] | None:
    view = employee_view(employee_id)
    if view is None:
        return None
    return next(
        (file for file in view["data"].get("employeeAppFiles", []) if _as_int(file.get("id")) == file_id),
        None,
    )
