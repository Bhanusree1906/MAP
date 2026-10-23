"""Unit tests for the Salesforce validator's pure helpers: sf JSON
extraction, status interpretation, failure rendering, and report text."""

from __future__ import annotations

import json

from agents.pipeline_agent.salesforce_validator import (
    ValidationReport,
    _deploy_error_messages,
    _extract_json,
    _fix_instruction,
    _status_indicates_failure,
    _test_failure_lines,
)


class TestExtractJson:
    def test_clean_object(self) -> None:
        assert _extract_json('{"status": 0, "result": {}}') == {"status": 0, "result": {}}

    def test_leading_and_trailing_noise(self) -> None:
        raw = 'Deploying...\n{"status": 1}\nwarning after json'
        assert _extract_json(raw) == {"status": 1}

    def test_nested_braces_in_strings(self) -> None:
        raw = json.dumps({"a": {"b": [1, 2]}, "c": "}"})
        assert _extract_json(raw)["c"] == "}"

    def test_no_json(self) -> None:
        assert _extract_json("nothing here") is None


class TestStatus:
    def test_numeric(self) -> None:
        assert not _status_indicates_failure({"status": 0})
        assert _status_indicates_failure({"status": 1})

    def test_string_variants(self) -> None:
        assert not _status_indicates_failure({"status": "Ok"})
        assert not _status_indicates_failure({"status": "Succeeded"})
        assert _status_indicates_failure({"status": "Failed"})
        assert _status_indicates_failure({"status": "Error"})

    def test_missing_status_is_failure(self) -> None:
        assert _status_indicates_failure({})


def test_deploy_error_messages_component_failures() -> None:
    payload = {
        "status": 1,
        "result": {
            "details": {
                "componentFailures": [
                    {"fullName": "MyHandler", "problem": "Variable does not exist: accnt", "lineNumber": 12}
                ]
            }
        },
    }
    assert _deploy_error_messages(payload) == ["MyHandler: Variable does not exist: accnt (line 12)"]


def test_deploy_error_messages_top_level_message() -> None:
    assert _deploy_error_messages({"message": "boom"}) == ["boom"]
    assert _deploy_error_messages({}) == []


def test_test_failure_lines_only_fails() -> None:
    result = {
        "tests": [
            {"Outcome": "Pass", "ApexClass": {"Name": "T"}, "MethodName": "ok"},
            {"Outcome": "Fail", "ApexClass": {"Name": "MyTest"}, "MethodName": "bulk", "Message": "assert failed"},
        ]
    }
    assert _test_failure_lines(result) == ["MyTest.bulk: assert failed"]
    assert _test_failure_lines({}) == []


class TestValidationReport:
    def test_passed_requires_all_checks(self) -> None:
        base = dict(deploy_ok=True, tests_passed=True, coverage_ok=True)
        assert ValidationReport(**base).passed
        assert not ValidationReport(**{**base, "coverage_ok": False}).passed
        assert not ValidationReport().passed

    def test_summary_and_markdown_render_failures(self) -> None:
        report = ValidationReport(
            deploy_ok=True,
            tests_passed=True,
            coverage_ok=False,
            min_coverage=90,
            coverage={"MyCls": 72.5},
            checked_classes=["MyCls"],
            failures=["Coverage below bar - MyCls: 72.5% < 90%"],
        )
        markdown = report.to_markdown()
        assert "**FAILED**" in markdown
        assert "`MyCls` - 72.5% (BELOW BAR)" in markdown
        assert "FAILED" in report.summary()

    def test_markdown_pass_case(self) -> None:
        report = ValidationReport(deploy_ok=True, tests_passed=True, coverage_ok=True)
        assert "**PASSED**" in report.to_markdown()

    def test_fix_instruction_mentions_bar_and_branch(self) -> None:
        report = ValidationReport(min_coverage=90, failures=["x"])
        instruction = _fix_instruction(report, None)
        assert "SAME branch" in instruction
        assert "90%" in instruction
        assert "- x" in instruction

    def test_fix_instruction_without_failures(self) -> None:
        instruction = _fix_instruction(ValidationReport(min_coverage=90), None)
        assert "(see report)" in instruction
