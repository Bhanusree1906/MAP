"""CRUD + rendering helpers for the Manual Ticket Ingestion feature.

Three tables (see database/models/): TicketGroup (a named batch of manual
tickets collected on the ingestion page), ManualTicket (one ingested ticket
inside a group), and ImportedTicket (a snapshot a project pulled in from one
of its sources - Jira, Azure, or a ticket group). Mirrors the exact SessionLocal
idiom of credentials.py (see agents/pipeline_agent/credentials.py) so callers
always receive detached objects safe to use outside the session.
"""

from __future__ import annotations

import uuid

from database.models.imported_ticket import ImportedTicket
from database.models.manual_ticket import MANUAL_TICKET_STATUSES, ManualTicket
from database.models.project import Project
from database.models.ticket_group import TicketGroup
from database.session import SessionLocal, init_db
from sqlalchemy.orm import selectinload

from agents.pipeline_agent.manual_work_items import sanitize_manual_html

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


# ---------------------------------------------------------------- Ticket groups


def list_ticket_groups() -> list[TicketGroup]:
    _ensure_schema()
    with SessionLocal() as db:
        groups = (
            db.query(TicketGroup)
            .options(selectinload(TicketGroup.tickets))
            .order_by(TicketGroup.name)
            .all()
        )
        db.expunge_all()
        return groups


def create_ticket_group(name: str, description: str | None = None) -> TicketGroup:
    _ensure_schema()
    with SessionLocal() as db:
        existing = db.query(TicketGroup).filter(TicketGroup.name == name).first()
        if existing is not None:
            raise ValueError(f"A ticket group named {name!r} already exists")
        group = TicketGroup(name=name, description=description or None)
        db.add(group)
        db.commit()
        db.refresh(group)
        db.expunge(group)
        return group


def get_ticket_group(group_id: str) -> TicketGroup | None:
    _ensure_schema()
    with SessionLocal() as db:
        group = (
            db.query(TicketGroup)
            .options(selectinload(TicketGroup.tickets))
            .filter(TicketGroup.id == group_id)
            .first()
        )
        if group is None:
            return None
        db.expunge_all()
        return group


def delete_ticket_group(group_id: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        group = db.query(TicketGroup).filter(TicketGroup.id == group_id).first()
        if group is None:
            return False
        db.delete(group)
        db.commit()
        return True


# ------------------------------------------------------------- Manual tickets


def create_manual_ticket(
    group_id: str,
    ticket_id: str,
    summary: str,
    description: str | None = None,
    issue_type: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    attachment_filename: str | None = None,
    attachment_content_type: str | None = None,
    attachment_size: int | None = None,
    attachment_data: bytes | None = None,
) -> ManualTicket | None:
    _ensure_schema()
    with SessionLocal() as db:
        group = db.query(TicketGroup).filter(TicketGroup.id == group_id).first()
        if group is None:
            return None
        duplicate = (
            db.query(ManualTicket)
            .filter(
                ManualTicket.group_id == group_id,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if duplicate is not None:
            raise ValueError(f"Ticket {ticket_id!r} already exists in this group")
        ticket = ManualTicket(
            group_id=group_id,
            ticket_id=ticket_id,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            attachment_filename=attachment_filename,
            attachment_content_type=attachment_content_type,
            attachment_size=attachment_size,
            attachment_data=attachment_data,
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        db.expunge(ticket)
        return ticket


def create_rich_manual_ticket(
    group_id: str, title: str, body_html: str
) -> ManualTicket | None:
    safe_html, _ = sanitize_manual_html(body_html)
    return create_manual_ticket(
        group_id=group_id,
        ticket_id=f"MT-{uuid.uuid4().hex[:8].upper()}",
        summary=title.strip(),
        description=safe_html,
    )


def update_manual_ticket_content(
    group_id: str, ticket_id: str, title: str, body_html: str
) -> ManualTicket | None:
    _ensure_schema()
    safe_html, _ = sanitize_manual_html(body_html)
    with SessionLocal() as db:
        ticket = (
            db.query(ManualTicket)
            .filter(
                ManualTicket.group_id == group_id,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if ticket is None:
            return None
        ticket.summary = title.strip()
        ticket.description = safe_html
        db.commit()
        db.refresh(ticket)
        db.expunge(ticket)
        return ticket


def update_manual_ticket_status(
    group_id: str, ticket_id: str, status: str
) -> ManualTicket | None:
    if status not in MANUAL_TICKET_STATUSES:
        raise ValueError(f"Invalid manual ticket status: {status!r}")
    _ensure_schema()
    with SessionLocal() as db:
        ticket = (
            db.query(ManualTicket)
            .filter(
                ManualTicket.group_id == group_id,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if ticket is None:
            return None
        ticket.status = status
        db.commit()
        db.refresh(ticket)
        db.expunge(ticket)
        return ticket


def update_manual_ticket_status_for_project(
    project_id: str, ticket_id: str, status: str
) -> ManualTicket | None:
    if status not in MANUAL_TICKET_STATUSES:
        raise ValueError(f"Invalid manual ticket status: {status!r}")
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None or not project.ticket_group_name:
            return None
        ticket = (
            db.query(ManualTicket)
            .join(TicketGroup, ManualTicket.group_id == TicketGroup.id)
            .filter(
                TicketGroup.name == project.ticket_group_name,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if ticket is None:
            return None
        ticket.status = status
        db.commit()
        db.refresh(ticket)
        db.expunge(ticket)
        return ticket


def list_manual_tickets(group_id: str) -> list[ManualTicket]:
    _ensure_schema()
    with SessionLocal() as db:
        tickets = (
            db.query(ManualTicket)
            .filter(ManualTicket.group_id == group_id)
            .order_by(ManualTicket.created_at)
            .all()
        )
        for ticket in tickets:
            db.expunge(ticket)
        return tickets


def get_manual_ticket(group_id: str, ticket_id: str) -> ManualTicket | None:
    _ensure_schema()
    with SessionLocal() as db:
        ticket = (
            db.query(ManualTicket)
            .filter(
                ManualTicket.group_id == group_id,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if ticket is None:
            return None
        db.expunge(ticket)
        return ticket


def delete_manual_ticket(group_id: str, ticket_id: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        ticket = (
            db.query(ManualTicket)
            .filter(
                ManualTicket.group_id == group_id,
                ManualTicket.ticket_id == ticket_id,
            )
            .first()
        )
        if ticket is None:
            return False
        db.delete(ticket)
        db.commit()
        return True


# ----------------------------------------------------------- Imported tickets


def list_imported_tickets(project_id: str) -> list[ImportedTicket]:
    _ensure_schema()
    with SessionLocal() as db:
        tickets = (
            db.query(ImportedTicket)
            .filter(ImportedTicket.project_id == project_id)
            .order_by(ImportedTicket.imported_at)
            .all()
        )
        for ticket in tickets:
            db.expunge(ticket)
        return tickets


def get_imported_ticket(
    project_id: str, key: str, source: str | None = None
) -> ImportedTicket | None:
    _ensure_schema()
    with SessionLocal() as db:
        query = db.query(ImportedTicket).filter(
            ImportedTicket.project_id == project_id,
            ImportedTicket.key == key,
        )
        if source is not None:
            query = query.filter(ImportedTicket.source == source)
        ticket = query.first()
        if ticket is None:
            return None
        db.expunge(ticket)
        return ticket


def add_imported_tickets(
    project_id: str,
    source: str,
    source_connection: str | None,
    snapshots: list[dict],
) -> tuple[list[ImportedTicket], list[str]]:
    """Persist imported-ticket snapshots, skipping keys already imported from
    the same source. Returns (imported rows, skipped keys)."""
    _ensure_schema()
    imported: list[ImportedTicket] = []
    skipped: list[str] = []
    with SessionLocal() as db:
        for snapshot in snapshots:
            key = snapshot["key"]
            existing = (
                db.query(ImportedTicket)
                .filter(
                    ImportedTicket.project_id == project_id,
                    ImportedTicket.source == source,
                    ImportedTicket.key == key,
                )
                .first()
            )
            if existing is not None:
                skipped.append(key)
                continue
            ticket = ImportedTicket(
                project_id=project_id,
                source=source,
                source_connection=source_connection,
                key=key,
                summary=snapshot.get("summary") or "",
                description=snapshot.get("description"),
                status=snapshot.get("status"),
                issue_type=snapshot.get("issue_type"),
                priority=snapshot.get("priority"),
                assignee=snapshot.get("assignee"),
                labels=",".join(snapshot.get("labels") or []),
                reporter=snapshot.get("reporter"),
                url=snapshot.get("url"),
                attachment_filename=snapshot.get("attachment_filename"),
                attachment_content_type=snapshot.get("attachment_content_type"),
                attachment_size=snapshot.get("attachment_size"),
                attachment_data=snapshot.get("attachment_data"),
            )
            db.add(ticket)
            imported.append(ticket)
        db.commit()
        for ticket in imported:
            db.refresh(ticket)
            db.expunge(ticket)
    return imported, skipped


# ------------------------------------------------- Local rendering (for runs)


def imported_ticket_to_markdown(ticket: ImportedTicket) -> str:
    """Render an imported ticket as the TICKET.md the sandbox would otherwise
    get from fetching the issue live - so a run on a manual (group) ticket
    receives the exact content the ingester captured, not a Jira 404."""
    lines = [
        f"# {ticket.key}: {ticket.summary}",
        "",
        f"- Type: {ticket.issue_type or '-'}",
        f"- Status: {ticket.status or '-'}",
        f"- Assignee: {ticket.assignee or 'Unassigned'}",
        f"- Priority: {ticket.priority or '-'}",
        "",
        "## Description",
        "",
        ticket.description or "(no description)",
    ]
    if ticket.attachment_filename:
        lines.extend(
            [
                "",
                "## Reference attachment",
                "",
                f"- {ticket.attachment_filename}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def manual_ticket_to_markdown(ticket: ManualTicket) -> str:
    lines = [
        f"# {ticket.ticket_id}: {ticket.summary}",
        "",
        f"- Type: {ticket.issue_type or '-'}",
        "- Status: -",
        f"- Assignee: {ticket.assignee or 'Unassigned'}",
        f"- Priority: {ticket.priority or '-'}",
        "",
        "## Description",
        "",
        ticket.description or "(no description)",
    ]
    if ticket.attachment_filename:
        lines.extend(["", "## Reference attachment", "", f"- {ticket.attachment_filename}"])
    return "\n".join(lines).rstrip() + "\n"


def imported_ticket_image_attachments(
    ticket: ImportedTicket,
) -> list[tuple[str, bytes, str]]:
    """The imported snapshot's stored attachment, as (filename, bytes, mime) -
    matching the shape fetch_ticket_image_attachments returns so the local
    planning path can hand it straight to the sandbox. Only captured for
    group imports."""
    if not ticket.attachment_data or not ticket.attachment_filename:
        return []
    mime = ticket.attachment_content_type or "application/octet-stream"
    return [(ticket.attachment_filename, ticket.attachment_data, mime)]


def manual_ticket_image_attachments(
    ticket: ManualTicket,
) -> list[tuple[str, bytes, str]]:
    if not ticket.attachment_data or not ticket.attachment_filename:
        return []
    mime = ticket.attachment_content_type or "application/octet-stream"
    return [(ticket.attachment_filename, ticket.attachment_data, mime)]