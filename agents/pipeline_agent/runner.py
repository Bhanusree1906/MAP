"""Invoke loops for the two pipeline phases.

Planning does not load a planning task onto the main Gemini agent. The
pipeline writes the ticket into the sandbox and Codex drafts PLAN.json
(run_codex_planning). Execution still uses the Gemini orchestrator
(run_execution) only to tell Codex to implement the approved plan.

Unlike agents/jira_agent/runner.py (which prints straight to stdout for the
CLI), these return structured results so the FastAPI background tasks can
persist them via runs.py instead of parsing terminal output.

Execution streams via agent.astream(..., stream_mode="values") instead of a
single blocking ainvoke() - each yield is the full state so far, so diffing
messages against what was already seen on the previous yield gives new
messages as they're produced, which on_event (if given) turns into "step"
events for runs.add_agent_run_event - live progress instead of a black box
until the whole phase finishes. See tools/sandbox_tools.py's run_codex for
the complementary, finer-grained stream from *inside* one tool call.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

from agents.jira_agent.runner import as_text
from agents.skills.service import fetch_memory_markdown

from ._timing import timed
from .config import Settings
from .runs import UsageTotals
from .sandbox_runtime import open_pipeline_sandbox, prepare_sandbox_repo
from .schemas import (
    PLAN_JSON_INSTRUCTIONS,
    ImplementationPlan,
    PlanExtractionError,
)
from .ticket import fetch_ticket_image_attachments, fetch_ticket_markdown

_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")

OnEvent = Callable[[str, str], None]


def _extract_usage(messages: list) -> UsageTotals:
    totals = UsageTotals()
    for msg in messages:
        usage_metadata = getattr(msg, "usage_metadata", None)
        if not usage_metadata:
            continue
        totals.input_tokens += usage_metadata.get("input_tokens", 0) or 0
        totals.output_tokens += usage_metadata.get("output_tokens", 0) or 0
        totals.total_tokens += usage_metadata.get("total_tokens", 0) or 0
    return totals


def _transcript(messages: list) -> str:
    lines = []
    for msg in messages:
        role = getattr(msg, "type", msg.__class__.__name__)
        lines.append(f"{role}: {as_text(getattr(msg, 'content', ''))}")
    return "\n\n".join(lines)


def _extract_pr_url(messages: list) -> str | None:
    """Fallback only. The pipeline opens the PR itself with `gh` after
    execution (see pipeline_runner._open_pull_request) and uses that URL;
    this scans the transcript for a pull request URL in case an engine
    printed one anyway. Most recent message first."""
    for msg in reversed(messages):
        content = as_text(getattr(msg, "content", ""))
        match = _PR_URL_RE.search(content)
        if match:
            return match.group(0)
    return None


def _short(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_message(msg) -> list[tuple[str, str]]:
    """Turns one new message into zero or more (kind, content) step events."""
    msg_type = getattr(msg, "type", msg.__class__.__name__)
    tool_calls = getattr(msg, "tool_calls", None)
    events: list[tuple[str, str]] = []

    if msg_type == "ai" and tool_calls:
        for call in tool_calls:
            name = call.get("name", "?")
            args_preview = ", ".join(f"{k}={_short(str(v), 80)}" for k, v in (call.get("args") or {}).items())
            events.append(("step", f"→ {name}({args_preview})"))
        text = as_text(getattr(msg, "content", ""))
        if text.strip():
            events.append(("step", _short(text)))
    elif msg_type == "tool":
        name = getattr(msg, "name", "tool")
        content = as_text(getattr(msg, "content", ""))
        events.append(("step", f"← {name}: {_short(content)}"))
    elif msg_type == "ai":
        text = as_text(getattr(msg, "content", ""))
        if text.strip():
            events.append(("step", _short(text)))

    return events


async def _stream_and_emit(agent, input_state: dict, on_event: OnEvent | None, pause_control=None) -> dict:
    """Drives agent.astream(...), emitting step events for new messages as
    they appear, and returns the final accumulated state (same shape
    ainvoke() would have returned)."""
    seen = 0
    final_state: dict = {}
    async for state in agent.astream(input_state, stream_mode="values"):
        final_state = state
        messages = state.get("messages", [])
        if on_event:
            for msg in messages[seen:]:
                for kind, content in _describe_message(msg):
                    on_event(kind, content)
        seen = len(messages)
        if pause_control is not None:
            await pause_control.checkpoint()
    return final_state


@dataclass
class PlanningResult:
    plan: ImplementationPlan
    transcript: str
    usage: UsageTotals


@dataclass
class ExecutionResult:
    summary: str
    pr_url: str | None
    transcript: str
    usage: UsageTotals


def _parse_plan_json(raw: str, ticket_key: str, label: str = "The planning agent") -> ImplementationPlan:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        plan = ImplementationPlan.model_validate_json(text)
    except Exception as exc:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            raise PlanExtractionError(
                f"{label} did not produce a valid PLAN.json for {ticket_key!r}: {exc}"
            ) from exc
        try:
            plan = ImplementationPlan.model_validate_json(match.group(0))
        except Exception as nested:
            raise PlanExtractionError(
                f"{label} did not produce a valid PLAN.json for {ticket_key!r}: {nested}"
            ) from nested
    if plan.ticket_key != ticket_key:
        plan = plan.model_copy(update={"ticket_key": ticket_key})
    return plan


def _codex_plan_instruction(ticket_key: str) -> str:
    return (
        f"Read TICKET.md at the repository root for ticket {ticket_key}. "
        "Inspect the repository as needed so the plan is grounded in the real "
        "codebase (language, frameworks, and files you would change).\n\n"
        "Before planning, check whatever Skills are available to you and open "
        "any whose description matches this ticket's domain (e.g. a "
        "Salesforce, Jira, or framework-specific skill) - installed skills "
        "are not used automatically just by being present; you have to "
        "actually open and follow them. Ground the plan in that guidance "
        "where relevant.\n\n"
        "If TICKET.md has a Subtasks section, the plan must cover every "
        "subtask that isn't already Done or already flagged complete in its "
        "comments - this is one plan for the whole ticket, not just its top "
        "description; skip only the subtasks TICKET.md says are already done.\n\n"
        "If TICKET.md has an Epic section, that is background only - it "
        "exists so you understand the wider goal this ticket serves, not as "
        f"more scope to implement. Plan strictly for ticket {ticket_key} (and "
        "its Subtasks section, if present). Do not plan work for other "
        "stories under the same epic just because the epic mentions them.\n\n"
        "If TICKET.md contains visual reference attachments, inspect every "
        "image in ticket-attachments/ before planning. Replicate each image "
        "exactly as the Salesforce UI using LWC, and treat this as a mandatory "
        "high-priority requirement in the plan and implementation.\n\n"
        "Write a concrete implementation plan to PLAN.json at the repository "
        "root. Do NOT modify any source code, tests, or config. Do NOT commit, "
        "push, or open a pull request. Your only output file is PLAN.json.\n\n"
        f"{PLAN_JSON_INSTRUCTIONS}\n"
        "Be concrete: name files and areas to change. Decide the unspecified "
        "details yourself and record each as an `assumed_default` in an "
        "open_question (or fold it into the steps) — do NOT pad `risks` with "
        "hedging. Everything in the plan must be traceable to TICKET.md and what "
        "you observed in the repo."
    )


def _run_codex_planning_sync(
    settings: Settings,
    ticket_key: str,
    ticket_markdown: str,
    on_event: OnEvent | None = None,
    pause_control=None,
    image_attachments: list[tuple[str, bytes, str]] | None = None,
    *,
    ticket_origin: str = "Jira",
) -> tuple[ImplementationPlan, list[str]]:
    """Push the ticket into a sandbox and have Codex write the plan. Runs off
    the event loop via asyncio.to_thread (see run_codex_planning below), so
    ticket_markdown/image_attachments are fetched by the caller beforehand -
    this function has no event loop to await on."""
    transcript_parts = [f"Fetched {ticket_key} from {ticket_origin}.", "", ticket_markdown]

    with open_pipeline_sandbox(
        settings,
        on_event=on_event,
        codex_model=settings.codex_planning_model,
        codex_reasoning_effort=settings.codex_planning_reasoning_effort,
        phase="planning",
    ) as sandbox:
        if pause_control is not None:
            pause_control.attach_sandbox(sandbox)
        try:
            prepare_sandbox_repo(
                sandbox, settings, ticket_markdown, on_event=on_event, image_attachments=image_attachments
            )
            # User-facing label for the coding agent. The raw engine name
            # (Codex/opencode) is an implementation detail and must never reach
            # events, transcripts, or the UI - see the persona settings in
            # config.py and the AGENTS.md injection in sandbox_runtime.py.
            agent_label = settings.persona_name
            if on_event:
                on_event("step", f"Asking {agent_label} to draft the implementation plan")
            on_stdout = (lambda chunk: on_event("codex_stdout", chunk)) if on_event else None
            on_stderr = (lambda chunk: on_event("codex_stderr", chunk)) if on_event else None
            run_engine = sandbox.run_opencode if settings.engine == "opencode" else sandbox.run_codex
            with timed(on_event, "planning engine exec"):
                result = run_engine(
                    _codex_plan_instruction(ticket_key),
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                )
            transcript_parts.extend(
                [
                    "",
                    f"{agent_label} exit_code: {result['exit_code']}",
                    result.get("stdout") or "",
                    result.get("stderr") or "",
                ]
            )
            plan_file = sandbox.read_file("PLAN.json")
            raw_plan = plan_file.get("content") if plan_file.get("success") else ""
            if not raw_plan.strip() and "{" in (result.get("stdout") or ""):
                # Engine printed the plan to stdout instead of writing the file.
                # Only trust stdout when it actually contains a JSON object -
                # otherwise it's log noise and feeding it to the parser just
                # buries the real error.
                raw_plan = result["stdout"]
            if not raw_plan.strip():
                detail = result.get("stderr") or result.get("stdout") or f"exit {result['exit_code']}"
                raise PlanExtractionError(
                    f"{agent_label} did not write PLAN.json for {ticket_key!r}: {detail}"
                )
            plan = _parse_plan_json(raw_plan, ticket_key, label=agent_label)
        finally:
            if pause_control is not None:
                pause_control.detach_sandbox()

    return plan, transcript_parts


async def run_codex_planning(
    settings: Settings,
    ticket_key: str,
    on_event: OnEvent | None = None,
    pause_control=None,
    ticket_markdown: str | None = None,
    image_attachments: list[tuple[str, bytes, str]] | None = None,
) -> PlanningResult:
    """Push the ticket into a sandbox and have Codex write the plan.

    ticket_markdown/image_attachments are normally fetched live from the
    project's ticket source; pass them in to run purely from stored content
    instead (an imported manual ticket that has no real Jira/Azure issue)."""
    local = ticket_markdown is not None
    if on_event:
        if local:
            on_event("step", f"Using stored content for ticket {ticket_key}")
        else:
            on_event("step", f"Fetching ticket {ticket_key} from Jira")
    if ticket_markdown is None:
        ticket_markdown = await fetch_ticket_markdown(settings, ticket_key)
        image_attachments = await fetch_ticket_image_attachments(settings, ticket_key)
    if pause_control is not None:
        await pause_control.checkpoint()
    origin = "the imported ticket (Jira Group)" if local else "Jira"
    plan, transcript_parts = await asyncio.to_thread(
        _run_codex_planning_sync,
        settings,
        ticket_key,
        ticket_markdown,
        on_event,
        pause_control,
        image_attachments,
        ticket_origin=origin,
    )
    if pause_control is not None:
        await pause_control.checkpoint()
    return PlanningResult(plan=plan, transcript="\n".join(transcript_parts).strip(), usage=UsageTotals())


async def run_execution(agent, instruction: str, on_event: OnEvent | None = None, pause_control=None) -> ExecutionResult:
    input_state = {"messages": [{"role": "user", "content": instruction}]}
    result = await _stream_and_emit(agent, input_state, on_event, pause_control)
    messages = result.get("messages", [])
    summary = as_text(messages[-1].content) if messages else ""

    return ExecutionResult(
        summary=summary,
        pr_url=_extract_pr_url(messages),
        transcript=_transcript(messages),
        usage=_extract_usage(messages),
    )
