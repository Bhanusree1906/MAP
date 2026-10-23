import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base

MANUAL_TICKET_STATUSES = ("todo", "progress", "agentcompleted", "completed")


class ManualTicket(Base):
    """One manually ingested ticket inside a TicketGroup - the fields captured
    by the Manual Ticket Ingestion form (external key, title, type, priority,
    markdown spec) plus one optional attachment. ticket_id must be unique
    within its group so an imported batch can never reference the same id
    twice.
    """

    __tablename__ = "manual_tickets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    group_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_groups.id"),
        nullable=False,
        index=True,
    )

    # External Ticket Key / ID from the ingestion form (e.g. SEC-402). Unique
    # per group - each ticket in a group is identified by this id.
    ticket_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    issue_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    priority: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    assignee: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="todo",
    )

    # One optional uploaded attachment per manual ticket (the ingestion form
    # allows a single file of reference material to travel with the ticket).
    attachment_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    attachment_content_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    attachment_size: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    attachment_data: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    group = relationship(
        "TicketGroup",
        back_populates="tickets",
    )

    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "ticket_id",
            name="uq_manual_ticket_group_ticket",
        ),
    )