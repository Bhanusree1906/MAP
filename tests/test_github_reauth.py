from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from agents.pipeline_agent import github_oauth
from backend.routers import github_connection


def test_github_reauth_preserves_the_selected_account(monkeypatch) -> None:
    config = github_oauth.OAuthAppConfig(
        client_id="client",
        client_secret="secret",
        redirect_uri="http://localhost/callback",
        scopes="repo",
        success_redirect_url="http://localhost:5173/connections",
    )
    saved: dict = {}

    monkeypatch.setattr(github_oauth, "load_oauth_app_config", lambda: config)
    monkeypatch.setattr(
        github_oauth, "exchange_code", lambda _config, _code: _resolved({"access_token": "new-token"})
    )
    monkeypatch.setattr(
        github_oauth, "fetch_authenticated_user", lambda _token: _resolved(("octocat", "repo"))
    )

    def save_token(access_token, *, login, scopes, name=None):
        saved.update(access_token=access_token, login=login, scopes=scopes, name=name)
        return SimpleNamespace(name=name or login)

    monkeypatch.setattr(github_connection.github_credentials, "save_github_token", save_token)

    state = github_connection._issue_state("octocat", "legacy-octocat")
    response = asyncio.run(
        github_connection.oauth_callback(code="code", state=state)
    )

    assert saved == {
        "access_token": "new-token",
        "login": "octocat",
        "scopes": "repo",
        "name": "legacy-octocat",
    }
    assert parse_qs(urlsplit(response.headers["location"]).query)["github_connected"] == [
        "octocat"
    ]
    assert github_connection.connection_health.get("github", "legacy-octocat")["status"] == "active"


def test_github_reauth_does_not_replace_a_different_account(monkeypatch) -> None:
    config = github_oauth.OAuthAppConfig(
        client_id="client",
        client_secret="secret",
        redirect_uri="http://localhost/callback",
        scopes="repo",
        success_redirect_url="http://localhost:5173/connections",
    )
    saved: list[str] = []

    monkeypatch.setattr(github_oauth, "load_oauth_app_config", lambda: config)
    monkeypatch.setattr(
        github_oauth, "exchange_code", lambda _config, _code: _resolved({"access_token": "wrong-token"})
    )
    monkeypatch.setattr(
        github_oauth, "fetch_authenticated_user", lambda _token: _resolved(("other-user", "repo"))
    )
    monkeypatch.setattr(
        github_connection.github_credentials,
        "save_github_token",
        lambda *args, **kwargs: saved.append(kwargs.get("login")),
    )

    state = github_connection._issue_state("octocat", "octocat")
    response = asyncio.run(
        github_connection.oauth_callback(code="code", state=state)
    )

    assert not saved
    assert parse_qs(urlsplit(response.headers["location"]).query)["github_error"] == [
        "GitHub authentication failed"
    ]
    assert github_connection.connection_health.get("github", "octocat")["status"] == "inactive"


def _resolved(value):
    async def result(*args, **kwargs):
        return value

    return result()
