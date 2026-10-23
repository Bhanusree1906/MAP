"""API + service tests for the Manual Ticket Ingestion flow: ticket groups,
manual tickets (incl. the 25MB attachment cap), and the project import flow
(source "group" here; Jira/Azure guards covered - live-fetch is exercised by
manual verification since it hits the real ticket APIs)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import projects as projects_router
from backend.routers import ticket_groups as ticket_groups_router
from backend.routers import tickets as tickets_router


@pytest.fixture()
def client(temp_db) -> TestClient:
    app = FastAPI()
    app.include_router(projects_router.router)
    app.include_router(tickets_router.router)
    app.include_router(ticket_groups_router.router)
    return TestClient(app)


def _create_group(client: TestClient, name: str = "SEG-1", description: str | None = None) -> dict:
    payload: dict = {"name": name}
    if description is not None:
        payload["description"] = description
    response = client.post("/api/ticket-groups", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _add_ticket(
    client: TestClient,
    group_id: str,
    ticket_id: str,
    summary: str = "Fix the thing",
    **extra,
) -> dict:
    data: dict = {"ticket_id": ticket_id, "summary": summary}
    data.update({k: v for k, v in extra.items() if v is not None})
    response = client.post(f"/api/ticket-groups/{group_id}/tickets", data=data)
    assert response.status_code == 200, response.text
    return response.json()


def _create_project(client: TestClient, name: str = "proj") -> dict:
    response = client.post(
        "/api/projects",
        json={"name": name, "repository_url": f"https://github.com/acme/{name}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestTicketGroups:
    def test_create_ok(self, client: TestClient) -> None:
        group = _create_group(client, "SEG-1", "Sprint one intake")
        assert group["name"] == "SEG-1"
        assert group["description"] == "Sprint one intake"
        assert group["ticket_count"] == 0
        assert group["tickets"] == []

    def test_duplicate_name_400(self, client: TestClient) -> None:
        _create_group(client, "SEG-1")
        response = client.post("/api/ticket-groups", json={"name": "SEG-1"})
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_empty_name_400(self, client: TestClient) -> None:
        response = client.post("/api/ticket-groups", json={"name": "   "})
        assert response.status_code == 400
        assert "Group name is required" in response.json()["detail"]

    def test_list_sorted_by_name(self, client: TestClient) -> None:
        _create_group(client, "Zap")
        _create_group(client, "Ace")
        names = [g["name"] for g in client.get("/api/ticket-groups").json()]
        assert names == ["Ace", "Zap"]

    def test_get_unknown_404(self, client: TestClient) -> None:
        response = client.get("/api/ticket-groups/no-such-group")
        assert response.status_code == 404

    def test_delete_ok_then_404(self, client: TestClient) -> None:
        group = _create_group(client)
        assert client.delete(f"/api/ticket-groups/{group['id']}").json() == {"status": "ok"}
        assert client.get(f"/api/ticket-groups/{group['id']}").status_code == 404

    def test_delete_unknown_404(self, client: TestClient) -> None:
        assert client.delete("/api/ticket-groups/no-such-group").status_code == 404

    def test_deleting_group_removes_its_tickets(self, client: TestClient) -> None:
        group = _create_group(client)
        _add_ticket(client, group["id"], "S-1")
        assert client.delete(f"/api/ticket-groups/{group['id']}").status_code == 200
        from agents.pipeline_agent import ingestion

        assert ingestion.list_manual_tickets(group["id"]) == []


class TestManualTickets:
    def test_rich_ticket_gets_generated_id_and_sanitized_content(self, client: TestClient) -> None:
        group = _create_group(client)
        response = client.post(
            f"/api/ticket-groups/{group['id']}/rich-tickets",
            json={
                "title": "Add lead field",
                "body_html": '<p style="text-align:center"><strong>Verify</strong> metadata</p><script>alert(1)</script>',
            },
        )
        assert response.status_code == 201, response.text
        ticket = response.json()
        assert ticket["ticket_id"].startswith("MT-")
        assert ticket["status"] == "todo"
        assert "script" not in ticket["description"]
        assert 'data-align="center"' in ticket["description"]

    def test_create_ok(self, client: TestClient) -> None:
        group = _create_group(client)
        ticket = _add_ticket(
            client,
            group["id"],
            "SEC-402",
            "Fix the retry loop",
            issue_type="Bug",
            priority="High",
            assignee="ada",
            description="Retries spin forever.",
        )
        assert ticket["ticket_id"] == "SEC-402"
        assert ticket["summary"] == "Fix the retry loop"
        assert ticket["issue_type"] == "Bug"
        assert ticket["priority"] == "High"
        assert ticket["assignee"] == "ada"
        assert ticket["status"] == "todo"
        assert ticket["has_attachment"] is False
        assert ticket["attachment_url"] is None

    def test_status_can_be_updated(self, client: TestClient) -> None:
        group = _create_group(client)
        ticket = _add_ticket(client, group["id"], "S-1")
        response = client.patch(
            f"/api/ticket-groups/{group['id']}/tickets/{ticket['ticket_id']}/status",
            json={"status": "agentcompleted"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "agentcompleted"

    def test_invalid_status_rejected(self, client: TestClient) -> None:
        group = _create_group(client)
        ticket = _add_ticket(client, group["id"], "S-1")
        response = client.patch(
            f"/api/ticket-groups/{group['id']}/tickets/{ticket['ticket_id']}/status",
            json={"status": "blocked"},
        )
        assert response.status_code == 422

    def test_missing_ticket_id_400(self, client: TestClient) -> None:
        group = _create_group(client)
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets", data={"summary": "x"}
        )
        assert response.status_code == 400
        assert "Ticket ID is required" in response.json()["detail"]

    def test_missing_summary_400(self, client: TestClient) -> None:
        group = _create_group(client)
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets", data={"ticket_id": "S-1"}
        )
        assert response.status_code == 400
        assert "Ticket summary is required" in response.json()["detail"]

    def test_create_in_unknown_group_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/ticket-groups/no-such-group/tickets",
            data={"ticket_id": "S-1", "summary": "x"},
        )
        assert response.status_code == 404

    def test_duplicate_ticket_id_400(self, client: TestClient) -> None:
        group = _create_group(client)
        _add_ticket(client, group["id"], "S-1")
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets",
            data={"ticket_id": "S-1", "summary": "again"},
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_same_ticket_id_allowed_in_other_group(self, client: TestClient) -> None:
        first = _create_group(client, "A")
        second = _create_group(client, "B")
        _add_ticket(client, first["id"], "S-1")
        _add_ticket(client, second["id"], "S-1")

    def test_attachment_upload_and_download(self, client: TestClient) -> None:
        group = _create_group(client)
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets",
            data={"ticket_id": "SEC-402", "summary": "With spec"},
            files={"file": ("spec.md", b"# spec body", "text/markdown")},
        )
        assert response.status_code == 200, response.text
        ticket = response.json()
        assert ticket["has_attachment"] is True
        assert ticket["attachment_filename"] == "spec.md"
        assert ticket["attachment_size"] == len(b"# spec body")

        download = client.get(ticket["attachment_url"])
        assert download.status_code == 200
        assert download.content == b"# spec body"
        assert download.headers["content-type"].startswith("text/markdown")

    def test_attachment_over_25mb_400(self, client: TestClient) -> None:
        group = _create_group(client)
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets",
            data={"ticket_id": "BIG-1", "summary": "huge"},
            files={"file": ("big.bin", b"\x00" * (25 * 1024 * 1024 + 1), "application/octet-stream")},
        )
        assert response.status_code == 400

    def test_list_tickets(self, client: TestClient) -> None:
        group = _create_group(client)
        _add_ticket(client, group["id"], "S-1")
        _add_ticket(client, group["id"], "S-2")
        tickets = client.get(f"/api/ticket-groups/{group['id']}/tickets").json()
        assert [t["ticket_id"] for t in tickets] == ["S-1", "S-2"]

    def test_delete_ticket_ok_then_404(self, client: TestClient) -> None:
        group = _create_group(client)
        ticket = _add_ticket(client, group["id"], "S-1")
        response = client.delete(f"/api/ticket-groups/{group['id']}/tickets/{ticket['ticket_id']}")
        assert response.json() == {"status": "ok"}
        assert client.get(f"/api/ticket-groups/{group['id']}/tickets/{ticket['ticket_id']}/attachment").status_code == 404


class TestImportFlow:
    def test_list_imported_empty(self, client: TestClient) -> None:
        project = _create_project(client)
        assert client.get(f"/api/projects/{project['id']}/tickets/imported").json() == []

    def test_list_tickets_no_connection_returns_empty(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.get(f"/api/projects/{project['id']}/tickets")
        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_list_tickets_group_source(self, client: TestClient) -> None:
        project = _create_project(client, name="grp-proj")
        group = _create_group(client, "SEG-1")
        _add_ticket(client, group["id"], "SEC-402", "Retry loop bug")
        _add_ticket(client, group["id"], "SEC-403", "Fix the flush")
        # Link the group as the project's ticket source.
        response = client.post(
            "/api/projects",
            json={
                "name": "grp-proj",
                "repository_url": "https://github.com/acme/grp-proj",
                "ticket_group_name": "SEG-1",
            },
        )
        assert response.status_code == 200, response.text
        tickets = client.get(f"/api/projects/{project['id']}/tickets").json()
        assert [t["key"] for t in tickets] == ["SEC-402", "SEC-403"]

    def test_group_source_loads_pipeline_settings(self, client: TestClient) -> None:
        project = _create_project(client, name="settings-group-proj")
        _create_group(client, "SEG-1")
        response = client.post(
            "/api/projects",
            json={
                "name": "settings-group-proj",
                "repository_url": "https://github.com/acme/settings-group-proj",
                "ticket_group_name": "SEG-1",
            },
        )
        assert response.status_code == 200, response.text

        from agents.pipeline_agent.config import load_settings

        settings = load_settings(None, project["name"])
        assert settings.jira is None
        assert settings.azure is None

    def test_get_ticket_from_group_source_without_import(self, client: TestClient) -> None:
        project = _create_project(client, name="grp-proj")
        group = _create_group(client, "SEG-1")
        _add_ticket(
            client,
            group["id"],
            "SEC-402",
            "Retry loop bug",
            issue_type="Bug",
            priority="High",
            assignee="ada",
            description="Retries spin forever.",
        )
        response = client.post(
            "/api/projects",
            json={
                "name": "grp-proj",
                "repository_url": "https://github.com/acme/grp-proj",
                "ticket_group_name": "SEG-1",
            },
        )
        assert response.status_code == 200, response.text
        detail = client.get(f"/api/projects/{project['id']}/tickets/SEC-402")
        assert detail.status_code == 200, detail.text
        assert detail.json()["key"] == "SEC-402"
        assert detail.json()["description"] == "Retries spin forever."

    def test_list_tickets_group_imported_source(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        _add_ticket(client, group["id"], "SEC-402", "Retry loop bug")
        result = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": ["SEC-402"]},
        )
        assert result.status_code == 200, result.text
        tickets = client.get(f"/api/projects/{project['id']}/tickets").json()
        assert [t["key"] for t in tickets] == ["SEC-402"]
        assert tickets[0]["summary"] == "Retry loop bug"

    def test_get_ticket_from_group_import(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        _add_ticket(
            client,
            group["id"],
            "SEC-402",
            "Retry loop bug",
            issue_type="Bug",
            priority="High",
            assignee="ada",
            description="Retries spin forever.",
        )
        result = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": ["SEC-402"]},
        )
        assert result.status_code == 200, result.text
        detail = client.get(f"/api/projects/{project['id']}/tickets/SEC-402")
        assert detail.status_code == 200, detail.text
        assert detail.json()["key"] == "SEC-402"
        assert detail.json()["description"] == "Retries spin forever."

    def test_get_attachment_from_group_import(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        response = client.post(
            f"/api/ticket-groups/{group['id']}/tickets",
            data={"ticket_id": "SEC-402", "summary": "With spec"},
            files={"file": ("spec.png", b"PNG-BYTES", "image/png")},
        )
        assert response.status_code == 200, response.text
        result = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": ["SEC-402"]},
        )
        assert result.status_code == 200, result.text
        attachment = client.get(
            f"/api/projects/{project['id']}/tickets/SEC-402/attachments/attachment"
        )
        assert attachment.status_code == 200, attachment.text
        assert attachment.content == b"PNG-BYTES"
        assert attachment.headers["content-type"].startswith("image/png")

    def test_fetch_group_source(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        _add_ticket(client, group["id"], "SEC-402", "Retry loop bug")
        response = client.post(
            f"/api/projects/{project['id']}/tickets/fetch",
            json={"source": "group", "group_id": group["id"]},
        )
        assert response.status_code == 200, response.text
        tickets = response.json()
        assert [t["key"] for t in tickets] == ["SEC-402"]
        assert tickets[0]["summary"] == "Retry loop bug"

    def test_fetch_group_requires_group_id(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/fetch", json={"source": "group"}
        )
        assert response.status_code == 400
        assert "group" in response.json()["detail"].lower()

    def test_fetch_unknown_project_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/projects/no-such-id/tickets/fetch", json={"source": "group"}
        )
        assert response.status_code == 404

    def test_fetch_jira_requires_linked_connection(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/fetch",
            json={"source": "jira", "jql": "project = PGD"},
        )
        assert response.status_code == 400
        assert "no Jira connection" in response.json()["detail"]

    def test_fetch_azure_requires_linked_connection(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/fetch", json={"source": "azure"}
        )
        assert response.status_code == 400
        assert "no Azure" in response.json()["detail"]

    def test_import_group_ok_and_dedupe(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        _add_ticket(client, group["id"], "S-1", "First")
        _add_ticket(client, group["id"], "S-2", "Second")
        url = f"/api/projects/{project['id']}/tickets/import"

        first = client.post(url, json={"source": "group", "group_id": group["id"], "keys": ["S-1", "S-2"]})
        assert first.status_code == 200, first.text
        assert sorted(t["key"] for t in first.json()["imported"]) == ["S-1", "S-2"]
        assert first.json()["skipped"] == []

        second = client.post(url, json={"source": "group", "group_id": group["id"], "keys": ["S-1", "S-2"]})
        assert second.status_code == 200, second.text
        assert second.json()["imported"] == []
        assert sorted(second.json()["skipped"]) == ["S-1", "S-2"]

        imported = client.get(f"/api/projects/{project['id']}/tickets/imported").json()
        assert len(imported) == 2
        by_key = {t["key"]: t for t in imported}
        assert by_key["S-1"]["source"] == "group"
        assert by_key["S-1"]["source_connection"] == "SEG-1"
        assert by_key["S-1"]["summary"] == "First"

    def test_import_no_keys_400(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": []},
        )
        assert response.status_code == 400

    def test_import_ticket_not_in_group_404(self, client: TestClient) -> None:
        project = _create_project(client)
        group = _create_group(client, "SEG-1")
        response = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": ["GHOST-1"]},
        )
        assert response.status_code == 404

    def test_import_jira_requires_linked_connection(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "jira", "keys": ["PGD-1"]},
        )
        assert response.status_code == 400

    def test_import_azure_requires_linked_connection(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "azure", "keys": ["1"]},
        )
        assert response.status_code == 400

    def test_local_run_content_for_group_import(self, client: TestClient) -> None:
        """The stored snapshot drives a local (no-live-fetch) planning run."""
        project = _create_project(client)
        group = _create_group(client, "SEG-2")

        uploaded = client.post(
            f"/api/ticket-groups/{group['id']}/tickets",
            data={"ticket_id": "SEC-402", "summary": "Retry loop bug", "issue_type": "Bug"},
            files={"file": ("spec.png", b"PNG-BYTES", "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text

        result = client.post(
            f"/api/projects/{project['id']}/tickets/import",
            json={"source": "group", "group_id": group["id"], "keys": ["SEC-402"]},
        )
        assert result.status_code == 200, result.text

        from agents.pipeline_agent import ingestion

        ticket = ingestion.get_imported_ticket(project["id"], "SEC-402", source="group")
        assert ticket is not None
        assert ticket.attachment_size == len(b"PNG-BYTES")

        markdown = ingestion.imported_ticket_to_markdown(ticket)
        assert "# SEC-402: Retry loop bug" in markdown
        assert "Type: Bug" in markdown
        assert "spec.png" in markdown

        images = ingestion.imported_ticket_image_attachments(ticket)
        assert images == [("spec.png", b"PNG-BYTES", "image/png")]