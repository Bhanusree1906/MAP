"""Azure DevOps connector for the agentic pipeline.

Reads work items (User Stories, Tasks, Bugs, ...) from one Azure DevOps
organization + project via the REST API with a PAT, providing for Azure what
agents/jira_agent provides for Jira: connection settings, REST helpers and
ticket -> markdown rendering.
"""

from .config import Settings, load_settings
from .connections import get_connection

__all__ = ["Settings", "load_settings", "get_connection"]