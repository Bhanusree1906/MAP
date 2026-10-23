"""Jira connection CRUD plus browser OAuth (3LO).

Connections can be created from the UI (POST below) or the CLI. Authorize
a connection either by clicking "Connect via Jira" (oauth/start + callback)
or by pasting an email + API token on PUT. tickets.py prefers OAuth tokens
when both are present.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from agents.jira_agent import connections as jira_connections
from agents.jira_agent import oauth3lo
from backend.schemas import JiraConnectionCreate, JiraConnectionOut, JiraConnectionUpdate
from backend.services import connection_health

router = APIRouter(prefix="/api/jira-connections", tags=["jira-connections"])

# In-memory CSRF state for the OAuth start/callback round trip - short-lived
# (a login takes seconds, not the process lifetime) and single-process,
# which is fine for the one backend instance driving this flow.
_OAUTH_STATE_TTL_SECONDS = 600
_pending_oauth_states: dict[str, tuple[str, float]] = {}


def _issue_state(connection_name: str) -> str:
    now = time.monotonic()
    for key, (_, expires_at) in list(_pending_oauth_states.items()):
        if expires_at < now:
            del _pending_oauth_states[key]
    state = secrets.token_urlsafe(24)
    _pending_oauth_states[state] = (connection_name, now + _OAUTH_STATE_TTL_SECONDS)
    return state


def _consume_state(state: str) -> str | None:
    entry = _pending_oauth_states.pop(state, None)
    if entry is None:
        return None
    connection_name, expires_at = entry
    if expires_at < time.monotonic():
        return None
    return connection_name


@router.get("", response_model=list[JiraConnectionOut])
async def list_connections(refresh_health: bool = False) -> list[JiraConnectionOut]:
    connections = jira_connections.list_connections()
    if refresh_health:
        await asyncio.gather(*(connection_health.refresh("jira", c.name) for c in connections))
    return [
        JiraConnectionOut.from_model(c, connection_health.get("jira", c.name))
        for c in connections
    ]


@router.get("/oauth/status")
def oauth_app_status() -> dict:
    """Whether the backend has a Jira OAuth app configured (Connect via Jira)."""
    try:
        oauth3lo.load_oauth_app_config()
        return {"configured": True}
    except oauth3lo.OAuthNotConfigured:
        return {"configured": False}


@router.post("", response_model=JiraConnectionOut, status_code=201)
async def create_connection(payload: JiraConnectionCreate) -> JiraConnectionOut:
    name = payload.name.strip()
    project_key = payload.project_key.strip().upper()
    if not name:
        raise HTTPException(status_code=400, detail="Connection name is required")
    if not project_key:
        raise HTTPException(status_code=400, detail="Jira project key is required")
    if jira_connections.get_connection(name) is not None:
        raise HTTPException(status_code=409, detail=f"A connection named {name!r} already exists")
    if payload.jira_api_token and (not (payload.site_url or "").strip() or not (payload.jira_email or "").strip()):
        raise HTTPException(
            status_code=400,
            detail="API token auth needs both a site URL (https://your-site.atlassian.net) and an email.",
        )
    created = jira_connections.upsert_connection(
        name=name,
        project_key=project_key,
        site_url=payload.site_url,
        read_only=payload.read_only,
        jira_email=payload.jira_email,
        jira_api_token=payload.jira_api_token,
    )
    health = await connection_health.refresh("jira", created.name)
    return JiraConnectionOut.from_model(created, health)


@router.get("/{name}", response_model=JiraConnectionOut)
def get_connection(name: str) -> JiraConnectionOut:
    conn = jira_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return JiraConnectionOut.from_model(conn, connection_health.get("jira", conn.name))


@router.put("/{name}", response_model=JiraConnectionOut)
async def update_connection(name: str, payload: JiraConnectionUpdate) -> JiraConnectionOut:
    conn = jira_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    updated = jira_connections.upsert_connection(
        name=name,
        project_key=payload.project_key or conn.project_key,
        site_url=payload.site_url if payload.site_url is not None else conn.site_url,
        read_only=payload.read_only if payload.read_only is not None else conn.read_only,
        jira_email=payload.jira_email,
        jira_api_token=payload.jira_api_token,
    )
    health = await connection_health.refresh("jira", updated.name)
    return JiraConnectionOut.from_model(updated, health)


@router.delete("/{name}")
def delete_connection(name: str) -> dict:
    removed, detached_projects = jira_connections.delete_connection(name)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {
        "status": "ok",
        "removed": True,
        "detached_projects": detached_projects,
        "projects_deleted": 0,
    }


@router.get("/{name}/oauth/start")
def start_oauth(name: str) -> RedirectResponse:
    """Kicks off browser OAuth for an existing connection - the target of
    the UI's "Connect via Jira" button. Opened in a new tab so the user
    stays on the app; Atlassian redirects back to oauth_callback below,
    which redirects the tab to the success_redirect_url configured for the
    OAuth app.
    """
    conn = jira_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        config = oauth3lo.load_oauth_app_config()
    except oauth3lo.OAuthNotConfigured as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None

    state = _issue_state(name)
    return RedirectResponse(oauth3lo.build_authorize_url(config, state))


@router.get("/oauth/callback")
async def oauth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    try:
        config = oauth3lo.load_oauth_app_config()
    except oauth3lo.OAuthNotConfigured as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None

    def _redirect_with(**params: str) -> RedirectResponse:
        return RedirectResponse(str(httpx.URL(config.success_redirect_url, params=params)))

    if error:
        return _redirect_with(jira_error=error)

    connection_name = _consume_state(state or "")
    if connection_name is None:
        return _redirect_with(jira_error="invalid_or_expired_state")
    if not code:
        return _redirect_with(jira_error="missing_code")

    try:
        token_response = await oauth3lo.exchange_code(config, code)
        resources = await oauth3lo.fetch_accessible_resources(token_response["access_token"])
    except httpx.HTTPStatusError as exc:
        return _redirect_with(jira_error=f"token_exchange_failed_{exc.response.status_code}")

    if not resources:
        return _redirect_with(jira_error="no_accessible_jira_sites")

    conn = jira_connections.get_connection(connection_name)
    site = next((r for r in resources if r.get("url") == (conn.site_url if conn else None)), resources[0])

    jira_connections.save_oauth3lo_tokens(
        name=connection_name,
        access_token=token_response["access_token"],
        refresh_token=token_response.get("refresh_token"),
        expires_at=oauth3lo.expires_at_from(token_response),
        cloud_id=site["id"],
        site_url=site["url"],
    )
    await connection_health.refresh("jira", connection_name)
    return _redirect_with(jira_connected=connection_name)
