"""CRUD around the CodexConnection table - the persisted result of a
device-code login (see codex_login.py). One named row ("default") shared by
every pipeline run for now, mirroring how agents/jira_agent/connections.py
stores Jira credentials.
"""

from __future__ import annotations

from database.models.codex_connection import CodexConnection
from database.session import SessionLocal, init_db

DEFAULT_NAME = "default"

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_codex_auth_json(name: str = DEFAULT_NAME) -> str | None:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(CodexConnection).filter(CodexConnection.name == name).first()
        return conn.auth_json if conn else None


def save_codex_auth_json(auth_json: str, name: str = DEFAULT_NAME) -> CodexConnection:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(CodexConnection).filter(CodexConnection.name == name).first()
        if conn is None:
            conn = CodexConnection(name=name)
            db.add(conn)
        conn.auth_json = auth_json
        db.commit()
        db.refresh(conn)
        db.expunge(conn)
        return conn


def delete_codex_connection(name: str = DEFAULT_NAME) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(CodexConnection).filter(CodexConnection.name == name).first()
        if conn is None:
            return False
        db.delete(conn)
        db.commit()
        return True
