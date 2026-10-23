#!/usr/bin/env python3
"""Standalone script for wiring a project's repo/credentials and running the
ticket -> sandbox -> PR pipeline against it, with an interactive
approval step between Codex planning and Codex execution.

Projects (repo URL + git token + linked Jira connection) are stored in the
project database (see database/models/project.py and
database/models/git_access_token.py); tickets are read through an existing
Jira connection (see agenttest.py for creating those).

Create/update a project (one-time per repository):
    python pipelineagenttest.py --setup-project --project acme-eng --repo-url https://github.com/acme/eng-repo.git --jira-connection acme-eng
    python pipelineagenttest.py --setup-project --project acme-eng --repo-url https://github.com/acme/eng-repo.git --branch develop

Set/update the git access token for a project (needed for private repos and to open PRs):
    python pipelineagenttest.py --setup-token --project acme-eng --token ghp_xxx

List saved projects:
    python pipelineagenttest.py --list

Remove a project:
    python pipelineagenttest.py --delete acme-eng

Run the pipeline on one specific ticket (Codex drafts a plan, asks you to
approve it, then Codex executes only on approval):
    python pipelineagenttest.py --project acme-eng ENG-42
    python pipelineagenttest.py --project acme-eng --jira-connection acme-eng --debug ENG-42

If --jira-connection is omitted, the project's linked connection is used, then
$JIRA_AGENT_CONNECTION, then "default".
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from agents.jira_agent.logging_config import configure_logging
from agents.pipeline_agent.agent import build_execution_agent, open_execution_workspace
from agents.pipeline_agent.manager import manager_enabled, run_managed_pipeline
from agents.pipeline_agent.config import load_settings
from agents.pipeline_agent.credentials import (
    delete_project,
    get_project,
    list_projects,
    upsert_git_token,
    upsert_project,
)
from agents.pipeline_agent.runner import run_codex_planning, run_execution
from agents.pipeline_agent.schemas import ImplementationPlan, plan_to_text


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure repository/credentials and run the ticket -> Codex plan -> Codex execute pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ticket", nargs="?", default=None, help="Jira ticket key to plan and implement, e.g. ENG-42.")
    parser.add_argument("--project", "-p", default=None, help="Project name to use.")
    parser.add_argument(
        "--jira-connection",
        "-j",
        default=None,
        help="Jira connection/project name to read the ticket from (default: the project's linked connection).",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive approval prompt and execute immediately (use with care).",
    )

    setup_group = parser.add_argument_group("project setup")
    setup_group.add_argument("--setup-project", action="store_true", help="Create or update a project, then exit.")
    setup_group.add_argument("--repo-url", default=None, help="Repository URL, e.g. https://github.com/org/repo.git.")
    setup_group.add_argument("--branch", default="main", help="Default branch (default: main).")
    setup_group.add_argument(
        "--setup-token", action="store_true", help="Set the git access token for a project, then exit."
    )
    setup_group.add_argument("--token", default=None, help="Git access token to store for --setup-token.")

    parser.add_argument("--list", action="store_true", help="List saved projects, then exit.")
    parser.add_argument("--delete", metavar="NAME", help="Delete a saved project, then exit.")
    parser.add_argument("--debug", action="store_true", help="Trace every model call, tool call, and MCP request.")
    return parser.parse_args()


def _print_projects() -> None:
    projects = list_projects()
    if not projects:
        print("No projects saved yet. Create one with:")
        print("  python pipelineagenttest.py --setup-project --project NAME --repo-url URL")
        return
    for project in projects:
        print(
            f"- {project.name}: repo={project.repository_url or '-'} branch={project.default_branch} "
            f"jira={project.jira_connection_name or '-'}"
        )


def _print_plan(plan: ImplementationPlan) -> None:
    print(f"\n=== Plan for {plan.ticket_key} ===")
    print(f"Summary: {plan.summary}")
    print(f"\nApproach: {plan.approach}")
    print("\nSteps:")
    for i, step in enumerate(plan.steps, 1):
        print(f"  {i}. {step.title} - {step.detail}")
    if plan.risks:
        print(f"\nRisks: {plan.risks}")
    if plan.out_of_scope:
        print(f"Out of scope: {plan.out_of_scope}")


async def _run(project_name: str, jira_connection_name: str | None, ticket_key: str, auto_approve: bool, debug: bool) -> None:
    settings = load_settings(jira_connection_name, project_name)
    trace = configure_logging("DEBUG" if debug else settings.log_level)

    print(f"Pushing ticket {ticket_key} to the sandbox and asking Codex to plan for '{settings.project_name}' ({settings.repository_url})...")
    planning_result = await run_codex_planning(settings, ticket_key)
    _print_plan(planning_result.plan)
    if trace:
        print(f"\n[trace] planning transcript:\n{planning_result.transcript}")

    if not auto_approve:
        answer = input("\nApprove this plan and let Codex implement it? [y/N] ").strip().lower()
        if answer != "y":
            print("Rejected. Nothing was touched.")
            return

    print("\nApproved. Opening a sandbox and starting execution...")
    pr_url = None
    plan_text = plan_to_text(planning_result.plan)
    if manager_enabled(settings):
        async with open_execution_workspace(settings, ticket_key, plan_text) as sandbox:
            outcome = await run_managed_pipeline(
                settings,
                sandbox,
                ticket_key,
                plan_text,
                project_id=settings.project_id,
                run_id="cli-" + ticket_key,
            )
        print("\n=== Execution summary ===")
        print(outcome.report_markdown)
        if outcome.pr_url:
            print(f"\nPull request: {outcome.pr_url}")
        return

    async with build_execution_agent(settings, ticket_key, plan_text) as (agent, sandbox):
        execution_result = await run_execution(agent, "Implement the approved plan and push a branch.")
        # The coding engine only pushes a branch; open the PR from it here,
        # in the same container, before it is torn down.
        head = sandbox.current_branch()
        if head and head != settings.default_branch:
            print(f"Opening pull request for branch {head}...")
            first_line = next((ln for ln in execution_result.summary.strip().splitlines() if ln.strip()), "")
            pr = sandbox.open_pull_request(
                base=settings.default_branch,
                head=head,
                title=f"{ticket_key}: {first_line[:72]}" if first_line else ticket_key,
                body=f"Automated implementation for {ticket_key}.\n\n{execution_result.summary.strip()}",
            )
            pr_url = pr["url"] if pr["success"] else None
            if not pr["success"]:
                print(f"Could not open a pull request: {(pr.get('stderr') or '').strip()[:400]}")

    print("\n=== Execution summary ===")
    print(execution_result.summary)
    if pr_url or execution_result.pr_url:
        print(f"\nPull request: {pr_url or execution_result.pr_url}")
    if trace:
        print(f"\n[trace] execution transcript:\n{execution_result.transcript}")


def main() -> None:
    load_dotenv()
    args = _parse_args()

    if args.list:
        _print_projects()
        return

    if args.delete:
        removed = delete_project(args.delete)
        print(f"Deleted project {args.delete!r}." if removed else f"No project named {args.delete!r}.")
        return

    if args.setup_project:
        if not args.project or not args.repo_url:
            raise SystemExit("--setup-project requires --project and --repo-url")
        project = upsert_project(
            name=args.project,
            repository_url=args.repo_url,
            default_branch=args.branch,
            jira_connection_name=args.jira_connection,
        )
        print(f"Saved project '{project.name}' -> {project.repository_url} (branch: {project.default_branch}).")
        print("If the repository is private, set its access token with:")
        print(f"  python pipelineagenttest.py --setup-token --project {project.name} --token <TOKEN>")
        return

    if args.setup_token:
        if not args.project or not args.token:
            raise SystemExit("--setup-token requires --project and --token")
        project = get_project(args.project)
        if project is None:
            raise SystemExit(f"No project named {args.project!r}. Create one first with --setup-project.")
        repo_url = args.repo_url or project.repository_url
        upsert_git_token(project_id=project.id, repository_url=repo_url, access_token=args.token)
        print(f"Saved git access token for project '{project.name}'.")
        return

    if not args.project:
        raise SystemExit("--project is required (or use --setup-project / --list).")
    if not args.ticket:
        raise SystemExit("A ticket key is required, e.g. `python pipelineagenttest.py --project acme-eng ENG-42`.")

    try:
        asyncio.run(_run(args.project, args.jira_connection, args.ticket, args.yes, args.debug))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
