"""Direct Jira Cloud REST helpers (no MCP, no LLM).

Used when the pipeline needs ticket content as plain data - e.g. to write
TICKET.md into the sandbox before Codex plans - rather than going through
MCP. Auth matches backend/routers/tickets.py: prefer OAuth 2.0 (3LO), fall
back to email + API token.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from . import oauth3lo
from .connections import JiraConnection, save_oauth3lo_tokens

_TOKEN_EXPIRY_BUFFER = timedelta(seconds=60)
_ISSUE_FIELDS = [
    "summary",
    "description",
    "comment",
    "status",
    "issuetype",
    "assignee",
    "priority",
    "parent",
    "subtasks",
    "issuelinks",
    "labels",
    "reporter",
    "created",
    "updated",
    "attachment",
]


class JiraRestError(RuntimeError):
    """Raised when Jira credentials are missing or a REST call fails."""


async def resolve_request_auth(connection: JiraConnection) -> tuple[str, dict[str, Any]]:
    """Returns (base_url, extra httpx.AsyncClient.get kwargs)."""
    if connection.jira_oauth_access_token and connection.jira_cloud_id:
        token = connection.jira_oauth_access_token
        expires_at = connection.jira_oauth_expires_at
        if expires_at is not None and expires_at <= datetime.utcnow() + _TOKEN_EXPIRY_BUFFER:
            if not connection.jira_oauth_refresh_token:
                raise JiraRestError(
                    "Jira connection's OAuth session expired. Click 'Connect via Jira' to reconnect."
                )
            try:
                config = oauth3lo.load_oauth_app_config()
                token_response = await oauth3lo.refresh_access_token(config, connection.jira_oauth_refresh_token)
            except oauth3lo.OAuthNotConfigured as exc:
                raise JiraRestError(str(exc)) from None
            except httpx.HTTPStatusError as exc:
                raise JiraRestError(
                    "Jira connection's OAuth session expired. Click 'Connect via Jira' to reconnect."
                ) from exc
            token = token_response["access_token"]
            save_oauth3lo_tokens(
                name=connection.name,
                access_token=token,
                refresh_token=token_response.get("refresh_token"),
                expires_at=oauth3lo.expires_at_from(token_response),
                cloud_id=connection.jira_cloud_id,
                site_url=connection.site_url or "",
            )
        base_url = f"https://api.atlassian.com/ex/jira/{connection.jira_cloud_id}"
        return base_url, {"headers": {"Authorization": f"Bearer {token}", "Accept": "application/json"}}

    if connection.site_url and connection.jira_email and connection.jira_api_token:
        base_url = connection.site_url.rstrip("/")
        return base_url, {
            "auth": httpx.BasicAuth(connection.jira_email, connection.jira_api_token),
            "headers": {"Accept": "application/json"},
        }

    raise JiraRestError(
        "Jira connection has no credentials configured. Click 'Connect via Jira', or set an email/API token."
    )


async def get_issue(connection: JiraConnection, ticket_key: str) -> dict[str, Any]:
    """Fetch one issue, scoped to the connection's configured project."""
    expected_prefix = f"{connection.project_key}-"
    if not ticket_key.upper().startswith(expected_prefix):
        raise JiraRestError(
            f"Ticket {ticket_key!r} is not in project {connection.project_key!r}."
        )

    base_url, request_kwargs = await resolve_request_auth(connection)
    url = f"{base_url}/rest/api/3/issue/{ticket_key}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url, params={"fields": ",".join(_ISSUE_FIELDS)}, **request_kwargs)
        except httpx.RequestError as exc:
            raise JiraRestError(f"Could not reach Jira: {exc}") from exc

    if response.status_code == 404:
        raise JiraRestError(f"Jira issue {ticket_key!r} was not found.")
    if response.status_code == 401:
        raise JiraRestError("Jira rejected the configured credentials.")
    if response.status_code == 403:
        raise JiraRestError("Jira credentials do not have access to this issue.")
    if response.status_code >= 400:
        raise JiraRestError(f"Jira API error ({response.status_code}): {response.text}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise JiraRestError(f"Jira returned an unexpected payload for {ticket_key!r}.")
    return payload


async def download_attachment(connection: JiraConnection, content_url: str) -> tuple[bytes, str]:
    """Download Jira attachment bytes with the same auth as issue reads."""
    base_url, request_kwargs = await resolve_request_auth(connection)
    if content_url.startswith("/"):
        content_url = f"{base_url}{content_url}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            response = await client.get(content_url, params={"redirect": "false"}, **request_kwargs)
        except httpx.RequestError as exc:
            raise JiraRestError(f"Could not fetch Jira attachment: {exc}") from exc
    if response.status_code == 403:
        raise JiraRestError("Jira credentials cannot view this attachment.")
    if response.status_code >= 400:
        raise JiraRestError(f"Jira attachment API error ({response.status_code}).")
    mime_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    return response.content, mime_type


def _text_to_adf(text: str) -> dict[str, Any]:
    """Plain text -> minimal Atlassian Document Format, one paragraph per
    blank-line-separated block (the inverse of ticket.py's adf_to_text).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs],
    }


async def add_comment(connection: JiraConnection, ticket_key: str, text: str) -> None:
    """Post a plain-text comment (auto-converted to ADF) on ticket_key."""
    expected_prefix = f"{connection.project_key}-"
    if not ticket_key.upper().startswith(expected_prefix):
        raise JiraRestError(
            f"Ticket {ticket_key!r} is not in project {connection.project_key!r}."
        )

    base_url, request_kwargs = await resolve_request_auth(connection)
    url = f"{base_url}/rest/api/3/issue/{ticket_key}/comment"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.post(url, json={"body": _text_to_adf(text)}, **request_kwargs)
        except httpx.RequestError as exc:
            raise JiraRestError(f"Could not reach Jira: {exc}") from exc

    if response.status_code == 404:
        raise JiraRestError(f"Jira issue {ticket_key!r} was not found.")
    if response.status_code == 401:
        if "scope does not match" in response.text.lower():
            raise JiraRestError(
                "Jira OAuth token does not have the required write scope. "
                "Add write:jira-work to the OAuth app, then click 'Connect via Jira' again."
            )
        raise JiraRestError("Jira rejected the configured credentials.")
    if response.status_code == 403:
        raise JiraRestError("Jira credentials do not have access to this issue.")
    if response.status_code >= 400:
        raise JiraRestError(f"Jira API error ({response.status_code}): {response.text}")


async def transition_issue_to_status(
    connection: JiraConnection, ticket_key: str, status_name: str
) -> None:
    """Move an issue using the Jira workflow transition with status_name."""
    expected_prefix = f"{connection.project_key}-"
    if not ticket_key.upper().startswith(expected_prefix):
        raise JiraRestError(
            f"Ticket {ticket_key!r} is not in project {connection.project_key!r}."
        )

    base_url, request_kwargs = await resolve_request_auth(connection)
    issue_url = f"{base_url}/rest/api/3/issue/{ticket_key}/transitions"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(issue_url, **request_kwargs)
        except httpx.RequestError as exc:
            raise JiraRestError(f"Could not reach Jira: {exc}") from exc

        if response.status_code >= 400:
            raise JiraRestError(f"Jira API error ({response.status_code}): {response.text}")
        transitions = response.json().get("transitions", [])
        requested_status = status_name.strip().casefold()
        accepted_statuses = {requested_status}
        if requested_status == "agent complete":
            accepted_statuses.add("agent completed")
        transition = next(
            (
                item
                for item in transitions
                if str(item.get("to", {}).get("name", "")).strip().casefold()
                in accepted_statuses
            ),
            None,
        )
        if transition is None:
            raise JiraRestError(
                f"Jira issue {ticket_key!r} has no available transition to {status_name!r}."
            )

        try:
            response = await client.post(
                issue_url,
                json={"transition": {"id": str(transition["id"])}},
                **request_kwargs,
            )
        except httpx.RequestError as exc:
            raise JiraRestError(f"Could not reach Jira: {exc}") from exc

    if response.status_code >= 400:
        raise JiraRestError(f"Jira API error ({response.status_code}): {response.text}")
