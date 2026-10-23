"""Configuration for the Jira project research agent.

Model/runtime knobs still come from the environment, but which Jira project
the agent is scoped to - and the credentials for it - come from a named
JiraConnection row in the database (see connections.py), so the same agent
code can be pointed at different Jira projects without touching env vars or
code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .connections import get_connection


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    google_model: str
    # gemini-2.5-flash defaults to a dynamic (unbounded) thinking budget. With
    # dozens of MCP tool schemas in context it can burn its entire output
    # budget on internal reasoning and hit MAX_TOKENS before ever emitting a
    # tool call or answer - i.e. a silent empty response. Capping the budget
    # and giving max_output_tokens headroom keeps the run from stalling out.
    thinking_budget: int
    max_output_tokens: int
    atlassian_mcp_url: str
    connection_name: str
    project_key: str
    site_url: str | None
    # Only expose get/search/lookup/fetch tools to the agent. The Atlassian
    # MCP server also exposes write tools (createJiraIssue, editJiraIssue,
    # transitionJiraIssue, ...); each connection decides whether those are
    # trusted for its project, defaulting to read-only.
    read_only: bool
    log_level: str


def load_settings(connection_name: str | None = None) -> Settings:
    connection_name = connection_name or os.getenv("JIRA_AGENT_CONNECTION", "default")
    connection = get_connection(connection_name)
    if connection is None:
        raise RuntimeError(
            f"No Jira connection named {connection_name!r} found. Create one first, e.g.:\n"
            f"  python agenttest.py --setup --connection {connection_name} --project-key ENG"
        )

    return Settings(
        google_model=os.getenv("JIRA_AGENT_MODEL", "gemini-2.5-flash"),
        thinking_budget=int(os.getenv("JIRA_AGENT_THINKING_BUDGET", "2048")),
        max_output_tokens=int(os.getenv("JIRA_AGENT_MAX_OUTPUT_TOKENS", "8192")),
        atlassian_mcp_url=connection.mcp_server_url,
        connection_name=connection.name,
        project_key=connection.project_key,
        site_url=connection.site_url,
        read_only=_env_bool("JIRA_AGENT_READ_ONLY", connection.read_only),
        log_level=os.getenv("JIRA_AGENT_LOG_LEVEL", "INFO").upper(),
    )
