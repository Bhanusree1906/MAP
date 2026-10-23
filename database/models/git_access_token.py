import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..crypto import EncryptedText


class GitAccessToken(Base):
    __tablename__ = "git_access_tokens"

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

    repository_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    access_token: Mapped[str] = mapped_column(
        EncryptedText,
        nullable=False,
    )

    token_type: Mapped[str] = mapped_column(
        String(50),
        default="git",
        nullable=False,
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

    project = relationship(
        "Project",
        back_populates="git_access_tokens",
    )
