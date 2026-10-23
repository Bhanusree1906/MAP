"""Device-code Codex login, so pipeline runs can use a ChatGPT account
instead of OPENAI_API_KEY. See agents/pipeline_agent/codex_login.py for the
actual flow (a background container running `codex login --device-auth`,
polled here) and codex_credentials.py for where the result is persisted.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from agents.pipeline_agent import codex_credentials, codex_login
from agents.pipeline_agent.config import _codex_model_settings, _engine
from backend.services import connection_health

router = APIRouter(prefix="/api/codex-connection", tags=["codex-connection"])


@router.get("")
def get_status() -> dict:
    return {
        "connected": codex_credentials.get_codex_auth_json() is not None,
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "engine": _engine(),
        "opencode_model": os.getenv("OPENCODE_MODEL", "").strip() or None,
        **connection_health.get("codex", "default"),
        **_codex_model_settings(),
    }


@router.post("/start")
def start() -> dict:
    image = os.getenv("PIPELINE_AGENT_SANDBOX_IMAGE", "agent-sandbox:latest")
    session_id = codex_login.start_login(image)
    return {"session_id": session_id}


@router.get("/status/{session_id}")
def status(session_id: str) -> dict:
    session = codex_login.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Login session not found")
    return {
        "status": session.status,
        "url": session.url,
        "code": session.code,
        "error": session.error,
    }


@router.delete("")
def disconnect() -> dict:
    removed = codex_credentials.delete_codex_connection()
    return {"status": "ok", "removed": removed}
