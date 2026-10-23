from __future__ import annotations

from agents.pipeline_agent.config import needs_azure_git_resolution
from agents.pipeline_agent.pr import _azure_repo_name


class _Settings:
    def __init__(self, git_provider: str, repository_url: str) -> None:
        self.git_provider = git_provider
        self.repository_url = repository_url


def test_azure_git_with_a_stale_github_url_is_resolved_before_sandbox_start() -> None:
    assert needs_azure_git_resolution(
        _Settings("azure", "https://github.com/acme/old-repository.git")
    )


def test_azure_pull_request_uses_repository_name_not_clone_suffix() -> None:
    assert _azure_repo_name("https://dev.azure.com/acme/support/_git/case-flow.git") == "case-flow"