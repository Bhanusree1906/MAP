"""Shared pytest configuration.

Sets deterministic env vars before any project import (crypto key, auth
token off) and puts the repo root on sys.path so tests can import the
packages directly without installation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stable in-process crypto key for EncryptedText round-trips; auth token set
# per-test where the middleware behavior under test needs it.
os.environ.setdefault("SECRET_ENCRYPTION_KEY", "test-only-key")
os.environ.pop("API_AUTH_TOKEN", None)
os.environ.pop("LLM_PRICING_JSON", None)

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from database.base import Base  # noqa: E402

# Every module that did `from database.session import SessionLocal, init_db`
# binds those names at import time; a temp-DB fixture must rebind each one.
_DB_CONSUMERS = [
    "agents.pipeline_agent.credentials",
    "agents.azure_agent.connections",
    "agents.skills.service",
    "agents.jira_agent.connections",
    "agents.pipeline_agent.runs",
    "agents.pipeline_agent.ingestion",
    "agents.pipeline_agent.manual_work_items",
]


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point every DB consumer at a fresh per-test SQLite file. Prevents
    tests from reading/writing the real agent_factory.db."""
    engine = sa.create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    session_factory = sa.orm.sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    import agents.azure_agent.connections as azure_connections
    import agents.jira_agent.connections as jira_connections
    import agents.pipeline_agent.credentials as credentials
    import agents.pipeline_agent.runs as runs
    import agents.pipeline_agent.ingestion as ingestion
    import agents.pipeline_agent.manual_work_items as manual_work_items
    import agents.skills.service as skill_service
    import database.session as db_session

    for mod in (
        credentials,
        skill_service,
        azure_connections,
        jira_connections,
        runs,
        ingestion,
        manual_work_items,
    ):
        monkeypatch.setattr(mod, "SessionLocal", session_factory)
        monkeypatch.setattr(mod, "init_db", lambda: None)
        if hasattr(mod, "_schema_ready"):
            monkeypatch.setattr(mod, "_schema_ready", True)
    monkeypatch.setattr(db_session, "engine", engine)

    yield session_factory
    engine.dispose()
