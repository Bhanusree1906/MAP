"""Azure DevOps Git REST helpers (repos, pull requests).

Mirrors what agents/pipeline_agent/github_repos.py does for GitHub. Uses the
same PAT-based auth as agents/azure_agent/rest.py. Endpoints use the same
/_apis base_url pattern (includes org + project).

Endpoints used (api-version 7.1):
  GET  /_apis/git/repositories                                - list repos in a project
  POST /_apis/git/repositories/{repoId}/pullrequests          - create a pull request
"""

from __future__ import annotations

from typing import Any

from .connections import AzureConnection
from .rest import AzureRestError, _get, _post, _raise_for_status


class AzureGitError(RuntimeError):
    """Raised when an Azure DevOps Git REST call fails."""


async def list_repositories(connection: AzureConnection) -> list[dict[str, Any]]:
    """List all Git repositories in the connection's project."""
    response = await _get(connection, "/git/repositories")
    _raise_for_status(response, "list repositories")
    payload = response.json() if response.content else {}
    return [r for r in (payload.get("value") or []) if isinstance(r, dict)]


async def resolve_project_clone_url(connection: AzureConnection) -> str:
    """Return the HTTPS clone URL for the Azure DevOps project's git repo.

    If there is exactly one repository in the project, use it directly.
    If there are multiple, the *first* (alphabetically by name) is chosen
    — an imperfect heuristic that covers the common single-repo case without
    requiring the caller to pick a name. Raises ``AzureGitError`` when the
    project contains no repositories or the PAT lacks Code read permission."""
    repos = await list_repositories(connection)
    if not repos:
        raise AzureGitError(
            f"No git repositories found in Azure DevOps project "
            f"{connection.project_name!r}. Create a repository first."
        )
    if len(repos) == 1:
        clone_url = repos[0].get("remoteUrl", "")
        if clone_url:
            return clone_url
        raise AzureGitError(
            "The single repository in the Azure DevOps project has no remoteUrl."
        )
    # Multiple repos: prefer the one whose name matches the Azure project
    # name (the conventional default), else take the first alphabetically.
    project_lower = connection.project_name.lower()
    best = None
    for repo in sorted(repos, key=lambda r: str(r.get("name", "")).lower()):
        if str(repo.get("name", "")).lower() == project_lower:
            best = repo
            break
    if best is None:
        best = repos[0]
    clone_url = best.get("remoteUrl", "")
    if not clone_url:
        raise AzureGitError(
            "The selected repository in the Azure DevOps project has no remoteUrl."
        )
    return clone_url


async def create_pull_request(
    connection: AzureConnection,
    repository_id: str,
    source_ref_name: str,
    target_ref_name: str,
    title: str,
    description: str,
) -> str:
    """Create a pull request in the named repo and return its web URL."""
    body: dict[str, Any] = {
        "sourceRefName": source_ref_name,
        "targetRefName": target_ref_name,
        "title": title,
        "description": description,
    }
    response = await _post(
        connection,
        f"/git/repositories/{repository_id}/pullrequests",
        body,
    )
    _raise_for_status(response, "create pull request")
    payload = response.json() if response.content else {}
    # The API returns _links.web.href for the PR URL.
    links = payload.get("_links") or {}
    web = links.get("web") or {}
    if isinstance(web, dict) and web.get("href"):
        return str(web["href"])
    # Fallback: construct from the PR id.
    pr_id = payload.get("pullRequestId")
    repo_name = repository_id
    org = connection.organization_url.rstrip("/").split("/")[-1]
    project = connection.project_name
    if pr_id:
        return f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo_name}/pullrequests/{pr_id}"
    raise AzureGitError("Azure DevOps did not return a URL for the created pull request.")
