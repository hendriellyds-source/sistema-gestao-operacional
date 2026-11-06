from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
import unicodedata
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .settings import settings
from .sqlite_connection import ClosingConnection


router = APIRouter(prefix="/api/projects", tags=["Projetos — Projetista"])

CITY_PORTALS = {
    "Cidade Demo": "https://bigua.atende.net/",
    "São José": "https://saojose.atende.net/cidadao",
    "Florianópolis": "https://servicos.floripa.sc.gov.br/atendimento/inicio",
    "Palhoça": "https://palhoca.atende.net/cidadao",
    "Balneário Camboriú": "",
    "Blumenau": "",
    "Brusque": "",
    "Chapecó": "",
    "Criciúma": "",
    "Itajaí": "",
    "Jaraguá do Sul": "",
    "Joinville": "",
    "Lages": "",
    "Navegantes": "",
    "Rio do Sul": "",
    "Tijucas": "",
    "Tubarão": "",
}
RI_DIGITAL_URL = "https://ridigital.org.br/eProtocolo/listagem_contratos.aspx?comum=1"
SERVICES = {"Retificação de área", "Amembramento", "Desmembramento"}
PROCESS_TYPES = {"Prefeitura", "Cartório"}
PROCESS_STATUSES = {"OK", "Aguardando", "Perto do vencimento", "Urgente", "Concluído"}
WORK_STATUSES = {
    "Aguardando",
    "Aguardando conferência",
    "Iniciado",
    "Em conferência",
    "Em andamento",
    "Correção solicitada",
    "Aguardando Projetista",
    "Conferida",
    "Concluído",
}
ALLOWED_PROFILE_HEADERS = {"projects", "projectist", "admin", "director", "diretor", "contato"}
ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".dwg", ".dxf",
}


class DataPayload(BaseModel):
    data: dict[str, Any]


class DocumentPayload(BaseModel):
    owner_type: str = Field(pattern="^(client|process|work)$")
    owner_id: int
    category: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=240)
    document_number: str = Field(default="", max_length=160)
    issue_date: str = Field(default="", max_length=10)
    expiry_date: str = Field(default="", max_length=10)
    notes: str = Field(default="", max_length=4000)
    filename: str = Field(min_length=1, max_length=240)
    data_url: str = Field(min_length=20, max_length=35_000_000)


class VaultSetupPayload(BaseModel):
    pin: str = Field(min_length=6, max_length=120)


class RegistryCredentialPayload(BaseModel):
    access_number: str = Field(default="", max_length=240)
    password: str = Field(default="", max_length=240)


class SpreadsheetPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    data_url: str = Field(min_length=20, max_length=35_000_000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return date.today()


def _connect() -> sqlite3.Connection:
    path = Path(settings.projects_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _safe(value: Any, fallback: str = "item") -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return text[:100] or fallback


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", text)).strip().upper()


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("extra_json", "pre_annotation_numbers", "metadata_json"):
        if key in item:
            item[key.removesuffix("_json")] = _json(item.pop(key), {} if key != "pre_annotation_numbers" else [])
    return item


def _audit(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int | None,
    action: str,
    actor: str,
    before: Any = None,
    after: Any = None,
    note: str = "",
) -> None:
    connection.execute(
        """
        INSERT INTO projects_audit
            (entity_type, entity_id, action, actor, before_json, after_json, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type, entity_id, action, actor or "Sistema",
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            note, _now(),
        ),
    )


def init_projects_projects() -> None:
    Path(settings.projects_documents_root_path).mkdir(parents=True, exist_ok=True)
    Path(settings.projects_imports_root_path).mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_type TEXT NOT NULL DEFAULT 'PF',
                name TEXT NOT NULL,
                cpf_cnpj TEXT,
                birth_date TEXT,
                legal_name TEXT,
                trade_name TEXT,
                responsible_name TEXT,
                state_registration TEXT,
                municipal_registration TEXT,
                phone TEXT,
                email TEXT,
                address TEXT,
                city TEXT,
                state TEXT DEFAULT 'SC',
                notes TEXT,
                extra_json TEXT NOT NULL DEFAULT '{}',
                archived_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_clients_search
                ON projects_clients(name, cpf_cnpj, archived_at);

            CREATE TABLE IF NOT EXISTS projects_registries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                city TEXT,
                office_number TEXT,
                portal_url TEXT,
                ri_digital_url TEXT NOT NULL,
                access_number_enc TEXT,
                password_enc TEXT,
                notes TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects_processes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES projects_clients(id),
                title TEXT NOT NULL,
                process_type TEXT NOT NULL,
                service_type TEXT NOT NULL,
                city TEXT NOT NULL,
                responsible TEXT NOT NULL DEFAULT 'Projetos',
                portal_url TEXT,
                entry_code TEXT,
                secondary_code TEXT,
                third_code TEXT,
                protocol_s TEXT,
                protocol_p TEXT,
                access_code_ac TEXT,
                pre_annotation_numbers TEXT NOT NULL DEFAULT '[]',
                legacy_reference TEXT,
                document_submission_due TEXT,
                next_deadline TEXT,
                registry_id INTEGER REFERENCES projects_registries(id),
                access_number_enc TEXT,
                password_enc TEXT,
                property_registration TEXT,
                property_address TEXT,
                expected_value REAL NOT NULL DEFAULT 0,
                billed_value REAL NOT NULL DEFAULT 0,
                paid_value REAL NOT NULL DEFAULT 0,
                status_manual TEXT NOT NULL DEFAULT 'OK',
                waiting_for TEXT,
                observations TEXT,
                source_key TEXT UNIQUE,
                source_notes TEXT,
                archived_at TEXT,
                concluded_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_processes_filters
                ON projects_processes(process_type, service_type, city, registry_id, archived_at);
            CREATE INDEX IF NOT EXISTS idx_projects_processes_codes
                ON projects_processes(entry_code, secondary_code, protocol_s, protocol_p, access_code_ac);

            CREATE TABLE IF NOT EXISTS projects_works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER REFERENCES projects_clients(id),
                source_profile TEXT NOT NULL DEFAULT 'Diretor',
                source_reference TEXT,
                company TEXT,
                work_name TEXT NOT NULL,
                description TEXT,
                deadline TEXT,
                responsible TEXT NOT NULL DEFAULT 'Projetos',
                priority TEXT NOT NULL DEFAULT 'Normal',
                status_manual TEXT NOT NULL DEFAULT 'Aguardando',
                waiting_for TEXT,
                expected_value REAL NOT NULL DEFAULT 0,
                billed_value REAL NOT NULL DEFAULT 0,
                paid_value REAL NOT NULL DEFAULT 0,
                observations TEXT,
                demand_kind TEXT NOT NULL DEFAULT 'Geral',
                admin_work_id INTEGER,
                drive_folder_url TEXT,
                drive_folder_name TEXT,
                requested_files TEXT,
                archived_at TEXT,
                concluded_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_works_filters
                ON projects_works(deadline, status_manual, responsible, archived_at);

            CREATE TABLE IF NOT EXISTS projects_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_type TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                client_id INTEGER REFERENCES projects_clients(id),
                category TEXT NOT NULL,
                title TEXT,
                document_number TEXT,
                issue_date TEXT,
                expiry_date TEXT,
                notes TEXT,
                original_name TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                uploaded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_documents_owner
                ON projects_documents(owner_type, owner_id, uploaded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_projects_documents_expiry
                ON projects_documents(expiry_date);

            CREATE TABLE IF NOT EXISTS projects_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                event_date TEXT,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                actor TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_events_entity
                ON projects_events(entity_type, entity_id, id DESC);

            CREATE TABLE IF NOT EXISTS projects_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_audit_entity
                ON projects_audit(entity_type, entity_id, id DESC);

            CREATE TABLE IF NOT EXISTS projects_notification_reads (
                notification_key TEXT PRIMARY KEY,
                read_at TEXT NOT NULL,
                read_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects_notification_dismissals (
                notification_key TEXT PRIMARY KEY,
                dismissed_at TEXT NOT NULL,
                dismissed_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects_portal_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                city TEXT,
                portal_type TEXT NOT NULL,
                url TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_portal_links_order
                ON projects_portal_links(active, sort_order, city, name);
            CREATE TABLE IF NOT EXISTS projects_vault (
                id INTEGER PRIMARY KEY CHECK(id=1),
                pin_salt TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                imported_records INTEGER NOT NULL,
                skipped_records INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects_email_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                enabled INTEGER NOT NULL DEFAULT 0,
                host TEXT NOT NULL DEFAULT 'imap.terra.com.br',
                port INTEGER NOT NULL DEFAULT 993,
                username_enc TEXT,
                password_enc TEXT,
                interval_seconds INTEGER NOT NULL DEFAULT 300,
                allowed_senders TEXT NOT NULL DEFAULT '',
                mark_read INTEGER NOT NULL DEFAULT 0,
                last_uid INTEGER NOT NULL DEFAULT 0,
                last_sync_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects_email_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_key TEXT NOT NULL UNIQUE,
                imap_uid INTEGER,
                message_id TEXT,
                sender TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                received_at TEXT,
                third_code TEXT,
                process_id INTEGER REFERENCES projects_processes(id) ON DELETE SET NULL,
                status TEXT NOT NULL,
                body_excerpt TEXT NOT NULL DEFAULT '',
                raw_relative_path TEXT,
                attachment_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_projects_email_messages_status
                ON projects_email_messages(status, received_at DESC, id DESC);
            """
        )
        timestamp = _now()
        default_registries = [
            (
                "2º Ofício de Registro de Imóveis de Florianópolis",
                "Florianópolis", "2º Ofício", "https://www.2ori.com.br/",
                RI_DIGITAL_URL,
            ),
            (
                "4º Ofício de Registro de Imóveis de Florianópolis",
                "Florianópolis", "4º Ofício",
                "https://4riflorianopolis-sc.sistemaasgard.com.br/app/v1/#/acompanhamento-online",
                RI_DIGITAL_URL,
            ),
            *[
                (
                    f"Registro de Imóveis — {city}", city, "",
                    "", RI_DIGITAL_URL,
                )
                for city in CITY_PORTALS
                if city not in {"Florianópolis"}
            ],
        ]
        for registry in default_registries:
            exists = connection.execute(
                "SELECT id FROM projects_registries WHERE LOWER(TRIM(name))=LOWER(TRIM(?))",
                (registry[0],),
            ).fetchone()
            if not exists:
                connection.execute(
                    """
                    INSERT INTO projects_registries
                        (name, city, office_number, portal_url, ri_digital_url, notes,
                         active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, '', 1, ?, ?)
                    """,
                    (*registry, timestamp, timestamp),
                )
        default_portals = [
            ("Prefeitura de Cidade Demo", "Cidade Demo", "Prefeitura", CITY_PORTALS["Cidade Demo"], 10),
            ("Prefeitura de Florianópolis", "Florianópolis", "Prefeitura", CITY_PORTALS["Florianópolis"], 20),
            ("Prefeitura de Palhoça", "Palhoça", "Prefeitura", CITY_PORTALS["Palhoça"], 30),
            ("Prefeitura de São José", "São José", "Prefeitura", CITY_PORTALS["São José"], 40),
            ("RI Digital — e-Protocolo", "", "Registro de imóveis", RI_DIGITAL_URL, 50),
        ]
        for portal in default_portals:
            exists = connection.execute(
                "SELECT id FROM projects_portal_links WHERE name=?", (portal[0],)
            ).fetchone()
            if not exists:
                connection.execute(
                    """
                    INSERT INTO projects_portal_links
                        (name, city, portal_type, url, sort_order, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (*portal, timestamp, timestamp),
                )
        process_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(projects_processes)")
        }
        if "access_number_enc" not in process_columns:
            connection.execute("ALTER TABLE projects_processes ADD COLUMN access_number_enc TEXT")
        if "password_enc" not in process_columns:
            connection.execute("ALTER TABLE projects_processes ADD COLUMN password_enc TEXT")
        if "waiting_for" not in process_columns:
            connection.execute("ALTER TABLE projects_processes ADD COLUMN waiting_for TEXT")
        if "third_code" not in process_columns:
            connection.execute("ALTER TABLE projects_processes ADD COLUMN third_code TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_processes_third_code ON projects_processes(third_code)"
        )
        work_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(projects_works)")
        }
        if "waiting_for" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN waiting_for TEXT")
        if "demand_kind" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN demand_kind TEXT NOT NULL DEFAULT 'Geral'")
        if "admin_work_id" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN admin_work_id INTEGER")
        if "drive_folder_url" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN drive_folder_url TEXT")
        if "drive_folder_name" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN drive_folder_name TEXT")
        if "requested_files" not in work_columns:
            connection.execute("ALTER TABLE projects_works ADD COLUMN requested_files TEXT")
        connection.commit()


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _deadline_state(deadline: Any) -> tuple[str, str, int | None]:
    due = _parse_date(deadline)
    if not due:
        return "SEM PRAZO", "Sem prazo definido", None
    remaining = (due - _today()).days
    if remaining < 0:
        return "VENCIDO", f"VENCIDO há {abs(remaining)} dia(s)", remaining
    if remaining <= 2:
        label = "PRAZO A VENCER — vence hoje" if remaining == 0 else f"PRAZO A VENCER — faltam {remaining} dia(s)"
        return "PRAZO A VENCER", label, remaining
    if remaining <= 10:
        return "PRÓXIMO", f"Faltam {remaining} dias", remaining
    return "NORMAL", f"Faltam {remaining} dias", remaining


def _automatic_status(manual: str, deadline: Any, archived_at: Any) -> tuple[str, int | None]:
    if archived_at or manual == "Concluído":
        return "Concluído", None
    state, _, remaining = _deadline_state(deadline)
    if state == "VENCIDO":
        return "VENCIDO", remaining
    if state == "PRAZO A VENCER":
        return "PRAZO A VENCER", remaining
    if state == "PRÓXIMO":
        return "Perto do vencimento", remaining
    if manual == "Aguardando":
        return "Aguardando", remaining
    return "OK", remaining


def _hydrate_process(item: dict[str, Any]) -> dict[str, Any]:
    item["process_has_credentials"] = bool(
        item.pop("access_number_enc", None) or item.pop("password_enc", None)
    )
    item["pre_annotation_numbers"] = _json(item.get("pre_annotation_numbers"), [])
    status, days = _automatic_status(
        item.get("status_manual", "OK"),
        item.get("next_deadline") or item.get("document_submission_due"),
        item.get("archived_at"),
    )
    item["status"] = status
    item["days_to_deadline"] = days
    state, label, _ = _deadline_state(
        item.get("next_deadline") or item.get("document_submission_due")
    )
    item["deadline_state"] = state
    item["deadline_label"] = label
    item["deadline_date"] = item.get("next_deadline") or item.get("document_submission_due")
    item["deadline_dates"] = []
    if item.get("document_submission_due"):
        item["deadline_dates"].append({
            "kind": "Prazo de envio dos documentos",
            "date": item["document_submission_due"],
        })
    if item.get("next_deadline") and item.get("next_deadline") != item.get("document_submission_due"):
        item["deadline_dates"].append({
            "kind": "Próximo prazo", "date": item["next_deadline"],
        })
    item["waiting_for"] = item.get("waiting_for") or (
        "Prefeitura/Cartório" if item.get("status_manual") == "Aguardando" else ""
    )
    item["portal_url"] = item.get("portal_url") or CITY_PORTALS.get(item.get("city"), "")
    return item


def _hydrate_work(item: dict[str, Any]) -> dict[str, Any]:
    status, days = _automatic_status(
        "Concluído" if item.get("status_manual") == "Concluído" else "OK",
        item.get("deadline"),
        item.get("archived_at"),
    )
    if (
        item.get("status_manual") == "Aguardando"
        and not item.get("archived_at")
        and status == "OK"
    ):
        status = "Aguardando"
    elif item.get("status_manual") in {"Iniciado", "Em andamento"} and status == "OK":
        status = item["status_manual"]
    item["progress_status"] = item.get("status_manual") or "Aguardando"
    state, label, _ = _deadline_state(item.get("deadline"))
    item["deadline_status"] = state
    item["deadline_state"] = state
    item["deadline_label"] = label
    item["deadline_date"] = item.get("deadline")
    item["deadline_dates"] = (
        [{"kind": "Prazo da obra", "date": item["deadline"]}]
        if item.get("deadline") else []
    )
    item["waiting_for"] = item.get("waiting_for") or (
        item.get("source_profile") if item.get("status_manual") == "Aguardando" else ""
    )
    item["status"] = status
    item["days_to_deadline"] = days
    return item


def _get_client(connection: sqlite3.Connection, client_id: int) -> dict[str, Any]:
    item = _row(connection.execute(
        "SELECT * FROM projects_clients WHERE id=?", (int(client_id),)
    ).fetchone())
    if not item:
        raise KeyError("Cliente não encontrado.")
    return item


def _ensure_unique_third_code(
    connection: sqlite3.Connection, third_code: str, exclude_process_id: int | None = None
) -> None:
    code = str(third_code or "").strip()
    if not code:
        return
    normalized_code = _normalized(code)
    for row in connection.execute(
        "SELECT id, third_code FROM projects_processes WHERE TRIM(COALESCE(third_code, ''))<>''"
    ).fetchall():
        if exclude_process_id is not None and int(row["id"]) == int(exclude_process_id):
            continue
        if _normalized(row["third_code"]) == normalized_code:
            raise HTTPException(409, "Este terceiro código já está vinculado a outro processo.")


def _entity_snapshot(connection: sqlite3.Connection, table: str, entity_id: int) -> dict[str, Any] | None:
    allowed = {"projects_clients", "projects_processes", "projects_works", "projects_registries"}
    if table not in allowed:
        return None
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    item = dict(row) if row else None
    if item:
        item.pop("access_number_enc", None)
        item.pop("password_enc", None)
    return item


def _insert_event(
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    event_type: str,
    description: str,
    actor: str,
    event_date: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO projects_events
            (entity_type, entity_id, event_date, event_type, description, actor,
             metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type, entity_id, event_date or _today().isoformat(),
            event_type, description, actor or "Sistema",
            json.dumps(metadata or {}, ensure_ascii=False), _now(),
        ),
    )


def _client_values(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Informe o nome do cliente.")
    person_type = str(payload.get("person_type") or "PF").upper()
    if person_type not in {"PF", "PJ"}:
        raise ValueError("Tipo de pessoa inválido.")
    keys = (
        "cpf_cnpj", "birth_date", "legal_name", "trade_name", "responsible_name",
        "state_registration", "municipal_registration", "phone", "email", "address",
        "city", "state", "notes",
    )
    result = {key: str(payload.get(key) or "").strip() for key in keys}
    result.update(
        person_type=person_type,
        name=name,
        extra_json=json.dumps(payload.get("extra") or {}, ensure_ascii=False),
    )
    return result


def _process_values(payload: dict[str, Any]) -> dict[str, Any]:
    process_type = str(payload.get("process_type") or "").strip().title()
    if process_type == "Cartorio":
        process_type = "Cartório"
    service = str(payload.get("service_type") or "").strip()
    city = str(payload.get("city") or "").strip()
    if process_type not in PROCESS_TYPES:
        raise ValueError("Selecione Prefeitura ou Cartório.")
    if service not in SERVICES:
        raise ValueError("Selecione Retificação de área, Amembramento ou Desmembramento.")
    if city not in CITY_PORTALS:
        raise ValueError("Selecione uma das cidades de Santa Catarina configuradas.")
    manual = str(payload.get("status_manual") or "OK").strip()
    if manual not in PROCESS_STATUSES:
        raise ValueError("Status inválido.")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("Informe o título do processo.")
    numbers = payload.get("pre_annotation_numbers") or []
    if isinstance(numbers, str):
        numbers = [part.strip() for part in re.split(r"[,;\n]+", numbers) if part.strip()]
    result: dict[str, Any] = {
        "client_id": int(payload.get("client_id") or 0),
        "title": title,
        "process_type": process_type,
        "service_type": service,
        "city": city,
        "responsible": str(payload.get("responsible") or "Projetos").strip(),
        "portal_url": CITY_PORTALS[city] if process_type == "Prefeitura" else RI_DIGITAL_URL,
        "entry_code": str(payload.get("entry_code") or "").strip(),
        "secondary_code": str(payload.get("secondary_code") or "").strip(),
        "third_code": str(payload.get("third_code") or "").strip(),
        "protocol_s": str(payload.get("protocol_s") or "").strip(),
        "protocol_p": str(payload.get("protocol_p") or "").strip(),
        "access_code_ac": str(payload.get("access_code_ac") or "").strip(),
        "pre_annotation_numbers": json.dumps(numbers, ensure_ascii=False),
        "legacy_reference": str(payload.get("legacy_reference") or "").strip(),
        "document_submission_due": str(payload.get("document_submission_due") or "").strip(),
        "next_deadline": str(payload.get("next_deadline") or "").strip(),
        "registry_id": int(payload["registry_id"]) if payload.get("registry_id") else None,
        "property_registration": str(payload.get("property_registration") or "").strip(),
        "property_address": str(payload.get("property_address") or "").strip(),
        "expected_value": float(payload.get("expected_value") or 0),
        "billed_value": float(payload.get("billed_value") or 0),
        "paid_value": float(payload.get("paid_value") or 0),
        "status_manual": manual,
        "waiting_for": str(payload.get("waiting_for") or "").strip(),
        "observations": str(payload.get("observations") or "").strip(),
        "source_key": payload.get("source_key"),
        "source_notes": str(payload.get("source_notes") or "").strip(),
    }
    if result["client_id"] <= 0:
        raise ValueError("Selecione o cliente.")
    if process_type == "Cartório" and not result["registry_id"]:
        raise ValueError("Selecione o registro de imóveis/cartório.")
    return result


def _work_values(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status_manual") or "Aguardando").strip()
    if status not in WORK_STATUSES:
        raise ValueError("Status da obra inválido.")
    work_name = str(payload.get("work_name") or "").strip()
    if not work_name:
        raise ValueError("Informe o nome da obra/demanda.")
    drive_folder_url = str(payload.get("drive_folder_url") or "").strip()
    if drive_folder_url and not re.match(
        r"^https://(?:drive|docs)\.google\.com/", drive_folder_url, flags=re.IGNORECASE
    ):
        raise ValueError("Informe um link válido de pasta do Google Drive.")
    return {
        "client_id": int(payload["client_id"]) if payload.get("client_id") else None,
        "source_profile": str(payload.get("source_profile") or "Diretor").strip(),
        "source_reference": str(payload.get("source_reference") or "").strip(),
        "company": str(payload.get("company") or "").strip(),
        "work_name": work_name,
        "description": str(payload.get("description") or "").strip(),
        "deadline": str(payload.get("deadline") or "").strip(),
        "responsible": str(payload.get("responsible") or "Projetos").strip(),
        "priority": str(payload.get("priority") or "Normal").strip(),
        "status_manual": status,
        "waiting_for": str(payload.get("waiting_for") or "").strip(),
        "expected_value": float(payload.get("expected_value") or 0),
        "billed_value": float(payload.get("billed_value") or 0),
        "paid_value": float(payload.get("paid_value") or 0),
        "observations": str(payload.get("observations") or "").strip(),
        "demand_kind": str(payload.get("demand_kind") or "Geral").strip(),
        "admin_work_id": int(payload["admin_work_id"]) if payload.get("admin_work_id") else None,
        "drive_folder_url": drive_folder_url,
        "drive_folder_name": str(payload.get("drive_folder_name") or "").strip(),
        "requested_files": str(payload.get("requested_files") or "").strip(),
    }


@router.get("/config")
def projects_config() -> dict[str, Any]:
    return {
        "cities": CITY_PORTALS,
        "ri_digital_url": RI_DIGITAL_URL,
        "services": sorted(SERVICES),
        "process_types": sorted(PROCESS_TYPES),
        "status_rules": {"near_days": 10, "urgent_days": 2},
        "secure_credentials": {
            "encrypted_at_rest": True,
            "automatic_login": False,
            "profiles": ["Projetos", "Administrador/Diretor"],
        },
    }


def _portal_values(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    url = str(data.get("url") or "").strip()
    if not name:
        raise HTTPException(422, "Informe o nome do portal.")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise HTTPException(422, "Informe um endereço iniciado por http:// ou https://.")
    city = str(data.get("city") or "").strip()
    if city and city not in CITY_PORTALS:
        raise HTTPException(422, "Selecione uma das cidades de Santa Catarina configuradas.")
    return {
        "name": name,
        "city": city,
        "portal_type": str(data.get("portal_type") or "Outro").strip(),
        "url": url,
        "sort_order": int(data.get("sort_order") or 0),
        "active": 1 if data.get("active", True) not in (False, 0, "0", "false") else 0,
    }


@router.get("/portal-links")
def list_portal_links(
    active_only: bool = Query(False),
    city: str = Query(""),
) -> list[dict[str, Any]]:
    conditions = []
    params: list[Any] = []
    if active_only:
        conditions.append("active=1")
    if city:
        conditions.append("(city=? OR city='')")
        params.append(city)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _connect() as connection:
        return [
            {**dict(row), "active": bool(row["active"])}
            for row in connection.execute(
                f"SELECT * FROM projects_portal_links {where} ORDER BY sort_order, name",
                params,
            ).fetchall()
        ]


@router.post("/portal-links")
def create_portal_link(
    payload: DataPayload,
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    values = _portal_values(payload.data)
    timestamp = _now()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO projects_portal_links
                (name, city, portal_type, url, sort_order, active, created_at, updated_at)
            VALUES (:name, :city, :portal_type, :url, :sort_order, :active, :created_at, :updated_at)
            """,
            {**values, "created_at": timestamp, "updated_at": timestamp},
        )
        portal_id = int(cursor.lastrowid)
        _audit(
            connection, entity_type="portal_link", entity_id=portal_id,
            action="create", actor=x_operational_profile, after=values,
        )
        connection.commit()
        return dict(connection.execute(
            "SELECT * FROM projects_portal_links WHERE id=?", (portal_id,)
        ).fetchone())


@router.put("/portal-links/{portal_id}")
def update_portal_link(
    portal_id: int,
    payload: DataPayload,
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    values = _portal_values(payload.data)
    with _connect() as connection:
        before = _row(connection.execute(
            "SELECT * FROM projects_portal_links WHERE id=?", (portal_id,)
        ).fetchone())
        if not before:
            raise HTTPException(404, "Portal não encontrado.")
        connection.execute(
            """
            UPDATE projects_portal_links SET
                name=:name, city=:city, portal_type=:portal_type, url=:url,
                sort_order=:sort_order, active=:active, updated_at=:updated_at
            WHERE id=:id
            """,
            {**values, "updated_at": _now(), "id": portal_id},
        )
        after = _row(connection.execute(
            "SELECT * FROM projects_portal_links WHERE id=?", (portal_id,)
        ).fetchone())
        _audit(
            connection, entity_type="portal_link", entity_id=portal_id,
            action="update", actor=x_operational_profile, before=before, after=after,
        )
        connection.commit()
        return after or {}


@router.get("/summary")
def projects_summary() -> dict[str, Any]:
    init_projects_projects()
    processes = list_processes(
        archived=False,
        search="",
        city="",
        service_type="",
        process_type="",
        status="",
        registry_id=None,
    )
    works = list_works(
        archived=False,
        search="",
        status="",
        responsible="",
    )
    notifications = list_notifications(unread_only=False)
    return {
        "clients": len(list_clients(archived=False, search="")),
        "active_processes": len(processes),
        "active_works": len(works),
        "overdue": sum(1 for item in processes + works if item.get("deadline_state") == "VENCIDO"),
        "due_soon": sum(1 for item in processes + works if item.get("deadline_state") == "PRAZO A VENCER"),
        "urgent": sum(
            1 for item in processes + works
            if item.get("deadline_state") in {"VENCIDO", "PRAZO A VENCER"}
        ),
        "near": sum(1 for item in processes + works if item["status"] == "Perto do vencimento"),
        "waiting": sum(1 for item in processes + works if item.get("waiting_for")),
        "unread_notifications": sum(1 for item in notifications if not item["read"]),
        "expected_value": sum(float(item.get("expected_value") or 0) for item in processes + works),
        "billed_value": sum(float(item.get("billed_value") or 0) for item in processes + works),
        "paid_value": sum(float(item.get("paid_value") or 0) for item in processes + works),
    }


@router.get("/clients")
def list_clients(
    archived: bool = Query(False),
    search: str = Query("", max_length=160),
) -> list[dict[str, Any]]:
    init_projects_projects()
    conditions = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"]
    params: list[Any] = []
    if search.strip():
        conditions.append("(name LIKE ? OR cpf_cnpj LIKE ? OR legal_name LIKE ? OR trade_name LIKE ?)")
        pattern = f"%{search.strip()}%"
        params.extend([pattern] * 4)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM projects_clients WHERE {' AND '.join(conditions)} ORDER BY name",
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = _row(row) or {}
            item["process_count"] = connection.execute(
                "SELECT COUNT(*) FROM projects_processes WHERE client_id=?", (item["id"],)
            ).fetchone()[0]
            item["work_count"] = connection.execute(
                "SELECT COUNT(*) FROM projects_works WHERE client_id=?", (item["id"],)
            ).fetchone()[0]
            result.append(item)
        return result


@router.post("/clients")
def create_client(payload: DataPayload, x_operational_profile: str = Header("Projetos")) -> dict[str, Any]:
    values = _client_values(payload.data)
    timestamp = _now()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO projects_clients
                (person_type, name, cpf_cnpj, birth_date, legal_name, trade_name,
                 responsible_name, state_registration, municipal_registration,
                 phone, email, address, city, state, notes, extra_json,
                 created_at, updated_at)
            VALUES (:person_type, :name, :cpf_cnpj, :birth_date, :legal_name,
                    :trade_name, :responsible_name, :state_registration,
                    :municipal_registration, :phone, :email, :address, :city,
                    :state, :notes, :extra_json, :created_at, :updated_at)
            """,
            {**values, "created_at": timestamp, "updated_at": timestamp},
        )
        client_id = int(cursor.lastrowid)
        after = _entity_snapshot(connection, "projects_clients", client_id)
        _audit(connection, entity_type="client", entity_id=client_id, action="create",
               actor=x_operational_profile, after=after)
        connection.commit()
        return _row(connection.execute(
            "SELECT * FROM projects_clients WHERE id=?", (client_id,)
        ).fetchone()) or {}


@router.put("/clients/{client_id}")
def update_client(
    client_id: int, payload: DataPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    values = _client_values(payload.data)
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_clients", client_id)
        if not before:
            raise HTTPException(404, "Cliente não encontrado.")
        assignments = ", ".join(f"{key}=:{key}" for key in values)
        connection.execute(
            f"UPDATE projects_clients SET {assignments}, updated_at=:updated_at WHERE id=:id",
            {**values, "updated_at": _now(), "id": client_id},
        )
        after = _entity_snapshot(connection, "projects_clients", client_id)
        _audit(connection, entity_type="client", entity_id=client_id, action="update",
               actor=x_operational_profile, before=before, after=after)
        connection.commit()
        return _row(connection.execute(
            "SELECT * FROM projects_clients WHERE id=?", (client_id,)
        ).fetchone()) or {}


@router.post("/clients/{client_id}/archive")
def archive_client(
    client_id: int, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    with _connect() as connection:
        client = _get_client(connection, client_id)
        active = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM projects_processes WHERE client_id=? AND archived_at IS NULL) +
              (SELECT COUNT(*) FROM projects_works WHERE client_id=? AND archived_at IS NULL)
            """,
            (client_id, client_id),
        ).fetchone()[0]
        if active:
            raise HTTPException(409, "Conclua ou arquive todos os processos e obras antes de finalizar o cliente.")
        connection.execute(
            "UPDATE projects_clients SET archived_at=?, updated_at=? WHERE id=?",
            (_now(), _now(), client_id),
        )
        _audit(connection, entity_type="client", entity_id=client_id, action="archive",
               actor=x_operational_profile, before=client, note="Cliente finalizado; dados preservados.")
        connection.commit()
    return {"ok": True, "archived": True}


@router.get("/processes")
def list_processes(
    archived: bool = Query(False),
    search: str = Query("", max_length=160),
    city: str = Query(""),
    service_type: str = Query(""),
    process_type: str = Query(""),
    status: str = Query(""),
    registry_id: int | None = Query(None),
) -> list[dict[str, Any]]:
    init_projects_projects()
    conditions = ["p.archived_at IS NOT NULL" if archived else "p.archived_at IS NULL"]
    params: list[Any] = []
    if search.strip():
        conditions.append(
            """(
              c.name LIKE ? OR c.cpf_cnpj LIKE ? OR p.title LIKE ? OR
              p.entry_code LIKE ? OR p.secondary_code LIKE ? OR p.third_code LIKE ? OR p.protocol_s LIKE ? OR
              p.protocol_p LIKE ? OR p.access_code_ac LIKE ? OR
              p.property_registration LIKE ? OR p.legacy_reference LIKE ?
            )"""
        )
        params.extend([f"%{search.strip()}%"] * 11)
    for column, value in (
        ("p.city", city), ("p.service_type", service_type), ("p.process_type", process_type)
    ):
        if value:
            conditions.append(f"{column}=?")
            params.append(value)
    if registry_id:
        conditions.append("p.registry_id=?")
        params.append(registry_id)
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT p.*, c.name AS client_name, c.cpf_cnpj, c.person_type,
                   r.name AS registry_name, r.office_number,
                   CASE WHEN r.access_number_enc IS NOT NULL OR r.password_enc IS NOT NULL
                        THEN 1 ELSE 0 END AS registry_has_credentials
            FROM projects_processes p
            JOIN projects_clients c ON c.id=p.client_id
            LEFT JOIN projects_registries r ON r.id=p.registry_id
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(p.next_deadline, p.document_submission_due, '9999-12-31'),
                     p.updated_at DESC
            """,
            params,
        ).fetchall()
        result = [_hydrate_process(dict(row)) for row in rows]
        return [item for item in result if not status or item["status"] == status]


@router.get("/processes/{process_id}")
def process_detail(process_id: int) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT p.*, c.name AS client_name, c.cpf_cnpj, c.person_type,
                   r.name AS registry_name, r.office_number, r.portal_url AS registry_portal_url,
                   r.ri_digital_url,
                   CASE WHEN r.access_number_enc IS NOT NULL OR r.password_enc IS NOT NULL
                        THEN 1 ELSE 0 END AS registry_has_credentials
            FROM projects_processes p
            JOIN projects_clients c ON c.id=p.client_id
            LEFT JOIN projects_registries r ON r.id=p.registry_id
            WHERE p.id=?
            """,
            (process_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Processo não encontrado.")
        item = _hydrate_process(dict(row))
        item["documents"] = _list_documents(connection, "process", process_id)
        item["events"] = [
            _row(event) for event in connection.execute(
                "SELECT * FROM projects_events WHERE entity_type='process' AND entity_id=? ORDER BY id DESC",
                (process_id,),
            ).fetchall()
        ]
        item["audit"] = [
            dict(event) for event in connection.execute(
                "SELECT * FROM projects_audit WHERE entity_type='process' AND entity_id=? ORDER BY id DESC",
                (process_id,),
            ).fetchall()
        ]
        return item


@router.post("/processes")
def create_process(payload: DataPayload, x_operational_profile: str = Header("Projetos")) -> dict[str, Any]:
    try:
        values = _process_values(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    timestamp = _now()
    with _connect() as connection:
        try:
            _get_client(connection, values["client_id"])
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        _ensure_unique_third_code(connection, values.get("third_code") or "")
        columns = ", ".join(values)
        placeholders = ", ".join(f":{key}" for key in values)
        cursor = connection.execute(
            f"""
            INSERT INTO projects_processes ({columns}, created_at, updated_at)
            VALUES ({placeholders}, :created_at, :updated_at)
            """,
            {**values, "created_at": timestamp, "updated_at": timestamp},
        )
        process_id = int(cursor.lastrowid)
        _insert_event(connection, "process", process_id, "Cadastro",
                      "Processo criado no sistema.", x_operational_profile)
        after = _entity_snapshot(connection, "projects_processes", process_id)
        _audit(connection, entity_type="process", entity_id=process_id, action="create",
               actor=x_operational_profile, after=after)
        if values["status_manual"] == "Concluído":
            connection.execute(
                "UPDATE projects_processes SET concluded_at=?, archived_at=? WHERE id=?",
                (timestamp, timestamp, process_id),
            )
        connection.commit()
    return process_detail(process_id)


@router.put("/processes/{process_id}")
def update_process(
    process_id: int, payload: DataPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    try:
        values = _process_values(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_processes", process_id)
        if not before:
            raise HTTPException(404, "Processo não encontrado.")
        _ensure_unique_third_code(
            connection, values.get("third_code") or "", exclude_process_id=process_id
        )
        assignments = ", ".join(f"{key}=:{key}" for key in values)
        concluded = values["status_manual"] == "Concluído"
        timestamp = _now()
        connection.execute(
            f"""
            UPDATE projects_processes
            SET {assignments}, updated_at=:updated_at,
                concluded_at=:concluded_at, archived_at=:archived_at
            WHERE id=:id
            """,
            {
                **values, "updated_at": timestamp, "id": process_id,
                "concluded_at": timestamp if concluded else before.get("concluded_at"),
                "archived_at": timestamp if concluded else before.get("archived_at"),
            },
        )
        after = _entity_snapshot(connection, "projects_processes", process_id)
        _insert_event(
            connection, "process", process_id, "Atualização",
            f"Processo atualizado. Status: {values['status_manual']}.",
            x_operational_profile,
        )
        _audit(connection, entity_type="process", entity_id=process_id, action="update",
               actor=x_operational_profile, before=before, after=after)
        connection.commit()
    return process_detail(process_id)


@router.post("/processes/{process_id}/archive")
def archive_process(
    process_id: int, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_processes", process_id)
        if not before:
            raise HTTPException(404, "Processo não encontrado.")
        timestamp = _now()
        connection.execute(
            """
            UPDATE projects_processes
            SET status_manual='Concluído', concluded_at=COALESCE(concluded_at, ?),
                archived_at=COALESCE(archived_at, ?), updated_at=?
            WHERE id=?
            """,
            (timestamp, timestamp, timestamp, process_id),
        )
        _insert_event(connection, "process", process_id, "Conclusão",
                      "Processo concluído e arquivado sem exclusão.", x_operational_profile)
        _audit(connection, entity_type="process", entity_id=process_id, action="archive",
               actor=x_operational_profile, before=before)
        connection.commit()
    return {"ok": True, "archived": True}


@router.get("/works")
def list_works(
    archived: bool = Query(False),
    search: str = Query("", max_length=160),
    status: str = Query(""),
    responsible: str = Query(""),
) -> list[dict[str, Any]]:
    conditions = ["w.archived_at IS NOT NULL" if archived else "w.archived_at IS NULL"]
    params: list[Any] = []
    if search.strip():
        conditions.append("(w.work_name LIKE ? OR w.company LIKE ? OR c.name LIKE ? OR w.description LIKE ?)")
        params.extend([f"%{search.strip()}%"] * 4)
    if responsible:
        conditions.append("w.responsible=?")
        params.append(responsible)
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT w.*, c.name AS client_name, c.cpf_cnpj,
                   c.city AS client_city
            FROM projects_works w
            LEFT JOIN projects_clients c ON c.id=w.client_id
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(w.deadline, '9999-12-31'), w.updated_at DESC
            """,
            params,
        ).fetchall()
        result = [_hydrate_work(dict(row)) for row in rows]
        return [item for item in result if not status or item["status"] == status]


@router.get("/works/{work_id}")
def work_detail(work_id: int) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT w.*, c.name AS client_name, c.cpf_cnpj
            FROM projects_works w LEFT JOIN projects_clients c ON c.id=w.client_id
            WHERE w.id=?
            """,
            (work_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Demanda de obra não encontrada.")
        item = _hydrate_work(dict(row))
        item["documents"] = _list_documents(connection, "work", work_id)
        item["events"] = [
            _row(event) for event in connection.execute(
                "SELECT * FROM projects_events WHERE entity_type='work' AND entity_id=? ORDER BY id DESC",
                (work_id,),
            ).fetchall()
        ]
        return item


@router.post("/works")
def create_work(payload: DataPayload, x_operational_profile: str = Header("Diretor")) -> dict[str, Any]:
    try:
        values = _work_values(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    timestamp = _now()
    with _connect() as connection:
        columns = ", ".join(values)
        cursor = connection.execute(
            f"""
            INSERT INTO projects_works ({columns}, created_at, updated_at)
            VALUES ({', '.join(f':{key}' for key in values)}, :created_at, :updated_at)
            """,
            {**values, "created_at": timestamp, "updated_at": timestamp},
        )
        work_id = int(cursor.lastrowid)
        _insert_event(connection, "work", work_id, "Demanda recebida",
                      f"Demanda criada por {values['source_profile']}.", x_operational_profile)
        _audit(connection, entity_type="work", entity_id=work_id, action="create",
               actor=x_operational_profile, after=_entity_snapshot(connection, "projects_works", work_id))
        connection.commit()
    return work_detail(work_id)


@router.put("/works/{work_id}")
def update_work(
    work_id: int, payload: DataPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    try:
        values = _work_values(payload.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_works", work_id)
        if not before:
            raise HTTPException(404, "Demanda de obra não encontrada.")
        timestamp = _now()
        concluded = values["status_manual"] == "Concluído"
        connection.execute(
            f"""
            UPDATE projects_works SET
                {', '.join(f'{key}=:{key}' for key in values)},
                updated_at=:updated_at,
                concluded_at=:concluded_at,
                archived_at=:archived_at
            WHERE id=:id
            """,
            {
                **values, "updated_at": timestamp, "id": work_id,
                "concluded_at": timestamp if concluded else before.get("concluded_at"),
                "archived_at": timestamp if concluded else before.get("archived_at"),
            },
        )
        transition_note = str(payload.data.get("transition_note") or "").strip()
        _insert_event(
            connection,
            "work",
            work_id,
            "Atualização",
            transition_note or f"Demanda atualizada. Status: {values['status_manual']}.",
            x_operational_profile,
        )
        _audit(connection, entity_type="work", entity_id=work_id, action="update",
               actor=x_operational_profile, before=before,
               after=_entity_snapshot(connection, "projects_works", work_id))
        connection.commit()
    return work_detail(work_id)


@router.post("/works/{work_id}/archive")
def archive_work(work_id: int, x_operational_profile: str = Header("Projetos")) -> dict[str, Any]:
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_works", work_id)
        if not before:
            raise HTTPException(404, "Demanda de obra não encontrada.")
        timestamp = _now()
        connection.execute(
            """
            UPDATE projects_works SET status_manual='Concluído',
                concluded_at=COALESCE(concluded_at, ?),
                archived_at=COALESCE(archived_at, ?), updated_at=?
            WHERE id=?
            """,
            (timestamp, timestamp, timestamp, work_id),
        )
        _insert_event(connection, "work", work_id, "Conclusão",
                      "Demanda concluída e arquivada sem exclusão.", x_operational_profile)
        _audit(connection, entity_type="work", entity_id=work_id, action="archive",
               actor=x_operational_profile, before=before)
        connection.commit()
    return {"ok": True, "archived": True}


def _registry_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["has_credentials"] = bool(item.pop("access_number_enc", None) or item.pop("password_enc", None))
    item["active"] = bool(item.get("active"))
    return item


@router.get("/registries")
def list_registries(active_only: bool = Query(False)) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM projects_registries {'WHERE active=1' if active_only else ''} ORDER BY city, name"
        ).fetchall()
        return [_registry_public(row) for row in rows]


@router.post("/registries")
def create_registry(payload: DataPayload, x_operational_profile: str = Header("Projetos")) -> dict[str, Any]:
    data = payload.data
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Informe o nome do cartório/registro de imóveis.")
    timestamp = _now()
    values = {
        "name": name,
        "city": str(data.get("city") or "").strip(),
        "office_number": str(data.get("office_number") or "").strip(),
        "portal_url": str(data.get("portal_url") or "").strip(),
        "ri_digital_url": str(data.get("ri_digital_url") or RI_DIGITAL_URL).strip(),
        "notes": str(data.get("notes") or "").strip(),
        "active": 1 if data.get("active", True) else 0,
    }
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO projects_registries
                (name, city, office_number, portal_url, ri_digital_url, notes,
                 active, created_at, updated_at)
            VALUES (:name, :city, :office_number, :portal_url, :ri_digital_url,
                    :notes, :active, :created_at, :updated_at)
            """,
            {**values, "created_at": timestamp, "updated_at": timestamp},
        )
        registry_id = int(cursor.lastrowid)
        _audit(connection, entity_type="registry", entity_id=registry_id, action="create",
               actor=x_operational_profile, after=values)
        connection.commit()
        return _registry_public(connection.execute(
            "SELECT * FROM projects_registries WHERE id=?", (registry_id,)
        ).fetchone())


@router.put("/registries/{registry_id}")
def update_registry(
    registry_id: int, payload: DataPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    data = payload.data
    values = {
        "name": str(data.get("name") or "").strip(),
        "city": str(data.get("city") or "").strip(),
        "office_number": str(data.get("office_number") or "").strip(),
        "portal_url": str(data.get("portal_url") or "").strip(),
        "ri_digital_url": str(data.get("ri_digital_url") or RI_DIGITAL_URL).strip(),
        "notes": str(data.get("notes") or "").strip(),
        "active": 1 if data.get("active", True) else 0,
    }
    if not values["name"]:
        raise HTTPException(422, "Informe o nome do cartório.")
    with _connect() as connection:
        before = _entity_snapshot(connection, "projects_registries", registry_id)
        if not before:
            raise HTTPException(404, "Cartório não encontrado.")
        connection.execute(
            """
            UPDATE projects_registries SET
                name=:name, city=:city, office_number=:office_number,
                portal_url=:portal_url, ri_digital_url=:ri_digital_url,
                notes=:notes, active=:active, updated_at=:updated_at
            WHERE id=:id
            """,
            {**values, "updated_at": _now(), "id": registry_id},
        )
        _audit(connection, entity_type="registry", entity_id=registry_id, action="update",
               actor=x_operational_profile, before=before, after=values)
        connection.commit()
        return _registry_public(connection.execute(
            "SELECT * FROM projects_registries WHERE id=?", (registry_id,)
        ).fetchone())


def _vault_hash(pin: str, salt: bytes) -> str:
    return hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def _vault_config(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM projects_vault WHERE id=1").fetchone()


def _require_profile(profile: str) -> None:
    if _normalized(profile).lower() not in ALLOWED_PROFILE_HEADERS:
        raise HTTPException(403, "Perfil sem permissão para acessar credenciais.")


def _require_vault(connection: sqlite3.Connection, pin: str) -> None:
    row = _vault_config(connection)
    if not row:
        raise HTTPException(409, "Configure primeiro o PIN do cofre da Projetos.")
    salt = bytes.fromhex(row["pin_salt"])
    if not secrets.compare_digest(_vault_hash(pin, salt), row["pin_hash"]):
        raise HTTPException(403, "PIN do cofre inválido.")


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise HTTPException(
            503,
            "O componente de criptografia ainda não foi instalado. Reinicie pelo 2_INICIAR_SISTEMA.bat.",
        ) from exc
    path = Path(settings.projects_master_key_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return Fernet(path.read_bytes().strip())


def _encrypt(value: str) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(500, "Não foi possível abrir a credencial criptografada.") from exc


@router.get("/vault/status")
def vault_status() -> dict[str, Any]:
    with _connect() as connection:
        return {"configured": bool(_vault_config(connection)), "automatic_login": False}


@router.post("/vault/setup")
def setup_vault(payload: VaultSetupPayload, x_operational_profile: str = Header("Projetos")) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        if _vault_config(connection):
            raise HTTPException(409, "O cofre já está configurado.")
        salt = secrets.token_bytes(16)
        timestamp = _now()
        connection.execute(
            """
            INSERT INTO projects_vault (id, pin_salt, pin_hash, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?)
            """,
            (salt.hex(), _vault_hash(payload.pin, salt), timestamp, timestamp),
        )
        _audit(connection, entity_type="vault", entity_id=1, action="setup",
               actor=x_operational_profile, note="Cofre configurado; PIN não registrado no histórico.")
        connection.commit()
    _fernet()
    return {"ok": True, "configured": True}


@router.put("/registries/{registry_id}/credentials")
def save_registry_credentials(
    registry_id: int,
    payload: RegistryCredentialPayload,
    x_projects_vault_pin: str = Header(""),
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        _require_vault(connection, x_projects_vault_pin)
        row = connection.execute(
            "SELECT id FROM projects_registries WHERE id=?", (registry_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Cartório não encontrado.")
        connection.execute(
            """
            UPDATE projects_registries
            SET access_number_enc=?, password_enc=?, updated_at=? WHERE id=?
            """,
            (_encrypt(payload.access_number), _encrypt(payload.password), _now(), registry_id),
        )
        _audit(connection, entity_type="registry", entity_id=registry_id,
               action="credentials_update", actor=x_operational_profile,
               note="Credenciais alteradas; valores não registrados no histórico.")
        connection.commit()
    return {"ok": True, "has_credentials": bool(payload.access_number or payload.password)}


@router.post("/registries/{registry_id}/credentials/reveal")
def reveal_registry_credentials(
    registry_id: int,
    x_projects_vault_pin: str = Header(""),
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        _require_vault(connection, x_projects_vault_pin)
        row = connection.execute(
            "SELECT access_number_enc, password_enc FROM projects_registries WHERE id=?",
            (registry_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Cartório não encontrado.")
        _audit(connection, entity_type="registry", entity_id=registry_id,
               action="credentials_reveal", actor=x_operational_profile,
               note="Credenciais reveladas a perfil autorizado; valores não registrados.")
        connection.commit()
        return {
            "access_number": _decrypt(row["access_number_enc"]),
            "password": _decrypt(row["password_enc"]),
        }


@router.put("/processes/{process_id}/credentials")
def save_process_credentials(
    process_id: int,
    payload: RegistryCredentialPayload,
    x_projects_vault_pin: str = Header(""),
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        _require_vault(connection, x_projects_vault_pin)
        row = connection.execute(
            "SELECT id FROM projects_processes WHERE id=?", (process_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Processo não encontrado.")
        connection.execute(
            """
            UPDATE projects_processes
            SET access_number_enc=?, password_enc=?, updated_at=? WHERE id=?
            """,
            (_encrypt(payload.access_number), _encrypt(payload.password), _now(), process_id),
        )
        _audit(
            connection, entity_type="process", entity_id=process_id,
            action="credentials_update", actor=x_operational_profile,
            note="Credenciais do protocolo alteradas; valores não registrados no histórico.",
        )
        connection.commit()
    return {"ok": True, "has_credentials": bool(payload.access_number or payload.password)}


@router.post("/processes/{process_id}/credentials/reveal")
def reveal_process_credentials(
    process_id: int,
    x_projects_vault_pin: str = Header(""),
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        _require_vault(connection, x_projects_vault_pin)
        row = connection.execute(
            "SELECT access_number_enc, password_enc FROM projects_processes WHERE id=?",
            (process_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Processo não encontrado.")
        _audit(
            connection, entity_type="process", entity_id=process_id,
            action="credentials_reveal", actor=x_operational_profile,
            note="Credenciais do protocolo reveladas; valores não registrados.",
        )
        connection.commit()
        return {
            "access_number": _decrypt(row["access_number_enc"]),
            "password": _decrypt(row["password_enc"]),
        }


def _owner_context(
    connection: sqlite3.Connection, owner_type: str, owner_id: int
) -> tuple[dict[str, Any], int, str, Path]:
    if owner_type == "client":
        client = _get_client(connection, owner_id)
        return client, client["id"], client["name"], Path("cliente")
    if owner_type == "process":
        process = connection.execute(
            "SELECT id, client_id, title FROM projects_processes WHERE id=?", (owner_id,)
        ).fetchone()
        if not process:
            raise KeyError("Processo não encontrado.")
        client = _get_client(connection, process["client_id"])
        return client, client["id"], client["name"], Path("processos") / f"{owner_id}_{_safe(process['title'])}"
    if owner_type == "work":
        work = connection.execute(
            "SELECT id, client_id, work_name FROM projects_works WHERE id=?", (owner_id,)
        ).fetchone()
        if not work:
            raise KeyError("Demanda de obra não encontrada.")
        if work["client_id"]:
            client = _get_client(connection, work["client_id"])
        else:
            client = {"id": 0, "name": "Sem cliente", "person_type": "PF", "cpf_cnpj": ""}
        return client, int(work["client_id"] or 0), client["name"], Path("obras") / f"{owner_id}_{_safe(work['work_name'])}"
    raise KeyError("Tipo de documento inválido.")


def _decode_file(filename: str, data_url: str) -> tuple[bytes, str, str]:
    name = Path(filename).name
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Formato não permitido.")
    encoded = data_url.split(",", 1)[1] if data_url.startswith("data:") and "," in data_url else data_url
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("Arquivo corrompido ou incompleto.") from exc
    if not content:
        raise ValueError("Arquivo vazio.")
    if len(content) > int(settings.projects_document_file_max_bytes):
        raise ValueError("Arquivo acima do limite de 25 MB.")
    return content, name, mimetypes.guess_type(name)[0] or "application/octet-stream"


def _list_documents(
    connection: sqlite3.Connection, owner_type: str, owner_id: int
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM projects_documents
        WHERE owner_type=? AND owner_id=?
        ORDER BY category, canonical_name, version DESC
        """,
        (owner_type, owner_id),
    ).fetchall()
    result = []
    for row in rows:
        item = _row(row) or {}
        item["download_url"] = f"/api/projects/documents/{item['id']}/download"
        result.append(item)
    return result


@router.get("/documents")
def list_documents_endpoint(
    owner_type: str = Query(pattern="^(client|process|work)$"),
    owner_id: int = Query(gt=0),
) -> list[dict[str, Any]]:
    with _connect() as connection:
        return _list_documents(connection, owner_type, owner_id)


@router.post("/documents")
def upload_document(
    payload: DocumentPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    try:
        content, original_name, mime = _decode_file(payload.filename, payload.data_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _connect() as connection:
        try:
            client, client_id, client_name, owner_folder = _owner_context(
                connection, payload.owner_type, payload.owner_id
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        category = str(payload.category).strip()
        document_key = _normalized(payload.title or category)
        current = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) FROM projects_documents
            WHERE owner_type=? AND owner_id=? AND category=? AND
                  UPPER(COALESCE(title, ''))=UPPER(?)
            """,
            (payload.owner_type, payload.owner_id, category, payload.title or ""),
        ).fetchone()[0]
        version = int(current) + 1
        digits = re.sub(r"\D", "", str(client.get("cpf_cnpj") or ""))
        if client.get("person_type") == "PJ":
            identity = f"PJ_CNPJ_{digits or 'NAO_INFORMADO'}"
        else:
            identity = f"PF_CPF_{digits or 'NAO_INFORMADO'}"
        stem = _safe(payload.title or category, "DOCUMENTO").upper()
        extension = Path(original_name).suffix.lower()
        canonical_name = (
            f"{identity}_{_safe(client_name).upper()}_{stem}_"
            f"v{version:02d}_{_today():%Y%m%d}{extension}"
        )
        relative_folder = (
            Path(f"{client_id or 'sem_cliente'}_{_safe(client_name)}") /
            owner_folder / _safe(category, "outros")
        )
        absolute_folder = Path(settings.projects_documents_root_path) / relative_folder
        absolute_folder.mkdir(parents=True, exist_ok=True)
        destination = absolute_folder / canonical_name
        destination.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        try:
            cursor = connection.execute(
                """
                INSERT INTO projects_documents (
                    owner_type, owner_id, client_id, category, title,
                    document_number, issue_date, expiry_date, notes,
                    original_name, canonical_name, version, relative_path,
                    mime_type, size_bytes, sha256, metadata_json, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.owner_type, payload.owner_id, client_id or None,
                    category, payload.title, payload.document_number,
                    payload.issue_date, payload.expiry_date, payload.notes,
                    original_name, canonical_name, version,
                    (relative_folder / canonical_name).as_posix(),
                    mime, len(content), digest,
                    json.dumps(
                        {
                            "naming_pattern": identity,
                            "source_profile": x_operational_profile,
                            "original_extension": extension,
                        },
                        ensure_ascii=False,
                    ),
                    _now(),
                ),
            )
            document_id = int(cursor.lastrowid)
            _insert_event(
                connection, payload.owner_type, payload.owner_id,
                "Documento", f"{category}: versão {version} adicionada.",
                x_operational_profile,
            )
            _audit(connection, entity_type="document", entity_id=document_id,
                   action="upload", actor=x_operational_profile,
                   after={"canonical_name": canonical_name, "sha256": digest, "version": version})
            connection.commit()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        item = _row(connection.execute(
            "SELECT * FROM projects_documents WHERE id=?", (document_id,)
        ).fetchone()) or {}
        item["download_url"] = f"/api/projects/documents/{document_id}/download"
        return item


@router.get("/documents/{document_id}/download")
def download_document(document_id: int) -> FileResponse:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM projects_documents WHERE id=?", (document_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Documento não encontrado.")
    root = Path(settings.projects_documents_root_path).resolve()
    path = (root / row["relative_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Arquivo não localizado.")
    return FileResponse(path, filename=row["canonical_name"], media_type=row["mime_type"])


@router.get("/calendar")
def projects_calendar(
    date_from: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    city: str = Query("", max_length=120),
) -> dict[str, Any]:
    """Return virtual deadline events without persisting or duplicating them."""
    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if not start or not end or start > end:
        raise HTTPException(422, "Informe um período de calendário válido.")
    if (end - start).days > 370:
        raise HTTPException(422, "Consulte no máximo 12 meses por vez.")

    events: dict[str, dict[str, Any]] = {}

    def add_event(
        *, key: str, event_date: Any, title: str, kind: str,
        entity_type: str, entity_id: int, client: str = "",
        event_city: str = "", url: str = "",
    ) -> None:
        due = _parse_date(event_date)
        if not due or due < start or due > end:
            return
        if city and _normalized(event_city) != _normalized(city):
            return
        state, label, days = _deadline_state(due.isoformat())
        events.setdefault(key, {
            "id": key,
            "date": due.isoformat(),
            "title": title,
            "kind": kind,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "client": client,
            "city": event_city,
            "status": state,
            "deadline_label": label,
            "days_to_deadline": days,
            "url": url,
        })

    processes = list_processes(
        archived=False, search="", city="", service_type="", process_type="",
        status="", registry_id=None,
    )
    for process in processes:
        for field, label in (
            ("document_submission_due", "Envio de documentos"),
            ("next_deadline", "Prazo do processo"),
        ):
            value = process.get(field)
            if not value:
                continue
            add_event(
                key=f"process:{process['id']}:{field}:{str(value)[:10]}",
                event_date=value,
                title=f"{label}: {process['title']}",
                kind="process",
                entity_type="process",
                entity_id=process["id"],
                client=process.get("client_name") or "",
                event_city=process.get("city") or "",
                url=f"/projects/processes/{process['id']}",
            )

    works = list_works(
        archived=False, search="", status="", responsible="",
    )
    for work in works:
        if not work.get("deadline"):
            continue
        add_event(
            key=f"work:{work['id']}:deadline:{str(work['deadline'])[:10]}",
            event_date=work["deadline"],
            title=f"Prazo da obra: {work['work_name']}",
            kind="work",
            entity_type="work",
            entity_id=work["id"],
            client=work.get("client_name") or work.get("company") or "",
            event_city=work.get("client_city") or "",
            url=f"/projects/works/{work['id']}",
        )

    with _connect() as connection:
        documents = connection.execute(
            """
            SELECT d.id, d.owner_type, d.owner_id, d.title, d.category,
                   d.expiry_date, c.name AS client_name, c.city AS client_city
            FROM projects_documents d
            LEFT JOIN projects_clients c ON c.id=d.client_id
            WHERE d.expiry_date BETWEEN ? AND ?
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    for document in documents:
        add_event(
            key=f"document:{document['id']}:expiry:{document['expiry_date']}",
            event_date=document["expiry_date"],
            title=f"Validade de documento: {document['title'] or document['category']}",
            kind="document",
            entity_type=document["owner_type"],
            entity_id=document["owner_id"],
            client=document["client_name"] or "",
            event_city=document["client_city"] or "",
        )

    ordered = sorted(events.values(), key=lambda item: (item["date"], item["title"], item["id"]))
    return {"date_from": start.isoformat(), "date_to": end.isoformat(), "events": ordered}


def _notification_rows() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for process in list_processes(
        archived=False,
        search="",
        city="",
        service_type="",
        process_type="",
        status="",
        registry_id=None,
    ):
        state = process.get("deadline_state")
        waiting_for = process.get("waiting_for")
        if state in {"VENCIDO", "PRAZO A VENCER", "PRÓXIMO"} or waiting_for:
            status = (
                state
                if state in {"VENCIDO", "PRAZO A VENCER"}
                else "Perto do vencimento"
                if state == "PRÓXIMO"
                else "Aguardando"
            )
            message = process.get("deadline_label") if state != "SEM PRAZO" else ""
            if waiting_for:
                dependency = f"AGUARDANDO — ação de {waiting_for}"
                message = f"{message} • {dependency}" if message else dependency
            items.append({
                "key": f"process:{process['id']}:{status}:{process.get('next_deadline') or process.get('document_submission_due')}:{waiting_for}",
                "entity_type": "process", "entity_id": process["id"],
                "title": process["title"], "client": process["client_name"],
                "city": process.get("city") or "",
                "status": status,
                "deadline_state": state,
                "days_to_deadline": process.get("days_to_deadline"),
                "deadline": process.get("next_deadline") or process.get("document_submission_due"),
                "message": message,
            })
    for work in list_works(
        archived=False,
        search="",
        status="",
        responsible="",
    ):
        state = work.get("deadline_state")
        waiting_for = work.get("waiting_for")
        if state in {"VENCIDO", "PRAZO A VENCER", "PRÓXIMO"} or waiting_for:
            status = (
                state
                if state in {"VENCIDO", "PRAZO A VENCER"}
                else "Perto do vencimento"
                if state == "PRÓXIMO"
                else "Aguardando"
            )
            message = work.get("deadline_label") if state != "SEM PRAZO" else ""
            if waiting_for:
                dependency = f"AGUARDANDO — ação de {waiting_for}"
                message = f"{message} • {dependency}" if message else dependency
            items.append({
                "key": f"work:{work['id']}:{status}:{work.get('deadline')}:{waiting_for}",
                "entity_type": "work", "entity_id": work["id"],
                "title": work["work_name"], "client": work.get("client_name") or work.get("company") or "",
                "city": work.get("client_city") or "",
                "status": status,
                "deadline_state": state,
                "days_to_deadline": work.get("days_to_deadline"),
                "deadline": work.get("deadline"),
                "message": message,
            })
        is_new_demand = work.get("status_manual") in {
            "Aguardando", "Aguardando conferência"
        }
        if (
            is_new_demand
            and not (
                state in {"VENCIDO", "PRAZO A VENCER", "PRÓXIMO"}
                or waiting_for
            )
        ):
            items.append({
                "key": f"work-new:{work['id']}",
                "entity_type": "work", "entity_id": work["id"],
                "title": work["work_name"],
                "client": work.get("client_name") or work.get("company") or "",
                "city": work.get("client_city") or "",
                "status": "Nova demanda",
                "deadline_state": "NOVA",
                "days_to_deadline": work.get("days_to_deadline"),
                "deadline": work.get("deadline"),
                "message": f"Nova demanda enviada por {work.get('source_profile') or 'Projetista/Diretor'}",
            })
    with _connect() as connection:
        email_rows = connection.execute(
            """
            SELECT m.id, m.sender, m.subject, m.received_at,
                   p.id AS process_id, p.title, p.city, c.name AS client_name
            FROM projects_email_messages m
            JOIN projects_processes p ON p.id=m.process_id
            JOIN projects_clients c ON c.id=p.client_id
            WHERE m.status='matched'
            """
        ).fetchall()
        for email_row in email_rows:
            items.append({
                "key": f"email:{email_row['id']}",
                "entity_type": "process", "entity_id": email_row["process_id"],
                "title": email_row["title"], "client": email_row["client_name"],
                "city": email_row["city"] or "",
                "status": "E-mail recebido", "deadline_state": "EMAIL",
                "days_to_deadline": None,
                "deadline": str(email_row["received_at"] or "")[:10] or None,
                "message": (
                    f"{email_row['subject'] or 'Mensagem sem assunto'} - "
                    f"{email_row['sender'] or 'remetente nao informado'}"
                ),
            })
        reads = {
            row["notification_key"]: row["read_at"]
            for row in connection.execute("SELECT * FROM projects_notification_reads")
        }
        dismissed = {
            row["notification_key"]
            for row in connection.execute("SELECT notification_key FROM projects_notification_dismissals")
        }
    for item in items:
        item["read"] = item["key"] in reads
        item["read_at"] = reads.get(item["key"])
    items = [item for item in items if item["key"] not in dismissed]
    order = {
        "VENCIDO": 0,
        "PRAZO A VENCER": 1,
        "E-mail recebido": 2,
        "Perto do vencimento": 3,
        "Nova demanda": 4,
        "Aguardando": 5,
    }
    return sorted(items, key=lambda item: (order.get(item["status"], 9), item.get("deadline") or "9999"))


@router.get("/notifications")
def list_notifications(unread_only: bool = Query(False)) -> list[dict[str, Any]]:
    rows = _notification_rows()
    return [item for item in rows if not unread_only or not item["read"]]


@router.post("/notifications/{notification_key}/read")
def read_notification(
    notification_key: str, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO projects_notification_reads (notification_key, read_at, read_by)
            VALUES (?, ?, ?)
            ON CONFLICT(notification_key) DO UPDATE SET read_at=excluded.read_at, read_by=excluded.read_by
            """,
            (notification_key, _now(), x_operational_profile),
        )
        connection.commit()
    return {"ok": True}


def _document_paths(rows: list[sqlite3.Row]) -> list[Path]:
    root = Path(settings.projects_documents_root_path).resolve()
    result = []
    for row in rows:
        path = (root / row["relative_path"]).resolve()
        if root in path.parents:
            result.append(path)
    return result


def _remove_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _delete_impact(connection: sqlite3.Connection, entity_type: str, entity_id: str) -> dict[str, Any]:
    if entity_type == "notification":
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": "Notificação",
            "documents": 0,
            "processes": 0,
            "works": 0,
            "message": "A notificação será ocultada permanentemente. O processo ou obra de origem não será apagado.",
        }
    try:
        numeric_id = int(entity_id)
    except ValueError as exc:
        raise HTTPException(422, "Identificador inválido.") from exc
    if entity_type == "client":
        row = connection.execute("SELECT * FROM projects_clients WHERE id=?", (numeric_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Cliente não encontrado.")
        processes = connection.execute(
            "SELECT COUNT(*) FROM projects_processes WHERE client_id=?", (numeric_id,)
        ).fetchone()[0]
        works = connection.execute(
            "SELECT COUNT(*) FROM projects_works WHERE client_id=?", (numeric_id,)
        ).fetchone()[0]
        documents = connection.execute(
            "SELECT COUNT(*) FROM projects_documents WHERE client_id=?", (numeric_id,)
        ).fetchone()[0]
        return {
            "entity_type": entity_type, "entity_id": numeric_id, "title": row["name"],
            "documents": documents, "processes": processes, "works": works,
            "message": (
                f"Serão excluídos o cliente, {processes} processo(s), {works} obra(s), "
                f"{documents} documento(s) e seus históricos."
            ),
        }
    table_map = {
        "process": ("projects_processes", "title"),
        "work": ("projects_works", "work_name"),
        "registry": ("projects_registries", "name"),
        "portal_link": ("projects_portal_links", "name"),
        "document": ("projects_documents", "canonical_name"),
    }
    if entity_type not in table_map:
        raise HTTPException(422, "Tipo de exclusão inválido.")
    table, title_column = table_map[entity_type]
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (numeric_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Item não encontrado.")
    documents = connection.execute(
        "SELECT COUNT(*) FROM projects_documents WHERE owner_type=? AND owner_id=?",
        (entity_type if entity_type in {"process", "work"} else "", numeric_id),
    ).fetchone()[0]
    linked = (
        connection.execute(
            "SELECT COUNT(*) FROM projects_processes WHERE registry_id=?", (numeric_id,)
        ).fetchone()[0]
        if entity_type == "registry" else 0
    )
    return {
        "entity_type": entity_type, "entity_id": numeric_id,
        "title": row[title_column], "documents": documents,
        "processes": linked if entity_type == "registry" else 0,
        "works": 0,
        "message": (
            f"Serão excluídos este item e {documents} documento(s)."
            + (f" {linked} processo(s) deixarão de apontar para este cartório." if linked else "")
        ),
    }


@router.get("/delete-impact/{entity_type}/{entity_id}")
def delete_impact(
    entity_type: str,
    entity_id: str,
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    with _connect() as connection:
        return _delete_impact(connection, entity_type, entity_id)


@router.delete("/delete/{entity_type}/{entity_id}")
def delete_entity(
    entity_type: str,
    entity_id: str,
    confirmed: bool = Query(False),
    x_operational_profile: str = Header("Projetos"),
) -> dict[str, Any]:
    _require_profile(x_operational_profile)
    if not confirmed:
        raise HTTPException(409, "Confirme a exclusão após revisar os impactos.")
    with _connect() as connection:
        impact = _delete_impact(connection, entity_type, entity_id)
        if entity_type == "notification":
            connection.execute(
                """
                INSERT INTO projects_notification_dismissals
                    (notification_key, dismissed_at, dismissed_by)
                VALUES (?, ?, ?)
                ON CONFLICT(notification_key) DO UPDATE SET
                    dismissed_at=excluded.dismissed_at,
                    dismissed_by=excluded.dismissed_by
                """,
                (entity_id, _now(), x_operational_profile),
            )
            _audit(
                connection, entity_type="notification", entity_id=None,
                action="delete", actor=x_operational_profile,
                note=f"Notificação descartada: {entity_id}",
            )
            connection.commit()
            return {"ok": True, "impact": impact}

        numeric_id = int(entity_id)
        document_rows: list[sqlite3.Row] = []
        if entity_type == "client":
            process_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM projects_processes WHERE client_id=?", (numeric_id,)
                )
            ]
            work_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM projects_works WHERE client_id=?", (numeric_id,)
                )
            ]
            document_rows = connection.execute(
                "SELECT * FROM projects_documents WHERE client_id=?", (numeric_id,)
            ).fetchall()
            for kind, ids in (("process", process_ids), ("work", work_ids)):
                for child_id in ids:
                    connection.execute(
                        "DELETE FROM projects_events WHERE entity_type=? AND entity_id=?",
                        (kind, child_id),
                    )
                    connection.execute(
                        "DELETE FROM projects_audit WHERE entity_type=? AND entity_id=?",
                        (kind, child_id),
                    )
            connection.execute("DELETE FROM projects_documents WHERE client_id=?", (numeric_id,))
            connection.execute("DELETE FROM projects_processes WHERE client_id=?", (numeric_id,))
            connection.execute("DELETE FROM projects_works WHERE client_id=?", (numeric_id,))
            connection.execute(
                "DELETE FROM projects_events WHERE entity_type='client' AND entity_id=?", (numeric_id,)
            )
            connection.execute(
                "DELETE FROM projects_audit WHERE entity_type='client' AND entity_id=?", (numeric_id,)
            )
            connection.execute("DELETE FROM projects_clients WHERE id=?", (numeric_id,))
        elif entity_type in {"process", "work"}:
            document_rows = connection.execute(
                "SELECT * FROM projects_documents WHERE owner_type=? AND owner_id=?",
                (entity_type, numeric_id),
            ).fetchall()
            connection.execute(
                "DELETE FROM projects_documents WHERE owner_type=? AND owner_id=?",
                (entity_type, numeric_id),
            )
            connection.execute(
                "DELETE FROM projects_events WHERE entity_type=? AND entity_id=?",
                (entity_type, numeric_id),
            )
            connection.execute(
                "DELETE FROM projects_audit WHERE entity_type=? AND entity_id=?",
                (entity_type, numeric_id),
            )
            connection.execute(
                f"DELETE FROM {'projects_processes' if entity_type == 'process' else 'projects_works'} WHERE id=?",
                (numeric_id,),
            )
        elif entity_type == "registry":
            connection.execute(
                "UPDATE projects_processes SET registry_id=NULL, updated_at=? WHERE registry_id=?",
                (_now(), numeric_id),
            )
            connection.execute("DELETE FROM projects_registries WHERE id=?", (numeric_id,))
        elif entity_type == "portal_link":
            connection.execute("DELETE FROM projects_portal_links WHERE id=?", (numeric_id,))
        elif entity_type == "document":
            document_rows = connection.execute(
                "SELECT * FROM projects_documents WHERE id=?", (numeric_id,)
            ).fetchall()
            connection.execute("DELETE FROM projects_documents WHERE id=?", (numeric_id,))
        _audit(
            connection, entity_type=entity_type, entity_id=numeric_id,
            action="permanent_delete", actor=x_operational_profile,
            note=impact["message"],
        )
        connection.commit()
    _remove_files(_document_paths(document_rows))
    return {"ok": True, "impact": impact}


@router.get("/audit")
def list_audit(
    entity_type: str = Query(""),
    entity_id: int | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if entity_type:
        conditions.append("entity_type=?")
        params.append(entity_type)
    if entity_id is not None:
        conditions.append("entity_id=?")
        params.append(entity_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM projects_audit {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def _xlsx_rows(path: Path) -> dict[str, list[list[str]]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", ns)))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("r:Relationship", rel_ns)}
        result: dict[str, list[list[str]]] = {}
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            name = sheet.attrib["name"]
            relationship = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rel_map[relationship].lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            root = ET.fromstring(archive.read(member))
            cells: dict[tuple[int, int], str] = {}
            max_row = max_col = 0
            for cell in root.findall(".//m:sheetData/m:row/m:c", ns):
                reference = cell.attrib.get("r", "A1")
                match = re.match(r"([A-Z]+)(\d+)", reference)
                if not match:
                    continue
                column_letters, row_text = match.groups()
                column = 0
                for char in column_letters:
                    column = column * 26 + ord(char) - 64
                row_number = int(row_text)
                cell_type = cell.attrib.get("t")
                value = ""
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                else:
                    node = cell.find("m:v", ns)
                    raw = node.text if node is not None and node.text is not None else ""
                    if cell_type == "s" and raw:
                        value = shared[int(raw)]
                    else:
                        value = raw
                cells[(row_number, column)] = value.strip()
                max_row = max(max_row, row_number)
                max_col = max(max_col, column)
            result[name] = [
                [cells.get((row_number, column), "") for column in range(1, max_col + 1)]
                for row_number in range(1, max_row + 1)
            ]
        return result


def _infer_service(value: str) -> str:
    text = _normalized(value)
    if "DESMEMBR" in text:
        return "Desmembramento"
    if "RETIF" in text:
        return "Retificação de área"
    if "AMEMBR" in text or "UNIFIC" in text:
        return "Amembramento"
    return "Retificação de área"


def _infer_client(value: str) -> str:
    text = re.sub(
        r"(?i)\b(RETIFICA[CÇ][AÃ]O|RETIF|AMEMBRAMENTO|DESMEMBRAMENTO|UNIFICA[CÇ][AÃ]O)\b",
        "", value.replace("_", " "),
    )
    text = re.sub(r"(?i)\bE\b\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" _-")
    return text.title() or "Cliente não identificado"


def _extract_deadline(lines: list[str], year: int = 2026) -> str:
    candidates: list[str] = []
    for line in lines:
        if not re.search(r"(?i)RETORNAR|ENTREGAR|VENC|AT[ÉE]\s+DIA|PRAZO", line):
            continue
        for day, month, found_year in re.findall(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", line):
            try:
                candidates.append(date(int(found_year or year), int(month), int(day)).isoformat())
            except ValueError:
                pass
    return max(candidates) if candidates else ""


def _migration_status(lines: list[str], deadline: str) -> str:
    joined = " ".join(lines)
    if re.search(r"(?i)FINALIZADO|CONCLU[IÍ]DO", joined):
        return "Concluído"
    if deadline:
        remaining = (_parse_date(deadline) - _today()).days  # type: ignore[operator]
        if remaining <= 2:
            return "Urgente"
        if remaining <= 10:
            return "Perto do vencimento"
    if re.search(r"(?i)AGUARDANDO|AN[AÁ]LISE|NOTIFICA", joined):
        return "Aguardando"
    return "OK"


def migrate_spreadsheet(path: Path, actor: str = "Migração da planilha") -> dict[str, Any]:
    init_projects_projects()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with _connect() as connection:
        existing = connection.execute(
            "SELECT summary_json FROM projects_import_batches WHERE sha256=?", (digest,)
        ).fetchone()
        if existing:
            summary = json.loads(existing["summary_json"])
            summary["already_imported"] = True
            return summary

    sheets = _xlsx_rows(path)
    city_map = {
        "BIGUACU": "Cidade Demo", "SAO JOSE": "São José",
        "FPOLIS": "Florianópolis", "PALHOCA": "Palhoça",
    }
    records: list[dict[str, Any]] = []
    for raw_sheet_name, rows in sheets.items():
        city = city_map.get(_normalized(raw_sheet_name))
        if not city or len(rows) < 2:
            continue
        max_columns = max((len(row) for row in rows), default=0)
        for column in range(max_columns):
            process_type_raw = rows[0][column] if column < len(rows[0]) else ""
            client_service = rows[1][column] if column < len(rows[1]) else ""
            if not client_service.strip():
                continue
            normalized_type = _normalized(process_type_raw)
            if "PREFEITURA" in normalized_type:
                process_type = "Prefeitura"
            elif "CARTORIO" in normalized_type:
                process_type = "Cartório"
            else:
                continue
            notes = [
                row[column].strip()
                for row in rows[2:]
                if column < len(row) and row[column].strip()
            ]
            joined = "\n".join(notes)
            ac = re.search(r"\bAC\s*([0-9]{5,})\b", joined, re.I)
            protocol_s = re.search(r"\bS[-\s]?([0-9][A-Z0-9./-]*)\b", joined, re.I)
            protocol_p = re.search(r"\bP[-\s]?([0-9][A-Z0-9./-]*)\b", joined, re.I)
            process_code = re.search(
                r"(?i)(?:PROCESSO|PROTOCOLO)(?:\s+(?:PMSJ|PMF))?(?:\s+PREFEITURA)?(?:\s+ACESSO)?\s*[:\-]?\s*(?:N[°º]\s*)?([0-9.]+/[0-9]{4})",
                joined,
            )
            if process_type == "Prefeitura" and not process_code:
                process_code = re.search(r"\b([0-9]{3,}/[0-9]{4})\b", joined)
            secondary = re.search(
                r"(?i)COD(?:IGO)?(?:\s+VER(?:IFICADOR)?)?\s*[:\-]?\s*([A-Z0-9]+)",
                joined,
            )
            if process_type == "Prefeitura" and not secondary:
                secondary = re.search(
                    r"\b[0-9]{3,}/[0-9]{4}[_ \t-]+([A-Z0-9]{6,})\b",
                    joined,
                )
            protocols = re.findall(r"(?i)PROTOCOLO\s*([0-9.]+)", joined)
            passwords = re.findall(r"(?i)SENHA(?:\s+ACESSO)?\s*[:\-]?\s*([0-9][0-9A-Z]+)", joined)
            registry_hint = ""
            if re.search(r"(?i)2[º°]?\s*(?:OF|ORI)|2ORI", joined):
                registry_hint = "2º Ofício"
            elif re.search(r"(?i)4\s*RI|4RIFLORIANOPOLIS", joined):
                registry_hint = "4º Ofício"
            deadline = _extract_deadline(notes)
            service = _infer_service(client_service)
            redacted_notes = [
                re.sub(
                    r"(?i)(SENHA(?:\s+ACESSO)?\s*[:\-]?\s*)([0-9][0-9A-Z]+)",
                    r"\1[PROTEGIDA — RECADASTRAR NO COFRE]",
                    line,
                )
                for line in notes
            ]
            records.append({
                "source_key": f"PROCESSOS_2026:{raw_sheet_name}:{column + 1}",
                "sheet": raw_sheet_name,
                "column": column + 1,
                "client_name": _infer_client(client_service),
                "title": re.sub(r"\s+", " ", client_service.replace("_", " ")).strip(),
                "process_type": process_type,
                "service_type": service,
                "city": city,
                "entry_code": process_code.group(1) if process_type == "Prefeitura" and process_code else "",
                "secondary_code": secondary.group(1) if secondary else "",
                "protocol_s": f"S{protocol_s.group(1)}" if protocol_s else "",
                "protocol_p": f"P{protocol_p.group(1)}" if protocol_p else "",
                "access_code_ac": f"AC{ac.group(1)}" if ac else "",
                "legacy_reference": "; ".join(f"Protocolo {value}" for value in protocols),
                "document_submission_due": deadline if process_type == "Cartório" else "",
                "next_deadline": deadline,
                "registry_hint": registry_hint,
                "status_manual": _migration_status(notes, deadline),
                "source_notes": "\n".join(redacted_notes),
                "events": redacted_notes,
                "credentials_detected": len(passwords),
            })

    imported = skipped = 0
    with _connect() as connection:
        for record in records:
            if connection.execute(
                "SELECT id FROM projects_processes WHERE source_key=?", (record["source_key"],)
            ).fetchone():
                skipped += 1
                continue
            client = connection.execute(
                "SELECT id FROM projects_clients WHERE UPPER(name)=UPPER(?)",
                (record["client_name"],),
            ).fetchone()
            if client:
                client_id = int(client["id"])
            else:
                timestamp = _now()
                client_id = int(connection.execute(
                    """
                    INSERT INTO projects_clients
                        (person_type, name, state, notes, extra_json, created_at, updated_at)
                    VALUES ('PF', ?, 'SC', ?, '{}', ?, ?)
                    """,
                    (
                        record["client_name"],
                        f"Cliente criado pela migração da planilha {path.name}.",
                        timestamp, timestamp,
                    ),
                ).lastrowid)
                _audit(connection, entity_type="client", entity_id=client_id,
                       action="migration_create", actor=actor,
                       after={"name": record["client_name"], "source": path.name})
            registry_id = None
            if record["process_type"] == "Cartório":
                if record["registry_hint"]:
                    registry = connection.execute(
                        "SELECT id FROM projects_registries WHERE office_number=? LIMIT 1",
                        (record["registry_hint"],),
                    ).fetchone()
                else:
                    registry = connection.execute(
                        "SELECT id FROM projects_registries WHERE city=? ORDER BY id LIMIT 1",
                        (record["city"],),
                    ).fetchone()
                registry_id = int(registry["id"]) if registry else None
            timestamp = _now()
            concluded = record["status_manual"] == "Concluído"
            cursor = connection.execute(
                """
                INSERT INTO projects_processes (
                    client_id, title, process_type, service_type, city, responsible,
                    portal_url, entry_code, secondary_code, protocol_s, protocol_p,
                    access_code_ac, pre_annotation_numbers, legacy_reference,
                    document_submission_due, next_deadline, registry_id,
                    expected_value, billed_value, paid_value, status_manual,
                    observations, source_key, source_notes, archived_at, concluded_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'Projetos', ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?,
                          0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id, record["title"], record["process_type"], record["service_type"],
                    record["city"],
                    CITY_PORTALS[record["city"]] if record["process_type"] == "Prefeitura" else RI_DIGITAL_URL,
                    record["entry_code"], record["secondary_code"], record["protocol_s"],
                    record["protocol_p"], record["access_code_ac"], record["legacy_reference"],
                    record["document_submission_due"], record["next_deadline"], registry_id,
                    record["status_manual"],
                    "Importado da organização usada em 2026; revisar códigos e prazos.",
                    record["source_key"], record["source_notes"],
                    timestamp if concluded else None, timestamp if concluded else None,
                    timestamp, timestamp,
                ),
            )
            process_id = int(cursor.lastrowid)
            for index, event in enumerate(record["events"], start=1):
                _insert_event(
                    connection, "process", process_id, "Histórico da planilha",
                    event, actor, metadata={"source_row": index + 2, "sheet": record["sheet"]},
                )
            _audit(connection, entity_type="process", entity_id=process_id,
                   action="migration_create", actor=actor,
                   after={"source_key": record["source_key"], "source": path.name})
            imported += 1
        summary = {
            "source": path.name,
            "sha256": digest,
            "records_detected": len(records),
            "imported": imported,
            "skipped": skipped,
            "cities": sorted({record["city"] for record in records}),
            "review_required": sum(
                1 for record in records
                if record["process_type"] == "Cartório" and not record["protocol_s"] and not record["protocol_p"]
            ),
            "credentials_to_reenter": sum(record["credentials_detected"] for record in records),
            "already_imported": False,
        }
        connection.execute(
            """
            INSERT INTO projects_import_batches
                (source_name, sha256, imported_records, skipped_records, summary_json, imported_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (path.name, digest, imported, skipped, json.dumps(summary, ensure_ascii=False), _now()),
        )
        connection.commit()
    archive = Path(settings.projects_imports_root_path) / f"{_today():%Y%m%d}_{_safe(path.stem)}.xlsx"
    if path.resolve() != archive.resolve():
        archive.write_bytes(path.read_bytes())
    return summary


@router.post("/imports/spreadsheet")
def import_spreadsheet(
    payload: SpreadsheetPayload, x_operational_profile: str = Header("Projetos")
) -> dict[str, Any]:
    if Path(payload.filename).suffix.lower() != ".xlsx":
        raise HTTPException(422, "Envie uma planilha XLSX.")
    try:
        content, _, _ = _decode_file(payload.filename, payload.data_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    destination = Path(settings.projects_imports_root_path) / (
        f"entrada_{datetime.now():%Y%m%d_%H%M%S}_{_safe(payload.filename)}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    try:
        return migrate_spreadsheet(destination, actor=x_operational_profile)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, f"Não foi possível interpretar a planilha: {exc}") from exc


def projects_health() -> dict[str, Any]:
    try:
        init_projects_projects()
        with _connect() as connection:
            return {
                "ok": True,
                "clients": connection.execute("SELECT COUNT(*) FROM projects_clients").fetchone()[0],
                "processes": connection.execute("SELECT COUNT(*) FROM projects_processes").fetchone()[0],
                "works": connection.execute("SELECT COUNT(*) FROM projects_works").fetchone()[0],
                "documents": connection.execute("SELECT COUNT(*) FROM projects_documents").fetchone()[0],
                "vault_configured": bool(_vault_config(connection)),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
