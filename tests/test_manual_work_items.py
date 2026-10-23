from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import manual_work_items as manual_router
from backend.routers import projects as projects_router


@pytest.fixture()
def client(temp_db) -> TestClient:
    app = FastAPI()
    app.include_router(projects_router.router)
    app.include_router(manual_router.router)
    return TestClient(app)


def _project(client: TestClient, name: str = "manual-project") -> dict:
    response = client.post(
        "/api/projects",
        json={"name": name, "repository_url": f"https://github.com/acme/{name}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_create_sanitizes_html_and_generates_id(client: TestClient) -> None:
    project = _project(client)
    response = client.post(
        f"/api/projects/{project['id']}/manual-tickets",
        json={
            "title": "Add lead field",
            "body_html": '<p style="text-align:center"><strong>Verify</strong> <em>metadata</em>.</p><script>alert(1)</script><ul><li>Deploy</li></ul>',
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["public_id"].startswith("MT-")
    assert item["status"] == "todo"
    assert "script" not in item["body_html"]
    assert 'data-align="center"' in item["body_html"]
    assert "Verify metadata" in item["body_text"]


def test_status_filter_update_and_delete_protection(client: TestClient) -> None:
    project = _project(client)
    create = client.post(
        f"/api/projects/{project['id']}/manual-tickets",
        json={"title": "Implement field", "body_html": "<p>Spec</p>"},
    )
    public_id = create.json()["public_id"]

    assert client.get(
        f"/api/projects/{project['id']}/manual-tickets?status=completed"
    ).json() == []
    status = client.patch(
        f"/api/projects/{project['id']}/manual-tickets/{public_id}/status",
        json={"status": "progress"},
    )
    assert status.status_code == 200
    assert status.json()["started_at"] is not None
    blocked = client.delete(
        f"/api/projects/{project['id']}/manual-tickets/{public_id}"
    )
    assert blocked.status_code == 409

    done = client.patch(
        f"/api/projects/{project['id']}/manual-tickets/{public_id}/status",
        json={"status": "completed"},
    )
    assert done.status_code == 200
    assert client.delete(
        f"/api/projects/{project['id']}/manual-tickets/{public_id}"
    ).status_code == 200


def test_cross_project_access_is_not_allowed(client: TestClient) -> None:
    first = _project(client, "first")
    second = _project(client, "second")
    created = client.post(
        f"/api/projects/{first['id']}/manual-tickets",
        json={"title": "Private", "body_html": "<p>Only first</p>"},
    )
    public_id = created.json()["public_id"]
    response = client.get(
        f"/api/projects/{second['id']}/manual-tickets/{public_id}"
    )
    assert response.status_code == 404
