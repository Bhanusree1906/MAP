"""What a project needs configured before its pipeline can clone a repo for
Codex planning or reach a pull request. Surfaced to the UI (see ProjectOut's
readiness_issues) so missing config is caught before a run is even started,
instead of discovered after a full plan/approve cycle fails deep inside
execution (a real cloning/PR-opening failure - "could not read Username for
'https://github.com'" - is what prompted this: the project simply had no
git token, but nothing said so until execution already ran).
"""

from __future__ import annotations

from database.models.project import Project

from agents.skills.service import get_active_memory

from .credentials import get_effective_git_token
from .salesforce_credentials import get_salesforce_connection


def check_project_readiness(project: Project) -> list[str]:
    issues = []
    if not project.repository_url:
        issues.append("No repository URL configured")
    if get_effective_git_token(project.id) is None:
        issues.append("No git account - needed to clone and open the pull request")
    if (
        not project.jira_connection_name
        and not project.azure_connection_name
        and not project.ticket_group_name
    ):
        issues.append("No Jira, Azure, or Jira group ticket source linked")

    if project.sandbox_kind == "salesforce":
        if not project.salesforce_connection_name:
            issues.append("Salesforce sandbox selected but no Salesforce connection linked")
        else:
            sf_conn = get_salesforce_connection(project.salesforce_connection_name)
            if sf_conn is None or not sf_conn.sfdx_auth_url:
                issues.append(
                    f"Linked Salesforce connection {project.salesforce_connection_name!r} has no auth URL configured"
                )
        if get_active_memory(project.id) is None:
            issues.append(
                "No org catalog yet - run the Salesforce bootstrap so planning sees real API names"
            )

    return issues
