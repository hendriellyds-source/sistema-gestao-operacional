from __future__ import annotations

import base64
import hashlib
import html
import imaplib
import re
import threading
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .aline_projects import (
    ALLOWED_EXTENSIONS,
    DocumentPayload,
    _audit,
    _connect,
    _decrypt,
    _encrypt,
    _insert_event,
    _normalized,
    _now,
    _require_profile,
    _require_vault,
    init_aline_projects,
    upload_document,
)
from .settings import settings


router = APIRouter(prefix="/api/aline/email", tags=["Aline — E-mail Terra"])
_sync_lock = threading.Lock()


class EmailSettingsPayload(BaseModel):
    username: str = Field(default="", max_length=240)
    password: str = Field(default="", max_length=240)
    enabled: bool = False
    interval_seconds: int = Field(default=300, ge=60, le=3600)
    allowed_senders: str = Field(default="", max_length=4000)
    mark_read: bool = False


class EmailAssignmentPayload(BaseModel):
    process_id: int = Field(gt=0)


def init_aline_email() -> None:
    init_aline_projects()
    Path(settings.aline_email_root_path).mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO aline_email_settings
                (id, enabled, host, port, interval_seconds, allowed_senders,
                 mark_read, last_uid, created_at, updated_at)
            VALUES (1, 0, 'imap.terra.com.br', 993, ?, '', 0, 0, ?, ?)
            """,
            (settings.aline_email_default_interval_seconds, _now(), _now()),
        )
        # A integração é somente leitura: nunca altera o estado da caixa Terra.
        connection.execute("UPDATE aline_email_settings SET mark_read=0 WHERE id=1")
        connection.commit()


def _settings(connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM aline_email_settings WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Configuração de e-mail não inicializada.")
    return dict(row)


def _public_settings(row: dict[str, Any]) -> dict[str, Any]:
    username = _decrypt(row.get("username_enc")) if row.get("username_enc") else ""
    return {
        "configured": bool(row.get("username_enc") and row.get("password_enc")),
        "enabled": bool(row.get("enabled")),
        "host": row.get("host") or "imap.terra.com.br",
        "port": int(row.get("port") or 993),
        "username": username,
        "interval_seconds": int(row.get("interval_seconds") or 300),
        "allowed_senders": row.get("allowed_senders") or "",
        "mark_read": False,
        "last_sync_at": row.get("last_sync_at"),
        "last_error": row.get("last_error"),
    }


def _plain_body(message: Message) -> str:
    values: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            value = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            value = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            value = re.sub(r"<[^>]+>", " ", value)
            value = html.unescape(value)
        values.append(str(value))
    return re.sub(r"\s+", " ", " ".join(values)).strip()


def _received_at(message: Message) -> str:
    try:
        value = parsedate_to_datetime(str(message.get("Date") or ""))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return _now()


def _allowed_sender(sender: str, configured: str) -> bool:
    rules = [item.strip().lower() for item in re.split(r"[,;\n]+", configured or "") if item.strip()]
    if not rules:
        return True
    address = parseaddr(sender)[1].lower()
    return any(
        address == rule
        or (rule.startswith("@") and address.endswith(rule))
        or ("@" not in rule and address.endswith(f"@{rule}"))
        for rule in rules
    )


def _match_process(connection, subject: str, body: str) -> tuple[dict[str, Any] | None, str, str]:
    haystack = f" {_normalized(subject + ' ' + body)} "
    matches = []
    for row in connection.execute(
        """
        SELECT id, title, third_code FROM aline_processes
        WHERE archived_at IS NULL AND TRIM(COALESCE(third_code, ''))<>''
        """
    ).fetchall():
        code = _normalized(row["third_code"])
        if code and f" {code} " in haystack:
            matches.append(dict(row))
    if len(matches) == 1:
        return matches[0], str(matches[0]["third_code"]), ""
    if len(matches) > 1:
        return None, "", "Mais de um processo corresponde ao terceiro código encontrado."
    return None, "", "Nenhum terceiro código cadastrado foi localizado no e-mail."


def _message_key(message: Message, raw: bytes) -> str:
    message_id = str(message.get("Message-ID") or "").strip()
    return hashlib.sha256((message_id.encode("utf-8") if message_id else raw)).hexdigest()


def _raw_path(message_key: str) -> tuple[Path, str]:
    relative = Path(message_key[:2]) / f"{message_key}.eml"
    root = Path(settings.aline_email_root_path)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, relative.as_posix()


def _attachment_parts(message: Message) -> list[tuple[str, bytes]]:
    result = []
    for part in message.walk():
        filename = Path(str(part.get_filename() or "")).name
        if not filename or Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        content = part.get_payload(decode=True) or b""
        if content and len(content) <= int(settings.aline_document_file_max_bytes):
            result.append((filename, content))
    return result


def _save_attachments(process_id: int, message: Message, message_key: str) -> int:
    saved = 0
    for filename, content in _attachment_parts(message):
        digest = hashlib.sha256(content).hexdigest()
        with _connect() as connection:
            exists = connection.execute(
                """
                SELECT id FROM aline_documents
                WHERE owner_type='process' AND owner_id=? AND sha256=? LIMIT 1
                """,
                (process_id, digest),
            ).fetchone()
        if exists:
            continue
        upload_document(
            DocumentPayload(
                owner_type="process",
                owner_id=process_id,
                category="E-mail automático",
                title=filename,
                notes=f"Anexo recebido automaticamente. Mensagem {message_key[:12]}.",
                filename=filename,
                data_url=base64.b64encode(content).decode("ascii"),
            ),
            x_portfolio_profile="Automação de e-mail",
        )
        saved += 1
    return saved


def _link_message(message_id: int, process_id: int) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM aline_email_messages WHERE id=?", (message_id,)
        ).fetchone()
        process = connection.execute(
            "SELECT id, title, third_code FROM aline_processes WHERE id=?", (process_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "E-mail não encontrado.")
        if not process:
            raise HTTPException(404, "Processo não encontrado.")
        if row["status"] == "matched":
            if int(row["process_id"] or 0) == process_id:
                return {
                    "ok": True, "message_id": message_id, "process_id": process_id,
                    "attachments": int(row["attachment_count"] or 0), "duplicate": True,
                }
            raise HTTPException(409, "Este e-mail já foi associado a outro processo.")
        raw_path = Path(settings.aline_email_root_path) / str(row["raw_relative_path"] or "")
        if not raw_path.is_file():
            raise HTTPException(409, "A mensagem original não está mais disponível para processar anexos.")
        raw = raw_path.read_bytes()
        message = BytesParser(policy=policy.default).parsebytes(raw)

    attachments = _save_attachments(process_id, message, row["message_key"])
    description = f"E-mail recebido de {row['sender']}: {row['subject'] or '(sem assunto)'}"
    with _connect() as connection:
        connection.execute(
            """
            UPDATE aline_email_messages
            SET process_id=?, third_code=?, status='matched', attachment_count=?,
                error=NULL, processed_at=? WHERE id=?
            """,
            (process_id, process["third_code"] or "", attachments, _now(), message_id),
        )
        _insert_event(
            connection, "process", process_id, "E-mail automático", description,
            "Automação de e-mail", event_date=str(row["received_at"] or "")[:10],
            metadata={"email_message_id": message_id, "message_key": row["message_key"]},
        )
        _audit(
            connection, entity_type="email", entity_id=message_id,
            action="linked", actor="Automação de e-mail",
            after={"process_id": process_id, "attachment_count": attachments},
        )
        connection.commit()
    return {
        "ok": True, "message_id": message_id, "process_id": process_id,
        "attachments": attachments, "status": "matched",
    }


def _store_message(uid: int, raw: bytes, allowed_senders: str) -> dict[str, Any]:
    if len(raw) > int(settings.aline_email_max_bytes):
        raise ValueError("E-mail acima do limite configurado para processamento.")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    key = _message_key(message, raw)
    sender = str(message.get("From") or "")[:500]
    subject = str(message.get("Subject") or "")[:1000]
    body = _plain_body(message)
    with _connect() as connection:
        existing = connection.execute(
            "SELECT id, status FROM aline_email_messages WHERE message_key=?", (key,)
        ).fetchone()
        if existing:
            return {"id": existing["id"], "status": existing["status"], "duplicate": True}
        process, third_code, match_error = _match_process(connection, subject, body)
        if not _allowed_sender(sender, allowed_senders):
            return {"status": "skipped", "reason": "sender", "duplicate": False}
        if not process:
            return {"status": "skipped", "reason": match_error, "duplicate": False}
    path, relative = _raw_path(key)
    path.write_bytes(raw)
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO aline_email_messages
                (message_key, imap_uid, message_id, sender, subject, received_at,
                 third_code, process_id, status, body_excerpt, raw_relative_path,
                 attachment_count, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                key, uid, str(message.get("Message-ID") or "")[:500], sender, subject,
                _received_at(message), third_code, process["id"],
                "processing", body[:4000], relative, None, _now(),
            ),
        )
        record_id = int(cursor.lastrowid)
        connection.commit()
    try:
        return _link_message(record_id, int(process["id"]))
    except Exception as exc:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE aline_email_messages SET status='unmatched', process_id=NULL,
                    error=? WHERE id=?
                """,
                (f"Falha ao importar e-mail relacionado: {str(exc)[:800]}", record_id),
            )
            connection.commit()
        return {"id": record_id, "status": "unmatched", "duplicate": False}


def sync_aline_email_once(
    imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
) -> dict[str, Any]:
    init_aline_email()
    if not _sync_lock.acquire(blocking=False):
        return {"ok": True, "running": True, "processed": 0}
    client = None
    processed = matched = unmatched = ignored = 0
    try:
        with _connect() as connection:
            config = _settings(connection)
        if not config.get("enabled"):
            return {"ok": True, "enabled": False, "processed": 0}
        username = _decrypt(config.get("username_enc"))
        password = _decrypt(config.get("password_enc"))
        if not username or not password:
            raise RuntimeError("Configure o e-mail e a senha do Terra antes de ativar a automação.")
        try:
            client = imap_factory(
                config.get("host") or "imap.terra.com.br",
                int(config.get("port") or 993), timeout=30,
            )
        except TypeError:
            client = imap_factory(
                config.get("host") or "imap.terra.com.br", int(config.get("port") or 993)
            )
        client.login(username, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Não foi possível abrir a caixa de entrada do Terra.")
        last_uid = int(config.get("last_uid") or 0)
        criteria = f"UID {last_uid + 1}:*" if last_uid else "ALL"
        status, data = client.uid("search", None, criteria)
        if status != "OK":
            raise RuntimeError("Não foi possível consultar as mensagens do Terra.")
        uids = sorted({int(value) for value in b" ".join(data or []).split() if value.isdigit()})
        if not last_uid:
            uids = uids[-int(settings.aline_email_max_messages_per_sync):]
        else:
            uids = uids[:int(settings.aline_email_max_messages_per_sync)]
        for uid in uids:
            status, fetched = client.uid("fetch", str(uid), "(RFC822)")
            if status != "OK":
                continue
            raw = next((item[1] for item in fetched if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if not raw:
                continue
            result = _store_message(uid, raw, config.get("allowed_senders") or "")
            processed += 1
            matched += int(result.get("status") == "matched")
            unmatched += int(result.get("status") == "unmatched")
            ignored += int(result.get("status") == "ignored")
            last_uid = max(last_uid, uid)
            with _connect() as connection:
                connection.execute(
                    "UPDATE aline_email_settings SET last_uid=?, updated_at=? WHERE id=1",
                    (last_uid, _now()),
                )
                connection.commit()
        with _connect() as connection:
            connection.execute(
                "UPDATE aline_email_settings SET last_sync_at=?, last_error=NULL, updated_at=? WHERE id=1",
                (_now(), _now()),
            )
            connection.commit()
        return {"ok": True, "enabled": True, "processed": processed, "matched": matched, "unmatched": unmatched, "ignored": ignored}
    except Exception as exc:
        with _connect() as connection:
            connection.execute(
                "UPDATE aline_email_settings SET last_sync_at=?, last_error=?, updated_at=? WHERE id=1",
                (_now(), str(exc)[:1000], _now()),
            )
            connection.commit()
        return {"ok": False, "processed": processed, "error": str(exc)}
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
        _sync_lock.release()


def aline_email_interval_seconds() -> int:
    init_aline_email()
    with _connect() as connection:
        row = _settings(connection)
    return max(60, min(3600, int(row.get("interval_seconds") or 300)))


@router.get("/status")
def email_status() -> dict[str, Any]:
    init_aline_email()
    with _connect() as connection:
        result = _public_settings(_settings(connection))
        result["counts"] = {
            row["status"]: row["total"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS total FROM aline_email_messages GROUP BY status"
            ).fetchall()
        }
    return result


@router.put("/settings")
def save_email_settings(
    payload: EmailSettingsPayload,
    x_aline_vault_pin: str = Header(""),
    x_portfolio_profile: str = Header("Aline"),
) -> dict[str, Any]:
    _require_profile(x_portfolio_profile)
    init_aline_email()
    with _connect() as connection:
        current = _settings(connection)
        current_username = _decrypt(current.get("username_enc"))
        username_changed = bool(payload.username and payload.username != current_username)
        credentials_changed = bool(
            payload.password or username_changed
        )
        if credentials_changed:
            _require_vault(connection, x_aline_vault_pin)
        username_enc = _encrypt(payload.username) if username_changed else current.get("username_enc")
        password_enc = _encrypt(payload.password) if payload.password else current.get("password_enc")
        if payload.enabled and not (username_enc and password_enc):
            raise HTTPException(422, "Informe o e-mail e a senha antes de ativar a automação.")
        connection.execute(
            """
            UPDATE aline_email_settings SET enabled=?, host='imap.terra.com.br', port=993,
                username_enc=?, password_enc=?, interval_seconds=?, allowed_senders=?,
                mark_read=?, last_uid=CASE WHEN ? THEN 0 ELSE last_uid END,
                last_error=NULL, updated_at=? WHERE id=1
            """,
            (
                int(payload.enabled), username_enc, password_enc, payload.interval_seconds,
                payload.allowed_senders.strip(), 0,
                int(username_changed), _now(),
            ),
        )
        _audit(
            connection, entity_type="email_settings", entity_id=1,
            action="update", actor=x_portfolio_profile,
            note="Configuração do Terra atualizada; senha não registrada no histórico.",
        )
        connection.commit()
        return _public_settings(_settings(connection))


@router.post("/sync")
def sync_email_now(x_portfolio_profile: str = Header("Aline")) -> dict[str, Any]:
    _require_profile(x_portfolio_profile)
    result = sync_aline_email_once()
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "Falha ao consultar o Terra.")
    return result


@router.get("/messages")
def list_email_messages(
    status: str = Query("", pattern="^(|matched|unmatched|ignored|processing)$"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    init_aline_email()
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE m.status=?"
        params.append(status)
    params.append(limit)
    with _connect() as connection:
        return [
            dict(row) for row in connection.execute(
                f"""
                SELECT m.id, m.sender, m.subject, m.received_at, m.third_code,
                       m.process_id, m.status, m.body_excerpt, m.attachment_count,
                       m.error, p.title AS process_title, c.name AS client_name
                FROM aline_email_messages m
                LEFT JOIN aline_processes p ON p.id=m.process_id
                LEFT JOIN aline_clients c ON c.id=p.client_id
                {where}
                ORDER BY COALESCE(m.received_at, m.created_at) DESC, m.id DESC LIMIT ?
                """,
                params,
            ).fetchall()
        ]


@router.post("/messages/{message_id}/assign")
def assign_email_message(
    message_id: int,
    payload: EmailAssignmentPayload,
    x_portfolio_profile: str = Header("Aline"),
) -> dict[str, Any]:
    _require_profile(x_portfolio_profile)
    return _link_message(message_id, payload.process_id)
