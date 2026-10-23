import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..crypto import EncryptedText


class SalesforceConnection(Base):
    """A named Salesforce org login, reusable across projects the same way
    JiraConnection is (see database/models/jira_connection.py).

    Salesforce CLI (`sf`) has no headless-friendly login in this version -
    `org login web` requires an actual GUI browser process (fails outright
    in a Docker container, no URL-printing fallback like Codex has), and
    `org login jwt` needs a pre-configured Connected App + certificate in
    the org. The practical option is the SFDX auth URL: log in once via
    `sf org login web` on a real machine, extract the URL with
    `sf org auth show-sfdx-auth-url --target-org <org> --json`, and paste it
    here. Every sandbox run then replays it non-interactively via
    `sf org login sfdx-url --sfdx-url-stdin` - no browser involved.
    """

    __tablename__ = "salesforce_connections"

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

    org_alias: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    sfdx_auth_url: Mapped[str] = mapped_column(
        EncryptedText,
        nullable=False,
    )

    instance_url: Mapped[str | None] = mapped_column(
        String(500),
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
