"""Configuration for the ticket -> sandbox pipeline agent.

Combines whichever ticket source the project linked - a Jira connection
(which project/tickets to read from - see agents/jira_agent/config.py) or an
Azure DevOps connection (agents/azure_agent/config.py) - with a Project's
repository and git credentials (see credentials.py) and sandbox runtime
knobs. Everything is either a named DB row (Jira/Azure connection, project)
or an environment variable - nothing is hardcoded in code.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from agents.jira_agent.config import Settings as JiraSettings
from agents.jira_agent.config import load_settings as load_jira_settings
from agents.azure_agent.config import Settings as AzureSettings
from agents.azure_agent.config import load_settings as load_azure_settings

from .credentials import get_effective_git_token, get_project
from .sandbox_kinds import resolve_sandbox_image

# ChatGPT Codex credit ladder (cheapest → most expensive):
#   gpt-5.6-luna  →  gpt-5.6-terra  →  gpt-5.6-sol (CLI default if unset)
# Reasoning: minimal | low | medium | high | xhigh  (high/xhigh burns credits fast)
_DEFAULT_CODEX_MODEL = "gpt-5.6-luna"
# _DEFAULT_CODEX_MODEL = "gpt-6-astra"
# WS-5.1: the execution worker restarts 4-5x/ticket on `luna` (each restart
# re-pays discovery). One `terra` session that finishes is faster AND cheaper.
# Planning stays on `luna` (one short call). Override with CODEX_EXECUTION_MODEL.
_DEFAULT_CODEX_EXECUTION_MODEL = "gpt-5.6-terra"
# _DEFAULT_CODEX_EXECUTION_MODEL = "gpt-6-astra"
# The review gate sets the ticket's whole outcome from ONE call. Across the
# AG-191..196 epic not a single review passed clean and two tickets were
# wrongly blocked, so review runs a notch up the ladder by default (one
# call, no subagents). Override with CODEX_REVIEW_MODEL / _REASONING_EFFORT.
_DEFAULT_CODEX_REVIEW_MODEL = "gpt-5.6-terra"
# _DEFAULT_CODEX_REVIEW_MODEL = "gpt-6-astra"
_ALLOWED_REASONING_EFFORT = frozenset({"minimal", "low", "medium", "high", "xhigh", "none"})
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _codex_model_id(value: str, fallback: str) -> str:
    model = (value or fallback).strip() or fallback
    return model if _MODEL_ID_RE.fullmatch(model) else fallback


def _codex_reasoning_effort(value: str, fallback: str) -> str:
    effort = (value or fallback).strip().lower()
    return effort if effort in _ALLOWED_REASONING_EFFORT else fallback


def _codex_model_settings() -> dict:
    """Pin Codex to a cheap model. Unset, the CLI picks gpt-5.6-sol and can
    spawn subagents at high reasoning - that is what drains a Plus/Pro quota
    on a single ticket. CODEX_MODEL / CODEX_REASONING_EFFORT set both phases;
    the per-phase vars override those.
    """
    shared_model = _codex_model_id(os.getenv("CODEX_MODEL", ""), _DEFAULT_CODEX_MODEL)
    shared_effort = os.getenv("CODEX_REASONING_EFFORT", "").strip()
    return {
        "codex_planning_model": _codex_model_id(os.getenv("CODEX_PLANNING_MODEL", ""), shared_model),
        "codex_execution_model": _codex_model_id(
            os.getenv("CODEX_EXECUTION_MODEL", "")
            or os.getenv("CODEX_MODEL", "")
            or _DEFAULT_CODEX_EXECUTION_MODEL,
            _DEFAULT_CODEX_EXECUTION_MODEL,
        ),
        "codex_planning_reasoning_effort": _codex_reasoning_effort(
            os.getenv("CODEX_PLANNING_REASONING_EFFORT", shared_effort),
            "low",
        ),
        "codex_execution_reasoning_effort": _codex_reasoning_effort(
            os.getenv("CODEX_EXECUTION_REASONING_EFFORT", shared_effort),
            "medium",
        ),
        "codex_review_model": _codex_model_id(
            os.getenv("CODEX_REVIEW_MODEL", ""), _DEFAULT_CODEX_REVIEW_MODEL
        ),
        "codex_review_reasoning_effort": _codex_reasoning_effort(
            os.getenv("CODEX_REVIEW_REASONING_EFFORT", shared_effort),
            # `high` on terra added ~1-2 min per review with little verdict gain
            # (run 176ded49). `medium` on the stronger model is the balance.
            "medium",
        ),
        "codex_max_threads": max(1, int(os.getenv("CODEX_MAX_THREADS", "2"))),
    }


_DEFAULT_PERSONA_NAME = "Nova"
_DEFAULT_PERSONA_ROLE = "a senior autonomous software engineer working on this team's codebase"
_DEFAULT_PERSONA_TRAITS = (
    "precise, pragmatic, and methodical - it communicates concisely in commit "
    "messages, pull request descriptions, and status updates, and never "
    "mentions internal tooling, vendor names, or underlying model names"
)


def _persona_settings() -> dict:
    """Identity injected into the coding agent (Codex/opencode) itself, not
    the Gemini orchestrator above it - see prompts.py's agent_label and
    sandbox_runtime.py's AGENTS.md injection. Fully overridable via env vars
    so operators can rebrand per deployment without touching code; set
    PIPELINE_AGENT_PERSONA_INSTRUCTIONS to replace the templated identity
    text wholesale instead of assembling it from name/role/traits.
    """
    return {
        "persona_name": os.getenv("PIPELINE_AGENT_PERSONA_NAME", "").strip() or _DEFAULT_PERSONA_NAME,
        "persona_role": os.getenv("PIPELINE_AGENT_PERSONA_ROLE", "").strip() or _DEFAULT_PERSONA_ROLE,
        "persona_traits": os.getenv("PIPELINE_AGENT_PERSONA_TRAITS", "").strip() or _DEFAULT_PERSONA_TRAITS,
        "persona_instructions": os.getenv("PIPELINE_AGENT_PERSONA_INSTRUCTIONS", "").strip() or None,
    }


_ALLOWED_ENGINES = frozenset({"codex", "opencode"})
# opencode's own Zen gateway - free, no signup or API key required (verified:
# `opencode run -m opencode/deepseek-v4-flash-free` works with zero
# credentials configured). Override with OPENCODE_MODEL for a different Zen
# model or another provider/model opencode has credentials for.
_DEFAULT_OPENCODE_MODEL = "opencode/deepseek-v4-flash-free"


def _engine() -> str:
    engine = os.getenv("PIPELINE_AGENT_ENGINE", "codex").strip().lower()
    return engine if engine in _ALLOWED_ENGINES else "codex"


@dataclass(frozen=True)
class Settings:
    # Ticket source: exactly one of jira / azure is set - whichever connection
    # the project picked. Code gates on whichever is not None.
    jira: JiraSettings | None
    azure: AzureSettings | None
    project_id: str
    project_name: str
    repository_url: str
    default_branch: str
    # Which host owns the git remote: "github" (default) or "azure". The
    # sandbox build derives clone auth + the pull-request flow from this.
    git_provider: str
    # The linked connection backing the git remote (github_connection_name or
    # azure_git_connection_name) - used by the PR helpers to pick credentials.
    git_connection_name: str | None
    git_token: str | None
    sandbox_kind: str
    sandbox_image: str
    sandbox_workdir: str
    salesforce_connection_name: str | None
    codex_api_key: str | None
    # ChatGPT-login Codex defaults to gpt-5.6-sol (flagship) unless we pin a
    # cheaper model. Luna is the lowest-cost GPT-5.6 option; Terra is the
    # mid-tier workhorse; Sol is the expensive default we are avoiding.
    # Override with CODEX_MODEL / CODEX_PLANNING_MODEL / CODEX_EXECUTION_MODEL.
    # API-key auth (no ChatGPT login) may need a different id, e.g. gpt-5.4-mini.
    codex_planning_model: str
    codex_execution_model: str
    codex_planning_reasoning_effort: str
    codex_execution_reasoning_effort: str
    # The review gate runs one Codex call at this (by default stronger) tier -
    # see _DEFAULT_CODEX_REVIEW_MODEL. Used only by manager._do_review.
    codex_review_model: str
    codex_review_reasoning_effort: str
    codex_max_threads: int
    # Which coding agent runs inside the sandbox: "codex" (default, needs
    # OPENAI_API_KEY/ChatGPT login) or "opencode" (free, see
    # _DEFAULT_OPENCODE_MODEL above). Override with PIPELINE_AGENT_ENGINE.
    engine: str
    opencode_model: str
    # Identity given to the coding agent inside the sandbox - see
    # sandbox_runtime.py's AGENTS.md injection and prompts.py's agent_label.
    persona_name: str
    persona_role: str
    persona_traits: str
    persona_instructions: str | None
    bot_git_name: str
    bot_git_email: str
    google_model: str
    thinking_budget: int
    max_output_tokens: int
    log_level: str


def load_settings(ticket_connection_name: str | None, project_name: str) -> Settings:
    project = get_project(project_name)
    if project is None:
        raise RuntimeError(
            f"No project named {project_name!r} found. Create one first, e.g.:\n"
            f"  python pipelineagenttest.py --setup-project --project {project_name} "
            f"--repo-url https://github.com/org/repo.git"
        )
    if not project.repository_url:
        raise RuntimeError(
            f"Project {project_name!r} has no repository URL configured. Set one with:\n"
            f"  python pipelineagenttest.py --setup-project --project {project_name} "
            f"--repo-url https://github.com/org/repo.git"
        )

    # Ticket source is whatever the project picked: Jira (jira_connection_name)
    # or Azure (azure_connection_name). The jira_connection_name positional arg
    # kept for CLI/legacy callers overrides the project's own choice.
    jira_connection_name = ticket_connection_name or project.jira_connection_name
    if jira_connection_name:
        jira_settings = load_jira_settings(jira_connection_name)
        azure_settings: AzureSettings | None = None
        model_default = jira_settings.google_model
        thinking_default = str(jira_settings.thinking_budget)
        max_tokens_default = str(jira_settings.max_output_tokens)
        log_level_default = jira_settings.log_level
    elif project.azure_connection_name:
        jira_settings = None
        azure_settings = load_azure_settings(project.azure_connection_name)
        model_default = os.getenv("PIPELINE_AGENT_MODEL", os.getenv("JIRA_AGENT_MODEL", "gemini-2.5-flash"))
        thinking_default = os.getenv("PIPELINE_AGENT_THINKING_BUDGET", os.getenv("JIRA_AGENT_THINKING_BUDGET", "2048"))
        max_tokens_default = os.getenv("PIPELINE_AGENT_MAX_OUTPUT_TOKENS", os.getenv("JIRA_AGENT_MAX_OUTPUT_TOKENS", "8192"))
        log_level_default = azure_settings.log_level
    elif project.ticket_group_name:
        jira_settings = None
        azure_settings = None
        model_default = os.getenv("PIPELINE_AGENT_MODEL", os.getenv("JIRA_AGENT_MODEL", "gemini-2.5-flash"))
        thinking_default = os.getenv("PIPELINE_AGENT_THINKING_BUDGET", os.getenv("JIRA_AGENT_THINKING_BUDGET", "2048"))
        max_tokens_default = os.getenv("PIPELINE_AGENT_MAX_OUTPUT_TOKENS", os.getenv("JIRA_AGENT_MAX_OUTPUT_TOKENS", "8192"))
        log_level_default = os.getenv("PIPELINE_AGENT_LOG_LEVEL", "INFO")
    else:
        from agents.pipeline_agent import manual_work_items

        has_manual_ticket = bool(manual_work_items.list_work_items(project.id))
        if not has_manual_ticket:
            raise RuntimeError(
                f"Project {project_name!r} has no ticket source linked. "
                "Link a Jira, Azure, or Jira group source, or create a manual ticket."
            )
        jira_settings = None
        azure_settings = None
        model_default = os.getenv("PIPELINE_AGENT_MODEL", os.getenv("JIRA_AGENT_MODEL", "gemini-2.5-flash"))
        thinking_default = os.getenv("PIPELINE_AGENT_THINKING_BUDGET", os.getenv("JIRA_AGENT_THINKING_BUDGET", "2048"))
        max_tokens_default = os.getenv("PIPELINE_AGENT_MAX_OUTPUT_TOKENS", os.getenv("JIRA_AGENT_MAX_OUTPUT_TOKENS", "8192"))
        log_level_default = os.getenv("PIPELINE_AGENT_LOG_LEVEL", "INFO")

    # Determine the git provider. An explicit azure_git_connection_name wins.
    # When none is set, check whether the repository URL belongs to an Azure
    # DevOps host so the pipeline clones/pushes to the right remote.
    _url_lower = (project.repository_url or "").lower()
    _is_azure_url = "dev.azure.com" in _url_lower or ".visualstudio.com" in _url_lower
    if project.azure_git_connection_name:
        git_provider = "azure"
    elif project.github_connection_name:
        git_provider = "github"
    elif _is_azure_url:
        git_provider = "azure"
    else:
        git_provider = "github"

    # The linked connection backing the git remote. For Azure projects that
    # only have a ticket-source connection (azure_connection_name) but no
    # dedicated azure_git_connection_name, use the ticket-source connection
    # name — the same PAT often has Code access too.
    if project.azure_git_connection_name:
        git_connection_name = project.azure_git_connection_name
    elif git_provider == "azure" and project.azure_connection_name:
        git_connection_name = project.azure_connection_name
    else:
        git_connection_name = project.github_connection_name

    return Settings(
        jira=jira_settings,
        azure=azure_settings,
        project_id=project.id,
        project_name=project.name,
        repository_url=project.repository_url,
        default_branch=project.default_branch,
        git_provider=git_provider,
        git_connection_name=git_connection_name,
        git_token=get_effective_git_token(project.id),
        sandbox_kind=project.sandbox_kind,
        sandbox_image=resolve_sandbox_image(project.sandbox_kind),
        sandbox_workdir=os.getenv("PIPELINE_AGENT_SANDBOX_WORKDIR", "/workspace"),
        salesforce_connection_name=project.salesforce_connection_name,
        codex_api_key=os.getenv("OPENAI_API_KEY"),
        **_codex_model_settings(),
        engine=_engine(),
        opencode_model=os.getenv("OPENCODE_MODEL", "").strip() or _DEFAULT_OPENCODE_MODEL,
        **_persona_settings(),
        bot_git_name=os.getenv("PIPELINE_AGENT_BOT_NAME", "pipeline-agent"),
        bot_git_email=os.getenv("PIPELINE_AGENT_BOT_EMAIL", "pipeline-agent@users.noreply.github.com"),
        google_model=os.getenv("PIPELINE_AGENT_MODEL", model_default),
        thinking_budget=int(os.getenv("PIPELINE_AGENT_THINKING_BUDGET", thinking_default)),
        max_output_tokens=int(os.getenv("PIPELINE_AGENT_MAX_OUTPUT_TOKENS", max_tokens_default)),
        log_level=os.getenv("PIPELINE_AGENT_LOG_LEVEL", log_level_default),
    )


def needs_azure_git_resolution(settings: Settings) -> bool:
    """True when the pipeline must resolve the project's Azure DevOps repo URL
    before it can clone/push.

    Covers the case where the project links an Azure DevOps connection (for
    tickets, or as the git source) but the stored repository URL is a GitHub
    URL or empty — an inconsistency that otherwise silently pushes the branch
    to a GitHub remote instead of Azure DevOps Git."""
    if settings.git_provider != "azure":
        return False
    url = (settings.repository_url or "").lower()
    if not url:
        return True
    return not ("dev.azure.com" in url or ".visualstudio.com" in url)


async def resolve_azure_git_for_project(settings: Settings) -> Settings:
    """Resolve the Azure DevOps repository URL for an azure-github project and
    return a replacement Settings that points repository_url at the Azure repo.

    Uses the linked Azure connection (git source or, when missing, the
    ticket-source connection) to list the project's git repositories. This is
    the "why is the pipeline pushing to GitHub?" fix: when the project's git
    source is Azure but the stored repository_url still points at a GitHub
    host, we derive the real Azure clone URL instead of blindly honoring it.

    No-op when the repository URL is already an Azure host, or when the
    project is not an Azure-git project. Raises when no Azure connection can
    back the git remote (readiness should have caught it first).
    """
    if not needs_azure_git_resolution(settings):
        return settings

    from agents.azure_agent.connections import get_connection as get_azure_conn
    from agents.azure_agent.git import resolve_project_clone_url

    connection_name = settings.git_connection_name
    if settings.azure is not None:
        connection_name = connection_name or settings.azure.connection_name
    conn = get_azure_conn(connection_name) if connection_name else None
    if conn is None:
        raise RuntimeError(
            f"Project {settings.project_name!r} uses Azure DevOps Git but no "
            "Azure DevOps connection can back it. Link the Azure connection as "
            "the git source in project settings, or set a valid repository URL."
        )
    clone_url = await resolve_project_clone_url(conn)
    if not clone_url:
        raise RuntimeError(
            f"Could not determine the Azure DevOps repository URL from "
            f"connection {connection_name!r}."
        )

    import dataclasses

    # Azure's remoteUrl may or may not include ".git" — normalize so the
    # credential helper and PR helpers agree on the origin.
    normalized = clone_url.rstrip("/")
    if not normalized.endswith(".git"):
        normalized = f"{normalized}.git"
    return dataclasses.replace(settings, repository_url=normalized)
