"""One immutable published package of a Skill.

Publishing never overwrites an existing (skill_id, version) row or its
storage_key - bump the version to publish again. That is what makes
rollback (repointing Skill.active_version_id) and reproducible sandbox
installs possible. The DB holds the key and checksum; bytes live in
SkillStorage (see agents/skills/storage.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    skill_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # "local" today, "s3" later. The key layout is identical either way:
    # skills/<slug>/<version>/skill.tar.gz - only the backend that resolves
    # the key changes. Never store an absolute filesystem path here; that
    # breaks the moment the process cwd, host, or bucket changes.
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

    # Snapshot of SKILL.md frontmatter at publish time, so the catalog can
    # show what Codex will see without unpacking the tarball.
    skill_md_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    skill_md_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Kept so existing SQLite rows (NOT NULL status) keep inserting. "published"
    # is the only value written today; archive happens on Skill.status.
    status: Mapped[str] = mapped_column(
        String(50),
        default="published",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    skill = relationship(
        "Skill",
        back_populates="versions",
        foreign_keys=[skill_id],
    )

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)
