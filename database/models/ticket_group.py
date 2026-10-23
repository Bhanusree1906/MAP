import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class TicketGroup(Base):
    """A named collection of manually ingested tickets (the "Jira group" a
    project can import from). Grouped on the Manual Ticket Ingestion page so
    off-line work can be curated in batches before any project pulls them in.
    """

    __tablename__ = "ticket_groups"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
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

    tickets = relationship(
        "ManualTicket",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ManualTicket.created_at",
    )