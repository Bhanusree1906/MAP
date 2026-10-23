"""Pydantic request/response models for the FastAPI backend.

Deliberately never echoes secrets back: GitTokenIn is write-only (no *Out
counterpart returns access_token), JiraConnectionOut excludes
oauth_tokens/oauth_client_info/jira_api_token entirely (only a
has_api_token flag is exposed so the UI can show configured status).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProjectSkillOut(BaseModel):
    slug: str
    version: str
    name: str
    phase: str = "both"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    status: str
    repository_url: str | None = None
    default_branch: str
    jira_connection_name: str | None = None
    azure_connection_name: str | None = None
    ticket_group_name: str | None = None
    github_connection_name: str | None = None
    azure_git_connection_name: str | None = None
    sandbox_kind: str
    salesforce_connection_name: str | None = None
    skills: list[ProjectSkillOut] = []
    has_memory: bool = False
    has_git_token: bool = False
    readiness_issues: list[str] = []
    created_at: datetime
    updated_at: datetime


class ProjectIn(BaseModel):
    name: str
    repository_url: str | None = None
    default_branch: str = "main"
    jira_connection_name: str | None = None
    azure_connection_name: str | None = None
    ticket_group_name: str | None = None
    github_connection_name: str | None = None
    azure_git_connection_name: str | None = None
    sandbox_kind: str | None = None
    salesforce_connection_name: str | None = None
    description: str | None = None


class GitTokenIn(BaseModel):
    access_token: str
    repository_url: str | None = None


class JiraConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    project_key: str
    site_url: str | None = None
    jira_email: str | None = None
    has_api_token: bool = False
    has_oauth_token: bool = False
    read_only: bool
    created_at: datetime
    updated_at: datetime
    health_status: str = "unknown"
    health_checked_at: str | None = None
    health_error: str | None = None

    @classmethod
    def from_model(cls, conn, health: dict | None = None) -> "JiraConnectionOut":
        health = health or {}
        return cls(
            name=conn.name,
            project_key=conn.project_key,
            site_url=conn.site_url,
            jira_email=conn.jira_email,
            has_api_token=bool(conn.jira_api_token),
            has_oauth_token=bool(conn.jira_oauth_access_token),
            read_only=conn.read_only,
            created_at=conn.created_at,
            updated_at=conn.updated_at,
            health_status=health.get("status", "unknown"),
            health_checked_at=health.get("checked_at"),
            health_error=health.get("error"),
        )


class JiraConnectionCreate(BaseModel):
    name: str
    project_key: str
    site_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    read_only: bool = True


class JiraConnectionUpdate(BaseModel):
    project_key: str | None = None
    site_url: str | None = None
    read_only: bool | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None


class AzureConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    organization_url: str
    project_name: str
    has_pat: bool = False
    read_only: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, conn) -> "AzureConnectionOut":
        return cls(
            name=conn.name,
            organization_url=conn.organization_url,
            project_name=conn.project_name,
            has_pat=bool(conn.pat),
            read_only=conn.read_only,
            created_at=conn.created_at,
            updated_at=conn.updated_at,
        )


class AzureConnectionCreate(BaseModel):
    name: str
    organization_url: str
    project_name: str
    pat: str | None = None
    read_only: bool = True


class AzureConnectionUpdate(BaseModel):
    organization_url: str | None = None
    project_name: str | None = None
    pat: str | None = None
    read_only: bool | None = None


class AzureGitRepoOut(BaseModel):
    id: str
    name: str
    clone_url: str
    web_url: str
    default_branch: str


class SalesforceConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    org_alias: str
    instance_url: str | None = None
    has_auth_url: bool = False
    created_at: datetime
    updated_at: datetime
    health_status: str = "unknown"
    health_checked_at: str | None = None
    health_error: str | None = None

    @classmethod
    def from_model(cls, conn, health: dict | None = None) -> "SalesforceConnectionOut":
        health = health or {}
        return cls(
            name=conn.name,
            org_alias=conn.org_alias,
            instance_url=conn.instance_url,
            has_auth_url=bool(conn.sfdx_auth_url),
            created_at=conn.created_at,
            updated_at=conn.updated_at,
            health_status=health.get("status", "unknown"),
            health_checked_at=health.get("checked_at"),
            health_error=health.get("error"),
        )


class SalesforceConnectionUpdate(BaseModel):
    org_alias: str | None = None
    sfdx_auth_url: str | None = None
    instance_url: str | None = None


class TicketSubtaskOut(BaseModel):
    key: str
    summary: str
    status: str | None = None
    url: str | None = None


class TicketAttachmentOut(BaseModel):
    id: str
    filename: str
    url: str | None = None
    mime_type: str | None = None
    size: int | None = None


class TicketOut(BaseModel):
    key: str
    summary: str
    status: str | None = None
    assignee: str | None = None
    description: str | None = None
    priority: str | None = None
    issue_type: str | None = None
    labels: list[str] = []
    reporter: str | None = None
    created: str | None = None
    updated: str | None = None
    url: str | None = None
    # Jira's own `fields.parent` / `fields.subtasks` are included in the
    # ticket response even when a subtask is not rendered as its own row.
    parent_key: str | None = None
    subtasks: list[TicketSubtaskOut] = []
    attachments: list[TicketAttachmentOut] = []


class PlanStepOut(BaseModel):
    title: str
    detail: str


class PlanSectionOut(BaseModel):
    title: str
    content: str


class OpenQuestionOut(BaseModel):
    id: str
    question: str
    why_it_matters: str
    assumed_default: str
    blocking: bool = False


class AddressedWarningOut(BaseModel):
    warning: str
    resolution: str


class PlanOut(BaseModel):
    ticket_key: str
    summary: str
    approach: str
    steps: list[PlanStepOut]
    sections: list[PlanSectionOut] = []
    open_questions: list[OpenQuestionOut] = []
    risks: str | None = None
    out_of_scope: str | None = None
    addressed_warnings: list[AddressedWarningOut] = []


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_name: str
    stage: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class AgentRunEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    kind: str
    content: str
    created_at: datetime


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    artifact_type: str
    name: str
    version: int
    content: str | None = None
    created_by: str
    created_at: datetime


class CostLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    total_cost: Decimal
    currency: str


class PipelineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    status: str
    current_stage: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    ticket_key: str | None = None
    retried_from_run_id: str | None = None
    pr_url: str | None = None
    total_cost: Decimal | None = None
    review_warnings: list[str] | None = None
    # Legacy result from metadata_.warning_validation, retained for older runs.
    warning_validation: dict | None = None

    @classmethod
    def from_run(cls, run) -> "PipelineRunOut":
        meta = getattr(run, "metadata_", None) or {}
        warnings = meta.get("review_warnings") if isinstance(meta, dict) else None
        validation = meta.get("warning_validation") if isinstance(meta, dict) else None
        return cls(
            id=run.id,
            project_id=run.project_id,
            status=run.status,
            current_stage=run.current_stage,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            ticket_key=meta.get("ticket_key") if isinstance(meta, dict) else None,
            retried_from_run_id=meta.get("retried_from_run_id") if isinstance(meta, dict) else None,
            review_warnings=warnings,
            warning_validation=validation if isinstance(validation, dict) else None,
        )


class RunDetailOut(BaseModel):
    run: PipelineRunOut
    agent_runs: list[AgentRunOut]
    artifacts: list[ArtifactOut]
    cost_logs: list[CostLogOut]
    plan: PlanOut | None = None
    pr_url: str | None = None
    total_cost: Decimal
    skills_used: list[SkillUsageOut] = []


class CreateRunIn(BaseModel):
    project_id: str
    ticket_key: str


class QuestionResponse(BaseModel):
    """One reviewer response to a plan's open_question (WS-7.2)."""

    question_id: str
    choice: Literal["default", "answer"] = "default"
    answer_text: str | None = None


class ApprovalIn(BaseModel):
    approve: bool
    # `approve` (bool) is kept for the old client: approve=False + no action
    # still means reject.
    action: Literal["approve", "reject"] | None = None
    rejection_reason: str | None = None
    # Optional edited plan from the approval screen.
    plan: PlanOut | None = None
    # Per-open_question responses to include as implementation guidance.
    question_responses: list[QuestionResponse] = []
    # Optional free-text note for the implementer. No longer load-bearing.
    reviewer_answers: str | None = None
    # Only meaningful when rejecting: post rejection_reason as a Jira comment.
    escalate_to_jira: bool = False

    def resolved_action(self) -> str:
        if self.action:
            return self.action
        return "approve" if self.approve else "reject"


class SkillVersionOut(BaseModel):
    version: str
    storage_backend: str
    storage_key: str
    checksum: str
    byte_size: int
    skill_md_name: str | None = None
    skill_md_description: str | None = None
    created_at: datetime


class SkillOut(BaseModel):
    slug: str
    name: str
    description: str | None = None
    origin: str
    origin_ref: str | None = None
    risk_notes: str | None = None
    status: str
    active_version: str | None = None
    # Most recently published version regardless of active/pending status -
    # a pending_review skill has no active_version yet, but the review UI
    # still needs a version to pass to the approve action.
    latest_version: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectSkillItemIn(BaseModel):
    slug: str
    phase: str = "both"


class ProjectSkillsIn(BaseModel):
    items: list[ProjectSkillItemIn]


class SkillImportCandidateOut(BaseModel):
    name: str
    description: str


class SkillImportIn(BaseModel):
    source: str
    skill_name: str


class SkillImportOut(BaseModel):
    slug: str
    version: str
    status: str
    risk_notes: str | None = None


class SkillApproveIn(BaseModel):
    version: str


class SkillContentOut(BaseModel):
    slug: str
    content: str
    version: str | None = None


class SkillProjectOut(BaseModel):
    id: str
    name: str


class SkillCreateIn(BaseModel):
    slug: str
    content: str
    name: str | None = None


class SkillContentIn(BaseModel):
    content: str
    name: str | None = None


class SkillUploadOut(BaseModel):
    """Extracted from an uploaded .md/.txt/.zip file - not saved yet, the
    editor shows this for review before the user explicitly saves it.
    """

    suggested_slug: str | None = None
    content: str
    # Other files the zip had alongside SKILL.md (references/*.md, scripts/,
    # etc.), relative paths, informational only - empty for .md/.txt
    # uploads. Saving still requires re-sending the zip itself (see
    # backend/routers/skills.py's /from-zip endpoints) since these aren't
    # staged anywhere server-side between upload and save.
    bundled_files: list[str] = []


class SkillUsageOut(BaseModel):
    """Whether an attached skill's SKILL.md was actually opened during a
    run, not just installed into the sandbox (see backend/routers/runs.py -
    installed and used are different things; see also
    agents/skills/service.py's install_into_sandbox/install_memory_into_sandbox).
    """

    slug: str
    name: str
    used: bool


class ProjectMemoryOut(BaseModel):
    version: str
    storage_key: str
    checksum: str
    byte_size: int
    commit_sha: str | None = None
    markdown: str
    created_at: datetime


class ProjectMemoryIn(BaseModel):
    markdown: str
    commit_sha: str | None = None
    version: str | None = None


class SalesforceBootstrapOut(BaseModel):
    catalog_objects: int
    api_version: str = ""
    scaffolded: bool
    scaffold_skipped: bool
    message: str


class SalesforceBootstrapJobOut(BaseModel):
    job_id: str
    status: str
    error: str | None = None
    result: SalesforceBootstrapOut | None = None


class SalesforceBootstrapEventOut(BaseModel):
    seq: int
    kind: str
    content: str
    created_at: datetime


class TicketGroupIn(BaseModel):
    name: str
    description: str | None = None


class TicketGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    ticket_count: int = 0
    created_at: datetime
    updated_at: datetime
    tickets: list["ManualTicketOut"] = []


class ManualTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    ticket_id: str
    summary: str
    description: str | None = None
    issue_type: str | None = None
    priority: str | None = None
    assignee: str | None = None
    status: Literal["todo", "progress", "agentcompleted", "completed"] = "todo"
    has_attachment: bool = False
    attachment_filename: str | None = None
    attachment_size: int | None = None
    attachment_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ManualTicketStatusIn(BaseModel):
    status: Literal["todo", "progress", "agentcompleted", "completed"]


class RichManualTicketCreate(BaseModel):
    title: str
    body_html: str = ""


class RichManualTicketUpdate(RichManualTicketCreate):
    pass


ManualWorkItemStatus = Literal["todo", "progress", "agentcompleted", "completed"]


class ManualWorkItemCreate(BaseModel):
    title: str
    body_html: str = ""


class ManualWorkItemUpdate(ManualWorkItemCreate):
    pass


class ManualWorkItemStatusIn(BaseModel):
    status: ManualWorkItemStatus


class ManualWorkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    public_id: str
    project_id: str
    title: str
    body_html: str
    body_text: str
    status: ManualWorkItemStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    agent_completed_at: datetime | None = None
    completed_at: datetime | None = None


class FetchTicketsIn(BaseModel):
    """Which source to pull tickets from for the project import flow."""

    source: Literal["jira", "azure", "group"]
    # Required when source == "group" - which manual ticket group to list.
    group_id: str | None = None
    # Optional Jira JQL. Only honored for source == "jira".
    jql: str | None = None


class ImportedTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source: str
    source_connection: str | None = None
    key: str
    summary: str
    description: str | None = None
    status: str | None = None
    issue_type: str | None = None
    priority: str | None = None
    assignee: str | None = None
    labels: list[str] = []
    reporter: str | None = None
    url: str | None = None
    attachment_filename: str | None = None
    attachment_size: int | None = None
    imported_at: datetime


class ImportTicketsIn(BaseModel):
    """Import the given ticket keys from one source into a project."""

    source: Literal["jira", "azure", "group"]
    keys: list[str]
    # Required when source == "group" - which group the keys belong to.
    group_id: str | None = None


class ImportTicketsOut(BaseModel):
    imported: list[ImportedTicketOut]
    # Keys already imported into this project from the same source.
    skipped: list[str] = []
