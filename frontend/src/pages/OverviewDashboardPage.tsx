import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import {
  ACTIVE_RUN_STATUSES,
  type PipelineRun,
  type PipelineRunStatus,
  type Project,
} from "../api/types";
import { formatDuration, formatLocalDateTime } from "../format";

const POLL_MS = 5000;
const COST_BUDGET = 500;

const STATUS_THEME: Record<
  string,
  { label: string; dot: string; ticketCls: string }
> = {
  planning: { label: "Planning", dot: "dot-cyan", ticketCls: "ticket-cyan" },
  replanning: {
    label: "Replanning",
    dot: "dot-cyan",
    ticketCls: "ticket-cyan",
  },
  awaiting_approval: {
    label: "Awaiting Approval",
    dot: "dot-warn",
    ticketCls: "ticket-amber",
  },
  approved: { label: "Approved", dot: "dot-warn", ticketCls: "ticket-amber" },
  running: {
    label: "Running",
    dot: "dot-magenta",
    ticketCls: "ticket-magenta",
  },
  paused: { label: "Paused", dot: "dot-warn", ticketCls: "ticket-amber" },
  completed: { label: "Completed", dot: "dot-ok", ticketCls: "ticket-green" },
  failed: { label: "Failed", dot: "dot-err", ticketCls: "ticket-red" },
  rejected: { label: "Rejected", dot: "dot-err", ticketCls: "ticket-red" },
  cancelled: { label: "Cancelled", dot: "dot-muted", ticketCls: "ticket-gray" },
};

const STATUS_ORDER: PipelineRunStatus[] = [
  "running",
  "planning",
  "replanning",
  "awaiting_approval",
  "paused",
  "approved",
  "completed",
  "failed",
  "rejected",
  "cancelled",
];

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`;
  const t = new Date(normalized).getTime();
  if (Number.isNaN(t)) return "—";
  const seconds = Math.max(1, Math.floor((Date.now() - t) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function sumCost(runs: PipelineRun[]): number {
  return runs.reduce((sum, r) => sum + (Number(r.total_cost) || 0), 0);
}

function topStages(runs: PipelineRun[], limit: number): string {
  const counts = new Map<string, number>();
  for (const run of runs) {
    const stage = run.current_stage ?? run.status;
    counts.set(stage, (counts.get(stage) ?? 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  return sorted
    .slice(0, limit)
    .map(([stage, count]) => `${count} in ${stage}`)
    .join(", ");
}

export function OverviewDashboardPage() {
  const fetchRuns = usePolling(() => api.listRuns(), POLL_MS, true, []);
  const fetchProjects = usePolling(() => api.listProjects(), POLL_MS, true, []);
  const fetchSkills = usePolling(() => api.listSkills(), 15000, true, []);
  const fetchCodex = usePolling(() => api.getCodexStatus(), 15000, true, []);
  const fetchJira = usePolling(() => api.listConnections(), 15000, true, []);
  const fetchSf = usePolling(
    () => api.listSalesforceConnections(),
    15000,
    true,
    [],
  );
  const fetchGitHub = usePolling(() => api.getGitHubStatus(), 15000, true, []);

  const runs = fetchRuns.data ?? [];
  const projects = fetchProjects.data ?? [];
  const skills = fetchSkills.data ?? [];
  const codexConnected = fetchCodex.data?.connected ?? false;
  const jiraReady = (fetchJira.data ?? []).some(
    (c) => c.health_status === "active" || Boolean(c.has_oauth_token || c.has_api_token),
  );
  const sfConnections = fetchSf.data ?? [];
  const githubConnected = fetchGitHub.data?.connected ?? false;

  const [statusFilter, setStatusFilter] = useState<"" | PipelineRunStatus>("");
  const [ticketQuery, setTicketQuery] = useState("");

  const nameById = Object.fromEntries(
    projects.map((p: Project) => [p.id, p.name]),
  );

  const liveRuns = runs.filter((r) => ACTIVE_RUN_STATUSES.includes(r.status));
  const awaitingApproval = runs.filter((r) => r.status === "awaiting_approval");

  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const completed7d = runs.filter((r) => {
    if (r.status !== "completed") return false;
    const t = new Date(r.completed_at ?? r.created_at).getTime();
    return !Number.isNaN(t) && t >= weekAgo;
  });

  const failedRuns = runs.filter((r) => r.status === "failed");

  const totalCost = sumCost(runs);
  const costPercent = Math.min(100, (totalCost / COST_BUDGET) * 100);
  const avgRunCost = runs.length > 0 ? totalCost / runs.length : 0;

  const sortedRuns = [...runs].sort((a, b) => {
    const at = new Date(a.started_at ?? a.created_at).getTime();
    const bt = new Date(b.started_at ?? b.created_at).getTime();
    if (Number.isNaN(at)) return 1;
    if (Number.isNaN(bt)) return -1;
    return bt - at;
  });

  const q = ticketQuery.trim().toLowerCase();
  const filteredRuns = sortedRuns.filter((run) => {
    if (statusFilter && run.status !== statusFilter) return false;
    if (!q) return true;
    return (
      (run.ticket_key ?? "").toLowerCase().includes(q) ||
      (nameById[run.project_id] ?? "").toLowerCase().includes(q)
    );
  });
  const recentRuns = filteredRuns.slice(0, 6);

  const memoryCount = projects.filter((p: Project) => p.has_memory).length;
  const activeSkills = skills.filter(
    (s) => s.status !== "rejected" && s.status !== "pending",
  ).length;

  const activity = sortedRuns.slice(0, 6);

  const error = fetchRuns.error ?? fetchProjects.error;

  return (
    <div className="dash">
      {error && <div className="banner banner-err">{error}</div>}

      {/* Header */}
      <div className="dash-header">
        <div className="dash-title-group">
          <div className="dash-title-row">
            <h2>Maverick Agentic Pipeline</h2>

          </div>
          <p className="dash-sub">
            From Jira to Salesforce, powered by Appstrail Technology
          </p>
        </div>

        {/* <div className="dash-actions">
          <button type="button" className="btn btn-secondary dash-btn">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            Export Audit Log
          </button>
          <button
            type="button"
            className="btn btn-secondary dash-btn"
            onClick={handleSync}
          >
            <svg
              className={syncSpinning ? "spin" : ""}
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
            Sync Jira &amp; GitHub
          </button>
          <button type="button" className="btn btn-cyber-primary dash-btn">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="6,3 20,12 6,21" />
            </svg>
            Start Pipeline Run
          </button>
        </div> */}
      </div>

      {/* Summary Metrics */}
      <div className="dash-metrics">
        {/* Active Runs */}
        <div className="dash-metric">
          <div className="dash-metric-top">
            <span className="dash-metric-head">Active Runs</span>
            {/* <span className="dash-tag tag-magenta">
              <span className="pulse-dot" />
              Live
            </span> */}
          </div>
          <div className="dash-metric-main">
            <span className="dash-metric-value">{liveRuns.length}</span>
            <span className="dash-metric-note">in-flight</span>
          </div>
          <div className="dash-metric-foot truncate">
            {liveRuns.length > 0
              ? topStages(liveRuns, 2)
              : "No runs currently executing"}
          </div>
        </div>

        {/* Awaiting Approval */}
        <div className="dash-metric">
          <div className="dash-metric-top">
            <span className="dash-metric-head">Awaiting Approval</span>
            {/* <span className="dash-tag tag-amber">Manual Gate</span> */}
          </div>
          <div className="dash-metric-main">
            <span className="dash-metric-value value-amber">
              {awaitingApproval.length}
            </span>
            <span className="dash-metric-note note-amber">Review needed</span>
          </div>
          <div className="dash-metric-foot truncate">
            {awaitingApproval.length > 0
              ? awaitingApproval.map((r) => r.ticket_key).join(", ") +
                " plan inspection"
              : "Nothing pending review"}
          </div>
        </div>

        {/* Completed 7d */}
        <div className="dash-metric">
          <div className="dash-metric-top">
            <span className="dash-metric-head">Completed (7d)</span>
            {/* <span className="dash-tag tag-green">
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
              </svg>
              {runs.length > 0
                ? `+${Math.round((completed7d.length / Math.max(1, runs.length)) * 100)}%`
                : "0%"}
            </span> */}
          </div>
          <div className="dash-metric-main">
            <span className="dash-metric-value">{completed7d.length}</span>
            <span className="dash-metric-note">passed PRs</span>
          </div>
          <div className="dash-metric-foot truncate">
            {runs.length > 0
              ? `${Math.round((completed7d.length / Math.max(1, runs.length)) * 100)}% autonomous acceptance`
              : "No completed runs this week"}
          </div>
        </div>

        {/* Failed Runs */}
        <div className="dash-metric">
          <div className="dash-metric-top">
            <span className="dash-metric-head">Failed Runs</span>
            {/* {failedRuns.length > 0 && (
              <span className="dash-tag tag-red">
                Alert ({failedRuns.length})
              </span>
            )} */}
          </div>
          <div className="dash-metric-main">
            <span className="dash-metric-value value-red">
              {failedRuns.length}
            </span>
            <span className="dash-metric-note note-red">Needs attention</span>
          </div>
          <div className="dash-metric-foot truncate text-red">
            {failedRuns.length > 0
              ? (() => {
                  const failedRun = failedRuns[failedRuns.length - 1];
                  return `Ticket ${failedRun.ticket_key ?? "Unknown"}`;
                })()
              : "All runs passing"}
          </div>
        </div>

        {/* Current Cost */}
        <div className="dash-metric">
          <div className="dash-metric-top">
            <span className="dash-metric-head">Current Cost</span>
            {/* <span className="dash-tag tag-muted">Budget ${COST_BUDGET}/mo</span> */}
          </div>
          <div className="dash-metric-main">
            <span className="dash-metric-value">${totalCost.toFixed(2)}</span>
            <span className="dash-metric-note note-cyan">
              {Math.round(costPercent)}%
            </span>
          </div>
          <div className="dash-metric-foot">
            <div className="dash-fill-track">
              <div
                className="dash-fill-bar"
                style={{ width: `${costPercent}%` }}
              />
            </div>
            <div className="dash-cost-row">
              <span>Avg run: ${avgRunCost.toFixed(2)}</span>
              <span className="mono">budget</span>
            </div>
          </div>
        </div>
      </div>

      {/* Recent Pipeline Runs */}
      <div className="dash-card dash-card-table">
        <div className="dash-card-head">
          <div className="dash-card-title">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="dash-card-icon"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="3" y1="9" x2="21" y2="9" />
              <line x1="3" y1="15" x2="21" y2="15" />
              <line x1="9" y1="9" x2="9" y2="21" />
            </svg>
            <h3>Recent Pipeline Runs</h3>
            <span className="dash-count-badge">{recentRuns.length} latest</span>
          </div>

          <div className="dash-toolbar">
            <div className="dash-search">
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                className="dash-search-icon"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                type="text"
                className="dash-search-input"
                placeholder="Filter by ticket or project..."
                value={ticketQuery}
                onChange={(e) => setTicketQuery(e.target.value)}
              />
            </div>
            <select
              className="dash-select"
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as "" | PipelineRunStatus)
              }
            >
              <option value="">All statuses</option>
              {STATUS_ORDER.map((s) => (
                <option key={s} value={s}>
                  {STATUS_THEME[s]?.label ?? s}
                </option>
              ))}
            </select>
          </div>
        </div>

        {recentRuns.length === 0 ? (
          <div className="dash-empty">
            <p>No pipeline runs match those filters.</p>
            {runs.length === 0 && (
              <Link className="btn" to="/">
                Go to projects to start a run
              </Link>
            )}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table dash-table">
              <thead>
                <tr>
                  <th>Ticket &amp; Specification</th>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Current Stage</th>
                  <th>Duration</th>
                  <th>Cost</th>
                  <th>Pull Request</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map((run) => {
                  const theme = STATUS_THEME[run.status] ?? {
                    label: run.status,
                    dot: "dot-muted",
                    ticketCls: "ticket-gray",
                  };
                  return (
                    <tr key={run.id} className="dash-row">
                      <td>
                        <div className="dash-ticket-cell">
                          <span className={`dash-ticket-dot ${theme.dot}`} />
                          <span
                            className={`dash-ticket-key ${theme.ticketCls}`}
                          >
                            {run.ticket_key ?? "—"}
                          </span>
                          <span className="dash-ticket-title truncate">
                            {nameById[run.project_id]?.split(/[-\s]/)[0] ?? ""}
                          </span>
                        </div>
                      </td>
                      <td className="dash-cell-muted">
                        {nameById[run.project_id] ?? run.project_id.slice(0, 8)}
                      </td>
                      <td>
                        <span className="dash-status-chip">
                          <span className={theme.dot} />
                          {theme.label}
                        </span>
                      </td>
                      <td className="dash-stage-cell">
                        <span className={`dash-stage-text stage-${run.status}`}>
                          {run.current_stage ?? run.status}
                        </span>
                      </td>
                      <td className="mono">
                        {formatDuration(run.started_at, run.completed_at)}
                      </td>
                      <td className="mono">
                        ${Number(run.total_cost || 0).toFixed(2)}
                      </td>
                      <td>
                        {run.pr_url ? (
                          <a
                            className="dash-pr-link"
                            href={run.pr_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            PR link
                          </a>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="dash-cell-muted whitespace-nowrap">
                        {formatLocalDateTime(run.started_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="dash-card-foot">
          <span>
            Showing {recentRuns.length} of {runs.length} total pipeline runs
          </span>
          <Link className="dash-foot-link" to="/runs">
            View all runs →
          </Link>
        </div>
      </div>

      {/* Lower Grid: Activity Feed + Health */}
      <div className="dash-lower">
        {/* Activity Feed */}
        <div className="dash-card dash-activity-card">
          <div className="dash-card-head-inline">
            <div className="dash-card-title">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="dash-card-icon"
              >
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <h3>Live Pipeline Activity Feed</h3>
            </div>
            <span className="dash-stream-chip">
              <span className="pulse-dot" />
              Streaming Realtime
            </span>
          </div>

          <div className="dash-activity">
            {activity.length === 0 ? (
              <p className="muted dash-empty">
                No run activity yet. Start a pipeline run to see live events
                here.
              </p>
            ) : (
              activity.map((run) => (
                <div key={run.id} className="dash-activity-item">
                  <div className={`dash-activity-icon icon-${run.status}`}>
                    {run.status === "completed" ? (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                      >
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : run.status === "failed" || run.status === "rejected" ? (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                      >
                        <circle cx="12" cy="12" r="10" />
                        <line x1="12" y1="8" x2="12" y2="12" />
                        <line x1="12" y1="16" x2="12.01" y2="16" />
                      </svg>
                    ) : (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="currentColor"
                      >
                        <polygon points="6,3 20,12 6,21" />
                      </svg>
                    )}
                  </div>
                  <div className="dash-activity-body">
                    <div className="dash-activity-textrow">
                      <p className="dash-activity-text truncate">
                        <span className="mono-bold">
                          {run.ticket_key ?? "Run"}
                        </span>{" "}
                        {run.status === "completed"
                          ? "completed after passing validation"
                          : run.status === "failed"
                            ? "failed during execution"
                            : run.status === "rejected"
                              ? "was rejected during review"
                              : run.status === "awaiting_approval"
                                ? "plan awaiting human review"
                                : run.status === "running"
                                  ? "is executing now"
                                  : `transitioned to ${run.status}`}
                      </p>
                      <span className="dash-activity-time">
                        {timeAgo(run.started_at ?? run.created_at)}
                      </span>
                    </div>
                    <span className="dash-activity-sub truncate">
                      {run.current_stage ??
                        nameById[run.project_id] ??
                        run.project_id.slice(0, 8)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>

          <Link className="dash-foot-link" to="/runs">
            View complete audit trail →
          </Link>
        </div>

        {/* System & Integration Health */}
        <div className="dash-card dash-health-card">
          <div className="dash-card-head-inline">
            <div className="dash-card-title">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="dash-card-icon"
              >
                <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
              </svg>
              <h3>System &amp; Integration Health</h3>
            </div>
            {/* <span className="dash-tag tag-green">Operational</span> */}
          </div>

          <div className="dash-health">
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M4 17l6-6-6-6" />
                  <path d="M12 19h8" />
                </svg>
              }
              name="GitHub Integration"
              detail={
                githubConnected
                  ? "Repo write & PR workflows active"
                  : "OAuth disconnected"
              }
              status={githubConnected ? "ok" : "err"}
              statusLabel={githubConnected ? "Connected" : "Disconnected"}
            />
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="3" width="18" height="9" rx="2" />
                  <rect x="3" y="15" width="18" height="9" rx="2" />
                </svg>
              }
              name="Jira Service Cloud"
              detail={
                jiraReady ? "Jira sync configured" : "No Jira credentials set"
              }
              status={jiraReady ? "ok" : "warn"}
              statusLabel={jiraReady ? "Connected" : "Needs auth"}
            />
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
                </svg>
              }
              name="Codex AI Engine"
              detail={
                codexConnected ? "Codex API connected" : "Codex login required"
              }
              status={codexConnected ? "ok" : "err"}
              statusLabel={codexConnected ? "Active" : "Disconnected"}
            />
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973" />
                  <path d="M12 12l-3 4h2v4l6-5h-2z" opacity="0" />
                </svg>
              }
              name="Salesforce Sandbox"
              detail={
                sfConnections.length > 0
                  ? `${sfConnections.length} org${sfConnections.length > 1 ? "s" : ""} authorized`
                  : "No sandbox org connected"
              }
              status={sfConnections.length > 0 ? "ok" : "warn"}
              statusLabel={sfConnections.length > 0 ? "Connected" : "Unlinked"}
            />
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
              }
              name="Reusable Skills"
              detail={`${activeSkills} active · ${skills.length - activeSkills} pending`}
              status="ok"
              statusLabel={`${skills.length} Registered`}
            />
            <HealthRow
              icon={
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <ellipse cx="12" cy="5" rx="9" ry="3" />
                  <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
                  <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                </svg>
              }
              name="Project Memory"
              detail={`${memoryCount} project${memoryCount === 1 ? "" : "s"} with learned memory`}
              status="ok"
              statusLabel="Persisted"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function HealthRow({
  icon,
  name,
  detail,
  status,
  statusLabel,
}: {
  icon: ReactNode;
  name: string;
  detail: string;
  status: "ok" | "warn" | "err";
  statusLabel: string;
}) {
  return (
    <div className="dash-health-row">
      <div className="dash-health-left">
        <div className={`dash-health-icon icon-${status}`}>{icon}</div>
        <div className="dash-health-text">
          <span className="dash-health-name">{name}</span>
          <span className="dash-health-detail">{detail}</span>
        </div>
      </div>
      <span className={`dash-health-tag tag-${status}`}>
        <span
          className={`status-dot dot-${status === "ok" ? "ok" : status === "warn" ? "warn" : "err"}`}
        />
        {statusLabel}
      </span>
    </div>
  );
}
