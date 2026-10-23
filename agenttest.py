#!/usr/bin/env python3
"""Standalone script for creating a Jira project connection and testing the
agent against it. Connections (project key + credentials) are stored in the
project database (see database/models/jira_connection.py), so the same
agent code can be reused against different Jira projects by creating a new
named connection - no code or .env changes needed.

Create/update a connection (one-time per Jira project):
    python agenttest.py --setup --connection acme-eng --project-key ENG
    python agenttest.py --setup --connection acme-eng --project-key ENG --site-url https://acme.atlassian.net
    python agenttest.py --setup --connection acme-eng --project-key ENG --allow-writes

Set the Jira REST API credentials used by the backend's ticket-listing
endpoint (email + API token from https://id.atlassian.com/manage-profile/security/api-tokens,
no browser/OAuth needed):
    python agenttest.py --setup --connection acme-eng --project-key ENG \
        --site-url https://acme.atlassian.net --jira-email you@acme.com --jira-api-token ...

List saved connections:
    python agenttest.py --list

Remove a connection:
    python agenttest.py --delete acme-eng

Ask the agent something. The first query for a given connection opens a
browser for Atlassian login; after that the token is reused automatically:
    python agenttest.py --connection acme-eng "What issues are open?"
    python agenttest.py --connection acme-eng --debug "What issues are open?"

If --connection is omitted, $JIRA_AGENT_CONNECTION (or "default") is used.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace

from dotenv import load_dotenv

from agents.jira_agent.agent import build_agent
from agents.jira_agent.config import load_settings
from agents.jira_agent.connections import delete_connection, list_connections, upsert_connection
from agents.jira_agent.logging_config import configure_logging
from agents.jira_agent.runner import run_query

DEFAULT_QUERY = "What issues are open in this project?"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Jira project connections and test the agent against them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY, help="Question to ask the agent.")
    parser.add_argument(
        "--connection",
        "-c",
        default=None,
        help="Connection/project name to use (default: $JIRA_AGENT_CONNECTION or 'default').",
    )

    setup_group = parser.add_argument_group("connection setup")
    setup_group.add_argument("--setup", action="store_true", help="Create or update a connection, then exit.")
    setup_group.add_argument("--project-key" ,help="Jira project key to scope the connection to, e.g. ENG.")
    setup_group.add_argument("--site-url", default=None, help="Jira site URL, e.g. https://acme.atlassian.net.")
    setup_group.add_argument("--mcp-url", default=None, help="Override the Atlassian MCP server URL.")
    setup_group.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow write tools (create/edit/transition/comment) for this connection. Default is read-only.",
    )
    setup_group.add_argument(
        "--jira-email",
        default=None,
        help="Atlassian account email for REST API Basic Auth (used by the backend's ticket-listing endpoint).",
    )
    setup_group.add_argument(
        "--jira-api-token",
        default=None,
        help="Atlassian API token for REST API Basic Auth, from id.atlassian.com/manage-profile/security/api-tokens.",
    )

    parser.add_argument("--list", action="store_true", help="List saved connections, then exit.")
    parser.add_argument("--delete", metavar="NAME", help="Delete a saved connection, then exit.")
    parser.add_argument("--debug", action="store_true", help="Trace every model call, tool call, and MCP request.")
    return parser.parse_args()


def _print_connections() -> None:
    connections = list_connections()
    if not connections:
        print("No connections saved yet. Create one with:")
        print("  python agenttest.py --setup --connection NAME --project-key KEY")
        return
    for conn in connections:
        mode = "read-only" if conn.read_only else "read-write"
        print(f"- {conn.name}: project={conn.project_key} site={conn.site_url or '-'} ({mode})")


async def _run_query(connection_name: str | None, query: str, debug: bool) -> None:
    settings = load_settings(connection_name)
    if debug:
        settings = replace(settings, log_level="DEBUG")
    trace = configure_logging(settings.log_level)

    print(f"Connecting to Jira project '{settings.project_key}' (connection={settings.connection_name!r})...")
    agent = await build_agent(settings)
    await run_query(agent, query, trace)


def main() -> None:
    load_dotenv()
    args = _parse_args()

    if args.list:
        _print_connections()
        return

    if args.delete:
        removed = delete_connection(args.delete)
        print(f"Deleted connection {args.delete!r}." if removed else f"No connection named {args.delete!r}.")
        return

    if args.setup:
        if not args.project_key:
            raise SystemExit("--setup requires --project-key")
        name = args.connection or "default"
        conn = upsert_connection(
            name=name,
            project_key=args.project_key,
            site_url=args.site_url,
            mcp_server_url=args.mcp_url,
            read_only=not args.allow_writes,
            jira_email=args.jira_email,
            jira_api_token=args.jira_api_token,
        )
        print(f"Saved connection '{conn.name}' -> project '{conn.project_key}'.")
        print("Now run a query against it, e.g.:")
        print(f'  python agenttest.py --connection {conn.name} "What issues are open?"')
        return

    try:
        asyncio.run(_run_query(args.connection, args.query, args.debug))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
