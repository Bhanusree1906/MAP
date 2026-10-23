"""CRUD for the shared GitHub OAuth credential."""

from __future__ import annotations

from database.models.github_connection import GitHubConnection
from database.models.project import Project
from database.session import SessionLocal, init_db

DEFAULT_NAME = "default"
_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_github_connection(name: str = DEFAULT_NAME) -> GitHubConnection | None:
    _ensure_schema()
    with SessionLocal() as db:
        connection = db.query(GitHubConnection).filter(GitHubConnection.name == name).first()
        if connection is not None:
            db.expunge(connection)
        return connection


def list_github_connections() -> list[GitHubConnection]:
    _ensure_schema()
    with SessionLocal() as db:
        connections = db.query(GitHubConnection).order_by(GitHubConnection.login, GitHubConnection.name).all()
        for connection in connections:
            db.expunge(connection)
        return connections


def save_github_token(
    access_token: str,
    login: str | None = None,
    scopes: str | None = None,
    name: str | None = None,
) -> GitHubConnection:
    _ensure_schema()
    with SessionLocal() as db:
        name = name or login or DEFAULT_NAME
        connection = db.query(GitHubConnection).filter(GitHubConnection.name == name).first()
        if connection is None:
            connection = GitHubConnection(name=name)
            db.add(connection)
        connection.access_token = access_token
        connection.login = login
        connection.scopes = scopes
        db.commit()
        db.refresh(connection)
        db.expunge(connection)
        return connection


def delete_github_connection(name: str = DEFAULT_NAME) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        connection = db.query(GitHubConnection).filter(GitHubConnection.name == name).first()
        if connection is None:
            return False
        db.delete(connection)
        db.commit()
        return True


def disconnect_github_connection(name: str = DEFAULT_NAME) -> tuple[bool, int]:
    """Unlink projects from an account and delete its stored credential."""
    _ensure_schema()
    with SessionLocal() as db:
        connection = db.query(GitHubConnection).filter(GitHubConnection.name == name).first()
        if connection is None:
            return False, 0
        detached_projects = (
            db.query(Project)
            .filter(Project.github_connection_name == name)
            .update({Project.github_connection_name: None}, synchronize_session=False)
        )
        db.delete(connection)
        db.commit()
        return True, detached_projects