"""Tests for the global exception handlers in backend/errors.py: unhandled
DB violations and crashes must produce structured JSON with the right status
codes, never Starlette's bare-text "Internal Server Error" page.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import register_error_handlers


def _app_with(route_factory) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    route_factory(app)
    return TestClient(app, raise_server_exceptions=False)


def _integrity_route(app: FastAPI) -> None:
    engine = sa.create_engine("sqlite://")

    @app.post("/boom/integrity")
    def integrity() -> dict:
        # Real SQLAlchemy IntegrityError, raised exactly like a commit would.
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("INSERT INTO t (id) VALUES (1)"))
            try:
                conn.execute(sa.text("INSERT INTO t (id) VALUES (1)"))
            except sa.exc.IntegrityError:
                raise
        return {}


def _crash_route(app: FastAPI) -> None:
    @app.get("/boom/crash")
    def crash() -> dict:
        raise RuntimeError("secret internal detail")


def test_integrity_error_becomes_409_json() -> None:
    client = _app_with(_integrity_route)
    response = client.post("/boom/integrity")
    assert response.status_code == 409
    body = response.json()
    assert "conflicts with stored data" in body["detail"]
    # No SQL or table internals leak to the client.
    assert "UNIQUE constraint" not in body["detail"]
    assert "t.id" not in body["detail"]


def test_unexpected_error_becomes_500_json_without_detail_leak() -> None:
    client = _app_with(_crash_route)
    response = client.get("/boom/crash")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    # The exception message must never reach the client.
    assert "secret internal detail" not in response.text
