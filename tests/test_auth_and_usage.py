"""Unit tests for the API auth middleware and the sandbox usage parser."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import _is_exempt, auth_middleware


def _client() -> TestClient:
    app = FastAPI()
    app.middleware("http")(auth_middleware)

    @app.get("/api/things")
    def things() -> dict:
        return {"ok": True}

    @app.get("/api/jira-connections/FOO/oauth/start")
    def start_oauth() -> dict:
        return {"ok": True}

    @app.get("/api/jira-connections/oauth/callback")
    def oauth_callback() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_requires_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "tok123")
    client = _client()
    assert client.get("/api/things").status_code == 401
    assert client.get("/api/things", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/things", headers={"Authorization": "Bearer tok123"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}


def test_oauth_redirect_paths_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "tok123")
    client = _client()
    assert client.get("/api/jira-connections/FOO/oauth/start").status_code == 200
    assert client.get("/api/jira-connections/oauth/callback").status_code == 200


def test_open_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    client = _client()
    assert client.get("/api/things").status_code == 200


def test_exempt_path_matching() -> None:
    assert _is_exempt("/api/github/oauth/callback")
    assert _is_exempt("/api/jira-connections/ANY/oauth/start")
    assert not _is_exempt("/api/projects")
    # Non-API paths are never touched by the middleware anyway.
    assert not _is_exempt("/assets/app.js")
