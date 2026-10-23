"""Versioned project-level memory injected into Codex as a skill.

This is the document Codex should read about *this* codebase (layout,
conventions, how tests run, Salesforce org notes, ...). It is not a
per-symbol search index - a row-per-file/symbol table cannot be injected
into ~/.codex/skills and would duplicate on every reindex without a
commit_sha + unique constraint.

Bytes live in SkillStorage under memories/<project_id>/<version>/MEMORY.md
(local disk today, S3 later). Project.active_memory_id points at the
snapshot to install when the sandbox opens.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class ProjectMemory(Base):
    __tablename__ = "project_memories"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    storage_backend: Mapped[str] = mapped_column(
        String(20),
        default="local",
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        String(500),
        unique=True,
        nullable=False,
    )

    checksum: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    byte_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Repo commit this snapshot describes, when known. Null for hand-written
    # memory that is not tied to a specific SHA.
    commit_sha: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="memories",
        foreign_keys=[project_id],
    )

    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_memory_version"),)
