import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="active",
        nullable=False,
    )

    repository_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    default_branch: Mapped[str] = mapped_column(
        String(100),
        default="main",
        nullable=False,
    )

    jira_connection_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("jira_connections.name"),
        nullable=True,
    )

    azure_connection_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("azure_connections.name"),
        nullable=True,
    )

    # The manual ticket group ("Jira group") this project pulls tickets from,
    # when its ticket source is a curated group instead of a Jira/Azure live
    # connection (see TicketGroup and the project import flow's "group" source).
    ticket_group_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("ticket_groups.name", ondelete="SET NULL"),
        nullable=True,
    )

    github_connection_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("github_connections.name"),
        nullable=True,
    )

    azure_git_connection_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("azure_connections.name"),
        nullable=True,
    )

    # "default" or "salesforce" - which sandbox image this project's pipeline
    # runs use (see agents/pipeline_agent/sandbox_kinds.py). Salesforce
    # projects also need salesforce_connection_name set for `sf` to be
    # authenticated inside that sandbox.
    sandbox_kind: Mapped[str] = mapped_column(
        String(50),
        default="default",
        nullable=False,
    )

    salesforce_connection_name: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("salesforce_connections.name"),
        nullable=True,
    )

    # Snapshot of project-level Codex memory to inject when a sandbox opens.
    # FK uses_alter to avoid a create cycle with project_memories.project_id.
    active_memory_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "project_memories.id",
            use_alter=True,
            name="fk_projects_active_memory_id",
            ondelete="SET NULL",
        ),
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

    pipeline_runs = relationship(
        "PipelineRun",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    artifacts = relationship(
        "Artifact",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    cost_logs = relationship(
        "CostLog",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    git_access_tokens = relationship(
        "GitAccessToken",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    project_skills = relationship(
        "ProjectSkill",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectSkill.sort_order",
    )

    memories = relationship(
        "ProjectMemory",
        back_populates="project",
        foreign_keys="ProjectMemory.project_id",
        cascade="all, delete-orphan",
    )

    active_memory = relationship(
        "ProjectMemory",
        foreign_keys=[active_memory_id],
        post_update=True,
    )

    imported_tickets = relationship(
        "ImportedTicket",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ImportedTicket.imported_at",
    )