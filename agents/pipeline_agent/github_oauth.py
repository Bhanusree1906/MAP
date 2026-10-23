"""GitHub OAuth App helpers for the shared pipeline Git credential."""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
DEFAULT_SCOPES = "repo"


class OAuthNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthAppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    success_redirect_url: str


def load_oauth_app_config() -> OAuthAppConfig:
    client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise OAuthNotConfigured("GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET are not configured")
    return OAuthAppConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.getenv(
            "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/github/oauth/callback"
        ),
        scopes=os.getenv("GITHUB_OAUTH_SCOPES", DEFAULT_SCOPES),
        success_redirect_url=os.getenv("GITHUB_OAUTH_SUCCESS_REDIRECT", "http://localhost:5173/connections"),
    )


def build_authorize_url(
    config: OAuthAppConfig,
    state: str,
    login: str | None = None,
    select_account: bool = False,
) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": config.scopes,
        "state": state,
        "allow_signup": "false",
    }
    if login:
        params["login"] = login
    if select_account:
        params["prompt"] = "select_account"
    return str(
        httpx.URL(
            AUTHORIZE_URL,
            params=params,
        )
    )


async def exchange_code(config: OAuthAppConfig, code: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    response.raise_for_status()
    payload = response.json()
    if "access_token" not in payload:
        raise ValueError(payload.get("error_description") or "GitHub did not return an access token")
    return payload


async def fetch_authenticated_user(access_token: str) -> tuple[str | None, str | None]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            USER_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
        )
    response.raise_for_status()
    user = response.json()
    return user.get("login"), response.headers.get("X-OAuth-Scopes")