import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    pipeline_run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.id"),
        nullable=False,
        index=True,
    )

    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    stage: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="queued",
        nullable=False,
    )

    input_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"),
        nullable=True,
    )

    output_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"),
        nullable=True,
    )

    attempt: Mapped[int] = mapped_column(
        default=1,
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )

    pipeline_run = relationship(
        "PipelineRun",
        back_populates="agent_runs",
    )