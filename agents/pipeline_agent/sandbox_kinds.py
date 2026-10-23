"""Maps a project's sandbox_kind to the Docker image its pipeline runs use.

Each project picks a kind (see database/models/project.py); the env var
overrides let each image be repointed (e.g. to a registry tag) without code
changes, mirroring PIPELINE_AGENT_SANDBOX_IMAGE's existing pattern.
"""

from __future__ import annotations

import os

DEFAULT_KIND = "default"

_ENV_VAR_BY_KIND = {
    "default": "PIPELINE_AGENT_SANDBOX_IMAGE",
    "salesforce": "PIPELINE_AGENT_SANDBOX_IMAGE_SALESFORCE",
}

_DEFAULT_IMAGE_BY_KIND = {
    "default": "agent-sandbox:latest",
    "salesforce": "agent-sandbox-salesforce:latest",
}


def available_kinds() -> list[str]:
    return list(_DEFAULT_IMAGE_BY_KIND)


def resolve_sandbox_image(kind: str | None) -> str:
    kind = kind or DEFAULT_KIND
    if kind not in _DEFAULT_IMAGE_BY_KIND:
        raise ValueError(f"Unknown sandbox_kind {kind!r}. Expected one of: {available_kinds()}")
    return os.getenv(_ENV_VAR_BY_KIND[kind], _DEFAULT_IMAGE_BY_KIND[kind])
