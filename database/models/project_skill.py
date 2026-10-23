"""Which catalog skills a project injects into its Codex sandbox.

Replaces the old Project.skill_slugs JSON list, which had no foreign keys,
could not pin a version, and could not order installs. Null
pinned_version_id means "follow the skill's current active version".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class ProjectSkill(Base):
    __tablename__ = "project_skills"

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

    skill_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("skills.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Null = install whatever Skill.active_version_id points at when the
    # sandbox is created. Set this to freeze a project on a known package
    # so an unrelated catalog bump cannot change what a pipeline run gets.
    pinned_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("skill_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # "planning" | "implementation" | "both" - which sandbox phase installs
    # this skill (see agents/pipeline_agent/sandbox_runtime.py). "both" keeps
    # pre-existing rows installing everywhere, same as before this column
    # existed.
    phase: Mapped[str] = mapped_column(
        String(20),
        default="both",
        nullable=False,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="project_skills",
    )

    skill = relationship(
        "Skill",
        back_populates="project_links",
    )

    pinned_version = relationship(
        "SkillVersion",
        foreign_keys=[pinned_version_id],
    )

    __table_args__ = (UniqueConstraint("project_id", "skill_id", name="uq_project_skill"),)
