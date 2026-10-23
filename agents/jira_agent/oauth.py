"""OAuth 2.1 wiring for Atlassian's official remote MCP server.

Atlassian hosts Jira/Confluence tools at https://mcp.atlassian.com/v1/mcp/authv2
(Streamable HTTP transport). The first connection needs a one-time browser
login; tokens are then persisted on the connection's DB row (see
connections.py) so later runs against the same named connection reuse them
silently, and different connections (different Jira projects/teams) don't
share a session.
"""

from __future__ import annotations

import asyncio
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from .connections import get_connection, save_oauth_field

# Fixed, not random (port 0) - a completed login registers this exact
# redirect_uri with Atlassian and caches that registration on the
# connection's DB row (see DBTokenStorage below). A random port would
# register a different redirect_uri every run, which the *next* run's
# (also-random) port would no longer match - Atlassian then rejects the
# login with "the app's callback URL is invalid" even though nothing about
# the setup actually changed.
_LOOPBACK_PORT = int(os.getenv("JIRA_MCP_OAUTH_PORT", "8765"))

# Bounds how long an abandoned login (browser tab closed, or never opened)
# can hold the port. Without this, handle_request() blocks forever on a
# single request that may never arrive - the fixed port above then stays
# bound for the rest of the process's life, and every later login attempt
# (including unrelated ones) fails immediately with "Address already in
# use" instead of the real problem.
_CALLBACK_TIMEOUT_SECONDS = 300


class DBTokenStorage(TokenStorage):
    """Persists OAuth tokens/client info on a JiraConnection DB row."""

    def __init__(self, connection_name: str):
        self._connection_name = connection_name

    async def get_tokens(self) -> OAuthToken | None:
        conn = get_connection(self._connection_name)
        if conn is None or not conn.oauth_tokens:
            return None
        return OAuthToken.model_validate(json.loads(conn.oauth_tokens))

    async def set_tokens(self, tokens: OAuthToken) -> None:
        save_oauth_field(self._connection_name, "oauth_tokens", tokens.model_dump(mode="json"))
        from backend.services.connection_health import record_result

        record_result("jira", self._connection_name, True)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        conn = get_connection(self._connection_name)
        if conn is None or not conn.oauth_client_info:
            return None
        return OAuthClientInformationFull.model_validate(json.loads(conn.oauth_client_info))

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        save_oauth_field(self._connection_name, "oauth_client_info", client_info.model_dump(mode="json"))


class _LoopbackCallbackServer:
    """One-shot local HTTP server that catches the OAuth redirect."""

    def __init__(self) -> None:
        result: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                result["code"] = query.get("code", [None])[0]
                result["state"] = query.get("state", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>Authorized - you can close this tab.</body></html>")

            def log_message(self, *args):  # silence default request logging
                pass

        self._httpd = HTTPServer(("127.0.0.1", _LOOPBACK_PORT), Handler)
        # handle_request() below returns after this many seconds even with
        # no request, so the wait loop can re-check its overall deadline
        # instead of blocking on one request indefinitely.
        self._httpd.timeout = 1
        self._result = result

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    async def wait_for_callback(self) -> tuple[str, str | None]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _CALLBACK_TIMEOUT_SECONDS
        while "code" not in self._result:
            if loop.time() > deadline:
                raise TimeoutError(
                    "Timed out waiting for the Jira/Atlassian login to complete in the browser "
                    f"(no callback within {_CALLBACK_TIMEOUT_SECONDS}s). Try again."
                )
            await loop.run_in_executor(None, self._httpd.handle_request)
        return self._result["code"], self._result.get("state")

    def close(self) -> None:
        self._httpd.server_close()


def build_atlassian_oauth(
    server_url: str,
    connection_name: str,
    *,
    allow_interactive_auth: bool = True,
) -> OAuthClientProvider:
    """Build the httpx.Auth handler that logs into Atlassian's MCP server.

    On first use (per connection) this opens a browser for the OAuth consent
    screen; on subsequent runs it silently reuses that connection's token
    from the database.
    """
    redirect_uri = f"http://127.0.0.1:{_LOOPBACK_PORT}/callback"

    client_metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        client_name="Agentic Pipeline Deep Agent",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )

    # Bound lazily, only once a fresh browser login actually turns out to be
    # needed (i.e. inside redirect_handler) - not here. Binding eagerly on
    # every call, even when a valid cached token makes the whole dance a
    # no-op, was the actual bug behind "Address already in use": one call
    # (e.g. planning) would bind the fixed port and never release it since
    # callback_handler - the only place that closed it - was never invoked,
    # so the very next call (e.g. execution) collided with its own leftover
    # socket.
    login: dict[str, _LoopbackCallbackServer] = {}

    async def redirect_handler(authorization_url: str) -> None:
        if not allow_interactive_auth:
            raise RuntimeError("No valid stored Atlassian MCP OAuth credentials")
        login["server"] = _LoopbackCallbackServer()
        print(f"\nOpening browser to authorize Jira/Atlassian access:\n{authorization_url}\n")
        webbrowser.open(authorization_url)

    async def callback_handler() -> tuple[str, str | None]:
        server = login.get("server")
        if server is None:
            raise RuntimeError("Jira OAuth callback awaited before a login was started.")
        try:
            return await server.wait_for_callback()
        finally:
            server.close()

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=DBTokenStorage(connection_name),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
