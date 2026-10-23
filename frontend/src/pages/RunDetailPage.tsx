import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatError } from "../api/client";
import { AgentRunTimeline } from "../components/AgentRunTimeline";
import { Breadcrumb } from "../components/Breadcrumb";
import { MarkdownBody } from "../components/MarkdownBody";
import { PlanView } from "../components/PlanView";
import { usePolling } from "../hooks/usePolling";
import { extractChangedFiles, formatDuration, formatUsd } from "../format";
import { readinessHref } from "../readiness";
import {
  ACTIVE_RUN_STATUSES,
  type AgentRunEvent,
  type PipelineRunStatus,
  type Plan,
  type Project,
  type Ticket,
} from "../api/types";

const POLL_INTERVAL_MS = 3000;
const EVENTS_POLL_INTERVAL_MS = 1200;

const STATUS_LABEL: Record<string, string> = {
  planning: "Planning",
  replanning: "Planning",
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
  replanning: "planning",
  awaiting_approval: "awaiting-approval",
  approved: "approved",
  running: "running",
  paused: "paused",
  completed: "completed",
  failed: "failed",
  rejected: "rejected",
  cancelled: "cancelled",
};

interface EventGroup {
  key: number;
  kind: AgentRunEvent["kind"];
  content: string;
}

// codex_stdout/codex_stderr are raw Docker exec chunks (see
// tools/sandbox_tools.py), not line-buffered - a chunk can end mid-word.
// Merge consecutive same-kind stream chunks into one continuous block so the
// engine output reads as flowing text instead of one row per fragment.
// "step" and "timing" events are already complete, distinct messages and
// never merge.
const DISCRETE_EVENT_KINDS = ["step", "timing"];

function groupEvents(events: AgentRunEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.kind === event.kind &&
      !DISCRETE_EVENT_KINDS.includes(event.kind)
    ) {
      last.content += event.content;
    } else {
      groups.push({ key: event.seq, kind: event.kind, content: event.content });
    }
  }
  return groups;
}

interface StageState {
  name: string;
  sub: string;
  icon: string;
  state: "done" | "active" | "pending" | "failed" | "skipped";
}

const STAGES: Array<{ name: string; sub: string; icon: string }> = [
  {
    name: "1. Plan",
    sub: "Initial implementation plan",
    icon: "M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z",
  },
  {
    name: "2. Human Review",
    sub: "Edit and approve the plan",
    icon: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0",
  },
  {
    name: "3. Implementation",
    sub: "Sandbox changes",
    icon: "M16 18l6-6-6-6M8 6l-6 6 6 6",
  },
  {
    name: "4. Validation",
    sub: "Build and test checks",
    icon: "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
  },
  {
    name: "5. PR Creation",
    sub: "Branch and pull request",
    icon: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3",
  },
];

function stageStates(
  status: PipelineRunStatus,
  hasPullRequest: boolean,
): StageState["state"][] {
  switch (status) {
    case "planning":
      return ["active", "pending", "pending", "pending", "pending"];
    case "replanning":
      return ["done", "active", "pending", "pending", "pending"];
    case "awaiting_approval":
      return ["done", "active", "pending", "pending", "pending"];
    case "approved":
    case "running":
    case "paused":
      return ["done", "done", "active", "pending", "pending"];
    case "completed":
      return ["done", "done", "done", "done", hasPullRequest ? "done" : "skipped"];
    case "failed":
      return ["done", "done", "failed", "pending", "pending"];
    case "rejected":
      return ["done", "failed", "pending", "pending", "pending"];
    case "cancelled":
      return ["done", "done", "failed", "pending", "pending"];
    default:
      return ["pending", "pending", "pending", "pending", "pending"];
  }
}

function Svg({
  d,
  width = 16,
  height = 16,
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

function fmtBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function artifactLabel(artifactType: string): string {
  switch (artifactType) {
    case "plan":
      return "Plan Spec AST";
    case "execution_summary":
      return "Execution Summary";
    case "comment":
      return "Ticket Comment";
    case "transcript":
      return "Full Tool-Call Trace";
    default:
      return artifactType
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [approving, setApproving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [readinessIssues, setReadinessIssues] = useState<string[]>([]);
  const [rejectionReason, setRejectionReason] = useState("");
  const [jiraComment, setJiraComment] = useState("");
  const [jiraCommentOpen, setJiraCommentOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [approvalPlan, setApprovalPlan] = useState<Plan | null>(null);
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [pausing, setPausing] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [ticketUrl, setTicketUrl] = useState<string | null>(null);
  const [stuckToBottom, setStuckToBottom] = useState(true);
  const [activeTab, setActiveTab] = useState<"log" | "artifacts" | "cost">(
    "log",
  );
  const [commentCopied, setCommentCopied] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);

  const fetchRun = useCallback(() => api.getRun(runId), [runId]);
  // Unknown status yet -> keep polling until the first response arrives.
  const lastStatus = useRef<PipelineRunStatus | "unknown">("unknown");
  const enabled =
    lastStatus.current === "unknown" ||
    ACTIVE_RUN_STATUSES.includes(lastStatus.current);
  const {
    data: detail,
    error,
    refetch,
  } = usePolling(fetchRun, POLL_INTERVAL_MS, enabled, [runId, enabled]);
  if (detail) lastStatus.current = detail.run.status;

  // Re-check readiness right as approval becomes relevant, not at plan
  // creation time - config (e.g. a git token) could have changed since.
  useEffect(() => {
    if (detail?.run.status !== "awaiting_approval") return;
    api
      .getProject(detail.run.project_id)
      .then((project) => setReadinessIssues(project.readiness_issues))
      .catch(() => {});
  }, [detail?.run.status, detail?.run.project_id]);

  useEffect(() => {
    if (!detail?.run.project_id) return;
    api
      .getProject(detail.run.project_id)
      .then(setProject)
      .catch(() => {});
  }, [detail?.run.project_id]);

  useEffect(() => {
    if (!detail?.run.project_id || !detail.run.ticket_key) {
      setTicketUrl(null);
      return;
    }
    api
      .getTicket(detail.run.project_id, detail.run.ticket_key)
      .then((t) => setTicketUrl(t.url))
      .catch(() => setTicketUrl(null));
  }, [detail?.run.project_id, detail?.run.ticket_key]);

  useEffect(() => {
    setApprovalPlan(null);
    setLiveEvents([]);
    setStuckToBottom(true);
    historyLoadedRef.current = null;
    lastSeqRef.current = 0;
    activeAgentRunIdRef.current = null;
  }, [runId]);

  useEffect(() => {
    if (
      detail?.run.status === "awaiting_approval" &&
      detail.plan &&
      approvalPlan === null
    ) {
      setApprovalPlan(detail.plan);
    }
    if (detail?.run.status !== "awaiting_approval" && approvalPlan !== null)
      setApprovalPlan(null);
  }, [approvalPlan, detail?.plan, detail?.run.status]);

  useEffect(() => {
    if (!detail?.run.project_id || !detail.run.ticket_key) return;
    api
      .getTicket(detail.run.project_id, detail.run.ticket_key)
      .then(setTicket)
      .catch(() => setTicket(null));
  }, [detail?.run.project_id, detail?.run.ticket_key]);

  // Live activity: which tool the current phase is calling, and (for
  // run_codex specifically) Codex's own live stdout - see
  // agents/pipeline_agent/runner.py / tools/sandbox_tools.py for where
  // these events come from. Polled separately from the run itself since
  // events accumulate (only new ones since the last-seen seq come back)
  // rather than replacing on each fetch, unlike usePolling's model.
  const activeAgentRun =
    detail?.agent_runs.find((a) => a.status === "running") ?? null;
  const [liveEvents, setLiveEvents] = useState<AgentRunEvent[]>([]);
  const lastSeqRef = useRef(0);
  const activeAgentRunIdRef = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const historyLoadedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!activeAgentRun) return undefined;

    if (activeAgentRunIdRef.current !== activeAgentRun.id) {
      activeAgentRunIdRef.current = activeAgentRun.id;
      lastSeqRef.current = 0;
      setLiveEvents([]);
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const events = await api.getAgentRunEvents(
          activeAgentRun.id,
          lastSeqRef.current,
        );
        if (cancelled || events.length === 0) return;
        lastSeqRef.current = events[events.length - 1].seq;
        setLiveEvents((prev) => [...prev, ...events]);
      } catch {
        // Transient poll failure - next tick retries, nothing to show for it.
      }
    };

    poll();
    const id = setInterval(poll, EVENTS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [activeAgentRun]);

  useEffect(() => {
    if (activeAgentRun || !detail) return;
    const last = [...detail.agent_runs]
      .reverse()
      .find((a) => a.status !== "running");
    if (!last || historyLoadedRef.current === last.id) return;
    if (liveEvents.length > 0) {
      historyLoadedRef.current = last.id;
      return;
    }
    historyLoadedRef.current = last.id;
    api
      .getAgentRunEvents(last.id, 0)
      .then((events) => {
        if (events.length) setLiveEvents(events);
      })
      .catch(() => {});
  }, [activeAgentRun, detail, liveEvents.length]);

  useEffect(() => {
    if (!stuckToBottom) return;
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [liveEvents, stuckToBottom]);

  const respond = async (
    action: "approve" | "reject",
    escalateToJira = false,
  ) => {
    setApproving(true);
    setActionError(null);
    try {
      await api.approveRun(runId, {
        approve: action === "approve",
        action,
        plan: action === "approve" ? approvalPlan ?? undefined : undefined,
        rejection_reason:
          action === "reject" &&
          (escalateToJira ? jiraComment.trim() : rejectionReason.trim())
            ? escalateToJira
              ? jiraComment.trim()
              : rejectionReason.trim()
            : undefined,
        escalate_to_jira: escalateToJira,
      });
      setRejectOpen(false);
      setRejectionReason("");
      setJiraCommentOpen(false);
      setJiraComment("");
      await refetch();
    } catch (e) {
      setActionError(formatError(e));
    } finally {
      setApproving(false);
    }
  };

  // Toggle pause/resume for the run's in-flight phase. The backend freezes
  // the sandbox container (or blocks at the next checkpoint), so the agent
  // continues exactly where it stopped when resumed.
  const togglePause = async () => {
    if (!detail) return;
    const paused = detail.run.status === "paused";
    setPausing(true);
    try {
      await (paused ? api.resumeRun(runId) : api.pauseRun(runId));
      await refetch();
    } finally {
      setPausing(false);
    }
  };

  // Kills the sandbox container immediately (unpausing first if frozen) -
  // unlike Pause, this cannot be undone, so it's confirmed like every other
  // destructive action in this app (delete project/connection, disconnect
  // Codex).
  const cancelRun = async () => {
    if (
      !confirm(
        "Cancel this run? This stops it immediately and cannot be undone.",
      )
    )
      return;
    setCanceling(true);
    setLifecycleError(null);
    try {
      await api.cancelRun(runId);
      await refetch();
    } catch (e) {
      setLifecycleError(formatError(e));
    } finally {
      setCanceling(false);
    }
  };

  // Retry the failed phase in a new run and navigate to it.
  const retryRun = async () => {
    setRetrying(true);
    setLifecycleError(null);
    try {
      const newRun = await api.retryRun(runId);
      navigate(`/runs/${newRun.id}`);
    } catch (e) {
      setLifecycleError(formatError(e));
      setRetrying(false);
    }
  };

  if (error) return <p className="error-text">{error}</p>;
  if (!detail) return <p className="muted">Loading…</p>;

  const {
    run,
    plan,
    pr_url,
    agent_runs,
    artifacts,
    cost_logs,
    total_cost,
    skills_used,
  } = detail;
  const executionSummary = artifacts.find(
    (a) => a.artifact_type === "execution_summary",
  );
  const ticketComment =
    artifacts.find((a) => a.artifact_type === "comment") ?? executionSummary;
  const transcripts = artifacts.filter((a) => a.artifact_type === "transcript");
  const changedFiles = extractChangedFiles(executionSummary?.content ?? "");
  const isFinished =
    run.status === "completed" ||
    run.status === "failed" ||
    run.status === "cancelled" ||
    run.status === "rejected";

  const canPause = run.status === "running";
  const isPaused = run.status === "paused";
  const canCancel = ACTIVE_RUN_STATUSES.includes(run.status);
  const canRetry =
    run.status === "failed" ||
    run.status === "rejected" ||
    run.status === "cancelled";
  const retryImplementation =
    (run.status === "failed" || run.status === "cancelled") &&
    run.current_stage === "execution" &&
    plan !== null;

  const stages: StageState[] = STAGES.map((s, i) => ({
    ...s,
    state: stageStates(run.status, Boolean(pr_url))[i],
  }));

  const costTotals = cost_logs.reduce(
    (acc, log) => ({
      input: acc.input + log.input_tokens,
      output: acc.output + log.output_tokens,
      cost: acc.cost + Number(log.total_cost),
    }),
    { input: 0, output: 0, cost: 0 },
  );

  const logGroups = groupEvents(liveEvents);

  const statusChip = (
    <span className={`run-chip ${STATUS_CHIP_CLASS[run.status] ?? ""}`}>
      <span className="run-chip-dot" />
      {STATUS_LABEL[run.status] ?? run.status}
    </span>
  );

  const headerActions = (
    <div className="rd-actions">
      {(canPause || isPaused) && (
        <button
          type="button"
          className="rd-btn"
          disabled={pausing}
          onClick={togglePause}
          title={
            isPaused
              ? "Resume the agent from where it stopped"
              : "Pause the running agent - you can resume it later from the same point"
          }
        >
          {isPaused
            ? pausing
              ? "Resuming…"
              : "Resume"
            : pausing
              ? "Pausing…"
              : "Pause"}
        </button>
      )}
      {canRetry && (
        <button
          type="button"
          className="rd-btn"
          disabled={retrying}
          onClick={retryRun}
          title={
            retryImplementation
              ? "Retry implementation using the approved plan"
              : "Start a fresh run for this ticket"
          }
        >
          {retrying
            ? "Starting…"
            : retryImplementation
              ? "Retry implementation"
              : "Retry"}
        </button>
      )}
      <Link className="rd-btn" to={`/projects/${run.project_id}/tickets`}>
        View tickets
      </Link>
      <Link className="rd-btn" to="/runs">
        All runs
      </Link>
      {canCancel && (
        <button
          type="button"
          className="rd-btn danger"
          disabled={canceling}
          onClick={cancelRun}
          title="Stop this run immediately - cannot be undone"
        >
          {canceling ? "Canceling…" : "Cancel"}
        </button>
      )}
    </div>
  );

  const gate = (() => {
    if (run.status === "awaiting_approval") {
      return (
        <section className="rd-gate approval">
          <div className="rd-gate-top">
            <div className="rd-gate-left">
              <div className="rd-gate-icon violet">
                <Svg d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4" />
              </div>
              <div>
                <div className="rd-gate-title-row">
                  <h3>Awaiting Human Approval</h3>
                  <span className="rd-gate-chip violet">
                    <span className="run-chip-dot" />
                    Gate: Pre-Sandbox Write Barrier
                  </span>
                </div>
                <p className="rd-gate-desc">
                  AI implementation plan generated and validated against the
                  project schema. Human sign-off required before sandbox build
                  and pull request creation.
                </p>
              </div>
            </div>
            <div className="rd-gate-actions">
              <button
                type="button"
                className="rd-btn primary"
                disabled={approving || readinessIssues.length > 0}
                title={
                  readinessIssues.length > 0
                    ? "Fix the project issues above first"
                    : undefined
                }
                onClick={() => respond("approve")}
              >
                <Svg d="M20 6 9 17l-5-5" />
                {approving ? "Starting implementation…" : "Approve & Implement"}
              </button>
              {rejectOpen ? (
                <button
                  type="button"
                  className="rd-btn danger"
                  disabled={approving}
                  onClick={() => respond("reject")}
                >
                  Reject plan
                </button>
              ) : (
                <button
                  type="button"
                  className="rd-btn danger"
                  disabled={approving}
                  onClick={() => setRejectOpen(true)}
                >
                  Reject
                </button>
              )}
              {plan?.risks && (
                <button
                  type="button"
                  className="rd-btn"
                  disabled={approving}
                  onClick={() => setJiraCommentOpen((open) => !open)}
                  title="Add a reviewer note and send it to Jira"
                >
                  Send to Jira for input
                </button>
              )}
            </div>
          </div>
          <div className="rd-gate-body">
            {actionError && <p className="error-text">{actionError}</p>}
            {jiraCommentOpen && (
              <>
                <div className="rd-gate-fields">
                  <label className="field">
                    Reviewer note
                    <textarea
                      autoFocus
                      value={jiraComment}
                      onChange={(e) => setJiraComment(e.target.value)}
                      rows={3}
                      placeholder="Add a note for the Jira ticket"
                    />
                  </label>
                </div>
                <div className="rd-gate-actions">
                  <button
                    type="button"
                    className="rd-btn"
                    disabled={approving}
                    onClick={() => {
                      setJiraCommentOpen(false);
                      setJiraComment("");
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="rd-btn primary"
                    disabled={approving || !jiraComment.trim()}
                    onClick={() => respond("reject", true)}
                  >
                    {approving ? "Sending…" : "Send comment to Jira"}
                  </button>
                </div>
              </>
            )}
            {readinessIssues.length > 0 && (
              <div>
                <p className="error-text">
                  Approving will fail - this project still needs:
                </p>
                <ul className="rd-issue-list">
                  {readinessIssues.map((issue) => (
                    <li key={issue}>
                      <Link to={readinessHref(run.project_id, issue)}>
                        {issue}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plan?.risks && (
              <div className="rd-gate-notes">
                <div className="field" style={{ marginBottom: 0 }}>
                  <span>Heads-up notes from planning</span>
                  <MarkdownBody text={plan.risks} />
                </div>
              </div>
            )}
            {/* <div className="rd-gate-fields">
              <label className="field">
                Notes for implementation (optional)
                <textarea
                  value={reviewerAnswers}
                  onChange={(e) => setReviewerAnswers(e.target.value)}
                  rows={2}
                  placeholder="Add any final implementation guidance"
                />
              </label>
            </div> */}
            {rejectOpen && (
              <label className="field">
                Reason for rejecting (optional)
                <textarea
                  value={rejectionReason}
                  onChange={(e) => setRejectionReason(e.target.value)}
                  rows={3}
                  placeholder="Why should this plan not be implemented?"
                />
              </label>
            )}
            {rejectOpen && (
              <div className="rd-gate-actions">
                <button
                  type="button"
                  className="rd-btn"
                  disabled={approving}
                  onClick={() => {
                    setRejectOpen(false);
                    setRejectionReason("");
                  }}
                >
                  Keep reviewing
                </button>
              </div>
            )}
          </div>
        </section>
      );
    }

    if (isFinished) {
      const doneOk = run.status === "completed";
      const title = doneOk
        ? pr_url
          ? "Pull request opened"
          : "Finished without a pull request"
        : run.status === "failed"
          ? "Run failed"
          : run.status === "rejected"
            ? "Plan rejected"
            : "Run cancelled";
      const iconCls = doneOk ? "ok" : "err";
      return (
        <section
          className={`rd-gate ${doneOk ? "done-ok" : run.status === "cancelled" ? "done-muted" : "done-err"}`}
        >
          <div className="rd-gate-top">
            <div className="rd-gate-left">
              <div className={`rd-gate-icon ${iconCls}`}>
                {doneOk ? (
                  <Svg d="M20 6 9 17l-5-5" />
                ) : (
                  <Svg d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4m0 4h.01" />
                )}
              </div>
              <div>
                <div className="rd-gate-title-row">
                  <h3>{title}</h3>
                  {statusChip}
                </div>
                {pr_url ? (
                  <p className="rd-gate-desc">
                    <a href={pr_url} target="_blank" rel="noreferrer">
                      {pr_url}
                    </a>
                  </p>
                ) : doneOk ? (
                  <p className="rd-gate-desc muted">
                    The agent finished but did not open a PR. Read the summary
                    below for why.
                  </p>
                ) : null}
                {run.error_message && (
                  <p className="error-text">{run.error_message}</p>
                )}
                <div className="rd-gate-title-row" style={{ marginTop: 6 }}>
                  {run.ticket_key && (
                    <span className="rd-meta-item">
                      Ticket {run.ticket_key} ·{" "}
                      <Link
                        to={`/projects/${run.project_id}/tickets?ticket=${run.ticket_key}`}
                      >
                        Open here
                      </Link>
                      {ticketUrl && (
                        <>
                          {" · "}
                          <a href={ticketUrl} target="_blank" rel="noreferrer">
                            Open in Jira
                          </a>
                        </>
                      )}
                    </span>
                  )}
                </div>
              </div>
            </div>
            <div className="rd-gate-actions">
              <span className="rd-meta-item">
                Spend <strong>{formatUsd(total_cost)}</strong>
              </span>
              {canRetry && (
                <button
                  type="button"
                  className="rd-btn"
                  disabled={retrying}
                  onClick={retryRun}
                >
                  {retrying
                    ? "Starting…"
                    : retryImplementation
                      ? "Retry implementation"
                      : "Retry this ticket"}
                </button>
              )}
            </div>
          </div>
          {changedFiles.length > 0 && (
            <div>
              <div className="rd-gate-title-row">
                <span
                  className="font-label-sm muted"
                  style={{ fontWeight: 600 }}
                >
                  Files mentioned
                </span>
              </div>
              <ul className="file-list">
                {changedFiles.map((f) => (
                  <li key={f} className="mono small">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      );
    }

    // Active / planning / paused gate
    const planning = run.status === "planning" || run.status === "replanning";
    const paused = run.status === "paused";
    const iconCls = planning ? "amber" : paused ? "amber" : "violet";
    const title = planning
      ? run.status === "replanning"
        ? "Revising the plan"
        : "Drafting the plan"
      : paused
        ? "Paused"
        : "Running";
    const desc = planning
      ? run.status === "replanning"
        ? "Revising the plan with your answers… this page updates automatically."
        : "Drafting a plan from the ticket and repo… this page updates automatically."
      : paused
        ? "The agent is paused mid-stage. Resume anytime to continue from exactly where it stopped."
        : activeAgentRun
          ? `${activeAgentRun.agent_name} is executing on ${activeAgentRun.stage}.`
          : run.current_stage
            ? `Executing stage: ${run.current_stage}.`
            : "Agent execution is in flight… this page updates automatically.";

    return (
      <section
        className={`rd-gate ${paused ? "done-muted" : planning ? "planning" : "active"}`}
      >
        <div className="rd-gate-top">
          <div className="rd-gate-left">
            <div className={`rd-gate-icon ${iconCls}`}>
              {planning ? (
                <Svg d="M12 2v6m0 8v6M8 8H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h4M16 8h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4" />
              ) : paused ? (
                <Svg d="M10 4 3 12h7l-1 8 7-8h-7l1-8z" />
              ) : (
                <Svg d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
              )}
            </div>
            <div>
              <div className="rd-gate-title-row">
                <h3>{title}</h3>
                {statusChip}
              </div>
              <p className="rd-gate-desc">{desc}</p>
            </div>
          </div>
          <div className="rd-gate-actions">
            {paused && (
              <button
                type="button"
                className="rd-btn success"
                disabled={pausing}
                onClick={togglePause}
              >
                {pausing ? "Resuming…" : "Resume run"}
              </button>
            )}
          </div>
        </div>
        {run.status === "replanning" && run.review_warnings && run.review_warnings.length > 0 && (
          <div className="rd-gate-body">
            <div className="rd-gate-warnings">
              <div className="rd-gate-warnings-header">
                <Svg d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <span>Warnings found during planning</span>
              </div>
              <ul>
                {run.review_warnings.map((warning, i) => (
                  <li key={i}>{warning}</li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </section>
    );
  })();

  const stepper = (
    <section className="rd-stepper">
      <div className="rd-steps">
        {stages.map((stage, i) => (
          <div
            key={stage.name}
            className="rd-step-row-wrap"
            style={{ display: "contents" }}
          >
            {i > 0 && (
              <div
                className={`rd-connector ${stages[i - 1].state === "done" ? "done" : "pending"}`}
              />
            )}
            <div
              className={`rd-step ${stage.state === "pending" || stage.state === "skipped" ? stage.state : ""}`}
              style={{ display: "flex" }}
            >
              <div className={`rd-step-badge ${stage.state}`}>
                {stage.state === "done" && (
                  <Svg d="M20 6 9 17l-5-5" width={16} height={16} />
                )}
                {stage.state === "active" && (
                  <Svg
                    d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"
                    width={15}
                    height={15}
                  />
                )}
                {stage.state === "failed" && (
                  <Svg
                    d="M12 9v4m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9 9l6 6M15 9l-6 6"
                    width={15}
                    height={15}
                  />
                )}
                {stage.state === "pending" && (
                  <Svg d={stage.icon} width={15} height={15} />
                )}
                {stage.state === "skipped" && (
                  <Svg d={stage.icon} width={15} height={15} />
                )}
              </div>
              <div className="rd-step-text">
                <div className="rd-step-name">
                  {stage.name}
                  {stage.state === "active" && (
                    <span className="rd-step-live-dot" />
                  )}
                </div>
                <span className="rd-step-sub">
                  {stage.state === "done"
                    ? "Completed"
                    : stage.state === "active"
                      ? "Current Active"
                      : stage.state === "failed"
                        ? "Stalled"
                        : stage.state === "skipped"
                          ? "Not needed"
                        : stage.sub}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );

  const logTab = (
    <div className="rd-tabpane">
      <div className="rd-console-toolbar">
        <div className="rd-console-counts">
          <span className="rd-console-count">
            All Events ({liveEvents.length})
          </span>
          <span className="rd-console-count">STEP</span>
          <span className="rd-console-count">AGENT</span>
        </div>
        {!stuckToBottom && activeAgentRun && (
          <button
            type="button"
            className="rd-btn"
            onClick={() => {
              setStuckToBottom(true);
              logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
            }}
          >
            Jump to latest
          </button>
        )}
      </div>
      <div
        ref={logRef}
        className="rd-console"
        onScroll={(e) => {
          const el = e.currentTarget;
          const nearBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          setStuckToBottom(nearBottom);
        }}
      >
        {logGroups.length === 0 && (
          <div className="rd-console-empty">
            {activeAgentRun
              ? "Waiting for the first step…"
              : "No agent events captured yet."}
          </div>
        )}
        {logGroups.map((g) => (
          <div key={g.key} className="rd-log-line">
            <span
              className={`rd-log-kind ${g.kind === "codex_stdout" || g.kind === "codex_stderr" ? "engine" : g.kind}`}
            >
              {g.kind === "codex_stdout"
                ? "AGENT"
                : g.kind === "codex_stderr"
                  ? "ERROR"
                  : g.kind === "timing"
                    ? "TIME"
                    : "STEP"}
            </span>
            <span
              className={
                g.kind === "step"
                  ? "rd-log-step"
                  : g.kind === "timing"
                    ? "rd-log-timing"
                    : "rd-log-engine"
              }
            >
              {g.content}
            </span>
          </div>
        ))}
      </div>
      <div className="rd-console-foot">
        <span>[CLI Runner: codex @ salesforce/cli]</span>
        <span>
          {activeAgentRun ? "Streaming live events" : "Run completed"}
        </span>
      </div>
    </div>
  );

  const artifactsTab = (
    <div className="rd-tabpane">
      {artifacts.length === 0 ? (
        <p className="muted">No artifacts produced yet.</p>
      ) : (
        artifacts.map((artifact) => (
          <div key={artifact.id}>
            <div className="rd-artifact">
              <div className="rd-artifact-left">
                <span className="rd-artifact-ico">
                  <Svg d="M4 4h16v16H4zM9 4v16 M14 4v16" />
                </span>
                <div>
                  <div className="rd-artifact-name">{artifact.name}</div>
                  <div className="rd-artifact-meta">
                    {artifactLabel(artifact.artifact_type)} ·{" "}
                    {fmtBytes(artifact.content?.length ?? 0)}
                  </div>
                </div>
              </div>
              <div className="rd-artifact-actions">
                <button
                  type="button"
                  className="rd-artifact-btn"
                  onClick={() =>
                    setOpenArtifact(
                      openArtifact === artifact.id ? null : artifact.id,
                    )
                  }
                >
                  {openArtifact === artifact.id ? "Hide" : "View"}
                </button>
              </div>
            </div>
            {openArtifact === artifact.id && (
              <pre className="rd-artifact-expand" style={{ marginTop: 8 }}>
                {artifact.content ?? "No preview available for this artifact."}
              </pre>
            )}
          </div>
        ))
      )}
    </div>
  );

  const costTab = (
    <div className="rd-tabpane">
      <div className="rd-cost-model">
        <span className="rd-cost-label">Model Telemetry</span>
        <div className="rd-cost-grid">
          <div className="rd-cost-tile">
            <span>Prompt</span>
            <b>{costTotals.input.toLocaleString()}</b>
          </div>
          <div className="rd-cost-tile">
            <span>Output</span>
            <b>{costTotals.output.toLocaleString()}</b>
          </div>
          <div className="rd-cost-tile">
            <span>Total Tokens</span>
            <b>{(costTotals.input + costTotals.output).toLocaleString()}</b>
          </div>
        </div>
      </div>
      <div className="rd-cost-rows">
        <div className="rd-cost-row">
          <span>Current Cost</span>
          <b>{formatUsd(total_cost)}</b>
        </div>
        {cost_logs.map((log) => (
          <div key={log.id} className="rd-cost-row">
            <span>
              {log.agent_name} · {log.model} ({log.input_tokens}/
              {log.output_tokens})
            </span>
            <b>{formatUsd(log.total_cost)}</b>
          </div>
        ))}
      </div>
    </div>
  );

  const tabsCard = (
    <div className="rd-tabs">
      <div className="rd-tabbar">
        <div className="rd-tabbar-group">
          <button
            type="button"
            className={`rd-tab ${activeTab === "log" ? "active" : ""}`}
            onClick={() => setActiveTab("log")}
          >
            <Svg d="M4 17l6-6-6-6M12 19h8" width={14} height={14} />
            Execution Log
          </button>
          <button
            type="button"
            className={`rd-tab ${activeTab === "artifacts" ? "active" : ""}`}
            onClick={() => setActiveTab("artifacts")}
          >
            <Svg d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" width={14} height={14} />
            Artifacts
            <span className="rd-tab-badge">{artifacts.length}</span>
          </button>
          <button
            type="button"
            className={`rd-tab ${activeTab === "cost" ? "active" : ""}`}
            onClick={() => setActiveTab("cost")}
          >
            <Svg
              d="M19 21V5a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2z M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8M12 3v18"
              width={14}
              height={14}
            />
            Cost &amp; Quotas
          </button>
        </div>
        <span className="rd-tab-status">
          <span className="pulse-dot" />
          {activeAgentRun ? "Streaming" : "Stable"}
        </span>
      </div>
      {activeTab === "log" && logTab}
      {activeTab === "artifacts" && artifactsTab}
      {activeTab === "cost" && costTab}
    </div>
  );

  const agentsBox =
    agent_runs.length > 0 ? (
      <div className="rd-agents">
        <div className="rd-agents-head">
          <h3>Active Agents &amp; Model Pool</h3>
          <span className="rd-agents-count">{agent_runs.length} Runs</span>
        </div>
        {agent_runs.map((agentRun) => (
          <div key={agentRun.id} className="rd-agent">
            <div className="rd-agent-left">
              <span
                className="rd-iconbox"
                style={{
                  background:
                    agentRun.status === "completed"
                      ? "var(--ok-soft)"
                      : "#eaddff",
                  color:
                    agentRun.status === "completed"
                      ? "var(--ok)"
                      : "var(--purple)",
                }}
              >
                <Svg
                  d="M12 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM2 21v-2a4 4 0 0 1 4-4h12a4 4 0 0 1 4 4v2"
                  width={14}
                  height={14}
                />
              </span>
              <div>
                <div className="rd-agent-name">{agentRun.agent_name}</div>
                <div className="rd-agent-spec">
                  {agentRun.stage}
                  {agentRun.error_message ? ` · ${agentRun.error_message}` : ""}
                </div>
              </div>
            </div>
            <span
              className={`run-chip ${STATUS_CHIP_CLASS[agentRun.status] ?? ""}`}
            >
              <span className="run-chip-dot" />
              {agentRun.status}
            </span>
          </div>
        ))}
      </div>
    ) : null;

  const planCard = plan && (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div className="rd-panel-title">
          <div className="rd-iconbox">
            <Svg d="M3 21h18M5 21V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16M8 9h8M8 13h6" />
          </div>
          <div>
            <h2>AI Implementation Plan</h2>
            <p>
              {run.ticket_key ?? "Ticket"} — synthesized by the planning agent
            </p>
          </div>
        </div>
      </div>
      <PlanView
        plan={approvalPlan ?? plan}
        editable={run.status === "awaiting_approval"}
        onPlanChange={setApprovalPlan}
        attachments={(ticket?.attachments ?? [])
          .filter((attachment) => attachment.mime_type?.startsWith("image/"))
          .map((attachment) => ({
            filename: attachment.filename,
            mime_type: attachment.mime_type,
            url: `/api/projects/${run.project_id}/tickets/${encodeURIComponent(run.ticket_key ?? "")}/attachments/${encodeURIComponent(attachment.id)}`,
          }))}
      />
    </div>
  );

  const skillsCard = skills_used.length > 0 && (
    <div className="rd-panel">
      <div className="rd-panel-head">
        <div className="rd-panel-title">
          <div className="rd-iconbox violet">
            <Svg d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
          </div>
          <div>
            <h2>Skills &amp; Memory</h2>
            <p>
              Whether the agent opened each available skill or memory during
              this run.
            </p>
          </div>
        </div>
      </div>
      <div className="meta-row">
        {skills_used.map((s) => (
          <span
            key={s.slug}
            className={`badge ${s.used ? "badge-ok" : ""}`}
            title={s.slug}
          >
            {s.name} {s.used ? "· used" : "· installed, not used"}
          </span>
        ))}
      </div>
    </div>
  );

  const summaryCard = executionSummary && (
    <>
      <div className="rd-panel">
        <div className="rd-panel-head">
          <div className="rd-panel-title">
            <div className="rd-iconbox teal">
              <Svg d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />
            </div>
            <div>
              <h2>Execution Summary</h2>
              <p>Generated by the agent when the run finished.</p>
            </div>
          </div>
        </div>
        <MarkdownBody text={executionSummary.content ?? ""} />
      </div>
      <div className="rd-panel rd-comment-box">
        <div className="rd-panel-head">
          <div className="rd-panel-title">
            <div className="rd-iconbox blue">
              <Svg d="M8 7h13M8 12h13M8 17h13M3 7h.01M3 12h.01M3 17h.01" />
            </div>
            <div>
              <h2>Comment to copy</h2>
              <p>Paste this update into the customer&apos;s ticket or Jira comment.</p>
            </div>
          </div>
          <button
            type="button"
            className="rd-btn rd-comment-copy"
            onClick={async () => {
              await navigator.clipboard.writeText(ticketComment?.content ?? "");
              setCommentCopied(true);
              window.setTimeout(() => setCommentCopied(false), 1800);
            }}
          >
            <Svg d="M8 8h10v12H8zM6 16H4V4h10v2" width={14} height={14} />
            {commentCopied ? "Copied" : "Copy comment"}
          </button>
        </div>
        <textarea
          className="rd-comment-text"
          value={ticketComment?.content ?? ""}
          readOnly
          aria-label="Customer comment"
          onFocus={(event) => event.currentTarget.select()}
        />
      </div>
    </>
  );

  return (
    <div className="run-page">
      <nav className="rd-breadcrumb">
        <Breadcrumb
          items={[
            { label: "Projects", to: "/" },
            {
              label: project?.name ?? "Project",
              to: `/projects/${run.project_id}`,
            },
            {
              label: run.ticket_key ?? "Tickets",
              to: `/projects/${run.project_id}/tickets${run.ticket_key ? `?ticket=${run.ticket_key}` : ""}`,
            },
            { label: `Run ${run.id.slice(0, 8)}` },
          ]}
        />
      </nav>

      <div className="rd-header">
        <div className="rd-headgroup">
          <div className="rd-title-row">
            <h1>Run {run.id.slice(0, 8)}</h1>
            {run.ticket_key && (
              <span className="rd-id-mark">{run.ticket_key}</span>
            )}
            <span className="rd-tag">Agentic Pipeline</span>
            {statusChip}
          </div>
          <div className="rd-meta-strip">
            <span className="rd-meta-item">
              <Svg
                d="M3 7v10M7 7v10M3 7a2 2 0 0 1 4 0M7 7a2 2 0 0 1 4 0"
                width={13}
                height={13}
              />
              <strong>Project:</strong>{" "}
              {project?.name ?? run.project_id.slice(0, 8)}
            </span>
            <span className="rd-meta-sep">·</span>
            {project?.default_branch && (
              <>
                <span className="rd-meta-item">
                  <Svg
                    d="M6 3v12M18 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 18a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 9a9 9 0 0 1-9 9"
                    width={13}
                    height={13}
                  />
                  <strong>Branch:</strong> {project.default_branch}
                </span>
                <span className="rd-meta-sep">·</span>
              </>
            )}
            <span className="rd-meta-item">
              <Svg
                d="M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
                width={13}
                height={13}
              />
              <strong>Stage:</strong> {run.current_stage ?? run.status}
            </span>
            {project?.sandbox_kind === "salesforce" &&
              project.salesforce_connection_name && (
                <>
                  <span className="rd-meta-sep">·</span>
                  <span className="rd-meta-item">
                    <Svg
                      d="M22 12h-4l-3 9L9 3l-3 9H2"
                      width={13}
                      height={13}
                    />
                    <strong>Target:</strong>{" "}
                    {project.salesforce_connection_name}
                  </span>
                </>
              )}
            {run.retried_from_run_id && (
              <>
                <span className="rd-meta-sep">·</span>
                <span className="rd-meta-item">
                  Retried from{" "}
                  <Link to={`/runs/${run.retried_from_run_id}`}>
                    run {run.retried_from_run_id.slice(0, 8)}
                  </Link>
                </span>
              </>
            )}
          </div>
        </div>
        <div className="rd-side">
          <div className="rd-chips">
            <span className="rd-chip accent">
              <span className="rd-chip-ico">
                <Svg
                  d="M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
                  width={14}
                  height={14}
                />
              </span>
              {run.started_at
                ? `${formatDuration(run.started_at, run.completed_at)} elapsed`
                : "In progress"}
            </span>
            <span className="rd-chip violet">
              <span className="rd-chip-ico">
                <Svg
                  d="M20 12V8H4v4M12 2v9M4 12v8h16M2 19h2M22 19h-2"
                  width={14}
                  height={14}
                />
              </span>
              {formatUsd(total_cost)} incurred
            </span>
          </div>
          {headerActions}
        </div>
      </div>

      {lifecycleError && <p className="error-text">{lifecycleError}</p>}

      {gate}
      {stepper}

      <section className="rd-workspace">
        <div className="rd-col">
          {planCard}
          {skillsCard}
          {summaryCard}
        </div>
        <div className="rd-col">
          {tabsCard}
          {agentsBox}
        </div>
      </section>

      <div className="rd-panel">
        <div className="rd-panel-head">
          <div className="rd-panel-title">
            <div className="rd-iconbox green">
              <Svg d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
            </div>
            <div>
              <h2>Agent runs</h2>
              <p>Execution timeline of every agent phase in this run.</p>
            </div>
          </div>
        </div>
        <AgentRunTimeline agentRuns={agent_runs} />
      </div>

      {transcripts.map((t) => (
        <details key={t.id} className="rd-panel">
          <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 15 }}>
            {t.name} (full tool-call trace)
          </summary>
          <pre
            className="mono small"
            style={{
              whiteSpace: "pre-wrap",
              marginTop: 12,
              background: "#f8f9ff",
              padding: 12,
              borderRadius: 8,
              border: "1px solid #e2e8f0",
              maxHeight: 420,
              overflowY: "auto",
            }}
          >
            {t.content}
          </pre>
        </details>
      ))}
    </div>
  );
}
