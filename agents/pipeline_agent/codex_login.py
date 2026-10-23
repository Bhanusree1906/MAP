"""Device-code login for Codex CLI, so pipeline runs can use a ChatGPT
account instead of OPENAI_API_KEY.

Unlike Jira's browser OAuth (see agents/jira_agent/oauth3lo.py), Codex's
normal `codex login` opens a loopback server and waits for a redirect - the
same shape that broke for Jira's MCP flow, and for the same reason: it runs
inside a throwaway Docker container (see sandbox/manager.py), so nothing on
your machine can reach that container's localhost, and the login dies with
the container anyway. `codex login --device-auth` sidesteps all of that: it
prints a URL and a short one-time code, and blocks until you approve it in
any browser on any device - no callback URL, no port, no app registration.

This module runs that command in a short-lived container (via the `docker`
SDK directly, not sandbox/manager.py's SandboxManager - that's built for
synchronous, single-shot tool calls, not a background job spanning several
HTTP requests while a human goes to a browser). It streams the container's
logs to pull out the URL/code as soon as they're printed, and on a
successful exit copies out ~/.codex/auth.json and persists it via
codex_credentials.py, for agent.py's build_execution_agent to inject into
every future pipeline run's sandbox.
"""

from __future__ import annotations

import io
import re
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import docker
from docker.errors import NotFound

from .codex_credentials import save_codex_auth_json

Status = Literal["starting", "awaiting_user", "connected", "failed", "timed_out"]

_AUTH_JSON_PATH = "/home/agentuser/.codex/auth.json"
_LOGIN_TIMEOUT_SECONDS = 900  # matches the ~15 min expiry codex itself prints
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_URL_RE = re.compile(r"https://\S+")
_CODE_RE = re.compile(r"\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b")


@dataclass
class LoginSession:
    id: str
    status: Status = "starting"
    url: str | None = None
    code: str | None = None
    error: str | None = None


_sessions: dict[str, LoginSession] = {}
_sessions_lock = threading.Lock()


def get_session(session_id: str) -> LoginSession | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def start_login(image: str) -> str:
    """Kicks off a login in a background thread and returns immediately
    with a session id to poll via get_session().
    """
    session = LoginSession(id=str(uuid.uuid4()))
    with _sessions_lock:
        _sessions[session.id] = session
    thread = threading.Thread(target=_run_login, args=(session, image), daemon=True)
    thread.start()
    return session.id


def _run_login(session: LoginSession, image: str) -> None:
    client = docker.from_env()
    container = None
    timed_out = {"value": False}
    watchdog: threading.Timer | None = None
    try:
        container = client.containers.run(
            image,
            command=["codex", "login", "--device-auth"],
            user="agentuser",
            detach=True,
            tty=False,
        )

        def _kill_on_timeout() -> None:
            timed_out["value"] = True
            try:
                container.kill()
            except Exception:  # noqa: BLE001 - best-effort cleanup, container may already be gone
                pass

        watchdog = threading.Timer(_LOGIN_TIMEOUT_SECONDS, _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()

        buffer = ""
        for raw_chunk in container.logs(stream=True, follow=True):
            buffer += _ANSI_RE.sub("", raw_chunk.decode("utf-8", errors="replace"))
            if session.url is None:
                url_match = _URL_RE.search(buffer)
                code_match = _CODE_RE.search(buffer)
                if url_match and code_match:
                    session.url = url_match.group(0).rstrip(".,)")
                    session.code = code_match.group(0)
                    session.status = "awaiting_user"

        watchdog.cancel()
        result = container.wait(timeout=15)
        exit_code = result.get("StatusCode", 1)

        if timed_out["value"]:
            session.status = "timed_out"
            session.error = "No one approved the login within 15 minutes; start again."
        elif exit_code == 0:
            auth_json = _read_auth_json(container)
            if auth_json:
                save_codex_auth_json(auth_json)
                session.status = "connected"
            else:
                session.status = "failed"
                session.error = f"codex login exited cleanly but {_AUTH_JSON_PATH} was not found."
        else:
            session.status = "failed"
            session.error = (buffer.strip()[-500:] or f"codex login exited with status {exit_code}")
    except Exception as exc:  # noqa: BLE001 - surfaced to the polling endpoint, not swallowed
        session.status = "failed"
        session.error = str(exc)
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def _read_auth_json(container) -> str | None:
    try:
        stream, _ = container.get_archive(_AUTH_JSON_PATH)
    except NotFound:
        return None
    buf = io.BytesIO()
    for chunk in stream:
        buf.write(chunk)
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar:
        member = next((m for m in tar.getmembers() if m.isfile()), None)
        if member is None:
            return None
        extracted = tar.extractfile(member)
        if extracted is None:
            return None
        return extracted.read().decode("utf-8")
