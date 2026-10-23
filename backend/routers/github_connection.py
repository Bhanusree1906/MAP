"""Shared GitHub OAuth connection endpoints."""

from __future__ import annotations

import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from agents.pipeline_agent import github_credentials, github_oauth, github_repos
from backend.services import connection_health

router = APIRouter(prefix="/api/github", tags=["github-connection"])
_OAUTH_STATE_TTL_SECONDS = 600
_pending_oauth_states: dict[str, tuple[float, str | None, str | None]] = {}


def _issue_state(login: str | None = None, connection_name: str | None = None) -> str:
    now = time.monotonic()
    for state, entry in list(_pending_oauth_states.items()):
        if entry[0] <= now:
            del _pending_oauth_states[state]
    state = secrets.token_urlsafe(24)
    _pending_oauth_states[state] = (
        now + _OAUTH_STATE_TTL_SECONDS,
        login,
        connection_name,
    )
    return state


def _consume_state(state: str) -> tuple[bool, str | None, str | None]:
    entry = _pending_oauth_states.pop(state, None)
    if entry is None:
        return False, None, None
    expires_at, login, connection_name = entry
    return expires_at > time.monotonic(), login, connection_name


def _redirect(config: github_oauth.OAuthAppConfig, **params: str) -> RedirectResponse:
    parsed = urlsplit(config.success_redirect_url)
    query = dict(parse_qsl(parsed.query))
    query.update(params)
    target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return RedirectResponse(target)


@router.get("/repos")
async def list_repos(connection: str = github_credentials.DEFAULT_NAME) -> dict:
    """List every repository the connected GitHub account can see."""
    record = github_credentials.get_github_connection(connection)
    if record is None:
        raise HTTPException(status_code=404, detail=f"GitHub account {connection!r} is not connected")
    try:
        repos = await github_repos.list_repositories(record.access_token)
    except github_repos.GitHubRepoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {"connection": connection, "repos": repos}


@router.get("/oauth/status")
def get_status() -> dict:
    connections = github_credentials.list_github_connections()
    configured = True
    try:
        github_oauth.load_oauth_app_config()
    except github_oauth.OAuthNotConfigured:
        configured = False
    return {
        "connected": bool(connections),
        "configured": configured,
        "connections": [
            {
                "name": connection.name,
                "login": connection.login,
                "scopes": connection.scopes,
                **connection_health.get("github", connection.name),
            }
            for connection in connections
        ],
    }


@router.get("/oauth/start")
def start_oauth(
    login: str | None = None,
    prompt: str | None = None,
    connection_name: str | None = None,
) -> RedirectResponse:
    if connection_name:
        connection = github_credentials.get_github_connection(connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="GitHub connection not found")
        if not connection.login:
            raise HTTPException(status_code=400, detail="GitHub connection has no account login to reauthorize")
        if login and login.casefold() != connection.login.casefold():
            raise HTTPException(status_code=400, detail="GitHub login does not match the selected connection")
        login = connection.login
    try:
        config = github_oauth.load_oauth_app_config()
    except github_oauth.OAuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return RedirectResponse(
        github_oauth.build_authorize_url(
            config,
            _issue_state(login, connection_name),
            login=login,
            select_account=prompt == "select_account",
        )
    )


@router.get("/oauth/callback")
async def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    try:
        config = github_oauth.load_oauth_app_config()
    except github_oauth.OAuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    valid_state, intended_login, connection_name = _consume_state(state or "")
    if error:
        if valid_state and intended_login:
            connection_health.record_result(
                "github",
                connection_name or intended_login,
                False,
                "GitHub authorization was denied",
            )
        return _redirect(config, github_error=error)
    if not code or not valid_state:
        return _redirect(config, github_error="Invalid or expired authorization state")
    try:
        token_response = await github_oauth.exchange_code(config, code)
        access_token = token_response["access_token"]
        login, scopes = await github_oauth.fetch_authenticated_user(access_token)
        if intended_login and (login or "").casefold() != intended_login.casefold():
            raise ValueError("GitHub account selection did not match the requested account")
        saved = github_credentials.save_github_token(
            access_token,
            login=login,
            scopes=scopes,
            name=connection_name,
        )
    except Exception:
        if intended_login:
            connection_health.record_result(
                "github",
                connection_name or intended_login,
                False,
                "GitHub authentication failed",
            )
        return _redirect(config, github_error="GitHub authentication failed")
    connection_health.record_result("github", saved.name, True)
    return _redirect(config, github_connected=login or "github")


@router.delete("/oauth")
def disconnect() -> dict:
    removed, detached_projects = github_credentials.disconnect_github_connection()
    return {"status": "ok", "removed": removed, "detached_projects": detached_projects}


@router.post("/oauth/{name}/test")
async def test_connection(name: str) -> dict:
    connection = github_credentials.get_github_connection(name)
    if connection is None:
        raise HTTPException(status_code=404, detail=f"GitHub account {name!r} is not connected")
    valid, login, scopes = await connection_health.validate_github(name, connection.access_token)
    if not valid:
        return {"valid": False, "login": connection.login, "scopes": connection.scopes}
    return {"valid": True, "login": login, "scopes": scopes}


@router.delete("/oauth/{name}")
def disconnect_account(name: str) -> dict:
    removed, detached_projects = github_credentials.disconnect_github_connection(name)
    return {"status": "ok", "removed": removed, "detached_projects": detached_projects}