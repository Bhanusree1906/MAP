"""GitHub repository listing and per-project branch bootstrap.

Used when creating a project from the UI: the form lists every repo the
connected GitHub account can see, and project creation opens a branch
named after the project (project-name slug) with an initial commit so the
pipeline has somewhere to push code from the very first run.
"""

from __future__ import annotations

import base64
import re

import httpx

GITHUB_API = "https://api.github.com"


class GitHubRepoError(RuntimeError):
    pass


def project_branch_slug(name: str) -> str:
    """'Demo Project (GitHub Repo Access)' -> 'demo-project-github-repo-access'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def repo_name_slug(project_name: str) -> str:
    """'Demo Project (GitHub Repo Access)' -> 'Demo-Project-GitHub-Repo-Access'.

    GitHub repo names may contain letters, digits, '-', '_' and '.' (max 100 chars).
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", project_name).strip("-.")
    return slug[:100] or "project"


def parse_repo_slug(repository_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from an https:// or git@ GitHub URL."""
    match = re.search(
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        repository_url.strip(),
    )
    if not match:
        raise GitHubRepoError(f"{repository_url!r} is not a GitHub repository URL")
    return match.group("owner"), match.group("repo")


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_error(response: httpx.Response, action: str) -> str:
    try:
        detail = response.json().get("message") or response.text
    except Exception:
        detail = response.text
    return f"GitHub failed to {action} (HTTP {response.status_code}): {str(detail)[:300]}"


async def list_repositories(access_token: str, max_pages: int = 5) -> list[dict]:
    """Every repo the token can see, most recently active first."""
    repos: list[dict] = []
    async with httpx.AsyncClient(timeout=20.0, headers=_headers(access_token)) as client:
        for page in range(1, max_pages + 1):
            response = await client.get(
                f"{GITHUB_API}/user/repos",
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            if response.status_code != 200:
                raise GitHubRepoError(_api_error(response, "list repositories"))
            batch = response.json()
            for repo in batch:
                repos.append(
                    {
                        "name": repo["name"],
                        "full_name": repo["full_name"],
                        "clone_url": repo["clone_url"],
                        "default_branch": repo.get("default_branch"),
                        "private": bool(repo.get("private")),
                    }
                )
            if len(batch) < 100:
                break
    return repos


async def create_repository(
    access_token: str,
    project_name: str,
    owner_login: str | None = None,
) -> dict:
    """Create a private GitHub repo named after the project and return its clone URL.

    Idempotent: when a repo with that name already exists under the connected
    account it is reused instead of failing, so re-saving a project still works.
    """
    repo_name = repo_name_slug(project_name)
    async with httpx.AsyncClient(timeout=20.0, headers=_headers(access_token)) as client:
        login = owner_login
        if not login:
            me = await client.get(f"{GITHUB_API}/user")
            if me.status_code != 200:
                raise GitHubRepoError(_api_error(me, "read the authenticated user"))
            login = me.json()["login"]

        created = await client.post(
            f"{GITHUB_API}/user/repos",
            json={"name": repo_name, "private": True, "auto_init": False},
        )
        if created.status_code == 201:
            data = created.json()
            return {"clone_url": data["clone_url"], "full_name": data["full_name"], "created": True}
        if created.status_code == 422:
            # Name already taken under this account: reuse the existing repo.
            existing = await client.get(f"{GITHUB_API}/repos/{login}/{repo_name}")
            if existing.status_code == 200:
                data = existing.json()
                return {"clone_url": data["clone_url"], "full_name": data["full_name"], "created": False}
        raise GitHubRepoError(_api_error(created, f"create repository {repo_name!r}"))


async def create_branch_with_initial_commit(
    access_token: str,
    repository_url: str,
    project_name: str,
) -> dict:
    """Create a branch named after the project and push an initial README commit.

    Idempotent: when the branch already exists the repo is left untouched,
    so re-saving a project never adds duplicate commits.
    """
    owner, repo = parse_repo_slug(repository_url)
    branch = project_branch_slug(project_name)
    async with httpx.AsyncClient(timeout=20.0, headers=_headers(access_token)) as client:
        base = f"{GITHUB_API}/repos/{owner}/{repo}"
        repo_info = await client.get(base)
        if repo_info.status_code == 404:
            raise GitHubRepoError(
                f"Repository {owner}/{repo} not found, or the connected account has no access to it"
            )
        if repo_info.status_code != 200:
            raise GitHubRepoError(_api_error(repo_info, "read repository"))
        default_branch = repo_info.json().get("default_branch") or "main"

        base_sha = await _branch_sha(client, base, default_branch)
        if base_sha is None:
            # Empty repo: seed the default branch so there is a commit to branch from.
            await _put_readme(client, base, default_branch, project_name, "chore: initial commit")
            base_sha = await _branch_sha(client, base, default_branch)
            if base_sha is None:
                raise GitHubRepoError(f"Could not initialize the default branch of {owner}/{repo}")

        created = await client.post(
            f"{base}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if created.status_code == 422:
            return {"branch": branch, "repo": f"{owner}/{repo}", "created": False, "skipped": True}
        if created.status_code not in (200, 201):
            raise GitHubRepoError(_api_error(created, f"create branch {branch!r}"))

        await _put_readme(
            client,
            base,
            branch,
            project_name,
            f"chore: initial commit for project {project_name!r}",
        )
        return {"branch": branch, "repo": f"{owner}/{repo}", "created": True, "skipped": False}


async def _branch_sha(client: httpx.AsyncClient, base: str, branch: str) -> str | None:
    response = await client.get(f"{base}/git/refs/heads/{branch}")
    if response.status_code != 200:
        return None
    return response.json()["object"]["sha"]


async def _put_readme(
    client: httpx.AsyncClient,
    base: str,
    branch: str,
    project_name: str,
    message: str,
) -> None:
    """Create or update README.md on the given branch (a real commit either way)."""
    path = f"{base}/contents/README.md"
    body: dict = {
        "message": message,
        "content": base64.b64encode(_readme(project_name).encode()).decode(),
        "branch": branch,
    }
    existing = await client.get(path, params={"ref": branch})
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
    response = await client.put(path, json=body)
    if response.status_code not in (200, 201):
        raise GitHubRepoError(_api_error(response, "push the initial commit"))


def _readme(project_name: str) -> str:
    return f"# {project_name}\n\nThis repository is managed by the Agentic Pipeline.\n"
