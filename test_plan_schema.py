"""ImplementationPlan: typed open_questions + backward compatibility (WS-7.1)."""

from __future__ import annotations

from agents.pipeline_agent.schemas import (
    ImplementationPlan,
    OpenQuestion,
    PLAN_JSON_INSTRUCTIONS,
    plan_to_text,
)


def _plan(**overrides):
    base = dict(
        ticket_key="AG-1",
        summary="Add a picklist field.",
        approach="One field.",
        steps=[{"title": "add field", "detail": "create the meta xml"}],
    )
    base.update(overrides)
    return ImplementationPlan(**base)


def test_open_questions_default_empty():
    assert _plan().open_questions == []


def test_old_plan_json_without_open_questions_still_parses():
    old = ImplementationPlan.model_validate_json(
        '{"ticket_key":"AG-2","summary":"s","approach":"a","steps":[],"risks":"heads up"}'
    )
    assert old.open_questions == []
    assert old.risks == "heads up"


def test_plan_to_text_renders_assumed_defaults_not_hedging():
    plan = _plan(
        open_questions=[
            OpenQuestion(
                id="default-priority-value",
                question="What is the default Lead_Priority__c value?",
                why_it_matters="Determines which Leads show in the list view.",
                assumed_default="Normal",
            )
        ]
    )
    text = plan_to_text(plan)
    assert "What is the default Lead_Priority__c value? → Normal" in text
    assert "Decisions made for unspecified details" in text


def test_reviewer_answers_are_marked_as_overriding_defaults():
    plan = _plan(
        open_questions=[
            OpenQuestion(id="q1", question="edit or read?", why_it_matters="FLS", assumed_default="read")
        ]
    )
    text = plan_to_text(plan, reviewer_answers="Sales users get edit access.")
    assert "override the assumed defaults" in text
    assert "Sales users get edit access." in text


def test_instructions_tell_the_planner_to_decide():
    assert "MAKE THE CALL" in PLAN_JSON_INSTRUCTIONS
    assert "open_questions" in PLAN_JSON_INSTRUCTIONS
    assert '"blocking": true' in PLAN_JSON_INSTRUCTIONS


class TestApprovalRouting:
    def test_resolved_action_falls_back_to_approve_bool(self):
        from backend.schemas import ApprovalIn

        assert ApprovalIn(approve=True).resolved_action() == "approve"
        assert ApprovalIn(approve=False).resolved_action() == "reject"

    def test_revise_action_is_not_supported(self):
        from pydantic import ValidationError

        from backend.schemas import ApprovalIn

        try:
            ApprovalIn(approve=True, action="revise")
        except ValidationError:
            return
        raise AssertionError("The approval API must not accept a revise action")

    def test_decisions_text_renders_answers_and_defaults(self):
        import json as _json

        from backend.routers.runs import _decisions_text
        from backend.schemas import ApprovalIn, QuestionResponse

        plan = _json.dumps(
            {
                "open_questions": [
                    {"id": "q1", "question": "Default value?", "assumed_default": "Normal"},
                    {"id": "q2", "question": "Editable?", "assumed_default": "read-only"},
                ]
            }
        )
        payload = ApprovalIn(
            approve=True,
            action="approve",
            question_responses=[
                QuestionResponse(question_id="q1", choice="answer", answer_text="High"),
                QuestionResponse(question_id="q2", choice="default"),
            ],
        )
        out = _decisions_text(plan, payload)
        assert "Default value?" in out and "ANSWER: High" in out
        assert "Editable?" in out and "planner's default: read-only" in out
