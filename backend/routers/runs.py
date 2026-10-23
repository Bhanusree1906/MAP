"""Pipeline run creation, polling, and approval.

POST /api/pipeline-runs kicks off Codex planning (ticket is pushed to a
sandbox; Codex writes the plan); GET /{id} is what the frontend polls;
POST /{id}/approval is the human approval gate - reject just ends the run,
approve kicks off execution (Codex implements the approved plan). See
backend/services/pipeline_runner.py for the actual background work.
"""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from sqlalchemy import func

from database.models.artifact import Artifact
from database.models.cost_log import CostLog
from database.session import SessionLocal

from agents.jira_agent.connections import get_connection as get_jira_connection
from agents.jira_agent.rest import JiraRestError, add_comment
from agents.azure_agent.connections import get_connection as get_azure_connection
from agents.azure_agent.rest import AzureRestError, add_comment as add_azure_comment
from agents.pipeline_agent import run_states as states
from agents.pipeline_agent.credentials import get_project_by_id
from agents.pipeline_agent.readiness import check_project_readiness
from agents.skills import service as skill_service
from agents.pipeline_agent.runs import (
    RunDetail,
    create_artifact,
    create_pipeline_run,
    get_pipeline_run,
    get_run_detail,
    list_agent_run_events,
    list_pipeline_runs,
    update_pipeline_run,
)
from agents.pipeline_agent.schemas import ImplementationPlan
from backend.schemas import (
    AgentRunEventOut,
    AgentRunOut,
    AddressedWarningOut,
    ApprovalIn,
    ArtifactOut,
    CostLogOut,
    CreateRunIn,
    PipelineRunOut,
    OpenQuestionOut,
    PlanOut,
    PlanSectionOut,
    PlanStepOut,
    RunDetailOut,
    SkillUsageOut,
)
from backend.services.pipeline_runner import (
    request_cancel,
    request_pause,
    request_resume,
    start_execution_phase,
    start_planning_phase,
)

router = APIRouter(prefix="/api/pipeline-runs", tags=["pipeline-runs"])


def _skills_used(detail: RunDetail) -> list[SkillUsageOut]:
    """Whether each attached or generated skill was
    actually opened during the run, not just installed into the sandbox -
    "installed" and "used" are different things (an agent decides on its
    own whether an available skill is relevant; see
    agents/pipeline_agent/sandbox_runtime.py). Detected by grepping the
    transcripts for the skill's sandbox path (e.g. .codex/skills/<slug>/ or
    .agents/skills/<slug>/ - see agents/skills/service.py's
    _SANDBOX_SKILLS_DIR), not by any explicit "I used skill X" signal,
    since neither Codex nor opencode emit one.
    """
    try:
        installs = skill_service.list_project_installs(detail.run.project_id, strict=False)
    except Exception:  # noqa: BLE001 - a run's skill list is best-effort display, not load-bearing
        return []
    transcript_text = "\n".join(
        a.content or ""
        for a in detail.artifacts
        if a.artifact_type == states.ARTIFACT_TRANSCRIPT
    )
    usage = [
        SkillUsageOut(slug=i.slug, name=i.name, used=f"/{i.slug}/" in transcript_text)
        for i in installs
    ]
    if "github-memory installed at /home/agentuser/.codex/skills/github-memory" in transcript_text:
        usage.append(SkillUsageOut(slug="github-memory", name="GitHub memory", used=True))
    if "/project-memory/" in transcript_text:
        usage.append(SkillUsageOut(slug="project-memory", name="Project memory", used=True))
    return usage


def _to_run_detail_out(detail: RunDetail) -> RunDetailOut:
    plan_out: PlanOut | None = None
    pr_url: str | None = None
    for artifact in detail.artifacts:
        if artifact.artifact_type == states.ARTIFACT_PLAN and artifact.content:
            try:
                data = json.loads(artifact.content)
                plan_out = PlanOut(
                    ticket_key=data.get("ticket_key", ""),
                    summary=data.get("summary", ""),
                    approach=data.get("approach", ""),
                    steps=[PlanStepOut(**step) for step in data.get("steps", [])],
                    sections=[PlanSectionOut(**section) for section in data.get("sections", [])],
                    open_questions=[
                        OpenQuestionOut(**q) for q in data.get("open_questions", []) if isinstance(q, dict)
                    ],
                    risks=data.get("risks"),
                    out_of_scope=data.get("out_of_scope"),
                    addressed_warnings=[
                        AddressedWarningOut(**warning)
                        for warning in data.get("addressed_warnings", [])
                        if isinstance(warning, dict)
                    ],
                )
            except (ValueError, TypeError):
                pass
        elif artifact.artifact_type == states.ARTIFACT_PR and artifact.content:
            pr_url = artifact.content

    total_cost = sum((c.total_cost for c in detail.cost_logs), Decimal(0))

    return RunDetailOut(
        run=PipelineRunOut.from_run(detail.run),
        agent_runs=[AgentRunOut.model_validate(a) for a in detail.agent_runs],
        artifacts=[ArtifactOut.model_validate(a) for a in detail.artifacts],
        cost_logs=[CostLogOut.model_validate(c) for c in detail.cost_logs],
        skills_used=_skills_used(detail),
        plan=plan_out,
        pr_url=pr_url,
        total_cost=total_cost,
    )


@router.post("", response_model=PipelineRunOut)
async def create_run(payload: CreateRunIn) -> PipelineRunOut:
    project = get_project_by_id(payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if (
        not project.jira_connection_name
        and not project.azure_connection_name
        and not project.ticket_group_name
    ):
        from agents.pipeline_agent import manual_work_items

        if manual_work_items.get_work_item(project.id, payload.ticket_key) is None:
            raise HTTPException(
                status_code=400,
                detail="Project has no linked ticket source or matching manual ticket",
            )

    run = create_pipeline_run(project_id=project.id, metadata={"ticket_key": payload.ticket_key})
    start_planning_phase(run.id, project.id, payload.ticket_key)
    return PipelineRunOut.from_run(run)


@router.get("", response_model=list[PipelineRunOut])
def list_runs(project_id: str | None = None) -> list[PipelineRunOut]:
    runs = list_pipeline_runs(project_id)
    pr_by: dict[str, str] = {}
    cost_by: dict[str, Decimal] = {}
    ids = [r.id for r in runs]
    if ids:
        with SessionLocal() as db:
            for run_id, content in (
                db.query(Artifact.pipeline_run_id, Artifact.content)
                .filter(Artifact.pipeline_run_id.in_(ids), Artifact.artifact_type == states.ARTIFACT_PR)
                .all()
            ):
                if run_id and content:
                    pr_by[run_id] = content
            for run_id, total in (
                db.query(CostLog.pipeline_run_id, func.sum(CostLog.total_cost))
                .filter(CostLog.pipeline_run_id.in_(ids))
                .group_by(CostLog.pipeline_run_id)
                .all()
            ):
                if run_id is not None and total is not None:
                    cost_by[run_id] = total
    out: list[PipelineRunOut] = []
    for run in runs:
        row = PipelineRunOut.from_run(run)
        out.append(row.model_copy(update={"pr_url": pr_by.get(run.id), "total_cost": cost_by.get(run.id)}))
    return out


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str) -> RunDetailOut:
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_run_detail_out(detail)


@router.get("/agent-runs/{agent_run_id}/events", response_model=list[AgentRunEventOut])
def get_agent_run_events(agent_run_id: str, after_seq: int = 0) -> list[AgentRunEventOut]:
    """Polled by the UI while a run is active - pass back the highest seq
    you've already seen (0 on first call) to get only the new events since.
    """
    return [AgentRunEventOut.model_validate(e) for e in list_agent_run_events(agent_run_id, after_seq)]


def _decisions_text(
    plan_content: str, payload: ApprovalIn, review_warnings: list[str] | None = None
) -> str:
    """Render the reviewer's per-question responses (+ any free note + review
    warnings) as implementation guidance."""
    try:
        questions = {q["id"]: q for q in json.loads(plan_content).get("open_questions", [])}
    except (ValueError, TypeError):
        questions = {}
    lines: list[str] = []
    for r in payload.question_responses:
        q = questions.get(r.question_id, {})
        label = q.get("question", r.question_id)
        if r.choice == "answer" and (r.answer_text or "").strip():
            lines.append(f"- {label}\n  ANSWER: {r.answer_text.strip()}")
        else:
            lines.append(f"- {label}\n  Use the planner's default: {q.get('assumed_default', '(default)')}")
    if payload.reviewer_answers and payload.reviewer_answers.strip():
        lines.append(f"- Extra note from the reviewer: {payload.reviewer_answers.strip()}")
    if review_warnings:
        lines.append("\nPreviously identified reviewer warnings to address during implementation:")
        for i, warning in enumerate(review_warnings, 1):
            lines.append(f"  {i}. {warning}")
    return "\n".join(lines) or "(no changes requested)"


@router.post("/{run_id}/approval", response_model=PipelineRunOut)
async def approve_or_reject_run(run_id: str, payload: ApprovalIn) -> PipelineRunOut:
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if detail.run.status != states.AWAITING_APPROVAL:
        raise HTTPException(status_code=409, detail=f"Run is {detail.run.status!r}, not awaiting approval")

    action = payload.resolved_action()

    if action == "reject":
        if payload.escalate_to_jira:
            await _escalate_to_jira(detail, payload.rejection_reason)
        run = update_pipeline_run(
            run_id, status=states.REJECTED, error_message=payload.rejection_reason, completed=True
        )
        return PipelineRunOut.from_run(run)

    plan_artifacts = [a for a in detail.artifacts if a.artifact_type == states.ARTIFACT_PLAN]
    plan_artifact = plan_artifacts[-1] if plan_artifacts else None
    if plan_artifact is None or not plan_artifact.content:
        raise HTTPException(status_code=500, detail="No plan artifact found for this run")

    ticket_key = (detail.run.metadata_ or {}).get("ticket_key")
    if not ticket_key:
        raise HTTPException(status_code=500, detail="Run has no ticket key recorded")

    # start from the reviewer's edited plan if they sent one
    plan_json = payload.plan.model_dump_json() if payload.plan is not None else plan_artifact.content

    # Approval executes the submitted plan directly.
    accumulated_warnings = (detail.run.metadata_ or {}).get("review_warnings", [])

    project = get_project_by_id(detail.run.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    readiness_issues = check_project_readiness(project)
    if readiness_issues:
        raise HTTPException(
            status_code=400,
            detail=f"Project isn't ready for execution: {'; '.join(readiness_issues)}",
        )

    # persist a reviewer-edited plan so a refetch shows what actually ran (WS-7.4)
    if payload.plan is not None and plan_json != plan_artifact.content:
        create_artifact(
            detail.run.project_id, run_id, states.ARTIFACT_PLAN, "plan", plan_json, "reviewer_edit"
        )
    # Fold per-question responses and any previously stored review warnings
    # into implementation guidance.
    accumulated_warnings = (detail.run.metadata_ or {}).get("review_warnings", [])
    answers = payload.reviewer_answers
    if payload.question_responses:
        answers = _decisions_text(plan_artifact.content, payload, accumulated_warnings or None)
    elif accumulated_warnings:
        warning_header = "Reviewer warnings (must be addressed during implementation):\n"
        warning_list = "\n".join(
            f"  {i + 1}. {w}" for i, w in enumerate(accumulated_warnings)
        )
        answers = f"{warning_header}{warning_list}" + (f"\n\n{answers}" if answers else "")
    run = update_pipeline_run(run_id, status=states.APPROVED, current_stage=states.STAGE_EXECUTION)
    start_execution_phase(run_id, detail.run.project_id, ticket_key, plan_json, answers)
    return PipelineRunOut.from_run(run)


@router.post("/{run_id}/pause", response_model=PipelineRunOut)
def pause_run(run_id: str) -> PipelineRunOut:
    """Pause an in-flight run (planning or execution). Freezes the run's
    sandbox container in place if the phase is mid-run, otherwise blocks the
    phase task at its next checkpoint. Resume via POST /{run_id}/resume.
    """
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if detail.run.status not in (states.PLANNING, states.RUNNING):
        raise HTTPException(
            status_code=409, detail=f"Run is {detail.run.status!r}, only planning/running runs can be paused"
        )
    if not request_pause(run_id):
        raise HTTPException(status_code=409, detail="Run has no active phase task to pause")
    paused = get_run_detail(run_id)
    if paused is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return PipelineRunOut.from_run(paused.run)


@router.post("/{run_id}/resume", response_model=PipelineRunOut)
def resume_run(run_id: str) -> PipelineRunOut:
    """Resume a paused run from exactly where it stopped - the sandbox
    container is thawed (SIGCONT) and the phase task's checkpoint unblocks.
    """
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if detail.run.status != states.PAUSED:
        raise HTTPException(status_code=409, detail=f"Run is {detail.run.status!r}, not paused")
    if not request_resume(run_id):
        raise HTTPException(status_code=409, detail="Run has no active phase task to resume")
    resumed = get_run_detail(run_id)
    if resumed is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return PipelineRunOut.from_run(resumed.run)


@router.post("/{run_id}/cancel", response_model=PipelineRunOut)
def cancel_run(run_id: str) -> PipelineRunOut:
    """Stop an in-flight run (planning, running, or paused) immediately.
    Kills the sandbox container if one is running - unpausing first if the
    run is currently paused - rather than waiting for the next checkpoint.
    """
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if detail.run.status not in states.ACTIVE_RUN_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"Run is {detail.run.status!r}, only active runs can be canceled"
        )
    if not request_cancel(run_id):
        raise HTTPException(status_code=409, detail="Run has no active phase task to cancel")
    cancelled = get_run_detail(run_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return PipelineRunOut.from_run(cancelled.run)


@router.post("/{run_id}/retry", response_model=PipelineRunOut)
async def retry_run(run_id: str) -> PipelineRunOut:
    """Retry a failed implementation with its approved plan; otherwise start
    a fresh planning run for the same project and ticket.
    """
    detail = get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if detail.run.status not in states.RETRYABLE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Run is {detail.run.status!r}, only rejected/failed/canceled runs can be retried",
        )
    ticket_key = (detail.run.metadata_ or {}).get("ticket_key")
    if not ticket_key:
        raise HTTPException(status_code=500, detail="Run has no ticket key recorded")

    plan_artifacts = [
        artifact
        for artifact in detail.artifacts
        if artifact.artifact_type == states.ARTIFACT_PLAN and artifact.content
    ]
    retry_implementation = (
        detail.run.status in (states.FAILED, states.CANCELLED)
        and detail.run.current_stage == states.STAGE_EXECUTION
        and bool(plan_artifacts)
    )

    if retry_implementation:
        plan_artifact = max(plan_artifacts, key=lambda artifact: artifact.version)
        plan = ImplementationPlan.model_validate_json(plan_artifact.content)
        project = get_project_by_id(detail.run.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        readiness_issues = check_project_readiness(project)
        if readiness_issues:
            raise HTTPException(
                status_code=400,
                detail=f"Project isn't ready for execution: {'; '.join(readiness_issues)}",
            )

        new_run = create_pipeline_run(
            project_id=detail.run.project_id,
            metadata={
                "ticket_key": ticket_key,
                "retried_from_run_id": run_id,
                "retry_mode": "implementation",
            },
        )
        plan_json = plan.model_dump_json()
        create_artifact(
            detail.run.project_id,
            new_run.id,
            states.ARTIFACT_PLAN,
            "plan",
            plan_json,
            "retry",
        )
        new_run = update_pipeline_run(
            new_run.id,
            status=states.APPROVED,
            current_stage=states.STAGE_EXECUTION,
        )
        if new_run is None:
            raise HTTPException(status_code=404, detail="Retry run not found")

        warnings = (detail.run.metadata_ or {}).get("review_warnings", [])
        reviewer_answers = None
        if warnings:
            reviewer_answers = "Reviewer warnings to address during implementation:\n" + "\n".join(
                f"  {index}. {warning}" for index, warning in enumerate(warnings, 1)
            )
        start_execution_phase(
            new_run.id,
            detail.run.project_id,
            ticket_key,
            plan_json,
            reviewer_answers,
        )
        return PipelineRunOut.from_run(new_run)

    new_run = create_pipeline_run(
        project_id=detail.run.project_id,
        metadata={"ticket_key": ticket_key, "retried_from_run_id": run_id},
    )
    start_planning_phase(new_run.id, detail.run.project_id, ticket_key)
    return PipelineRunOut.from_run(new_run)


async def _escalate_to_jira(detail: RunDetail, rejection_reason: str | None) -> None:
    """Posts the reviewer's note to the linked ticket."""
    if not rejection_reason or not rejection_reason.strip():
        raise HTTPException(status_code=400, detail="A reviewer note is required")

    project = get_project_by_id(detail.run.project_id)
    if project is None:
        raise HTTPException(status_code=400, detail="Project not found")

    ticket_key = (detail.run.metadata_ or {}).get("ticket_key")
    if not ticket_key:
        raise HTTPException(status_code=500, detail="Run has no ticket key recorded")

    comment_text = f"Reviewer note: {rejection_reason.strip()}"

    if project.azure_connection_name:
        connection = get_azure_connection(project.azure_connection_name)
        if connection is None:
            raise HTTPException(status_code=400, detail="Azure connection not found")
        try:
            await add_azure_comment(connection, int(ticket_key), comment_text)
        except AzureRestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not post to Azure DevOps: {exc}") from None
        return

    if not project.jira_connection_name:
        return
    connection = get_jira_connection(project.jira_connection_name)
    if connection is None:
        raise HTTPException(status_code=400, detail="Jira connection not found")

    try:
        await add_comment(connection, ticket_key, comment_text)
    except JiraRestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not post to Jira: {exc}") from None
