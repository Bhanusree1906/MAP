"""Project + git-token CRUD, wrapping agents/pipeline_agent/credentials.py."""

from __future__ import annotations

import zipfile

from database.models.project import Project
from fastapi import APIRouter, File, HTTPException, UploadFile

from agents.pipeline_agent import credentials as project_repo
from agents.pipeline_agent import github_credentials, github_repos
from agents.pipeline_agent.readiness import check_project_readiness
from agents.skills import service as skill_service
from backend.schemas import (
    GitTokenIn,
    ProjectIn,
    ProjectMemoryIn,
    ProjectMemoryOut,
    ProjectOut,
    ProjectSkillOut,
    ProjectSkillsIn,
    SalesforceBootstrapEventOut,
    SalesforceBootstrapJobOut,
    SalesforceBootstrapOut,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _project_context_slug(project_id: str) -> str:
    return f"project-context-{project_id.replace('-', '')[:12]}"


def _project_out(project: Project) -> ProjectOut:
    installs = skill_service.list_project_installs(project.id, strict=False)
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        repository_url=project.repository_url,
        default_branch=project.default_branch,
        jira_connection_name=project.jira_connection_name,
        azure_connection_name=project.azure_connection_name,
        ticket_group_name=project.ticket_group_name,
        github_connection_name=project.github_connection_name,
        azure_git_connection_name=project.azure_git_connection_name,
        sandbox_kind=project.sandbox_kind,
        salesforce_connection_name=project.salesforce_connection_name,
        skills=[ProjectSkillOut(slug=i.slug, version=i.version, name=i.name, phase=i.phase) for i in installs],
        has_memory=skill_service.get_active_memory(project.id) is not None,
        has_git_token=project_repo.get_effective_git_token(project.id) is not None,
        readiness_issues=check_project_readiness(project),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects() -> list[ProjectOut]:
    return [_project_out(p) for p in project_repo.list_projects()]


def _coerce_connection(value: str | None) -> str | None:
    """Callers (and older UIs) send '' for "no connection"; FK columns are
    SET-NULL-safe only when the value is None, so normalize empty -> None."""
    return value if value and value.strip() else None


@router.post("", response_model=ProjectOut)
async def create_or_update_project(payload: ProjectIn) -> ProjectOut:
    git_source_supplied = bool(
        {"github_connection_name", "azure_git_connection_name"} & payload.model_fields_set
    )
    group_source_supplied = "ticket_group_name" in payload.model_fields_set
    jira_connection_name = _coerce_connection(payload.jira_connection_name)
    azure_connection_name = _coerce_connection(payload.azure_connection_name)
    ticket_group_name = _coerce_connection(payload.ticket_group_name)
    github_connection_name = _coerce_connection(payload.github_connection_name)
    azure_git_connection_name = _coerce_connection(payload.azure_git_connection_name)
    salesforce_connection_name = _coerce_connection(payload.salesforce_connection_name)

    if ticket_group_name:
        from agents.pipeline_agent.ingestion import list_ticket_groups

        if not any(g.name == ticket_group_name for g in list_ticket_groups()):
            raise HTTPException(
                status_code=400,
                detail=f"Jira group {ticket_group_name!r} does not exist",
            )
    if jira_connection_name and azure_connection_name:
        raise HTTPException(
            status_code=400,
            detail="A project links exactly one ticket source - pick Jira or Azure, not both.",
        )
    if github_connection_name and azure_git_connection_name:
        raise HTTPException(
            status_code=400,
            detail="A project links exactly one git source - pick GitHub or Azure DevOps Git, not both.",
        )
    connection = None
    if github_connection_name:
        connection = github_credentials.get_github_connection(github_connection_name)
        if connection is None:
            raise HTTPException(
                status_code=400,
                detail=f"GitHub account {github_connection_name!r} is not connected",
            )
    if azure_git_connection_name:
        from agents.azure_agent.connections import get_connection as get_azure_conn
        azure_conn = get_azure_conn(azure_git_connection_name)
        if azure_conn is None:
            raise HTTPException(
                status_code=400,
                detail=f"Azure DevOps connection {azure_git_connection_name!r} not found",
            )
    repository_url = (payload.repository_url or "").strip()
    if connection is not None and not repository_url:
        # No repository selected: create one under the connected account,
        # named after the project.
        try:
            repo = await github_repos.create_repository(
                connection.access_token, payload.name, connection.login
            )
        except github_repos.GitHubRepoError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not create a GitHub repository for this project: {exc}",
            ) from None
        repository_url = repo["clone_url"]
    project = project_repo.upsert_project(
        name=payload.name,
        repository_url=repository_url,
        default_branch=payload.default_branch,
        description=payload.description,
        jira_connection_name=jira_connection_name,
        azure_connection_name=azure_connection_name,
        ticket_group_name=ticket_group_name,
        github_connection_name=github_connection_name,
        azure_git_connection_name=azure_git_connection_name,
        sandbox_kind=payload.sandbox_kind,
        salesforce_connection_name=salesforce_connection_name,
    )
    # Enforce ticket source exclusivity: switching to one clears the others.
    # A group sent explicitly as null ("unlink the group") clears it too.
    if (
        jira_connection_name is not None
        or azure_connection_name is not None
        or ticket_group_name is not None
        or (group_source_supplied and ticket_group_name is None)
    ):
        project = project_repo.set_project_ticket_source(
            project.id,
            jira_connection_name,
            azure_connection_name,
            ticket_group_name,
        )
    # Enforce git source exclusivity.
    if git_source_supplied:
        project = project_repo.set_project_git_source(
            project.id,
            github_connection_name,
            azure_git_connection_name,
        )
    if connection is not None and repository_url:
        try:
            await github_repos.create_branch_with_initial_commit(
                connection.access_token, repository_url, payload.name
            )
        except github_repos.GitHubRepoError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Project saved, but creating the project branch in GitHub failed: {exc}",
            ) from None
    return _project_out(project)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str) -> ProjectOut:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_out(project)


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project_repo.delete_project(project.name)
    return {"status": "ok"}


@router.post("/{project_id}/git-token")
def set_git_token(project_id: str, payload: GitTokenIn) -> dict:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    repository_url = payload.repository_url or project.repository_url
    if not repository_url:
        raise HTTPException(status_code=400, detail="No repository URL to attach this token to")
    project_repo.upsert_git_token(
        project_id=project.id, repository_url=repository_url, access_token=payload.access_token
    )
    return {"status": "ok"}


@router.delete("/{project_id}/git-token")
def delete_git_token(project_id: str) -> dict:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project_repo.delete_git_token(project_id):
        # raise HTTPException(status_code=404, detail="No git access token configured")
        raise HTTPException(status_code=404, detail="No github account selected")
    return {"status": "ok"}


@router.put("/{project_id}/skills", response_model=ProjectOut)
def set_project_skills(project_id: str, payload: ProjectSkillsIn) -> ProjectOut:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        attachments = [skill_service.SkillAttachment(slug=i.slug, phase=i.phase) for i in payload.items]
        skill_service.set_project_skills(project_id, attachments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    project = project_repo.get_project_by_id(project_id)
    return _project_out(project)


@router.post("/{project_id}/context", response_model=ProjectOut)
async def upload_project_context(project_id: str, file: UploadFile = File(...)) -> ProjectOut:
    """Upload an Agents.MD project-context zip and attach it to the project."""
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Project context must be a .zip file")
    raw = await file.read()
    slug = _project_context_slug(project_id)
    try:
        skill_service.save_project_context_from_zip(slug, raw)
        existing = [
            skill_service.SkillAttachment(slug=install.slug, phase=install.phase)
            for install in skill_service.list_project_installs(project_id, strict=False)
            if install.slug != slug
        ]
        existing.append(skill_service.SkillAttachment(slug=slug, phase="both"))
        skill_service.set_project_skills(project_id, existing)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _project_out(project_repo.get_project_by_id(project_id))


@router.get("/{project_id}/memory", response_model=ProjectMemoryOut | None)
def get_project_memory(project_id: str) -> ProjectMemoryOut | None:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    memory = skill_service.get_active_memory(project_id)
    if memory is None:
        return None
    markdown = skill_service.fetch_memory_markdown(project_id) or ""
    return ProjectMemoryOut(
        version=memory.version,
        storage_key=memory.storage_key,
        checksum=memory.checksum,
        byte_size=memory.byte_size,
        commit_sha=memory.commit_sha,
        markdown=markdown,
        created_at=memory.created_at,
    )


@router.put("/{project_id}/memory", response_model=ProjectMemoryOut)
def put_project_memory(project_id: str, payload: ProjectMemoryIn) -> ProjectMemoryOut:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        memory = skill_service.publish_memory(
            project_id,
            payload.markdown,
            version=payload.version,
            commit_sha=payload.commit_sha,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    markdown = skill_service.fetch_memory_markdown(project_id) or payload.markdown
    return ProjectMemoryOut(
        version=memory.version,
        storage_key=memory.storage_key,
        checksum=memory.checksum,
        byte_size=memory.byte_size,
        commit_sha=memory.commit_sha,
        markdown=markdown,
        created_at=memory.created_at,
    )


@router.delete("/{project_id}/memory")
def clear_project_memory(project_id: str) -> dict:
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if skill_service.get_active_memory(project_id) is None:
        raise HTTPException(status_code=404, detail="No project memory to clear")
    skill_service.deactivate_memory(project_id)
    return {"status": "ok"}


@router.post("/{project_id}/salesforce-bootstrap", response_model=SalesforceBootstrapJobOut)
def salesforce_bootstrap(project_id: str) -> SalesforceBootstrapJobOut:
    """Start org-catalog dump + optional DX scaffold in the background.

    Returns a job id immediately. Poll GET .../salesforce-bootstrap/{job_id}
    and .../events the same way the run page polls planning/execution.
    """
    project = project_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.sandbox_kind != "salesforce":
        raise HTTPException(status_code=400, detail="Not a Salesforce project")
    from agents.pipeline_agent.salesforce_bootstrap import start_bootstrap_job

    job = start_bootstrap_job(project_id)
    return SalesforceBootstrapJobOut(job_id=job.id, status=job.status)


@router.get("/{project_id}/salesforce-bootstrap/{job_id}", response_model=SalesforceBootstrapJobOut)
def salesforce_bootstrap_job(project_id: str, job_id: str) -> SalesforceBootstrapJobOut:
    from agents.pipeline_agent.salesforce_bootstrap import get_bootstrap_job

    job = get_bootstrap_job(project_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bootstrap job not found")
    result = SalesforceBootstrapOut(**job.result) if job.result else None
    return SalesforceBootstrapJobOut(
        job_id=job.id, status=job.status, error=job.error, result=result
    )


@router.get(
    "/{project_id}/salesforce-bootstrap/{job_id}/events",
    response_model=list[SalesforceBootstrapEventOut],
)
def salesforce_bootstrap_events(
    project_id: str, job_id: str, after_seq: int = 0
) -> list[SalesforceBootstrapEventOut]:
    from agents.pipeline_agent.salesforce_bootstrap import get_bootstrap_job, list_bootstrap_events

    if get_bootstrap_job(project_id, job_id) is None:
        raise HTTPException(status_code=404, detail="Bootstrap job not found")
    return [
        SalesforceBootstrapEventOut(
            seq=event.seq, kind=event.kind, content=event.content, created_at=event.created_at
        )
        for event in list_bootstrap_events(project_id, job_id, after_seq)
    ]
