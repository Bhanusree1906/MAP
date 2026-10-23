"""Tests for sandbox repo cloning: credential embedding, prompt disabling,
and token scrubbing. Regression coverage for the "could not read Password
for 'https://TOKEN@github.com': No such device or address" clone failure -
caused by embedding the token as a username with no password component.
"""

from __future__ import annotations

import pytest

from sandbox.manager import SandboxManager, authenticated_clone_url


class _FakeResult:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _FakeSession:
    def __init__(self, result: _FakeResult):
        self.result = result
        self.commands: list[str] = []

    def execute_command(self, command: str, **_kwargs) -> _FakeResult:
        self.commands.append(command)
        return self.result


def _manager_with(result: _FakeResult) -> tuple[SandboxManager, _FakeSession]:
    manager = SandboxManager()
    session = _FakeSession(result)
    manager._session = session  # bypass the context-manager guard
    return manager, session


# ---------------------------------------------------------------------------
# authenticated_clone_url
# ---------------------------------------------------------------------------


def test_token_is_embedded_as_username_AND_password() -> None:
    # Username-only URLs make git prompt for a password (the original bug);
    # both components must be present so git never goes interactive.
    url = authenticated_clone_url("https://github.com/acme/widgets", "tok123")
    assert url == "https://x-access-token:tok123@github.com/acme/widgets"


def test_url_suffixes_and_ports_survive() -> None:
    url = authenticated_clone_url("https://github.com:8443/acme/w.git?ref=x#f", "t")
    assert url == "https://x-access-token:t@github.com:8443/acme/w.git?ref=x#f"


def test_no_token_returns_url_untouched() -> None:
    assert (
        authenticated_clone_url("https://github.com/acme/widgets", None)
        == "https://github.com/acme/widgets"
    )
    assert (
        authenticated_clone_url("https://github.com/acme/widgets", "")
        == "https://github.com/acme/widgets"
    )


def test_non_https_url_is_rejected_rather_than_silently_tokenized() -> None:
    with pytest.raises(ValueError):
        authenticated_clone_url("git@github.com:acme/widgets.git", "t")


# ---------------------------------------------------------------------------
# clone_repository
# ---------------------------------------------------------------------------

_SECRET = "gho_supersecret"


def test_missing_token_fails_fast_without_touching_the_session() -> None:
    manager, session = _manager_with(_FakeResult())
    result = manager.clone_repository("https://github.com/acme/widgets", token=None)
    assert result["success"] is False
    assert "No git access token" in result["error"]
    assert session.commands == []  # no anonymous clone attempt that would hang/prompt


def test_clone_command_is_shell_free() -> None:
    # llm_sandbox exec_run does NOT spawn a shell: env-prefix syntax like
    # "GIT_TERMINAL_PROMPT=0 git clone ..." is parsed as a program name and
    # dies with exit 127. The command must be bare argv.
    manager, session = _manager_with(_FakeResult(stdout=""))
    manager.clone_repository("https://github.com/acme/widgets", token=_SECRET)
    assert session.commands[0].startswith("find /workspace ")
    command = session.commands[1]
    assert command.startswith("git clone ")
    assert "GIT_TERMINAL_PROMPT" not in command
    # Credential rides in the URL (inherent to https clone) - shlex-quoted.
    assert "x-access-token:" in command


def test_cleanup_failure_prevents_clone() -> None:
    manager, session = _manager_with(_FakeResult(exit_code=1, stderr="permission denied"))
    result = manager.clone_repository("https://github.com/acme/widgets", token=_SECRET)
    assert result == {"success": False, "error": "permission denied", "path": "/workspace"}
    assert len(session.commands) == 1


def test_prompt_disabled_env_applied_at_container_level(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prompt-disabling must happen via container runtime env, not per-command
    # prefixes. Verify the merged env actually reaches SandboxSession.
    captured: dict = {}

    class _CapturingSessionCM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    import sandbox.manager as manager_module

    monkeypatch.setattr(manager_module, "SandboxSession", lambda **kwargs: _CapturingSessionCM(**kwargs))
    with SandboxManager(env={"CODEX_API_KEY": "x"}):
        pass
    env = captured["runtime_configs"]["environment"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "echo"
    assert env["CODEX_API_KEY"] == "x"


def test_docker_level_failure_on_stdout_is_surfaced() -> None:
    # exec failures ("exec: ... not found") print to STDOUT and leave stderr
    # empty - the error must not come back as an empty string.
    stdout_err = 'OCI runtime exec failed: exec failed: unable to start container process: exec: "git": not found'
    manager, _ = _manager_with(_FakeResult(exit_code=127, stdout=stdout_err, stderr=""))
    result = manager.clone_repository("https://github.com/acme/widgets", token=_SECRET)
    assert result["success"] is False
    assert "OCI runtime exec failed" in result["error"]


def test_failed_clone_scrubs_token_from_stderr() -> None:
    # Git echoes the remote URL (credentials included) in fatal messages.
    stderr = (
        f"Cloning into '/workspace'...\n"
        f"fatal: unable to access 'https://x-access-token:{_SECRET}@github.com/acme/private/': "
        f"The requested URL returned error: 404"
    )
    manager, _ = _manager_with(_FakeResult(exit_code=128, stderr=stderr))
    result = manager.clone_repository("https://github.com/acme/private", token=_SECRET)
    assert result["success"] is False
    assert _SECRET not in result["error"]
    assert "[REDACTED]" in result["error"]
    # 404-style failures get the not-found guidance.
    assert "Hint:" in result["error"]


def test_auth_failure_gets_actionable_hint() -> None:
    stderr = f"fatal: Authentication failed for 'https://x-access-token:[REDACTED]@github.com/'"
    manager, _ = _manager_with(_FakeResult(exit_code=128, stderr=stderr))
    result = manager.clone_repository("https://github.com/acme/private", token=_SECRET)
    assert "Re-run Initialize Project" in result["error"]


def test_successful_clone_reports_path() -> None:
    manager, _ = _manager_with(_FakeResult(stdout=""))
    result = manager.clone_repository("https://github.com/acme/widgets", token=_SECRET)
    assert result["success"] is True
    assert result["path"] == "/workspace"
