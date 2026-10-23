import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentRunEvent(Base):
    """An append-only, ordered log of what an AgentRun is doing right now -
    which tool it's calling, and (for run_codex specifically) Codex's own
    live stdout/stderr - so the UI can show live progress instead of a
    black box until the run's final transcript artifact is written.

    seq is a per-agent_run counter (not a timestamp) so the frontend can
    poll "give me events after seq N" and only ever receive new ones,
    instead of re-fetching and re-rendering everything on each poll.
    """

    __tablename__ = "agent_run_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id"),
        nullable=False,
        index=True,
    )

    seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # "step" (orchestrator tool-call transitions) or "codex_stdout" /
    # "codex_stderr" (Codex's own live output from inside run_codex).
    kind: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (Index("ix_agent_run_events_run_seq", "agent_run_id", "seq"),)
