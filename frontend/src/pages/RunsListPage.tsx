import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { formatDuration, formatLocalDateTime, formatUsd } from "../format";
import { usePolling } from "../hooks/usePolling";
import {
  LIVE_RUN_STATUSES,
  type PipelineRun,
  type PipelineRunStatus,
  type Project,
} from "../api/types";

const POLL_MS = 3000;

const STATUS_FILTERS: Array<{ value: "" | PipelineRunStatus; label: string }> =
  [
    { value: "", label: "All statuses" },
    { value: "planning", label: "Planning" },
    { value: "awaiting_approval", label: "Awaiting approval" },
    { value: "running", label: "Running" },
    { value: "paused", label: "Paused" },
    { value: "completed", label: "Completed" },
    { value: "failed", label: "Failed" },
    { value: "rejected", label: "Rejected" },
    { value: "cancelled", label: "Cancelled" },
  ];

const QUICK_TABS: Array<{ value: "" | PipelineRunStatus; label: string }> = [
  { value: "", label: "All Runs" },
  { value: "awaiting_approval", label: "Awaiting" },
  { value: "running", label: "Active" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

const STATUS_LABEL: Record<string, string> = {
  planning: "Planning",
  replanning: "Replanning",
  awaiting_approval: "Awaiting Approval",
  approved: "Approved",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

const STATUS_CHIP_CLASS: Record<string, string> = {
  planning: "planning",
  replanning: "replanning",
  awaiting_approval: "awaiting-approval",
  approved: "approved",
  running: "running",
  paused: "paused",
  completed: "completed",
  failed: "failed",
  rejected: "rejected",
  cancelled: "cancelled",
};

function Svg({
  d,
  width = 15,
  height = 15,
  className,
}: {
  d: string;
  width?: number;
  height?: number;
  className?: string;
}) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d={d} />
    </svg>
  );
}

function statusIconPath(status: PipelineRunStatus): string {
  switch (status) {
    case "completed":
      return "M22 11.08V12a10 10 0 1 1-5.93-9.14";
    case "failed":
    case "rejected":
      return "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4m0 4h.01";
    case "cancelled":
      return "M18 6 6 18M6 6l12 12";
    case "paused":
      return "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71";
    case "planning":
    case "replanning":
      return "M9 3v2m6-2v2M9 19v2m6-2v2M5 17H3m2-10H3m18 10h-2m2-10h-2m-3 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0z";
    case "approved":
    case "running":
    default:
      return "M13 2 3 14h9l-1 8 10-12h-9l1-8z";
  }
}

function statusIconColor(status: PipelineRunStatus): string {
  switch (status) {
    case "completed":
      return "#059669";
    case "failed":
    case "rejected":
      return "#dc2626";
    case "planning":
    case "replanning":
      return "#0284c7";
    case "awaiting_approval":
    case "paused":
      return "#d97706";
    case "approved":
    case "running":
      return "#2563eb";
    default:
      return "#94a3b8";
  }
}

function stageInfo(run: PipelineRun): {
  cls: string;
  text: string;
  icon?: string;
} {
  switch (run.status) {
    case "planning":
      return {
        cls: "planning",
        text: "Drafting plan",
        icon: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
      };
    case "replanning":
      return {
        cls: "planning",
        text: "Revising plan",
        icon: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
      };
    case "awaiting_approval":
      return {
        cls: "warn",
        text: "Human review required",
        icon: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
      };
    case "approved":
      return {
        cls: "accent",
        text: "Approved — starting",
        icon: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
      };
    case "running":
      return {
        cls: "accent",
        text: run.current_stage ?? "Executing",
        icon: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
      };
    case "paused":
      return {
        cls: "warn",
        text: "Paused — resume to continue",
        icon: "M10 4 3 12h7l-1 8 7-8h-7l1-8z",
      };
    case "completed":
      return {
        cls: "ok",
        text: run.pr_url ? "PR opened" : "Completed",
        icon: "M22 11.08V12a10 10 0 1 1-5.93-9.14",
      };
    case "failed":
      return {
        cls: "err",
        text: run.error_message ?? "Failed",
        icon: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
      };
    case "rejected":
      return {
        cls: "err",
        text: "Plan rejected",
        icon: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
      };
    case "cancelled":
      return {
        cls: "muted",
        text: "Cancelled",
        icon: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
      };
    default:
      return { cls: "muted", text: run.status };
  }
}

export function RunsListPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<"" | PipelineRunStatus>("");
  const [projectFilter, setProjectFilter] = useState("");
  const [ticketQuery, setTicketQuery] = useState("");

  const fetchRuns = useCallback(() => api.listRuns(), []);
  const fetchProjects = useCallback(() => api.listProjects(), []);
  const { data: runs, error } = usePolling(fetchRuns, POLL_MS, true, []);
  const { data: projects } = usePolling(fetchProjects, POLL_MS, true, []);

  const list = runs ?? [];
  const projectList = projects ?? [];
  const nameById = useMemo(
    () =>
      Object.fromEntries((projects ?? []).map((p: Project) => [p.id, p.name])),
    [projects],
  );
  const loading = runs === undefined && !error;

  const q = ticketQuery.trim().toLowerCase();
  const filtered = list.filter((run) => {
    if (statusFilter && run.status !== statusFilter) return false;
    if (projectFilter && run.project_id !== projectFilter) return false;
    if (
      q &&
      !`${run.ticket_key ?? ""} ${nameById[run.project_id] ?? ""}`
        .toLowerCase()
        .includes(q)
    )
      return false;
    return true;
  });

  const liveCount = list.filter((r) =>
    LIVE_RUN_STATUSES.includes(r.status),
  ).length;
  const awaitingCount = list.filter(
    (r) => r.status === "awaiting_approval",
  ).length;
  const completedCount = list.filter((r) => r.status === "completed").length;
  const totalSpend = list.reduce(
    (sum, run) => sum + (Number(run.total_cost) || 0),
    0,
  );

  const openRun = (run: PipelineRun) => navigate(`/runs/${run.id}`);

  const resetFilters = () => {
    setTicketQuery("");
    setStatusFilter("");
    setProjectFilter("");
  };

  const exportCsv = () => {
    const header = [
      "Ticket",
      "Project",
      "Status",
      "Duration",
      "Cost",
      "PR",
      "Started",
    ];
    const rows = filtered.map((run) => [
      run.ticket_key ?? "",
      nameById[run.project_id] ?? run.project_id,
      run.status,
      formatDuration(run.started_at, run.completed_at),
      formatUsd(run.total_cost),
      run.pr_url ?? "",
      formatLocalDateTime(run.started_at),
    ]);
    const csv = [header, ...rows]
      .map((cells) =>
        cells.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","),
      )
      .join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `pipeline-runs-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="run-page">
      {/* Top metric bar */}
      <div className="run-topbar">
        <div>
          <div className="run-title-row">
            <h1>Pipeline Runs</h1>
            {/* <span className="run-live-badge">
              <span className="pulse-dot" />
              Auto-poll {POLL_MS / 1000}s
            </span> */}
          </div>
          <p className="run-sub">
            Every ticket that went through planning and execution, across
            projects and sandbox environments.
          </p>
        </div>
        <div className="run-metrics">
          <span className="run-metric">
            <span className="run-metric-label">Total</span>
            <span className="run-metric-value">{list.length}</span>
          </span>
          <span className="run-metric accent">
            <span className="pulse-dot" />
            <span className="run-metric-label">Active</span>
            <span className="run-metric-value">{liveCount}</span>
          </span>
          <span className="run-metric review">
            <Svg
              d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"
              className="run-metric-icon"
              width={13}
              height={13}
            />
            <span className="run-metric-label">Review</span>
            <span className="run-metric-value">{awaitingCount}</span>
          </span>
          <span className="run-metric">
            <Svg
              d="M22 11.08V12a10 10 0 1 1-5.93-9.14"
              className="run-metric-icon"
              width={13}
              height={13}
            />
            <span className="run-metric-label">Success</span>
            <span className="run-metric-value">
              {list.length === 0
                ? "—"
                : `${((completedCount / list.length) * 100).toFixed(1)}%`}
            </span>
          </span>
          <span className="run-metric">
            <Svg
              d="M11 17h2m-1 0v-4m0-2h.01M2 12a10 10 0 1 0 20 0 10 10 0 0 0-20 0z"
              className="run-metric-icon"
              width={13}
              height={13}
            />
            <span className="run-metric-label">Spend</span>
            <span className="run-metric-value">{formatUsd(totalSpend)}</span>
          </span>
        </div>
      </div>

      {error && <div className="banner banner-err">{error}</div>}

      {loading && (
        <div
          className="skeleton-table"
          aria-busy="true"
          aria-label="Loading runs"
        >
          <div className="skeleton-row" />
          <div className="skeleton-row" />
          <div className="skeleton-row" />
          <div className="skeleton-row" />
        </div>
      )}

      {!loading && list.length === 0 && !error && (
        <div className="card empty-state">
          <h3>No runs yet</h3>
          <p>Open a project, pick a Jira ticket, and start a pipeline.</p>
          <Link className="btn" to="/">
            Go to projects
          </Link>
        </div>
      )}

      {list.length > 0 && (
        <>
          {/* Filter & control deck */}
          <div className="run-deck">
            <div className="run-deck-row">
              <div className="run-search">
                <span className="run-search-icon">
                  <Svg d="M21 21l-4.35-4.35M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z" />
                </span>
                <input
                  className="run-search-input"
                  value={ticketQuery}
                  onChange={(e) => setTicketQuery(e.target.value)}
                  placeholder="Filter by ticket key, project, or status..."
                />
                <span className="run-search-kbd">/</span>
              </div>
              <div className="run-tabs">
                {QUICK_TABS.map((tab) => (
                  <button
                    key={tab.value}
                    type="button"
                    className={`run-tab ${
                      statusFilter === tab.value ? "active" : ""
                    }`}
                    onClick={() => setStatusFilter(tab.value)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="run-export-btn"
                onClick={exportCsv}
                disabled={filtered.length === 0}
              >
                <Svg d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
                Export CSV
              </button>
            </div>
            <div className="run-deck-row sub">
              <div className="run-select-pill">
                <span>Status</span>
                <select
                  value={statusFilter}
                  onChange={(e) =>
                    setStatusFilter(e.target.value as "" | PipelineRunStatus)
                  }
                >
                  {STATUS_FILTERS.map((opt) => (
                    <option key={opt.label} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="run-select-pill">
                <span>Project</span>
                <select
                  value={projectFilter}
                  onChange={(e) => setProjectFilter(e.target.value)}
                >
                  <option value="">All projects</option>
                  {projectList.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="run-reset"
                onClick={resetFilters}
              >
                <Svg d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8M3 3v5h5" />
                Reset
              </button>
              <span className="run-deck-note">
                Realtime telemetry synced from the runner
              </span>
            </div>
          </div>

          {/* Data grid */}
          <div className="run-grid">
            {filtered.length === 0 ? (
              <div className="run-empty">
                <h3>No runs match those filters.</h3>
                <p>Try a different ticket key, status, or project.</p>
                <button type="button" className="rd-btn" onClick={resetFilters}>
                  Reset filters
                </button>
              </div>
            ) : (
              <div className="run-grid-scroll">
                <table className="run-table">
                  <thead>
                    <tr>
                      <th>Ticket &amp; Workflow</th>
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
                    {filtered.map((run) => {
                      const stage = stageInfo(run);
                      return (
                        <tr
                          key={run.id}
                          tabIndex={0}
                          onClick={() => openRun(run)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              openRun(run);
                            }
                          }}
                        >
                          <td>
                            <div className="run-ticket-cell">
                              <span
                                className="run-ticket-icon"
                                style={{ color: statusIconColor(run.status) }}
                              >
                                <Svg d={statusIconPath(run.status)} />
                              </span>
                              <div>
                                <div className="run-ticket-keyrow">
                                  <a
                                    className="run-ticket-key"
                                    href={`/runs/${run.id}`}
                                    onClick={(e) => {
                                      e.preventDefault();
                                      openRun(run);
                                    }}
                                  >
                                    {run.ticket_key ?? "—"}
                                  </a>
                                  <span className="run-ticket-tag">
                                    {run.current_stage ?? run.status}
                                  </span>
                                </div>
                                <div className="run-ticket-title">
                                  {nameById[run.project_id] ?? run.project_id}
                                  {" · "}
                                  {run.status}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="run-cell-muted">
                            {nameById[run.project_id] ??
                              run.project_id.slice(0, 8)}
                          </td>
                          <td>
                            <span
                              className={`run-chip ${STATUS_CHIP_CLASS[run.status]}`}
                            >
                              <span className="run-chip-dot" />
                              {STATUS_LABEL[run.status] ?? run.status}
                            </span>
                          </td>
                          <td>
                            <span className={`run-stage ${stage.cls}`}>
                              {stage.icon && (
                                <span className="run-stage-ico">
                                  <Svg d={stage.icon} width={13} height={13} />
                                </span>
                              )}
                              <span
                                className={
                                  stage.cls === "err" ? "truncate" : ""
                                }
                              >
                                {stage.text}
                              </span>
                            </span>
                          </td>
                          <td className="run-cell-mono">
                            {formatDuration(run.started_at, run.completed_at)}
                          </td>
                          <td className="run-cost">
                            {formatUsd(run.total_cost)}
                          </td>
                          <td>
                            {run.pr_url ? (
                              <a
                                className="run-pr"
                                href={run.pr_url}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                title={run.pr_url}
                              >
                                <span className="run-pr-ico">
                                  <Svg d="M6 3v12M18 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 18a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 9a9 9 0 0 1-9 9" />
                                </span>
                                {run.pr_url
                                  .replace(/^https?:\/\//, "")
                                  .slice(0, 28)}
                              </a>
                            ) : (
                              <span className="run-cell-muted">
                                {[
                                  "planning",
                                  "replanning",
                                  "running",
                                  "paused",
                                  "awaiting_approval",
                                  "approved",
                                ].includes(run.status)
                                  ? "Pending"
                                  : "N/A"}
                              </span>
                            )}
                          </td>
                          <td className="run-cell-mono">
                            {formatLocalDateTime(run.started_at)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="run-grid-foot">
              <span className="run-foot-summary">
                Showing 1 - {filtered.length} of {list.length} pipeline runs
              </span>
              <span className="run-foot-note">
                Realtime telemetry synced to Codex CLI
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
