"""Background-task bodies for the two pipeline phases.

Planning writes the ticket into a sandbox and asks Codex for PLAN.json;
execution tells Codex to implement the approved plan. Fired via
asyncio.create_task() from the route handlers in backend/routers/runs.py -
no task queue infra, this is an in-process, single-deployment tool. Each
phase is wrapped in try/except so a crash always leaves the run in a
terminal "failed" state instead of stuck "running" forever with nothing
for the UI to explain.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from collections.abc import Callable, Coroutine

from agents.jira_agent.connections import get_connection as get_jira_connection
from agents.jira_agent.rest import JiraRestError, add_comment, transition_issue_to_status
from agents.azure_agent.connections import get_connection as get_azure_connection
from agents.azure_agent.rest import AzureRestError, add_comment as add_azure_comment
from agents.pipeline_agent import ingestion
from agents.pipeline_agent import manual_work_items
from agents.pipeline_agent import run_states as states
from agents.pipeline_agent._timing import timed
from agents.pipeline_agent.agent import build_execution_agent, open_execution_workspace
from agents.pipeline_agent.manager import ManagerOutcome, manager_enabled, run_managed_pipeline
from agents.pipeline_agent.codex_credentials import get_codex_auth_json
from agents.pipeline_agent.config import Settings, load_settings, resolve_azure_git_for_project
from agents.pipeline_agent.credentials import get_project_by_id
from agents.pipeline_agent.pause import RunPauseControl
from agents.pipeline_agent.runner import UsageTotals, run_codex_planning, run_execution
from agents.pipeline_agent.runs import (
    add_agent_run_event,
    complete_agent_run,
    create_agent_run,
    create_artifact,
    create_cost_log,
    get_pipeline_run,
    update_pipeline_run,
)
from agents.pipeline_agent.salesforce_credentials import get_salesforce_connection
from agents.pipeline_agent.schemas import ImplementationPlan, plan_to_text

logger = logging.getLogger(__name__)


def _make_redactor(settings: Settings):
    """Live events stream raw sandbox output (see tools/sandbox_tools.py,
    agents/pipeline_agent/runner.py) - unlike the final transcript, nothing
    upstream truncates or filters it before it reaches the DB. Codex's own
    shell commands routinely echo things like `git remote -v`, which
    includes the git token embedded in the authenticated clone URL (see
    sandbox/manager.py's authenticated_clone_url) in cleartext. Scrub every
    known secret out of event content at the one place all of it funnels
    through before being persisted.
    """
    secrets = [s for s in (settings.git_token, settings.codex_api_key) if s]
    if settings.salesforce_connection_name:
        sf_connection = get_salesforce_connection(settings.salesforce_connection_name)
        if sf_connection and sf_connection.sfdx_auth_url:
            secrets.append(sf_connection.sfdx_auth_url)
    codex_auth_json = get_codex_auth_json()
    if codex_auth_json:
        secrets.append(codex_auth_json)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    return redact

# Keeps background tasks referenced so they aren't garbage-collected mid-run
# (asyncio only holds a weak reference otherwise) - not used for
# cancellation/inspection in v1, just task lifetime safety.
_tasks: dict[str, asyncio.Task] = {}

# Pause controls keyed by run_id, one per in-flight phase task. The pause/
# resume routes look these up to freeze/thaw the run (see RunPauseControl);
# the phase tasks hold the same control to block at cooperative checkpoints.
_controls: dict[str, RunPauseControl] = {}

# Cap each pipeline phase independently. Each phase holds a Docker container
# for its whole duration, so without a cap N tickets = N containers = host
# OOM. Runs over their phase cap sit in status "queued" and start FIFO as
# slots free. This is an in-process gate (single-worker only - see Readme.md);
# a real job queue is a later phase.
_MAX_CONCURRENT_RUNS = max(1, int(os.getenv("PIPELINE_AGENT_MAX_CONCURRENT_RUNS", "3")))
_planning_slots = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)
_execution_slots = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)

# Hard wall-clock cap on the execution phase as a whole - a backstop for parts
# the in-container engine timeout (see SandboxManager.engine_timeout_seconds)
# doesn't cover, e.g. a hung model stream or MCP handshake.
_PHASE_TIMEOUT_SECONDS = max(60, int(os.getenv("PIPELINE_AGENT_PHASE_TIMEOUT", "3600")))


def _stored_group_ticket(project_id: str, ticket_key: str):
    standalone = manual_work_items.get_work_item(project_id, ticket_key)
    if standalone is not None:
        return manual_work_items.work_item_to_markdown(standalone), []

    imported = ingestion.get_imported_ticket(project_id, ticket_key, source="group")
    if imported is not None:
        return (
            ingestion.imported_ticket_to_markdown(imported),
            ingestion.imported_ticket_image_attachments(imported),
        )

    project = get_project_by_id(project_id)
    if project is None or not project.ticket_group_name:
        return None
    for group in ingestion.list_ticket_groups():
        if group.name != project.ticket_group_name:
            continue
        manual = ingestion.get_manual_ticket(group.id, ticket_key)
        if manual is not None:
            return (
                ingestion.manual_ticket_to_markdown(manual),
                ingestion.manual_ticket_image_attachments(manual),
            )
    return None


async def _run_with_slot(
    run_id: str, active_status: str, phase: Coroutine, slots: asyncio.Semaphore
) -> None:
    """Gate a phase coroutine on a concurrency slot.

    Marks the run "queued" if no slot is free, acquires FIFO, then flips it to
    its active status and runs. If the run reached a terminal status while
    queued (e.g. the user cancelled it), the phase never starts.
    """
    if slots.locked():
        update_pipeline_run(run_id, status=states.QUEUED)
    await slots.acquire()
    try:
        run = get_pipeline_run(run_id)
        if run is None or run.status in states.TERMINAL_RUN_STATUSES:
            logger.info("Run %s is %s before its slot opened; not starting", run_id, run and run.status)
            phase.close()
            return
        update_pipeline_run(run_id, status=active_status)
        await phase
    finally:
        slots.release()


def _open_pull_request(
    sandbox,
    settings: Settings,
    ticket_key: str,
    summary: str,
    on_event: Callable[[str, str], None] | None,
) -> str | None:
    """Open the PR from the branch the coding engine pushed.

    Runs in a worker thread (blocking sandbox commands). Returns the PR URL,
    or None if there is no feature branch or opening it failed (GitHub via the
    sandbox's `gh`, Azure via REST) - the run still completes, just without a
    linked PR.
    """
    from agents.pipeline_agent.pr import _open_pull_request_aware_sync

    head = sandbox.current_branch()
    if not head or head == settings.default_branch:
        if on_event:
            on_event("step", "No feature branch was pushed; not opening a pull request")
        return None

    remote = sandbox.execute(
        f"git ls-remote --exit-code --heads origin refs/heads/{shlex.quote(head)}"
    )
    if not remote.get("success"):
        if on_event:
            detail = (remote.get("stderr") or remote.get("stdout") or "unknown error").strip()
            on_event("step", f"Branch {head} is not present on origin; not opening a pull request: {detail[:400]}")
        return None

    first_line = next((ln for ln in summary.strip().splitlines() if ln.strip()), "")
    title = f"{ticket_key}: {first_line[:72]}" if first_line else ticket_key
    body = (
        f"Automated implementation for {ticket_key}.\n\n"
        f"{summary.strip() or 'See the ticket for details.'}"
    )
    if on_event:
        on_event("step", f"Opening pull request for branch {head}")
    with timed(on_event, "open pull request"):
        pr = _open_pull_request_aware_sync(
            settings, sandbox, base=settings.default_branch, head=head, title=title, body=body
        )
    if pr["success"]:
        if on_event:
            on_event("step", f"Pull request: {pr['url']}")
        return pr["url"]
    if on_event:
        on_event("step", f"Could not open a pull request: {(pr.get('stderr') or '').strip()[:400]}")
    return None


def _completion_comment(summary: str, pr_url: str | None) -> str:
    comment = [
        "The automated pipeline completed the implementation for this ticket.",
        "",
        summary.strip() or "Implementation completed without a summary.",
    ]
    if pr_url:
        comment.extend(["", f"Pull request: {pr_url}"])
    return "\n".join(comment)


async def _post_completion_comment(settings: Settings, ticket_key: str, summary: str, pr_url: str | None) -> None:
    comment_text = _completion_comment(summary, pr_url)

    if settings.azure is not None:
        connection = get_azure_connection(settings.azure.connection_name)
        if connection is None:
            raise RuntimeError(f"No Azure connection named {settings.azure.connection_name!r} found")
        try:
            await add_azure_comment(connection, int(ticket_key), comment_text)
        except AzureRestError as exc:
            raise RuntimeError(
                f"Implementation completed, but posting the Azure DevOps comment failed: {exc}"
            ) from exc
        return

    if settings.jira is None:
        return

    connection = get_jira_connection(settings.jira.connection_name)
    if connection is None:
        raise RuntimeError(f"No Jira connection named {settings.jira.connection_name!r} found")

    try:
        await add_comment(connection, ticket_key, comment_text)
    except JiraRestError as exc:
        raise RuntimeError(f"Implementation completed, but posting the Jira comment failed: {exc}") from exc


async def _mark_jira_agent_complete(settings: Settings, ticket_key: str) -> None:
    # Jira Group tickets are stored locally and have no live Jira connection
    # to transition. Their lifecycle is maintained by the manual-ticket DB
    # status updates in the execution phase.
    if settings.azure is not None or settings.jira is None:
        return
    connection = get_jira_connection(settings.jira.connection_name)
    if connection is None:
        raise RuntimeError(f"No Jira connection named {settings.jira.connection_name!r} found")
    try:
        await transition_issue_to_status(connection, ticket_key, "Agent Complete")
    except JiraRestError as exc:
        raise RuntimeError(
            f"Implementation completed and PR was created, but moving the Jira ticket to "
            f"'Agent Complete' failed: {exc}"
        ) from exc


def start_planning_phase(run_id: str, project_id: str, ticket_key: str) -> None:
    manual_work_items.update_work_item_status(project_id, ticket_key, "progress")
    ingestion.update_manual_ticket_status_for_project(
        project_id, ticket_key, "progress"
    )
    control = RunPauseControl(run_id, active_status=states.PLANNING)
    _controls[run_id] = control
    task = asyncio.create_task(
        _run_with_slot(
            run_id,
            states.PLANNING,
            _run_planning_phase(run_id, project_id, ticket_key, control),
            _planning_slots,
        )
    )
    _tasks[run_id] = task
    task.add_done_callback(lambda _: (_tasks.pop(run_id, None), _controls.pop(run_id, None)))


def start_execution_phase(
    run_id: str, project_id: str, ticket_key: str, plan_json: str, reviewer_answers: str | None = None
) -> None:
    control = RunPauseControl(run_id, active_status=states.RUNNING)
    _controls[run_id] = control
    task = asyncio.create_task(
        _run_with_slot(
            run_id,
            states.RUNNING,
            _run_execution_phase(run_id, project_id, ticket_key, plan_json, reviewer_answers, control),
            _execution_slots,
        )
    )
    _tasks[run_id] = task
    task.add_done_callback(lambda _: (_tasks.pop(run_id, None), _controls.pop(run_id, None)))


def request_pause(run_id: str) -> bool:
    """Freeze an in-flight run. Returns False if the run has no live control
    (no phase task is actually running) or is already paused."""
    control = _controls.get(run_id)
    if control is None:
        return False
    # Re-check the DB status here (not just at the route) so a phase that
    # finished between the route's read and this call can't be paused
    # retroactively - pausing a completed run would wedge it forever.
    run = get_pipeline_run(run_id)
    if run is None or run.status not in (states.PLANNING, states.RUNNING):
        return False
    return control.request_pause()


def request_resume(run_id: str) -> bool:
    """Thaw a paused run. Returns False if the run has no live control."""
    control = _controls.get(run_id)
    if control is None:
        return False
    run = get_pipeline_run(run_id)
    if run is None or run.status != states.PAUSED:
        return False
    return control.request_resume()


def request_cancel(run_id: str) -> bool:
    """Stop an in-flight run (planning or execution, paused or not). Returns
    False if the run has no live control (no phase task is actually
    running)."""
    control = _controls.get(run_id)
    if control is None:
        return False
    # Same defensive re-check as request_pause/request_resume - a phase that
    # finished between the route's read and this call must not be canceled
    # retroactively.
    run = get_pipeline_run(run_id)
    if run is None or run.status not in states.ACTIVE_RUN_STATUSES:
        return False
    cancelled = control.request_cancel()
    # Second line of defense for any bare `await` not wrapped in a
    # checkpoint() call (an HTTP request to Jira, an LLM call) - the
    # sandbox kill above only reaches blocking sandbox execs. This route
    # runs in FastAPI's threadpool (like /pause and /resume), never the
    # event loop the task itself runs on, so cancellation must be handed
    # to that loop via call_soon_threadsafe rather than calling task.cancel()
    # directly - same reasoning as pause.py's threading.Event over
    # asyncio.Event.
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        task.get_loop().call_soon_threadsafe(task.cancel)
    return cancelled


async def _run_planning_phase(
    run_id: str, project_id: str, ticket_key: str, control: RunPauseControl
) -> None:
    agent_run = create_agent_run(run_id, states.AGENT_NAME_PLANNING, states.STAGE_PLANNING)
    # Overwritten with the real redactor once settings load - a raised exception
    # (e.g. Codex's own stdout echoing `git remote -v`) can carry the same
    # secrets live events do, so the except block below must never store one
    # unredacted, including when the crash happens before settings exist.
    redact: Callable[[str], str] = lambda text: text
    try:
        project = get_project_by_id(project_id)
        settings = load_settings(project.jira_connection_name, project.name)
        settings = await resolve_azure_git_for_project(settings)
        redact = _make_redactor(settings)

        def on_event(kind: str, content: str) -> None:
            add_agent_run_event(agent_run.id, kind, redact(content))

        local = _stored_group_ticket(project_id, ticket_key)

        result = await run_codex_planning(
            settings, ticket_key, on_event=on_event, pause_control=control,
            ticket_markdown=local[0] if local else None,
            image_attachments=local[1] if local else None,
        )

        artifact = create_artifact(
            project_id,
            run_id,
            states.ARTIFACT_PLAN,
            "plan",
            result.plan.model_dump_json(),
            states.AGENT_NAME_PLANNING,
        )
        create_artifact(
            project_id,
            run_id,
            states.ARTIFACT_TRANSCRIPT,
            "planning_transcript",
            redact(result.transcript),
            states.AGENT_NAME_PLANNING,
        )
        complete_agent_run(agent_run.id, status=states.AGENT_RUN_COMPLETED, output_artifact_id=artifact.id)
        update_pipeline_run(run_id, status=states.AWAITING_APPROVAL, current_stage=states.STAGE_PLANNING)
    except asyncio.CancelledError:
        # RunPauseControl.request_cancel() already wrote status=CANCELLED
        # (single writer, same as pause/resume) - just close out this
        # AgentRun row so it doesn't dangle as "running", then let the
        # cancellation propagate.
        logger.info("Planning phase canceled for run %s", run_id)
        complete_agent_run(agent_run.id, status=states.AGENT_RUN_FAILED, error_message="Canceled by user")
        raise
    except Exception as exc:  # noqa: BLE001 - failure boundary for a background task
        logger.exception("Planning phase failed for run %s", run_id)
        error_message = redact(str(exc))
        complete_agent_run(agent_run.id, status=states.AGENT_RUN_FAILED, error_message=error_message)
        update_pipeline_run(run_id, status=states.FAILED, error_message=error_message, completed=True)


async def _run_execution_phase(
    run_id: str,
    project_id: str,
    ticket_key: str,
    plan_json: str,
    reviewer_answers: str | None,
    control: RunPauseControl,
) -> None:
    agent_run = create_agent_run(run_id, states.AGENT_NAME_EXECUTION, states.STAGE_EXECUTION)
    redact: Callable[[str], str] = lambda text: text
    try:
        project = get_project_by_id(project_id)
        settings = load_settings(project.jira_connection_name, project.name)
        settings = await resolve_azure_git_for_project(settings)
        plan = ImplementationPlan.model_validate_json(plan_json)
        redact = _make_redactor(settings)

        def on_event(kind: str, content: str) -> None:
            add_agent_run_event(agent_run.id, kind, redact(content))

        update_pipeline_run(run_id, status=states.RUNNING, current_stage=states.STAGE_EXECUTION)
        plan_text = plan_to_text(plan, reviewer_answers)
        local = _stored_group_ticket(project_id, ticket_key)

        # Keep a handle on the container so the phase-timeout guard can kill it
        # directly - build_execution_agent detaches it from `control` during its
        # own (possibly cancelled) teardown, so control.kill can't be relied on.
        sandbox_ref: dict = {}

        async def _execute():
            if manager_enabled(settings):
                async with open_execution_workspace(
                    settings,
                    ticket_key,
                    plan_text,
                    on_event=on_event,
                    pause_control=control,
                    ticket_markdown=local[0] if local else None,
                    image_attachments=local[1] if local else None,
                ) as sandbox:
                    sandbox_ref["sandbox"] = sandbox
                    outcome = await run_managed_pipeline(
                        settings,
                        sandbox,
                        ticket_key,
                        plan_text,
                        project_id=project_id,
                        run_id=run_id,
                        on_event=on_event,
                        pause_control=control,
                    )
                    return outcome

            async with build_execution_agent(
                settings,
                ticket_key,
                plan_text,
                on_event=on_event,
                pause_control=control,
                ticket_markdown=local[0] if local else None,
                image_attachments=local[1] if local else None,
            ) as (agent, sandbox):
                sandbox_ref["sandbox"] = sandbox
                exec_result = await run_execution(
                    agent,
                    "Implement the approved plan and push a branch.",
                    on_event=on_event,
                    pause_control=control,
                )
                pr_url = await asyncio.to_thread(
                    _open_pull_request,
                    sandbox,
                    settings,
                    ticket_key,
                    redact(exec_result.summary),
                    on_event,
                )
                exec_result.pr_url = pr_url or exec_result.pr_url
                return exec_result

        try:
            result = await asyncio.wait_for(_execute(), timeout=_PHASE_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError):
            sandbox = sandbox_ref.get("sandbox")
            if sandbox is not None:
                try:
                    sandbox.kill()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    logger.exception("Failed to kill sandbox after execution timeout for run %s", run_id)
            raise RuntimeError(
                f"Execution phase exceeded the {_PHASE_TIMEOUT_SECONDS}s time limit and was stopped."
            ) from None

        if isinstance(result, ManagerOutcome):
            summary = result.report_markdown
            pr_url = result.pr_url
            transcript = f"tools: {', '.join(result.tool_calls)}"
            usage = result.usage or UsageTotals()
            already_commented = result.reported
        else:
            summary = result.summary
            pr_url = result.pr_url
            transcript = result.transcript
            usage = result.usage
            already_commented = False

        manual_work_items.update_work_item_status(project_id, ticket_key, "agentcompleted")
        ingestion.update_manual_ticket_status_for_project(project_id, ticket_key, "agentcompleted")

        comment_text = summary if already_commented else _completion_comment(summary, pr_url)

        summary_artifact = create_artifact(
            project_id,
            run_id,
            states.ARTIFACT_EXECUTION_SUMMARY,
            "summary",
            redact(summary),
            states.AGENT_NAME_EXECUTION,
        )
        create_artifact(
            project_id,
            run_id,
            states.ARTIFACT_COMMENT,
            "ticket_comment",
            redact(comment_text),
            states.AGENT_NAME_EXECUTION,
        )
        create_artifact(
            project_id,
            run_id,
            states.ARTIFACT_TRANSCRIPT,
            "execution_transcript",
            redact(transcript),
            states.AGENT_NAME_EXECUTION,
        )
        if pr_url:
            create_artifact(
                project_id,
                run_id,
                states.ARTIFACT_PR,
                "pull_request",
                pr_url,
                states.AGENT_NAME_EXECUTION,
            )
        create_cost_log(
            project_id,
            run_id,
            states.AGENT_NAME_EXECUTION,
            "google",
            settings.google_model,
            "execution",
            usage,
        )
        complete_agent_run(
            agent_run.id, status=states.AGENT_RUN_COMPLETED, output_artifact_id=summary_artifact.id
        )
        if not already_commented:
            await _post_completion_comment(settings, ticket_key, summary, pr_url)
        if pr_url:
            await _mark_jira_agent_complete(settings, ticket_key)
        manual_work_items.update_work_item_status(project_id, ticket_key, "completed")
        ingestion.update_manual_ticket_status_for_project(project_id, ticket_key, "completed")
        update_pipeline_run(run_id, status=states.COMPLETED, completed=True)
    except asyncio.CancelledError:
        # See _run_planning_phase - status is already CANCELLED, just close
        # out this AgentRun row and propagate.
        logger.info("Execution phase canceled for run %s", run_id)
        complete_agent_run(agent_run.id, status=states.AGENT_RUN_FAILED, error_message="Canceled by user")
        raise
    except Exception as exc:  # noqa: BLE001 - failure boundary for a background task
        logger.exception("Execution phase failed for run %s", run_id)
        error_message = redact(str(exc))
        complete_agent_run(agent_run.id, status=states.AGENT_RUN_FAILED, error_message=error_message)
        update_pipeline_run(run_id, status=states.FAILED, error_message=error_message, completed=True)
