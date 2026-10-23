"""MCP client wiring: connects to Atlassian's remote MCP server and returns
LangChain tools scoped to a single Jira project, ready to hand to a
Gemini-backed agent.

Two things happen here beyond just fetching tools:

1. Whole-workspace tools are dropped entirely - not filtered by read/write,
   excluded outright - because this agent is meant to operate inside one
   Jira project, not browse every project/space/component the
   authenticated user can see (getVisibleJiraProjects, Confluence spaces,
   Compass, Teamwork Graph, the generic cross-product search/fetch tools).
2. The remaining Jira tools are wrapped so they can't be used to read or
   write another project: JQL searches get `project = "<KEY>"` ANDed in,
   project-key arguments are pinned to the configured project, and
   issue-key arguments are validated against it before the call goes out.

Gemini's function-calling schema only understands a fixed subset of JSON
Schema (see langchain_google_genai._function_utils._ALLOWED_SCHEMA_FIELDS).
Anything outside that set - like the `$schema` and `additionalProperties`
keys the Atlassian MCP server emits on every tool - gets silently dropped by
langchain-google-genai, but it logs a WARNING for every dropped key on every
tool. We sanitize the schema ourselves before it reaches that code path, so
there's nothing left to warn about.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from .config import Settings
from .oauth import build_atlassian_oauth
from .runner import as_text

logger = logging.getLogger(__name__)

# Mirrors langchain_google_genai._function_utils._ALLOWED_SCHEMA_FIELDS.
_GEMINI_ALLOWED_SCHEMA_KEYS = {
    "type", "type_", "description", "enum", "format", "items", "properties",
    "required", "nullable", "anyOf", "default", "minimum", "maximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "title",
}

# Tool-name prefixes considered read-only on Atlassian's remote MCP server
# (everything else - create/update/edit/add/transition - mutates Jira/Confluence).
_READ_ONLY_PREFIXES = ("get", "search", "lookup", "fetch", "atlassianUserInfo")

# Tools that browse or act outside a single Jira project - listing every
# project, Confluence spaces/pages, Compass components, the Teamwork Graph,
# and the ungoverned generic search/fetch tools. Excluded outright,
# regardless of read_only, because there's no per-project scope to enforce
# on them.
_OUT_OF_SCOPE_TOOLS = {
    "getVisibleJiraProjects",
    "search",
    "fetch",
    "getConfluencePage",
    "searchConfluenceUsingCql",
    "getConfluenceSpaces",
    "getPagesInConfluenceSpace",
    "getConfluencePageFooterComments",
    "getConfluencePageInlineComments",
    "getConfluenceCommentChildren",
    "getConfluencePageDescendants",
    "createConfluencePage",
    "updateConfluencePage",
    "createConfluenceFooterComment",
    "createConfluenceInlineComment",
    "getCompassComponents",
    "getCompassComponent",
    "getCompassCustomFieldDefinitions",
    "createCompassCustomFieldDefinition",
    "createCompassComponent",
    "createCompassComponentRelationship",
    "getTeamworkGraphContext",
    "getTeamworkGraphObject",
    "addTeamworkGraphContext",
}

# Args that name a single Jira issue (e.g. "ENG-123") - validated against the
# configured project's key prefix before the call is allowed through.
_ISSUE_KEY_ARGS = ("issueIdOrKey",)

# Args that name a Jira project directly - pinned to the configured project
# key rather than trusting whatever the model supplied.
_PROJECT_KEY_ARGS = ("projectKey", "projectIdOrKey")


def _sanitize_schema(node: Any) -> Any:
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _GEMINI_ALLOWED_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # Keys here are arbitrary field names (e.g. "projectId"), not
                # schema keywords - only sanitize each property's schema, not
                # the field names themselves, or every property gets dropped.
                cleaned[key] = {name: _sanitize_schema(prop) for name, prop in value.items()}
            else:
                cleaned[key] = _sanitize_schema(value)
        return cleaned
    if isinstance(node, list):
        return [_sanitize_schema(item) for item in node]
    return node


def _is_read_only(tool_name: str) -> bool:
    return tool_name.startswith(_READ_ONLY_PREFIXES)


_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def _scope_jql(jql: str | None, project_key: str) -> str:
    project_filter = f'project = "{project_key}"'
    jql = (jql or "").strip()
    if not jql:
        return project_filter

    # ORDER BY must stay outside any parenthesized condition - split it off
    # before wrapping, then re-append, or the query is invalid JQL syntax.
    match = _ORDER_BY_RE.search(jql)
    condition, order_by = (jql[: match.start()].strip(), jql[match.start() :].strip()) if match else (jql, "")

    # Always AND the project filter in, even if the model already scoped the
    # query correctly - redundant ANDing is a no-op, but it's the only way to
    # guarantee a mis-scoped or unscoped query can't slip through.
    scoped = f"({condition}) AND {project_filter}" if condition else project_filter
    return f"{scoped} {order_by}" if order_by else scoped


def _enforce_issue_scope(issue_key: str, project_key: str) -> None:
    if not re.match(rf"^{re.escape(project_key)}-\d+$", issue_key.strip(), re.IGNORECASE):
        raise ToolException(
            f"This agent is scoped to Jira project '{project_key}'. Issue "
            f"'{issue_key}' is outside that project and cannot be accessed."
        )


def unwrap_tool_result(raw: Any) -> Any:
    """MCP tool results come back as a list of content blocks
    (`[{"type": "text", "text": "..."}]`), not the plain value - unwrap to
    the text and parse it as JSON where possible."""
    text = as_text(raw) if isinstance(raw, list) else raw
    if isinstance(text, str):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


async def _resolve_cloud_id(tools: list[BaseTool], site_url: str | None) -> str:
    """Nearly every tool on Atlassian's remote MCP server requires a
    `cloudId` the caller is expected to already know - resolved here via
    getAccessibleAtlassianResources so callers (including the model) never
    have to discover and pass it themselves."""
    resource_tool = next((t for t in tools if t.name == "getAccessibleAtlassianResources"), None)
    if resource_tool is None:
        raise ToolException(
            "Atlassian MCP server did not expose getAccessibleAtlassianResources; "
            "cannot resolve a cloudId for this connection."
        )
    resources = unwrap_tool_result(await resource_tool.ainvoke({}))
    if not isinstance(resources, list) or not resources:
        raise ToolException("No accessible Atlassian resources found for this connection.")
    if site_url:
        match = next(
            (r for r in resources if str(r.get("url", "")).rstrip("/") == site_url.rstrip("/")), None
        )
        if match:
            return match["id"]
    return resources[0]["id"]


def _scope_tool(tool: BaseTool, project_key: str, get_cloud_id: Callable[[], Awaitable[str]]) -> None:
    """Rewrite a tool's coroutine in place so every call stays inside project_key."""
    original_coroutine = tool.coroutine
    if original_coroutine is None:
        return

    needs_cloud_id = "cloudId" in ((tool.args_schema or {}).get("properties") or {})

    async def scoped_coroutine(**kwargs: Any) -> Any:
        if needs_cloud_id and not kwargs.get("cloudId"):
            kwargs["cloudId"] = await get_cloud_id()
        if tool.name == "searchJiraIssuesUsingJql":
            kwargs["jql"] = _scope_jql(kwargs.get("jql"), project_key)
        else:
            for arg_name in _ISSUE_KEY_ARGS:
                if kwargs.get(arg_name):
                    _enforce_issue_scope(str(kwargs[arg_name]), project_key)
            for arg_name in _PROJECT_KEY_ARGS:
                if arg_name in kwargs:
                    # Pin rather than validate-and-reject: these tools only
                    # ever act on one project at a time, so there's nothing
                    # lost by always pointing them at the configured one.
                    kwargs[arg_name] = project_key
        return await original_coroutine(**kwargs)

    tool.coroutine = scoped_coroutine


async def load_atlassian_tools(
    settings: Settings,
    *,
    allow_interactive_auth: bool = True,
    update_health: bool = False,
) -> list[BaseTool]:
    mcp_client = MultiServerMCPClient(
        {
            "atlassian": {
                "transport": "streamable_http",
                "url": settings.atlassian_mcp_url,
                "auth": build_atlassian_oauth(
                    settings.atlassian_mcp_url,
                    settings.connection_name,
                    allow_interactive_auth=allow_interactive_auth,
                ),
            }
        }
    )
    try:
        tools = await mcp_client.get_tools()
    except Exception:
        if update_health:
            from backend.services import connection_health

            connection_health.record_result(
                "jira", settings.connection_name, False, "Atlassian MCP authentication failed"
            )
        raise

    if update_health:
        from backend.services import connection_health

        connection_health.record_result("jira", settings.connection_name, True)

    out_of_scope = [tool.name for tool in tools if tool.name in _OUT_OF_SCOPE_TOOLS]
    tools = [tool for tool in tools if tool.name not in _OUT_OF_SCOPE_TOOLS]
    if out_of_scope:
        logger.info("Excluded %d out-of-scope tool(s): %s", len(out_of_scope), ", ".join(out_of_scope))

    if settings.read_only:
        excluded = [tool.name for tool in tools if not _is_read_only(tool.name)]
        tools = [tool for tool in tools if _is_read_only(tool.name)]
        if excluded:
            logger.info("Excluded %d write tool(s) (read-only mode): %s", len(excluded), ", ".join(excluded))

    cloud_id_cache: dict[str, str] = {}

    async def get_cloud_id() -> str:
        # Resolved lazily (only if some tool actually needs it) and cached
        # for the lifetime of this tool list, so it costs at most one extra
        # MCP call per agent run/backend request, not one per tool call.
        if "value" not in cloud_id_cache:
            cloud_id_cache["value"] = await _resolve_cloud_id(tools, settings.site_url)
        return cloud_id_cache["value"]

    for tool in tools:
        tool.args_schema = _sanitize_schema(tool.args_schema)
        _scope_tool(tool, settings.project_key, get_cloud_id)

    logger.info(
        "Loaded %d Atlassian MCP tool(s) scoped to project '%s': %s",
        len(tools),
        settings.project_key,
        ", ".join(tool.name for tool in tools),
    )
    return tools
