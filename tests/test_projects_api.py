"""API tests for /api/projects - regression coverage for the Internal
Server Error caused by dangling jira/salesforce connection references
(SQLite FK IntegrityError bubbling out as an unhandled 500).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import projects as projects_router


@pytest.fixture()
def client(temp_db) -> TestClient:
    app = FastAPI()
    app.include_router(projects_router.router)
    return TestClient(app)


def _seed_jira(temp_db, name: str, project_key: str = "PGD") -> None:
    from database.models.jira_connection import JiraConnection

    with temp_db() as db:
        db.add(
            JiraConnection(
                name=name,
                project_key=project_key,
                site_url="https://x.atlassian.net",
                jira_email="a@b.c",
            )
        )
        db.commit()


def _seed_azure(temp_db, name: str) -> None:
    from database.models.azure_connection import AzureConnection

    with temp_db() as db:
        db.add(
            AzureConnection(
                name=name,
                organization_url="https://dev.azure.com/acme",
                project_name="support",
            )
        )
        db.commit()


def _seed_group(temp_db, name: str) -> None:
    from database.models.ticket_group import TicketGroup

    with temp_db() as db:
        db.add(TicketGroup(name=name))
        db.commit()


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "p1",
        "repository_url": "https://github.com/acme/p1",
        "default_branch": "main",
    }
    payload.update(overrides)
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


class TestCreateProject:
    def test_create_minimal_ok(self, client: TestClient) -> None:
        body = _create(client)
        assert body["name"] == "p1"
        assert body["setup_status"] == "not_started"
        assert body["default_branch"] == "main"

    def test_create_with_existing_jira_connection_ok(self, client: TestClient, temp_db) -> None:
        _seed_jira(temp_db, "PriyaGold-TestRun")
        body = _create(client, jira_connection_name="PriyaGold-TestRun")
        assert body["jira_connection_name"] == "PriyaGold-TestRun"
        # Readiness must reflect the linked Jira connection.
        assert all("Jira" not in issue for issue in body["readiness_issues"])

    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        [
            ("jira_connection_name", "GHOST-JIRA", "does not exist"),
            ("salesforce_connection_name", "GHOST-ORG", "does not exist"),
            ("sandbox_kind", "quantum", "Unknown sandbox_kind"),
        ],
    )
    def test_dangling_references_return_400_not_500(
        self, client: TestClient, field: str, value: str, fragment: str
    ) -> None:
        # Before the fix this crashed with sqlite3.IntegrityError ->
        # HTTP 500 "Internal Server Error" (the bug report).
        response = client.post(
            "/api/projects",
            json={"name": "bad-refs", "repository_url": "https://github.com/acme/x", field: value},
        )
        assert response.status_code == 400, f"{field}={value} must be a 400, got {response.status_code}"
        assert fragment in response.json()["detail"]

    def test_failed_create_leaves_no_partial_row(self, client: TestClient) -> None:
        client.post(
            "/api/projects",
            json={
                "name": "ghost",
                "repository_url": "https://github.com/acme/x",
                "jira_connection_name": "NOPE",
            },
        )
        listing = client.get("/api/projects").json()
        assert all(p["name"] != "ghost" for p in listing)


class TestUpsertSemantics:
    def test_same_name_updates_in_place(self, client: TestClient, temp_db) -> None:
        first = _create(client, description="v1")
        second = _create(client, description="v2")
        assert second["id"] == first["id"]
        assert second["description"] == "v2"
        assert len(client.get("/api/projects").json()) == 1

    def test_upsert_does_not_clobber_unset_fields(self, client: TestClient, temp_db) -> None:
        _seed_jira(temp_db, "real-jira")
        _create(client, jira_connection_name="real-jira")
        updated = _create(client, name="p1", repository_url="https://github.com/acme/p1")
        # A later upsert without the field keeps the previous linkage.
        assert updated["jira_connection_name"] == "real-jira"

    def test_azure_git_selection_replaces_an_existing_github_source(self, client: TestClient, temp_db) -> None:
        _seed_azure(temp_db, "azure-git")
        initial = _create(client)
        from agents.pipeline_agent import credentials as project_repo

        project_repo.set_project_git_source(initial["id"], "legacy-github", None)

        updated = _create(
            client,
            repository_url="https://dev.azure.com/acme/support/_git/case-flow",
            github_connection_name="",
            azure_git_connection_name="azure-git",
        )

        assert updated["github_connection_name"] is None
        assert updated["azure_git_connection_name"] == "azure-git"


class TestTicketGroupSource:
    def test_create_with_existing_group_ok(self, client: TestClient, temp_db) -> None:
        _seed_group(temp_db, "Q4 Backlog")
        body = _create(client, ticket_group_name="Q4 Backlog")
        assert body["ticket_group_name"] == "Q4 Backlog"
        assert all("ticket source" not in i for i in body["readiness_issues"])

    def test_unknown_group_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/projects",
            json={
                "name": "bad-group",
                "repository_url": "https://github.com/acme/x",
                "ticket_group_name": "GHOST-GROUP",
            },
        )
        assert response.status_code == 400
        assert "GHOST-GROUP" in response.json()["detail"]

    def test_group_source_is_exclusive_with_connections(self, client: TestClient, temp_db) -> None:
        _seed_jira(temp_db, "real-jira")
        _seed_group(temp_db, "Q4 Backlog")
        # Switching a Jira-linked project to a group clears the Jira linkage.
        body = _create(client, jira_connection_name="real-jira")
        assert body["jira_connection_name"] == "real-jira"
        switched = _create(client, ticket_group_name="Q4 Backlog")
        assert switched["ticket_group_name"] == "Q4 Backlog"
        assert switched["jira_connection_name"] is None
        assert switched["azure_connection_name"] is None

    def test_clearing_group_unlinks_it(self, client: TestClient, temp_db) -> None:
        _seed_group(temp_db, "Q4 Backlog")
        first = _create(client, ticket_group_name="Q4 Backlog")
        assert first["ticket_group_name"] == "Q4 Backlog"
        # Edits send ticket_group_name=null to remove the linkage.
        updated = _create(client, ticket_group_name=None)
        assert updated["ticket_group_name"] is None


class TestGetDeleteProject:
    def test_get_unknown_returns_404_json(self, client: TestClient) -> None:
        response = client.get("/api/projects/no-such-id")
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}

    def test_delete_removes_project(self, client: TestClient) -> None:
        body = _create(client)
        assert client.delete(f"/api/projects/{body['id']}").status_code == 200
        assert client.get(f"/api/projects/{body['id']}").status_code == 404

    def test_delete_unknown_returns_404(self, client: TestClient) -> None:
        assert client.delete("/api/projects/no-such-id").status_code == 404


class TestGitToken:
    def test_token_uses_project_repo_url_as_fallback(self, client: TestClient) -> None:
        # GitTokenIn.repository_url is optional; the project's own URL wins.
        body = _create(client)
        response = client.post(
            f"/api/projects/{body['id']}/git-token", json={"access_token": "tok"}
        )
        assert response.status_code == 200
        project = client.get(f"/api/projects/{body['id']}").json()
        assert project["has_git_token"] is True

    def test_token_makes_readiness_pass(self, client: TestClient) -> None:
        body = _create(client)
        response = client.post(
            f"/api/projects/{body['id']}/git-token",
            json={"access_token": "tok", "repository_url": "https://github.com/acme/p1"},
        )
        assert response.status_code == 200
        project = client.get(f"/api/projects/{body['id']}").json()
        assert project["has_git_token"] is True
        assert all("git access token" not in i for i in project["readiness_issues"])

    def test_token_for_unknown_project_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/projects/no-such-id/git-token", json={"access_token": "tok"}
        )
        assert response.status_code == 404
