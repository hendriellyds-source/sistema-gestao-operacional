from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .demo_cleanup import DEMO_VEHICLE_IDENTIFIERS, has_demo_marker
from .settings import settings
from .sqlite_connection import ClosingConnection

_ALLOWED_OWNER_TYPES = {"vehicle", "employee"}
_ALLOWED_DOCUMENT_CATEGORIES = {"DOCUMENTO", "EXAME", "CERTIFICADO"}
_ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._-")
    return (normalized[:100] or fallback)


def _connect() -> sqlite3.Connection:
    db_path = Path(settings.documents_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def init_document_storage() -> None:
    Path(settings.documents_root_path).mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS stored_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_type TEXT NOT NULL,
                owner_key TEXT NOT NULL,
                owner_name TEXT,
                document_type TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'DOCUMENTO',
                title TEXT,
                document_number TEXT,
                issue_date TEXT,
                expiry_date TEXT,
                notes TEXT,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stored_documents_owner
                ON stored_documents(owner_type, owner_key, uploaded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_stored_documents_expiry
                ON stored_documents(expiry_date);
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(stored_documents)")
        }
        if "category" not in columns:
            connection.execute(
                """
                ALTER TABLE stored_documents
                ADD COLUMN category TEXT NOT NULL DEFAULT 'DOCUMENTO'
                """
            )
            connection.execute(
                """
                UPDATE stored_documents
                SET category = CASE
                    WHEN UPPER(document_type) IN ('EXAME', 'ASO') THEN 'EXAME'
                    WHEN UPPER(document_type) = 'CERTIFICADO' THEN 'CERTIFICADO'
                    ELSE 'DOCUMENTO'
                END
                """
            )
        connection.commit()


def purge_demo_vehicle_documents() -> dict[str, int]:
    """Remove anexos explicitamente ligados à antiga frota fictícia."""
    init_document_storage()
    root = Path(settings.documents_root_path).resolve()
    placeholders = ",".join("?" for _ in DEMO_VEHICLE_IDENTIFIERS)
    identifiers = tuple(DEMO_VEHICLE_IDENTIFIERS)
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT id, owner_key, title, document_number, notes, relative_path
            FROM stored_documents
            WHERE owner_type='vehicle'
              AND UPPER(REPLACE(owner_key, '-', '')) IN ({placeholders})
            """,
            identifiers,
        ).fetchall()
        removed_files = 0
        removed_records = 0
        for row in rows:
            if not (
                str(row["owner_key"] or "").upper().replace("-", "")
                in DEMO_VEHICLE_IDENTIFIERS
                or any(
                    has_demo_marker(row[key])
                    for key in ("title", "document_number", "notes")
                )
            ):
                continue
            path = (root / str(row["relative_path"] or "")).resolve()
            if path != root and root not in path.parents:
                continue
            try:
                if path.is_file():
                    path.unlink()
                    removed_files += 1
            except OSError:
                continue
            connection.execute("DELETE FROM stored_documents WHERE id=?", (row["id"],))
            removed_records += 1
        connection.commit()
    return {"records": removed_records, "files": removed_files}


def decode_document_content(filename: str, content_base64: str) -> tuple[bytes, str, str]:
    clean_name = Path(str(filename or "")).name
    extension = Path(clean_name).suffix.lower()
    if not clean_name or extension not in _ALLOWED_EXTENSIONS:
        raise ValueError(
            "Formato não permitido. Use PDF, imagem, Word, Excel, CSV ou TXT."
        )
    encoded = str(content_base64 or "")
    if "," in encoded and encoded.lstrip().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("O arquivo enviado está corrompido ou incompleto.") from exc
    if not content:
        raise ValueError("O arquivo está vazio.")
    if len(content) > int(settings.document_file_max_bytes):
        limit_mb = int(settings.document_file_max_bytes) / (1024 * 1024)
        raise ValueError(f"O arquivo ultrapassa o limite de {limit_mb:.0f} MB.")
    mime = mimetypes.guess_type(clean_name)[0] or "application/octet-stream"
    return content, clean_name, mime


def _owner_folder(owner_type: str, owner_key: str) -> tuple[Path, str]:
    normalized_type = str(owner_type or "").strip().lower()
    if normalized_type not in _ALLOWED_OWNER_TYPES:
        raise ValueError("Tipo de proprietário inválido.")
    normalized_key = str(owner_key or "").strip()
    if not normalized_key:
        raise ValueError("Proprietário do documento não informado.")
    group = "vehicles" if normalized_type == "vehicle" else "employees"
    folder_key = _safe_component(normalized_key, "sem_identificacao")
    relative = Path(group) / folder_key
    absolute = Path(settings.documents_root_path) / relative
    absolute.mkdir(parents=True, exist_ok=True)
    return absolute, relative.as_posix()


def save_document(
    *,
    owner_type: str,
    owner_key: str,
    owner_name: str | None,
    document_type: str,
    filename: str,
    category: str = "DOCUMENTO",
    content: bytes,
    mime_type: str | None = None,
    title: str | None = None,
    document_number: str | None = None,
    issue_date: str | None = None,
    expiry_date: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    init_document_storage()
    normalized_category = str(category or "DOCUMENTO").strip().upper()
    if normalized_category not in _ALLOWED_DOCUMENT_CATEGORIES:
        raise ValueError("Categoria de documento inválida.")
    folder, relative_folder = _owner_folder(owner_type, owner_key)
    original_name = Path(filename).name
    safe_original = _safe_component(Path(original_name).stem, "documento")
    extension = Path(original_name).suffix.lower()
    stored_name = (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{secrets.token_hex(4)}_{safe_original}{extension}"
    )
    destination = folder / stored_name
    destination.write_bytes(content)
    relative_path = (Path(relative_folder) / stored_name).as_posix()
    digest = hashlib.sha256(content).hexdigest()
    timestamp = _now_iso()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO stored_documents (
                    owner_type, owner_key, owner_name, document_type, category, title,
                    document_number, issue_date, expiry_date, notes,
                    original_name, stored_name, relative_path, mime_type,
                    size_bytes, sha256, uploaded_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_type.lower(), str(owner_key), owner_name,
                    str(document_type or "DOCUMENTO").strip().upper(),
                    normalized_category,
                    title,
                    document_number, issue_date, expiry_date, notes,
                    original_name, stored_name, relative_path,
                    mime_type or mimetypes.guess_type(original_name)[0],
                    len(content), digest, timestamp, timestamp,
                ),
            )
            record_id = int(cursor.lastrowid)
            connection.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return get_document(record_id)


def list_documents(
    owner_type: str | None = None,
    owner_key: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    init_document_storage()
    conditions: list[str] = []
    params: list[Any] = []
    if owner_type:
        conditions.append("owner_type=?")
        params.append(str(owner_type).lower())
    if owner_key:
        conditions.append("owner_key=?")
        params.append(str(owner_key))
    if category:
        normalized_category = str(category).strip().upper()
        if normalized_category not in _ALLOWED_DOCUMENT_CATEGORIES:
            raise ValueError("Categoria de documento inválida.")
        conditions.append("category=?")
        params.append(normalized_category)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM stored_documents{where} ORDER BY uploaded_at DESC, id DESC",
            params,
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["download_url"] = f"/api/stored-documents/{item['id']}/download"
        item["folder"] = str(Path(item["relative_path"]).parent).replace("\\", "/")
    return result


def get_document(record_id: int) -> dict[str, Any]:
    init_document_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM stored_documents WHERE id=?", (int(record_id),)
        ).fetchone()
    if row is None:
        raise KeyError("Documento não encontrado.")
    item = dict(row)
    item["download_url"] = f"/api/stored-documents/{item['id']}/download"
    item["folder"] = str(Path(item["relative_path"]).parent).replace("\\", "/")
    return item


def document_path(record_id: int) -> tuple[dict[str, Any], Path]:
    item = get_document(record_id)
    root = Path(settings.documents_root_path).resolve()
    path = (root / item["relative_path"]).resolve()
    if root not in path.parents:
        raise ValueError("Caminho de documento inválido.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Arquivo do documento não foi localizado.")
    return item, path


def delete_document(record_id: int) -> dict[str, Any]:
    item = get_document(record_id)
    root = Path(settings.documents_root_path).resolve()
    path = (root / item["relative_path"]).resolve()
    if root in path.parents:
        path.unlink(missing_ok=True)
    with _connect() as connection:
        connection.execute("DELETE FROM stored_documents WHERE id=?", (int(record_id),))
        connection.commit()
    folder = path.parent
    try:
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
    return item
