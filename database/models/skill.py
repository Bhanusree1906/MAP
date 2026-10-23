"""A named Codex skill in the catalog.

The database stores identity and which version is live. Package bytes never
live here - they live in SkillStorage under a backend-relative storage_key
(local disk today, S3 later with the same key layout). See skill_version.py
for immutable published packages, and project_skill.py for which skills a
project injects into its sandbox.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Folder name under ~/.codex/skills/<slug>/ inside the sandbox.
    # Lowercase letters, digits, hyphens; max 64 chars (Agent Skills spec).
    slug: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # "custom" (authored here), "skills_sh" (imported from skills.sh / GitHub),
    # "image" (baked into the sandbox image at build time - catalog-only).
    origin: Mapped[str] = mapped_column(
        String(50),
        default="custom",
        nullable=False,
    )

    # Origin locator, e.g. "vercel-labs/agent-skills@react-best-practices"
    # or a git URL. Null for custom skills authored locally.
    origin_ref: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Risk signal captured at import time from the `skills` CLI's own
    # Socket/Snyk scan (see agents/skills/importer.py) - e.g. "Gen: Safe |
    # Socket: 0 alerts | Snyk: Low Risk". Null for custom/image-origin
    # skills, which never go through the import scan.
    risk_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # "active" | "pending_review" | "archived". Imports land as
    # pending_review unless every risk signal reads clean (see importer.py);
    # only "active" skills can be attached to a project or installed into a
    # sandbox (list_project_installs / set_project_skills both filter on
    # this).
    status: Mapped[str] = mapped_column(
        String(50),
        default="active",
        nullable=False,
        index=True,
    )

    # Pointer at the live SkillVersion. A FK (not a version string) so it
    # cannot name a version that was never published. SET NULL if that
    # version row is deleted. use_alter avoids a circular-create cycle with
    # skill_versions.skill_id.
    active_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "skill_versions.id",
            use_alter=True,
            name="fk_skills_active_version_id",
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

    versions = relationship(
        "SkillVersion",
        back_populates="skill",
        foreign_keys="SkillVersion.skill_id",
        cascade="all, delete-orphan",
    )

    active_version = relationship(
        "SkillVersion",
        foreign_keys=[active_version_id],
        post_update=True,
    )

    project_links = relationship(
        "ProjectSkill",
        back_populates="skill",
        cascade="all, delete-orphan",
    )
