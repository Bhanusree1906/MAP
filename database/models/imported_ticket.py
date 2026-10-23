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


class ImportedTicket(Base):
    """A ticket snapshot a project imported from one of its sources - the
    project's Jira connection, its Azure DevOps connection, or a manual ticket
    group. Source metadata (source + source_connection) is preserved so the
    UI can show where each ticket came from, and the content is snapshotted
    at import time so a run can use the stored copy instead of re-fetching
    (explicitly for group-sourced tickets, whose ids are not real Jira keys).
    """

    __tablename__ = "project_imported_tickets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    # "jira" | "azure" | "group" - which kind of source the ticket came from.
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # The connection/group the ticket was imported from (display metadata:
    # Jira/Azure connection name, or the manual ticket group's name).
    source_connection: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # The ticket's key as the browser/run refers to it: a Jira issue key, an
    # Azure work item id, or a manual ticket's ticket_id.
    key: Mapped[str] = mapped_column(
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

    status: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    issue_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    priority: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    assignee: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Comma-joined; split back into a list in the schema layer.
    labels: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reporter: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Only populated for group imports - the manual ticket's attachment bytes,
    # so a local (no-live-fetch) run can still hand the file to the sandbox.
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

    imported_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="imported_tickets",
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source",
            "key",
            name="uq_imported_ticket_project_source_key",
        ),
    )