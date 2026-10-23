"""Lists tickets for a project's linked connection - Jira or Azure DevOps,
whichever the project linked.

Jira: calls the Jira Cloud REST API directly instead of going through the
MCP/OAuth flow the Jira tools use. That flow is a local-browser loopback
OAuth dance (see agents/jira_agent/oauth.py) meant for a CLI running on the
same machine as the browser doing the login - it doesn't work from a hosted
backend. This endpoint is a plain scoped read, so it talks to Jira's REST
API directly with no MCP involved. Auth (see agents/jira_agent/rest.py), in
priority order: browser OAuth 2.0 (3LO) from "Connect via Jira", then Email +
API token (Basic Auth), pasted in the UI.

Azure: talks to the Azure DevOps REST API directly with a PAT (see
agents/azure_agent/rest.py). Work items are referenced by their numeric id.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response

from agents.jira_agent.connections import JiraConnection, get_connection as get_jira_connection
from agents.jira_agent.rest import JiraRestError, get_issue, resolve_request_auth
from agents.azure_agent.connections import get_connection as get_azure_connection
from agents.azure_agent.rest import (
    AzureRestError,
    download_attachment as download_azure_attachment,
    get_work_item,
    html_to_text,
    related_work_item_ids,
)
from agents.pipeline_agent import ingestion
from agents.pipeline_agent.manual_work_items import sanitize_manual_html
from agents.pipeline_agent.credentials import get_project_by_id
from agents.pipeline_agent.ticket import adf_to_text
from backend.schemas import (
    FetchTicketsIn,
    ImportTicketsIn,
    ImportTicketsOut,
    ImportedTicketOut,
    TicketAttachmentOut,
    TicketOut,
    TicketSubtaskOut,
)

router = APIRouter(prefix="/api/projects", tags=["tickets"])

_DEFAULT_JQL = "ORDER BY updated DESC"
_FIELDS = [
    "summary",
    "status",
    "assignee",
    "description",
    "priority",
    "issuetype",
    "labels",
    "reporter",
    "created",
    "updated",
    "parent",
    "subtasks",
    "attachment",
]
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_DESCRIPTION_LIST_LIMIT = 1200


def _scope_jql(jql: str, project_key: str) -> str:
    """ANDs `project = "<KEY>"` into the query so this endpoint only ever
    returns tickets from the connection's configured project.
    """
    project_filter = f'project = "{project_key}"'
    jql = jql.strip()
    if not jql:
        return project_filter

    match = _ORDER_BY_RE.search(jql)
    condition, order_by = (jql[: match.start()].strip(), jql[match.start() :].strip()) if match else (jql, "")

    scoped = f"({condition}) AND {project_filter}" if condition else project_filter
    return f"{scoped} {order_by}" if order_by else scoped


def _field_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("name")
    return str(value) if value else None


def _ticket_url(connection: JiraConnection, key: str) -> str | None:
    if not connection.site_url:
        return None
    return f"{connection.site_url.rstrip('/')}/browse/{key}"


def _map_subtask(raw: Any, connection: JiraConnection) -> TicketSubtaskOut | None:
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not key:
        return None
    fields = raw.get("fields") or {}
    return TicketSubtaskOut(
        key=key,
        summary=fields.get("summary") or "",
        status=_field_name(fields.get("status")),
        url=_ticket_url(connection, key),
    )


def _map_attachment(raw: Any) -> TicketAttachmentOut | None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("filename"):
        return None
    return TicketAttachmentOut(
        id=str(raw["id"]),
        filename=str(raw["filename"]),
        url=raw.get("content") or raw.get("url"),
        mime_type=raw.get("mimeType"),
        size=raw.get("size") if isinstance(raw.get("size"), int) else None,
    )


def _map_issue(issue: dict, connection: JiraConnection, *, truncate_description: bool) -> TicketOut | None:
    if not isinstance(issue, dict):
        return None
    key = issue.get("key")
    if not key:
        return None
    fields = issue.get("fields") or {}
    description = adf_to_text(fields.get("description")).strip() or None
    if description and truncate_description and len(description) > _DESCRIPTION_LIST_LIMIT:
        description = description[: _DESCRIPTION_LIST_LIMIT - 1] + "…"
    labels = fields.get("labels") or []
    parent = fields.get("parent")
    parent_key = parent.get("key") if isinstance(parent, dict) else None
    subtasks = [st for st in (_map_subtask(raw, connection) for raw in fields.get("subtasks") or []) if st]
    attachments = [item for item in (_map_attachment(raw) for raw in fields.get("attachment") or []) if item]
    return TicketOut(
        key=key,
        summary=fields.get("summary") or "",
        status=_field_name(fields.get("status")),
        assignee=_field_name(fields.get("assignee")),
        description=description,
        priority=_field_name(fields.get("priority")),
        issue_type=_field_name(fields.get("issuetype")),
        labels=[str(label) for label in labels] if isinstance(labels, list) else [],
        reporter=_field_name(fields.get("reporter")),
        created=fields.get("created"),
        updated=fields.get("updated"),
        url=_ticket_url(connection, key),
        parent_key=parent_key,
        subtasks=subtasks,
        attachments=attachments,
    )


def _http_from_jira(exc: JiraRestError) -> HTTPException:
    msg = str(exc)
    lowered = msg.lower()
    if "no credentials" in lowered:
        return HTTPException(status_code=400, detail=msg)
    if "was not found" in lowered:
        return HTTPException(status_code=404, detail=msg)
    return HTTPException(status_code=502, detail=msg)


def _http_from_azure(exc: AzureRestError) -> HTTPException:
    msg = str(exc)
    lowered = msg.lower()
    if "no pat" in lowered:
        return HTTPException(status_code=400, detail=msg)
    if "was not found" in lowered:
        return HTTPException(status_code=404, detail=msg)
    return HTTPException(status_code=502, detail=msg)


def _work_item_field(work_item: dict[str, Any], *names: str) -> Any:
    fields = work_item.get("fields") or {}
    for name in names:
        if fields.get(name) is not None:
            return fields[name]
    return None


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("uniqueName") or "")
    return str(value) if value else None


def _azure_ticket_url(connection: Any, work_item_id: int) -> str:
    org = connection.organization_url.rstrip("/")
    return f"{org}/{connection.project_name}/_workitems/edit/{work_item_id}"


def _azure_subtask(connection: Any, child_id: int) -> TicketSubtaskOut:
    return TicketSubtaskOut(
        key=str(child_id),
        summary="",
        status=None,
        url=_azure_ticket_url(connection, child_id),
    )


def _azure_attachment_id(url: str) -> str:
    """Ids the ticket browser uses for attachment download are the trailing
    GUID of the _apis/wit/attachments content URL - unique per attachment."""
    match = re.search(r"/attachments/([^/?#]+)", url or "")
    return match.group(1) if match else url


def _azure_attachment(raw: Any) -> TicketAttachmentOut | None:
    if not isinstance(raw, dict) or str(raw.get("rel") or "") != "AttachedFile":
        return None
    attributes = raw.get("attributes") or {}
    name = attributes.get("name") if isinstance(attributes, dict) else None
    url = raw.get("url")
    if not name or not url:
        return None
    return TicketAttachmentOut(
        id=_azure_attachment_id(url),
        filename=str(name),
        url=url,
        mime_type=None,
        size=None,
    )


def _map_work_item(work_item: dict, connection: Any, *, truncate_description: bool) -> TicketOut | None:
    if not isinstance(work_item, dict):
        return None
    item_id = _work_item_field(work_item, "System.Id")
    if item_id is None:
        return None
    key = str(item_id)
    description = html_to_text(_work_item_field(work_item, "System.Description")).strip() or None
    if description and truncate_description and len(description) > _DESCRIPTION_LIST_LIMIT:
        description = description[: _DESCRIPTION_LIST_LIMIT - 1] + "…"
    tags = _work_item_field(work_item, "System.Tags") or ""
    parent_ids = related_work_item_ids(work_item, "Hierarchy-Reverse")
    child_ids = related_work_item_ids(work_item, "Hierarchy-Forward")
    return TicketOut(
        key=key,
        summary=_work_item_field(work_item, "System.Title") or "",
        status=_name(_work_item_field(work_item, "System.State")),
        assignee=_name(_work_item_field(work_item, "System.AssignedTo")),
        description=description,
        priority=_name(_work_item_field(work_item, "Microsoft.VSTS.Common.Priority", "System.Priority")),
        issue_type=_name(_work_item_field(work_item, "System.WorkItemType")),
        labels=[label.strip() for label in str(tags).split(";") if label.strip()],
        reporter=_name(_work_item_field(work_item, "System.CreatedBy")),
        created=_work_item_field(work_item, "System.CreatedDate"),
        updated=_work_item_field(work_item, "System.ChangedDate"),
        url=_azure_ticket_url(connection, item_id),
        parent_key=str(parent_ids[0]) if parent_ids else None,
        subtasks=[_azure_subtask(connection, child_id) for child_id in child_ids],
        attachments=[item for item in (_azure_attachment(raw) for raw in work_item.get("relations") or []) if item],
    )


def _load_project_connection(project_id: str) -> tuple[Any, Any, str]:
    """Returns (project, connection, source) where source is "jira" or
    "azure" - whichever ticket source the project linked."""
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.azure_connection_name and not project.jira_connection_name:
        connection = get_azure_connection(project.azure_connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="Azure connection not found")
        return project, connection, "azure"
    if project.jira_connection_name:
        connection = get_jira_connection(project.jira_connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="Jira connection not found")
        return project, connection, "jira"
    raise HTTPException(
        status_code=400,
        detail="This project has no ticket connection. Link a Jira or Azure connection on the project, "
        "then authorize it under Connections.",
    )


async def _fetch_jira_tickets(connection: JiraConnection, jql: str | None) -> list[TicketOut]:
    try:
        base_url, request_kwargs = await resolve_request_auth(connection)
    except JiraRestError as exc:
        raise _http_from_jira(exc) from None

    scoped_jql = _scope_jql(jql or _DEFAULT_JQL, connection.project_key)
    url = f"{base_url}/rest/api/3/search/jql"
    tickets = []
    next_page_token: str | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            params = {"jql": scoped_jql, "fields": ",".join(_FIELDS), "maxResults": 50}
            if next_page_token:
                params["nextPageToken"] = next_page_token
            try:
                response = await client.get(url, params=params, **request_kwargs)
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Could not reach Jira: {exc}") from None

            if response.status_code == 401:
                raise HTTPException(
                    status_code=502,
                    detail="Jira rejected the configured credentials. Reconnect under Connections.",
                )
            if response.status_code == 403:
                raise HTTPException(
                    status_code=502,
                    detail="Jira credentials do not have access to this project.",
                )
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Jira API error ({response.status_code}): {response.text}")

            payload = response.json() if response.content else {}
            issues = payload.get("issues") if isinstance(payload, dict) else None
            for issue in issues or []:
                mapped = _map_issue(issue, connection, truncate_description=True)
                if mapped:
                    tickets.append(mapped)

            next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
            if not next_page_token or payload.get("isLast", False):
                break
    return tickets


async def _fetch_azure_tickets(connection: Any) -> list[TicketOut]:
    from agents.azure_agent.rest import list_work_items

    try:
        work_items = await list_work_items(connection, top=100)
    except AzureRestError as exc:
        raise _http_from_azure(exc) from None
    return [item for item in (_map_work_item(wi, connection, truncate_description=True) for wi in work_items) if item]


def _manual_ticket_to_ticket(manual_ticket: Any) -> TicketOut | None:
    """A manual ticket rendered as a TicketOut so the import flow can present
    group tickets alongside Jira/Azure rows."""
    ticket_id = getattr(manual_ticket, "ticket_id", None)
    if not ticket_id:
        return None
    attachments: list[TicketAttachmentOut] = []
    filename = getattr(manual_ticket, "attachment_filename", None)
    if filename:
        size = getattr(manual_ticket, "attachment_size", None)
        attachments.append(
            TicketAttachmentOut(id="attachment", filename=filename, size=size)
        )
    description, _ = sanitize_manual_html(manual_ticket.description or "")
    return TicketOut(
        key=ticket_id,
        summary=manual_ticket.summary or "",
        status=manual_ticket.status,
        assignee=manual_ticket.assignee,
        description=description,
        priority=manual_ticket.priority,
        issue_type=manual_ticket.issue_type,
        labels=[],
        reporter=None,
        created=None,
        updated=None,
        url=None,
        parent_key=None,
        subtasks=[],
        attachments=attachments,
    )


def _issue_snapshot(mapped: TicketOut, *, attachment: dict | None = None) -> dict:
    """Ticket metadata captured at import time so a later run can use the
    stored copy (see agents/pipeline_agent/ingestion.py)."""
    snapshot = {
        "key": mapped.key,
        "summary": mapped.summary,
        "description": mapped.description,
        "status": mapped.status,
        "issue_type": mapped.issue_type,
        "priority": mapped.priority,
        "assignee": mapped.assignee,
        "labels": mapped.labels,
        "reporter": mapped.reporter,
        "url": mapped.url,
    }
    if attachment:
        snapshot.update(attachment)
    return snapshot


def _manual_snapshot(manual_ticket: Any) -> dict:
    description, _ = sanitize_manual_html(manual_ticket.description or "")
    return {
        "key": manual_ticket.ticket_id,
        "summary": manual_ticket.summary or "",
        "description": description,
        "status": manual_ticket.status,
        "issue_type": manual_ticket.issue_type,
        "priority": manual_ticket.priority,
        "assignee": manual_ticket.assignee,
        "labels": [],
        "reporter": None,
        "url": None,
        "attachment_filename": manual_ticket.attachment_filename,
        "attachment_content_type": manual_ticket.attachment_content_type,
        "attachment_size": manual_ticket.attachment_size,
        "attachment_data": manual_ticket.attachment_data,
    }


def _imported_out(ticket) -> "ImportedTicketOut":
    from backend.schemas import ImportedTicketOut

    labels = [label for label in (ticket.labels or "").split(",") if label.strip()]
    return ImportedTicketOut(
        id=ticket.id,
        project_id=ticket.project_id,
        source=ticket.source,
        source_connection=ticket.source_connection,
        key=ticket.key,
        summary=ticket.summary,
        description=ticket.description,
        status=ticket.status,
        issue_type=ticket.issue_type,
        priority=ticket.priority,
        assignee=ticket.assignee,
        labels=labels,
        reporter=ticket.reporter,
        url=ticket.url,
        attachment_filename=ticket.attachment_filename,
        attachment_size=ticket.attachment_size,
        imported_at=ticket.imported_at,
    )


def _imported_ticket_to_ticket(ticket) -> TicketOut | None:
    """Convert an ImportedTicket to TicketOut for the ticket picker UI."""
    if not ticket:
        return None
    labels = [label for label in (ticket.labels or "").split(",") if label.strip()]
    attachments: list[TicketAttachmentOut] = []
    if ticket.attachment_filename:
        attachments.append(
            TicketAttachmentOut(
                id="attachment",
                filename=ticket.attachment_filename,
                url=f"/api/projects/{ticket.project_id}/tickets/{ticket.key}/attachments/attachment",
                mime_type=ticket.attachment_content_type,
                size=ticket.attachment_size,
            )
        )
    description = ticket.description
    if ticket.source == "group":
        description, _ = sanitize_manual_html(description or "")
    return TicketOut(
        key=ticket.key,
        summary=ticket.summary or "",
        status=ticket.status,
        assignee=ticket.assignee,
        description=description,
        priority=ticket.priority,
        issue_type=ticket.issue_type,
        labels=labels,
        reporter=ticket.reporter,
        created=None,
        updated=ticket.imported_at.isoformat() if ticket.imported_at else None,
        url=ticket.url,
        parent_key=None,
        subtasks=[],
        attachments=attachments,
    )


@router.get("/{project_id}/tickets", response_model=list[TicketOut])
async def list_tickets(project_id: str, jql: str | None = None) -> list[TicketOut]:
    """List tickets for a project. Returns live tickets from the linked
    Jira/Azure connection, or imported tickets if no live connection exists."""
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Try to get tickets from a live connection
    if project.jira_connection_name or project.azure_connection_name:
        try:
            _, connection, source = _load_project_connection(project_id)
            if source == "azure":
                return await _fetch_azure_tickets(connection)
            return await _fetch_jira_tickets(connection, jql)
        except HTTPException:
            # If live connection fails, fall back to imported tickets
            pass

    # Fall back to the project's linked ticket group's manual tickets plus any
    # imported tickets (from groups or previous imports), deduped by key.
    tickets: list[TicketOut] = []
    seen: set[str] = set()
    if project.ticket_group_name:
        for group in ingestion.list_ticket_groups():
            if group.name == project.ticket_group_name:
                for ticket in group.tickets:
                    mapped = _manual_ticket_to_ticket(ticket)
                    if mapped and mapped.key not in seen:
                        tickets.append(mapped)
                        seen.add(mapped.key)
                break
    for ticket in ingestion.list_imported_tickets(project_id):
        mapped = _imported_ticket_to_ticket(ticket)
        if mapped and mapped.key not in seen:
            tickets.append(mapped)
            seen.add(mapped.key)
    return tickets


@router.get("/{project_id}/tickets/imported", response_model=list[ImportedTicketOut])
def list_imported_tickets(project_id: str) -> list[ImportedTicketOut]:
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [_imported_out(t) for t in ingestion.list_imported_tickets(project_id)]


@router.get("/{project_id}/tickets/{ticket_key}", response_model=TicketOut)
async def get_ticket(project_id: str, ticket_key: str) -> TicketOut:
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # For group-sourced (or any imported) tickets, return the stored snapshot
    # so the detail view works without a live connection.
    imported = ingestion.get_imported_ticket(project_id, ticket_key)
    if imported is not None:
        mapped = _imported_ticket_to_ticket(imported)
        if mapped is not None:
            return mapped

    # Otherwise, a manual ticket in the project's linked group can be served
    # straight from the group (no live connection needed either).
    if project.ticket_group_name:
        for group in ingestion.list_ticket_groups():
            if group.name == project.ticket_group_name:
                manual = ingestion.get_manual_ticket(group.id, ticket_key)
                if manual is not None:
                    mapped = _manual_ticket_to_ticket(manual)
                    if mapped is not None:
                        return mapped
                break

    _, connection, source = _load_project_connection(project_id)
    if source == "azure":
        try:
            work_item = await get_work_item(connection, int(ticket_key))
        except AzureRestError as exc:
            raise _http_from_azure(exc) from None
        mapped = _map_work_item(work_item, connection, truncate_description=False)
        if mapped is None:
            raise HTTPException(status_code=502, detail=f"Azure returned an unexpected payload for {ticket_key}")
        return mapped

    try:
        issue = await get_issue(connection, ticket_key)
    except JiraRestError as exc:
        raise _http_from_jira(exc) from None
    mapped = _map_issue(issue, connection, truncate_description=False)
    if mapped is None:
        raise HTTPException(status_code=502, detail=f"Jira returned an unexpected payload for {ticket_key}")
    return mapped


@router.get("/{project_id}/tickets/{ticket_key}/attachments/{attachment_id}")
async def get_ticket_attachment(project_id: str, ticket_key: str, attachment_id: str) -> Response:
    """Fetch an attachment with the project's configured credentials.

    The browser cannot use the backend's OAuth bearer token when opening
    Jira's attachment URL directly, so this endpoint proxies the binary.
    Azure attachments are proxied the same way (PAT never reaches the UI).
    Imported (group) tickets store their attachment bytes locally - serve
    them straight from the snapshot so no live connection is needed.
    """
    imported = ingestion.get_imported_ticket(project_id, ticket_key)
    if imported is not None and imported.attachment_data and imported.attachment_filename:
        headers = {"Content-Disposition": f'inline; filename="{imported.attachment_filename}"'}
        media_type = imported.attachment_content_type or "application/octet-stream"
        return Response(content=imported.attachment_data, media_type=media_type, headers=headers)

    _, connection, source = _load_project_connection(project_id)
    if source == "azure":
        try:
            work_item = await get_work_item(connection, int(ticket_key))
            content_url = None
            filename = attachment_id
            for relation in work_item.get("relations") or []:
                if not isinstance(relation, dict) or str(relation.get("rel") or "") != "AttachedFile":
                    continue
                attrs = relation.get("attributes") or {}
                name = attrs.get("name") if isinstance(attrs, dict) else None
                if _azure_attachment_id(relation.get("url") or "") == attachment_id:
                    content_url = relation.get("url")
                    if name:
                        filename = str(name)
                    break
            if not content_url:
                raise HTTPException(status_code=404, detail="Attachment not found on this work item")
            content, mime_type = await download_azure_attachment(connection, content_url)
        except AzureRestError as exc:
            raise _http_from_azure(exc) from None
        headers = {"Content-Disposition": f'inline; filename="{filename}"'}
        return Response(content=content, media_type=mime_type, headers=headers)

    try:
        issue = await get_issue(connection, ticket_key)
        attachments = (issue.get("fields") or {}).get("attachment") or []
        attachment = next(
            (item for item in attachments if isinstance(item, dict) and str(item.get("id")) == attachment_id),
            None,
        )
        if attachment is None:
            raise HTTPException(status_code=404, detail="Attachment not found on this ticket")
        content_url = attachment.get("content") or attachment.get("url")
        if not content_url:
            raise HTTPException(status_code=502, detail="Jira returned no attachment URL")
        base_url, request_kwargs = await resolve_request_auth(connection)
    except JiraRestError as exc:
        raise _http_from_jira(exc) from None

    # Jira Cloud's API gateway URL is the authenticated URL for OAuth. For
    # API-token connections, the issue response URL is already the right host.
    if content_url.startswith("/rest/"):
        content_url = f"{base_url}{content_url}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            response = await client.get(content_url, params={"redirect": "false"}, **request_kwargs)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not fetch Jira attachment: {exc}") from None

    if response.status_code == 401:
        raise HTTPException(status_code=502, detail="Jira rejected the configured credentials for this attachment")
    if response.status_code == 403:
        raise HTTPException(status_code=502, detail="Jira credentials cannot view this attachment")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Jira attachment API error ({response.status_code})")

    media_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    headers = {"Content-Disposition": f'inline; filename="{attachment.get("filename", attachment_id)}"'}
    return Response(content=response.content, media_type=media_type, headers=headers)


@router.post("/{project_id}/tickets/fetch", response_model=list[TicketOut])
async def fetch_source_tickets(project_id: str, payload: FetchTicketsIn) -> list[TicketOut]:
    """Pull candidate tickets for the import flow from one source: the
    project's own Jira/Azure connection, or a manual ticket group."""
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.source == "group":
        if not payload.group_id:
            raise HTTPException(status_code=400, detail="A ticket group must be selected first")
        group = ingestion.get_ticket_group(payload.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Ticket group not found")
        tickets = [_manual_ticket_to_ticket(t) for t in group.tickets]
        return [t for t in tickets if t]

    if payload.source == "jira":
        if not project.jira_connection_name:
            raise HTTPException(
                status_code=400,
                detail="This project has no Jira connection linked - it cannot pull tickets from Jira.",
            )
        connection = get_jira_connection(project.jira_connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="Jira connection not found")
        return await _fetch_jira_tickets(connection, payload.jql)

    # source == "azure"
    if not project.azure_connection_name:
        raise HTTPException(
            status_code=400,
            detail="This project has no Azure DevOps connection linked - it cannot pull tickets from Azure.",
        )
    connection = get_azure_connection(project.azure_connection_name)
    if connection is None:
        raise HTTPException(status_code=404, detail="Azure DevOps connection not found")
    return await _fetch_azure_tickets(connection)


@router.post("/{project_id}/tickets/import", response_model=ImportTicketsOut)
async def import_tickets(project_id: str, payload: ImportTicketsIn) -> ImportTicketsOut:
    """Import the selected ticket keys from one source into the project,
    preserving which source (connection/group) each ticket came from."""
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    keys = [key.strip() for key in payload.keys if key and key.strip()]
    if not keys:
        raise HTTPException(status_code=400, detail="Select at least one ticket to import")

    snapshots: list[dict] = []
    source_connection: str | None = None

    if payload.source == "group":
        if not payload.group_id:
            raise HTTPException(status_code=400, detail="A ticket group must be selected first")
        group = ingestion.get_ticket_group(payload.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Ticket group not found")
        source_connection = group.name
        for key in keys:
            ticket = ingestion.get_manual_ticket(group.id, key)
            if ticket is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Ticket {key!r} is not in group {group.name!r}",
                )
            snapshots.append(_manual_snapshot(ticket))
    elif payload.source == "jira":
        if not project.jira_connection_name:
            raise HTTPException(
                status_code=400,
                detail="This project has no Jira connection linked - it cannot import from Jira.",
            )
        connection = get_jira_connection(project.jira_connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="Jira connection not found")
        source_connection = project.jira_connection_name
        for key in keys:
            try:
                issue = await get_issue(connection, key)
            except JiraRestError as exc:
                raise _http_from_jira(exc) from None
            mapped = _map_issue(issue, connection, truncate_description=False)
            if mapped is None:
                raise HTTPException(status_code=502, detail=f"Jira returned an unexpected payload for {key}")
            snapshots.append(_issue_snapshot(mapped))
    else:  # source == "azure"
        if not project.azure_connection_name:
            raise HTTPException(
                status_code=400,
                detail="This project has no Azure DevOps connection linked - it cannot import from Azure.",
            )
        connection = get_azure_connection(project.azure_connection_name)
        if connection is None:
            raise HTTPException(status_code=404, detail="Azure DevOps connection not found")
        source_connection = project.azure_connection_name
        for key in keys:
            try:
                work_item_id = int(key)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Azure DevOps work items are referenced by numeric id, got {key!r}",
                ) from None
            try:
                work_item = await get_work_item(connection, work_item_id)
            except AzureRestError as exc:
                raise _http_from_azure(exc) from None
            mapped = _map_work_item(work_item, connection, truncate_description=False)
            if mapped is None:
                raise HTTPException(status_code=502, detail=f"Azure returned an unexpected payload for {key}")
            snapshots.append(_issue_snapshot(mapped))

    imported, skipped = ingestion.add_imported_tickets(
        project.id, payload.source, source_connection, snapshots
    )
    return ImportTicketsOut(
        imported=[_imported_out(t) for t in imported],
        skipped=skipped,
    )