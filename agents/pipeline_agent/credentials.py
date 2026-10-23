"""CRUD helpers for the project + git-credential rows backing the pipeline
agent: a Project (repository URL, default branch) and its GitAccessToken
(the token used to clone/push that repo), stored in the project database
(see database/models/project.py and database/models/git_access_token.py) so
the same agent code can target different repositories without touching env
vars or code - mirrors agents/jira_agent/connections.py.
"""

from __future__ import annotations

from database.models.git_access_token import GitAccessToken
from database.models.project import Project
from database.session import SessionLocal, init_db

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def get_project(name: str) -> Project | None:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.name == name).first()
        if project is not None:
            db.expunge(project)
        return project


def get_project_by_id(project_id: str) -> Project | None:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is not None:
            db.expunge(project)
        return project


def list_projects() -> list[Project]:
    _ensure_schema()
    with SessionLocal() as db:
        projects = db.query(Project).order_by(Project.name).all()
        for project in projects:
            db.expunge(project)
        return projects


def upsert_project(
    name: str,
    repository_url: str,
    default_branch: str = "main",
    description: str | None = None,
    jira_connection_name: str | None = None,
    azure_connection_name: str | None = None,
    ticket_group_name: str | None = None,
    sandbox_kind: str | None = None,
    salesforce_connection_name: str | None = None,
    github_connection_name: str | None = None,
    azure_git_connection_name: str | None = None,
) -> Project:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.name == name).first()
        if project is None:
            project = Project(name=name)
            db.add(project)
        project.repository_url = repository_url
        project.default_branch = default_branch
        if description is not None:
            project.description = description
        if jira_connection_name is not None:
            project.jira_connection_name = jira_connection_name
        if azure_connection_name is not None:
            project.azure_connection_name = azure_connection_name
        if ticket_group_name is not None:
            project.ticket_group_name = ticket_group_name
        if sandbox_kind is not None:
            project.sandbox_kind = sandbox_kind
        if salesforce_connection_name is not None:
            project.salesforce_connection_name = salesforce_connection_name
        if github_connection_name is not None:
            project.github_connection_name = github_connection_name
        if azure_git_connection_name is not None:
            project.azure_git_connection_name = azure_git_connection_name
        db.commit()
        db.refresh(project)
        db.expunge(project)
        return project


def set_project_git_source(
    project_id: str, github_name: str | None, azure_git_name: str | None
) -> Project | None:
    """A project links exactly one git source (GitHub or Azure DevOps Git);
    setting one clears the other so the pipeline knows how to clone."""
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            return None
        project.github_connection_name = github_name
        project.azure_git_connection_name = azure_git_name
        db.commit()
        db.refresh(project)
        db.expunge(project)
        return project


def set_project_ticket_source(
    project_id: str, jira_name: str | None, azure_name: str | None, group_name: str | None = None
) -> Project | None:
    """A project links exactly one ticket source (Jira, Azure, or a manual
    ticket group); setting one clears the others so the pipeline knows where
    to fetch tickets from."""
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            return None
        project.jira_connection_name = jira_name
        project.azure_connection_name = azure_name
        project.ticket_group_name = group_name
        db.commit()
        db.refresh(project)
        db.expunge(project)
        return project


def set_project_jira_connection(project_id: str, connection_name: str | None) -> Project | None:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            return None
        project.jira_connection_name = connection_name
        db.commit()
        db.refresh(project)
        db.expunge(project)
        return project


def delete_project(name: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.name == name).first()
        if project is None:
            return False
        db.delete(project)
        db.commit()
        return True


def get_git_token(project_id: str) -> GitAccessToken | None:
    _ensure_schema()
    with SessionLocal() as db:
        token = db.query(GitAccessToken).filter(GitAccessToken.project_id == project_id).first()
        if token is not None:
            db.expunge(token)
        return token


def get_effective_git_token(project_id: str) -> str | None:
    """Resolve the credential for the project's configured git source.

    An explicitly linked GitHub account is authoritative. This prevents a
    stale legacy project token from overriding a reauthorized account.

    For projects that predate named GitHub connections, retain the legacy
    project token and then the default GitHub account as compatibility
    fallbacks.

    Falls back to the project's Azure ticket-source connection when no
    explicit Azure *git* connection is linked but the same Azure DevOps PAT
    can serve both work items and git operations."""
    from .github_credentials import get_github_connection

    project = get_project_by_id(project_id)
    if project and project.github_connection_name:
        connection = get_github_connection(project.github_connection_name)
        return connection.access_token if connection is not None else None

    token = get_git_token(project_id)
    if token is not None:
        return token.access_token
    if project and project.azure_git_connection_name:
        from agents.azure_agent.connections import get_connection as get_azure_conn
        azure_conn = get_azure_conn(project.azure_git_connection_name)
        if azure_conn is not None:
            return azure_conn.pat
    # When no explicit Azure *git* connection is linked, try the Azure
    # ticket-source connection — the same PAT is often authorised for Code
    # access as well as Work Items, so it can authenticate git clone/push.
    if project and not project.azure_git_connection_name and project.azure_connection_name:
        from agents.azure_agent.connections import get_connection as get_azure_conn
        azure_conn = get_azure_conn(project.azure_connection_name)
        if azure_conn is not None and azure_conn.pat:
            return azure_conn.pat
    connection = get_github_connection()
    return connection.access_token if connection is not None else None


def upsert_git_token(
    project_id: str,
    repository_url: str,
    access_token: str,
    token_type: str = "git",
) -> GitAccessToken:
    _ensure_schema()
    with SessionLocal() as db:
        token = db.query(GitAccessToken).filter(GitAccessToken.project_id == project_id).first()
        if token is None:
            token = GitAccessToken(project_id=project_id)
            db.add(token)
        token.repository_url = repository_url
        token.access_token = access_token
        token.token_type = token_type
        db.commit()
        db.refresh(token)
        db.expunge(token)
        return token


def delete_git_token(project_id: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        token = db.query(GitAccessToken).filter(GitAccessToken.project_id == project_id).first()
        if token is None:
            return False
        db.delete(token)
        db.commit()
        return True
