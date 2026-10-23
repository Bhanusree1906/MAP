from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.services import connection_health


def test_validate_github_records_health_without_exposing_token(monkeypatch) -> None:
    async def fail_fetch(token: str):
        raise ValueError(f"invalid token {token}")

    monkeypatch.setattr(connection_health.github_oauth, "fetch_authenticated_user", fail_fetch)

    valid, login, scopes = asyncio.run(
        connection_health.validate_github("octocat", "secret-token")
    )

    assert not valid
    assert login is None
    assert scopes is None
    health = connection_health.get("github", "octocat")
    assert health["status"] == "inactive"
    assert "secret-token" not in health["error"]


def test_salesforce_health_exchanges_refresh_token_and_validates_org(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    class FakeResponse:
        def __init__(self, payload=None):
            self.payload = payload or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return FakeResponse(
                {
                    "access_token": "access-token",
                    "instance_url": "https://example.my.salesforce.com",
                }
            )

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(connection_health.httpx, "AsyncClient", FakeClient)
    connection = SimpleNamespace(
        name="production",
        sfdx_auth_url=(
            "force://"
            + "client-id"
            + ":"
            + "client-secret"
            + ":"
            + "refresh-token"
            + "@"
            + "login.salesforce.com"
        ),
    )

    asyncio.run(connection_health._check_salesforce(connection))

    assert calls[0][0:2] == (
        "POST",
        "https://login.salesforce.com/services/oauth2/token",
    )
    assert calls[0][2]["data"]["refresh_token"] == "refresh-token"
    assert calls[1][0:2] == (
        "GET",
        "https://example.my.salesforce.com/services/data/",
    )
    assert calls[1][2]["headers"]["Authorization"] == "Bearer " + "access-token"
    assert connection_health.get("salesforce", "production")["status"] == "active"


def test_salesforce_health_rejects_invalid_auth_url() -> None:
    connection = SimpleNamespace(
        name="invalid",
        sfdx_auth_url="https://attacker.example/token",
    )

    asyncio.run(connection_health._check_salesforce(connection))

    health = connection_health.get("salesforce", "invalid")
    assert health["status"] == "inactive"
    assert health["error"] == "Invalid Salesforce auth URL"
