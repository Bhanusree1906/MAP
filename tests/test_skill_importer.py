from __future__ import annotations

import pytest

from agents.skills import importer


def test_list_importable_rejects_a_pasted_npx_command() -> None:
    with pytest.raises(ValueError, match="GitHub repository URL"):
        importer.list_importable("npx skills add https://github.com/github/awesome-copilot")


def test_list_importable_parses_windows_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importer,
        "_run_skills_cli",
        lambda args: """Available Skills
Γöé
Γöé    salesforce-apex-quality
Γöé
Γöé      Review Apex code quality.
Γöé
└""",
    )

    assert importer.list_importable("github/awesome-copilot") == [
        importer.ImportCandidate("salesforce-apex-quality", "Review Apex code quality.")
    ]