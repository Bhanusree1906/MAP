"""Startup health checks for external connection credentials."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from agents.jira_agent import connections as jira_connections
from agents.pipeline_agent import codex_credentials, github_credentials, salesforce_credentials
from agents.pipeline_agent import github_oauth

TIMEOUT = httpx.Timeout(8.0)
_health: dict[str, dict[str, Any]] = {}


def _key(provider: str, name: str) -> str:
    return f"{provider}:{name}"


def _set(provider: str, name: str, active: bool, error: str | None = None) -> None:
    _health[_key(provider, name)] = {
        "status": "active" if active else "inactive",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
    }


def get(provider: str, name: str) -> dict[str, Any]:
    return _health.get(
        _key(provider, name),
        {"status": "unknown", "checked_at": None, "error": "Not checked yet"},
    )


def _safe_error(exc: Exception, secrets: tuple[str | None, ...] = ()) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"External service returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "Could not reach external service"
    message = str(exc).replace("\n", " ")
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:240] or exc.__class__.__name__


def record_result(provider: str, name: str, active: bool, error: str | None = None) -> None:
    _set(provider, name, active, error)


async def validate_github(name: str, token: str) -> tuple[bool, str | None, str | None]:
    try:
        login, scopes = await github_oauth.fetch_authenticated_user(token)
    except Exception as exc:
        _set("github", name, False, _safe_error(exc, (token,)))
        return False, None, None
    else:
        _set("github", name, True)
        return True, login, scopes


async def _check_github(name: str, token: str) -> None:
    await validate_github(name, token)


async def _check_jira_mcp(connection) -> None:
    from agents.jira_agent.config import load_settings
    from agents.jira_agent.mcp_tools import load_atlassian_tools

    await load_atlassian_tools(
        load_settings(connection.name),
        allow_interactive_auth=False,
        update_health=False,
    )


async def _check_jira(connection) -> None:
    name = connection.name
    if connection.oauth_tokens:
        try:
            await _check_jira_mcp(connection)
        except Exception:
            _set("jira", name, False, "Atlassian MCP authentication failed")
            return
        _set("jira", name, True)
        return

    try:
        from agents.jira_agent.rest import resolve_request_auth

        base_url, request_kwargs = await resolve_request_auth(connection)
        project_key = getattr(connection, "project_key", None) or ""
        if not project_key:
            raise ValueError("Jira connection project key is not configured")

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{base_url}/rest/api/3/project/{project_key}",
                **request_kwargs,
            )
            response.raise_for_status()
    except Exception as exc:
        if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
            error = _safe_error(exc)
        else:
            error = (
                "No complete Jira credentials configured"
                if "credentials" in str(exc).lower() and "configured" in str(exc).lower()
                else "Jira authentication failed"
            )
        _set("jira", name, False, error)
    else:
        _set("jira", name, True)


def _salesforce_auth(connection) -> tuple[str, str, str, str] | None:
    parsed = urlsplit(connection.sfdx_auth_url.strip())
    if parsed.scheme != "force" or not parsed.hostname or not parsed.username or not parsed.password:
        return None
    client_id = unquote(parsed.username)
    client_secret, separator, refresh_token = parsed.password.partition(":")
    if not separator or not refresh_token:
        return None
    if not client_secret and client_id != "PlatformCLI":
        return None
    return f"https://{parsed.hostname}", client_id, unquote(client_secret), unquote(refresh_token)


async def _check_salesforce(connection) -> None:
    try:
        auth = _salesforce_auth(connection)
        if auth is None:
            raise ValueError("Invalid Salesforce auth URL")
        auth_url, client_id, client_secret, refresh_token = auth
        auth_host = (urlsplit(auth_url).hostname or "").lower()
        if not (auth_host.endswith(".salesforce.com") or auth_host.endswith(".force.com")):
            raise ValueError("Invalid Salesforce auth URL")
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            token_data = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
            if client_secret:
                token_data["client_secret"] = client_secret
            response = await client.post(
                f"{auth_url}/services/oauth2/token",
                data=token_data,
            )
            response.raise_for_status()
            token_response = response.json()
            access_token = token_response.get("access_token")
            instance_url = token_response.get("instance_url")
            if not access_token or not instance_url:
                raise ValueError("Salesforce token exchange returned an invalid response")
            parsed_instance = urlsplit(instance_url)
            instance_host = (parsed_instance.hostname or "").lower()
            if parsed_instance.scheme != "https" or not (
                instance_host.endswith(".salesforce.com") or instance_host.endswith(".force.com")
            ):
                raise ValueError("Salesforce token exchange returned an invalid response")
            validation = await client.get(
                f"{instance_url.rstrip('/')}/services/data/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            validation.raise_for_status()
    except Exception as exc:
        if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
            error = _safe_error(exc)
        elif str(exc) == "Invalid Salesforce auth URL":
            error = str(exc)
        else:
            error = "Salesforce authentication failed"
        _set("salesforce", connection.name, False, error)
    else:
        _set("salesforce", connection.name, True)


def _check_codex() -> None:
    auth_json = codex_credentials.get_codex_auth_json()
    if auth_json:
        try:
            parsed = json.loads(auth_json)
            active = isinstance(parsed, dict) and bool(parsed)
        except json.JSONDecodeError:
            active = False
        _set("codex", "default", active, None if active else "Saved Codex credentials are invalid")
    elif os.getenv("OPENAI_API_KEY"):
        _set("codex", "default", True)
    else:
        _set("codex", "default", False, "No Codex login or OPENAI_API_KEY configured")


async def check_all() -> None:
    tasks: list[asyncio.Future | asyncio.Task | Any] = []
    for connection in github_credentials.list_github_connections():
        tasks.append(_check_github(connection.name, connection.access_token))
    for connection in jira_connections.list_connections():
        tasks.append(_check_jira(connection))
    for connection in salesforce_credentials.list_salesforce_connections():
        tasks.append(_check_salesforce(connection))
    _check_codex()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def refresh(provider: str, name: str) -> dict[str, Any]:
    """Recheck one persisted connection and return its latest health result."""
    if provider == "github":
        connection = github_credentials.get_github_connection(name)
        if connection is None:
            _set(provider, name, False, "GitHub connection not found")
        else:
            await _check_github(connection.name, connection.access_token)
    elif provider == "jira":
        connection = jira_connections.get_connection(name)
        if connection is None:
            _set(provider, name, False, "Jira connection not found")
        else:
            await _check_jira(connection)
    elif provider == "salesforce":
        connection = salesforce_credentials.get_salesforce_connection(name)
        if connection is None:
            _set(provider, name, False, "Salesforce connection not found")
        else:
            await _check_salesforce(connection)
    else:
        raise ValueError(f"Unsupported connection health provider: {provider}")
    return get(provider, name)
