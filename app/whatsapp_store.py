from __future__ import annotations

import json
import sqlite3
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from hashlib import sha256
import hmac
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken

from app.settings import settings


def _database_path() -> Path:
    configured_path = getattr(settings, "whatsapp_db_path", "data/whatsapp.db")
    path = Path(configured_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _credential_cipher() -> Fernet:
    app_secret = settings.whatsapp_app_secret.strip()
    if not app_secret:
        raise RuntimeError(
            "WHATSAPP_APP_SECRET não configurado para proteger a credencial."
        )
    digest = hmac.new(
        app_secret.encode("utf-8"),
        b"operational-whatsapp-operational-token-v1",
        sha256,
    ).digest()
    return Fernet(urlsafe_b64encode(digest))


def _encrypt_access_token(access_token: str) -> str:
    return _credential_cipher().encrypt(access_token.encode("utf-8")).decode("ascii")


def _decrypt_access_token(access_token_enc: str) -> str:
    try:
        return _credential_cipher().decrypt(
            access_token_enc.encode("ascii")
        ).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "A credencial do WhatsApp não pôde ser descriptografada. "
            "Refaça a conexão pela Meta."
        ) from exc


_PUBLIC_CONNECTION_COLUMNS = """
    id, business_id, waba_id, phone_number_id, business_name,
    display_phone_number, verified_name, connection_status,
    connected_by, connected_at, disconnected_by, disconnected_at,
    updated_at, is_active,
    CASE WHEN access_token_enc IS NOT NULL THEN 1 ELSE 0 END AS has_access_token
"""

_initialized_databases: set[Path] = set()
_database_init_lock = Lock()
_UNSET = object()


def initialize_whatsapp_database() -> None:
    database_path = _database_path().resolve()
    if database_path in _initialized_databases and database_path.exists():
        return
    with _database_init_lock:
        if database_path in _initialized_databases and database_path.exists():
            return
        _initialize_whatsapp_database_schema()
        _initialized_databases.add(database_path)


def _initialize_whatsapp_database_schema() -> None:
    with _connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                sender_phone TEXT,
                message_id TEXT,
                message_text TEXT,
                payload_json TEXT NOT NULL,
                received_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS whatsapp_contacts (
                phone TEXT PRIMARY KEY,
                profile_name TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_phone TEXT NOT NULL,
                queue_key TEXT,
                queue_label TEXT,
                assignee TEXT,
                assignee_profile_id TEXT,
                assignment_version INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'awaiting_menu',
                unread_count INTEGER NOT NULL DEFAULT 0,
                last_message_text TEXT,
                last_message_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                FOREIGN KEY(contact_phone)
                    REFERENCES whatsapp_contacts(phone)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                message_id TEXT,
                direction TEXT NOT NULL,
                sender_phone TEXT,
                message_type TEXT NOT NULL DEFAULT 'text',
                body TEXT,
                status TEXT,
                actor_name TEXT,
                source TEXT NOT NULL DEFAULT 'api',
                media_id TEXT,
                media_mime_type TEXT,
                media_filename TEXT,
                media_path TEXT,
                meta_read_at DATETIME,
                payload_json TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id)
                    REFERENCES whatsapp_conversations(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS whatsapp_business_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id TEXT,
                waba_id TEXT NOT NULL UNIQUE,
                phone_number_id TEXT,
                business_name TEXT,
                display_phone_number TEXT,
                verified_name TEXT,
                connection_status TEXT NOT NULL DEFAULT 'connected',
                access_token_enc TEXT,
                is_active INTEGER NOT NULL DEFAULT 0,
                connected_by TEXT,
                connected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                disconnected_by TEXT,
                disconnected_at DATETIME,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS whatsapp_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                color TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS whatsapp_contact_labels (
                contact_phone TEXT NOT NULL,
                label_id INTEGER NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(contact_phone, label_id),
                FOREIGN KEY(contact_phone)
                    REFERENCES whatsapp_contacts(phone) ON DELETE CASCADE,
                FOREIGN KEY(label_id)
                    REFERENCES whatsapp_labels(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS whatsapp_conversation_reads (
                conversation_id INTEGER NOT NULL,
                user_key TEXT NOT NULL,
                last_read_message_row_id INTEGER NOT NULL DEFAULT 0,
                last_read_assignment_version INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(conversation_id, user_key),
                FOREIGN KEY(conversation_id)
                    REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS whatsapp_schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
                whatsapp_messages_message_id_unique
                ON whatsapp_messages(message_id)
                WHERE message_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS
                whatsapp_conversations_queue_status_idx
                ON whatsapp_conversations(queue_key, status, last_message_at DESC);

            CREATE INDEX IF NOT EXISTS
                whatsapp_messages_conversation_idx
                ON whatsapp_messages(conversation_id, id);

            CREATE INDEX IF NOT EXISTS
                whatsapp_messages_conversation_time_idx
                ON whatsapp_messages(conversation_id, created_at DESC, id DESC);

            CREATE UNIQUE INDEX IF NOT EXISTS
                whatsapp_one_open_conversation_per_contact
                ON whatsapp_conversations(contact_phone)
                WHERE status IN ('awaiting_menu', 'open', 'pending');

            CREATE INDEX IF NOT EXISTS whatsapp_contact_labels_label_idx
                ON whatsapp_contact_labels(label_id, contact_phone);

            CREATE INDEX IF NOT EXISTS whatsapp_conversation_reads_user_idx
                ON whatsapp_conversation_reads(user_key, conversation_id);
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(whatsapp_business_connections)"
            ).fetchall()
        }
        if "access_token_enc" not in columns:
            connection.execute(
                "ALTER TABLE whatsapp_business_connections "
                "ADD COLUMN access_token_enc TEXT"
            )
        if "is_active" not in columns:
            connection.execute(
                "ALTER TABLE whatsapp_business_connections "
                "ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0"
            )
        if "disconnected_by" not in columns:
            connection.execute(
                "ALTER TABLE whatsapp_business_connections "
                "ADD COLUMN disconnected_by TEXT"
            )
        if "disconnected_at" not in columns:
            connection.execute(
                "ALTER TABLE whatsapp_business_connections "
                "ADD COLUMN disconnected_at DATETIME"
            )
        message_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(whatsapp_messages)"
            ).fetchall()
        }
        message_column_definitions = {
            "actor_name": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'api'",
            "media_id": "TEXT",
            "media_mime_type": "TEXT",
            "media_filename": "TEXT",
            "media_path": "TEXT",
            "meta_read_at": "DATETIME",
        }
        for column_name, definition in message_column_definitions.items():
            if column_name not in message_columns:
                connection.execute(
                    f"ALTER TABLE whatsapp_messages ADD COLUMN {column_name} {definition}"
                )
        conversation_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(whatsapp_conversations)"
            ).fetchall()
        }
        if "assignment_version" not in conversation_columns:
            connection.execute(
                "ALTER TABLE whatsapp_conversations "
                "ADD COLUMN assignment_version INTEGER NOT NULL DEFAULT 0"
            )
        if "assignee_profile_id" not in conversation_columns:
            connection.execute(
                "ALTER TABLE whatsapp_conversations ADD COLUMN assignee_profile_id TEXT"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS whatsapp_conversations_assignee_profile_idx "
            "ON whatsapp_conversations(assignee_profile_id, status, last_message_at DESC)"
        )
        read_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(whatsapp_conversation_reads)"
            ).fetchall()
        }
        if "last_read_assignment_version" not in read_columns:
            connection.execute(
                "ALTER TABLE whatsapp_conversation_reads "
                "ADD COLUMN last_read_assignment_version INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS whatsapp_business_connections_active_idx
            ON whatsapp_business_connections(is_active, updated_at DESC)
            """
        )
        if connection.execute(
            "SELECT 1 FROM whatsapp_schema_migrations WHERE name = ?",
            ("message_event_time_v1",),
        ).fetchone() is None:
            connection.execute(
                """
                UPDATE whatsapp_conversations
                SET last_message_at = (
                        SELECT message.created_at
                        FROM whatsapp_messages AS message
                        WHERE message.conversation_id = whatsapp_conversations.id
                        ORDER BY message.created_at DESC, message.id DESC
                        LIMIT 1
                    ),
                    last_message_text = (
                        SELECT COALESCE(NULLIF(message.body, ''),
                                        '[' || message.message_type || ']')
                        FROM whatsapp_messages AS message
                        WHERE message.conversation_id = whatsapp_conversations.id
                        ORDER BY message.created_at DESC, message.id DESC
                        LIMIT 1
                    )
                WHERE EXISTS (
                    SELECT 1 FROM whatsapp_messages AS message
                    WHERE message.conversation_id = whatsapp_conversations.id
                )
                """
            )
            connection.execute(
                "INSERT INTO whatsapp_schema_migrations (name) VALUES (?)",
                ("message_event_time_v1",),
            )
        connection.commit()


def save_whatsapp_business_connection(
    *,
    waba_id: str,
    business_id: str | None = None,
    phone_number_id: str | None = None,
    business_name: str | None = None,
    display_phone_number: str | None = None,
    verified_name: str | None = None,
    connection_status: str = "connected",
    connected_by: str | None = None,
    access_token: str | None = None,
    activate: bool = False,
) -> dict[str, Any]:
    """Save Meta connection metadata and an encrypted operational credential."""
    initialize_whatsapp_database()
    access_token_enc = (
        _encrypt_access_token(access_token.strip())
        if access_token and access_token.strip()
        else None
    )
    with _connection() as connection:
        if activate:
            connection.execute(
                "UPDATE whatsapp_business_connections SET is_active = 0"
            )
        connection.execute(
            """
            INSERT INTO whatsapp_business_connections (
                business_id,
                waba_id,
                phone_number_id,
                business_name,
                display_phone_number,
                verified_name,
                connection_status,
                access_token_enc,
                is_active,
                connected_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(waba_id) DO UPDATE SET
                business_id = COALESCE(excluded.business_id, business_id),
                phone_number_id = COALESCE(excluded.phone_number_id, phone_number_id),
                business_name = COALESCE(excluded.business_name, business_name),
                display_phone_number = COALESCE(
                    excluded.display_phone_number,
                    display_phone_number
                ),
                verified_name = COALESCE(excluded.verified_name, verified_name),
                connection_status = excluded.connection_status,
                access_token_enc = COALESCE(
                    excluded.access_token_enc,
                    access_token_enc
                ),
                is_active = CASE
                    WHEN excluded.is_active = 1 THEN 1 ELSE is_active
                END,
                connected_by = COALESCE(excluded.connected_by, connected_by),
                disconnected_by = NULL,
                disconnected_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                business_id,
                waba_id,
                phone_number_id,
                business_name,
                display_phone_number,
                verified_name,
                connection_status,
                access_token_enc,
                int(activate),
                connected_by,
            ),
        )
        row = connection.execute(
            f"""
            SELECT {_PUBLIC_CONNECTION_COLUMNS}
            FROM whatsapp_business_connections
            WHERE waba_id = ?
            """,
            (waba_id,),
        ).fetchone()
        return dict(row)


def list_whatsapp_business_connections(*, limit: int = 20) -> list[dict[str, Any]]:
    initialize_whatsapp_database()
    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT {_PUBLIC_CONNECTION_COLUMNS}
            FROM whatsapp_business_connections
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_active_whatsapp_business_connection() -> dict[str, Any] | None:
    """Return active non-secret metadata for status screens."""
    initialize_whatsapp_database()
    with _connection() as connection:
        row = connection.execute(
            f"""
            SELECT {_PUBLIC_CONNECTION_COLUMNS}
            FROM whatsapp_business_connections
            WHERE is_active = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row is not None else None


def disconnect_whatsapp_business_connection(
    connection_id: int, *, waba_id: str, disconnected_by: str,
) -> dict[str, Any]:
    """Stop using a saved account without deleting its number or message history."""
    initialize_whatsapp_database()
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, waba_id, connection_status FROM whatsapp_business_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Conta do WhatsApp não encontrada.")
        if str(row["waba_id"]) != str(waba_id):
            raise ValueError("A conta selecionada mudou. Atualize a página e tente novamente.")
        if str(row["connection_status"]) == "disconnected":
            raise ValueError("Esta conta já está desconectada da central.")
        connection.execute(
            """
            UPDATE whatsapp_business_connections
            SET is_active = 0,
                connection_status = 'disconnected',
                access_token_enc = NULL,
                disconnected_by = ?,
                disconnected_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND waba_id = ?
            """,
            (disconnected_by, connection_id, waba_id),
        )
        updated = connection.execute(
            f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM whatsapp_business_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        return dict(updated)


def get_active_whatsapp_credentials() -> tuple[str, str] | None:
    """Return the active phone id and decrypted token for server-side API calls."""
    initialize_whatsapp_database()
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT phone_number_id, access_token_enc
            FROM whatsapp_business_connections
            WHERE is_active = 1
              AND phone_number_id IS NOT NULL
              AND access_token_enc IS NOT NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return str(row["phone_number_id"]), _decrypt_access_token(
        str(row["access_token_enc"])
    )


def save_whatsapp_event(
    *,
    payload: dict[str, Any],
    event_type: str | None = None,
    sender_phone: str | None = None,
    message_id: str | None = None,
    message_text: str | None = None,
) -> None:
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO whatsapp_events (
                event_type, sender_phone, message_id, message_text, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                sender_phone,
                message_id,
                message_text,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()


def record_incoming_message(
    *,
    payload: dict[str, Any],
    sender_phone: str,
    message_id: str,
    message_text: str | None,
    message_type: str,
    profile_name: str | None = None,
    source: str = "whatsapp",
    media_id: str | None = None,
    media_mime_type: str | None = None,
    media_filename: str | None = None,
    created_at: str | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    """Persist an inbound message and return its active conversation.

    The booleans indicate whether the message was new and whether this contact
    had any earlier message. Meta can retry deliveries, so replies happen once.
    """
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO whatsapp_contacts (phone, profile_name)
            VALUES (?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                profile_name = COALESCE(excluded.profile_name, profile_name),
                updated_at = CURRENT_TIMESTAMP
            """,
            (sender_phone, profile_name),
        )

        conversation = connection.execute(
            """
            SELECT * FROM whatsapp_conversations
            WHERE contact_phone = ?
              AND status IN ('awaiting_menu', 'open', 'pending')
            ORDER BY id DESC LIMIT 1
            """,
            (sender_phone,),
        ).fetchone()

        if conversation is None:
            cursor = connection.execute(
                """
                INSERT INTO whatsapp_conversations (
                    contact_phone, status, last_message_at
                ) VALUES (?, 'awaiting_menu', ?)
                """,
                (sender_phone, created_at if source == "history" else None),
            )
            conversation_id = int(cursor.lastrowid)
        else:
            conversation_id = int(conversation["id"])

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO whatsapp_messages (
                conversation_id, message_id, direction, sender_phone,
                message_type, body, status, source, media_id,
                media_mime_type, media_filename, payload_json, created_at
            ) VALUES (?, ?, 'inbound', ?, ?, ?, 'received', ?, ?, ?, ?, ?,
                COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                conversation_id,
                message_id,
                sender_phone,
                message_type,
                message_text,
                source,
                media_id,
                media_mime_type,
                media_filename,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )
        inserted = cursor.rowcount == 1
        is_first_contact_message = False
        if inserted:
            is_first_contact_message = not bool(connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM whatsapp_messages AS message
                    JOIN whatsapp_conversations AS existing
                      ON existing.id = message.conversation_id
                    WHERE existing.contact_phone = ? AND message.id < ?
                )
                """,
                (sender_phone, cursor.lastrowid),
            ).fetchone()[0])

        if inserted and source != "history":
            connection.execute(
                """
                UPDATE whatsapp_conversations
                SET unread_count = unread_count + 1,
                    last_message_text = CASE
                        WHEN last_message_at IS NULL
                          OR last_message_at <= COALESCE(?, CURRENT_TIMESTAMP)
                        THEN ? ELSE last_message_text END,
                    last_message_at = CASE
                        WHEN last_message_at IS NULL
                          OR last_message_at <= COALESCE(?, CURRENT_TIMESTAMP)
                        THEN COALESCE(?, CURRENT_TIMESTAMP)
                        ELSE last_message_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    created_at,
                    message_text or f"[{message_type}]",
                    created_at,
                    created_at,
                    conversation_id,
                ),
            )
        elif inserted:
            connection.execute(
                """
                UPDATE whatsapp_conversations
                SET last_message_text = CASE
                        WHEN last_message_at IS NULL OR last_message_at <= ?
                        THEN ? ELSE last_message_text END,
                    last_message_at = CASE
                        WHEN last_message_at IS NULL OR last_message_at <= ?
                        THEN ? ELSE last_message_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    created_at,
                    message_text or f"[{message_type}]",
                    created_at,
                    created_at,
                    conversation_id,
                ),
            )

        connection.commit()
        result = get_conversation(conversation_id, connection=connection)

    if result is None:
        raise RuntimeError("A conversa do WhatsApp não pôde ser criada.")
    return result, inserted, is_first_contact_message


def record_outbound_message(
    *,
    conversation_id: int,
    message_id: str | None,
    body: str,
    status: str = "sent",
    payload: dict[str, Any] | None = None,
    actor_name: str | None = None,
    source: str = "api",
    message_type: str = "text",
    media_id: str | None = None,
    media_mime_type: str | None = None,
    media_filename: str | None = None,
    media_path: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    initialize_whatsapp_database()
    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO whatsapp_messages (
                conversation_id, message_id, direction, message_type,
                body, status, actor_name, source, media_id,
                media_mime_type, media_filename, media_path, payload_json,
                created_at
            ) VALUES (?, ?, 'outbound', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                conversation_id,
                message_id,
                message_type,
                body,
                status,
                actor_name,
                source,
                media_id,
                media_mime_type,
                media_filename,
                media_path,
                json.dumps(payload or {}, ensure_ascii=False),
                created_at,
            ),
        )
        if cursor.rowcount != 1 and message_id:
            row = connection.execute(
                "SELECT * FROM whatsapp_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM whatsapp_messages WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()

        preview = body or f"[{message_type}]"
        if source == "history" and cursor.rowcount == 1:
            connection.execute(
                """
                UPDATE whatsapp_conversations
                SET last_message_text = CASE
                        WHEN last_message_at IS NULL OR last_message_at <= ?
                        THEN ? ELSE last_message_text END,
                    last_message_at = CASE
                        WHEN last_message_at IS NULL OR last_message_at <= ?
                        THEN ? ELSE last_message_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (created_at, preview, created_at, created_at, conversation_id),
            )
        elif cursor.rowcount == 1:
            connection.execute(
                """
                UPDATE whatsapp_conversations
                SET last_message_text = CASE
                        WHEN last_message_at IS NULL
                          OR last_message_at <= COALESCE(?, CURRENT_TIMESTAMP)
                        THEN ? ELSE last_message_text END,
                    last_message_at = CASE
                        WHEN last_message_at IS NULL
                          OR last_message_at <= COALESCE(?, CURRENT_TIMESTAMP)
                        THEN COALESCE(?, CURRENT_TIMESTAMP)
                        ELSE last_message_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (created_at, preview, created_at, created_at, conversation_id),
            )
        connection.commit()
        return dict(row) if row is not None else {}


def get_or_create_outbound_conversation(
    *,
    contact_phone: str,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Return the active conversation used for a business-initiated message."""
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO whatsapp_contacts (phone, profile_name)
            VALUES (?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                profile_name = COALESCE(excluded.profile_name, profile_name),
                updated_at = CURRENT_TIMESTAMP
            """,
            (contact_phone, profile_name),
        )
        conversation = connection.execute(
            """
            SELECT * FROM whatsapp_conversations
            WHERE contact_phone = ?
              AND status IN ('awaiting_menu', 'open', 'pending')
            ORDER BY id DESC LIMIT 1
            """,
            (contact_phone,),
        ).fetchone()
        if conversation is None:
            cursor = connection.execute(
                """
                INSERT INTO whatsapp_conversations (
                    contact_phone, status
                ) VALUES (?, 'open')
                """,
                (contact_phone,),
            )
            conversation_id = int(cursor.lastrowid)
        else:
            conversation_id = int(conversation["id"])
        connection.commit()
        result = get_conversation(conversation_id, connection=connection)

    if result is None:
        raise RuntimeError("A conversa do WhatsApp não pôde ser criada.")
    return result


def update_message_status(message_id: str, status: str) -> None:
    if not message_id:
        return
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            "UPDATE whatsapp_messages SET status = ? WHERE message_id = ?",
            (status, message_id),
        )
        connection.commit()


def whatsapp_sync_diagnostics() -> dict[str, Any]:
    """Return content-free counts for diagnosing coexistence synchronization."""
    initialize_whatsapp_database()
    with _connection() as connection:
        active = connection.execute(
            "SELECT waba_id FROM whatsapp_business_connections WHERE is_active = 1 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        active_waba_id = str(active["waba_id"] or "") if active else ""
        event_rows = connection.execute(
            "SELECT payload_json, received_at FROM whatsapp_events ORDER BY id"
        ).fetchall()
        message_rows = connection.execute(
            """
            SELECT direction, source, COUNT(*) AS total,
                   MAX(created_at) AS last_message_at
            FROM whatsapp_messages
            GROUP BY direction, source
            """
        ).fetchall()

    events: dict[str, dict[str, Any]] = {}
    history_batches = 0
    history_threads = 0
    history_messages = 0
    history_errors: dict[str, int] = {}
    history_phases: dict[str, int] = {}
    echo_messages = 0
    echo_missing_recipient_or_id = 0
    for row in event_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        for entry in payload.get("entry") or []:
            if active_waba_id and str(entry.get("id") or "") != active_waba_id:
                continue
            for change in entry.get("changes") or []:
                field = str(change.get("field") or "unknown")
                event = events.setdefault(field, {"total": 0, "last_received_at": None})
                event["total"] += 1
                event["last_received_at"] = row["received_at"]
                if field == "smb_message_echoes":
                    for message in (change.get("value") or {}).get("message_echoes") or []:
                        echo_messages += 1
                        if not message.get("to") or not message.get("id"):
                            echo_missing_recipient_or_id += 1
                if change.get("field") != "history":
                    continue
                for batch in (change.get("value") or {}).get("history") or []:
                    history_batches += 1
                    phase = (batch.get("metadata") or {}).get("phase")
                    if phase is not None:
                        key = str(phase)
                        history_phases[key] = history_phases.get(key, 0) + 1
                    for error in batch.get("errors") or []:
                        key = str(error.get("code") or "unknown")
                        history_errors[key] = history_errors.get(key, 0) + 1
                    threads = batch.get("threads") or []
                    history_threads += len(threads)
                    history_messages += sum(
                        len(thread.get("messages") or []) for thread in threads
                    )

    return {
        "event_scope": "active_waba" if active_waba_id else "all_wabas",
        "events": events,
        "message_scope": "all_connections",
        "echo": {
            "messages_in_webhooks": echo_messages,
            "missing_recipient_or_id": echo_missing_recipient_or_id,
        },
        "messages": {
            f"{row['direction']}:{row['source']}": {
                "total": row["total"],
                "last_message_at": row["last_message_at"],
            }
            for row in message_rows
        },
        "history": {
            "batches": history_batches,
            "threads": history_threads,
            "messages_in_webhooks": history_messages,
            "phases": history_phases,
            "errors": history_errors,
        },
    }


def update_message_media(
    message_id: str,
    *,
    media_path: str,
    media_mime_type: str | None = None,
    media_filename: str | None = None,
) -> None:
    if not message_id:
        return
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE whatsapp_messages
            SET media_path = ?,
                media_mime_type = COALESCE(?, media_mime_type),
                media_filename = COALESCE(?, media_filename)
            WHERE message_id = ?
            """,
            (media_path, media_mime_type, media_filename, message_id),
        )
        connection.commit()


def get_message(message_row_id: int) -> dict[str, Any] | None:
    initialize_whatsapp_database()
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM whatsapp_messages WHERE id = ?",
            (message_row_id,),
        ).fetchone()
    return _row(row)


def get_message_by_provider_id(message_id: str) -> dict[str, Any] | None:
    if not message_id:
        return None
    initialize_whatsapp_database()
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM whatsapp_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    return _row(row)


def upsert_whatsapp_contact(*, phone: str, profile_name: str | None) -> None:
    if not phone:
        return
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO whatsapp_contacts (phone, profile_name)
            VALUES (?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                profile_name = COALESCE(excluded.profile_name, profile_name),
                updated_at = CURRENT_TIMESTAMP
            """,
            (phone, profile_name),
        )


def list_labels() -> list[dict[str, Any]]:
    initialize_whatsapp_database()
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM whatsapp_labels ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def create_label(*, name: str, color: str) -> dict[str, Any]:
    normalized_name = " ".join(str(name or "").split()).strip()
    normalized_color = str(color or "").strip().lower()
    if not normalized_name:
        raise ValueError("Informe o nome da etiqueta.")
    if len(normalized_name) > 40:
        raise ValueError("O nome da etiqueta deve ter até 40 caracteres.")
    if len(normalized_color) != 7 or not normalized_color.startswith("#") or any(
        character not in "0123456789abcdef" for character in normalized_color[1:]
    ):
        raise ValueError("Informe uma cor válida para a etiqueta.")
    initialize_whatsapp_database()
    with _connection() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO whatsapp_labels (name, color) VALUES (?, ?)",
                (normalized_name, normalized_color),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Já existe uma etiqueta com esse nome.") from exc
        row = connection.execute(
            "SELECT * FROM whatsapp_labels WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def set_contact_labels(
    *, contact_phone: str, label_ids: list[int], primary_label_id: int | None
) -> list[dict[str, Any]]:
    unique_ids = list(dict.fromkeys(int(label_id) for label_id in label_ids))
    if primary_label_id is not None and int(primary_label_id) not in unique_ids:
        raise ValueError("A etiqueta principal precisa estar selecionada.")
    initialize_whatsapp_database()
    with _connection() as connection:
        existing = {
            int(row["id"])
            for row in connection.execute(
                f"SELECT id FROM whatsapp_labels WHERE id IN ({','.join('?' for _ in unique_ids)})",
                unique_ids,
            ).fetchall()
        } if unique_ids else set()
        if existing != set(unique_ids):
            raise ValueError("Uma das etiquetas selecionadas não existe.")
        connection.execute(
            "DELETE FROM whatsapp_contact_labels WHERE contact_phone = ?",
            (contact_phone,),
        )
        for label_id in unique_ids:
            connection.execute(
                """
                INSERT INTO whatsapp_contact_labels (
                    contact_phone, label_id, is_primary
                ) VALUES (?, ?, ?)
                """,
                (contact_phone, label_id, int(label_id == primary_label_id)),
            )
    return get_contact_labels(contact_phone)


def get_contact_labels(contact_phone: str) -> list[dict[str, Any]]:
    initialize_whatsapp_database()
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT label.id, label.name, label.color, relation.is_primary
            FROM whatsapp_contact_labels AS relation
            JOIN whatsapp_labels AS label ON label.id = relation.label_id
            WHERE relation.contact_phone = ?
            ORDER BY relation.is_primary DESC, label.name COLLATE NOCASE
            """,
            (contact_phone,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_conversation_read_for_user(
    conversation_id: int, *, user_key: str
) -> list[str]:
    """Mark only the viewer's inbox as read; Meta receipts are tracked separately."""
    initialize_whatsapp_database()
    with _connection() as connection:
        latest = connection.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS message_row_id
            FROM whatsapp_messages WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        latest_id = int(latest["message_row_id"] if latest else 0)
        assignment = connection.execute(
            "SELECT assignment_version FROM whatsapp_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        assignment_version = int(assignment["assignment_version"]) if assignment else 0
        rows = connection.execute(
            """
            SELECT message_id FROM whatsapp_messages
            WHERE conversation_id = ?
              AND id <= ?
              AND direction = 'inbound'
              AND source != 'history'
              AND meta_read_at IS NULL
              AND message_id IS NOT NULL
            ORDER BY id
            """,
            (conversation_id, latest_id),
        ).fetchall()
        connection.execute(
            """
            INSERT INTO whatsapp_conversation_reads (
                conversation_id, user_key, last_read_message_row_id,
                last_read_assignment_version
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id, user_key) DO UPDATE SET
                last_read_message_row_id = excluded.last_read_message_row_id,
                last_read_assignment_version = excluded.last_read_assignment_version,
                updated_at = CURRENT_TIMESTAMP
            """,
            (conversation_id, user_key, latest_id, assignment_version),
        )
    return [str(row["message_id"]) for row in rows if row["message_id"]]


def mark_message_meta_read(message_id: str) -> None:
    """Record a successful Meta receipt so failed receipts can be retried."""
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            "UPDATE whatsapp_messages SET meta_read_at = CURRENT_TIMESTAMP "
            "WHERE message_id = ? AND direction = 'inbound'",
            (message_id,),
        )


def get_conversation(
    conversation_id: int,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    owns_connection = connection is None
    if owns_connection:
        initialize_whatsapp_database()
    active_connection = connection or _connect()
    try:
        row = active_connection.execute(
            """
            SELECT c.*, contact.profile_name,
                   label.id AS primary_label_id,
                   label.name AS primary_label_name,
                   label.color AS primary_label_color
            FROM whatsapp_conversations AS c
            JOIN whatsapp_contacts AS contact
              ON contact.phone = c.contact_phone
            LEFT JOIN whatsapp_contact_labels AS relation
              ON relation.contact_phone = c.contact_phone
             AND relation.is_primary = 1
            LEFT JOIN whatsapp_labels AS label ON label.id = relation.label_id
            WHERE c.id = ?
            """,
            (conversation_id,),
        ).fetchone()
        return _row(row)
    finally:
        if owns_connection:
            active_connection.close()


def list_conversations(
    *,
    queue_key: str | None = None,
    status: str | None = None,
    label_id: int | None = None,
    assignee_terms: list[str] | None = None,
    assignee_profile_id: str | None = None,
    viewer_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    initialize_whatsapp_database()
    clauses: list[str] = []
    values: list[Any] = []
    if queue_key:
        clauses.append("c.queue_key = ?")
        values.append(queue_key)
    if status:
        clauses.append("c.status = ?")
        values.append(status)
    if label_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM whatsapp_contact_labels selected_label "
            "WHERE selected_label.contact_phone = c.contact_phone "
            "AND selected_label.label_id = ?)"
        )
        values.append(int(label_id))
    normalized_terms = [
        str(term or "").strip().lower()
        for term in (assignee_terms or [])
        if str(term or "").strip()
    ]
    if assignee_profile_id and normalized_terms:
        clauses.append(
            "(c.assignee_profile_id = ? OR "
            "(c.assignee_profile_id IS NULL AND "
            f"LOWER(COALESCE(c.assignee, '')) IN ({','.join('?' for _ in normalized_terms)})))"
        )
        values.extend([assignee_profile_id, *normalized_terms])
    elif assignee_profile_id:
        clauses.append("c.assignee_profile_id = ?")
        values.append(assignee_profile_id)
    elif normalized_terms:
        clauses.append(
            "c.assignee_profile_id IS NULL AND "
            f"LOWER(COALESCE(c.assignee, '')) IN ({','.join('?' for _ in normalized_terms)})"
        )
        values.extend(normalized_terms)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    viewer_value = str(viewer_key or "")
    values = [viewer_value, viewer_value, *values]
    values.append(max(1, min(limit, 500)))

    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT c.*, contact.profile_name,
                   label.id AS primary_label_id,
                   label.name AS primary_label_name,
                   label.color AS primary_label_color,
                   COALESCE((
                     SELECT read_state.last_read_assignment_version
                     FROM whatsapp_conversation_reads AS read_state
                     WHERE read_state.conversation_id = c.id
                       AND read_state.user_key = ?
                   ), 0) AS viewer_read_assignment_version,
                   (
                     SELECT COUNT(*) FROM whatsapp_messages AS unread_message
                     WHERE unread_message.conversation_id = c.id
                       AND unread_message.direction = 'inbound'
                       AND unread_message.source != 'history'
                       AND unread_message.id > COALESCE((
                         SELECT read_state.last_read_message_row_id
                         FROM whatsapp_conversation_reads AS read_state
                         WHERE read_state.conversation_id = c.id
                           AND read_state.user_key = ?
                       ), 0)
                   ) AS viewer_unread_count
            FROM whatsapp_conversations AS c
            JOIN whatsapp_contacts AS contact
              ON contact.phone = c.contact_phone
            LEFT JOIN whatsapp_contact_labels AS relation
              ON relation.contact_phone = c.contact_phone
             AND relation.is_primary = 1
            LEFT JOIN whatsapp_labels AS label ON label.id = relation.label_id
            {where}
            ORDER BY
                CASE WHEN c.status = 'closed' THEN 1 ELSE 0 END,
                COALESCE(c.last_message_at, c.created_at) DESC,
                c.id DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    results = [dict(row) for row in rows]
    for item in results:
        belongs_to_viewer = (
            bool(assignee_profile_id)
            and item.get("assignee_profile_id") == assignee_profile_id
        ) or (
            not item.get("assignee_profile_id")
            and str(item.get("assignee") or "").strip().lower() in normalized_terms
        )
        if belongs_to_viewer and int(item.get("assignment_version") or 0) > int(
            item.get("viewer_read_assignment_version") or 0
        ):
            item["viewer_unread_count"] = max(
                1, int(item.get("viewer_unread_count") or 0)
            )
    return results


def list_messages(
    conversation_id: int,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    initialize_whatsapp_database()
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM whatsapp_messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (conversation_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def route_conversation(
    conversation_id: int,
    *,
    queue_key: str | None,
    queue_label: str | None,
    assignee: str | None,
    status: str,
) -> dict[str, Any] | None:
    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE whatsapp_conversations
            SET queue_key = ?, queue_label = ?, assignee = ?,
                assignee_profile_id = NULL, status = ?,
                assignment_version = assignment_version + CASE
                    WHEN COALESCE(assignee, '') != COALESCE(?, '')
                      OR assignee_profile_id IS NOT NULL THEN 1 ELSE 0 END,
                closed_at = CASE
                    WHEN ? = 'closed' THEN CURRENT_TIMESTAMP ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                queue_key,
                queue_label,
                assignee,
                status,
                assignee,
                status,
                conversation_id,
            ),
        )
        connection.commit()
        return get_conversation(conversation_id, connection=connection)


def update_conversation(
    conversation_id: int,
    *,
    queue_key: str | None = None,
    queue_label: str | None = None,
    assignee: str | None = None,
    assignee_profile_id: str | None | object = _UNSET,
    status: str | None = None,
    mark_read: bool = False,
) -> dict[str, Any] | None:
    current = get_conversation(conversation_id)
    if current is None:
        return None

    next_status = status or str(current["status"])
    next_queue_key = queue_key if queue_key is not None else current.get("queue_key")
    next_queue_label = (
        queue_label if queue_label is not None else current.get("queue_label")
    )
    next_assignee = assignee if assignee is not None else current.get("assignee")
    next_assignee_profile_id = (
        current.get("assignee_profile_id")
        if assignee_profile_id is _UNSET and next_assignee == current.get("assignee")
        else None if assignee_profile_id is _UNSET else assignee_profile_id
    )

    initialize_whatsapp_database()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE whatsapp_conversations
            SET queue_key = ?, queue_label = ?, assignee = ?,
                assignee_profile_id = ?, status = ?,
                assignment_version = assignment_version + CASE
                    WHEN COALESCE(assignee, '') != COALESCE(?, '')
                      OR COALESCE(assignee_profile_id, '') != COALESCE(?, '')
                    THEN 1 ELSE 0 END,
                unread_count = CASE WHEN ? THEN 0 ELSE unread_count END,
                closed_at = CASE
                    WHEN ? = 'closed' THEN COALESCE(closed_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                next_queue_key,
                next_queue_label,
                next_assignee,
                next_assignee_profile_id,
                next_status,
                next_assignee,
                next_assignee_profile_id,
                int(mark_read),
                next_status,
                conversation_id,
            ),
        )
        connection.commit()
        return get_conversation(conversation_id, connection=connection)
