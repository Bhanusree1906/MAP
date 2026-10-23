"""Structured plan Codex writes to PLAN.json during the planning phase.

The pipeline reads that file and validates it here so the UI and the
execution orchestrator consume the same shape the human approved.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    title: str = Field(description="Short label for this step.")
    detail: str = Field(description="What this step actually involves.")


class PlanSection(BaseModel):
    title: str = Field(description="Heading for an additional plan section.")
    content: str = Field(description="Content of the additional plan section.")


class OpenQuestion(BaseModel):
    """A decision the planner could NOT make with a safe default, or where the
    choice materially changes the deliverable. Everything else the planner just
    decides and records as an assumption in the steps (WS-7.1)."""

    id: str = Field(description="Stable kebab-case slug, e.g. 'default-priority-value'.")
    question: str = Field(description="The decision to be made, in one sentence.")
    why_it_matters: str = Field(description="What breaks or differs depending on the answer.")
    assumed_default: str = Field(
        description="What the implementation will do if this is left unanswered. REQUIRED."
    )
    blocking: bool = Field(
        default=False,
        description="True only when there is no safe default at all (e.g. an org "
        "prerequisite the ticket needs but the org lacks).",
    )


class AddressedWarning(BaseModel):
    """Legacy plan field accepted for compatibility with saved plan artifacts."""

    warning: str = Field(description="The original warning text from the reviewer.")
    resolution: str = Field(description="How the plan addresses this warning.")


class ImplementationPlan(BaseModel):
    ticket_key: str = Field(description="The Jira issue key this plan is for, e.g. ENG-123.")
    summary: str = Field(description="One or two sentence summary of what the ticket asks for.")
    approach: str = Field(description="The overall implementation approach in prose.")
    steps: list[PlanStep] = Field(description="Ordered concrete implementation steps.")
    sections: list[PlanSection] = Field(default_factory=list, description="Additional approved plan sections.")
    open_questions: list[OpenQuestion] = Field(
        default_factory=list,
        description="Decisions that genuinely need the reviewer. Empty for most tickets.",
    )
    risks: str | None = Field(
        default=None,
        description="FYI prose only — heads-up notes for the implementer. NOT a place "
        "for questions or hedging; use open_questions or just decide.",
    )
    out_of_scope: str | None = Field(default=None, description="What this plan deliberately will not do.")
    addressed_warnings: list[AddressedWarning] = Field(
        default_factory=list,
        description="Legacy field retained for previously saved plan artifacts.",
    )


class PlanExtractionError(RuntimeError):
    """Raised when Codex did not produce a usable PLAN.json."""


PLAN_JSON_INSTRUCTIONS = """\
PLAN.json must be valid JSON (no markdown fences) with this exact shape:
{
  "ticket_key": "the Jira issue key",
  "summary": "one or two sentences",
  "approach": "overall implementation approach",
  "steps": [{"title": "short label", "detail": "what this step involves"}],
  "sections": [{"title": "additional heading", "content": "additional section content"}],
  "open_questions": [
    {"id": "kebab-slug", "question": "one sentence", "why_it_matters": "...",
     "assumed_default": "what we will do if unanswered", "blocking": false}
  ],
  "risks": "optional FYI prose for the implementer, or null",
  "out_of_scope": "optional string or null"
}

MAKE THE CALL. Raise an open_question ONLY when there is no reasonable default,
or when the choice materially changes what gets built. For everything else, pick
the conventional safe option (a new picklist's default = its first/safest value;
a new field is optional unless the ticket says required; users get read access
unless the ticket says edit), state it in `assumed_default`, and reflect it in
the steps. "Retrieve current metadata before editing" is a normal step, not a
question. Set "blocking": true only when the ticket needs something the org does
not have (a picklist value, a dependency component) and you cannot proceed
without it. Most tickets should have open_questions == [].

"""


def plan_to_text(plan: ImplementationPlan, reviewer_answers: str | None = None) -> str:
    """Render a plan as the human-readable text written into TICKET.md
    for execution and shown in the CLI - shared by the CLI and the
    backend so both feed Codex the same wording for what was approved.

    reviewer_answers, if given, is whatever the human approver wrote when
    resolving plan.risks at approval time (see backend/routers/runs.py) -
    without it, risks/out_of_scope were previously shown for approval but
    silently dropped from what execution actually reads.
    """
    steps = "\n".join(f"- {step.title}: {step.detail}" for step in plan.steps)
    parts = [plan.summary, plan.approach, steps]
    parts.extend(f"{section.title}:\n{section.content}" for section in plan.sections if section.content)
    if plan.open_questions:
        lines = ["Decisions made for unspecified details (implement these):"]
        for q in plan.open_questions:
            lines.append(f"- {q.question} → {q.assumed_default}")
        parts.append("\n".join(lines))
    if plan.risks:
        parts.append(f"Heads-up notes:\n{plan.risks}")
    if reviewer_answers:
        parts.append(
            f"Reviewer's answers (these override the assumed defaults above):\n{reviewer_answers}"
        )
    if plan.out_of_scope:
        parts.append(f"Out of scope:\n{plan.out_of_scope}")
    return "\n\n".join(part for part in parts if part)
