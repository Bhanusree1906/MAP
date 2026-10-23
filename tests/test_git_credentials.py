from types import SimpleNamespace

from agents.pipeline_agent import credentials


def test_linked_github_connection_wins_over_legacy_token(monkeypatch):
    project = SimpleNamespace(github_connection_name="work")
    linked = SimpleNamespace(access_token="fresh-token")
    legacy = SimpleNamespace(access_token="stale-token")

    monkeypatch.setattr(credentials, "get_project_by_id", lambda _: project)
    monkeypatch.setattr(credentials, "get_git_token", lambda _: legacy)
    monkeypatch.setattr(
        "agents.pipeline_agent.github_credentials.get_github_connection",
        lambda name="default": linked if name == "work" else None,
    )

    assert credentials.get_effective_git_token("project") == "fresh-token"


def test_missing_linked_github_connection_does_not_fall_back(monkeypatch):
    project = SimpleNamespace(github_connection_name="missing")
    legacy = SimpleNamespace(access_token="stale-token")

    monkeypatch.setattr(credentials, "get_project_by_id", lambda _: project)
    monkeypatch.setattr(credentials, "get_git_token", lambda _: legacy)
    monkeypatch.setattr(
        "agents.pipeline_agent.github_credentials.get_github_connection",
        lambda name="default": None,
    )

    assert credentials.get_effective_git_token("project") is None
