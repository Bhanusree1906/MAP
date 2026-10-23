"""Provider-aware pull-request opening for the pipeline.

GitHub projects open the PR with the `gh` CLI inside the sandbox
(SandboxManager.open_pull_request), which authenticates via GH_TOKEN and
infers the repo from the `origin` remote. Azure DevOps Git projects have no
`gh` equivalent when the remote host is Azure, so the PR is created from
outside the sandbox through the Azure DevOps REST API
(agents/azure_agent/git.py) - the engine already pushed the branch, so no
container is needed for that step.

Callers run in two different shapes of event loop, so two thin wrappers exist:
the sync one (worker threads in backend/services/pipeline_runner.py) spins up
its own loop via asyncio.run; the async one (the manager's LLM tool handlers,
which already live on a loop) awaits directly.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _azure_repo_name(repository_url: str) -> str:
    """Last path segment of the Azure remote URL = the repository name Azure's
    REST API accepts as the repositoryId."""
    name = repository_url.rstrip("/").rsplit("/", 1)[-1] if repository_url else ""
    return name.removesuffix(".git")


async def open_azure_pull_request(
    settings: Any,
    *,
    base: str,
    head: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    """Create the PR on Azure DevOps Git via REST. Returns the same shape as
    SandboxManager.open_pull_request ({"success", "url", "stderr"})."""
    from agents.azure_agent.connections import get_connection as get_azure_conn
    from agents.azure_agent.git import create_pull_request as azure_create_pr

    if not settings.git_connection_name:
        return {
            "success": False,
            "url": None,
            "stderr": "No Azure DevOps connection is linked to this project's git source.",
        }
    conn = get_azure_conn(settings.git_connection_name)
    if conn is None:
        return {
            "success": False,
            "url": None,
            "stderr": f"Azure DevOps connection {settings.git_connection_name!r} no longer exists.",
        }
    repo_name = _azure_repo_name(settings.repository_url)
    if not repo_name:
        return {
            "success": False,
            "url": None,
            "stderr": "Cannot derive the repository name from the project's repository URL.",
        }
    try:
        url = await azure_create_pr(
            conn,
            repo_name,
            f"refs/heads/{head}",
            f"refs/heads/{base}",
            title,
            body,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as the PR error
        return {"success": False, "url": None, "stderr": str(exc)}
    return {"success": True, "url": url, "stderr": ""}


def _open_pull_request_aware_sync(
    settings: Any,
    sandbox: Any,
    *,
    base: str,
    head: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    if settings.git_provider == "azure":
        return asyncio.run(
            open_azure_pull_request(settings, base=base, head=head, title=title, body=body)
        )
    return sandbox.open_pull_request(base=base, head=head, title=title, body=body)


async def open_pull_request_aware(
    settings: Any,
    sandbox: Any,
    *,
    base: str,
    head: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    if settings.git_provider == "azure":
        return await open_azure_pull_request(settings, base=base, head=head, title=title, body=body)
    return await asyncio.to_thread(
        sandbox.open_pull_request, base=base, head=head, title=title, body=body
    )