import { Link } from "react-router-dom";
import type {
  AzureConnection,
  JiraConnection,
  Project,
} from "../api/types";

interface SetupChecklistProps {
  codexConnected: boolean | null;
  connections: JiraConnection[];
  azureConnections: AzureConnection[];
  projects: Project[];
}

export function SetupChecklist({
  codexConnected,
  connections,
  azureConnections,
  projects,
}: SetupChecklistProps) {
  const jiraReady = connections.some(
    (c) => c.has_oauth_token || c.has_api_token,
  );
  const azureReady = azureConnections.some((c) => c.has_pat);
  const ticketSourceReady = jiraReady || azureReady;
  const hasProject = projects.length > 0;
  const readyProject =
    projects.find((p) => p.readiness_issues.length === 0) ?? projects[0];

  const steps = [
    {
      done: codexConnected === true,
      label: "Connect Codex",
      detail:
        codexConnected === true
          ? "ChatGPT login is connected."
          : "Connect a ChatGPT account, or leave the server on OPENAI_API_KEY.",
      to: "/connections#codex",
      action: codexConnected === true ? "Review" : "Connect Codex",
    },
    {
      done: ticketSourceReady,
      label: "Authorize Jira or Azure",
      detail: ticketSourceReady
        ? "At least one ticket source can list tickets."
        : "Create a Jira or Azure DevOps connection and authorize it so projects can load tickets.",
      to: ticketSourceReady ? "/connections" : "/connections#jira",
      action: ticketSourceReady ? "Review" : "Authorize tracker",
    },
    {
      done: hasProject,
      label: "Create a project",
      detail: hasProject
        ? "A repo is linked."
        : "Name a repo and attach the ticket connection.",
      to: "/#new-project",
      action: hasProject ? "View projects" : "Create a project",
    },
    {
      done: projects.length > 0 && projects.every((p) => p.has_git_token),
      label: "Authorize GitHub",
      detail:
        "Connect GitHub once for clone and pull request access across projects.",
      to: "/connections#github",
      action: "Authorize GitHub",
      disabled: !hasProject,
    },
    {
      done: Boolean(readyProject && readyProject.readiness_issues.length === 0),
      label: "Pick a ticket",
      detail: "Start a pipeline from an open story.",
      to: readyProject ? `/projects/${readyProject.id}/tickets` : "/",
      action: "Pick a ticket",
      disabled: !readyProject || readyProject.readiness_issues.length > 0,
    },
  ];

  const remaining = steps.filter((s) => !s.done).length;
  if (remaining === 0) return null;

  return (
    <div className="card setup-checklist">
      <h3>Get to the first run</h3>
      <p className="muted small">
        {remaining} step{remaining === 1 ? "" : "s"} left. This is the path from
        a blank install to a pipeline.
      </p>
      <ol className="setup-steps">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "setup-done" : undefined}>
            <div>
              <strong>{step.label}</strong>
              <p className="muted small">{step.detail}</p>
            </div>
            {step.done ? (
              <span className="badge badge-ok">Done</span>
            ) : (
              <Link
                className={`btn ${step.disabled ? "btn-secondary" : ""}`}
                to={step.to}
                aria-disabled={step.disabled}
                onClick={(e) => {
                  if (step.disabled) e.preventDefault();
                }}
              >
                {step.action}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
