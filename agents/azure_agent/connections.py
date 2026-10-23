"""CRUD helpers around the AzureConnection table - the per-project credential
store. Each row is a named connection (one per Azure DevOps organization +
project a team points this pipeline at) holding its scope and credentials,
mirroring agents/jira_agent/connections.py.
"""

from __future__ import annotations

from database.models.azure_connection import AzureConnection
from database.session import SessionLocal, init_db

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_connection(name: str) -> AzureConnection | None:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(AzureConnection).filter(AzureConnection.name == name).first()
        if conn is not None:
            db.expunge(conn)
        return conn


def list_connections() -> list[AzureConnection]:
    _ensure_schema()
    with SessionLocal() as db:
        conns = db.query(AzureConnection).order_by(AzureConnection.name).all()
        for conn in conns:
            db.expunge(conn)
        return conns


def upsert_connection(
    name: str,
    organization_url: str,
    project_name: str,
    pat: str | None = None,
    read_only: bool = True,
) -> AzureConnection:
    _ensure_schema()
    with SessionLocal() as db:
        conn = db.query(AzureConnection).filter(AzureConnection.name == name).first()
        if conn is None:
            conn = AzureConnection(name=name)
            db.add(conn)
        conn.organization_url = organization_url.rstrip("/")
        conn.project_name = project_name.strip()
        conn.read_only = read_only
        if pat is not None:
            conn.pat = pat
        db.commit()
        db.refresh(conn)
        db.expunge(conn)
        return conn


def delete_connection(name: str) -> bool:
    _ensure_schema()
    from database.models.project import Project
    from database.session import SessionLocal as _SessionLocal

    with _SessionLocal() as db:
        conn = db.query(AzureConnection).filter(AzureConnection.name == name).first()
        if conn is None:
            return False
        # Projects referencing the deleted connection (as ticket source or as
        # git source) must not keep dangling pointers the pipeline would trip on.
        for project in db.query(Project).filter(
            (Project.azure_connection_name == name) | (Project.azure_git_connection_name == name)
        ):
            was_ticket = project.azure_connection_name == name
            was_git = project.azure_git_connection_name == name
            if was_ticket:
                project.azure_connection_name = None
            if was_git:
                project.azure_git_connection_name = None
        db.delete(conn)
        db.commit()
        return True