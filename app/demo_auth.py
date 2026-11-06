"""Local demo authentication. Never selected when DEMO_MODE=false."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .settings import settings

PROFILE_ID = "00000000-0000-0000-0000-000000000001"


def _connect():
    path = Path(settings.auth_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS demo_sessions (digest TEXT PRIMARY KEY, expires TEXT NOT NULL)")
    return conn


def demo_user():
    return {"username": settings.demo_email, "email": settings.demo_email,
            "display_name": "Administrador Demo", "role": "adm", "role_code": "admin",
            "areas": ["admin", "finance"], "can_dispatch_demands": True,
            "dispatch_targets": ["projectist", "projects", "financeiro"],
            "profile_id": PROFILE_ID, "auth_user_id": PROFILE_ID,
            "organization_id": "00000000-0000-0000-0000-000000000002", "avatar_url": None}


def authenticate_demo(email, password):
    if not settings.demo_mode:
        return None
    if secrets.compare_digest(email.strip().lower().encode(), settings.demo_email.lower().encode()) and secrets.compare_digest(password.encode(), settings.demo_password.encode()):
        return demo_user()
    return None


def create_demo_session(profile_id, hours):
    if not settings.demo_mode or profile_id != PROFILE_ID:
        raise ValueError("Perfil de demonstração inválido")
    token = secrets.token_urlsafe(48)
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM demo_sessions WHERE expires <= ?", (datetime.now(timezone.utc).isoformat(),))
            conn.execute("INSERT INTO demo_sessions VALUES (?,?)", (hashlib.sha256(token.encode()).hexdigest(), expires))
    finally:
        conn.close()
    return token, expires


def demo_user_from_session(token):
    if not settings.demo_mode or not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT expires FROM demo_sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    finally:
        conn.close()
    if row and datetime.fromisoformat(row[0]) > datetime.now(timezone.utc):
        return demo_user()
    return None


def delete_demo_session(token):
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM demo_sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
    finally:
        conn.close()
