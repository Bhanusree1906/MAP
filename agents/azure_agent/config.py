"""Configuration for the Azure DevOps integration.

Which Azure organization + project the pipeline reads work items from - and
the credentials for it - come from a named AzureConnection row in the
database (see connections.py), so the same code can be pointed at different
Azure projects without touching env vars or code. Mirrors
agents/jira_agent/config.py.
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
    connection_name: str
    organization_url: str
    project_name: str
    # Tokens grant "read, write & manage" on Work Items; each connection
    # decides whether write tools (posting completion comments) are trusted
    # for its project, defaulting to read-only.
    read_only: bool
    log_level: str


def load_settings(connection_name: str | None = None) -> Settings:
    connection_name = connection_name or os.getenv("AZURE_AGENT_CONNECTION", "default")
    connection = get_connection(connection_name)
    if connection is None:
        raise RuntimeError(
            f"No Azure connection named {connection_name!r} found. Create one from the "
            "Connections page in the UI (set an organization URL, project name and PAT)."
        )

    return Settings(
        connection_name=connection.name,
        organization_url=connection.organization_url,
        project_name=connection.project_name,
        read_only=_env_bool("AZURE_AGENT_READ_ONLY", connection.read_only),
        log_level=os.getenv("AZURE_AGENT_LOG_LEVEL", "INFO").upper(),
    )