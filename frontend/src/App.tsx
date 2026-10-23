import { useCallback } from "react";
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import appstrailLogo from "./assets/appstrail_logo.png";
import { api } from "./api/client";
import { LIVE_RUN_STATUSES } from "./api/types";
import { usePolling } from "./hooks/usePolling";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { ManualIngestPage } from "./pages/ManualIngestPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OverviewDashboardPage } from "./pages/OverviewDashboardPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsListPage } from "./pages/RunsListPage";
import { SkillsPage } from "./pages/SkillsPage";
import { TicketPickerPage } from "./pages/TicketPickerPage";

function padTwo(num: number): string {
  return num < 10 ? `0${num}` : `${num}`;
}

function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const fetchProjects = useCallback(() => api.listProjects(), []);
  const fetchRuns = useCallback(() => api.listRuns(), []);
  const fetchSkills = useCallback(() => api.listSkills(), []);
  const fetchCodex = useCallback(() => api.getCodexStatus(), []);
  const fetchJira = useCallback(() => api.listConnections(), []);
  const fetchSf = useCallback(() => api.listSalesforceConnections(), []);
  const fetchGitHub = useCallback(() => api.getGitHubStatus(), []);

  const { data: projects } = usePolling(fetchProjects, 10000, true, []);
  const { data: runs } = usePolling(fetchRuns, 5000, true, []);
  const { data: skills } = usePolling(fetchSkills, 15000, true, []);
  const { data: codex } = usePolling(fetchCodex, 15000, true, []);
  const { data: jira } = usePolling(fetchJira, 15000, true, []);
  const { data: sf } = usePolling(fetchSf, 15000, true, []);
  const { data: github } = usePolling(fetchGitHub, 15000, true, []);

  const projectCount = projects?.length ?? 0;
  const runCount = runs?.length ?? 0;
  const skillCount = skills?.length ?? 0;
  const totalConnections =
    (jira?.length ?? 0) +
    (sf?.length ?? 0) +
    (github?.connections?.length ?? 0);
  const liveCount =
    runs?.filter((r) => LIVE_RUN_STATUSES.includes(r.status)).length ?? 0;

  const jiraReady =
    jira && jira.length > 0
      ? jira.some((c) => c.has_oauth_token || c.has_api_token)
      : false;

  const isProjectsRoute =
    location.pathname === "/" || location.pathname.startsWith("/projects/");
  const callbackParams = new URLSearchParams(location.search);
  const isGitHubOAuthCallback =
    callbackParams.has("github_connected") || callbackParams.has("github_error");

  const handleNewProjectClick = () => {
    if (location.pathname === "/") {
      window.location.hash = "#new-project";
    } else {
      navigate("/#new-project");
    }
  };

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="app">
        {/* Futuristic Cyber Sidenav */}
        <aside className="sidenav">
          {/* Brand Logo & Name */}
          <div className="brand-box">
            <div className="brand-logo-badge">
              <img
                className="brand-logo-img"
                src={appstrailLogo}
                alt="Appstrail logo"
              />
            </div>
            <div className="brand-titles">
              <span className="brand-name-main">MAVERICK AGENTIC</span>
              <span className="brand-name-sub">PIPELINE</span>
            </div>
          </div>

          {/* "+ NEW PROJECT" Neon Button */}
          <button
            type="button"
            className="btn-sidenav-new-project"
            onClick={handleNewProjectClick}
          >
            <span className="plus-icon">+</span> NEW PROJECT
          </button>

          {/* Navigation Section */}
          <div className="nav-group-title">NAVIGATE</div>

          <nav className="nav-links-list">
            {/* 0. Dashboard */}
            <NavLink
              to="/dashboard"
              className={({ isActive }) =>
                `sidenav-link ${isActive ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="3" width="7" height="9" rx="1" />
                  <rect x="14" y="3" width="7" height="5" rx="1" />
                  <rect x="14" y="12" width="7" height="9" rx="1" />
                  <rect x="3" y="16" width="7" height="5" rx="1" />
                </svg>
                <span>Dashboard</span>
              </div>
            </NavLink>

            {/* 1. Projects */}
            <NavLink
              to="/"
              className={() =>
                `sidenav-link ${isProjectsRoute ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <span>Projects</span>
              </div>
              <span className="nav-count-badge">{padTwo(projectCount)}</span>
            </NavLink>

            {/* 2. Runs */}
            <NavLink
              to="/runs"
              className={({ isActive }) =>
                `sidenav-link ${isActive ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                </svg>
                <span>Runs</span>
              </div>
              <span className="nav-count-badge">{padTwo(runCount)}</span>
            </NavLink>

            {/* 3. Connections */}
            <NavLink
              to="/connections"
              className={({ isActive }) =>
                `sidenav-link ${isActive ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M12 2v6m0 8v6M8 8H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h4M16 8h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4" />
                </svg>
                <span>Connections</span>
              </div>
              <span className="nav-count-badge">
                {padTwo(totalConnections)}
              </span>
            </NavLink>

            {/* 4. Skills */}
            <NavLink
              to="/skills"
              className={({ isActive }) =>
                `sidenav-link ${isActive ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
                </svg>
                <span>Skills</span>
              </div>
              <span className="nav-count-badge">{padTwo(skillCount)}</span>
            </NavLink>

            {/* 5. Manual Ingest */}
            <NavLink
              to="/manual-ingest"
              className={({ isActive }) =>
                `sidenav-link ${isActive ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="9" y1="13" x2="15" y2="13" />
                  <line x1="9" y1="17" x2="13" y2="17" />
                </svg>
                <span>Manual Ingest</span>
              </div>
            </NavLink>

            {/* 6. Live */}
            <NavLink
              to="/runs"
              className={({ isActive }) =>
                `sidenav-link ${isActive && liveCount > 0 ? "active" : ""}`
              }
            >
              <div className="nav-item-left">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <circle cx="12" cy="12" r="2" />
                  <path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14" />
                </svg>
                <span>Live</span>
              </div>
              <span className="nav-count-badge">{padTwo(liveCount)}</span>
            </NavLink>
          </nav>

          {/* Bottom Integration Health Panel */}
          <div className="sidenav-bottom-panel">
            <div className="ambient-grid-glow" />
            <div className="health-section-header">
              <span>INTEGRATION HEALTH</span>
              <span className="header-chevron">›</span>
            </div>

            <div className="health-status-rows">
              {/* GitHub */}
              <NavLink to="/connections#github" className="health-status-row">
                <span className="health-service-name">GitHub</span>
                <span className="health-status-indicator">
                  <span
                    className={`status-dot ${github?.connected ? "dot-ok" : "dot-err"}`}
                  />
                  <span
                    className={`status-text ${github?.connected ? "text-ok" : "text-err"}`}
                  >
                    {github?.connected ? "OK" : "DISC"}
                  </span>
                </span>
              </NavLink>

              {/* Codex */}
              <NavLink to="/connections#codex" className="health-status-row">
                <span className="health-service-name">Codex</span>
                <span className="health-status-indicator">
                  <span
                    className={`status-dot ${codex?.connected ? "dot-ok" : "dot-err"}`}
                  />
                  <span
                    className={`status-text ${codex?.connected ? "text-ok" : "text-err"}`}
                  >
                    {codex?.connected ? "OK" : "API"}
                  </span>
                </span>
              </NavLink>

              {/* Jira */}
              <NavLink to="/connections#jira" className="health-status-row">
                <span className="health-service-name">Jira</span>
                <span className="health-status-indicator">
                  <span
                    className={`status-dot ${jiraReady ? "dot-ok" : "dot-warn"}`}
                  />
                  <span
                    className={`status-text ${jiraReady ? "text-ok" : "text-warn"}`}
                  >
                    {jiraReady ? "OK" : "WARN"}
                  </span>
                </span>
              </NavLink>
            </div>
          </div>
        </aside>

        {/* Main Viewport */}
        <main id="main" className="content" tabIndex={-1}>
          <Routes>
            <Route path="/dashboard" element={<OverviewDashboardPage />} />
            <Route path="/" element={<ProjectsPage />} />
            <Route
              path="/projects"
              element={
                <Navigate
                  to={
                    isGitHubOAuthCallback
                      ? `/connections${location.search}`
                      : "/"
                  }
                  replace
                />
              }
            />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/manual-ingest" element={<ManualIngestPage />} />
            <Route
              path="/codex"
              element={<Navigate to="/connections#codex" replace />}
            />
            <Route
              path="/projects/:projectId/tickets"
              element={<TicketPickerPage />}
            />
            <Route
              path="/projects/:projectId"
              element={<ProjectDetailPage />}
            />
            <Route path="/runs" element={<RunsListPage />} />
            <Route path="/runs/:runId" element={<RunDetailPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </main>
      </div>
    </>
  );
}

export default App;
