"""LangChain tools for repository and command operations inside a live
sandbox session. Bound to one already-open SandboxManager so cloning,
running commands, and invoking Codex all share the same container - see
tools/file_tools.py for the matching read/write tools.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.tools import BaseTool, tool

from agents.pipeline_agent._timing import timed
from sandbox.manager import SandboxManager


def build_sandbox_tools(
    sandbox: SandboxManager,
    *,
    repo_url: str,
    git_token: str | None,
    on_event: Callable[[str, str], None] | None = None,
    include_clone: bool = True,
    engine: str = "codex",
) -> list[BaseTool]:
    """on_event(kind, content), if given, is called with the coding agent's
    own live stdout/stderr as it works inside run_codex/run_opencode - the
    one tool call here that can run for minutes and otherwise looks like a
    single opaque step from the orchestrator's point of view (see
    agents/pipeline_agent/runner.py, which emits "step" events for tool
    calls themselves; this is the complementary, finer-grained stream from
    *inside* one of them).

    include_clone=False when the pipeline already cloned the repo (planning
    and execution both write TICKET.md in Python before the agent starts).

    engine picks which coding-agent tool gets exposed to the orchestrator:
    "codex" (default) or "opencode" - see agents/pipeline_agent/config.py.
    """

    @tool
    def clone_repository(clone_path: str = "") -> str:
        """Clone the configured project repository into the sandbox.

        Call this first, before reading or writing any files. clone_path is
        relative to the sandbox workdir; leave empty to clone directly into
        the workdir root.
        """
        result = sandbox.clone_repository(repo_url, token=git_token, clone_path=clone_path or None)
        if not result["success"]:
            return f"Clone failed:\n{result['error']}"
        return f"Cloned {repo_url} into {result['path']}"

    @tool
    def run_command(command: str, workdir: str = "") -> str:
        """Run a shell command inside the sandbox and return its output.

        Use this to inspect the repo (ls, cat, grep), run tests, check git
        status/diff, or anything else a shell can do.
        """
        result = sandbox.execute(command, workdir=workdir or None)
        parts = [f"exit_code: {result['exit_code']}"]
        if result["stdout"]:
            parts.append(f"stdout:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"stderr:\n{result['stderr']}")
        return "\n".join(parts)

    @tool
    def run_codex(instruction: str, workdir: str = "") -> str:
        """Delegate to the coding agent inside the sandbox with the given
        instruction to make code changes, then return its output.
        """
        on_stdout = (lambda chunk: on_event("codex_stdout", chunk)) if on_event else None
        on_stderr = (lambda chunk: on_event("codex_stderr", chunk)) if on_event else None
        with timed(on_event, "coding agent exec"):
            result = sandbox.run_codex(instruction, workdir=workdir or None, on_stdout=on_stdout, on_stderr=on_stderr)
        parts = [f"exit_code: {result['exit_code']}"]
        if result["stdout"]:
            parts.append(f"stdout:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"stderr:\n{result['stderr']}")
        return "\n".join(parts)

    @tool
    def run_opencode(instruction: str, workdir: str = "") -> str:
        """Delegate to the coding agent inside the sandbox with the given
        instruction to make code changes, then return its output.
        """
        on_stdout = (lambda chunk: on_event("codex_stdout", chunk)) if on_event else None
        on_stderr = (lambda chunk: on_event("codex_stderr", chunk)) if on_event else None
        with timed(on_event, "coding agent exec"):
            result = sandbox.run_opencode(instruction, workdir=workdir or None, on_stdout=on_stdout, on_stderr=on_stderr)
        parts = [f"exit_code: {result['exit_code']}"]
        if result["stdout"]:
            parts.append(f"stdout:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"stderr:\n{result['stderr']}")
        return "\n".join(parts)

    coding_agent_tool = run_opencode if engine == "opencode" else run_codex
    tools = [run_command, coding_agent_tool]
    if include_clone:
        tools.insert(0, clone_repository)
    return tools
