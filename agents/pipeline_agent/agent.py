"""Sandbox bring-up for the execution phase.

Planning is not loaded onto the manager. The pipeline writes the ticket into
the sandbox and Codex drafts PLAN.json (see runner.run_codex_planning). After
a human approves that plan this module opens one container: clone, TICKET.md,
skills, org login.

Salesforce + Gemini runs the LangGraph manager (manager.run_managed_pipeline).
Otherwise the original create_deep_agent orchestrator is the fallback.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import partial

from deepagents import create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.jira_agent.mcp_tools import load_atlassian_tools
from tools.file_tools import build_file_tools
from tools.sandbox_tools import build_sandbox_tools

from .config import Settings
from .manager import manager_enabled
from .prompts import build_execution_prompt, build_execution_prompt_azure
from .sandbox_runtime import open_pipeline_sandbox, prepare_sandbox_repo
from .ticket import fetch_ticket_image_attachments, fetch_ticket_markdown


def _build_model(settings: Settings) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.google_model,
        thinking_budget=settings.thinking_budget,
        max_output_tokens=settings.max_output_tokens,
    )


@asynccontextmanager
async def open_execution_workspace(
    settings: Settings,
    ticket_key: str,
    plan_text: str,
    on_event: Callable[[str, str], None] | None = None,
    pause_control=None,
    ticket_markdown: str | None = None,
    image_attachments: list[tuple[str, bytes, str]] | None = None,
):
    """Yield a prepared sandbox (repo cloned, TICKET.md written, skills in).

    The container is torn down on exit. Callers attach Codex or the LangGraph
    manager to this same sandbox for the rest of the run.
    """
    if ticket_markdown is None:
        ticket_markdown = await fetch_ticket_markdown(settings, ticket_key)
        image_attachments = await fetch_ticket_image_attachments(settings, ticket_key)
    ticket_file = (
        f"{ticket_markdown.rstrip()}\n\n## Approved implementation plan\n\n{plan_text.rstrip()}\n"
    )
    sandbox_cm = open_pipeline_sandbox(
        settings,
        on_event=on_event,
        codex_model=settings.codex_execution_model,
        codex_reasoning_effort=settings.codex_execution_reasoning_effort,
        phase="implementation",
    )
    sandbox = await asyncio.to_thread(sandbox_cm.__enter__)
    try:
        if pause_control is not None:
            pause_control.attach_sandbox(sandbox)
        # Only the manager path pre-checks-out feature/<TICKET> and clears
        # stale branches — its worker prefix tells Codex it is already on the
        # ticket branch. The deep-agent fallback still has its orchestrator
        # create the branch (see prompts.build_execution_prompt).
        manage_branch = manager_enabled(settings)
        await asyncio.to_thread(
            partial(
                prepare_sandbox_repo,
                sandbox,
                settings,
                ticket_file,
                on_event,
                image_attachments,
                ticket_key=ticket_key if manage_branch else None,
                # Execution runs `npm run lint` / `prettier` / `test:unit`; get
                # the repo's own toolchain on disk first (see _npm_install_project).
                install_npm=True,
            )
        )
        yield sandbox
    finally:
        if pause_control is not None:
            pause_control.detach_sandbox()
        await asyncio.to_thread(sandbox_cm.__exit__, *sys.exc_info())


@asynccontextmanager
async def build_execution_agent(
    settings: Settings,
    ticket_key: str,
    plan_text: str,
    on_event: Callable[[str, str], None] | None = None,
    pause_control=None,
    ticket_markdown: str | None = None,
    image_attachments: list[tuple[str, bytes, str]] | None = None,
):
    """Fallback path: Gemini deep-agent with sandbox tools.

    Used when the LangGraph manager is off (non-Salesforce, missing Gemini
    key, or PIPELINE_AGENT_MANAGER=0).
    """
    if settings.jira is not None:
        jira_tools = await load_atlassian_tools(settings.jira)
        prompt = build_execution_prompt(
            settings.jira.project_key,
            settings.repository_url,
            settings.default_branch,
            ticket_key,
            plan_text,
            settings.bot_git_name,
            settings.bot_git_email,
            settings.persona_name,
            engine=settings.engine,
        )
    elif settings.azure is not None:
        jira_tools: list = []
        prompt = build_execution_prompt_azure(
            settings.azure.project_name,
            settings.repository_url,
            settings.default_branch,
            ticket_key,
            plan_text,
            settings.bot_git_name,
            settings.bot_git_email,
            settings.persona_name,
            engine=settings.engine,
        )
    else:
        jira_tools = []
        prompt = build_execution_prompt(
            settings.project_name,
            settings.repository_url,
            settings.default_branch,
            ticket_key,
            plan_text,
            settings.bot_git_name,
            settings.bot_git_email,
            settings.persona_name,
            engine=settings.engine,
        )
    async with open_execution_workspace(
        settings,
        ticket_key,
        plan_text,
        on_event=on_event,
        pause_control=pause_control,
        ticket_markdown=ticket_markdown,
        image_attachments=image_attachments,
    ) as sandbox:
        sandbox_tools = build_sandbox_tools(
            sandbox,
            repo_url=settings.repository_url,
            git_token=settings.git_token,
            on_event=on_event,
            include_clone=False,
            engine=settings.engine,
        )
        file_tools = build_file_tools(sandbox)
        agent = create_deep_agent(
            model=_build_model(settings),
            system_prompt=prompt,
            tools=[*jira_tools, *sandbox_tools, *file_tools],
        )
        yield agent, sandbox
