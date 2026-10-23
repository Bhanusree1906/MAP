"""Azure DevOps connection CRUD.

Each connection scopes the pipeline to one Azure DevOps organization + project
and holds its Personal Access Token. The UI lets the user pick Jira or Azure
as the ticket source; Azure connections are created here (POST) and only know
how to read work items unless read_only is disabled (which also permits the
pipeline to post completion comments). PATs are encrypted at rest via
EncryptedText and never echoed back - only a has_pat flag.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agents.azure_agent import connections as azure_connections
from agents.azure_agent.git import list_repositories
from agents.azure_agent.rest import AzureRestError
from backend.schemas import (
    AzureConnectionCreate,
    AzureConnectionOut,
    AzureConnectionUpdate,
    AzureGitRepoOut,
)

router = APIRouter(prefix="/api/azure-connections", tags=["azure-connections"])


def _validate_org_url(organization_url: str) -> str:
    url = organization_url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Organization URL is required")
    if not (url.startswith("https://dev.azure.com/") or url.startswith("https://vsrm.dev.azure.com/") or url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="Organization URL should look like https://dev.azure.com/your-org",
        )
    return url


@router.get("", response_model=list[AzureConnectionOut])
def list_connections() -> list[AzureConnectionOut]:
    return [AzureConnectionOut.from_model(c) for c in azure_connections.list_connections()]


@router.post("", response_model=AzureConnectionOut, status_code=201)
def create_connection(payload: AzureConnectionCreate) -> AzureConnectionOut:
    name = payload.name.strip()
    organization_url = _validate_org_url(payload.organization_url)
    project_name = payload.project_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Connection name is required")
    if not project_name:
        raise HTTPException(status_code=400, detail="Project name is required")
    if azure_connections.get_connection(name) is not None:
        raise HTTPException(status_code=409, detail=f"A connection named {name!r} already exists")
    created = azure_connections.upsert_connection(
        name=name,
        organization_url=organization_url,
        project_name=project_name,
        pat=payload.pat,
        read_only=payload.read_only,
    )
    return AzureConnectionOut.from_model(created)


@router.get("/{name}", response_model=AzureConnectionOut)
def get_connection(name: str) -> AzureConnectionOut:
    conn = azure_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return AzureConnectionOut.from_model(conn)


@router.put("/{name}", response_model=AzureConnectionOut)
def update_connection(name: str, payload: AzureConnectionUpdate) -> AzureConnectionOut:
    conn = azure_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    updated = azure_connections.upsert_connection(
        name=name,
        organization_url=_validate_org_url(payload.organization_url or conn.organization_url),
        project_name=(payload.project_name if payload.project_name is not None else conn.project_name),
        pat=payload.pat,
        read_only=payload.read_only if payload.read_only is not None else conn.read_only,
    )
    return AzureConnectionOut.from_model(updated)


@router.delete("/{name}")
def delete_connection(name: str) -> dict:
    removed = azure_connections.delete_connection(name)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "ok"}


@router.get("/{name}/repos", response_model=list[AzureGitRepoOut])
async def list_git_repos(name: str) -> list[AzureGitRepoOut]:
    """List the Git repositories in this connection's Azure DevOps project,
    used by the project git-source picker."""
    conn = azure_connections.get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        repos = await list_repositories(conn)
    except AzureRestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return [
        AzureGitRepoOut(
            id=str(repo.get("id", "")),
            name=str(repo.get("name", "")),
            clone_url=str(repo.get("remoteUrl", "") or ""),
            web_url=str(repo.get("webUrl", "") or ""),
            default_branch=str(repo.get("defaultBranch", ""))
            .replace("refs/heads/", "")
            or "main",
        )
        for repo in repos
        if repo.get("id") and repo.get("name")
    ]