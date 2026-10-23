"""Command-line entrypoint.

Usage:
    python -m agents.jira_agent.cli "What issues are open?"
    python -m agents.jira_agent.cli --connection acme-eng "What issues are open?"
    python -m agents.jira_agent.cli --debug "What issues are open?"

Requires a connection to already exist (see agenttest.py --setup at the repo
root to create one).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace

from dotenv import load_dotenv

from .agent import build_agent
from .config import load_settings
from .logging_config import configure_logging
from .runner import run_query

DEFAULT_QUERY = "What issues are open in this project?"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask the Jira project research agent a question.")
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument(
        "--connection",
        "-c",
        default=None,
        help="Named connection/project to use (default: $JIRA_AGENT_CONNECTION or 'default').",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Trace every model call, tool call, and MCP request as it happens.",
    )
    return parser.parse_args()


async def _run(query: str, connection_name: str | None, force_debug: bool) -> None:
    settings = load_settings(connection_name)
    if force_debug:
        settings = replace(settings, log_level="DEBUG")
    trace = configure_logging(settings.log_level)

    agent = await build_agent(settings)
    await run_query(agent, query, trace)


def main() -> None:
    load_dotenv()
    args = _parse_args()
    asyncio.run(_run(args.query, args.connection, args.debug))


if __name__ == "__main__":
    main()
