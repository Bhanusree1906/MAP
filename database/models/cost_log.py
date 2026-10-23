import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class CostLog(Base):
    __tablename__ = "cost_logs"

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

    pipeline_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_runs.id"),
        nullable=True,
        index=True,
    )

    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    model: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    operation: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    input_tokens: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    output_tokens: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    total_tokens: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    input_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=0,
        nullable=False,
    )

    output_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=0,
        nullable=False,
    )

    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=0,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        default="USD",
        nullable=False,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="success",
        nullable=False,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="cost_logs",
    )

    pipeline_run = relationship(
        "PipelineRun",
        back_populates="cost_logs",
    )