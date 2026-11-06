from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import settings


def init_audit_db() -> None:
    db_path = Path(settings.audit_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                success INTEGER NOT NULL,
                request_payload TEXT,
                response_payload TEXT,
                error_message TEXT
            )
            '''
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        for name, definition in (
            ("actor_user_id", "TEXT"),
            ("actor_role_code", "TEXT"),
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE audit_log ADD COLUMN {name} {definition}"
                )
        conn.commit()
    finally:
        conn.close()


def write_audit(
    *,
    action: str,
    target: str | None,
    success: bool,
    request_payload: Any = None,
    response_payload: Any = None,
    error_message: str | None = None,
    actor_user_id: str | None = None,
    actor_role_code: str | None = None,
) -> None:
    init_audit_db()

    conn = sqlite3.connect(settings.audit_db_path)
    try:
        conn.execute(
            '''
            INSERT INTO audit_log (
                occurred_at,
                action,
                target,
                success,
                request_payload,
                response_payload,
                error_message,
                actor_user_id,
                actor_role_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                datetime.now(timezone.utc).isoformat(),
                action,
                target,
                int(success),
                json.dumps(request_payload, ensure_ascii=False, default=str)
                if request_payload is not None
                else None,
                json.dumps(response_payload, ensure_ascii=False, default=str)
                if response_payload is not None
                else None,
                error_message,
                actor_user_id,
                actor_role_code,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    init_audit_db()

    conn = sqlite3.connect(settings.audit_db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            '''
            SELECT *
            FROM audit_log
            ORDER BY id DESC
            LIMIT ?
            ''',
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]
