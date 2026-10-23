import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

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

    run_type: Mapped[str] = mapped_column(
        String(50),
        default="full",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="queued",
        nullable=False,
        index=True,
    )

    current_stage: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    triggered_by: Mapped[str] = mapped_column(
        String(50),
        default="user",
        nullable=False,
    )

    input_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"),
        nullable=True,
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="pipeline_runs",
    )

    agent_runs = relationship(
        "AgentRun",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )

    artifacts = relationship(
        "Artifact",
        back_populates="pipeline_run",
        foreign_keys="Artifact.pipeline_run_id",
    )

    cost_logs = relationship(
        "CostLog",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
    )