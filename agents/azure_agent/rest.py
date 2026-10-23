"""Direct Azure DevOps REST helpers (no MCP, no LLM).

Used when the pipeline needs work item content as plain data - e.g. to write
TICKET.md into the sandbox before Codex plans - and by
backend/routers/tickets.py to list/fetch work items for the UI. Auth is an
Azure DevOps Personal Access Token (PAT) sent as HTTP Basic credentials with
an empty username, the same way the Azure DevOps REST API accepts them.

Endpoints used (api-version 7.1; work item *comments* take 7.1-preview):
  POST /wit/wiql                      - run a WIQL query scoped to a project
  GET  /wit/workitems?ids=...&$expand=all  - bulk-fetch work items by id
  GET  /wit/workitems/{id}            - fetch one work item (+ relations)
  GET  /wit/workitems/{id}/comments   - discussion comments on an item
  POST /wit/workitems/{id}/comments   - add a discussion comment
"""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser
from typing import Any

import httpx
from urllib.parse import quote

from .connections import AzureConnection

_API_VERSION = "7.1"


_DEFAULT_WIQL = (
    "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType] "
    "FROM WorkItems WHERE [System.TeamProject] = @project "
    "ORDER BY [System.ChangedDate] DESC"
)


class AzureRestError(RuntimeError):
    """Raised when Azure credentials are missing or a REST call fails."""


def _html_to_text(html: str | None) -> str:
    """Straightforward HTML -> plain text conversion for Azure's rich-text
    ``System.Description``. Standard-library only."""
    if not html:
        return ""

    class _Extractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._in_pre = False

        def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D102
            if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "hr"):
                self.parts.append("\n")
            if tag == "pre":
                self._in_pre = True

        def handle_endtag(self, tag: str) -> None:  # noqa: D102
            if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre"):
                self.parts.append("\n")
            if tag == "pre":
                self._in_pre = False

        def handle_data(self, data: str) -> None:  # noqa: D102
            if self._in_pre:
                self.parts.append(data)
            else:
                self.parts.append(data)

    extractor = _Extractor()
    extractor.feed(html or "")
    text = "".join(extractor.parts)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(line for line in lines if line)


def _field(work_item: dict[str, Any], *names: str) -> Any:
    fields = work_item.get("fields") or {}
    for name in names:
        if fields.get(name) is not None:
            return fields[name]
    return None


def html_to_text(html: str | None) -> str:
    """Public HTML -> plain text helper (Azure's rich-text Description)."""
    return _html_to_text(html)


def _display_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("uniqueName") or "")
    return str(value or "")


def related_work_item_ids(work_item: dict[str, Any], rel_substring: str) -> list[int]:
    """Ids of related work items whose relation type contains rel_substring
    (e.g. ``Hierarchy-Reverse`` for the parent, ``Hierarchy-Forward`` for
    children)."""
    ids = []
    for relation in work_item.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        rel = str(relation.get("rel") or "")
        if rel_substring.lower() in rel.lower() and isinstance(relation.get("id"), int):
            ids.append(int(relation["id"]))
    return ids


async def resolve_request_auth(connection: AzureConnection) -> tuple[str, dict[str, Any]]:
    """Returns (base_url, extra httpx.AsyncClient method kwargs)."""
    if not connection.pat:
        raise AzureRestError(
            "Azure connection has no PAT configured. Add a Personal Access Token "
            "with Work Items read access under Connections."
        )
    token = base64.b64encode(f":{connection.pat}".encode("utf-8")).decode("ascii")
    project = quote(connection.project_name)
    base_url = f"{connection.organization_url.rstrip('/')}/{project}/_apis"
    return base_url, {
        "headers": {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }
    }


async def _get(
    connection: AzureConnection, path: str, *, preview: bool = False, **client_kwargs
) -> httpx.Response:
    """GET helper. ``preview=True`` sends ``7.1-preview`` - some resources
    (e.g. work item comments) are still under preview in 7.1 and Azure
    rejects a plain ``7.1`` with a 400."""
    base_url, request_kwargs = await resolve_request_auth(connection)
    url = f"{base_url}{path}"
    separator = "&" if "?" in path else "?"
    version = f"{_API_VERSION}-preview" if preview else _API_VERSION
    url = f"{url}{separator}api-version={version}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            return await client.get(url, **{**request_kwargs, **client_kwargs})
        except httpx.RequestError as exc:
            raise AzureRestError(f"Could not reach Azure DevOps: {exc}") from exc


async def _post(
    connection: AzureConnection,
    path: str,
    body: dict[str, Any],
    *,
    preview: bool = False,
) -> httpx.Response:
    base_url, request_kwargs = await resolve_request_auth(connection)
    url = f"{base_url}{path}"
    separator = "&" if "?" in path else "?"
    version = f"{_API_VERSION}-preview" if preview else _API_VERSION
    url = f"{url}{separator}api-version={version}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            return await client.post(url, json=body, **request_kwargs)
        except httpx.RequestError as exc:
            raise AzureRestError(f"Could not reach Azure DevOps: {exc}") from exc


def _raise_for_status(response: httpx.Response, context: str) -> None:
    if response.status_code in (401, 403):
        raise AzureRestError(
            f"Azure DevOps rejected the configured credentials ({context}). "
            "Check the connection's PAT under Connections."
        )
    if response.status_code == 404:
        raise AzureRestError(f"Azure DevOps work item was not found ({context}).")
    if response.status_code >= 400:
        raise AzureRestError(f"Azure DevOps API error ({response.status_code}): {response.text[:500]}")


def _friendly_work_item_type(work_item_type: str) -> str:
    """'User Story' -> 'User Story', 'Bug' -> 'Bug', keep Azure's own names."""
    return str(work_item_type or "Work Item")


def _wiql(wiql: str | None, project_name: str) -> str:
    query = (wiql or _DEFAULT_WIQL).strip()
    marker = "@project"
    # Azure WIQL string literals are single-quoted with '' as the escape, so a
    # project name containing a quote is doubled instead of backslash-escaped.
    if marker in query:
        literal = "'" + project_name.replace("'", "''") + "'"
        query = query.replace(marker, literal)
    return query


async def list_work_items(
    connection: AzureConnection,
    wiql: str | None = None,
    top: int = 100,
) -> list[dict[str, Any]]:
    """Run a project-scoped WIQL query and return the matching work items
    with their full field sets (bulk-fetched in one or more requests).
    Mirrors the working reference pattern: WIQL for ids, then GET with
    $expand=all for full details."""
    response = await _post(
        connection,
        "/wit/wiql",
        {"query": _wiql(wiql, connection.project_name)},
    )
    _raise_for_status(response, "query")

    payload = response.json() if response.content else {}
    refs = payload.get("workItems") or []
    ids = [int(ref["id"]) for ref in refs if isinstance(ref, dict) and ref.get("id")][:top]
    if not ids:
        return []

    items: list[dict[str, Any]] = []
    # Azure DevOps GET /wit/workitems accepts max 200 ids per call.
    for start in range(0, len(ids), 200):
        chunk = ids[start : start + 200]
        ids_param = ",".join(str(i) for i in chunk)
        batch = await _get(
            connection,
            f"/wit/workitems?ids={ids_param}&$expand=all",
        )
        _raise_for_status(batch, "work items fetch")
        values = batch.json().get("value") if batch.content else []
        items.extend(v for v in values if isinstance(v, dict))
    return items


async def get_work_item(connection: AzureConnection, work_item_id: int) -> dict[str, Any]:
    response = await _get(
        connection,
        f"/wit/workitems/{int(work_item_id)}",
        params={"$expand": "all"},
    )
    _raise_for_status(response, "work item fetch")
    payload = response.json() if response.content else {}
    if not isinstance(payload, dict):
        raise AzureRestError(f"Azure DevOps returned an unexpected payload for {work_item_id!r}.")
    return payload


async def get_work_item_comments(connection: AzureConnection, work_item_id: int) -> list[dict[str, Any]]:
    # Comments are a preview resource in 7.1 - Azure 400s a plain 7.1 here.
    response = await _get(connection, f"/wit/workitems/{int(work_item_id)}/comments", preview=True)
    _raise_for_status(response, "comments fetch")
    payload = response.json() if response.content else {}
    comments = payload.get("comments") or []
    return [c for c in comments if isinstance(c, dict)]


def _comment_lines(comments: list[dict[str, Any]], heading: str) -> list[str]:
    if not comments:
        return []
    lines: list[str] = ["", heading, ""]
    for comment in comments:
        author = _display_name(comment.get("createdBy"))
        created = comment.get("createdDate") or ""
        body = (comment.get("text") or "").strip() or "(empty)"
        lines.append(f"### {author or 'Unknown'} ({created})")
        lines.append("")
        lines.append(body)
        lines.append("")
    return lines


def work_item_to_markdown(
    work_item: dict[str, Any],
    *,
    parent: dict[str, Any] | None = None,
    children: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    parent_comments: list[dict[str, Any]] | None = None,
) -> str:
    """Render one Azure DevOps work item as markdown, mirroring
    agents/jira_agent/rest.py's issue_to_markdown section layout."""
    item_id = _field(work_item, "System.Id")
    title = _field(work_item, "System.Title") or ""
    work_item_type = _friendly_work_item_type(_field(work_item, "System.WorkItemType") or "")
    state = _field(work_item, "System.State") or ""
    assignee = _display_name(_field(work_item, "System.AssignedTo")) or "Unassigned"
    area = _field(work_item, "System.AreaPath") or ""
    priority = _field(work_item, "Microsoft.VSTS.Common.Priority", "System.Priority")
    description = _html_to_text(_field(work_item, "System.Description"))

    lines: list[str] = [
        f"# {item_id}: {title}",
        "",
        f"- Type: {work_item_type or '-'}",
        f"- Status: {state or '-'}",
        f"- Assigned to: {assignee}",
        f"- Priority: {priority or '-'}",
        f"- Area: {area or '-'}",
        "",
        "## Description",
        "",
        description or "(no description)",
    ]

    if parent is not None:
        parent_id = _field(parent, "System.Id")
        parent_title = _field(parent, "System.Title") or ""
        parent_description = _html_to_text(_field(parent, "System.Description"))
        lines.extend(
            [
                "",
                "## Parent",
                "",
                f"### {parent_id}: {parent_title}",
                "",
                "This item is one slice of the parent work item below - use it for the "
                "overall goal and constraints, but only implement the item above.",
                "",
                parent_description or "(no description)",
            ]
        )
        if parent_comments:
            lines.extend(_comment_lines(parent_comments, "### Parent comments"))

    if comments:
        lines.extend(_comment_lines(comments, "## Comments"))

    if children:
        lines.extend(["", "## Child work items", ""])
        lines.append(
            "Check each child item's status and comments below before doing its "
            "work - a child already Done, or with a comment saying the work is "
            "already implemented, should not be redone."
        )
        for child in children:
            child_id = _field(child, "System.Id")
            child_title = _field(child, "System.Title") or ""
            child_state = _field(child, "System.State") or "-"
            child_description = _html_to_text(_field(child, "System.Description"))
            lines.append("")
            lines.append(f"### {child_id}: {child_title}")
            lines.append("")
            lines.append(f"- Status: {child_state}")
            lines.append("")
            lines.append(child_description or "(no description)")

    return "\n".join(lines).rstrip() + "\n"


async def download_attachment(connection: AzureConnection, content_url: str) -> tuple[bytes, str]:
    """Download an attached file (content_url from an AttachedFile relation)
    with the same auth as work item reads."""
    base_url, request_kwargs = await resolve_request_auth(connection)
    if content_url.startswith("/"):
        content_url = f"{base_url}{content_url}"
    separator = "&" if "?" in content_url else "?"
    url = f"{content_url}{separator}api-version={_API_VERSION}&download=false"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, **request_kwargs)
        except httpx.RequestError as exc:
            raise AzureRestError(f"Could not fetch Azure attachment: {exc}") from exc
    if response.status_code in (401, 403):
        raise AzureRestError("Azure credentials cannot view this attachment.")
    if response.status_code >= 400:
        raise AzureRestError(f"Azure attachment API error ({response.status_code}).")
    mime_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    return response.content, mime_type


async def add_comment(connection: AzureConnection, work_item_id: int, text: str) -> None:
    if connection.read_only:
        raise AzureRestError(
            f"Azure connection {connection.name!r} is read-only; cannot post a comment."
        )
    response = await _post(
        connection,
        f"/wit/workitems/{int(work_item_id)}/comments",
        {"text": text},
        preview=True,
    )
    if response.status_code in (401, 403):
        raise AzureRestError("Azure DevOps rejected the configured credentials (comment).")
    if response.status_code >= 400:
        raise AzureRestError(f"Azure DevOps comment API error ({response.status_code}): {response.text[:300]}")