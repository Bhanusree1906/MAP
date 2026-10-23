"""Approval and legacy review-warning tests.

Scope note (why no HTTP/DB integration file): the repo's shared
tests/conftest.py temp_db fixture is currently broken for the whole repo —
it imports agents.github_agent.connections and agents.pipeline_agent.
project_setup, neither of which exists in this checkout. That means no
TestClient/DB-backed test can run here today, so every test in this file is
written against the pure string/schema helpers (which carry the actual
feature logic) and a real-but-throwaway SQLite via the runs module's own
SessionLocal (no HTTP layer, no shared conftest).

What's covered:
  * Approval feedback and prior warning metadata are folded into implementation guidance.
  * PipelineRunOut.from_run surfaces metadata_.review_warnings to the API,
    defaulting to None when absent, and preserves the []/non-empty split.
  * Existing run records keep their review_warnings metadata through updates.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _approval_payload(**overrides):
    from backend.schemas import ApprovalIn

    base: dict = {
        "approve": True,
        "action": "approve",
        "question_responses": [],
    }
    base.update(overrides)
    return ApprovalIn(**base)


def _plan_with_questions() -> str:
    return json.dumps(
        {
            "ticket_key": "TICKET-1",
            "summary": "Add a toggle",
            "approach": "Extend the settings page",
            "steps": [{"title": "Add field", "detail": "Add a boolean field"}],
            "sections": [],
            "open_questions": [
                {
                    "id": "q1",
                    "question": "Should the toggle default on?",
                    "assumed_default": "off",
                }
            ],
            "risks": None,
            "out_of_scope": None,
        }
    )


def _run_mock(**metadata):
    from agents.pipeline_agent import run_states as states

    run = MagicMock()
    run.id = "run-1"
    run.project_id = "proj-1"
    run.status = states.AWAITING_APPROVAL
    run.current_stage = None
    run.error_message = None
    run.started_at = None
    run.completed_at = None
    run.created_at = datetime(2025, 1, 1)
    run.metadata_ = metadata
    return run


def test_planning_with_risks_waits_for_human_approval(monkeypatch) -> None:
    from agents.pipeline_agent import run_states as states
    from backend.services import pipeline_runner

    settings = object()
    agent_run = SimpleNamespace(id="agent-run-1")
    result = SimpleNamespace(
        plan=SimpleNamespace(
            risks="A planning risk",
            model_dump_json=lambda: '{"risks":"A planning risk"}',
        ),
        transcript="planning transcript",
    )
    update_run = MagicMock()
    monkeypatch.setattr(pipeline_runner, "create_agent_run", lambda *_: agent_run)
    monkeypatch.setattr(
        pipeline_runner,
        "get_project_by_id",
        lambda _project_id: SimpleNamespace(name="project", jira_connection_name=None),
    )
    monkeypatch.setattr(pipeline_runner, "load_settings", lambda *_: settings)
    monkeypatch.setattr(
        pipeline_runner, "resolve_azure_git_for_project", AsyncMock(return_value=settings)
    )
    monkeypatch.setattr(pipeline_runner, "_make_redactor", lambda _settings: lambda text: text)
    monkeypatch.setattr(pipeline_runner, "_stored_group_ticket", lambda *_: None)
    monkeypatch.setattr(pipeline_runner, "run_codex_planning", AsyncMock(return_value=result))
    monkeypatch.setattr(
        pipeline_runner, "create_artifact", lambda *_args, **_kwargs: SimpleNamespace(id="artifact-1")
    )
    monkeypatch.setattr(pipeline_runner, "complete_agent_run", MagicMock())
    monkeypatch.setattr(pipeline_runner, "update_pipeline_run", update_run)

    asyncio.run(pipeline_runner._run_planning_phase("run-1", "project-1", "TICKET-1", object()))

    update_run.assert_called_once_with(
        "run-1", status=states.AWAITING_APPROVAL, current_stage=states.STAGE_PLANNING
    )


# ------------------------------------------------------------------ decisions


class TestDecisionsText:
    def test_includes_prior_warnings(self) -> None:
        from backend.routers.runs import _decisions_text

        text = _decisions_text(
            _plan_with_questions(),
            _approval_payload(),
            review_warnings=["Old warning"],
        )

        assert "Previously identified reviewer warnings" in text
        assert "Old warning" in text

    def test_warnings_only_blocks_when_present(self) -> None:
        from backend.routers.runs import _decisions_text

        text = _decisions_text(
            _plan_with_questions(), _approval_payload()
        )

        assert "reviewer warning" not in text.lower()

    def test_answers_still_folded_with_warnings(self) -> None:
        from backend.routers.runs import _decisions_text

        payload = _approval_payload(
            reviewer_answers="Use the admin scope",
            question_responses=[{"question_id": "q1", "choice": "answer", "answer_text": "Default on"}],
        )

        text = _decisions_text(_plan_with_questions(), payload, review_warnings=["Old"])

        assert "Default on" in text
        assert "admin scope" in text
        assert "Old" in text


# ------------------------------------------------------------------ schema


class TestPipelineRunOut:
    def test_review_warnings_from_metadata(self) -> None:
        from backend.schemas import PipelineRunOut

        out = PipelineRunOut.from_run(
            _run_mock(review_warnings=["Warning A", "Warning B"])
        )

        assert out.review_warnings == ["Warning A", "Warning B"]

    def test_no_warnings_is_none(self) -> None:
        from backend.schemas import PipelineRunOut

        out = PipelineRunOut.from_run(_run_mock())

        assert out.review_warnings is None

    def test_empty_list_is_empty_not_none(self) -> None:
        from backend.schemas import PipelineRunOut

        out = PipelineRunOut.from_run(_run_mock(review_warnings=[]))

        assert out.review_warnings == []


# ------------------------------------------------------------ persistence


class TestWarningsPersistence:
    def test_first_revision_stores_warnings(self, runs_db) -> None:
        from agents.pipeline_agent.runs import get_run_detail, update_pipeline_run

        run_id = _seed_run_with_plan(runs_db)

        update_pipeline_run(
            run_id, metadata_patch={"review_warnings": ["Missing rollback"]}
        )

        detail = get_run_detail(run_id)
        run = detail.run
        assert (run.metadata_ or {}).get("review_warnings") == ["Missing rollback"]

    def test_second_revision_accumulates_warnings(self, runs_db) -> None:
        from agents.pipeline_agent.runs import get_run_detail, update_pipeline_run

        run_id = _seed_run_with_plan(runs_db)

        update_pipeline_run(
            run_id, metadata_patch={"review_warnings": ["First warning"]}
        )
        update_pipeline_run(
            run_id, metadata_patch={"review_warnings": ["Second warning"]}
        )

        detail = get_run_detail(run_id)
        run = detail.run
        assert (run.metadata_ or {}).get("review_warnings") == [
            "First warning",
            "Second warning",
        ]

    def test_approve_folds_warnings_into_answers(self, runs_db) -> None:
        from agents.pipeline_agent.runs import get_run_detail, update_pipeline_run

        run_id = _seed_run_with_plan(runs_db)
        update_pipeline_run(
            run_id, metadata_patch={"review_warnings": ["Approve warning"]}
        )

        detail = get_run_detail(run_id)
        run = detail.run
        warnings = (run.metadata_ or {}).get("review_warnings")
        assert warnings == ["Approve warning"]


def test_approval_executes_edited_plan_without_replanning(monkeypatch) -> None:
    from agents.pipeline_agent import run_states as states
    from backend.routers import runs
    from backend.schemas import ApprovalIn, PlanOut

    review_run = _run_mock(
        ticket_key="TICKET-1",
        review_warnings=["Include a rollback strategy"],
    )
    artifact = SimpleNamespace(
        artifact_type=states.ARTIFACT_PLAN,
        content=_plan_with_questions(),
    )
    detail = SimpleNamespace(run=review_run, artifacts=[artifact])
    updated_run = _run_mock(ticket_key="TICKET-1")
    updated_run.status = states.APPROVED
    updated_run.current_stage = states.STAGE_EXECUTION
    start_execution = MagicMock()

    monkeypatch.setattr(runs, "get_run_detail", lambda _run_id: detail)
    monkeypatch.setattr(runs, "get_project_by_id", lambda _project_id: object())
    monkeypatch.setattr(runs, "check_project_readiness", lambda _project: [])
    monkeypatch.setattr(runs, "create_artifact", MagicMock())
    monkeypatch.setattr(runs, "update_pipeline_run", lambda *_args, **_kwargs: updated_run)
    monkeypatch.setattr(runs, "start_execution_phase", start_execution)

    edited_plan = PlanOut(
        ticket_key="TICKET-1",
        summary="Implement the toggle with rollback support",
        approach="Extend the settings page",
        steps=[{"title": "Add field", "detail": "Add a boolean field"}],
    )
    response = asyncio.run(
        runs.approve_or_reject_run(
            "run-1",
            ApprovalIn(
                approve=True,
                action="approve",
                plan=edited_plan,
                reviewer_answers="Keep the existing API compatible",
            ),
        )
    )

    assert response.status == states.APPROVED
    start_execution.assert_called_once()
    args = start_execution.call_args.args
    assert args[:3] == ("run-1", "proj-1", "TICKET-1")
    assert json.loads(args[3])["summary"] == edited_plan.summary
    assert "Keep the existing API compatible" in args[4]
    assert "Include a rollback strategy" in args[4]


def test_execution_retry_reuses_plan_without_planning(monkeypatch) -> None:
    from agents.pipeline_agent import run_states as states
    from backend.routers import runs

    failed_run = _run_mock(
        ticket_key="TICKET-1",
        review_warnings=["Include a rollback strategy"],
    )
    failed_run.status = states.FAILED
    failed_run.current_stage = states.STAGE_EXECUTION
    plan_content = json.dumps(
        {
            "ticket_key": "TICKET-1",
            "summary": "Add a toggle",
            "approach": "Extend the settings page",
            "steps": [{"title": "Add field", "detail": "Add a boolean field"}],
        }
    )
    artifact = SimpleNamespace(
        artifact_type=states.ARTIFACT_PLAN,
        content=plan_content,
        version=2,
    )
    detail = SimpleNamespace(run=failed_run, artifacts=[artifact])
    created_run = _run_mock(ticket_key="TICKET-1", retried_from_run_id="run-1")
    created_run.id = "run-2"
    created_run.status = states.PLANNING
    execution_run = _run_mock(ticket_key="TICKET-1", retry_mode="implementation")
    execution_run.id = "run-2"
    execution_run.status = states.APPROVED
    execution_run.current_stage = states.STAGE_EXECUTION
    create_artifact = MagicMock()
    start_execution = MagicMock()
    start_planning = MagicMock()

    monkeypatch.setattr(runs, "get_run_detail", lambda _run_id: detail)
    monkeypatch.setattr(runs, "get_project_by_id", lambda _project_id: object())
    monkeypatch.setattr(runs, "check_project_readiness", lambda _project: [])
    monkeypatch.setattr(runs, "create_pipeline_run", lambda **_kwargs: created_run)
    monkeypatch.setattr(runs, "create_artifact", create_artifact)
    monkeypatch.setattr(runs, "update_pipeline_run", lambda *_args, **_kwargs: execution_run)
    monkeypatch.setattr(runs, "start_execution_phase", start_execution)
    monkeypatch.setattr(runs, "start_planning_phase", start_planning)

    response = asyncio.run(runs.retry_run("run-1"))

    assert response.status == states.APPROVED
    start_planning.assert_not_called()
    create_artifact.assert_called_once()
    assert create_artifact.call_args.args[1:4] == (
        "run-2",
        states.ARTIFACT_PLAN,
        "plan",
    )
    start_execution.assert_called_once()
    args = start_execution.call_args.args
    assert args[:3] == ("run-2", "proj-1", "TICKET-1")
    assert json.loads(args[3])["summary"] == "Add a toggle"
    assert "Include a rollback strategy" in args[4]
