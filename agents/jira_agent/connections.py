"""CRUD helpers around the JiraConnection table - the per-project credential
store. Each row is a named connection (one per Jira project a team points
this agent at) holding its project scope, MCP server URL, and its own OAuth
client registration/tokens.
"""

from __future__ import annotations

from datetime import datetime

from database.models.jira_connection import JiraConnection
from database.models.project import Project
from database.session import SessionLocal, init_db

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_connection(name: str) -> JiraConnection | None:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(JiraConnection).filter(JiraConnection.name == name).first()
        if conn is not None:
            db.expunge(conn)
        return conn


def list_connections() -> list[JiraConnection]:
    _ensure_schema()
    with SessionLocal() as db:
        conns = db.query(JiraConnection).order_by(JiraConnection.name).all()
        for conn in conns:
            db.expunge(conn)
        return conns


def upsert_connection(
    name: str,
    project_key: str,
    site_url: str | None = None,
    mcp_server_url: str | None = None,
    read_only: bool = True,
    jira_email: str | None = None,
    jira_api_token: str | None = None,
) -> JiraConnection:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(JiraConnection).filter(JiraConnection.name == name).first()
        if conn is None:
            conn = JiraConnection(name=name)
            db.add(conn)
        conn.project_key = project_key.strip().upper()
        conn.site_url = site_url
        if mcp_server_url:
            conn.mcp_server_url = mcp_server_url
        conn.read_only = read_only
        if jira_email is not None:
            conn.jira_email = jira_email
        if jira_api_token is not None:
            conn.jira_api_token = jira_api_token
        db.commit()
        db.refresh(conn)
        db.expunge(conn)
        return conn


def delete_connection(name: str) -> tuple[bool, int]:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(JiraConnection).filter(JiraConnection.name == name).first()
        if conn is None:
            return False, 0
        # Projects reference the connection by name. Detach them in the same
        # transaction before deleting the parent row so SQLite FK checks pass.
        detached_projects = db.query(Project).filter(Project.jira_connection_name == name).update(
            {Project.jira_connection_name: None}, synchronize_session=False
        )
        db.delete(conn)
        db.commit()
        return True, detached_projects


def save_oauth_field(name: str, field: str, value: dict) -> None:
    """Persist an OAuth client-info/token payload for a connection.

    Used by DBTokenStorage (see oauth.py) so each connection authenticates
    and refreshes independently of any other connection sharing this
    codebase.
    """
    import json

    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(JiraConnection).filter(JiraConnection.name == name).first()
        if conn is None:
            raise ValueError(
                f"No Jira connection named {name!r}. Create one first with "
                f"`python agenttest.py --setup --connection {name} --project-key <KEY>`."
            )
        setattr(conn, field, json.dumps(value))
        db.commit()


def save_oauth3lo_tokens(
    name: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime,
    cloud_id: str,
    site_url: str,
) -> JiraConnection:
    """Persist a completed browser OAuth (3LO) login for a connection - the
    access/refresh tokens and the resolved cloudId/site URL (from the
    accessible-resources endpoint), so later ticket requests need no more
    input than the connection name. See oauth3lo.py for the flow itself.
    """
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(JiraConnection).filter(JiraConnection.name == name).first()
        if conn is None:
            raise ValueError(f"No Jira connection named {name!r}.")
        conn.jira_oauth_access_token = access_token
        if refresh_token:
            conn.jira_oauth_refresh_token = refresh_token
        conn.jira_oauth_expires_at = expires_at
        conn.jira_cloud_id = cloud_id
        conn.site_url = site_url
        db.commit()
        db.refresh(conn)
        db.expunge(conn)
        return conn
