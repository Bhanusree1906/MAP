# Re-plan Based on Approval Warnings — Implementation Plan

## Goal

Add a "warnings" concept to the plan approval gate so that reviewers can flag issues about the plan without fully rejecting or manually editing it. Warnings are fed back into the replanning process as additional context, and the revised plan goes back to human review. This loops until the reviewer is satisfied.

## Architecture

The existing plan-approval flow has: AWAITING_APPROVAL → (approve | revise | reject). "revise" re-plans from scratch by feeding question responses and reviewer notes to Codex. This plan adds:

1. A `warnings` field on the approval payload (list of strings from the reviewer)
2. Warnings are accumulated across replan rounds in `PipelineRun.metadata_["review_warnings"]`
3. Warnings are injected into `_decisions_text()` and consumed by `_replan_instruction()` so Codex incorporates them into PLAN.json
4. On final approval, accumulated warnings are folded into `reviewer_answers` so the implementer sees them
5. Frontend shows a warnings textarea on the approval gate, displays accumulated warnings, and enables "Revise plan" whenever there's content to send (warnings, plan edits, or reviewer answers)

## Files to Modify

| File | Change |
|---|---|
| `agents/pipeline_agent/runs.py` | Add `metadata_patch` param to `update_pipeline_run` |
| `backend/schemas.py` | Add `warnings: list[str]` to `ApprovalIn`, add `review_warnings` to `PipelineRunOut` |
| `backend/routers/runs.py` | Store/accumulate warnings, pass to replan, fold into approval answers |
| `agents/pipeline_agent/runner.py` | Update `_replan_instruction` prompt to include warnings |
| `frontend/src/api/types.ts` | Add `review_warnings?: string[]` to the Run/Detail types |
| `frontend/src/api/client.ts` | Add `warnings` to the `approveRun` payload type |
| `frontend/src/pages/RunDetailPage.tsx` | Add warnings textarea, show accumulated warnings, enable revise button |
| `frontend/src/index.css` | Style for warnings banner |
| `tests/test_replan_warnings.py` (new) | Unit tests for the warning accumulation and replan flow |

---

## Task 1: Backend — Add metadata_patch to update_pipeline_run

**Files:**
- Modify: `agents/pipeline_agent/runs.py:63-80`

The existing `update_pipeline_run()` does not support patching `metadata_`. Add a `metadata_patch` keyword parameter so callers can merge keys without overwriting the entire dict.

- [ ] **Step 1: Add metadata_patch parameter**

```python
def update_pipeline_run(
    run_id: str,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
    metadata_patch: dict | None = None,
) -> PipelineRun | None:
    _ensure_schema()
    with SessionLocal() as db:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is None:
            return None
        if status is not None:
            run.status = status
        if current_stage is not None:
            run.current_stage = current_stage
        if error_message is not None:
            run.error_message = error_message
        if metadata_patch:
            run.metadata_ = {**(run.metadata_ or {}), **metadata_patch}
        if completed:
            run.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run
```

- [ ] **Step 2: Verify no existing callers break**

All existing callers of `update_pipeline_run` pass no `metadata_patch`, so the new param defaults to `None` and is a no-op. No migration needed.

- [ ] **Step 3: Commit**

---

## Task 2: Backend — Extend ApprovalIn schema and PipelineRunOut with warnings

**Files:**
- Modify: `backend/schemas.py:352-376` (ApprovalIn)
- Modify: `backend/schemas.py:295-325` (PipelineRunOut)

- [ ] **Step 1: Add `warnings` field to ApprovalIn**

```python
class ApprovalIn(BaseModel):
    approve: bool
    action: Literal["approve", "revise", "reject"] | None = None
    rejection_reason: str | None = None
    plan: PlanOut | None = None
    question_responses: list[QuestionResponse] = []
    reviewer_answers: str | None = None
    escalate_to_jira: bool = False
    # NEW: reviewer-identified issues that must be addressed before execution
    warnings: list[str] = []

    def resolved_action(self) -> str:
        if self.action:
            return self.action
        return "approve" if self.approve else "reject"
```

- [ ] **Step 2: Add `review_warnings` to PipelineRunOut**

```python
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
    review_warnings: list[str] | None = None  # NEW

    @classmethod
    def from_run(cls, run) -> "PipelineRunOut":
        meta = getattr(run, "metadata_", None) or {}
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
            review_warnings=meta.get("review_warnings") if isinstance(meta, dict) else None,
        )
```

- [ ] **Step 3: Commit**

---

## Task 3: Backend — Wire warnings into the approval endpoint and replan flow

**Files:**
- Modify: `backend/routers/runs.py:198-290`

This is the core backend task. Three changes in `approve_or_reject_run()`:

1. **On REVISE:** store warnings in metadata, pass them through `_decisions_text()` to replan
2. **On APPROVE:** fold accumulated warnings into reviewer_answers for execution
3. **`_decisions_text()`** helper: include warnings in the decisions text

- [ ] **Step 1: Update `_decisions_text()` to accept and render warnings**

```python
def _decisions_text(plan_content: str, payload: ApprovalIn, review_warnings: list[str] | None = None) -> str:
    """Render the reviewer's per-question responses (+ any free note + warnings)
    as the text the re-plan / execution consumes."""
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
    # Accumulated warnings from previous rounds
    if review_warnings:
        lines.append("\nReviewer warnings that must be addressed in the revised plan:")
        for i, warning in enumerate(review_warnings, 1):
            lines.append(f"  {i}. {warning}")
    # New warnings from this round
    if payload.warnings:
        lines.append("\nNew reviewer warnings for this revision:")
        for i, warning in enumerate(payload.warnings, 1):
            lines.append(f"  {i}. {warning}")
    return "\n".join(lines) or "(no changes requested)"
```

- [ ] **Step 2: Update the REVISE path to store/accumulate warnings and pass them to replan**

```python
    if action == "revise":
        if len(plan_artifacts) > _MAX_REPLAN_ROUNDS:
            raise HTTPException(
                status_code=409,
                detail=f"Plan has already been revised {_MAX_REPLAN_ROUNDS} times — approve it or reject it.",
            )
        # Accumulate warnings: merge existing + new
        existing_warnings = (detail.run.metadata_ or {}).get("review_warnings", [])
        all_warnings = list(existing_warnings)
        if payload.warnings:
            all_warnings.extend(payload.warnings)

        decisions = _decisions_text(plan_artifact.content, payload, all_warnings if all_warnings else None)

        if payload.plan is not None:
            create_artifact(
                detail.run.project_id, run_id, states.ARTIFACT_PLAN, "plan",
                plan_json, "reviewer_edit",
            )
        run = update_pipeline_run(
            run_id,
            status=states.REPLANNING,
            current_stage=states.STAGE_PLANNING,
            metadata_patch={"review_warnings": all_warnings} if all_warnings else None,
        )
        start_replanning_phase(run_id, detail.run.project_id, ticket_key, plan_json, decisions)
        return PipelineRunOut.from_run(run)
```

- [ ] **Step 3: Update the APPROVE path to fold accumulated warnings into reviewer_answers**

```python
    # action == "approve"
    project = get_project_by_id(detail.run.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    readiness_issues = check_project_readiness(project)
    if readiness_issues:
        raise HTTPException(
            status_code=400,
            detail=f"Project isn't ready for execution: {'; '.join(readiness_issues)}",
        )

    if payload.plan is not None and plan_json != plan_artifact.content:
        create_artifact(
            detail.run.project_id, run_id, states.ARTIFACT_PLAN, "plan", plan_json, "reviewer_edit"
        )

    # Fold question responses AND accumulated warnings into reviewer_answers
    answers = payload.reviewer_answers
    if payload.question_responses:
        existing_warnings = (detail.run.metadata_ or {}).get("review_warnings", [])
        answers = _decisions_text(plan_artifact.content, payload, existing_warnings or None)
    elif (detail.run.metadata_ or {}).get("review_warnings"):
        # No question responses, but warnings exist — fold them into answers
        warning_header = "Reviewer warnings (must be addressed during implementation):\n"
        warning_list = "\n".join(f"  {i+1}. {w}" for i, w in enumerate(detail.run.metadata_["review_warnings"]))
        answers = f"{warning_header}{warning_list}" + (f"\n\n{answers}" if answers else "")

    run = update_pipeline_run(run_id, status=states.APPROVED, current_stage=states.STAGE_EXECUTION)
    start_execution_phase(run_id, detail.run.project_id, ticket_key, plan_json, answers)
    return PipelineRunOut.from_run(run)
```

- [ ] **Step 4: Commit**

---

## Task 4: Backend — Update replan instruction prompt to explicitly address warnings

**Files:**
- Modify: `agents/pipeline_agent/runner.py:201-215`

The current `_replan_instruction()` mentions open questions. Update it to explicitly call out that warnings must be addressed:

- [ ] **Step 1: Update _replan_instruction**

```python
def _replan_instruction(ticket_key: str, decisions: str) -> str:
    """The REVISE prompt. The current PLAN.json is already in the
    sandbox — edit it, do not re-derive from scratch."""
    return (
        f"PLAN.json for ticket {ticket_key} is already at the repository root, "
        "along with TICKET.md. The reviewer has reviewed the plan and provided "
        "feedback including resolved open questions and/or warnings about the plan:\n\n"
        f"{decisions}\n\n"
        "Produce a REVISED PLAN.json that already incorporates every decision "
        "and addresses every warning into `approach` and `steps` — the steps "
        "must reflect the decisions and warnings concretely. Remove every "
        "open_question the reviewer resolved. Add a NEW open_question only if "
        "a resolution genuinely surfaced a new unknown with no safe default. "
        "If the reviewer raised warnings about the plan, the revised plan must "
        "fix the issues they identified — do not ignore warnings. Edit the "
        "existing plan; do not re-derive it. Do NOT modify source, tests, or "
        f"config; PLAN.json is your only output.\n\n"
        f"{PLAN_JSON_INSTRUCTIONS}"
    )
```

- [ ] **Step 2: Commit**

---

## Task 5: Frontend — Add `review_warnings` to types and API client

**Files:**
- Modify: `frontend/src/api/types.ts` (PipelineRun interface area)
- Modify: `frontend/src/api/client.ts` (approveRun payload type)

- [ ] **Step 1: Add review_warnings to the RunDetailOut type in types.ts**

The `PipelineRun` type is used for the run list. The detail page uses `RunDetailOut` which contains `run: PipelineRun`. Find the PipelineRun interface and add:

```typescript
export interface PipelineRun {
  id: string;
  project_id: string;
  status: PipelineRunStatus;
  current_stage: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  ticket_key?: string | null;
  retried_from_run_id?: string | null;
  pr_url?: string | null;
  total_cost?: string | null;
  review_warnings?: string[] | null;  // NEW
}
```

- [ ] **Step 2: Add warnings to the approveRun payload in client.ts**

```typescript
approveRun: (
  id: string,
  payload: {
    approve: boolean;
    action?: "approve" | "revise" | "reject";
    plan?: Plan;
    rejection_reason?: string;
    question_responses?: QuestionResponse[];
    reviewer_answers?: string;
    escalate_to_jira?: boolean;
    warnings?: string[];  // NEW
  },
) => post<PipelineRun>(`/pipeline-runs/${id}/approval`, payload),
```

- [ ] **Step 3: Commit**

---

## Task 6: Frontend — Add warnings UI to the approval gate on RunDetailPage

**Files:**
- Modify: `frontend/src/pages/RunDetailPage.tsx` (lines 188-700 area)
- Modify: `frontend/src/index.css`

This is the main frontend task. Changes:

1. Add `reviewWarnings` state (textarea for new warnings)
2. Show accumulated warnings from previous rounds
3. Enable "Revise plan" button whenever there are warnings OR plan is edited
4. Pass `warnings` array in the `respond()` call

- [ ] **Step 1: Add `reviewWarnings` state variable**

After `rejectionReason` (line 195), add:

```typescript
const [reviewWarnings, setReviewWarnings] = useState("");
```

- [ ] **Step 2: Reset reviewWarnings when runId changes**

In the existing `useEffect` at line 253-260, add `setReviewWarnings("")`:

```typescript
useEffect(() => {
  setApprovalPlan(null);
  setLiveEvents([]);
  setStuckToBottom(true);
  setReviewWarnings("");  // NEW
  historyLoadedRef.current = null;
  lastSeqRef.current = 0;
  activeAgentRunIdRef.current = null;
}, [runId]);
```

- [ ] **Step 3: Update the `respond()` function to include warnings**

In `respond()` (lines 356-388), update the API call to include warnings when revising:

```typescript
const respond = async (
  action: "approve" | "revise" | "reject",
  escalateToJira = false,
) => {
  setApproving(true);
  setActionError(null);
  try {
    const warningsToSend =
      action === "revise" && reviewWarnings.trim()
        ? reviewWarnings
            .split("\n")
            .map((w) => w.trim())
            .filter(Boolean)
        : [];
    await api.approveRun(runId, {
      approve: action === "approve",
      action,
      plan:
        (action === "approve" || action === "revise") && approvalPlan
          ? approvalPlan
          : undefined,
      rejection_reason:
        action === "reject" && !escalateToJira && rejectionReason.trim()
          ? rejectionReason.trim()
          : undefined,
      reviewer_answers:
        action !== "reject" && reviewerAnswers.trim()
          ? reviewerAnswers.trim()
          : undefined,
      escalate_to_jira: escalateToJira,
      warnings: warningsToSend.length > 0 ? warningsToSend : undefined,
    });
    setRejectOpen(false);
    setRejectionReason("");
    setReviewWarnings("");
    await refetch();
  } catch (e) {
    setActionError(formatError(e));
  } finally {
    setApproving(false);
  }
};
```

- [ ] **Step 4: Show accumulated warnings from previous rounds in the gate**

In the gate section, after the `plan?.risks` block (around line 659), add a warnings banner:

```tsx
{run.review_warnings && run.review_warnings.length > 0 && (
  <div className="rd-gate-warnings">
    <div className="rd-gate-warnings-header">
      <Svg d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <span>Previous Review Warnings</span>
    </div>
    <ul>
      {run.review_warnings.map((warning, i) => (
        <li key={i}>{warning}</li>
      ))}
    </ul>
  </div>
)}
```

- [ ] **Step 5: Add warnings textarea to the gate fields**

In the `rd-gate-fields` section (after `reviewerAnswers` textarea, before `rejectOpen`), add:

```tsx
<label className="field">
  Review warnings (revise the plan to address these)
  <textarea
    value={reviewWarnings}
    onChange={(e) => setReviewWarnings(e.target.value)}
    rows={3}
    placeholder={"One warning per line, e.g.:\nStep 3 assumes access to admin API but ticket doesn't mention permissions\nMissing rollback strategy for the schema migration"}
  />
</label>
```

- [ ] **Step 6: Enable "Revise plan" button whenever warnings OR plan edits exist**

Change the gate buttons section. Currently `planEdited` gates the Revise button. Change to:

```tsx
{(planEdited || reviewWarnings.trim().length > 0) && (
  <button
    type="button"
    className="rd-btn violet"
    disabled={approving}
    onClick={() => respond("revise")}
    title="Re-plan incorporating your warnings and/or plan edits"
  >
    <Svg d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
    Revise plan
  </button>
)}
```

- [ ] **Step 7: Add CSS for the warnings banner**

Add to `index.css`:

```css
.rd-gate-warnings {
  background: #fef3cd;
  border: 1px solid #ffc107;
  border-radius: 8px;
  padding: 12px 14px;
  margin: 12px 0;
}

.rd-gate-warnings-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13px;
  color: #856404;
  margin-bottom: 8px;
}

.rd-gate-warnings-header svg {
  color: #856404;
  flex-shrink: 0;
}

.rd-gate-warnings ul {
  margin: 0;
  padding-left: 18px;
}

.rd-gate-warnings li {
  font-size: 13px;
  color: #664d03;
  line-height: 1.5;
}
```

- [ ] **Step 8: Commit**

---

## Task 7: Tests — Unit tests for the warnings flow

**Files:**
- Create: `tests/test_replan_warnings.py`

Test the three paths: revise with warnings, approve with accumulated warnings, and that warnings accumulate across rounds.

- [ ] **Step 1: Write the test file**

```python
"""Tests for the review-warnings feature: warnings accumulate across replan
rounds and are folded into reviewer_answers on final approval."""

import json
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.pipeline_agent import run_states as states


@pytest.fixture()
def app():
    from backend.routers.runs import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def _make_run(client, project_id="proj-1", ticket_key="TICKET-1"):
    """Create a pipeline run and return its id."""
    resp = client.post("/api/pipeline-runs", json={"project_id": project_id, "ticket_key": ticket_key})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _get_run(client, run_id):
    resp = client.get(f"/api/pipeline-runs/{run_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve(client, run_id, **overrides):
    payload = {
        "approve": False,
        "action": "revise",
        "warnings": [],
        **overrides,
    }
    resp = client.post(f"/api/pipeline-runs/{run_id}/approval", json=payload)
    return resp


def test_warnings_stored_in_metadata(client):
    """When a reviewer submits warnings via 'revise', they are stored in metadata."""
    # This test requires a run in AWAITING_APPROVAL with a plan artifact.
    # Mock the prerequisite calls to isolate the approval logic.
    with patch("backend.routers.runs.get_run_detail") as mock_detail, \
         patch("backend.routers.runs.update_pipeline_run") as mock_update, \
         patch("backend.routers.runs.start_replanning_phase"):
        mock_run = type("Run", (), {
            "id": "run-1", "status": states.AWAITING_APPROVAL,
            "project_id": "proj-1", "metadata_": {}, "completed_at": None,
        })()
        mock_artifact = type("Artifact", (), {
            "artifact_type": states.ARTIFACT_PLAN,
            "content": json.dumps({"open_questions": [], "steps": [], "approach": "", "summary": "", "ticket_key": "T1"}),
        })()
        mock_detail.return_value = type("Detail", (), {
            "run": mock_run,
            "artifacts": [mock_artifact],
            "agent_runs": [],
            "cost_logs": [],
        })()
        mock_update.return_value = mock_run

        resp = _approve(client, "run-1", warnings=["Missing rollback step", "Step 3 assumes admin access"])
        assert resp.status_code == 200, resp.text
        # Verify metadata_patch was passed with the warnings
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("metadata_patch") == {"review_warnings": ["Missing rollback step", "Step 3 assumes admin access"]}


def test_warnings_accumulate_across_rounds(client):
    """Second revision appends new warnings to existing ones."""
    with patch("backend.routers.runs.get_run_detail") as mock_detail, \
         patch("backend.routers.runs.update_pipeline_run") as mock_update, \
         patch("backend.routers.runs.start_replanning_phase"):
        mock_run = type("Run", (), {
            "id": "run-1", "status": states.AWAITING_APPROVAL,
            "project_id": "proj-1", "metadata_": {"review_warnings": ["First warning"]},
            "completed_at": None,
        })()
        mock_artifact = type("Artifact", (), {
            "artifact_type": states.ARTIFACT_PLAN,
            "content": json.dumps({"open_questions": [], "steps": [], "approach": "", "summary": "", "ticket_key": "T1"}),
        })()
        mock_detail.return_value = type("Detail", (), {
            "run": mock_run,
            "artifacts": [mock_artifact],
            "agent_runs": [],
            "cost_logs": [],
        })()
        mock_update.return_value = mock_run

        resp = _approve(client, "run-1", warnings=["Second warning"])
        assert resp.status_code == 200, resp.text
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("metadata_patch") == {
            "review_warnings": ["First warning", "Second warning"]
        }


def test_review_warnings_appears_in_run_output(client):
    """PipelineRunOut.from_run includes review_warnings from metadata."""
    from backend.schemas import PipelineRunOut
    from unittest.mock import MagicMock

    run = MagicMock()
    run.id = "run-1"
    run.project_id = "proj-1"
    run.status = "awaiting_approval"
    run.current_stage = None
    run.error_message = None
    run.started_at = None
    run.completed_at = None
    run.created_at = None
    run.metadata_ = {"ticket_key": "T1", "review_warnings": ["warning A"]}

    out = PipelineRunOut.from_run(run)
    assert out.review_warnings == ["warning A"]


def test_decisions_text_includes_warnings():
    """_decisions_text renders accumulated and new warnings."""
    from backend.routers.runs import _decisions_text
    from backend.schemas import ApprovalIn

    plan_content = json.dumps({"open_questions": []})
    payload = ApprovalIn(approve=False, action="revise", warnings=["New warning here"])
    text = _decisions_text(plan_content, payload, review_warnings=["Old warning"])
    assert "Old warning" in text
    assert "New warning here" in text
    assert "Reviewer warnings that must be addressed" in text
    assert "New reviewer warnings for this revision" in text


def test_decisions_text_no_warnings_unchanged():
    """Without warnings, _decisions_text output is unchanged."""
    from backend.routers.runs import _decisions_text
    from backend.schemas import ApprovalIn

    plan_content = json.dumps({"open_questions": []})
    payload = ApprovalIn(approve=False, action="revise")
    text = _decisions_text(plan_content, payload)
    assert "warnings" not in text.lower()


def test_replan_instruction_mentions_warnings():
    """The replan prompt explicitly tells Codex to address warnings."""
    from agents.pipeline_agent.runner import _replan_instruction
    instruction = _replan_instruction("TICKET-1", "decisions here\nwarning: step 3 is wrong")
    assert "warning" in instruction.lower()
    assert "addresses every warning" in instruction.lower()
```

- [ ] **Step 2: Run the tests**

```bash
cd D:\Appstrail\Agentic-Pipeline-Demo\Agentic-Pipeline
python -m pytest tests/test_replan_warnings.py -v
```

- [ ] **Step 3: Commit**

---

## Task 8: End-to-end verification

- [ ] **Step 1: Run the full test suite to check for regressions**

```bash
cd D:\Appstrail\Agentic-Pipeline-Demo\Agentic-Pipeline
python -m pytest tests/ -v
```

- [ ] **Step 2: Run the frontend type check**

```bash
cd D:\Appstrail\Agentic-Pipeline-Demo\Agentic-Pipeline\frontend
npx tsc -b
npx oxlint src/pages/RunDetailPage.tsx
```

- [ ] **Step 3: Manual smoke test**

1. Start the backend and frontend
2. Create a run for a ticket → it enters AWAITING_APPROVAL
3. Type warnings in the new textarea → click "Revise plan"
4. Verify the run goes to REPLANNING → AWAITING_APPROVAL
5. Verify the previously-entered warnings appear in the "Previous Review Warnings" banner
6. Approve → verify the warnings are folded into reviewer_answers in the plan text
7. Verify execution starts normally

---

## Global Constraints

- No new database tables. All new state lives in `PipelineRun.metadata_["review_warnings"]`.
- No new dependencies.
- The existing `_MAX_REPLAN_ROUNDS = 2` limit is preserved; warnings don't increase the cap.
- Backward compatible: `review_warnings` and `warnings` default to empty/None.
- No changes to `run_states.py` state machine — the same REPLANNING state is reused.
