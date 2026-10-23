"""Gemini manager that assigns Codex workers inside one pipeline run.

Phase 0 used create_deep_agent with write_file / run_command / Jira MCP on
the orchestrator. This keeps the identity (orchestrator, not implementer) and
drives it with a LangGraph StateGraph. Codex is the only writer. Validate,
review, deliver, and the Jira report are tools on the same run — not extra
tickets and not a 3-attempt restart loop.

A worker call is one `codex exec`. The first session is told to use the
harness (sf CLI, skills, org) and fix deploy/tests/PMD itself. A second
session is allowed only after validate failed, in the same sandbox, with a
diary of what already happened. The 1-hour phase timeout is the wall.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Annotated, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from agents.jira_agent.connections import get_connection as get_jira_connection
from agents.jira_agent.rest import add_comment
from agents.azure_agent.connections import get_connection as get_azure_connection
from agents.azure_agent.rest import add_comment as add_azure_comment
from agents.skills.service import fetch_memory_markdown, get_skill, list_project_installs

from .classify import budget_for, classify_ticket
from .pr import open_pull_request_aware

_MAX_TURNS = 24  # ceiling; the real per-run cap is session.max_turns (see classify.budget_for)
_RECURSION_LIMIT = 2 * _MAX_TURNS + 4
_DIARY_CAP = 8
_DIARY_ITEM_CHARS = 2500

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# `sf` deploy/analyzer progress spam: braille spinner frames, status glyphs,
# per-component counters, "In Progress" polls. Keep the final Status: line,
# the component table, and real error text.
_SF_SPINNER_RE = re.compile(
    r"^.*(?:"
    r"[⠀-⣿]"                                            # braille spinner frame
    r"|Elapsed Time:|Waiting for the org|Status: In Progress"
    r"|\bComponents:\s*\d+/\d+"                          # "▸ Components: 5/7 (71%)"
    r"|Deploying Metadata\b.*?\d+(?:%|\.\d+s|ms)"        # progress line variants
    r"|^[ \t]*[◼◻◯○●✔✓✗✘⚠▸►](?:\s|$)"                    # a line that is just a glyph
    r"|^[ \t]*(?:Preparing|Running Tests|Updating Source Tracking|Done)\b.*\d+\s*ms"
    r").*$",
    re.MULTILINE,
)


def _clean(text: str, keep_last_lines: int | None = None) -> str:
    """Strip ANSI escapes and `sf` progress-spinner spam before text enters the
    model's context (diary, review prompt, PR/Jira body). One `sf` deploy emits
    hundreds of `^[[2K^[[1A ... Elapsed Time: 1.9s` lines — pure noise that
    then rides in every later session's diary. keep_last_lines trims long CLI
    dumps to their tail (where the result actually is)."""
    if not text:
        return ""
    text = _ANSI_RE.sub("", text)
    text = _SF_SPINNER_RE.sub("", text)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()
    if keep_last_lines:
        lines = text.splitlines()
        if len(lines) > keep_last_lines:
            text = "…\n" + "\n".join(lines[-keep_last_lines:])
    return text
# Git-ignored scratch dir inside the repo (see sandbox_runtime._PIPELINE_EXCLUDE_PATHS).
# The worker reads verified org/repo facts from ORG_CATALOG.md instead of
# re-describing the whole org every session, and drops throwaway helper
# scripts under scripts/.
_PIPELINE_DIR = ".pipeline"
_ORG_CATALOG_SANDBOX_PATH = f"{_PIPELINE_DIR}/ORG_CATALOG.md"
# A failed review re-opens delegation exactly this many times. Without a cap a
# worker that keeps missing the requirements loops until the step limit.
_MAX_REVIEW_RETRIES = 1
# A validate that keeps failing re-opens delegation. Without a cap the manager
# thrashes until the step limit (run df1198c7: ~10 delegate/validate pairs on a
# ListView the worker could not make deploy). After this many validate FAILs,
# the manager must report_to_ticket(blocked) with the accumulated gate text.
_MAX_VALIDATE_RETRIES = 3
# Paths a feature branch may change without counting as an implementation.
# `sf project deploy --dry-run` reads the filesystem, so a branch whose only
# commit adds TICKET.md still "deploys" green (PGD-48).
_NONSOURCE_PREFIXES = (
    "TICKET.md",
    "TICKET-",
    "PLAN.json",
    "ticket-attachments/",
    ".pipeline/",
    "package-lock.json",
    "node_modules/",
)
# Pathspec that drops the same noise from the diff shown to the reviewer, so a
# stray package-lock.json / TICKET.md deletion never reads as "unrelated
# changes" and fails review (run 176ded49).
_DIFF_EXCLUDE_PATHSPEC = (
    ":(exclude)TICKET.md",
    ":(exclude)TICKET-*",
    ":(exclude)PLAN.json",
    ":(exclude)GROUND_TRUTH.md",
    ":(exclude)ticket-attachments/**",
    ":(exclude).pipeline/**",
    ":(exclude)package-lock.json",
    ":(exclude)node_modules/**",
)

_SYSTEM_PROMPT = """\
You are the engineering manager for one Jira ticket. You are an ORCHESTRATOR,
not an implementer. You do not write or edit source code. You do not run git.
You do not open the pull request yourself.

The repository is already cloned. The org (if Salesforce) is already logged
in. Skills are already installed in the sandbox — Codex will not use them
unless the assignment tells it to open them.

MAKE THE CALL. Real tickets have gaps — an unspecified default value, an
unstated permission, a layout position not pinned down. That is NOT a reason to
block. The worker must pick the conventional, safe default (a new picklist's
default is its first/safest value; a new field is optional unless the ticket
says required; Sales users get read access unless the ticket says edit), STATE
the assumption in the commit message and PR body, and implement. A later ticket
can change it. "blocked" is only for: technically impossible, contradicts the
org's real schema, or the same wall hit after real worker attempts. You may
NEVER report blocked without having called delegate_worker at least once.

Your loop, on this same ticket and this same run:

1. The ticket, the approved plan, the org catalog and the skill list are ALL
   in the opening message. Read them there. Do not call inspect_context for
   static data and do not call validate yet — go straight to delegate_worker.
   inspect_context is only for `branch` / `validation` once work has started.
2. delegate_worker with a brief in your own words. That starts one Codex
   session. Tell it what to build, which reasonable defaults to assume for any
   gap in the ticket, to run the ONE focused Apex test class and `sf
   code-analyzer` once and fix what they report — but NOT to run the dry-run
   deploy (validate owns that). It must commit and push a feature branch and
   must NOT open a pull request. If a
   session ends early (timeout / turn budget), the tool auto-commits and pushes
   whatever it left uncommitted and tells you so in the observation — that work
   is NOT lost, go straight to validate.
3. validate — ground truth on the branch Codex pushed. A Salesforce dry-run
   can pass on uncommitted files; validate still FAILS unless the feature
   branch has unique commits ahead of the default branch. GitHub cannot open
   a PR from an empty range (`No commits between main and feature/...`).
4. If validate failed: delegate_worker AGAIN. The tool will attach a diary of
   what already changed and what the gate said. Write a NEW brief that names
   what failed and what to change — an identical brief is refused outright. If
   the failure is uncommitted / zero unique
   commits, tell Codex to `git add`, commit, and push — do not report blocked
   just because deliver was refused. If validate says the branch only changes
   TICKET.md / scaffolding, the implementation was never written — re-delegate
   with the acceptance criteria spelled out. If the failure is a plan or org
   constraint (already implemented, missing object the ticket never asked to
   create), stop and report_to_ticket with outcome blocked or
   already_implemented.
5. If validate passed: review.
   - review pass or warn: deliver, then ship, then report_to_ticket.
   - review fail: delegate_worker ONE more time, quoting exactly what review
     said was missing, then validate and review again. If review still fails,
     stop and report_to_ticket with outcome blocked. Never deliver over a
     failing review.
   - review returns an ERROR (empty diff while the branch has commits): this
     is a tooling problem, not the worker's fault. Do NOT re-delegate. Call
     validate again and, if it passes, deliver.
6. ship — deploys the branch to the CONNECTED ORG for real and merges the PR.
   A pull request alone never touches the org; this is the step that does. If
   the real deploy fails, the observation has the errors — re-delegate ONCE to
   fix them (they are real org-rejection errors, unlike a dry-run), then
   validate / review / deliver / ship again. If merge is blocked by branch
   protection, that is fine — the org still got the code; note it and continue.
7. You must finish by calling report_to_ticket. Write that comment as an
   engineer to a colleague: what happened, API names, the real deploy id and
   result, what was verified, what was not, and every assumption you made for
   an unspecified detail. Never claim a check passed that you did not see pass.
   Never open with "the automated pipeline completed the implementation".

Call delegate_worker again only after validate has failed, after deliver
refused because nothing was committed, after review returned fail (once), or
after ship's REAL org deploy failed. Do not spawn extra Codex sessions to
polish a gate that already passed validate AND review with commits on the
branch.
"""


class inspect_context(BaseModel):
    """Read one piece of ground truth. Cheap — use it before assigning work."""

    what: Literal["ticket", "plan", "org_memory", "branch", "validation", "skills"] = Field(
        description=(
            "ticket = TICKET.md; plan = approved plan text; org_memory = project "
            "memory / org catalog; branch = current branch and changed files; "
            "validation = latest gate report; skills = installed skill slugs."
        )
    )


class delegate_worker(BaseModel):
    """Start one Codex session in this sandbox to implement (or fix) the ticket.

    Each call is a fresh exec, but the sandbox and git history persist. After
    the first session, call this again if validate failed, if deliver refused
    because the branch has no unique commits, or if review returned fail (the
    tool allows that once and prepends what review said was missing). The tool
    also prepends a diary of what already happened so Codex is not starting
    cold. Do not call this to polish a gate that already passed validate AND
    review with commits on the branch.
    """

    brief: str = Field(
        description=(
            "The assignment in your words. Name the ticket, the acceptance "
            "criteria that still need work, and which installed skills to open."
        )
    )


class validate(BaseModel):
    """Run ground-truth checks on the current branch without changing code.

    Salesforce: dry-run deploy. Always: git status, log, diff vs default branch.
    """


class review(BaseModel):
    """Have a coding agent review the branch diff. It must not change code."""


class deliver(BaseModel):
    """Open the pull request from the feature branch. Refuses while validate
    is failing or the branch is still the default branch."""


class ship(BaseModel):
    """Deploy the branch's metadata to the CONNECTED ORG for real (not a
    dry-run), then merge the pull request. Call after deliver on a passing
    review. This is the step that actually gets code into the org — a PR alone
    never touches it."""


class report_to_ticket(BaseModel):
    """Post your report as a Jira comment and end the run. The only exit."""

    outcome: Literal["delivered", "blocked", "already_implemented"] = Field(
        description=(
            "delivered = the PR is open (and, if ship ran, merged + deployed); "
            "blocked = genuinely could not complete after AT LEAST ONE worker "
            "attempt — never because a ticket detail was unspecified; "
            "already_implemented = the org already satisfies the ticket."
        )
    )
    markdown: str = Field(description="The Jira comment, in your own words.")


_TOOLS = [inspect_context, delegate_worker, validate, review, deliver, ship, report_to_ticket]


class ManagerState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    gate_passed: bool | None
    review_failed: bool
    review_retries: int
    outcome: str
    report_markdown: str
    pr_url: str
    diary: list[str]
    finished: bool


def _sum_usage(messages: list[AnyMessage]) -> "UsageTotals":
    """Aggregate token usage across a LangGraph run's messages.

    Gemini (ChatGoogleGenerativeAI) attaches a usage_metadata dict to each
    AIMessage; Codex worker sessions don't run through this graph, so only the
    manager's own Gemini turns are counted here."""
    from .runs import UsageTotals

    totals = UsageTotals()
    for msg in messages:
        usage_metadata = getattr(msg, "usage_metadata", None)
        if not usage_metadata:
            continue
        totals.input_tokens += usage_metadata.get("input_tokens", 0) or 0
        totals.output_tokens += usage_metadata.get("output_tokens", 0) or 0
        totals.total_tokens += usage_metadata.get("total_tokens", 0) or 0
    return totals


@dataclass
class ManagerOutcome:
    outcome: str = "blocked"
    report_markdown: str = ""
    pr_url: str | None = None
    reported: bool = False
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)
    merged: bool = False
    deployed: bool = False
    usage: "UsageTotals | None" = None

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered" and bool(self.pr_url)


def _auto_ship_enabled() -> bool:
    """ship() merges the PR and deploys to the real org. On by default — set
    PIPELINE_AGENT_AUTO_SHIP=0 for a PR-only pipeline."""
    return os.getenv("PIPELINE_AGENT_AUTO_SHIP", "1").strip().lower() not in {"0", "false", "no", "off"}


def manager_enabled(settings) -> bool:
    """Salesforce default. PIPELINE_AGENT_MANAGER=0 forces the old path;
    =1 forces this path even for non-Salesforce projects."""
    flag = os.getenv("PIPELINE_AGENT_MANAGER", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    if getattr(settings, "sandbox_kind", "") != "salesforce":
        return False
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _skill_lines(project_id: str) -> list[str]:
    lines: list[str] = []
    try:
        installs = list_project_installs(project_id, strict=False, phase="implementation")
    except Exception:  # noqa: BLE001
        return lines
    for install in installs:
        skill = get_skill(install.slug)
        desc = (getattr(skill, "description", None) or install.name or "").strip()
        lines.append(f"- {install.slug}: {desc}" if desc else f"- {install.slug}")
    return lines


def _tooling_block(settings) -> str:
    """Tell the worker exactly what is already on PATH in this sandbox so it
    stops re-discovering the environment (and stops opening skills that were
    never installed, e.g. the old `salesforce-org-harness` reference)."""
    is_sf = getattr(settings, "sandbox_kind", "") == "salesforce"
    lines = [
        "TOOLS ALREADY SET UP IN THIS SANDBOX — do not reinstall or reconfigure them:",
        "- `git`: you are on the ticket branch and `git push -u origin HEAD` works. Never open a PR.",
        "- The installed skills are ALREADY in your instructions (AGENTS.md, \"Loaded skills\"). "
        "Follow them — do NOT `cat`, `find`, or `ls` anything under `~/.codex/skills/`.",
        "- `rg`, `jq`, `python3`, `node`, `npm`: for search, parsing, and quick scripts.",
        f"- `{_ORG_CATALOG_SANDBOX_PATH}`: verified org + repo facts from bootstrap and earlier "
        "sessions. READ IT FIRST. Only `describe` an object it does not already cover. Never "
        "invent an API name.",
        f"- `{_PIPELINE_DIR}/` is git-ignored. For any repetitive check or bulk edit, writing a "
        f"short throwaway script under `{_PIPELINE_DIR}/scripts/` (Python or bash) is encouraged "
        "— one script is faster than twenty manual tool calls.",
    ]
    if is_sf:
        lines[3:3] = [
            "- `sf`: the target org is already authenticated (`sf org display` to confirm). Use "
            "`sf sobject describe`, `sf project retrieve start`, "
            "`sf project deploy start --source-dir force-app --dry-run`.",
            "- `sf code-analyzer run`: the plugin AND a Java runtime are installed — PMD/CPD "
            "work. Do not report Java or the plugin as missing.",
            "- The repo's npm toolchain is installed (`npm ci` ran at startup): `npm run lint`, "
            "`npm run prettier`, `npm run test:unit` (sfdx-lwc-jest) all work. Do NOT `npm "
            "install`, `npx jest`, or report these as 'not installed'.",
        ]
    lines += [
        "",
        "WORK FAST — this whole session has a hard time budget:",
        "- The approved plan in TICKET.md is a CONTRACT. Public Apex method names/signatures and "
        "every acceptance criterion are MANDATORY — implement them exactly as written. Deviating "
        "(e.g. a different method name) fails review even if the code compiles.",
        "- Do NOT run `sf project deploy start --dry-run` yourself — the pipeline's validation "
        "gate is the single owner of that check. Your job: write the metadata, run the ONE "
        "focused Apex test class for what you changed (once), run `sf code-analyzer` at most "
        "once, fix what they report, commit and push. Do not re-run the full Apex suite.",
        "- Format/lint ONLY the files you created or edited (`prettier --write <those files>`). "
        "Do NOT run repo-wide `npm run prettier:verify` / `npm run lint` — pre-existing files may "
        "be unformatted and fixing them is not this ticket.",
        "- Do not re-read skills or re-`describe` the org in a follow-up session — the diary and "
        f"`{_ORG_CATALOG_SANDBOX_PATH}` already have it.",
    ]
    return "\n".join(lines) + "\n"


def _skill_prefix(project_id: str, ticket_key: str, settings) -> str:
    skills = _skill_lines(project_id)
    listed = "\n".join(skills) if skills else "- (none attached — still open any skills on disk that match)"
    tooling = _tooling_block(settings)
    default_branch = getattr(settings, "default_branch", "main")
    branch = f"feature/{ticket_key}"
    return (
        f"Ticket {ticket_key}. You are the implementing engineer, not the manager.\n"
        "Open any installed skill whose description matches this work:\n"
        f"{listed}\n"
        f"{tooling}"
        f"The pipeline has already created and checked out `{branch}` from "
        f"origin/{default_branch}. Stay on it. Do NOT run `git switch`, "
        "`git checkout <branch>`, `git switch -C`, `git stash`, or "
        "`git reset --hard` — any edits already in the working tree are a "
        "previous attempt on THIS ticket, they are yours to finish, not to "
        "discard. Build on them.\n"
        "COMMIT DISCIPLINE — this matters more than a clean session:\n"
        "1. If the working tree ALREADY has changes when you start, save them "
        f"first: `git add -A && git commit -m \"{ticket_key}: wip\" && "
        "git push -u origin HEAD`. Then keep working.\n"
        "2. The moment the metadata/code is written — BEFORE any verification "
        "step (tests, retrieve, describe, analyzer) — commit and push again. "
        "Those steps can use up the whole session; an uncommitted branch is "
        "invisible to validation and lost when the session ends.\n"
        f"3. Commit format: `git add -A && git commit -m \"{ticket_key}: "
        "<what changed>\" && git push -u origin HEAD`. Push EVEN IF deploy / "
        "tests / PMD still have problems — a pushed branch with issues beats "
        "a perfect uncommitted one. Do NOT open a pull request; delivery "
        "does that.\n"
        "If the org already fully satisfies the ticket, change nothing and write a "
        "line starting with ALREADY-IMPLEMENTED: plus the API names that prove it.\n"
        "NEVER delete or `git rm` an existing file under `force-app/` — only add "
        "or edit. If a dry-run deploy fails on a component you did not touch, that "
        "is not yours to fix by removing it; adjust only your own metadata. Also "
        "never stage `node_modules`, `package-lock.json`, TICKET.md or `.pipeline/`.\n"
        "DECIDE, DO NOT STALL: if the ticket leaves a detail unspecified (a default "
        "value, a permission, a layout slot), pick the conventional safe choice, "
        "add a one-line `Assumption:` note in the commit body, and implement it. "
        "Never stop to ask for clarification — a later ticket can change it.\n"
        "This branch will be DEPLOYED TO THE REAL ORG after review, so your "
        "metadata must actually deploy (correct element order, valid references, "
        "API version present).\n"
        "---\n"
        "Assignment:\n"
    )


def _diary_block(diary: list[str]) -> str:
    if not diary:
        return ""
    body = "\n\n".join(diary[-_DIARY_CAP:])
    return (
        "---\nMemory of this same execution (you are not starting cold). This "
        "is progress ON THIS BRANCH — build on it. Do NOT `git stash`, "
        "`git reset --hard`, or re-create the branch to 'start clean':\n"
        f"{body}\n---\n"
    )


def _is_nonsource_path(path: str) -> bool:
    """True for pipeline scaffolding that a real implementation never consists
    solely of — TICKET.md and friends (see _NONSOURCE_PREFIXES)."""
    base = path.strip()
    if base.startswith("./"):
        base = base[2:]
    return any(base == p or base.startswith(p) for p in _NONSOURCE_PREFIXES)


def _review_verdict(text: str) -> str:
    """pass | warn | fail, parsed from a review. Unrecognised -> warn, so a
    review we cannot read does not silently block or reopen delegation."""
    lowered = text.lower()
    match = re.search(r"verdict\W{0,4}\**\s*(pass|warn|fail)\b", lowered)
    if match:
        return match.group(1)
    if "**fail**" in lowered:
        return "fail"
    return "warn"


class _Session:
    def __init__(self, settings, sandbox, ticket_key: str, plan_text: str, project_id: str, on_event, pause_control):
        self.settings = settings
        self.sandbox = sandbox
        self.ticket_key = ticket_key
        self.plan_text = plan_text
        self.project_id = project_id
        self.on_event = on_event
        self.pause_control = pause_control
        self.calls: list[str] = []
        # WS-5.3: per-ticket-type budgets. classify_ticket from plan_text here;
        # run_managed_pipeline refines it once TICKET.md is readable.
        self.ticket_type = classify_ticket("", plan_text)
        self.max_turns, self.light_review = budget_for(self.ticket_type)
        self.gate_passed: bool | None = None
        self.last_validation = ""
        self.last_review = ""
        self.review_failed = False
        self.review_retries = 0
        self.validate_failures = 0
        self._last_engine_result: dict[str, Any] = {}
        self.pr_url: str | None = None
        self.merged = False
        self.deployed = False
        self.last_ship = ""
        self.finished = False
        self.outcome = "blocked"
        self.report_markdown = ""
        self.diary: list[str] = []
        # Point 5: a word-for-word repeat of the last brief is refused.
        self._last_delegate_brief = ""
        # Point 6: ORG_CATALOG.md is written into the sandbox once per run.
        self._pipeline_context_ready = False

    def emit(self, kind: str, content: str) -> None:
        if self.on_event:
            self.on_event(kind, content)

    def _run_engine(
        self, instruction: str, model: str | None = None, reasoning_effort: str | None = None
    ) -> str:
        on_stdout = (lambda chunk: self.emit("codex_stdout", chunk)) if self.on_event else None
        on_stderr = (lambda chunk: self.emit("codex_stderr", chunk)) if self.on_event else None
        if self.settings.engine == "opencode":
            result = self.sandbox.run_opencode(
                instruction, on_stdout=on_stdout, on_stderr=on_stderr, model=model
            )
        else:
            result = self.sandbox.run_codex(
                instruction,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        self._last_engine_result = result if isinstance(result, dict) else {}
        parts = [f"exit_code: {result.get('exit_code')}"]
        if result.get("stdout"):
            parts.append(_clean(result["stdout"])[:12000])
        if result.get("stderr"):
            parts.append(_clean(result["stderr"])[:4000])
        return "\n".join(parts)

    def _session_was_cut_off(self) -> bool:
        """True when the last engine call did not finish cleanly - a wall-clock
        timeout, a non-zero exit, or it stopped mid-turn without printing a
        closing summary. Such a session usually never reached `git commit`
        (AG-155: files written by `apply patch`, then the turn budget ran out
        during the dry-run deploy)."""
        result = self._last_engine_result
        if result.get("timed_out"):
            return True
        if result.get("exit_code") not in (0, None):
            return True
        return not (result.get("stdout") or "").strip()

    def _autocommit_pending(self, label: str) -> str | None:
        """Commit and push whatever the worker left in the working tree when
        nothing is yet ahead of the default branch, so a killed or short
        session's output survives the sandbox teardown (AG-155). Returns a
        one-line note for the diary, or None when there was nothing to save."""
        if not self._dirty_tree():
            return None
        ahead = self._commits_ahead()
        if ahead and ahead > 0:
            return None
        head = self.sandbox.current_branch()
        if not head or head == self.settings.default_branch:
            return None
        self.emit("step", f"Manager -> auto-commit pending work ({label})")
        add = self.sandbox.execute("git add -A")
        if not add.get("success"):
            return f"Auto-commit skipped: `git add` failed ({(add.get('stderr') or '')[:200]})."
        message = f"{self.ticket_key}: {label}"
        commit = self.sandbox.execute(f"git commit -m {shlex.quote(message)}")
        if not commit.get("success"):
            body = (commit.get("stderr") or commit.get("stdout") or "")[:200]
            if "nothing to commit" in body:
                return None
            return f"Auto-commit skipped: `git commit` failed ({body})."
        push = self.sandbox.execute("git push -u origin HEAD")
        if push.get("success"):
            return f"Auto-committed the worker's uncommitted changes as `{message}` and pushed."
        return (
            f"Auto-committed the worker's uncommitted changes as `{message}`, "
            f"but `git push` FAILED ({(push.get('stderr') or push.get('stdout') or '')[:200]})."
        )

    def _note(self, text: str) -> None:
        self.diary.append(_clean(text)[:_DIARY_ITEM_CHARS])

    def _commits_ahead(self) -> int | None:
        """How many commits this branch has that the default branch does not.

        Zero is the PGD-48 failure: a feature branch exists, `sf` dry-run
        passed on the working tree, GitHub still said `No commits between
        main and feature/...`. `sf project deploy --source-dir` reads the
        filesystem, not git, so an uncommitted (or never-committed) change
        looks like a green deploy.
        """
        if not self.sandbox.current_branch():
            return None
        counted = self.sandbox.execute(
            f"git rev-list --count {self.settings.default_branch}..HEAD"
        )
        if not counted.get("success"):
            counted = self.sandbox.execute(
                f"git rev-list --count origin/{self.settings.default_branch}..HEAD"
            )
        if not counted.get("success"):
            return None
        try:
            return int((counted.get("stdout") or "0").strip().splitlines()[-1])
        except ValueError:
            return None

    def _remote_branch_error(self, branch: str) -> str | None:
        """Return an actionable error unless ``branch`` is visible on origin."""
        ref = f"refs/heads/{branch}"
        result = self.sandbox.execute(
            f"git ls-remote --exit-code --heads origin {shlex.quote(ref)}"
        )
        if result.get("success"):
            return None
        detail = (result.get("stderr") or result.get("stdout") or "unknown error").strip()
        return detail[:800]

    def _dirty_tree(self) -> bool:
        status = self.sandbox.execute("git status --porcelain")
        return bool((status.get("stdout") or "").strip())

    def _changed_files(self) -> list[str] | None:
        """Paths this branch changes vs the default branch, or None if git
        could not be asked. Used to catch a branch whose only commit adds
        TICKET.md (see _NONSOURCE_PREFIXES / PGD-48)."""
        names = self.sandbox.execute(
            f"git diff --name-only {self.settings.default_branch}...HEAD"
        )
        if not names.get("success"):
            names = self.sandbox.execute(
                f"git diff --name-only origin/{self.settings.default_branch}...HEAD"
            )
        if not names.get("success"):
            return None
        return [line.strip() for line in (names.get("stdout") or "").splitlines() if line.strip()]

    def _range_base(self) -> str:
        """The ref to diff the feature branch against. `_checkout_ticket_branch`
        does `git switch -C feature/X origin/<default>` and only `git fetch`es
        the remote, so the LOCAL `<default>` ref can be missing or stale — and
        `git diff <default>...HEAD` then silently returns nothing. That is how
        AG-195, fully committed and pushed, was reviewed as an empty diff and
        blocked. Prefer local `<default>` only when it actually resolves, else
        fall back to `origin/<default>`."""
        default = self.settings.default_branch
        check = self.sandbox.execute(f"git rev-parse --verify --quiet {default}")
        if check.get("success") and (check.get("stdout") or "").strip():
            return default
        return f"origin/{default}"

    def _review_model(self) -> str | None:
        if self.settings.engine != "codex":
            return None
        if self.light_review:
            # metadata tickets don't need the stronger review model
            return getattr(self.settings, "codex_planning_model", None) or None
        return getattr(self.settings, "codex_review_model", None) or None

    def _review_effort(self) -> str | None:
        if self.settings.engine != "codex":
            return None
        return getattr(self.settings, "codex_review_reasoning_effort", None) or None

    def _ensure_pipeline_context(self) -> None:
        """Write `.pipeline/ORG_CATALOG.md` into the sandbox once so every worker
        session starts from the verified org catalog instead of re-describing
        the whole org (Point 6). Cheap, deterministic, no LLM."""
        if self._pipeline_context_ready:
            return
        self._pipeline_context_ready = True
        writer = getattr(self.sandbox, "write_file", None)
        if writer is None:
            return
        try:
            self.sandbox.execute(f"mkdir -p {_PIPELINE_DIR}/scripts")
        except Exception:  # noqa: BLE001
            pass
        try:
            memory = fetch_memory_markdown(self.project_id) or ""
        except Exception:  # noqa: BLE001
            memory = ""
        if memory.strip():
            body = memory
        else:
            body = (
                "# Org catalog — NOT AVAILABLE\n\n"
                "Project memory is empty: Bootstrap Salesforce was never run for "
                "this project. Describe every object you touch with `sf sobject "
                "describe` and do not guess API names.\n"
            )
            self.emit(
                "step",
                "Manager: no org catalog in project memory — worker must describe the org itself",
            )
        try:
            writer(_ORG_CATALOG_SANDBOX_PATH, body)
        except Exception:  # noqa: BLE001
            pass

    async def dispatch(self, name: str, args: dict[str, Any], state: ManagerState) -> str:
        # Only block a *polish* session. Two things reopen delegation even
        # after validate passed: nothing is actually ahead of main (PGD-48:
        # uncommitted working tree), or review failed and the one post-review
        # retry is still available (PGD-48 again: validate greenlit a branch
        # whose only commit added TICKET.md, review caught it, the old guard
        # then refused the fix).
        if name == "delegate_worker" and self.gate_passed is True:
            review_reopens = self.review_failed and self.review_retries < _MAX_REVIEW_RETRIES
            if not review_reopens:
                ahead = self._commits_ahead()
                if ahead and ahead > 0 and not self._dirty_tree():
                    used_retry = (
                        " Review already failed here and the one post-review "
                        "retry is spent."
                        if self.review_retries >= _MAX_REVIEW_RETRIES
                        else ""
                    )
                    return (
                        "REFUSED: validate already passed and this branch has "
                        f"{ahead} commit(s) ahead of {self.settings.default_branch}."
                        f"{used_retry} Do not start another Codex session. Call "
                        "review, then deliver, then report_to_ticket."
                    )
        # Hard cap on validate-fail thrash.
        if name == "delegate_worker" and self.validate_failures >= _MAX_VALIDATE_RETRIES:
            return (
                f"REFUSED: validate has failed {self.validate_failures} times and "
                "re-delegation is exhausted. The worker cannot make this deploy. "
                "Call report_to_ticket with outcome blocked, quoting the deploy "
                "error and what was attempted.\n\nLast gate:\n"
                f"{(self.last_validation or '')[:2500]}"
            )
        # Point 5: a re-delegation that repeats the last brief verbatim just
        # burns a session — the manager must say what was wrong and what to do
        # differently. Force it to look at the gate output.
        if name == "delegate_worker":
            brief = str(args.get("brief") or "").strip()
            if brief and brief == self._last_delegate_brief:
                gate = self.last_review or self.last_validation or "(no gate output yet)"
                return (
                    "REFUSED: this assignment is word-for-word identical to the "
                    "previous one, which did not pass. Do not resend it. Rewrite "
                    "the brief so it names exactly what failed and what to change. "
                    f"The last gate said:\n{gate[:2500]}"
                )

        # WS-4.3: validate checks the branch the worker pushes — running it
        # before any delegate_worker just burns a Gemini round-trip on an empty
        # branch (AG-212 opened with a premature validate).
        if name == "validate" and "delegate_worker" not in self.calls:
            return (
                "REFUSED: nothing is implemented yet. Call delegate_worker first; "
                "validate then checks the branch the worker pushed."
            )

        self.calls.append(name)
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return f"REFUSED: unknown tool {name!r}."
        try:
            return await handler(args, state)
        except Exception as exc:  # noqa: BLE001
            self.emit("step", f"Manager tool {name} errored: {exc}")
            return f"ERROR from `{name}`: {str(exc)[:800]}"

    async def _do_inspect_context(self, args: dict[str, Any], _state: ManagerState) -> str:
        what = str(args.get("what") or "").strip()
        if what == "ticket":
            read = self.sandbox.read_file("TICKET.md")
            return (read.get("content") or "")[:8000] if read.get("success") else "TICKET.md is not present."
        if what == "plan":
            return (self.plan_text or "No plan text.")[:8000]
        if what == "org_memory":
            memory = fetch_memory_markdown(self.project_id)
            if not memory:
                return (
                    "No project memory (Bootstrap Salesforce was not run). Do not "
                    "guess API names. Tell the worker to describe the org with `sf` "
                    "and record what it finds."
                )
            return (
                f"(Also written into the sandbox at `{_ORG_CATALOG_SANDBOX_PATH}` "
                "for the worker.)\n\n" + memory[:20000]
            )
        if what == "skills":
            lines = _skill_lines(self.project_id)
            return "Installed skills:\n" + ("\n".join(lines) if lines else "_none attached_")
        if what == "branch":
            return self._branch_state()
        if what == "validation":
            return self.last_validation or "The validation gate has not run yet."
        return f"REFUSED: unknown inspection target {what!r}."

    def _branch_state(self) -> str:
        branch = self.sandbox.current_branch() or "(none)"
        status = self.sandbox.execute("git status --short --branch")
        log = self.sandbox.execute("git log --oneline -n 8")
        diff = self.sandbox.execute(
            f"git diff --stat {self.settings.default_branch}...HEAD"
        )
        return (
            f"Current branch: {branch}\nDefault: {self.settings.default_branch}\n\n"
            f"status:\n{status.get('stdout') or status.get('stderr')}\n\n"
            f"log:\n{log.get('stdout') or ''}\n\n"
            f"diff vs {self.settings.default_branch}:\n{diff.get('stdout') or diff.get('stderr') or ''}"
        )[:8000]

    async def _do_delegate_worker(self, args: dict[str, Any], state: ManagerState) -> str:
        brief = str(args.get("brief") or "").strip()
        if not brief:
            return "REFUSED: the assignment cannot be empty."
        self._last_delegate_brief = brief
        await _to_thread(self._ensure_pipeline_context)
        prefix = _skill_prefix(self.project_id, self.ticket_key, self.settings)
        diary = _diary_block(state.get("diary") or self.diary)
        review_block = ""
        if self.review_failed and self.last_review:
            review_block = (
                "---\nThe previous review FAILED. You are being sent back to "
                "finish the work it flagged as missing. Address every point "
                "below and commit the source changes:\n"
                f"{self.last_review[:_DIARY_ITEM_CHARS]}\n---\n"
            )
            self.review_retries += 1
            self.review_failed = False
            # Force a fresh validate + review after this session.
            self.gate_passed = None
        instruction = prefix + diary + review_block + brief
        self.emit("step", f"Manager -> Codex worker ({len(brief)} chars)")
        output = await _to_thread(self._run_engine, instruction)
        cut_off = self._session_was_cut_off()
        saved = await _to_thread(self._autocommit_pending, "worker session snapshot")

        status = "Worker session ENDED EARLY (timeout / turn budget)" if cut_off else "Worker finished"
        note_lines = [f"{status}:", output[:_DIARY_ITEM_CHARS]]
        if saved:
            note_lines.append(saved)
        self._note("\n".join(note_lines))

        branch = self.sandbox.current_branch() or "(unknown)"
        prefix_msg = (
            f"Codex session on `{branch}` ended early (timeout or turn budget) before it "
            "could wrap up."
            if cut_off
            else f"Codex finished on branch `{branch}`."
        )
        tail = f"\n\n[manager] {saved} Continue from HEAD - do not have the next session re-inspect the org from scratch." if saved else ""
        return f"{prefix_msg}{tail}\n\n{output[:8000]}"

    async def _do_validate(self, _args: dict[str, Any], _state: ManagerState) -> str:
        self.emit("step", "Manager -> validate")
        report = await _to_thread(self._run_validate)
        self.last_validation = report
        passed = report.startswith("PASSED")
        self.gate_passed = passed
        if not passed:
            self.validate_failures += 1
        self._note(f"Validate: {'PASSED' if passed else 'FAILED'}\n{report[:1200]}")
        if not passed and self.validate_failures >= _MAX_VALIDATE_RETRIES:
            return (
                report[:5000]
                + f"\n\n[manager] validate has now FAILED {self.validate_failures} times. "
                "Do NOT re-delegate again — the worker cannot get this to deploy. "
                "Call report_to_ticket with outcome blocked, quoting the deploy "
                "error above and what was tried."
            )
        return report[:6000]

    def _run_validate(self) -> str:
        bits = [self._branch_state()]
        head = self.sandbox.current_branch()
        if not head or head == self.settings.default_branch:
            self.gate_passed = False
            return (
                "FAILED: still on the default branch, so there is nothing to "
                "deliver. The worker must push a feature branch.\n\n" + bits[0]
            )
        ahead = self._commits_ahead()
        dirty = self._dirty_tree()
        if ahead is None:
            return (
                f"FAILED: could not count commits on `{head}` vs "
                f"`{self.settings.default_branch}`. Without that count, a "
                "dry-run deploy is not enough — GitHub still needs unique "
                "commits to open a PR.\n\n" + bits[0]
            )
        if ahead == 0:
            why = (
                f"FAILED: branch `{head}` has no commits ahead of "
                f"`{self.settings.default_branch}`, so GitHub cannot open a "
                "pull request (`No commits between ...`). "
            )
            if dirty:
                why += (
                    "There are uncommitted files in the working tree — that is "
                    "the implementation in progress, NOT junk to discard. `sf` "
                    "dry-run reads the filesystem, not git, so a green deploy "
                    "does not mean anything was committed. Re-delegate: tell "
                    "Codex to `git add -A`, commit, and push what is already "
                    "there (do not stash or reset it), then validate again."
                )
            else:
                why += (
                    "Nothing was committed. Re-delegate: either implement the "
                    "ticket and commit, or report already_implemented."
                )
            return why + "\n\n" + bits[0]
        remote_error = self._remote_branch_error(head)
        if remote_error:
            return (
                f"FAILED: branch `{head}` has local commits but is not confirmed on origin. "
                "The worker must run `git push -u origin HEAD`; do not review or open a "
                f"pull request until it succeeds. GitHub returned: {remote_error}\n\n" + bits[0]
            )
        changed = self._changed_files()
        if changed is not None and changed and all(_is_nonsource_path(p) for p in changed):
            return (
                f"FAILED: every file `{head}` changes vs "
                f"`{self.settings.default_branch}` is pipeline scaffolding "
                f"({', '.join(changed[:10])}) — TICKET.md / plan / attachments, "
                "no implementation. A dry-run deploy passes on this because it "
                "reads the filesystem, not the diff. Re-delegate: implement the "
                "ticket and commit the source changes.\n\n" + bits[0]
            )
        if self.settings.sandbox_kind == "salesforce":
            deploy = self.sandbox.execute(
                "sf project deploy start --source-dir force-app --dry-run "
                "--test-level NoTestRun --wait 10 --ignore-conflicts"
            )
            ok = bool(deploy.get("success"))
            bits.append(
                "Salesforce dry-run deploy:\n"
                + _clean(deploy.get("stdout") or deploy.get("stderr") or "", keep_last_lines=40)[:4000]
            )
            if not ok:
                return "FAILED: dry-run deploy did not succeed.\n\n" + "\n\n".join(bits)
        return "PASSED\n\n" + "\n\n".join(bits)

    async def _do_review(self, _args: dict[str, Any], _state: ManagerState) -> str:
        head = self.sandbox.current_branch()
        if not head or head == self.settings.default_branch:
            return "REFUSED: no feature branch is available to review."
        remote_error = self._remote_branch_error(head)
        if remote_error:
            return (
                f"REFUSED: branch `{head}` is not confirmed on origin, so it cannot be "
                "described as committed and pushed. Push it successfully, then validate "
                f"again. GitHub returned: {remote_error}"
            )
        # Point 1: use the SAME base ref logic as validate. A bare
        # `git diff <default>...HEAD` against a missing local ref returns
        # nothing, and an empty patch always reads as "nothing delivered".
        base = self._range_base()
        ex = " ".join(shlex.quote(p) for p in _DIFF_EXCLUDE_PATHSPEC)
        patch = self.sandbox.execute(f"git diff {base}...HEAD -- . {ex}").get("stdout") or ""
        stat = self.sandbox.execute(f"git diff --stat {base}...HEAD -- . {ex}").get("stdout") or ""
        log = self.sandbox.execute(f"git log --oneline {base}..HEAD").get("stdout") or ""
        names = self.sandbox.execute(f"git diff --name-only {base}...HEAD -- . {ex}").get("stdout") or ""
        changed = [n.strip() for n in names.splitlines() if n.strip()]
        ahead = self._commits_ahead()

        if not patch.strip():
            # Distinguish "git range is broken" (AG-195: fully committed branch,
            # empty diff) from "the branch only touched scaffolding" (real fail).
            raw = self.sandbox.execute(f"git diff {base}...HEAD").get("stdout") or ""
            if raw.strip():
                self.last_review = (
                    "Verdict: fail\nThe branch's only changes are pipeline "
                    "scaffolding / lockfiles — no implementation was delivered."
                )
                self.review_failed = True
                self._note("Review: FAIL (scaffolding only)")
                return self.last_review
            # Point 2: never hand the reviewer an empty patch. If the branch
            # genuinely has commits, this is a git-range problem, not a missing
            # implementation — say so and do NOT reopen delegation.
            if ahead and ahead > 0:
                self._note("Review skipped: branch has commits but the diff came back empty.")
                return (
                    f"ERROR: `git diff {base}...HEAD` is empty even though "
                    f"`{self.sandbox.current_branch()}` is {ahead} commit(s) "
                    f"ahead of {self.settings.default_branch}. This is a git range "
                    "problem, not a missing implementation. Do NOT re-delegate. "
                    "Call validate again; if it passes, deliver."
                )
            self.last_review = (
                "Verdict: fail\nThe branch has no diff against the base branch — "
                "nothing was implemented."
            )
            self.review_failed = True
            self._note("Review: FAIL (empty branch)")
            return self.last_review

        instruction = (
            "You are a senior code reviewer. The feature branch is ALREADY "
            "committed and pushed: the working tree is clean, so `git diff` with "
            "no arguments shows nothing — that does NOT mean no work was done. "
            "Review the committed branch diff below against the ticket in "
            "TICKET.md. Do not run your own git commands, do not change files, do "
            "not commit or push.\n\n"
            "Write: `Verdict: pass|warn|fail`, then what was delivered and what "
            "is missing or wrong.\n\n"
            f"Commits on the branch:\n{log.strip() or '(none listed)'}\n\n"
            f"Files changed:\n{chr(10).join(changed) or '(none)'}\n\n"
            f"Diff stat:\n{stat.strip()}\n\n"
            f"```diff\n{patch[:14000]}\n```"
        )
        self.emit("step", "Manager -> review")
        report = (
            await _to_thread(self._run_engine, instruction, self._review_model(), self._review_effort())
        )[:6000]
        self.last_review = report
        verdict = _review_verdict(report)
        self.review_failed = verdict == "fail"
        self._note(f"Review: {verdict.upper()}\n{report[:1200]}")
        if self.review_failed:
            if self.review_retries < _MAX_REVIEW_RETRIES:
                report += (
                    "\n\n[manager] Review FAILED. Call delegate_worker ONCE more "
                    "to implement exactly what is listed above, then validate "
                    "and review again. Do not deliver."
                )
            else:
                report += (
                    "\n\n[manager] Review FAILED again and the one post-review "
                    "retry is spent. Call report_to_ticket with outcome blocked "
                    "— do not deliver."
                )
        return report

    def _assumptions(self) -> list[str]:
        """`Assumption: ...` lines the worker recorded in commit bodies / diary
        for anything the ticket left unspecified (see the MAKE THE CALL rule)."""
        seen: list[str] = []
        for entry in self.diary:
            for line in entry.splitlines():
                s = line.strip().lstrip("-* ").strip()
                if s.lower().startswith("assumption:"):
                    val = s.split(":", 1)[1].strip()
                    if val and val not in seen:
                        seen.append(val)
        return seen

    def _pr_body(self) -> str:
        """A readable PR description — not the raw validate dump. Sections:
        summary, commits, files changed, what was verified, assumptions."""
        base = self._range_base()
        ex = " ".join(shlex.quote(p) for p in _DIFF_EXCLUDE_PATHSPEC)
        files = self.sandbox.execute(
            f"git diff --name-status {base}...HEAD -- . {ex}"
        ).get("stdout") or ""
        commits = self.sandbox.execute(
            f"git log --format=%s {base}..HEAD"
        ).get("stdout") or ""
        verified = "dry-run deploy: passed" if self.gate_passed else "dry-run deploy: not confirmed"
        if self.last_review:
            verified += f" · review: {_review_verdict(self.last_review)}"
        parts = [
            f"## {self.ticket_key}",
            "",
            "Automated implementation. See the Jira ticket for the requirement.",
            "",
            "### Commits",
            "\n".join(f"- {c}" for c in commits.splitlines() if c.strip()) or "- (none)",
            "",
            "### Files changed",
            "```",
            files.strip() or "(none)",
            "```",
            "",
            "### Verified",
            verified,
        ]
        assumptions = self._assumptions()
        if assumptions:
            parts += ["", "### Assumptions made for unspecified details"]
            parts += [f"- {a}" for a in assumptions]
        return "\n".join(parts)

    async def _do_deliver(self, _args: dict[str, Any], _state: ManagerState) -> str:
        if self.review_failed:
            return (
                "REFUSED: the last review FAILED. Re-delegate to implement what "
                "it flagged as missing, or report_to_ticket with outcome "
                "blocked. No pull request is opened over a failing review."
            )
        if self.gate_passed is False:
            return (
                "REFUSED: the last validate failed, so no pull request will be "
                "opened. Re-delegate with the gate text, or report_to_ticket."
            )
        if self.gate_passed is None:
            return "REFUSED: call validate before deliver."
        head = self.sandbox.current_branch()
        if not head or head == self.settings.default_branch:
            return "REFUSED: no feature branch to open a pull request from."
        ahead = self._commits_ahead()
        if ahead == 0:
            return (
                "REFUSED: no unique commits on this branch, GitHub will reject "
                "the PR. Re-delegate so Codex commits and pushes, then validate "
                "again."
            )
        remote_error = self._remote_branch_error(head)
        if remote_error:
            return (
                f"REFUSED: branch `{head}` is not present on origin. Push it successfully "
                "before opening a pull request. "
                f"GitHub returned: {remote_error}"
            )
        self.emit("step", f"Manager -> deliver ({head})")
        title = f"{self.ticket_key}: {head}"
        body = await _to_thread(self._pr_body)
        pr = await open_pull_request_aware(
            self.settings,
            self.sandbox,
            base=self.settings.default_branch,
            head=head,
            title=title,
            body=body,
        )
        if not pr.get("success"):
            return f"Delivery refused: {(pr.get('stderr') or pr.get('error') or '')[:1500]}"
        self.pr_url = pr.get("url")
        return f"Pull request opened: {self.pr_url}. Now call ship to deploy it to the org and merge."

    async def _do_ship(self, _args: dict[str, Any], _state: ManagerState) -> str:
        """Real deploy to the connected org, then merge the PR. A PR alone
        never reaches the org — this is the step that actually ships."""
        if not _auto_ship_enabled():
            self.last_ship = "auto-ship disabled (PIPELINE_AGENT_AUTO_SHIP=0)"
            return "Auto-ship is disabled. Call report_to_ticket with outcome delivered."
        if not self.pr_url:
            return "REFUSED: call deliver first (no pull request to ship)."
        if self.review_failed or self.gate_passed is not True:
            return "REFUSED: ship only after validate is green and review passed."
        head = self.sandbox.current_branch() or ""
        default = self.settings.default_branch
        notes: list[str] = []

        if self.settings.sandbox_kind == "salesforce":
            self.emit("step", "Manager -> ship: deploying to the connected org")
            deploy = await _to_thread(
                lambda: self.sandbox.execute(
                    "sf project deploy start --source-dir force-app --wait 33 "
                    "--ignore-conflicts",
                    timeout_seconds=int(os.getenv("PIPELINE_AGENT_DEPLOY_TIMEOUT", "2400")),
                )
            )
            self.deployed = bool(deploy.get("success"))
            body = _clean(deploy.get("stdout") or deploy.get("stderr") or "", keep_last_lines=50)[-3000:]
            notes.append(
                f"Org deploy: {'SUCCEEDED' if self.deployed else 'FAILED'}\n{body}"
            )
            if not self.deployed:
                self._note("Ship: org deploy FAILED\n" + body[:1500])
                self.last_ship = "\n\n".join(notes)
                # Let the manager re-delegate to fix real deploy errors even
                # though validate/review had passed.
                self.gate_passed = None
                self.last_validation = "Real org deploy FAILED:\n" + body[:2000]
                return (
                    "Org deploy FAILED — these are REAL org-rejection errors "
                    "(not a dry-run). Re-delegate ONCE to fix exactly what the "
                    "deploy reported, then validate / review / deliver / ship "
                    "again. If it cannot be fixed, report_to_ticket blocked.\n\n"
                    + "\n\n".join(notes)
                )

        if self.settings.git_provider == "azure":
            self.last_ship = (
                "Auto-merge via Azure DevOps REST is not supported; the code is "
                "pushed and the PR is open - merge it in the Azure DevOps UI."
            )
            notes.append(self.last_ship)
            self._note("Ship:\n" + self.last_ship[:1500])
            return self.last_ship

        self.emit("step", f"Manager -> ship: merging {self.pr_url}")
        merge = await _to_thread(
            lambda: self.sandbox.execute(
                f"gh pr merge {shlex.quote(head)} --squash "
                f"|| gh pr merge {shlex.quote(head)} --merge",
                timeout_seconds=180,
            )
        )
        self.merged = bool(merge.get("success"))
        if self.merged:
            await _to_thread(
                lambda: self.sandbox.execute(
                    f"git fetch origin {shlex.quote(default)} "
                    f"&& git switch -C {shlex.quote(default)} origin/{shlex.quote(default)}",
                    timeout_seconds=60,
                )
            )
            notes.append(f"PR merged into `{default}`.")
        else:
            blob = (merge.get("stderr") or merge.get("stdout") or "")[:600]
            notes.append(
                f"PR NOT merged (branch protection or conflict): {blob}\n"
                "The org already has the code; merge the PR by hand."
            )
        self.last_ship = "\n\n".join(notes)
        self._note("Ship:\n" + self.last_ship[:1500])
        return self.last_ship + "\n\nNow call report_to_ticket with outcome delivered."

    async def _do_report_to_ticket(self, args: dict[str, Any], _state: ManagerState) -> str:
        markdown = str(args.get("markdown") or "").strip()
        if not markdown:
            return "REFUSED: the report cannot be empty."
        outcome = str(args.get("outcome") or "blocked")
        if outcome == "blocked" and "delegate_worker" not in self.calls:
            return (
                "REFUSED: you cannot report blocked without a single worker "
                "attempt. An unspecified detail is not a blocker — brief the "
                "worker with a sensible default for it (and tell it to note the "
                "assumption) and call delegate_worker."
            )
        if outcome == "delivered" and self.pr_url and not self.last_ship and _auto_ship_enabled():
            return (
                "REFUSED: review passed and the PR is open, but you have not "
                "shipped it. Call ship to deploy to the org and merge, then "
                "report delivered."
            )
        if self.pr_url and self.pr_url not in markdown:
            markdown = markdown.rstrip() + f"\n\nPull request: {self.pr_url}"
        if self.last_ship and "deploy" not in markdown.lower():
            status = "deployed to the org" if self.deployed else "org deploy did not succeed"
            merged = " and merged" if self.merged else ""
            markdown = markdown.rstrip() + f"\n\n_Ship: {status}{merged}._"
        if self.settings.jira is not None:
            connection = get_jira_connection(self.settings.jira.connection_name)
            if connection is None:
                return "REFUSED: no Jira connection; cannot post the comment."
            await add_comment(connection, self.ticket_key, markdown)
        elif self.settings.azure is not None:
            connection = get_azure_connection(self.settings.azure.connection_name)
            if connection is None:
                return "REFUSED: no Azure connection; cannot post the comment."
            try:
                await add_azure_comment(connection, int(self.ticket_key), markdown)
            except ValueError:
                return "REFUSED: Azure work items need a numeric id; cannot post the comment."
        # Manual/Jira Group tickets have no remote issue to update. The report
        # is still the final customer comment and is persisted in the run's
        # execution summary for copy/paste.
        self.finished = True
        self.outcome = outcome
        self.report_markdown = markdown
        self.emit("step", f"Posted report on {self.ticket_key} ({outcome})")
        return "Report posted. The run is finished."


async def _to_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


def _checkpointer():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        from database.session import BASE_DIR

        path = str(BASE_DIR / "langgraph.db")
        import sqlite3

        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver
    except Exception:  # noqa: BLE001 - sqlite extra is optional
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()


def _build_model(settings):
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.google_model,
        thinking_budget=settings.thinking_budget,
        max_output_tokens=settings.max_output_tokens,
    ).bind_tools(_TOOLS)


def _compile(session: _Session, model, checkpointer=None):
    async def agent_node(state: ManagerState):
        if session.pause_control is not None:
            await session.pause_control.checkpoint()
        response = await model.ainvoke(state["messages"])
        return {"messages": [response], "steps": state.get("steps", 0) + 1}

    async def tools_node(state: ManagerState):
        if session.pause_control is not None:
            await session.pause_control.checkpoint()
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        messages: list[AnyMessage] = []
        for call in tool_calls:
            name = call.get("name") or ""
            args = call.get("args") or {}
            session.emit("step", f"Manager -> {name}")
            observation = await session.dispatch(name, args, state)
            messages.append(ToolMessage(content=observation, tool_call_id=call.get("id") or name))
        updates: dict[str, Any] = {
            "messages": messages,
            "gate_passed": session.gate_passed,
            "review_failed": session.review_failed,
            "review_retries": session.review_retries,
            "diary": list(session.diary),
            "pr_url": session.pr_url or "",
            "finished": session.finished,
            "outcome": session.outcome,
            "report_markdown": session.report_markdown,
        }
        return updates

    async def nudge_node(state: ManagerState):
        return {
            "messages": [
                HumanMessage(
                    content=(
                        "You must act through the tools. Call report_to_ticket if you "
                        "are done, otherwise call the tool you need next."
                    )
                )
            ]
        }

    def after_agent(state: ManagerState) -> str:
        if state.get("steps", 0) >= session.max_turns:
            return END
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "nudge"

    def after_tools(state: ManagerState) -> str:
        if state.get("finished"):
            return END
        if state.get("steps", 0) >= session.max_turns:
            return END
        return "agent"

    builder = StateGraph(ManagerState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("nudge", nudge_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, {"tools": "tools", "nudge": "nudge", END: END})
    builder.add_edge("nudge", "agent")
    builder.add_conditional_edges("tools", after_tools, {"agent": "agent", END: END})
    return builder.compile(checkpointer=checkpointer)


def _opening(session: "_Session") -> str:
    """Front-load the ticket, the approved plan, the org catalog and the skill
    list so the manager can write the first brief WITHOUT spending
    inspect_context round-trips on static data (AG-212 opened with
    inspect_context x4 + a premature validate)."""
    settings = session.settings
    ticket = ""
    try:
        r = session.sandbox.read_file("TICKET.md")
        if r.get("success"):
            ticket = (r.get("content") or "")[:6000]
    except Exception:  # noqa: BLE001
        pass
    catalog = ""
    try:
        catalog = (fetch_memory_markdown(session.project_id) or "")[:6000]
    except Exception:  # noqa: BLE001
        pass
    skills = _skill_lines(session.project_id)
    return "\n".join(
        [
            f"Ticket: {session.ticket_key}",
            f"Project: {settings.project_name}",
            f"Default branch: {settings.default_branch}",
            f"Platform: {'Salesforce' if settings.sandbox_kind == 'salesforce' else 'generic'}",
            "",
            "Everything you need to write the first brief is below. Do NOT spend "
            "tool calls re-fetching the ticket, plan, catalog or skills, and do "
            "NOT call validate before delegate_worker. Inspect_context is only "
            "for `branch` and `validation` once work has started.",
            "",
            "=== TICKET.md ===",
            ticket or "(TICKET.md not readable yet)",
            "",
            "=== APPROVED PLAN ===",
            (session.plan_text or "(no plan text)")[:6000],
            "",
            "=== ORG CATALOG (verified facts — do not re-describe what is here) ===",
            catalog or "(no project memory — the worker must describe the org itself)",
            "",
            "=== INSTALLED SKILLS ===",
            "\n".join(skills) if skills else "(none attached)",
        ]
    )


async def run_managed_pipeline(
    settings,
    sandbox,
    ticket_key: str,
    plan_text: str,
    *,
    project_id: str,
    run_id: str,
    on_event=None,
    pause_control=None,
    model=None,
    checkpointer=None,
) -> ManagerOutcome:
    session = _Session(settings, sandbox, ticket_key, plan_text, project_id, on_event, pause_control)
    # WS-5.3: refine the ticket type now that TICKET.md is on disk.
    try:
        _tkt = sandbox.read_file("TICKET.md")
        if _tkt.get("success"):
            session.ticket_type = classify_ticket(_tkt.get("content") or "", plan_text)
            session.max_turns, session.light_review = budget_for(session.ticket_type)
    except Exception:  # noqa: BLE001
        pass
    model = model or _build_model(settings)
    checkpointer = checkpointer if checkpointer is not None else _checkpointer()
    graph = _compile(session, model, checkpointer=checkpointer)
    if on_event:
        on_event("step", f"Manager: taking the ticket ({session.ticket_type}, ≤{session.max_turns} turns)")
    initial: ManagerState = {
        "messages": [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_opening(session)),
        ],
        "steps": 0,
        "gate_passed": None,
        "review_failed": False,
        "review_retries": 0,
        "outcome": "blocked",
        "report_markdown": "",
        "pr_url": "",
        "diary": [],
        "finished": False,
    }
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": _RECURSION_LIMIT,
    }
    final = await graph.ainvoke(initial, config=config)
    outcome = ManagerOutcome(
        outcome=session.outcome if session.finished else (final.get("outcome") or "blocked"),
        report_markdown=session.report_markdown
        or _fallback_report(ticket_key, session),
        pr_url=session.pr_url or (final.get("pr_url") or None) or None,
        reported=session.finished,
        steps=int(final.get("steps") or 0),
        tool_calls=session.calls,
        merged=session.merged,
        deployed=session.deployed,
        usage=_sum_usage(final.get("messages") or []),
    )
    if not session.finished:
        # Manager hit the step cap without reporting — still tell Jira.
        try:
            await session._do_report_to_ticket(
                {"outcome": outcome.outcome, "markdown": outcome.report_markdown}, final
            )
            outcome.reported = True
        except Exception:  # noqa: BLE001
            pass
    if on_event:
        on_event("step", f"Manager finished: {outcome.outcome} in {outcome.steps} step(s)")
    return outcome


def _fallback_report(ticket_key: str, session: _Session) -> str:
    """Posted when the manager hits its step cap without calling
    report_to_ticket. Short and clean — no raw validate dump."""
    gate = _clean(session.last_validation or "", keep_last_lines=12)
    tail = f"\n\nLast gate output:\n```\n{gate}\n```" if gate else ""
    n = session.validate_failures
    if session.pr_url:
        return (
            f"{ticket_key}: a pull request is open ({session.pr_url}) but the run "
            f"hit its step limit after {session.calls.count('delegate_worker')} "
            f"worker attempts and {n} failed validation(s) without a clean pass. "
            "Needs a human to finish it." + tail
        )
    return (
        f"{ticket_key}: not completed. The run hit its step limit after "
        f"{session.calls.count('delegate_worker')} worker attempts and {n} failed "
        "validation(s); no pull request was opened." + tail
    )


def worker_instruction_for_tests(project_id: str, ticket_key: str, settings, brief: str, diary: list[str] | None = None) -> str:
    """Pure helper so tests can assert the skill prefix and diary without Docker."""
    return _skill_prefix(project_id, ticket_key, settings) + _diary_block(diary or []) + brief
