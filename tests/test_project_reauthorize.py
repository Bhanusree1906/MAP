"""Tests for the GitHub reauthorization path: a completed project whose
stored OAuth token GitHub has started rejecting (401 on every API/git call)
must be recoverable without DB surgery - `complete` is terminal for the
normal setup endpoint, but force-reentry backs the reauthorize route.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.pipeline_agent import project_setup
from backend.routers import project_setup as project_setup_router


@pytest.fixture()
def client(temp_db, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "secret")
    app = FastAPI()
    app.include_router(project_setup_router.router)
    return TestClient(app)


def _create_project(temp_db, **kwargs) -> str:
    from database.models.project import Project

    with temp_db() as db:
        project = Project(name="reauth-p", repository_url="https://github.com/acme/reauth-p", **kwargs)
        db.add(project)
        db.commit()
        project_id = project.id
        return project_id


def _complete_project(temp_db) -> str:
    from database.models.project import Project
    from datetime import datetime

    project_id = _create_project(
        temp_db,
        setup_status="complete",
        setup_completed_at=datetime.utcnow(),
        setup_github_mode="adopted",
        setup_warning=None,
    )
    return project_id


class TestBeginSetupForce:
    def test_complete_is_terminal_without_force(self, temp_db) -> None:
        project_id = _complete_project(temp_db)
        with pytest.raises(ValueError, match="already_complete"):
            project_setup.begin_setup(project_id, "https://github.com/acme/reauth-p")

    def test_force_reenters_complete_and_clears_completion_fields(self, temp_db) -> None:
        from database.models.project import Project

        project_id = _complete_project(temp_db)
        project = project_setup.begin_setup(
            project_id, "https://github.com/acme/reauth-p", force=True
        )
        assert project.setup_status == "in_progress"
        assert project.setup_completed_at is None
        assert project.setup_github_mode is None
        with temp_db() as db:
            row = db.query(Project).filter(Project.id == project_id).first()
            assert row.setup_status == "in_progress"

    def test_force_still_rejects_in_progress(self, temp_db) -> None:
        from datetime import datetime

        project_id = _create_project(temp_db, setup_status="in_progress", setup_started_at=datetime.utcnow())
        with pytest.raises(ValueError, match="in_progress"):
            project_setup.begin_setup(project_id, "https://github.com/acme/x", force=True)

    def test_force_unknown_project_raises_not_found(self, temp_db) -> None:
        with pytest.raises(ValueError, match="not_found"):
            project_setup.begin_setup("no-such-id", "https://github.com/acme/x", force=True)


class TestReauthorizeEndpoint:
    def test_returns_authorize_url_and_flips_state(self, client: TestClient, temp_db) -> None:
        project_id = _complete_project(temp_db)
        response = client.post(f"/api/projects/{project_id}/setup/github/reauthorize")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["oauth_required"] is True
        assert body["authorize_url"] == f"/api/projects/{project_id}/setup/github/oauth/start"
        assert body["status"] == "in_progress"

    def test_unknown_project_is_404(self, client: TestClient) -> None:
        response = client.post("/api/projects/no-such-id/setup/github/reauthorize")
        assert response.status_code == 404

    def test_regular_setup_endpoint_still_409s_on_complete(self, client: TestClient, temp_db) -> None:
        project_id = _complete_project(temp_db)
        response = client.post(
            f"/api/projects/{project_id}/setup",
            json={"repository_url": "https://github.com/acme/reauth-p"},
        )
        assert response.status_code == 409

    def test_oauth_start_redirects_after_reauthorize(self, client: TestClient, temp_db) -> None:
        # The reauthorize POST flips complete -> in_progress, so the existing
        # oauth/start redirect (which 409s on `complete`) must now work.
        project_id = _complete_project(temp_db)
        client.post(f"/api/projects/{project_id}/setup/github/reauthorize")
        response = client.get(f"/api/projects/{project_id}/setup/github/oauth/start", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert "github.com/login/oauth/authorize" in response.headers["location"]
