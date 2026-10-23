"""Fetch the ticket linked to a project and render it as markdown for
TICKET.md.

No LLM involved - the pipeline writes this file into the sandbox so Codex
can plan and implement against the real ticket text. The project's ticket
source determines the backend: Jira (agents/jira_agent) or Azure DevOps
(agents/azure_agent), whichever the project picked.
"""

from __future__ import annotations

from typing import Any

from agents.jira_agent.connections import get_connection as get_jira_connection
from agents.azure_agent.connections import get_connection as get_azure_connection
from agents.jira_agent.rest import JiraRestError, download_attachment, get_issue
from agents.azure_agent.rest import (
    AzureRestError,
    download_attachment as download_azure_attachment,
    get_work_item,
    get_work_item_comments,
    related_work_item_ids,
    work_item_to_markdown,
)

from .config import Settings


def adf_to_text(node: Any) -> str:
    """Convert Atlassian Document Format to Markdown (or preserve plain text)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(item) for item in node)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "hardBreak":
        return "\n"
    if node_type == "mention":
        return str(node.get("attrs", {}).get("text") or "")
    if node_type == "emoji":
        return str(node.get("attrs", {}).get("shortName") or "")
    if node_type == "inlineCard":
        return str(node.get("attrs", {}).get("url") or "")

    if node_type == "text":
        text = str(node.get("text") or "")
        for mark in node.get("marks") or []:
            mark_type = mark.get("type") if isinstance(mark, dict) else None
            attrs = mark.get("attrs") or {} if isinstance(mark, dict) else {}
            if mark_type == "strong":
                text = f"**{text}**"
            elif mark_type == "em":
                text = f"*{text}*"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "strike":
                text = f"~~{text}~~"
            elif mark_type == "link" and attrs.get("href"):
                text = f"[{text}]({attrs['href']})"
        return text

    inner = adf_to_text(node.get("content") or [])
    if node_type == "paragraph":
        return f"{inner.rstrip()}\n\n"
    if node_type == "heading":
        level = max(1, min(int((node.get("attrs") or {}).get("level", 1)), 6))
        return f"{'#' * level} {inner.strip()}\n\n"
    if node_type == "listItem":
        return inner.strip()
    if node_type in {"bulletList", "orderedList"}:
        items = [adf_to_text(item).strip() for item in node.get("content") or []]
        lines = []
        for index, item in enumerate(items, 1):
            prefix = "- " if node_type == "bulletList" else f"{index}. "
            lines.append(prefix + item.replace("\n", "\n  "))
        return "\n".join(lines) + "\n\n"
    if node_type == "blockquote":
        return "\n".join(f"> {line}" if line else ">" for line in inner.strip().splitlines()) + "\n\n"
    if node_type == "codeBlock":
        language = (node.get("attrs") or {}).get("language") or ""
        return f"```{language}\n{inner.rstrip()}\n```\n\n"
    if node_type == "rule":
        return "---\n\n"
    if node_type == "link":
        href = (node.get("attrs") or {}).get("href")
        return f"[{inner}]({href})" if href else inner
    return inner


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or "")
    return str(value or "")


def _comments_section(fields: dict[str, Any], heading: str) -> list[str]:
    comments = ((fields.get("comment") or {}).get("comments") or []) if isinstance(fields.get("comment"), dict) else []
    if not comments:
        return []
    lines = ["", heading, ""]
    for comment in comments:
        author = _name(comment.get("author")) or "Unknown"
        created = comment.get("created") or ""
        body = adf_to_text(comment.get("body")).strip() or "(empty)"
        lines.append(f"{'#' * (len(heading) - len(heading.lstrip('#')) + 1)} {author} ({created})")
        lines.append("")
        lines.append(body)
        lines.append("")
    return lines


def issue_to_markdown(
    issue: dict[str, Any],
    subtasks: list[dict[str, Any]] | None = None,
    epic: dict[str, Any] | None = None,
) -> str:
    """subtasks, if given, are full issue payloads (see fetch_ticket_markdown) -
    not the bare {key, summary, status} refs embedded in the parent issue's
    own `fields.subtasks` - so each subtask's own description/comments (e.g.
    "already implemented" or reviewer feedback left on the subtask, not the
    parent) actually reach the sandbox instead of being silently dropped.

    epic, if given, is the full parent Epic issue payload (not just the bare
    {key, summary} ref embedded in `fields.parent`) - so the epic's own
    description and comments, which carry the overall goal a single story is
    only a slice of, reach the sandbox too instead of being reduced to a
    one-line reference.
    """
    key = issue.get("key") or ""
    fields = issue.get("fields") or {}
    summary = fields.get("summary") or ""
    status = _name(fields.get("status"))
    issue_type = _name(fields.get("issuetype"))
    assignee = _name(fields.get("assignee")) or "Unassigned"
    priority = _name(fields.get("priority"))
    description = adf_to_text(fields.get("description")).strip()
    attachments = fields.get("attachment") or []

    lines = [
        f"# {key}: {summary}",
        "",
        f"- Type: {issue_type or '-'}",
        f"- Status: {status or '-'}",
        f"- Assignee: {assignee}",
        f"- Priority: {priority or '-'}",
        "",
        "## Description",
        "",
        description or "(no description)",
    ]

    if attachments:
        lines.extend(
            [
                "",
                "## Visual reference attachments (mandatory)",
                "",
                "For every image below, replicate the visual design exactly in the Salesforce UI using LWC. Treat the image as a high-priority, mandatory implementation requirement.",
                "",
            ]
        )
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            filename = attachment.get("filename") or "Unnamed attachment"
            url = attachment.get("content") or attachment.get("url") or ""
            lines.append(f"- [{filename}]({url})" if url else f"- {filename}")

    parent = fields.get("parent")
    if isinstance(parent, dict) and parent.get("key"):
        if epic is not None:
            epic_key = epic.get("key") or parent["key"]
            epic_fields = epic.get("fields") or {}
            epic_summary = epic_fields.get("summary") or ""
            epic_description = adf_to_text(epic_fields.get("description")).strip()
            lines.extend(
                [
                    "",
                    "## Epic",
                    "",
                    f"### {epic_key}: {epic_summary}".rstrip(": "),
                    "",
                    "This story is one slice of the epic below - use it for the overall goal "
                    "and constraints, but only implement the story above.",
                    "",
                    epic_description or "(no description)",
                ]
            )
            lines.extend(_comments_section(epic_fields, "### Epic comments"))
        else:
            parent_summary = (parent.get("fields") or {}).get("summary") or ""
            lines.extend(["", "## Parent", "", f"{parent['key']}: {parent_summary}".rstrip(": ")])

    lines.extend(_comments_section(fields, "## Comments"))

    if subtasks:
        lines.extend(["", "## Subtasks", ""])
        lines.append(
            "Check each subtask's status and comments below before doing its "
            "work - a subtask already Done, or with a comment saying the work "
            "is already implemented, should not be redone."
        )
        for subtask in subtasks:
            sub_key = subtask.get("key") or ""
            sub_fields = subtask.get("fields") or {}
            sub_summary = sub_fields.get("summary") or ""
            sub_status = _name(sub_fields.get("status")) or "-"
            sub_description = adf_to_text(sub_fields.get("description")).strip()

            lines.append("")
            lines.append(f"### {sub_key}: {sub_summary}")
            lines.append("")
            lines.append(f"- Status: {sub_status}")
            lines.append("")
            lines.append(sub_description or "(no description)")
            lines.extend(_comments_section(sub_fields, "#### Comments"))

    return "\n".join(lines).rstrip() + "\n"


async def _fetch_jira_image_attachments(settings: Settings, ticket_key: str) -> list[tuple[str, bytes, str]]:
    """Jira image attachments so Codex can inspect the actual pixels."""
    connection = get_jira_connection(settings.jira.connection_name)
    if connection is None:
        raise RuntimeError(f"No Jira connection named {settings.jira.connection_name!r}.")
    issue = await get_issue(connection, ticket_key)
    downloaded: list[tuple[str, bytes, str]] = []
    for attachment in (issue.get("fields") or {}).get("attachment") or []:
        if not isinstance(attachment, dict) or not str(attachment.get("mimeType", "")).startswith("image/"):
            continue
        content_url = attachment.get("content") or attachment.get("url")
        filename = attachment.get("filename")
        if not content_url or not filename:
            continue
        try:
            content, mime_type = await download_attachment(connection, content_url)
        except JiraRestError:
            continue
        downloaded.append((str(filename), content, mime_type))
    return downloaded


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")


async def _fetch_azure_image_attachments(settings: Settings, ticket_key: str) -> list[tuple[str, bytes, str]]:
    """Azure AttachedFile relations that look like images."""
    connection = get_azure_connection(settings.azure.connection_name)
    if connection is None:
        raise RuntimeError(f"No Azure connection named {settings.azure.connection_name!r}.")
    work_item = await get_work_item(connection, int(ticket_key))
    downloaded: list[tuple[str, bytes, str]] = []
    for relation in work_item.get("relations") or []:
        if not isinstance(relation, dict) or str(relation.get("rel") or "") != "AttachedFile":
            continue
        content_url = relation.get("url")
        attributes = relation.get("attributes") or {}
        filename = attributes.get("name") if isinstance(attributes, dict) else None
        if not content_url or not filename:
            continue
        try:
            content, mime_type = await download_azure_attachment(connection, content_url)
        except AzureRestError:
            continue
        lowered = str(filename).lower()
        if not (mime_type.startswith("image/") or lowered.endswith(_IMAGE_EXTENSIONS)):
            continue
        downloaded.append((str(filename), content, mime_type))
    return downloaded


async def fetch_ticket_image_attachments(settings: Settings, ticket_key: str) -> list[tuple[str, bytes, str]]:
    """Fetch image attachments so Codex can inspect the actual pixels."""
    if settings.azure is not None:
        return await _fetch_azure_image_attachments(settings, ticket_key)
    return await _fetch_jira_image_attachments(settings, ticket_key)


async def _fetch_jira_ticket_markdown(settings: Settings, ticket_key: str) -> str:
    connection = get_jira_connection(settings.jira.connection_name)
    if connection is None:
        raise RuntimeError(f"No Jira connection named {settings.jira.connection_name!r}.")
    issue = await get_issue(connection, ticket_key)

    issue_fields = issue.get("fields") or {}

    subtask_refs = issue_fields.get("subtasks") or []
    subtasks: list[dict[str, Any]] = []
    for ref in subtask_refs:
        sub_key = ref.get("key")
        if not sub_key:
            continue
        try:
            subtasks.append(await get_issue(connection, sub_key))
        except JiraRestError:
            # Permissions or a stale ref shouldn't sink the whole ticket fetch -
            # fall back to the bare {key, summary, status} ref already in hand.
            subtasks.append(ref)

    epic: dict[str, Any] | None = None
    parent_ref = issue_fields.get("parent")
    parent_key = parent_ref.get("key") if isinstance(parent_ref, dict) else None
    if parent_key:
        try:
            epic = await get_issue(connection, parent_key)
        except JiraRestError:
            # Same fallback logic as subtasks above - a missing/forbidden
            # parent shouldn't sink the fetch, just fall back to the bare ref.
            epic = None

    return issue_to_markdown(issue, subtasks=subtasks, epic=epic)


async def _fetch_azure_ticket_markdown(settings: Settings, ticket_key: str) -> str:
    connection = get_azure_connection(settings.azure.connection_name)
    if connection is None:
        raise RuntimeError(f"No Azure connection named {settings.azure.connection_name!r}.")
    try:
        work_item_id = int(ticket_key)
    except (TypeError, ValueError):
        raise RuntimeError(f"Azure DevOps work items are referenced by numeric id, got {ticket_key!r}.") from None

    work_item = await get_work_item(connection, work_item_id)
    comments = await get_work_item_comments(connection, work_item_id)

    parent: dict[str, Any] | None = None
    parent_comments: list[dict[str, Any]] | None = None
    parent_ids = related_work_item_ids(work_item, "Hierarchy-Reverse")
    if parent_ids:
        try:
            parent = await get_work_item(connection, parent_ids[0])
            parent_comments = await get_work_item_comments(connection, parent_ids[0])
        except AzureRestError:
            # A missing/forbidden parent shouldn't sink the ticket fetch.
            parent = None

    children: list[dict[str, Any]] = []
    for child_id in related_work_item_ids(work_item, "Hierarchy-Forward")[:20]:
        try:
            children.append(await get_work_item(connection, child_id))
        except AzureRestError:
            continue

    return work_item_to_markdown(
        work_item, parent=parent, children=children, comments=comments, parent_comments=parent_comments
    )


async def fetch_ticket_markdown(settings: Settings, ticket_key: str) -> str:
    """Render the linked ticket's full text (parent, children, comments) as
    markdown, from either the Jira or Azure connection the project linked."""
    if settings.azure is not None:
        return await _fetch_azure_ticket_markdown(settings, ticket_key)
    return await _fetch_jira_ticket_markdown(settings, ticket_key)