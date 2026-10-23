"""Definition of the Azure DevOps connection model.

Each row is a named connection scoping the pipeline to one Azure DevOps
organization + project (e.g. organization_url ``https://dev.azure.com/myorg``
and project_name ``FirstProject``), mirroring how
``database/models/jira_connection.py`` scopes the Jira agent to one Jira
project. Credentials are authenticated with a Personal Access Token (PAT) via
HTTP Basic auth (empty username, PAT as password) - see
``agents/azure_agent/rest.py``.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..crypto import EncryptedText


class AzureConnection(Base):
    __tablename__ = "azure_connections"

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

    # e.g. "https://dev.azure.com/myorg" - the organization's public URL.
    organization_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # e.g. "FirstProject" - the project the agent reads work items from.
    project_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Azure DevOps Personal Access Token, authenticated with Basic auth
    # (username empty, PAT as the password). High privileges = "Read, write &
    # manage" of Work Items is enough for the pipeline to list work items,
    # fetch details and post completion comments.
    pat: Mapped[str | None] = mapped_column(
        EncryptedText,
        nullable=True,
    )

    read_only: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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