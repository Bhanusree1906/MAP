from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from agents.jira_agent import rest as jira_rest
from backend.services import connection_health
from database.models.jira_connection import JiraConnection


def test_refresh_jira_health_rechecks_cached_status(monkeypatch) -> None:
    connection = JiraConnection(
        name="demo",
        project_key="PRJ",
        site_url="https://demo.atlassian.net",
        jira_oauth_access_token="token",
        jira_oauth_refresh_token="refresh",
        jira_oauth_expires_at=datetime.utcnow() + timedelta(hours=1),
        jira_cloud_id="cloud-123",
    )
    connection_health._health["jira:demo"] = {"status": "inactive", "checked_at": "stale", "error": "old failure"}

    async def fake_resolve_request_auth(conn):
        assert conn.name == "demo"
        return "https://api.atlassian.com/ex/jira/cloud-123", {"headers": {"Authorization": "Bearer token"}}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert url == "https://api.atlassian.com/ex/jira/cloud-123/rest/api/3/project/PRJ"
            return FakeResponse()

    monkeypatch.setattr(jira_rest, "resolve_request_auth", fake_resolve_request_auth)
    monkeypatch.setattr(connection_health.httpx, "AsyncClient", FakeClient)

    asyncio.run(connection_health._check_jira(connection))

    status = connection_health.get("jira", "demo")
    assert status["status"] == "active", status
    assert status["error"] is None
