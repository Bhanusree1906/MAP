import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatError } from "../api/client";
import type {
  AzureConnection,
  GitSource,
  GitHubRepo,
  JiraConnection,
  SalesforceConnection,
  SandboxKind,
  TicketGroup,
  TicketSource,
} from "../api/types";

interface GitHubConnectionOption {
  name: string;
  login: string | null;
}

interface NewProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated?: (projectId: string) => void;
  connections: JiraConnection[];
  azureConnections: AzureConnection[];
  sfConnections: SalesforceConnection[];
  githubConnections: GitHubConnectionOption[];
}

type RepoSourceMode = "select" | "create_new" | "custom_url";

export function NewProjectModal({
  isOpen,
  onClose,
  onCreated,
  connections,
  azureConnections,
  sfConnections,
  githubConnections,
}: NewProjectModalProps) {
  const navigate = useNavigate();

  const [availableGitHubAccounts, setAvailableGitHubAccounts] =
    useState(githubConnections);
  const [githubAccount, setGithubAccount] = useState("");
  const [newGitHubLogin, setNewGitHubLogin] = useState("");
  const [sourceMode, setSourceMode] = useState<RepoSourceMode>("select");

  const [gitSource, setGitSource] = useState<GitSource>("github");

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [ticketSource, setTicketSource] = useState<TicketSource>("jira");
  const [ticketConnection, setTicketConnection] = useState("");
  const [ticketGroup, setTicketGroup] = useState("");
  const [ticketGroups, setTicketGroups] = useState<TicketGroup[]>([]);
  const [sandboxKind, setSandboxKind] = useState<SandboxKind>("default");
  const [sfConnection, setSfConnection] = useState("");

  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState<string | null>(null);
  const [repoSearch, setRepoSearch] = useState("");

  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAvailableGitHubAccounts(githubConnections);
  }, [githubConnections]);

  useEffect(() => {
    api
      .listTicketGroups()
      .then(setTicketGroups)
      .catch(() => setTicketGroups([]));
  }, []);

  useEffect(() => {
    const onGitHubOAuthComplete = (event: MessageEvent) => {
      if (event.data?.type !== "github-oauth-done") return;
      api
        .getGitHubStatus()
        .then(({ connections: nextConnections }) => {
          setAvailableGitHubAccounts(nextConnections);
          const connectedAccount = nextConnections.find(
            (connection) => connection.login === event.data.account,
          );
          if (connectedAccount) setGithubAccount(connectedAccount.name);
        })
        .catch((oauthError) => setError(formatError(oauthError)));
    };
    window.addEventListener("message", onGitHubOAuthComplete);
    return () => window.removeEventListener("message", onGitHubOAuthComplete);
  }, []);

  // Auto-select initial GitHub account if available
  useEffect(() => {
    if (availableGitHubAccounts.length > 0 && !githubAccount) {
      setGithubAccount(availableGitHubAccounts[0].name);
    }
  }, [availableGitHubAccounts, githubAccount]);

  // Load repos when GitHub account changes
  useEffect(() => {
    if (!githubAccount || sourceMode !== "select") {
      setRepos([]);
      setReposError(null);
      return;
    }

    let cancelled = false;
    setReposLoading(true);
    setReposError(null);

    api
      .listGitHubRepos(githubAccount)
      .then((res) => {
        if (!cancelled) setRepos(res.repos);
      })
      .catch((err) => {
        if (!cancelled) {
          setRepos([]);
          setReposError(formatError(err));
        }
      })
      .finally(() => {
        if (!cancelled) setReposLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [githubAccount, sourceMode, gitSource]);

  // Handle ESC key to close modal
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSelectRepo = (selectedRepo: GitHubRepo) => {
    setRepoUrl(selectedRepo.clone_url);
    if (!name || name === "my-new-project") {
      setName(selectedRepo.name);
    }
    setBranch(selectedRepo.default_branch || "main");
  };

  const filteredRepos = repos.filter(
    (r) =>
      r.name.toLowerCase().includes(repoSearch.toLowerCase()) ||
      r.full_name.toLowerCase().includes(repoSearch.toLowerCase()),
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setCreating(true);

    try {
      const finalRepoUrl = sourceMode === "create_new" ? "" : repoUrl.trim();

      const created = await api.createOrUpdateProject({
        name: name.trim(),
        description: description.trim() || null,
        repository_url: finalRepoUrl,
        default_branch: branch.trim() || "main",
        jira_connection_name:
          ticketSource === "jira" ? ticketConnection || null : null,
        azure_connection_name:
          ticketSource === "azure" ? ticketConnection || null : null,
        ticket_group_name:
          ticketSource === "group" ? ticketGroup || null : null,
        github_connection_name:
          gitSource === "github" ? githubAccount || null : null,
        azure_git_connection_name: null,
        sandbox_kind: sandboxKind,
        salesforce_connection_name:
          sandboxKind === "salesforce" ? sfConnection || null : null,
      });

      onClose();
      if (onCreated) {
        onCreated(created.id);
      } else {
        navigate(`/projects/${created.id}`);
      }
    } catch (err) {
      setError(formatError(err));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="modal-container project-create-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="modal-icon-badge">
              <span>+</span>
            </div>
            <div>
              <h3>CREATE NEW PROJECT</h3>
              <p className="modal-subtitle">
                Configure repository, Jira/Azure ticket integration, and
                execution sandbox
              </p>
            </div>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        {error && <div className="banner banner-err modal-banner">{error}</div>}

        <form onSubmit={handleSubmit} className="project-form">
          {/* Section 1: Git & Repository Source */}
          <div className="form-section">
            <div className="form-section-title">
              <span className="step-num">01</span>
              <h4>GIT & REPOSITORY SOURCE</h4>
            </div>

            <div className="form-grid">
              <label className="field-group full-width">
                <span className="field-label">Git Host</span>
                <div className="source-mode-tabs">
                  <button
                    type="button"
                    className={`source-tab ${gitSource === "github" ? "active" : ""}`}
                    onClick={() => {
                      setGitSource("github");
                      setRepoUrl("");
                    }}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                    </svg>
                    GitHub
                  </button>
                  <button
                    type="button"
                    className={`source-tab ${gitSource === "azure" ? "active" : ""}`}
                    onClick={() => {
                      setGitSource("azure");
                      setRepoUrl("");
                    }}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <polyline points="14 2 14 8 20 8" />
                      <line x1="8" y1="13" x2="16" y2="13" />
                      <line x1="8" y1="17" x2="16" y2="17" />
                    </svg>
                    Azure DevOps Git
                  </button>
                </div>
              </label>
            </div>

            {gitSource === "github" ? (
              <>
                <div className="form-grid">
                  <label className="field-group">
                    <span className="field-label">GitHub Account</span>
                    <select
                      className="cyber-input"
                      value={githubAccount}
                      onChange={(e) => {
                        setGithubAccount(e.target.value);
                        if (sourceMode === "select") {
                          setRepoUrl("");
                        }
                      }}
                    >
                      <option value="">— Select connected GitHub account —</option>
                      {availableGitHubAccounts.map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.login ? `@${c.login}` : c.name} ({c.name})
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="account-alert">
                    <label className="field-group">
                      <span className="field-label">Account to add</span>
                      <input
                        type="text"
                        className="cyber-input"
                        value={newGitHubLogin}
                        onChange={(event) => setNewGitHubLogin(event.target.value)}
                        placeholder="GitHub username"
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => {
                        const startUrl = api.githubOAuthStartUrl(
                          newGitHubLogin.trim() || undefined,
                          true,
                        );
                        window.open(
                          startUrl,
                          "github_oauth_popup",
                          "popup=yes,width=640,height=720",
                        );
                      }}
                    >
                      Add GitHub account
                    </button>
                  </div>

                  {availableGitHubAccounts.length === 0 && (
                    <div className="account-alert">
                      <Link to="/connections#github" onClick={onClose}>
                        Manage GitHub connections →
                      </Link>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <label className="field-group full-width">
                  <span className="field-label">Repository URL</span>
                  <input
                    type="url"
                    className="cyber-input"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    placeholder="https://dev.azure.com/org/project/_git/repository"
                    required
                  />
                  <p className="field-hint">
                    Enter the Azure DevOps Git repository clone URL
                  </p>
                </label>
              </>
            )}

            {/* Source Mode Selector - GitHub only */}
            {gitSource === "github" && (
              <div className="source-mode-tabs">
                <button
                  type="button"
                  className={`source-tab ${sourceMode === "select" ? "active" : ""}`}
                  onClick={() => setSourceMode("select")}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                  </svg>
                  Select Existing Repo
                </button>

                <button
                  type="button"
                  className={`source-tab ${sourceMode === "create_new" ? "active" : ""}`}
                  onClick={() => {
                    setSourceMode("create_new");
                    setRepoUrl("");
                  }}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                  Create New GitHub Repo
                </button>

                <button
                  type="button"
                  className={`source-tab ${sourceMode === "custom_url" ? "active" : ""}`}
                  onClick={() => setSourceMode("custom_url")}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                  </svg>
                  Custom Git URL
                </button>
              </div>
            )}

            {/* Mode 1: Select Existing Repo */}
            {sourceMode === "select" && gitSource === "github" && (
              <div className="repo-select-container">
                <div className="repo-search-row">
                  <input
                    type="text"
                    className="cyber-input repo-search-input"
                    placeholder="Search repositories..."
                    value={repoSearch}
                    onChange={(e) => setRepoSearch(e.target.value)}
                    disabled={!githubAccount || reposLoading}
                  />
                  {reposLoading && <span className="loading-spinner-small" />}
                </div>

                {reposError && (
                  <p className="field-error">
                    Could not load repositories: {reposError}
                  </p>
                )}

                <div className="repo-list-scroll">
                  {!githubAccount ? (
                    <div className="repo-empty-note">
                      Select a GitHub account above to view available
                      repositories.
                    </div>
                  ) : reposLoading ? (
                    <div className="repo-empty-note">
                      Fetching repositories from GitHub…
                    </div>
                  ) : filteredRepos.length === 0 ? (
                    <div className="repo-empty-note">
                      {repos.length === 0
                        ? "No repositories found for this account."
                        : "No repositories match your search."}
                    </div>
                  ) : (
                    filteredRepos.map((repo) => {
                      const isSelected = repoUrl === repo.clone_url;
                      return (
                        <div
                          key={repo.full_name}
                          className={`repo-card-item ${isSelected ? "selected" : ""}`}
                          onClick={() => handleSelectRepo(repo)}
                        >
                          <div className="repo-item-main">
                            <span className="repo-name">{repo.name}</span>
                            <span className="repo-fullname">
                              {repo.full_name}
                            </span>
                          </div>
                          <div className="repo-item-meta">
                            {repo.private ? (
                              <span className="repo-badge-private">
                                Private
                              </span>
                            ) : (
                              <span className="repo-badge-public">Public</span>
                            )}
                            <span className="repo-branch-tag">
                              {repo.default_branch || "main"}
                            </span>
                            {isSelected && (
                              <span className="repo-check-icon">✓</span>
                            )}
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            {/* Mode 2: Create New Repo (GitHub only) */}
            {sourceMode === "create_new" && gitSource === "github" && (
              <div className="create-repo-banner">
                <div className="create-repo-icon">⚡</div>
                <div>
                  <strong>Automatic Provisioning</strong>
                  <p>
                    A new GitHub repository named after your project will be
                    automatically created under{" "}
                    <code>{githubAccount || "the selected account"}</code> with
                    an initial commit.
                  </p>
                </div>
              </div>
            )}

            {/* Mode 3: Custom Git URL (GitHub only) */}
            {sourceMode === "custom_url" && gitSource === "github" && (
              <div className="form-grid">
                <label className="field-group full-width">
                  <span className="field-label">Repository Git Clone URL</span>
                  <input
                    type="url"
                    className="cyber-input"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    placeholder="https://github.com/organization/repository.git"
                    required
                  />
                </label>
              </div>
            )}
          </div>

          {/* Section 2: Project Identity */}
          <div className="form-section">
            <div className="form-section-title">
              <span className="step-num">02</span>
              <h4>PROJECT DETAILS</h4>
            </div>

            <div className="form-grid two-cols">
              <label className="field-group">
                <span className="field-label">Project Name</span>
                <input
                  type="text"
                  className="cyber-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. core-auth-service"
                  required
                />
              </label>

              <label className="field-group">
                <span className="field-label">Default Branch</span>
                <input
                  type="text"
                  className="cyber-input mono"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                  required
                />
              </label>

              <label className="field-group full-width">
                <span className="field-label">Description (Optional)</span>
                <input
                  type="text"
                  className="cyber-input"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What this repository and pipeline manage..."
                />
              </label>
            </div>
          </div>

          {/* Section 3: Integrations & Sandbox */}
          <div className="form-section">
            <div className="form-section-title">
              <span className="step-num">03</span>
              <h4>INTEGRATIONS & SANDBOX ENVIRONMENT</h4>
            </div>

            <div className="form-grid two-cols">
              <label className="field-group">
                <span className="field-label">Ticket Source</span>
                <div className="source-mode-tabs" style={{ marginBottom: 8 }}>
                  <button
                    type="button"
                    className={`source-tab ${ticketSource === "jira" ? "active" : ""}`}
                    onClick={() => {
                      setTicketSource("jira");
                      setTicketConnection("");
                    }}
                  >
                    Jira
                  </button>
                  <button
                    type="button"
                    className={`source-tab ${ticketSource === "azure" ? "active" : ""}`}
                    onClick={() => {
                      setTicketSource("azure");
                      setTicketConnection("");
                    }}
                  >
                    Azure DevOps
                  </button>
                  <button
                    type="button"
                    className={`source-tab ${ticketSource === "group" ? "active" : ""}`}
                    onClick={() => {
                      setTicketSource("group");
                      setTicketGroup("");
                    }}
                  >
                    Jira Group
                  </button>
                </div>
                {ticketSource === "group" ? (
                  <select
                    className="cyber-input"
                    value={ticketGroup}
                    onChange={(e) => setTicketGroup(e.target.value)}
                  >
                    <option value="">— None (Configure later) —</option>
                    {ticketGroups.map((g) => (
                      <option key={g.id} value={g.name}>
                        {g.name} ({g.ticket_count} ticket
                        {g.ticket_count === 1 ? "" : "s"})
                      </option>
                    ))}
                  </select>
                ) : (
                  <select
                    className="cyber-input"
                    value={ticketConnection}
                    onChange={(e) => setTicketConnection(e.target.value)}
                  >
                    <option value="">— None (Configure later) —</option>
                    {ticketSource === "jira"
                      ? connections.map((c) => (
                          <option key={c.name} value={c.name}>
                            {c.name} ({c.project_key})
                            {c.has_oauth_token || c.has_api_token
                              ? " [Authorized]"
                              : " [Not Authorized]"}
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

              <label className="field-group">
                <span className="field-label">Sandbox Environment</span>
                <select
                  className="cyber-input"
                  value={sandboxKind}
                  onChange={(e) =>
                    setSandboxKind(e.target.value as SandboxKind)
                  }
                >
                  <option value="default">
                    Default (Node/Python + Codex Engine)
                  </option>
                  <option value="salesforce">
                    Salesforce (Adds sf CLI + Org Catalog)
                  </option>
                </select>
              </label>

              {sandboxKind === "salesforce" && (
                <label className="field-group full-width">
                  <span className="field-label">Salesforce Org Connection</span>
                  <select
                    className="cyber-input"
                    value={sfConnection}
                    onChange={(e) => setSfConnection(e.target.value)}
                    required={sandboxKind === "salesforce"}
                  >
                    <option value="">— Select Salesforce Org —</option>
                    {sfConnections.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name} ({c.org_alias})
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          </div>

          {/* Section 4: Live Setup Summary Preview */}
          <div className="setup-preview-card">
            <div className="preview-header">
              <span className="preview-label">
                PRE-FLIGHT CONFIGURATION SUMMARY
              </span>
            </div>
            <div className="preview-chips">
              <div className="preview-chip">
                <span
                  className={`status-dot ${
                    gitSource === "github"
                      ? githubAccount
                        ? "dot-ok"
                        : "dot-warn"
                      : repoUrl
                        ? "dot-ok"
                        : "dot-warn"
                  }`}
                />
                <span>
                  {gitSource === "github"
                    ? `GitHub: ${githubAccount || "Not Selected"}`
                    : `Azure DevOps Git: ${repoUrl ? "URL Provided" : "Not Configured"}`}
                </span>
              </div>
              <div className="preview-chip">
                <span
                  className={`status-dot ${
                    ticketSource === "group"
                      ? ticketGroup
                        ? "dot-ok"
                        : "dot-muted"
                      : ticketConnection
                        ? "dot-ok"
                        : "dot-muted"
                  }`}
                />
                <span>
                  {ticketSource === "jira"
                    ? "Jira"
                    : ticketSource === "azure"
                      ? "Azure DevOps"
                      : "Jira Group"}
                  :{" "}
                  {ticketSource === "group" ? ticketGroup : ticketConnection || "None"}
                </span>
              </div>
              <div className="preview-chip">
                <span className="status-dot dot-ok" />
                <span>Sandbox: {sandboxKind}</span>
              </div>
              <div className="preview-chip">
                <span className="status-dot dot-ok" />
                <span>Branch: {branch || "main"}</span>
              </div>
            </div>
          </div>

          {/* Modal Footer */}
          <div className="modal-footer">
            <button
              type="button"
              className="btn btn-secondary modal-cancel-btn"
              onClick={onClose}
              disabled={creating}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="btn-cyber-primary"
              disabled={
                creating ||
                !name ||
                (gitSource === "github"
                  ? sourceMode !== "create_new" && !repoUrl
                  : !repoUrl)
              }
            >
              {creating ? (
                <>
                  <span className="btn-spinner" />
                  INITIALIZING PROJECT…
                </>
              ) : (
                <>
                  CREATE PROJECT <span className="btn-arrow">›</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
