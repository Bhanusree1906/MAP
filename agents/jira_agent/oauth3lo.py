"""Browser-based OAuth 2.0 (3LO) for Jira Cloud's REST API.

This is the "click Connect, authorize Jira in a new tab, land back
automatically" flow - unrelated to oauth.py's loopback flow, which is a
Dynamic Client Registration dance against Atlassian's *MCP* server, only
usable from a local CLI (see that module's docstring for why it broke on a
hosted backend). This one uses a single OAuth app registered once at
https://developer.atlassian.com/console/myapps with a fixed redirect URI,
which is what makes it work from a real web backend.

Setup (one-time, per environment):
1. Create an OAuth 2.0 (3LO) app at developer.atlassian.com/console/myapps.
2. Add the "Jira API" product, with scopes `read:jira-work write:jira-work` and
   `offline_access` (for refresh tokens).
3. Under Authorization, set the callback URL to match JIRA_OAUTH_REDIRECT_URI
   below exactly (e.g. http://localhost:8000/api/jira-connections/oauth/callback
   for local dev).
4. Set JIRA_OAUTH_CLIENT_ID / JIRA_OAUTH_CLIENT_SECRET from the app's
   Settings tab in the environment running this backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
DEFAULT_SCOPES = "read:jira-work write:jira-work offline_access"


class OAuthNotConfigured(RuntimeError):
    """Raised when JIRA_OAUTH_CLIENT_ID/SECRET aren't set in the environment."""


@dataclass(frozen=True)
class OAuthAppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    success_redirect_url: str


def load_oauth_app_config() -> OAuthAppConfig:
    client_id = os.getenv("JIRA_OAUTH_CLIENT_ID")
    client_secret = os.getenv("JIRA_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise OAuthNotConfigured(
            "JIRA_OAUTH_CLIENT_ID / JIRA_OAUTH_CLIENT_SECRET are not set. Create an OAuth 2.0 "
            "(3LO) app at https://developer.atlassian.com/console/myapps, add the Jira API with "
            "scopes 'read:jira-work write:jira-work offline_access', and set those two env vars (see "
            "agents/jira_agent/oauth3lo.py for the full setup steps)."
        )
    return OAuthAppConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.getenv(
            "JIRA_OAUTH_REDIRECT_URI", "http://localhost:8000/api/jira-connections/oauth/callback"
        ),
        scopes=os.getenv("JIRA_OAUTH_SCOPES", DEFAULT_SCOPES),
        success_redirect_url=os.getenv("JIRA_OAUTH_SUCCESS_REDIRECT", "http://localhost:5173/connections"),
    )


def build_authorize_url(config: OAuthAppConfig, state: str) -> str:
    params = {
        "audience": "api.atlassian.com",
        "client_id": config.client_id,
        "scope": config.scopes,
        "redirect_uri": config.redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return str(httpx.URL(AUTHORIZE_URL, params=params))


async def exchange_code(config: OAuthAppConfig, code: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
        )
    response.raise_for_status()
    return response.json()


async def refresh_access_token(config: OAuthAppConfig, refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": refresh_token,
            },
        )
    response.raise_for_status()
    return response.json()


async def fetch_accessible_resources(access_token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            ACCESSIBLE_RESOURCES_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    response.raise_for_status()
    return response.json()


def expires_at_from(token_response: dict) -> datetime:
    return datetime.utcnow() + timedelta(seconds=int(token_response.get("expires_in", 3600)))
