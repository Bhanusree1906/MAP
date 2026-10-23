"""Shared sandbox setup for Codex planning and Gemini-orchestrated execution.

Opens one container, injects Codex/Salesforce auth and project skills, and
can clone the project repo and write TICKET.md so Codex has the ticket
without the main agent drafting anything.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from agents.skills.service import (
    install_into_sandbox,
    install_memory_into_sandbox,
    list_project_installs,
)
from sandbox.manager import SandboxManager

from ._timing import timed
from .codex_credentials import get_codex_auth_json
from .config import Settings
from .salesforce_credentials import get_salesforce_connection

_CODEX_AUTH_JSON_PATH = "/home/agentuser/.codex/auth.json"
_CODEX_CONFIG_PATH = "/home/agentuser/.codex/config.toml"
_OPENCODE_CONFIG_PATH = "/home/agentuser/.config/opencode/opencode.json"
_SFDX_AUTH_URL_TMP_PATH = "/home/agentuser/.sfdx_auth_url.txt"
# Codex auto-loads ~/.codex/AGENTS.md as global instructions on every `codex
# exec` call, same mechanism as a repo-root AGENTS.md - writing the persona
# here means it applies across planning, retries, memory refresh, and
# orchestrator-delegated execution without touching any instruction string.
# opencode has no equivalent auto-load, so the same path is also added to its
# `instructions` array below (see _opencode_user_config).
_PERSONA_AGENTS_MD_PATH = "/home/agentuser/.codex/AGENTS.md"
# GitHub (and GitHub-compatible hosts) accept a PAT as the password with
# username `x-access-token`. Azure DevOps Git accepts any non-empty username
# with the PAT as password, so the same helper authenticates both providers -
# which one is in play only changes which token lands in GH_TOKEN
# (a GitHub PAT for github projects, an Azure PAT for azure_git projects).
# The project's stored git token is already in GH_TOKEN on the container. A
# shell helper is stored in Git config rather than a file under /home: the
# coding engine can never lose an executable between sandbox setup and its
# first push, and the token remains absent from `git remote -v` and
# `git config --list` output.
_GIT_CREDENTIAL_HELPER = (
        "!f() { test \"$1\" = get && "
        "printf 'username=x-access-token\\npassword=%s\\n' \"$GH_TOKEN\"; }; f"
)


def _fold_skills_into_agents_md(persona_md: str, skill_bodies: list[tuple[str, str]]) -> str:
    """Codex only auto-loads ~/.codex/AGENTS.md. Concatenate the installed
    SKILL.md bodies into it so they are always-on context and the worker never
    `cat`s files under ~/.codex/skills/ (WS-4.1)."""
    parts = [
        persona_md.rstrip(),
        "",
        "# Loaded skills",
        "",
        "The skills below are ALREADY in your context. Follow them. Do NOT "
        "`cat`, `find`, or `ls` anything under `~/.codex/skills/` — it is all here.",
    ]
    for slug, body in skill_bodies:
        parts.append(f"\n---\n\n## Skill: {slug}\n\n{(body or '').strip()}")
    return "\n".join(parts).rstrip() + "\n"


def _persona_agents_md(settings: Settings) -> str:
    if settings.persona_instructions:
        return settings.persona_instructions
    return (
        "# Identity\n\n"
        f"You are {settings.persona_name}, {settings.persona_role}. You are "
        f"{settings.persona_traits}.\n\n"
        f"When you introduce yourself, write commit messages, pull request "
        f"titles/descriptions, code comments, or any other text a human might "
        f"read, refer to yourself as {settings.persona_name}. Do not mention "
        "that you are Codex, ChatGPT, OpenAI, opencode, or any other "
        "underlying tool, vendor, or model name - that is an implementation "
        "detail, not part of your identity.\n\n"
        "This applies to everything you produce: git commits, pull request "
        "content, code comments, and any summary returned to whatever "
        "orchestrator delegated this task to you.\n"
    )


def _codex_user_config(model: str, reasoning_effort: str, max_threads: int) -> str:
    """Replace the image's ~/.codex/config.toml at boot so model/effort apply
    even to Codex-spawned subagents (CLI --model only covers the parent exec).

    No GitHub MCP server: the pipeline opens the pull request itself with the
    `gh` CLI after the engine returns (see SandboxManager.open_pull_request),
    so the engine only needs git to commit and push a branch.
    """
    threads = max(1, max_threads)
    return (
        f"model = {model!r}\n"
        f"model_reasoning_effort = {reasoning_effort!r}\n"
        'model_reasoning_summary = "none"\n'
        'model_verbosity = "low"\n'
        "\n"
        "[agents]\n"
        f"max_threads = {threads}\n"
        "max_depth = 1\n"
    )


def _opencode_user_config(model: str, instructions: list[str] | None = None) -> str:
    """Write ~/.config/opencode/opencode.json at boot, mirroring the Codex
    config above: pin the model. Like Codex, opencode only needs git here -
    the pull request is opened Python-side with `gh` after the run.

    instructions, if given, are markdown file paths already written into the
    sandbox: the persona file (_PERSONA_AGENTS_MD_PATH, always first - see
    _persona_agents_md) plus skill SKILL.md paths (see
    install_into_sandbox/install_memory_into_sandbox). opencode has no
    directory it auto-reads these from the way Codex does, so this is the
    documented mechanism (opencode.json's `instructions` array) that actually
    gets their content in front of the model.
    """
    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
    }
    if instructions:
        config["instructions"] = instructions
    return json.dumps(config, indent=2)


OnEvent = Callable[[str, str], None]


def authenticate_salesforce_org(sandbox: SandboxManager, connection) -> None:
    """`sf org login sfdx-url` into the sandbox and set it as the default target."""
    sandbox.write_file(_SFDX_AUTH_URL_TMP_PATH, connection.sfdx_auth_url)
    login_result = sandbox.execute(
        f"sf org login sfdx-url --sfdx-url-file {_SFDX_AUTH_URL_TMP_PATH} "
        f"--alias {connection.org_alias} --set-default"
    )
    sandbox.execute(f"rm -f {_SFDX_AUTH_URL_TMP_PATH}")
    if not login_result["success"]:
        raise RuntimeError(
            f"Failed to authenticate Salesforce org {connection.org_alias!r} "
            f"in the sandbox: {login_result['stderr'] or login_result['stdout']}"
        )


@contextmanager
def open_sf_cli_sandbox(
    settings: Settings, on_event: OnEvent | None = None
) -> Iterator[SandboxManager]:
    """Salesforce CLI only: org login, no Codex, no skills, no GitHub memory.

    Used by the one-shot project bootstrap (org-catalog dump + DX scaffold,
    see salesforce_bootstrap.py) so those don't wait on a full pipeline boot.
    """
    connection = (
        get_salesforce_connection(settings.salesforce_connection_name)
        if settings.salesforce_connection_name
        else None
    )
    if connection is None or not connection.sfdx_auth_url:
        raise RuntimeError("No Salesforce connection with an auth URL is linked to this project")

    env = {}
    if settings.git_token:
        env["GH_TOKEN"] = settings.git_token
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"

    if on_event:
        on_event("step", "Opening Salesforce CLI sandbox")
    with SandboxManager(
        image=settings.sandbox_image,
        workdir=settings.sandbox_workdir,
        env=env or None,
    ) as sandbox:
        if on_event:
            on_event("step", f"Authenticating Salesforce org `{connection.org_alias}`")
        with timed(on_event, "sf org login"):
            authenticate_salesforce_org(sandbox, connection)
        yield sandbox


@contextmanager
def open_pipeline_sandbox(
    settings: Settings,
    on_event: OnEvent | None = None,
    *,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
    phase: str = "both",
) -> Iterator[SandboxManager]:
    """Yield a sandbox with Codex credentials, project skills, and memory ready.

    codex_model / codex_reasoning_effort default to the execution-phase pins
    on Settings. Planning passes the cheaper planning pins explicitly.

    phase ("planning" | "implementation" | "both") scopes which attached
    project skills install for this boot - see ProjectSkill.phase and
    list_project_installs.
    """
    model = codex_model or settings.codex_execution_model
    effort = codex_reasoning_effort or settings.codex_execution_reasoning_effort
    if on_event:
        on_event("step", f"Codex model {model} (reasoning: {effort})")

    codex_auth_json = get_codex_auth_json()

    env = {}
    if codex_auth_json is None and settings.codex_api_key:
        env["OPENAI_API_KEY"] = settings.codex_api_key
    if settings.git_token:
        # GH_TOKEN feeds the git credential helper the same way for both hosts
        # (see _GIT_CREDENTIAL_HELPER / configure_sandbox_git). The GitHub-only
        # helpers (gh CLI, memory root) get their vars only for github repos so
        # an Azure PAT is never handed to the GitHub API.
        env["GH_TOKEN"] = settings.git_token
        if settings.git_provider == "azure":
            env["AZURE_REPO_URL"] = settings.repository_url
        else:
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = settings.git_token
            env["GITHUB_MEMORY_REPO_URL"] = settings.repository_url
            env["GITHUB_MEMORY_USERNAME"] = settings.repository_url.rstrip("/").split("/")[-2]
            env["GH_REPO_URL"] = settings.repository_url
            env["GH_USERNAME"] = settings.repository_url.rstrip("/").split("/")[-2]

    salesforce_connection = (
        get_salesforce_connection(settings.salesforce_connection_name)
        if settings.salesforce_connection_name
        else None
    )

    with SandboxManager(
        image=settings.sandbox_image,
        workdir=settings.sandbox_workdir,
        env=env or None,
        codex_model=model,
        codex_reasoning_effort=effort,
        opencode_model=settings.opencode_model,
        engine_timeout_seconds=int(os.getenv("PIPELINE_AGENT_ENGINE_TIMEOUT", "1800")),
    ) as sandbox:
        written = sandbox.write_file(
            _CODEX_CONFIG_PATH,
            _codex_user_config(model, effort, settings.codex_max_threads),
        )
        if not written["success"]:
            raise RuntimeError(f"Failed to write Codex config: {written.get('error')}")

        if codex_auth_json:
            sandbox.write_file(_CODEX_AUTH_JSON_PATH, codex_auth_json)

        persona_written = sandbox.write_file(_PERSONA_AGENTS_MD_PATH, _persona_agents_md(settings))
        if not persona_written["success"]:
            raise RuntimeError(f"Failed to write persona instructions: {persona_written.get('error')}")

        if salesforce_connection:
            authenticate_salesforce_org(sandbox, salesforce_connection)

        skill_md_paths: list[str] = [_PERSONA_AGENTS_MD_PATH]
        skill_bodies: list[tuple[str, str]] = []  # (slug, SKILL.md body) for the AGENTS.md fold
        with timed(on_event, "skill install"):
            for install in list_project_installs(settings.project_id, phase=phase):
                if on_event:
                    on_event("step", f"Installing skill {install.slug}@{install.version} ({settings.engine})")
                skill_result = install_into_sandbox(
                    sandbox, install.slug, version=install.version, engine=settings.engine
                )
                if not skill_result["success"]:
                    raise RuntimeError(
                        f"Failed to install skill {install.slug!r} in the sandbox: {skill_result['error']}"
                    )
                if skill_result.get("skill_md_path"):
                    skill_md_paths.append(skill_result["skill_md_path"])
                if skill_result.get("skill_md"):
                    skill_bodies.append((install.slug, skill_result["skill_md"]))

        with timed(on_event, "project memory install"):
            memory_result = install_memory_into_sandbox(sandbox, settings.project_id, engine=settings.engine)
        if memory_result is not None:
            if on_event:
                on_event("step", "Installing project codebase memory")
            if not memory_result["success"]:
                raise RuntimeError(
                    f"Failed to install project memory in the sandbox: {memory_result['error']}"
                )
            if memory_result.get("skill_md_path"):
                skill_md_paths.append(memory_result["skill_md_path"])
            if memory_result.get("skill_md"):
                skill_bodies.append(("project-memory", memory_result["skill_md"]))

        # WS-4.1: Codex only auto-loads ~/.codex/AGENTS.md. Loose SKILL.md files
        # in ~/.codex/skills/ were never read automatically — the worker `cat`ed
        # them by hand every session and guessed wrong paths. Fold the bodies
        # into AGENTS.md so they are always-on context, zero tool calls.
        # opencode keeps the file-path list via opencode.json below.
        if settings.engine == "codex" and skill_bodies:
            fold_written = sandbox.write_file(
                _PERSONA_AGENTS_MD_PATH,
                _fold_skills_into_agents_md(_persona_agents_md(settings), skill_bodies),
            )
            if not fold_written["success"]:
                raise RuntimeError(f"Failed to fold skills into AGENTS.md: {fold_written.get('error')}")
            if on_event:
                on_event("step", f"Folded {len(skill_bodies)} skills into AGENTS.md")

        # GitHub repo snapshot (branch history, PRs, conventions) as a skill the
        # engine can read. A dozen-plus live GitHub API calls (5-7s in the
        # traces). WS-3.3: OFF by default for BOTH phases now - the planner
        # inspects the repo directly (`rg --files`, `git log`) and the approved
        # plan carries the understanding forward. PIPELINE_AGENT_GITHUB_MEMORY:
        # `always` = both phases, `planning` = planning only, anything else = off.
        _gh_mem = os.getenv("PIPELINE_AGENT_GITHUB_MEMORY", "off").strip().lower()
        _want_gh_mem = _gh_mem == "always" or (_gh_mem == "planning" and phase == "planning")
        # GitHub-only: the memory snapshot calls the GitHub API, so it never
        # runs against an Azure DevOps Git remote.
        if settings.git_provider == "github" and settings.git_token and _want_gh_mem:
            if on_event:
                on_event("step", "Generating GitHub repository memory")
            with timed(on_event, "github memory"):
                gh_memory = sandbox.generate_github_memory()
            if not gh_memory["success"] and on_event:
                on_event(
                    "step",
                    "GitHub memory generation failed; continuing without repository memory. "
                    f"{(gh_memory.get('stderr') or gh_memory.get('stdout') or '').strip()[:500]}",
                )

        if settings.engine == "opencode":
            if on_event:
                on_event("step", f"opencode model {settings.opencode_model}")
            # Unlike ~/.codex (created at image build time - see
            # sandbox/Dockerfile's config.toml COPY), nothing pre-creates
            # ~/.config/opencode, and write_file (docker cp) needs the parent
            # directory to already exist.
            sandbox.execute("mkdir -p /home/agentuser/.config/opencode")
            opencode_written = sandbox.write_file(
                _OPENCODE_CONFIG_PATH,
                _opencode_user_config(settings.opencode_model, instructions=skill_md_paths),
            )
            if not opencode_written["success"]:
                raise RuntimeError(f"Failed to write opencode config: {opencode_written.get('error')}")

        yield sandbox


def configure_sandbox_git(sandbox: SandboxManager, settings: Settings) -> None:
    """Make commit and push work without Codex inventing credentials.

    Two different identities are in play and they must not be mixed:

    - Commit author: PIPELINE_AGENT_BOT_NAME / PIPELINE_AGENT_BOT_EMAIL
      (`git config user.name` / `user.email`). This is only the stamp on
      commits. Git refuses to commit if it is missing.
    - HTTPS auth: username `x-access-token`, password = the project's stored
      git token (`GH_TOKEN`). That is how clone and push authenticate. The
      bot name is not a GitHub login.

    Origin is rewritten to the public https URL so `git remote -v` does not
    show the token. Push still authenticates via the credential helper.
    """
    name = shlex.quote(settings.bot_git_name)
    email = shlex.quote(settings.bot_git_email)
    helper = shlex.quote(_GIT_CREDENTIAL_HELPER)
    origin = shlex.quote(settings.repository_url)
    # sandbox.execute() shlex-splits the command (no shell) unless a timeout is
    # given, so `&&` chaining does NOT work here — one exec per command.
    commands = [
        f"git config user.name {name}",
        f"git config user.email {email}",
        f"git config --global user.name {name}",
        f"git config --global user.email {email}",
        f"git config --global credential.helper {helper}",
        "git config --global credential.useHttpPath true",
    ]
    if settings.git_provider == "azure":
        # Rewrite Azure's ssh:// and git@ssh.dev.azure.com:v3/ remotes to the
        # https form so the credential helper (not an SSH key) authenticates.
        commands += [
            "git config --global --add url.https://dev.azure.com/.insteadOf git@ssh.dev.azure.com:v3/",
            "git config --global --add url.https://dev.azure.com/.insteadOf ssh://git@ssh.dev.azure.com/",
        ]
    else:
        commands += [
            "git config --global --add url.https://github.com/.insteadOf git@github.com:",
            "git config --global --add url.https://github.com/.insteadOf ssh://git@github.com/",
        ]
    commands.append(f"git remote set-url origin {origin}")
    for command in commands:
        result = sandbox.execute(command)
        if not result.get("success"):
            raise RuntimeError(
                f"Failed to configure git ({command}): "
                f"{result.get('stderr') or result.get('stdout') or 'unknown error'}"
            )
    remote = sandbox.execute("git ls-remote --exit-code origin HEAD")
    if not remote.get("success"):
        raise RuntimeError(
            "GitHub authentication check failed after configuring push credentials: "
            f"{remote.get('stderr') or remote.get('stdout') or 'unknown error'}"
        )


# Pipeline inputs / build noise the worker may touch but must never land in the
# feature-branch diff. TICKET.md used to merge-conflict every ticket; a
# `git rm --cached` staged deletion of it (and .pipeline/ORG_CATALOG.md,
# package-lock.json) leaked into the branch and review flagged them as
# "unrelated file changes" (run 176ded49).
_PIPELINE_EXCLUDE_PATHS = (
    "TICKET.md",
    "PLAN.json",
    "GROUND_TRUTH.md",
    "ticket-attachments/",
    ".pipeline/",
    "node_modules",  # no trailing slash — also matches the baked-toolchain symlink
    "node_modules/",
    "package-lock.json",
)


def _exclude_pipeline_files(sandbox: SandboxManager, on_event: OnEvent | None = None) -> None:
    """Keep pipeline scaffolding and npm build noise out of every commit.

    Two mechanisms, because .git/info/exclude only covers UNTRACKED paths:
    - append the paths to .git/info/exclude (per-clone, not the repo's tracked
      .gitignore) so `git add -A` skips new/created files like
      .pipeline/ORG_CATALOG.md and a freshly generated package-lock.json.
    - for paths the repo already tracks (TICKET.md merged in from earlier PRs),
      `git update-index --skip-worktree` so the worker's edits/deletes are
      invisible to `git add -A` WITHOUT staging a deletion that would show up
      in `git diff main...HEAD`.

    Runs AFTER `git switch -C feature/<TICKET>` so the checkout cannot reset the
    index changes. Best-effort.
    """
    # sandbox.execute() has no shell, so `>>` redirection does not work here —
    # read the existing exclude file and rewrite it with our entries appended.
    block = "".join(f"{path}\n" for path in _PIPELINE_EXCLUDE_PATHS)
    sandbox.execute("mkdir -p .git/info")
    existing = sandbox.read_file(".git/info/exclude")
    current = existing.get("content", "") if existing.get("success") else ""
    if "# pipeline scaffolding" not in current:
        sandbox.write_file(
            ".git/info/exclude",
            current.rstrip() + "\n\n# pipeline scaffolding (added by prepare_sandbox_repo)\n" + block,
        )
    files = " ".join(shlex.quote(p.rstrip("/")) for p in _PIPELINE_EXCLUDE_PATHS)
    listed = sandbox.execute(f"git ls-files -z -- {files}")
    tracked = [f for f in (listed.get("stdout") or "").split("\0") if f.strip()]
    if tracked:
        sandbox.execute(
            "git update-index --skip-worktree -- "
            + " ".join(shlex.quote(f) for f in tracked)
        )
        if on_event:
            on_event("step", f"Ignoring worker edits to tracked scaffolding: {', '.join(tracked[:4])}")


_BAKED_NODE_MODULES = "/opt/sf-toolchain/node_modules"


def _npm_install_project(sandbox: SandboxManager, on_event: OnEvent | None = None) -> None:
    """Get the repo's lint/format/test toolchain onto disk before the worker
    runs `npm run lint` / `prettier` / `test:unit`.

    Fast path (WS-2.1): if the image baked /opt/sf-toolchain/node_modules and
    the repo ships no node_modules, symlink it (~0.5s). The pipeline generates
    repos from the standard SF DX template so the dep set matches; `npm run
    <script>` resolves through the symlink.

    Fallback: `npm ci` (27-35s). Used when there is no baked toolchain (base
    image not rebuilt) or PIPELINE_AGENT_NPM_MODE=ci. `npm ci` never rewrites
    package-lock.json; the no-lockfile path uses --no-save --package-lock=false
    (a fresh 12k-line lockfile on the branch was 90% of run 176ded49's diff).
    Best-effort.
    """
    has_pkg = sandbox.execute("test -f package.json")
    if not has_pkg.get("success"):
        return

    # Default `ci` for now: the `ln -s` symlink confused Codex (it staged
    # `node_modules` as a change) and broke global-prettier plugin resolution
    # (run df1198c7). Set PIPELINE_AGENT_NPM_MODE=link once the image is
    # rebuilt with `bc997cd` (real exclude) + a wrapper that runs the LOCAL
    # prettier/eslint.
    mode = os.getenv("PIPELINE_AGENT_NPM_MODE", "ci").strip().lower()
    if mode in ("link", "auto"):
        # timeout_seconds forces the bash -lc wrap so `&&` works.
        linked = sandbox.execute(
            f"test -d {shlex.quote(_BAKED_NODE_MODULES)} && test ! -e node_modules "
            f"&& ln -s {shlex.quote(_BAKED_NODE_MODULES)} node_modules "
            "&& echo linked",
            timeout_seconds=30,
        )
        if linked.get("success") and "linked" in (linked.get("stdout") or ""):
            if on_event:
                on_event("step", "Linked baked npm toolchain (/opt/sf-toolchain)")
            return
        if mode == "link":
            if on_event:
                on_event("step", "Baked npm toolchain not available; skipping npm (link mode)")
            return

    if on_event:
        on_event("step", "Installing repo npm toolchain (npm ci)")
    with timed(on_event, "npm ci"):
        # `npm ci` never rewrites package-lock.json. The fallback for a repo
        # that ships no lockfile uses --no-save --package-lock=false so npm
        # cannot create one (a fresh 12k-line package-lock.json committed onto
        # the feature branch was 90% of run 176ded49's diff).
        result = sandbox.execute(
            "HUSKY=0 npm ci --no-audit --no-fund "
            "|| HUSKY=0 npm install --no-audit --no-fund --no-save --package-lock=false",
            timeout_seconds=int(os.getenv("PIPELINE_AGENT_NPM_TIMEOUT", "420")),
        )
    # Belt-and-suspenders: undo any lockfile churn npm made anyway.
    sandbox.execute("git checkout -- package-lock.json 2>/dev/null || true", timeout_seconds=30)
    if not result.get("success") and on_event:
        tail = (result.get("stderr") or result.get("stdout") or "").strip()[-500:]
        on_event("step", f"npm install did not complete cleanly; continuing with global tools. {tail}")


def _delete_stale_feature_branches(
    sandbox: SandboxManager, ticket_key: str, on_event: OnEvent | None = None
) -> None:
    """Remove any `feature/<TICKET>-*` branch left on origin by an earlier run.

    A previous attempt for this ticket can push a branch with an empty or
    half-done commit and then fail. The next run clones it, sees a commit
    "ahead of main", and the worker treats that stale commit as the
    implementation (PGD-48: two sessions both said "the prior implementation
    is present", committed only TICKET.md, and passed validate). Deleting the
    remote up front makes every run branch fresh from the default branch.

    Best-effort: a failure here just means the worker still has to avoid the
    stale branch itself (the assignment tells it to).
    """
    # WS-3.2: ask the server for only this ticket's refs instead of every head
    # in the repo — one glob-scoped round-trip, no client-side scan.
    patterns = (
        f"refs/heads/feature/{ticket_key}",
        f"refs/heads/feature/{ticket_key}-*",
    )
    listed = sandbox.execute(
        "git ls-remote --heads origin " + " ".join(shlex.quote(p) for p in patterns)
    )
    if not listed.get("success"):
        return
    prefix = f"feature/{ticket_key}-"
    stale: list[str] = []
    for line in (listed.get("stdout") or "").splitlines():
        parts = line.split("\t", 1) if "\t" in line else line.split(None, 1)
        if len(parts) != 2:
            continue
        ref = parts[1].strip()
        if not ref.startswith("refs/heads/"):
            continue
        branch = ref[len("refs/heads/") :]
        # trust the server glob, but keep a client-side guard so a git that
        # ignores the pattern can never delete `main` or another ticket.
        if branch == f"feature/{ticket_key}" or branch.startswith(prefix):
            stale.append(branch)
    for branch in stale:
        if on_event:
            on_event("step", f"Deleting stale branch origin/{branch} from a prior run")
        deleted = sandbox.execute(f"git push origin --delete {shlex.quote(branch)}")
        if not deleted.get("success") and on_event:
            on_event(
                "step",
                f"Could not delete origin/{branch}: "
                f"{(deleted.get('stderr') or deleted.get('stdout') or '').strip()[:300]}",
            )


def _checkout_ticket_branch(
    sandbox: SandboxManager, settings: Settings, ticket_key: str, on_event: OnEvent | None = None
) -> None:
    """Create and check out `feature/<TICKET>` from origin/<default> so the
    coding agent never has to manage branch state itself.

    The installed skills already tell the worker "the pipeline checked out the
    ticket branch, stay on it". Before this, that was a lie on the manager
    path — the worker was on the default branch and had to `git switch` — and
    a worker told to "branch fresh" would `git stash` its own edits and reset
    to origin/main, losing the implementation (PGD-12). The name is
    deterministic (no slug) so stale-branch cleanup and delivery agree on it.
    """
    default = settings.default_branch
    branch = f"feature/{ticket_key}"
    result = sandbox.execute(
        f"git fetch origin {shlex.quote(default)} "
        f"&& git switch -C {shlex.quote(branch)} origin/{shlex.quote(default)}",
        timeout_seconds=60,  # forces the shell wrap so `&&` actually runs
    )
    if not result.get("success"):
        result = sandbox.execute(f"git switch -C {shlex.quote(branch)}")
    if not result.get("success"):
        raise RuntimeError(
            f"Failed to check out {branch}: "
            f"{result.get('stderr') or result.get('stdout') or 'unknown error'}"
        )
    if on_event:
        on_event("step", f"Checked out {branch} from origin/{default}")


def prepare_sandbox_repo(
    sandbox: SandboxManager,
    settings: Settings,
    ticket_markdown: str,
    on_event: OnEvent | None = None,
    image_attachments: list[tuple[str, bytes, str]] | None = None,
    *,
    ticket_key: str | None = None,
    install_npm: bool = False,
) -> None:
    """Clone the project repo and write TICKET.md for the coding agent to read.

    When ticket_key is given (the execution phase), stale `feature/<TICKET>-*`
    branches left on origin by earlier attempts are deleted, then a fresh
    `feature/<TICKET>` is checked out from origin/<default> so the worker
    starts on the ticket branch and only has to commit and push.
    """
    if on_event:
        on_event("step", f"Cloning {settings.repository_url}")
    with timed(on_event, "git clone"):
        clone = sandbox.clone_repository(settings.repository_url, token=settings.git_token)
    if not clone["success"]:
        raise RuntimeError(f"Clone failed: {clone['error']}")

    if on_event:
        on_event("step", "Configuring git identity and push credentials")
    configure_sandbox_git(sandbox, settings)

    if ticket_key and settings.git_token:
        with timed(on_event, "stale branch cleanup"):
            _delete_stale_feature_branches(sandbox, ticket_key, on_event)
    if ticket_key:
        _checkout_ticket_branch(sandbox, settings, ticket_key, on_event)

    # After the branch checkout so `git switch -C` can't reset the index changes.
    _exclude_pipeline_files(sandbox, on_event)

    if install_npm:
        _npm_install_project(sandbox, on_event)

    if on_event:
        on_event("step", "Writing TICKET.md")
    written = sandbox.write_file("TICKET.md", ticket_markdown)
    if not written["success"]:
        raise RuntimeError(f"Failed to write TICKET.md: {written['error']}")

    if image_attachments:
        directory = sandbox.execute("mkdir -p ticket-attachments")
        if not directory["success"]:
            raise RuntimeError(f"Failed to create ticket attachment directory: {directory['stderr']}")
    for filename, content, _mime_type in image_attachments or []:
        safe_name = filename.replace("/", "_").replace("\\", "_")
        attachment_path = f"ticket-attachments/{safe_name}"
        written = sandbox.write_bytes(attachment_path, content)
        if not written["success"]:
            raise RuntimeError(f"Failed to write ticket attachment {filename!r}: {written['error']}")
