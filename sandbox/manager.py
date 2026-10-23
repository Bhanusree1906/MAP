"""Docker-backed sandbox session manager.

Everything here is configurable by the caller (image, workdir, backend,
credentials) - nothing is hardcoded. A SandboxManager owns exactly one
underlying llm_sandbox session for its whole lifetime, so a clone followed
by a write followed by a command execution all see the same container and
filesystem. Use it as a context manager:

    with SandboxManager(image="agent-sandbox:latest") as sandbox:
        sandbox.clone_repository(repo_url, token=token)
        sandbox.write_file("TICKET.md", ticket_markdown)
        sandbox.run_codex("implement the ticket in TICKET.md")
"""

from __future__ import annotations

import re
import shlex
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from llm_sandbox import SandboxBackend, SandboxSession
from llm_sandbox.exceptions import ImagePullError

# Docker Desktop (macOS) pauses its VM after ~5 minutes idle and cold-starts
# it on the next request (see "Resource Saver" in Docker Desktop settings -
# disabling it avoids this entirely). A request that lands while the VM is
# still coming up can get a spurious "no such image" a few seconds before
# the image store finishes mounting, even though the image is genuinely
# present on disk. Retrying absorbs that race instead of failing the whole
# pipeline run over a already-known-transient Docker Desktop quirk.
_IMAGE_RETRY_ATTEMPTS = 3
_IMAGE_RETRY_DELAY_SECONDS = 5
_GITHUB_MEMORY_PATH = "/home/agentuser/.codex/skills/github-memory"


def _docker_unreachable_message(exc: BaseException) -> str | None:
    """The docker SDK's first call is 'fetch server API version' over the
    unix socket. When Docker Desktop is stopped that becomes FileNotFoundError
    on docker.sock — which looks nothing like 'start Docker'."""
    text = str(exc)
    lowered = text.lower()
    if "server api version" not in lowered and "docker.sock" not in lowered:
        return None
    return (
        "Cannot reach Docker. Start Docker Desktop and retry — bootstrap and "
        "every pipeline run need a running daemon. "
        f"({text})"
    )


def authenticated_clone_url(repo_url: str, token: str | None) -> str:
    """Inject an access token into an https:// git remote URL.

    Emits `https://x-access-token:<token>@host/...` - both a username AND a
    password component. A username-only URL (`https://<token>@host/...`) makes
    git prompt for the missing password and hang / fail with "could not read
    Password ... No such device or address". Works for any host that accepts
    basic-auth git credentials (GitHub, GitLab, Bitbucket, Azure DevOps, ...).
    Any username already embedded in the URL (e.g. Azure's
    `https://user@dev.azure.com/...`) is stripped so a malformed double-@
    never leaks through.
    """
    if not token:
        return repo_url
    parts = urlsplit(repo_url)
    if parts.scheme != "https":
        raise ValueError(f"Token auth requires an https:// repository URL, got: {repo_url!r}")
    host = parts.netloc.rsplit("@", 1)[-1]
    netloc = f"x-access-token:{token}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class SandboxManager:
    def __init__(
        self,
        image: str = "agent-sandbox:latest",
        workdir: str = "/workspace",
        backend: SandboxBackend = SandboxBackend.DOCKER,
        keep_template: bool = False,
        verbose: bool = False,
        env: dict[str, str] | None = None,
        # llm_sandbox hardcodes the container's user to "root" unless
        # overridden via runtime_configs - "root" would make $HOME resolve
        # to /root, not agentuser's home, so anything relying on the
        # image's per-user config (e.g. Codex's ~/.codex/config.toml, or
        # ~/.agents/skills/) would silently go missing. Match whatever
        # non-root user the image actually creates (see sandbox/Dockerfile).
        user: str | None = "agentuser",
        # Unset, Codex CLI picks the flagship model (currently gpt-5.6-sol)
        # and can spend a whole ChatGPT quota on one ticket. Pipeline callers
        # pass a cheaper model + reasoning effort (see agents/pipeline_agent/config.py).
        codex_model: str | None = None,
        codex_reasoning_effort: str | None = None,
        # opencode's alternative-engine model id (e.g. "openrouter/<vendor>/<model>:free").
        opencode_model: str | None = None,
        # Hard wall-clock cap (seconds) on a single coding-engine exec. Without
        # it a stuck `codex exec` / `opencode run` hangs the phase forever - the
        # only recovery is a manual cancel. Enforced in-container via coreutils
        # `timeout`, so it bounds the exec even though llm_sandbox's
        # execute_command has no timeout of its own.
        engine_timeout_seconds: int = 1800,
    ):
        self.image = image
        self.workdir = workdir
        self.backend = backend
        self.keep_template = keep_template
        self.verbose = verbose
        self.env = env
        self.user = user
        self.codex_model = codex_model
        self.codex_reasoning_effort = codex_reasoning_effort
        self.opencode_model = opencode_model
        self.engine_timeout_seconds = engine_timeout_seconds
        self._session_cm = None
        self._session = None

    def __enter__(self) -> "SandboxManager":
        # Disable git's interactive credential prompt at the container level -
        # llm_sandbox's exec does not spawn a shell, so a per-command
        # "GIT_TERMINAL_PROMPT=0 git ..." prefix is parsed as a program name and
        # dies with exit 127. Set it (and GIT_ASKPASS) as container env instead.
        # setdefault so an explicit override from the caller still wins.
        env = dict(self.env or {})
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_ASKPASS", "echo")
        runtime_configs: dict[str, Any] = {"environment": env}
        if self.user:
            runtime_configs["user"] = self.user

        for attempt in range(1, _IMAGE_RETRY_ATTEMPTS + 1):
            session_cm = SandboxSession(
                backend=self.backend,
                image=self.image,
                lang="python",
                keep_template=self.keep_template,
                verbose=self.verbose,
                skip_environment_setup=True,
                workdir=self.workdir,
                runtime_configs=runtime_configs or None,
            )
            try:
                self._session = session_cm.__enter__()
                self._session_cm = session_cm
                return self
            except ImagePullError:
                if attempt == _IMAGE_RETRY_ATTEMPTS:
                    raise
                time.sleep(_IMAGE_RETRY_DELAY_SECONDS)
            except Exception as exc:
                hint = _docker_unreachable_message(exc)
                if hint:
                    raise RuntimeError(hint) from exc
                raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            self._session_cm.__exit__(exc_type, exc, tb)
        self._session_cm = None
        self._session = None

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError(
                "SandboxManager must be used as a context manager: "
                "`with SandboxManager(...) as sandbox:`."
            )
        return self._session

    def _resolve(self, path: str) -> str:
        """Relative paths resolve inside the configured workdir."""
        return path if path.startswith("/") else f"{self.workdir.rstrip('/')}/{path}"

    def clone_repository(
        self, repo_url: str, token: str | None = None, clone_path: str | None = None
    ) -> dict[str, Any]:
        """Clone straight into the target directory (the configured workdir by
        default). The workdir is empty at this point - TICKET.md and skills go
        elsewhere - so there is no need for a nested `<workdir>/repo/` checkout
        (which previously leaked a second `.git` into the tree and confused the
        coding agent).
        """
        if not token:
            return {
                "success": False,
                # "error": "No git access token configured for this project - cannot clone a private repo.",
                "error" : "No git account selected for this project."
            }
        target = self._resolve(clone_path) if clone_path else self.workdir
        auth_url = authenticated_clone_url(repo_url, token)
        cleanup = self.session.execute_command(
            "find "
            f"{shlex.quote(target)} "
            "-mindepth 1 -maxdepth 1 -exec rm -rf -- {} +"
        )
        if cleanup.exit_code != 0:
            raw = (cleanup.stderr or cleanup.stdout or "could not clear clone destination").strip()
            return {"success": False, "error": raw, "path": target}
        command = f"git clone {shlex.quote(auth_url)} {shlex.quote(target)}"
        result = self.session.execute_command(command)
        if result.exit_code != 0:
            # git echoes the credential-bearing URL in fatal messages - scrub it.
            raw = (result.stderr or result.stdout or "git clone failed").strip()
            error = raw.replace(token, "[REDACTED]")
            lowered = error.lower()
            if "authentication failed" in lowered or "invalid username or password" in lowered:
                error = f"{error}\nRe-run Initialize Project to refresh the git access token."
            elif "404" in error or "not found" in lowered:
                error = f"{error}\nHint: the repository was not found, or the token cannot access it."
            return {"success": False, "error": error, "path": target}
        return {"success": True, "path": target, "message": result.stdout}



    def write_file(self, path: str, content: str) -> dict[str, Any]:
        dest = self._resolve(path)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            self.session.copy_to_runtime(tmp_path, dest)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller/tool, not swallowed
            return {"success": False, "error": str(exc)}
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return {"success": True, "message": f"Wrote {len(content)} bytes to {dest}"}

    def write_bytes(self, path: str, data: bytes) -> dict[str, Any]:
        """Binary-safe counterpart to write_file - for content that isn't
        text (e.g. a packaged skill's tar.gz, see agents/skills/service.py).
        """
        dest = self._resolve(path)
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            self.session.copy_to_runtime(tmp_path, dest)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller/tool, not swallowed
            return {"success": False, "error": str(exc)}
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return {"success": True, "message": f"Wrote {len(data)} bytes to {dest}"}

    def read_file(self, path: str) -> dict[str, Any]:
        dest = self._resolve(path)
        result = self.session.execute_command(f"cat {shlex.quote(dest)}")
        if result.exit_code != 0:
            return {"success": False, "error": result.stderr or f"{dest} not found"}
        return {"success": True, "content": result.stdout}

    def pause(self) -> bool:
        """Freeze every process in the container (SIGSTOP) so a long-running
        Codex/opencode exec is suspended exactly where it is. Resuming via
        unpause() continues the same process from the same point - nothing is
        restarted or lost. Only meaningful for the Docker backend; returns
        False on backends without a container (no-op for the caller).
        """
        container = getattr(self.session, "container", None)
        if container is None:
            return False
        try:
            container.pause()
            return True
        except Exception:  # noqa: BLE001 - best-effort, caller decides severity
            return False

    def unpause(self) -> bool:
        """Counterpart to pause() - SIGCONT the container's frozen processes."""
        container = getattr(self.session, "container", None)
        if container is None:
            return False
        try:
            container.unpause()
            return True
        except Exception:  # noqa: BLE001 - best-effort, caller decides severity
            return False

    def kill(self) -> bool:
        """Force-stop the container immediately (SIGKILL) - used to cancel a
        run, interrupting a blocking Codex/opencode exec that a checkpoint()
        raise alone can't reach. Callers must unpause() first if the
        container is currently frozen (pause()) - a paused container can't
        receive SIGKILL until it's thawed. Best-effort like pause()/
        unpause(): the container may already be gone.
        """
        container = getattr(self.session, "container", None)
        if container is None:
            return False
        try:
            container.kill()
            return True
        except Exception:  # noqa: BLE001 - best-effort, caller decides severity
            return False

    def execute(
        self,
        command: str,
        workdir: str | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        # Passing either callback forces llm_sandbox into streaming mode
        # (see CommandExecutionMixin.execute_command) - each gets called
        # with a chunk of output as soon as Docker's exec API delivers it,
        # not buffered until the command finishes.
        #
        # timeout_seconds wraps the command in coreutils `timeout` so a
        # long-running command (e.g. `sf ... RunLocalTests`, which has no
        # timeout of its own) can't hang the caller forever. Exit 124 (TERM)
        # / 137 (KILL 10s later) are normalised to a clear timeout error.
        run_command = command
        if timeout_seconds is not None:
            # `timeout` execs argv[0] with no shell. A multi-line `set -e; git …`
            # script was run as the binary `set` ("failed to run command 'set'").
            # Always invoke bash so &&, pipes, and scripts work.
            run_command = (
                f"timeout --kill-after=10 {int(timeout_seconds)} "
                f"bash -lc {shlex.quote(command)}"
            )
        result = self.session.execute_command(
            run_command, workdir=workdir, on_stdout=on_stdout, on_stderr=on_stderr
        )
        if timeout_seconds is not None and result.exit_code in (124, 137):
            return {
                "success": False,
                "stdout": result.stdout,
                "stderr": (
                    f"Command exceeded the {int(timeout_seconds)}s time limit and was stopped.\n"
                    f"{result.stderr}"
                ),
                "exit_code": result.exit_code,
                "timed_out": True,
            }
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    def run_codex(
        self,
        instruction: str,
        workdir: str | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        # Codex's own sandbox modes (read-only/workspace-write/danger-full-access)
        # are implemented via bubblewrap user-namespaces on Linux, which Docker
        # blocks by default - every mode fails here with a bwrap namespace error
        # and a read-only-looking workspace. --dangerously-bypass-approvals-and-sandbox
        # is the flag Codex documents for exactly this case ("environments that
        # are externally sandboxed") - this container *is* that outer sandbox:
        # ephemeral, isolated, and destroyed after one run.
        #
        # --model / -c model_reasoning_effort pin a cheaper model. Without them
        # Codex defaults to gpt-5.6-sol (flagship) and can drain a ChatGPT
        # Plus/Pro quota on a single ticket.
        argv = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        chosen_model = model or self.codex_model
        chosen_effort = reasoning_effort or self.codex_reasoning_effort
        if chosen_model:
            argv.extend(["--model", chosen_model])
        if chosen_effort:
            argv.extend(["-c", f"model_reasoning_effort={chosen_effort}"])
        argv.extend(["-c", "model_reasoning_summary=none"])
        argv.append(instruction)
        return self._exec_engine(argv, workdir, on_stdout, on_stderr)

    def _exec_engine(
        self,
        argv: list[str],
        workdir: str | None,
        on_stdout: Callable[[str], None] | None,
        on_stderr: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        """Run a coding-engine argv under a hard wall-clock cap.

        coreutils `timeout` sends TERM at engine_timeout_seconds and KILL 10s
        later; it exits 124 on TERM-timeout and 137 (128+SIGKILL) on the
        follow-up kill. Both are normalised to a clear timeout error so the
        phase fails fast with a readable message instead of hanging.
        """
        timed_argv = [
            "timeout",
            "--kill-after=10",
            str(self.engine_timeout_seconds),
            *argv,
        ]
        command = " ".join(shlex.quote(part) for part in timed_argv)
        result = self.session.execute_command(
            command, workdir=workdir, on_stdout=on_stdout, on_stderr=on_stderr
        )
        if result.exit_code in (124, 137):
            return {
                "success": False,
                "stdout": result.stdout,
                "stderr": (
                    f"Coding engine exceeded the {self.engine_timeout_seconds}s time limit "
                    f"and was stopped.\n{result.stderr}"
                ),
                "exit_code": result.exit_code,
                "timed_out": True,
            }
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    def current_branch(self, workdir: str | None = None) -> str | None:
        """The checked-out branch in the repo workdir, or None if detached /
        not a repo."""
        result = self.session.execute_command(
            "git rev-parse --abbrev-ref HEAD", workdir=workdir or self.workdir
        )
        branch = (result.stdout or "").strip()
        if result.exit_code != 0 or not branch or branch == "HEAD":
            return None
        return branch

    def open_pull_request(
        self,
        *,
        base: str,
        head: str,
        title: str,
        body: str,
        workdir: str | None = None,
    ) -> dict[str, Any]:
        """Open a pull request with the `gh` CLI from inside the sandbox.

        Runs after the coding engine has committed and pushed `head` - the
        engine no longer opens PRs itself (no GitHub MCP server; see
        sandbox/config.toml). `gh` authenticates via the GH_TOKEN env var set
        on the container (see agents/pipeline_agent/sandbox_runtime.py) and
        infers the repo from the `origin` remote. If a PR for `head` already
        exists, returns that one's URL instead of failing.
        """
        workdir = workdir or self.workdir
        create = self.session.execute_command(
            "gh pr create "
            f"--base {shlex.quote(base)} --head {shlex.quote(head)} "
            f"--title {shlex.quote(title)} --body {shlex.quote(body)}",
            workdir=workdir,
        )
        combined = f"{create.stdout or ''}\n{create.stderr or ''}"
        url_match = re.search(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", combined)
        if create.exit_code == 0 and url_match:
            return {"success": True, "url": url_match.group(0), "stderr": create.stderr}

        # "a pull request for branch ... already exists" - recover its URL.
        if "already exists" in combined:
            view = self.session.execute_command(
                f"gh pr view {shlex.quote(head)} --json url --jq .url", workdir=workdir
            )
            view_url = (view.stdout or "").strip()
            if view.exit_code == 0 and view_url:
                return {"success": True, "url": view_url, "stderr": create.stderr, "existing": True}

        return {
            "success": False,
            "url": None,
            "stderr": create.stderr or create.stdout or "gh pr create failed",
            "exit_code": create.exit_code,
        }

    def generate_github_memory(
        self, out_path: str = _GITHUB_MEMORY_PATH, workdir: str | None = None
    ) -> dict[str, Any]:
        """Run the repo-snapshot script that writes a `github-memory/` skill
        the engine can read (branch history, PRs, conventions). Called once per
        sandbox at boot (see agents/pipeline_agent/sandbox_runtime.py), not per
        engine call - it makes a dozen-plus live GitHub API calls and only ever
        produces the same snapshot for a given container. Best-effort: a failure
        here must not abort the pipeline, so callers treat a non-zero exit as
        "no repository memory" and continue.
        """
        result = self.session.execute_command(
            f"python3 /usr/local/bin/generate_github_memory.py --out {shlex.quote(out_path)}",
            workdir=workdir,
        )
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "path": out_path,
        }

    def run_opencode(
        self,
        instruction: str,
        workdir: str | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        # --auto auto-approves permissions that aren't explicitly denied - the
        # opencode analogue of Codex's --dangerously-bypass-approvals-and-sandbox
        # above. This container is already the isolation boundary.
        argv = ["opencode", "run", "--auto"]
        chosen_model = model or self.opencode_model
        if chosen_model:
            argv.extend(["--model", chosen_model])
        argv.append(instruction)
        return self._exec_engine(argv, workdir, on_stdout, on_stderr)
