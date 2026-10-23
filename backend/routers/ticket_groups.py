"""Manual Ticket Ingestion - ticket groups and their manual tickets.

Groups are the named batches of manually ingested tickets collected on the
Manual Ticket Ingestion page (the source a project's import flow labels "Jira
Group"). Endpoints here are the standalone CRUD part of that page plus the
opt-in ~25MB single attachment a ticket can carry.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from agents.pipeline_agent import ingestion
from backend.schemas import (
    ManualTicketOut,
    ManualTicketStatusIn,
    RichManualTicketCreate,
    RichManualTicketUpdate,
    TicketGroupIn,
    TicketGroupOut,
)

router = APIRouter(prefix="/api/ticket-groups", tags=["ticket-groups"])

# Mirrors the ingestion form's stated cap ("Max 25MB each").
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _manual_ticket_out(ticket) -> ManualTicketOut:
    attachment_url = None
    if ticket.attachment_filename:
        attachment_url = (
            f"/api/ticket-groups/{ticket.group_id}"
            f"/tickets/{ticket.ticket_id}/attachment"
        )
    return ManualTicketOut(
        id=ticket.id,
        group_id=ticket.group_id,
        ticket_id=ticket.ticket_id,
        summary=ticket.summary,
        description=ticket.description,
        issue_type=ticket.issue_type,
        priority=ticket.priority,
        assignee=ticket.assignee,
        status=ticket.status,
        has_attachment=ticket.attachment_filename is not None,
        attachment_filename=ticket.attachment_filename,
        attachment_size=ticket.attachment_size,
        attachment_url=attachment_url,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def _group_out(group) -> TicketGroupOut:
    return TicketGroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        ticket_count=len(group.tickets),
        created_at=group.created_at,
        updated_at=group.updated_at,
        tickets=[_manual_ticket_out(t) for t in group.tickets],
    )


@router.get("", response_model=list[TicketGroupOut])
def list_groups() -> list[TicketGroupOut]:
    return [_group_out(group) for group in ingestion.list_ticket_groups()]


@router.post("", response_model=TicketGroupOut)
def create_group(payload: TicketGroupIn) -> TicketGroupOut:
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")
    try:
        group = ingestion.create_ticket_group(name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    group = ingestion.get_ticket_group(group.id)
    return _group_out(group)


@router.get("/{group_id}", response_model=TicketGroupOut)
def get_group(group_id: str) -> TicketGroupOut:
    group = ingestion.get_ticket_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Ticket group not found")
    return _group_out(group)


@router.delete("/{group_id}")
def delete_group(group_id: str) -> dict:
    if not ingestion.delete_ticket_group(group_id):
        raise HTTPException(status_code=404, detail="Ticket group not found")
    return {"status": "ok"}


@router.get("/{group_id}/tickets", response_model=list[ManualTicketOut])
def list_group_tickets(group_id: str) -> list[ManualTicketOut]:
    if ingestion.get_ticket_group(group_id) is None:
        raise HTTPException(status_code=404, detail="Ticket group not found")
    return [_manual_ticket_out(t) for t in ingestion.list_manual_tickets(group_id)]


@router.post("/{group_id}/tickets", response_model=ManualTicketOut)
async def create_manual_ticket(
    group_id: str,
    ticket_id: str | None = Form(None),
    summary: str | None = Form(None),
    description: str | None = Form(None),
    issue_type: str | None = Form(None),
    priority: str | None = Form(None),
    assignee: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> ManualTicketOut:
    ticket_id = (ticket_id or "").strip()
    summary = (summary or "").strip()
    if not ticket_id:
        raise HTTPException(status_code=400, detail="Ticket ID is required")
    if not summary:
        raise HTTPException(status_code=400, detail="Ticket summary is required")

    attachment_data: bytes | None = None
    attachment_filename: str | None = None
    attachment_content_type: str | None = None
    if file is not None and file.filename:
        attachment_data = await file.read()
        if len(attachment_data) > _MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment exceeds the {_MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB limit",
            )
        attachment_filename = file.filename
        attachment_content_type = file.content_type

    try:
        ticket = ingestion.create_manual_ticket(
            group_id=group_id,
            ticket_id=ticket_id,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            attachment_filename=attachment_filename,
            attachment_content_type=attachment_content_type,
            attachment_size=len(attachment_data) if attachment_data else None,
            attachment_data=attachment_data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket group not found")
    return _manual_ticket_out(ticket)


@router.get("/{group_id}/tickets/{ticket_id}/attachment")
def get_manual_ticket_attachment(group_id: str, ticket_id: str):
    ticket = ingestion.get_manual_ticket(group_id, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    if not ticket.attachment_filename or not ticket.attachment_data:
        raise HTTPException(status_code=404, detail="This manual ticket has no attachment")
    from starlette.responses import Response

    headers = {"Content-Disposition": f'inline; filename="{ticket.attachment_filename}"'}
    media_type = ticket.attachment_content_type or "application/octet-stream"
    return Response(content=ticket.attachment_data, media_type=media_type, headers=headers)


@router.delete("/{group_id}/tickets/{ticket_id}")
def delete_manual_ticket(group_id: str, ticket_id: str) -> dict:
    if not ingestion.delete_manual_ticket(group_id, ticket_id):
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return {"status": "ok"}


@router.patch("/{group_id}/tickets/{ticket_id}/status", response_model=ManualTicketOut)
def update_manual_ticket_status(
    group_id: str, ticket_id: str, payload: ManualTicketStatusIn
) -> ManualTicketOut:
    try:
        ticket = ingestion.update_manual_ticket_status(
            group_id, ticket_id, payload.status
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if ticket is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return _manual_ticket_out(ticket)


@router.post("/{group_id}/rich-tickets", response_model=ManualTicketOut, status_code=201)
def create_rich_manual_ticket(
    group_id: str, payload: RichManualTicketCreate
) -> ManualTicketOut:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Ticket title is required")
    try:
        ticket = ingestion.create_rich_manual_ticket(group_id, title, payload.body_html)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket group not found")
    return _manual_ticket_out(ticket)


@router.patch("/{group_id}/tickets/{ticket_id}/content", response_model=ManualTicketOut)
def update_rich_manual_ticket(
    group_id: str, ticket_id: str, payload: RichManualTicketUpdate
) -> ManualTicketOut:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Ticket title is required")
    ticket = ingestion.update_manual_ticket_content(
        group_id, ticket_id, title, payload.body_html
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Manual ticket not found")
    return _manual_ticket_out(ticket)