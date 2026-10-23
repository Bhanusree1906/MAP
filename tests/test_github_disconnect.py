from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from agents.pipeline_agent import github_credentials
from database.base import Base
from database.models.github_connection import GitHubConnection
from database.models.project import Project


def test_disconnect_unlinks_projects_before_deleting_connection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(github_credentials, "SessionLocal", session_factory)
    monkeypatch.setattr(github_credentials, "_schema_ready", True)
    with session_factory() as db:
        db.add(GitHubConnection(name="octocat", access_token="expired-token"))
        db.add(Project(name="Existing project", github_connection_name="octocat"))
        db.commit()

    removed, detached_projects = github_credentials.disconnect_github_connection("octocat")

    assert removed is True
    assert detached_projects == 1
    with session_factory() as db:
        assert db.query(GitHubConnection).filter_by(name="octocat").first() is None
        project = db.query(Project).filter_by(name="Existing project").one()
        assert project.github_connection_name is None
    engine.dispose()