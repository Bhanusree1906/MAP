from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from agents.jira_agent import connections as jira_connections
from database.base import Base
from database.models.jira_connection import JiraConnection
from database.models.project import Project


def test_delete_connection_unlinks_projects_before_deleting(tmp_path, monkeypatch) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(jira_connections, "SessionLocal", session_factory)
    monkeypatch.setattr(jira_connections, "_schema_ready", True)
    with session_factory() as db:
        db.add(JiraConnection(name="jira", project_key="AG"))
        db.add(Project(name="Existing project", jira_connection_name="jira"))
        db.commit()

    removed, detached_projects = jira_connections.delete_connection("jira")
    assert removed is True
    assert detached_projects == 1

    with session_factory() as db:
        assert db.query(JiraConnection).filter_by(name="jira").first() is None
        project = db.query(Project).filter_by(name="Existing project").one()
        assert project.jira_connection_name is None
    engine.dispose()