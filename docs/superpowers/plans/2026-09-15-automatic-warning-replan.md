# Automatic Warning Re-plan Implementation Plan

> **For agentic workers:** Inline execution in this session.

**Goal:** Automatically turn planner warnings into a re-plan before the first human approval gate, then show the revised plan for approval and execution.

**Architecture:** Reuse `ImplementationPlan.risks` as the planner-produced warning source. The initial planning worker persists non-empty risk lines, transitions the run to `REPLANNING`, and invokes the existing re-plan worker with those warnings. The revised plan returns to `AWAITING_APPROVAL`; the existing stages and approval action then drive implementation, validation, and PR creation.

**Tech Stack:** Python, FastAPI background tasks, Pydantic, React/TypeScript, pytest.

**Spec:** User request in the active conversation.

## Global Constraints

- Do not add a new warning service or dependency.
- Preserve manual reviewer warnings as an additive path.
- Keep human approval before sandbox writes and execution.
- Automatic warning re-plan must be bounded by the existing re-plan limit.

### Task 1: Automatic warning extraction and transition

**Files:**
- Modify: `backend/services/pipeline_runner.py`
- Test: `tests/test_replan_warnings.py`

- [ ] Extract non-empty lines from the initial plan's `risks` field.
- [ ] Persist them as `review_warnings`, increment `replan_count`, set `REPLANNING`, and start the existing re-plan worker.
- [ ] Leave plans without risks on the existing `AWAITING_APPROVAL` path.
- [ ] Test extraction and that the warning text is available to the re-plan prompt.

### Task 2: UI wording for the automatic cycle

**Files:**
- Modify: `frontend/src/pages/RunDetailPage.tsx`

- [ ] Label persisted warnings as automatic planning warnings when they came from the initial plan.
- [ ] Keep revised plan steps visible at the approval gate and retain the approval action for execution.

### Task 3: Validation

- [ ] Run the focused warning tests.
- [ ] Run the frontend typecheck/build.
- [ ] Run diagnostics for touched files.
