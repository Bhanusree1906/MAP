import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..crypto import EncryptedText


class CodexConnection(Base):
    """A saved Codex CLI login (~/.codex/auth.json from a device-code login -
    see agents/pipeline_agent/codex_login.py), injected into every pipeline
    run's sandbox so Codex runs authenticated as a ChatGPT account instead
    of OPENAI_API_KEY. One shared row ("default") for now, not per-project.
    """

    __tablename__ = "codex_connections"

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

    auth_json: Mapped[str] = mapped_column(
        EncryptedText,
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
