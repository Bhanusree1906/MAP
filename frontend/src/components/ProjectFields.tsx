import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatError } from "../api/client";
import type {
  AzureConnection,
  AzureGitRepo,
  GitSource,
  GitHubRepo,
  JiraConnection,
  SalesforceConnection,
  SandboxKind,
  TicketGroup,
} from "../api/types";

export interface GitHubConnectionOption {
  name: string;
  login: string | null;
}

export type TicketSource = "jira" | "azure" | "group";

export interface ProjectDraft {
  name: string;
  description: string;
  repoUrl: string;
  branch: string;
  gitSource: GitSource;
  ticketSource: TicketSource;
  connectionName: string;
  ticketGroupName: string;
  githubConnectionName: string;
  azureGitConnectionName: string;
  sandboxKind: SandboxKind;
  sfConnectionName: string;
}

interface ProjectFieldsProps {
  draft: ProjectDraft;
  onChange: (draft: ProjectDraft) => void;
  connections: JiraConnection[];
  azureConnections: AzureConnection[];
  sfConnections: SalesforceConnection[];
  githubConnections: GitHubConnectionOption[];
  ticketGroups?: TicketGroup[];
  nameLocked?: boolean;
  /** Create mode: pick a repo from the selected GitHub account instead of typing a URL. */
  repoPicker?: boolean;
}

export function ProjectFields({
  draft,
  onChange,
  connections,
  azureConnections,
  sfConnections,
  githubConnections,
  ticketGroups = [],
  nameLocked = false,
  repoPicker = false,
}: ProjectFieldsProps) {
  const set = (patch: Partial<ProjectDraft>) =>
    onChange({ ...draft, ...patch });
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState<string | null>(null);

  const [azureRepos, setAzureRepos] = useState<AzureGitRepo[]>([]);
  const [azureReposLoading, setAzureReposLoading] = useState(false);
  const [azureReposError, setAzureReposError] = useState<string | null>(null);

  useEffect(() => {
    if (
      !repoPicker ||
      draft.gitSource !== "github" ||
      !draft.githubConnectionName
    ) {
      setRepos([]);
      setReposError(null);
      return;
    }
    let cancelled = false;
    setReposLoading(true);
    setReposError(null);
    api
      .listGitHubRepos(draft.githubConnectionName)
      .then((result) => {
        if (!cancelled) setRepos(result.repos);
      })
      .catch((e) => {
        if (!cancelled) {
          setRepos([]);
          setReposError(formatError(e));
        }
      })
      .finally(() => {
        if (!cancelled) setReposLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repoPicker, draft.gitSource, draft.githubConnectionName]);

  useEffect(() => {
    if (
      !repoPicker ||
      draft.gitSource !== "azure" ||
      !draft.azureGitConnectionName
    ) {
      setAzureRepos([]);
      setAzureReposError(null);
      return;
    }
    let cancelled = false;
    setAzureReposLoading(true);
    setAzureReposError(null);
    api
      .listAzureGitRepos(draft.azureGitConnectionName)
      .then((result) => {
        if (!cancelled) setAzureRepos(result);
      })
      .catch((e) => {
        if (!cancelled) {
          setAzureRepos([]);
          setAzureReposError(formatError(e));
        }
      })
      .finally(() => {
        if (!cancelled) setAzureReposLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repoPicker, draft.gitSource, draft.azureGitConnectionName]);

  return (
    <>
      <div className="form-row">
        <label>
          Git host
          <div className="source-mode-tabs" style={{ marginTop: 4, marginBottom: 4 }}>
            <button
              type="button"
              className={`source-tab ${draft.gitSource === "github" ? "active" : ""}`}
              onClick={() =>
                set({
                  gitSource: "github",
                  githubConnectionName: "",
                  azureGitConnectionName: "",
                  repoUrl: "",
                })
              }
            >
              GitHub
            </button>
            <button
              type="button"
              className={`source-tab ${draft.gitSource === "azure" ? "active" : ""}`}
              onClick={() =>
                set({
                  gitSource: "azure",
                  githubConnectionName: "",
                  azureGitConnectionName: "",
                  repoUrl: "",
                })
              }
            >
              Azure DevOps Git
            </button>
          </div>
        </label>
      </div>
      {draft.gitSource === "github" ? (
        <div className="form-row">
          <label>
            GitHub account
            <select
              value={draft.githubConnectionName}
              onChange={(e) =>
                set(
                  repoPicker
                    ? { githubConnectionName: e.target.value, repoUrl: "" }
                    : { githubConnectionName: e.target.value },
                )
              }
              required
            >
              <option value="">— select account —</option>
              {githubConnections.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.login ?? c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Name
            <input
              value={draft.name}
              onChange={(e) => set({ name: e.target.value })}
              required
              disabled={nameLocked}
            />
          </label>
          <label>
            Description
            <input
              value={draft.description}
              onChange={(e) => set({ description: e.target.value })}
              placeholder="What this repo is for (optional)"
            />
          </label>
        </div>
      ) : (
        <div className="form-row">
          <label>
            Azure DevOps connection
            <select
              value={draft.azureGitConnectionName}
              onChange={(e) =>
                set(
                  repoPicker
                    ? { azureGitConnectionName: e.target.value, repoUrl: "" }
                    : { azureGitConnectionName: e.target.value },
                )
              }
              required
            >
              <option value="">— select connection —</option>
              {azureConnections.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.project_name})
                </option>
              ))}
            </select>
          </label>
          <label>
            Name
            <input
              value={draft.name}
              onChange={(e) => set({ name: e.target.value })}
              required
              disabled={nameLocked}
            />
          </label>
          <label>
            Description
            <input
              value={draft.description}
              onChange={(e) => set({ description: e.target.value })}
              placeholder="What this repo is for (optional)"
            />
          </label>
        </div>
      )}

      {repoPicker ? (
        <div className="form-row">
          <label>
            Repository
            <select
              value={draft.repoUrl}
              onChange={(e) => {
                const selectedUrl = e.target.value;
                if (draft.gitSource === "github") {
                  const repo = repos.find((r) => r.clone_url === selectedUrl);
                  set({
                    repoUrl: selectedUrl,
                    branch: repo?.default_branch || "main",
                  });
                } else {
                  const repo = azureRepos.find((r) => r.clone_url === selectedUrl);
                  set({
                    repoUrl: selectedUrl,
                    branch: repo?.default_branch || "main",
                  });
                }
              }}
              disabled={
                draft.gitSource === "github"
                  ? !draft.githubConnectionName || reposLoading
                  : !draft.azureGitConnectionName || azureReposLoading
              }
            >
              <option value="">
                {draft.gitSource === "github"
                  ? !draft.githubConnectionName
                    ? "— select a GitHub account first —"
                    : reposLoading
                      ? "Loading repositories…"
                      : "— create a new repository from the project name —"
                  : !draft.azureGitConnectionName
                    ? "— select an Azure DevOps connection first —"
                    : azureReposLoading
                      ? "Loading repositories…"
                      : "— select an existing repository —"}
              </option>
              {draft.gitSource === "github"
                ? repos.map((r) => (
                    <option key={r.full_name} value={r.clone_url}>
                      {r.full_name}
                      {r.private ? " (private)" : ""}
                    </option>
                  ))
                : azureRepos.map((r) => (
                    <option key={r.id} value={r.clone_url}>
                      {r.name} ({r.default_branch || "main"})
                    </option>
                  ))}
            </select>
          </label>
        </div>
      ) : (
        <div className="form-row">
          <label>
            Repository URL
            <input
              value={draft.repoUrl}
              onChange={(e) => set({ repoUrl: e.target.value })}
              placeholder={
                draft.gitSource === "github"
                  ? "https://github.com/org/repo.git"
                  : "https://dev.azure.com/org/project/_git/repository"
              }
              required
            />
          </label>
        </div>
      )}
      {repoPicker &&
        (draft.gitSource === "github" ? reposError : azureReposError) && (
          <p className="muted small">
            Could not load repositories:{" "}
            {draft.gitSource === "github" ? reposError : azureReposError}
          </p>
        )}
      <div className="form-row">
        <label>
          Default branch
          <input
            value={draft.branch}
            onChange={(e) => set({ branch: e.target.value })}
          />
        </label>
        <label>
          Ticket source
          <div className="source-mode-tabs" style={{ marginTop: 4, marginBottom: 4 }}>
            <button
              type="button"
              className={`source-tab ${draft.ticketSource === "jira" ? "active" : ""}`}
              onClick={() =>
                set({ ticketSource: "jira" as const, connectionName: "" })
              }
            >
              Jira
            </button>
            <button
              type="button"
              className={`source-tab ${draft.ticketSource === "azure" ? "active" : ""}`}
              onClick={() =>
                set({ ticketSource: "azure" as const, connectionName: "" })
              }
            >
              Azure DevOps
            </button>
            <button
              type="button"
              className={`source-tab ${draft.ticketSource === "group" ? "active" : ""}`}
              onClick={() =>
                set({
                  ticketSource: "group" as const,
                  connectionName: "",
                })
              }
            >
              Jira Group
            </button>
          </div>
          {draft.ticketSource === "group" ? (
            <select
              value={draft.ticketGroupName}
              onChange={(e) => set({ ticketGroupName: e.target.value })}
            >
              <option value="">— none —</option>
              {ticketGroups.map((g) => (
                <option key={g.id} value={g.name}>
                  {g.name} ({g.ticket_count} ticket{g.ticket_count === 1 ? "" : "s"})
                </option>
              ))}
            </select>
          ) : (
            <select
              value={draft.connectionName}
              onChange={(e) => set({ connectionName: e.target.value })}
            >
              <option value="">— none —</option>
              {draft.ticketSource === "jira"
                ? connections.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} ({c.project_key})
                      {c.has_oauth_token || c.has_api_token
                        ? ""
                        : " — not authorized"}
                    </option>
                  ))
                : azureConnections.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} ({c.project_name})
                    </option>
                  ))}
            </select>
          )}
        </label>
      </div>
      <div className="form-row">
        <label>
          Sandbox environment
          <select
            value={draft.sandboxKind}
            onChange={(e) =>
              set({ sandboxKind: e.target.value as SandboxKind })
            }
          >
            <option value="default">Default (Node/Python + Codex)</option>
            <option value="salesforce">Salesforce (adds sf CLI)</option>
          </select>
        </label>
        {draft.sandboxKind === "salesforce" && (
          <label>
            Salesforce org
            <select
              value={draft.sfConnectionName}
              onChange={(e) => set({ sfConnectionName: e.target.value })}
            >
              <option value="">— none —</option>
              {sfConnections.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.org_alias})
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {((draft.ticketSource === "jira" && connections.length === 0) ||
        (draft.ticketSource === "azure" && azureConnections.length === 0) ||
        (draft.ticketSource === "group" && ticketGroups.length === 0)) && (
        <p className="muted small">
          {draft.ticketSource === "jira"
            ? "No Jira"
            : draft.ticketSource === "azure"
              ? "No Azure DevOps"
              : "No Jira groups"}{" "}
          yet.{" "}
          <Link to={draft.ticketSource === "group" ? "/manual-ingest" : "/connections#jira"}>
            Create one first
          </Link>.
        </p>
      )}
    </>
  );
}

export function emptyProjectDraft(): ProjectDraft {
  return {
    name: "",
    description: "",
    repoUrl: "",
    branch: "main",
    gitSource: "github",
    ticketSource: "jira",
    connectionName: "",
    ticketGroupName: "",
    sandboxKind: "default",
    sfConnectionName: "",
    githubConnectionName: "",
    azureGitConnectionName: "",
  };
}

export function draftFromProject(project: {
  name: string;
  description: string | null;
  repository_url: string | null;
  default_branch: string;
  jira_connection_name: string | null;
  azure_connection_name: string | null;
  ticket_group_name: string | null;
  sandbox_kind: SandboxKind;
  salesforce_connection_name: string | null;
  github_connection_name: string | null;
  azure_git_connection_name: string | null;
}): ProjectDraft {
  const useGroup = Boolean(project.ticket_group_name);
  const useAzureTicket = Boolean(
    project.azure_connection_name && !project.jira_connection_name,
  );
  const gitSource: GitSource = project.azure_git_connection_name
    ? "azure"
    : "github";
  return {
    name: project.name,
    description: project.description ?? "",
    repoUrl: project.repository_url ?? "",
    branch: project.default_branch,
    gitSource,
    ticketSource: useGroup ? "group" : useAzureTicket ? "azure" : "jira",
    connectionName: useAzureTicket
      ? project.azure_connection_name ?? ""
      : project.jira_connection_name ?? "",
    ticketGroupName: project.ticket_group_name ?? "",
    sandboxKind: project.sandbox_kind,
    sfConnectionName: project.salesforce_connection_name ?? "",
    githubConnectionName: project.github_connection_name ?? "",
    azureGitConnectionName: project.azure_git_connection_name ?? "",
  };
}
