import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..crypto import EncryptedText


class JiraConnection(Base):
    """A named connection scoping the Jira agent to one Jira project.

    Different people/teams reuse the same agent code against different Jira
    projects by creating their own named row here. Each row stores its own
    OAuth client registration and tokens, so connections authenticate and
    refresh independently of one another.
    """

    __tablename__ = "jira_connections"

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

    project_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    site_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Jira Cloud REST API credentials (email + API token, Basic Auth) used by
    # the plain scoped ticket-listing endpoint (backend/routers/tickets.py).
    # Separate from the OAuth fields below: this is a direct API integration
    # that needs no browser, unlike the MCP/OAuth flow the LLM agent uses.
    jira_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    jira_api_token: Mapped[str | None] = mapped_column(
        EncryptedText,
        nullable=True,
    )

    # Browser-based OAuth 2.0 (3LO) tokens for the same REST API integration
    # above - the "click Connect, authorize in a new tab" alternative to
    # pasting an email/API token by hand. Distinct from oauth_client_info/
    # oauth_tokens below, which belong to the MCP server's own (unrelated)
    # OAuth dance used by the LLM agent. jira_cloud_id is resolved once via
    # the accessible-resources endpoint right after authorizing, so REST
    # calls can hit https://api.atlassian.com/ex/jira/{cloud_id}/... without
    # the caller needing to know it.
    jira_oauth_access_token: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    jira_oauth_refresh_token: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    jira_oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    jira_cloud_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    mcp_server_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="https://mcp.atlassian.com/v1/mcp/authv2",
    )

    read_only: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # OAuth client registration + token set for this connection, persisted as
    # JSON text. Storing these per-connection (instead of one shared file on
    # disk) is what lets several Jira-project connections coexist and each
    # keep its own login session.
    oauth_client_info: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    oauth_tokens: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)

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
