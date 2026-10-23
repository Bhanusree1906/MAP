import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, formatError } from "../api/client";
import { NewProjectModal } from "../components/NewProjectModal";
import type {
  AzureConnection,
  JiraConnection,
  PipelineRun,
  Project,
  SalesforceConnection,
} from "../api/types";

type ProjectFilter = "all" | "active" | "blocked";
type SandboxFilter = "all" | "salesforce" | "default";

export function ProjectsPage() {
  const location = useLocation();
  const navigate = useNavigate();

  const [projects, setProjects] = useState<Project[]>([]);
  const [connections, setConnections] = useState<JiraConnection[]>([]);
  const [azureConnections, setAzureConnections] = useState<AzureConnection[]>(
    [],
  );
  const [sfConnections, setSfConnections] = useState<SalesforceConnection[]>(
    [],
  );
  const [githubConnections, setGithubConnections] = useState<
    { name: string; login: string | null }[]
  >([]);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [codexConnected, setCodexConnected] = useState<boolean | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<ProjectFilter>("active");
  const [sandboxFilter, setSandboxFilter] = useState<SandboxFilter>("all");
  const [readinessFilter, setReadinessFilter] = useState<string>("all");
  const [jiraFilter, setJiraFilter] = useState<string>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deletingProject, setDeletingProject] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Focused project detail tabs preview
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [, setActiveTab] = useState("overview");

  const reload = () => {
    setLoading(true);
    Promise.allSettled([
      api.listProjects().then(setProjects),
      api.listConnections().then(setConnections),
      api.listAzureConnections().then(setAzureConnections),
      api.listSalesforceConnections().then(setSfConnections),
      api.getGitHubStatus().then((s) => setGithubConnections(s.connections)),
      api.listRuns().then(setRuns),
      api.getCodexStatus().then((s) => setCodexConnected(s.connected)),
    ])
      .catch((e) => setError(formatError(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    if (location.hash === "#new-project") {
      setIsModalOpen(true);
    }
  }, [location.hash]);

  const handleModalClose = () => {
    setIsModalOpen(false);
    if (location.hash === "#new-project") {
      navigate("/", { replace: true });
    }
  };

  const handleProjectCreated = (newId: string) => {
    reload();
    navigate(`/projects/${newId}`);
  };

  const confirmDelete = async () => {
    if (!deletingProject) return;
    setDeleting(true);
    try {
      await api.deleteProject(deletingProject.id);
      setDeletingProject(null);
      reload();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setDeleting(false);
    }
  };

  // Helper to get initials monogram (e.g. "AD" for "Agentic Demo Repo")
  const getMonogram = (name: string): string => {
    const parts = name.trim().split(/[\s-_]+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  };

  // Clean GitHub repo display URL (e.g. github.com/user/repo.git)
  const formatRepoUrl = (url: string | null): string => {
    if (!url) return "No repository connected";
    return url
      .replace(/^https?:\/\//i, "")
      .replace(/^git@github\.com:/i, "github.com/");
  };

  const repoName = (url: string | null): string => {
    if (!url) return "No repository";
    const cleaned = formatRepoUrl(url).replace(/\.git$/i, "");
    const parts = cleaned.split("/");
    return parts[parts.length - 1] || cleaned;
  };

  const connByName = useMemo(() => {
    const map: Record<string, JiraConnection> = {};
    for (const c of connections) map[c.name] = c;
    return map;
  }, [connections]);

  const azureConnByName = useMemo(() => {
    const map: Record<string, AzureConnection> = {};
    for (const c of azureConnections) map[c.name] = c;
    return map;
  }, [azureConnections]);

  /** Returns true if the project is linked to an azure connection. */
  const projectUsesAzure = (p: Project): boolean =>
    Boolean(p.azure_connection_name && !p.jira_connection_name);

  /** Key badge text: Jira project_key or Azure project name (or null). */
  const projectBadgeText = (p: Project): string | null => {
    if (p.ticket_group_name) return p.ticket_group_name;
    if (projectUsesAzure(p))
      return azureConnByName[p.azure_connection_name ?? ""]?.project_name ?? null;
    return connByName[p.jira_connection_name ?? ""]?.project_key ?? null;
  };

  const runsByProject = useMemo(() => {
    const map: Record<string, PipelineRun[]> = {};
    for (const r of runs) {
      (map[r.project_id] ||= []).push(r);
    }
    return map;
  }, [runs]);

  const latestRunFor = (projectId: string): PipelineRun | undefined => {
    const list = (runsByProject[projectId] || []).slice().sort((a, b) => {
      const at = new Date(a.started_at ?? a.created_at).getTime();
      const bt = new Date(b.started_at ?? b.created_at).getTime();
      if (Number.isNaN(at)) return 1;
      if (Number.isNaN(bt)) return -1;
      return bt - at;
    });
    return list[0];
  };

  // ---- Telemetry metric computations ----
  const jiraAuthorized = connections.filter(
    (c) => c.has_oauth_token || c.has_api_token,
  ).length;
  const jiraHealth =
    connections.length > 0
      ? Math.round((jiraAuthorized / connections.length) * 100)
      : 100;

  const sfPool = sfConnections.length;
  const sfPoolLabel =
    sfPool > 0
      ? `${sfPool} Available`
      : "No Orgs";

  const runs24h = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return runs.filter((r) => {
      if (!r.created_at) return true;
      const t = new Date(r.created_at).getTime();
      return isNaN(t) || t >= cutoff;
    });
  }, [runs]);

  const completedRunsCount = (runs24h.length ? runs24h : runs).filter(
    (r) => r.status === "completed",
  ).length;
  const totalRunsCount = runs24h.length || runs.length;
  const passRate =
    totalRunsCount > 0
      ? Math.round((completedRunsCount / totalRunsCount) * 1000) / 10
      : 0;

  // ---- Filter and Search Projects ----
  const filteredProjects = useMemo(() => {
    return projects.filter((p) => {
      const q = searchQuery.trim().toLowerCase();
      if (q) {
        const matchesName = p.name.toLowerCase().includes(q);
        const matchesRepo = (p.repository_url ?? "").toLowerCase().includes(q);
        const matchesDesc = (p.description ?? "").toLowerCase().includes(q);
        const matchesBranch = (p.default_branch ?? "")
          .toLowerCase()
          .includes(q);
        if (!matchesName && !matchesRepo && !matchesDesc && !matchesBranch) {
          return false;
        }
      }

      // Sandbox filter
      if (sandboxFilter !== "all" && p.sandbox_kind !== sandboxFilter) {
        return false;
      }

      // Readiness filter
      const isBlocked = p.readiness_issues.length > 0 || !p.has_git_token;
      if (readinessFilter === "ready" && isBlocked) return false;
      if (readinessFilter === "warnings" && !isBlocked) return false;

      // Jira connection filter
      if (jiraFilter !== "all" && jiraFilter !== "none") {
        if (p.jira_connection_name !== jiraFilter) return false;
      } else if (jiraFilter === "none" && p.jira_connection_name) {
        return false;
      }

      // Tab filter
      if (activeFilter === "active") return !isBlocked;
      if (activeFilter === "blocked") return isBlocked;
      return true;
    });
  }, [
    projects,
    searchQuery,
    sandboxFilter,
    readinessFilter,
    jiraFilter,
    activeFilter,
  ]);

  const isBlocked = (p: Project): boolean =>
    p.readiness_issues.length > 0 || !p.has_git_token;

  // Focused project defaults to first active project when list changes
  useEffect(() => {
    if (focusedId) return;
    const first = filteredProjects[0];
    if (first) {
      setFocusedId(first.id);
      setActiveTab("overview");
    } else {
      setFocusedId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredProjects]);

  const focusedProject =
    projects.find((p) => p.id === focusedId) ?? filteredProjects[0] ?? null;

  const focusProject = (project: Project) => {
    setFocusedId(project.id);
    setActiveTab("overview");
  };

  return (
    <div className="proj">
      <header className="proj-top">
        <div className="proj-top-left">
          <h1 className="proj-title">Projects</h1>
          <div className="proj-title-meta">
            <span className="proj-badge proj-badge-accent">
              {projects.length} Active Repositories
            </span>
            <span className="proj-badge">
              DevHub: {githubConnections.length > 0 ? "Connected" : "Disconnected"}
            </span>
          </div>
          <p className="proj-subtitle">
            Manage repositories, target environments, connected tools, and
            automated pipelines.
          </p>
        </div>
        <div className="proj-top-actions">
          {/* <button
            type="button"
            className="proj-btn proj-btn-ghost"
            onClick={() => {
              const reduce = window.matchMedia(
                "(prefers-reduced-motion: reduce)",
              ).matches;
              reload();
              if (document.querySelector(".proj-telemetry")) {
                document
                  .querySelector(".proj-telemetry")
                  ?.scrollIntoView({
                    behavior: reduce ? "auto" : "smooth",
                    block: "start",
                  });
              }
            }}
          >
            <span className="proj-ico">⟳</span>
            <span>Sync DevHub</span>
          </button>
          <button
            type="button"
            className="proj-btn proj-btn-ghost"
            onClick={() => setError(null)}
          >
            <span className="proj-ico">⇩</span>
            <span>Export Specs</span>
          </button> */}
          <button
            type="button"
            className="proj-btn proj-btn-primary"
            onClick={() => setIsModalOpen(true)}
          >
            <span className="proj-ico">+</span>
            <span>New Project</span>
          </button>
        </div>
      </header>

      {error && (
        <div className="banner banner-err proj-banner" role="alert">
          {error}
        </div>
      )}

      {/* Operational Telemetry Bar */}
      <div className="proj-telemetry">
        <div className="proj-stat">
          <div className="proj-stat-body">
            <span className="proj-stat-label">Scratch Orgs Pool</span>
            <div className="proj-stat-main">
              <span className="proj-stat-value">{sfPool}</span>
              <span className="proj-stat-note">{sfPoolLabel}</span>
            </div>
          </div>
          <div className="proj-stat-icon proj-icon-blue">
            <span className="proj-ico">☁</span>
          </div>
        </div>
        <div className="proj-stat">
          <div className="proj-stat-body">
            <span className="proj-stat-label">Jira Sync Health</span>
            <div className="proj-stat-main">
              <span className="proj-stat-value">{jiraHealth}%</span>
              <span className="proj-stat-note">
                {jiraAuthorized}/{connections.length || 0} authorized
              </span>
            </div>
          </div>
          <div className="proj-stat-icon proj-icon-tertiary">
            <span className="proj-ico">✓</span>
          </div>
        </div>
        <div className="proj-stat">
          <div className="proj-stat-body">
            <span className="proj-stat-label">Agent Codex Tokens</span>
            <div className="proj-stat-main">
              <span className="proj-stat-value">
                {codexConnected ? "Connected" : "Disconnected"}
              </span>
              <span className="proj-stat-note">Reasoning engine</span>
            </div>
          </div>
          <div className="proj-stat-icon proj-icon-violet">
            <span className="proj-ico">◉</span>
          </div>
        </div>
        <div className="proj-stat">
          <div className="proj-stat-body">
            <span className="proj-stat-label">Autonomous Pipelines</span>
            <div className="proj-stat-main">
              <span className="proj-stat-value">{passRate}%</span>
              <span className="proj-stat-note">{totalRunsCount} runs total</span>
            </div>
          </div>
          <div className="proj-stat-icon proj-icon-cyan">
            <span className="proj-ico">▤</span>
          </div>
        </div>
      </div>

      {/* Search & Filter Controls */}
      <div className="proj-controls">
        <div className="proj-search-row">
          <div className="proj-search">
            <span className="proj-search-ico">⌕</span>
            <input
              type="text"
              className="proj-search-input"
              placeholder="Search projects by name, repo, or branch..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="proj-search-clear"
                onClick={() => setSearchQuery("")}
              >
                ×
              </button>
            )}
          </div>

          <div className="proj-pill-group">
            <button
              type="button"
              className={`proj-pill ${sandboxFilter === "all" ? "active" : ""}`}
              onClick={() => setSandboxFilter("all")}
            >
              All Sandboxes
            </button>
            <button
              type="button"
              className={`proj-pill ${sandboxFilter === "salesforce" ? "active" : ""}`}
              onClick={() => setSandboxFilter("salesforce")}
            >
              Salesforce (SFDX)
            </button>
            <button
              type="button"
              className={`proj-pill ${sandboxFilter === "default" ? "active" : ""}`}
              onClick={() => setSandboxFilter("default")}
            >
              Default (Docker)
            </button>
          </div>
        </div>

        <div className="proj-filter-row">
          <div className="proj-tabs">
            <button
              type="button"
              className={`proj-tab ${activeFilter === "all" ? "active" : ""}`}
              onClick={() => setActiveFilter("all")}
            >
              All
            </button>
            <button
              type="button"
              className={`proj-tab ${activeFilter === "active" ? "active" : ""}`}
              onClick={() => setActiveFilter("active")}
            >
              Active
            </button>
            <button
              type="button"
              className={`proj-tab ${activeFilter === "blocked" ? "active" : ""}`}
              onClick={() => setActiveFilter("blocked")}
            >
              Blocked
            </button>
          </div>

          <div className="proj-selects">
            <select
              className="proj-select"
              value={jiraFilter}
              onChange={(e) => setJiraFilter(e.target.value)}
            >
              <option value="all">Jira: All Connections</option>
              {connections.map((c) => (
                <option key={c.name} value={c.name}>
                  Jira: {c.name} ({c.project_key})
                </option>
              ))}
              <option value="none">Jira: Unlinked</option>
            </select>
            <select
              className="proj-select"
              value={readinessFilter}
              onChange={(e) => setReadinessFilter(e.target.value)}
            >
              <option value="all">Readiness: All States</option>
              <option value="ready">Readiness: Fully Ready</option>
              <option value="warnings">Readiness: Warnings / Missing Skills</option>
            </select>
          </div>
        </div>
      </div>

      {/* High Density Project Table */}
      <div className="proj-table-card">
        <div className="proj-table-scroll">
          {loading && projects.length === 0 ? (
            <div className="proj-table-empty">
              <p>Loading projects & pipeline status…</p>
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="proj-table-empty">
              <h4>
                {projects.length === 0
                  ? "No projects configured yet"
                  : "No projects match your filters"}
              </h4>
              <p>
                {projects.length === 0
                  ? "Create your first project to link a repository with Jira stories and launch autonomous pipelines."
                  : "Try adjusting your search query or filters above."}
              </p>
              {projects.length === 0 && (
                <button
                  type="button"
                  className="proj-btn proj-btn-primary"
                  onClick={() => setIsModalOpen(true)}
                >
                  + CREATE NEW PROJECT
                </button>
              )}
            </div>
          ) : (
            <table className="proj-table">
              <thead>
                <tr>
                  <th className="proj-th">Project &amp; Details</th>
                  <th className="proj-th">Repository</th>
                  <th className="proj-th">Ticket Key</th>
                  <th className="proj-th">Sandbox &amp; Org</th>
                  <th className="proj-th">Readiness Health</th>
                  <th className="proj-th">Status</th>
                  <th className="proj-th">Latest Run</th>
                  <th className="proj-th proj-th-right">Actions</th>
                </tr>
              </thead>
              <tbody className="proj-tbody">
                {filteredProjects.map((project) => {
                  const monogram = getMonogram(project.name);
                  const blocked = isBlocked(project);
                  const latestRun = latestRunFor(project.id);
                  return (
                    <tr
                      key={project.id}
                      className={`proj-row ${focusedId === project.id ? "row-focus" : ""} ${blocked ? "row-warn" : ""}`}
                      onClick={() => focusProject(project)}
                    >
                      <td className="proj-cell">
                        <div className="proj-proj-id">
                          <span
                            className={`proj-monogram ${blocked ? "monogram-warn" : ""}`}
                          >
                            {monogram}
                          </span>
                          <div className="proj-proj-id-body">
                            <div className="proj-proj-name-row">
                              <Link
                                to={`/projects/${project.id}`}
                                className="proj-proj-name"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {project.name}
                              </Link>
                              {project.sandbox_kind === "salesforce" && (
                                <span className="proj-mini-dot" title="Salesforce org linked" />
                              )}
                            </div>
                            <span className="proj-proj-desc">
                              {project.description?.slice(0, 60) ||
                                "No description"}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="proj-cell">
                        <div className="proj-repo">
                          <Link
                            to={`/projects/${project.id}`}
                            className="proj-repo-name"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <span className="proj-ico">⌥</span>
                            {repoName(project.repository_url)}
                          </Link>
                          {project.azure_git_connection_name && (
                            <span className="repo-badge-private" title={`Azure DevOps Git connection: ${project.azure_git_connection_name}`}>
                              Azure
                            </span>
                          )}
<span className="proj-repo-branch">
                            branch:{" "}
                            <strong>{project.default_branch || "main"}</strong>{" "}
                            ({project.azure_git_connection_name || project.github_connection_name || "no-account"})
                          </span>
                        </div>
                      </td>
                      <td className="proj-cell">
                        <div className="proj-jira-key">
                          {projectBadgeText(project) ? (
                            <span className="proj-key-badge">
                              {projectBadgeText(project)}
                            </span>
                          ) : (
                            <span className="proj-key-none">—</span>
                          )}
                          <span className="proj-jira-active">
                            {project.jira_connection_name ||
                            project.azure_connection_name ||
                            project.ticket_group_name
                              ? "Active"
                              : "Unlinked"}
                          </span>
                        </div>
                      </td>
                      <td className="proj-cell">
                        <div className="proj-sandbox">
                          <span
                            className={`proj-sandbox-tag ${project.sandbox_kind === "salesforce" ? "tag-sfdx" : "tag-docker"}`}
                          >
                            <span className="proj-ico">
                              {project.sandbox_kind === "salesforce" ? "☁" : "⌘"}
                            </span>
                            {project.sandbox_kind === "salesforce"
                              ? "Salesforce (SFDX)"
                              : "Default (Docker)"}
                          </span>
                          <span className="proj-org">
                            {project.salesforce_connection_name ||
                              (project.sandbox_kind === "salesforce"
                                ? "No org linked"
                                : "isolated container")}
                          </span>
                        </div>
                      </td>
                      <td className="proj-cell">
                        <div className="proj-readiness">
                          <span className="proj-chip chip-ok">
                            <span className="proj-chip-dot" />
                            GitHub: {project.has_git_token ? "Ready" : "Missing"}
                          </span>
                          {project.sandbox_kind === "salesforce" && (
                            <span className="proj-chip chip-ok">
                              <span className="proj-chip-dot" />
                              SFDX:{" "}
                              {project.salesforce_connection_name
                                ? "Ready"
                                : "Missing"}
                            </span>
                          )}
                          {project.readiness_issues.length > 0 ? (
                            <span className="proj-chip chip-err">
                              {project.readiness_issues[0].slice(0, 32)}
                            </span>
                          ) : (
                            <span className="proj-chip chip-violet">
                              Skills: {project.skills.length} attached
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="proj-cell">
                        <span
                          className={`proj-status ${blocked ? "status-config" : "status-active"}`}
                        >
                          <span className="proj-status-bullet" />
                          {blocked ? "Configuring" : "Active"}
                        </span>
                      </td>
                      <td className="proj-cell">
                        {latestRun ? (
                          <div className="proj-latest">
                            <span
                              className={`proj-latest-state ${latestRun.status === "running" || latestRun.status === "planning" ? "running" : latestRun.status === "completed" ? "done" : latestRun.status === "failed" || latestRun.status === "rejected" ? "failed" : ""}`}
                            >
                              {latestRun.ticket_key ?? "RUN"} ·{" "}
                              {latestRun.status}
                            </span>
                            <span className="proj-latest-meta">
                              {latestRun.current_stage || latestRun.status}
                            </span>
                          </div>
                        ) : (
                          <div className="proj-latest">
                            <span className="proj-latest-meta">No runs yet</span>
                            <span className="proj-latest-meta">Queue idle</span>
                          </div>
                        )}
                      </td>
                      <td className="proj-cell proj-cell-right">
                        <div className="proj-actions">
                          <Link
                            to={`/projects/${project.id}`}
                            className="proj-action-btn"
                            title="Configure Project"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <span className="proj-ico">⚙</span>
                          </Link>
                          <Link
                            to={`/projects/${project.id}/tickets`}
                            className="proj-action-btn primary"
                            title="Trigger Pipeline"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <span className="proj-ico">▶</span>
                          </Link>
                          <button
                            type="button"
                            className="proj-action-btn"
                            title="Expand View"
                            onClick={(e) => {
                              e.stopPropagation();
                              focusProject(project);
                            }}
                          >
                            <span className="proj-ico">›</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="proj-table-foot">
          <span>
            Showing {filteredProjects.length} active enterprise project
            configurations
          </span>
          <div className="proj-pagination">
            <button type="button" className="proj-page-btn" disabled>
              Previous
            </button>
            <span>Page 1 of 1</span>
            <button type="button" className="proj-page-btn" disabled>
              Next
            </button>
          </div>
        </div>
      </div>

      {/* Focused Project Detail Tabs Preview */}
      {focusedProject && (
        <div className="proj-detail-card">
          {/* <div className="proj-detail-banner">
            <div className="proj-detail-banner-left">
              <div className="proj-detail-banner-icon">
                <span className="proj-ico">✓</span>
              </div>
              <div>
                <div className="proj-detail-banner-title">
                  <span>Focused Project: {focusedProject.name}</span>
                  {focusedProject.sandbox_kind === "salesforce" && (
                    <span className="proj-badge proj-badge-solid">
                      {focusedProject.salesforce_connection_name || "SFDX"}
                    </span>
                  )}
                </div>
                <p className="proj-detail-banner-sub">
                  {focusedProject.readiness_issues.length === 0
                    ? "Ready for automated agent run execution. All validation checks passed."
                    : `${focusedProject.readiness_issues.length} readiness issue${focusedProject.readiness_issues.length > 1 ? "s" : ""} requiring attention before dispatch.`}
                </p>
              </div>
            </div>
            <div className="proj-detail-banner-right">
              <div className="proj-score">
                <span>Pipeline Readiness Score</span>
                <strong>
                  {focusedProject.readiness_issues.length === 0
                    ? "100% Operational"
                    : "Needs Setup"}
                </strong>
              </div>
              <Link
                to={`/projects/${focusedProject.id}/tickets`}
                className="proj-btn proj-btn-primary"
              >
                <span className="proj-ico">▶</span>
                <span>Pick Ticket</span>
              </Link>
            </div>
          </div> */}

          {/* <div className="proj-tabs-bar">
            <button
              type="button"
              className={`proj-tab-bar ${activeTab === "overview" ? "active" : ""}`}
              onClick={() => setActiveTab("overview")}
            >
              <span className="proj-ico">◧</span>
              Overview
            </button>
            <button
              type="button"
              className={`proj-tab-bar ${activeTab === "tickets" ? "active" : ""}`}
              onClick={() => setActiveTab("tickets")}
            >
              <span className="proj-ico">≡</span>
              Tickets
              <span className="proj-tab-count">
                {runsByProject[focusedProject.id]?.length ?? 0}
              </span>
            </button>
            <button
              type="button"
              className={`proj-tab-bar ${activeTab === "skills" ? "active" : ""}`}
              onClick={() => setActiveTab("skills")}
            >
              <span className="proj-ico">◷</span>
              Skills &amp; Memory
              <span className="proj-tab-count">
                {focusedProject.skills.length} Active
              </span>
            </button>
            <button
              type="button"
              className={`proj-tab-bar ${activeTab === "runs" ? "active" : ""}`}
              onClick={() => setActiveTab("runs")}
            >
              <span className="proj-ico">∿</span>
              Runs
            </button>
            <button
              type="button"
              className={`proj-tab-bar ${activeTab === "settings" ? "active" : ""}`}
              onClick={() => setActiveTab("settings")}
            >
              <span className="proj-ico">⚙</span>
              Settings
            </button>
          </div> */}

          {/* <div className="proj-detail-body">
            {activeTab === "overview" && (
              <div className="proj-detail-grid">
                <div className="proj-detail-main">
                  <div className="proj-detail-heading">
                    <div>
                      <h3>Active Pipeline Orchestration Topology</h3>
                      <span className="proj-detail-muted">
                        Live autonomous stages executed by Codex +{" "}
                        {focusedProject.sandbox_kind === "salesforce"
                          ? "SFDX runner"
                          : "Docker runner"}
                      </span>
                    </div>
                    <Link
                      to={`/projects/${focusedProject.id}`}
                      className="proj-ghost-link"
                    >
                      Open full project →
                    </Link>
                  </div>
                  <div className="proj-dag">
                    <div className="proj-dag-step">
                      <span className="proj-dag-num">01 · Ingest</span>
                      <strong>
                        {focusedProject.azure_connection_name && !focusedProject.jira_connection_name
                          ? "Azure Work Item Parse"
                          : focusedProject.ticket_group_name
                            ? "Jira Group Parse"
                            : "Jira Ticket Parse"}
                      </strong>
                      <span className="proj-dag-state done">
                        {focusedProject.jira_connection_name ||
                        focusedProject.azure_connection_name ||
                        focusedProject.ticket_group_name
                          ? "Ready"
                          : "Unlinked"}
                      </span>
                    </div>
                    <span className="proj-dag-arrow">→</span>
                    <div className="proj-dag-step">
                      <span className="proj-dag-num">02 · Synthesis</span>
                      <strong>Codex Synthesis</strong>
                      <span className="proj-dag-state done">
                        {focusedProject.skills.length} skills attached
                      </span>
                    </div>
                    <span className="proj-dag-arrow">→</span>
                    <div className="proj-dag-step active">
                      <span className="proj-dag-num">03 · Deploy &amp; Test</span>
                      <strong>
                        {focusedProject.sandbox_kind === "salesforce"
                          ? "SFDX Scratch Org Deploy"
                          : "Container Test Runner"}
                      </strong>
                      <span className="proj-dag-state active">
                        {rankProject(focusedProject)}% readiness
                      </span>
                    </div>
                    <span className="proj-dag-arrow">→</span>
                    <div className="proj-dag-step dim">
                      <span className="proj-dag-num">04 · Pull Request</span>
                      <strong>
                        GitHub PR &amp;{" "}
                        {projectUsesAzure(focusedProject)
                          ? "Azure Close"
                          : "Jira Close"}
                      </strong>
                      <span className="proj-dag-state">
                        {focusedProject.has_git_token ? "Ready" : "Blocked"}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="proj-detail-rail">
                  <h3 className="proj-rail-title">Configured Bindings</h3>
                  <div className="proj-binding">
                    <div className="proj-binding-head">
                      <span className="proj-ico proj-ico-blue">⛁</span>
                      <span>Git Integration</span>
                    </div>
                    <span className="proj-binding-badge">
                      {focusedProject.has_git_token ? "Protected" : "No Token"}
                    </span>
                  </div>
                  <div className="proj-binding">
                    <div className="proj-binding-head">
                      <span className="proj-ico proj-ico-blue">☁</span>
                      <span>Salesforce DevHub</span>
                    </div>
                    <span className="proj-binding-badge">
                      {focusedProject.salesforce_connection_name || "None"}
                    </span>
                  </div>
                  <div className="proj-binding">
                    <div className="proj-binding-head">
                      <span className="proj-ico proj-ico-violet">◷</span>
                      <span>Attached Skills ({focusedProject.skills.length})</span>
                    </div>
                    <div className="proj-binding-tags">
                      {focusedProject.skills.length > 0 ? (
                        focusedProject.skills.slice(0, 3).map((s) => (
                          <span key={s.slug} className="proj-skill-tag">
                            {s.name || s.slug}
                          </span>
                        ))
                      ) : (
                        <span className="proj-detail-muted">No skills attached</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "tickets" && (
              <div className="proj-detail-simple">
                <div className="proj-detail-heading">
                  <h3>Tickets &amp; Runs</h3>
                  <span className="proj-detail-muted">
                    {runsByProject[focusedProject.id]?.length ?? 0} pipeline runs
                    recorded for this project
                  </span>
                </div>
                <p className="proj-detail-muted">
                  Select a ticket to dispatch a run, or review the project{" "}
                  <Link to={`/projects/${focusedProject.id}/tickets`}>
                    ticket picker
                  </Link>{" "}
                  for the full queue.
                </p>
              </div>
            )}

            {activeTab === "skills" && (
              <div className="proj-detail-simple">
                <div className="proj-detail-heading">
                  <h3>Skills &amp; Memory</h3>
                  <span className="proj-detail-muted">
                    {focusedProject.skills.length} skills attached ·{" "}
                    {focusedProject.has_memory ? "Memory persisted" : "No memory yet"}
                  </span>
                </div>
                <div className="proj-binding-tags">
                  {focusedProject.skills.map((s) => (
                    <span key={s.slug} className="proj-skill-tag">
                      {s.name || s.slug} · {s.phase}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {activeTab === "runs" && (
              <div className="proj-detail-simple">
                <div className="proj-detail-heading">
                  <h3>Runs</h3>
                  <span className="proj-detail-muted">
                    {runsByProject[focusedProject.id]?.length ?? 0} total runs
                  </span>
                </div>
                <Link className="proj-ghost-link" to="/runs">
                  View audit trail →
                </Link>
              </div>
            )}

            {activeTab === "settings" && (
              <div className="proj-detail-simple">
                <div className="proj-detail-heading">
                  <h3>Settings</h3>
                  <span className="proj-detail-muted">
                    Branch: {focusedProject.default_branch || "main"} · Sandbox:{" "}
                    {focusedProject.sandbox_kind}
                  </span>
                </div>
                <div className="proj-detail-actions">
                  <Link
                    to={`/projects/${focusedProject.id}`}
                    className="proj-btn proj-btn-ghost"
                  >
                    Configure Project
                  </Link>
                  <button
                    type="button"
                    className="proj-btn proj-btn-ghost proj-btn-danger"
                    onClick={() => setDeletingProject(focusedProject)}
                  >
                    Delete Project
                  </button>
                </div>
              </div>
            )}
          </div> */}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deletingProject && (
        <div
          className="modal-backdrop"
          onClick={() => !deleting && setDeletingProject(null)}
        >
          <div
            className="modal-container delete-confirm-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div className="modal-title-group">
                <div className="modal-icon-badge icon-danger">
                  <span>🗑</span>
                </div>
                <div>
                  <h3>DELETE PROJECT</h3>
                  <p className="modal-subtitle">
                    Are you sure you want to delete{" "}
                    <strong>{deletingProject.name}</strong>?
                  </p>
                </div>
              </div>
            </div>

            <p className="delete-warning-text">
              This action permanently removes the project configuration, all
              agent runs, plan artifacts, logs, installed skills, and learned
              memory.
            </p>

            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={deleting}
                onClick={() => setDeletingProject(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger-glow"
                disabled={deleting}
                onClick={confirmDelete}
              >
                {deleting ? "Deleting…" : "Yes, Delete Project"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Project Modal */}
      <NewProjectModal
        isOpen={isModalOpen}
        onClose={handleModalClose}
        onCreated={handleProjectCreated}
        connections={connections}
        azureConnections={azureConnections}
        sfConnections={sfConnections}
        githubConnections={githubConnections}
      />
    </div>
  );
}
