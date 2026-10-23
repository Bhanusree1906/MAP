import { useEffect, useMemo, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, formatError } from "../api/client";
import { Breadcrumb, type Crumb } from "../components/Breadcrumb";
import { MarkdownBody } from "../components/MarkdownBody";
import { TicketImportPanel } from "../components/TicketImportPanel";
import { readinessHref } from "../readiness";
import type { Project, Ticket } from "../api/types";

// Which tracker this project is wired to (Azure wins when it is the active
// ticket source since the project links exactly one of jira/azure).
function ticketSourceLabel(project: Project | null): "Jira" | "Azure" | "Jira Group" {
  if (project?.ticket_group_name) return "Jira Group";
  return project?.azure_connection_name && !project.jira_connection_name
    ? "Azure"
    : "Jira";
}

function formatWhen(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

// Rough "x mins/hours/days ago" label - mirrored from the reference mock.
function relativeWhen(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const diff = Math.max(0, Date.now() - date.getTime());
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return formatWhen(value);
}

function initials(name: string | null | undefined) {
  if (!name) return "–";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}

// Priority metadata for icons/badges.
function priorityMeta(priority: string | null) {
  const p = (priority ?? "").trim().toLowerCase();
  if (/highest/.test(p))
    return { icon: "⇈", badge: "badge-max", title: "Highest Priority" };
  if (/high/.test(p))
    return { icon: "⇧", badge: "badge-high", title: "High Priority" };
  if (/medium|low/.test(p))
    return { icon: "－", badge: "badge-med", title: "Medium / Low Priority" };
  return { icon: "", badge: "", title: "" };
}

function issueIcon(issueType: string | null) {
  const t = (issueType ?? "").trim().toLowerCase();
  if (/bug/i.test(t)) return "🐞";
  if (/story/i.test(t)) return "⊞";
  if (/task/i.test(t)) return "☐";
  return "◇";
}

// Epics are containers, not runnable work - the pipeline runs per story (or
// other non-epic issue). Sub-tasks are rendered nested under their parent
// story instead of as their own row, since Jira returns them as ordinary
// issues from the JQL search too and they'd otherwise show up twice.
function isEpic(t: Ticket) {
  return (t.issue_type ?? "").trim().toLowerCase() === "epic";
}
function isSubtask(t: Ticket) {
  return /sub-?task/i.test(t.issue_type ?? "");
}
type TicketStatusFilter =
  | ""
  | "todo"
  | "in-progress"
  | "agent-complete"
  | "completed";

function statusGroup(
  status: string | null,
): Exclude<TicketStatusFilter, ""> | null {
  const normalized = (status ?? "").trim().toLowerCase();
  if (/agent\s*complete/.test(normalized)) return "agent-complete";
  if (/done|complete|closed|resolved|cancelled/.test(normalized))
    return "completed";
  if (/in\s*progress|in\s*development|in\s*review|progress/.test(normalized)) {
    return "in-progress";
  }
  if (/to\s*do|open|backlog|new/.test(normalized)) return "todo";
  return null;
}

function formatFileSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function isManualGroupTicket(project: Project | null, ticket: Ticket): boolean {
  return Boolean(project?.ticket_group_name) || ticket.key.toUpperCase().startsWith("MT-");
}

export function TicketPickerPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(
    searchParams.get("ticket"),
  );
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<TicketStatusFilter>("");
  const [typeFilter, setTypeFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .getProject(projectId)
      .then(setProject)
      .catch(() => {});
    api
      .listTickets(projectId)
      .then((t) => {
        setTickets(t);
        const roots = t.filter(
          (ticket) => !isEpic(ticket) && !isSubtask(ticket),
        );
        const wanted = searchParams.get("ticket");
        const match = roots.find((ticket) => ticket.key === wanted);
        const next = match ?? roots[0] ?? null;
        if (next) {
          setSelectedKey(next.key);
          if (wanted !== next.key)
            setSearchParams({ ticket: next.key }, { replace: true });
        }
      })
      .catch((e) => setError(formatError(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const roots = useMemo(
    () => tickets.filter((t) => !isEpic(t) && !isSubtask(t)),
    [tickets],
  );
  const epicCount = tickets.filter(isEpic).length;
  const types = useMemo(
    () =>
      [...new Set(roots.map((t) => t.issue_type).filter(Boolean))] as string[],
    [roots],
  );
  const assignees = useMemo(
    () =>
      [...new Set(roots.map((t) => t.assignee).filter(Boolean))] as string[],
    [roots],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = (text: string | null) =>
      (text ?? "").toLowerCase().includes(q);
    return roots.filter((t) => {
      if (statusFilter && statusGroup(t.status) !== statusFilter) return false;
      if (typeFilter && t.issue_type !== typeFilter) return false;
      if (priorityFilter && t.priority !== priorityFilter) return false;
      if (assigneeFilter === "__none__") {
        if (t.assignee) return false;
      } else if (assigneeFilter && t.assignee !== assigneeFilter) {
        return false;
      }
      if (!q) return true;
      return (
        matches(t.key) ||
        matches(t.summary) ||
        matches(t.status) ||
        matches(t.assignee) ||
        t.subtasks.some((s) => matches(s.key) || matches(s.summary))
      );
    });
  }, [roots, query, statusFilter, typeFilter, priorityFilter, assigneeFilter]);

  const clearFilters = () => {
    setQuery("");
    setStatusFilter("");
    setTypeFilter("");
    setPriorityFilter("");
    setAssigneeFilter("");
  };

  const selectTicket = (key: string) => {
    setSelectedKey(key);
    setSearchParams({ ticket: key }, { replace: true });
  };

  const selected =
    filtered.find((t) => t.key === selectedKey) ?? filtered[0] ?? null;

  useEffect(() => {
    const current =
      roots.find((t) => t.key === selectedKey) ?? roots[0] ?? null;
    if (!current) {
      setDetail(null);
      return;
    }
    setDetail(current);
    setDetailLoading(true);
    api
      .getTicket(projectId, current.key)
      .then(setDetail)
      .catch(() => setDetail(current))
      .finally(() => setDetailLoading(false));
  }, [projectId, selectedKey, roots]);

  const startPipeline = async (ticketKey: string) => {
    if (starting) return;
    setStarting(ticketKey);
    try {
      const run = await api.createRun(projectId, ticketKey);
      navigate(`/runs/${run.id}`);
    } catch (e) {
      setError(formatError(e));
      setStarting(null);
    }
  };

  const blockingIssues = project?.readiness_issues ?? [];
  const notReady = blockingIssues.length > 0;
  const unauthorized = Boolean(
    error &&
    /no credentials|reconnect|rejected|no (jira|azure) connection|AZURE_AGENT_CONNECTION/i.test(
      error,
    ),
  );

  const matchedCount = filtered.length;
  const skillNames = (project?.skills ?? [])
    .map((s) => s.name ?? s.slug)
    .slice(0, 4);

  return (
    <div>
      {/* Workspace context bar */}
      <div className="tp-context">
        <div className="tp-context-left">
          <Breadcrumb
            items={
              [
                { label: "Workspace", to: "/" },
                { label: "Projects", to: "/" },
                ...(project
                  ? [
                      { label: project.name, to: `/projects/${project.id}` },
                      { label: "Ticket Browser" },
                    ]
                  : [{ label: "Ticket Browser" }]),
              ] as Crumb[]
            }
          />
          <span className="tp-live-chip">
            <span className="tp-live-dot" />
            {ticketSourceLabel(project)} Sync · LIVE
          </span>
        </div>
        <div className="tp-context-right">
          <span className="tp-sync">
            <span className="tp-sync-icon">↻</span>
            Last synced {relativeWhen(tickets[0]?.updated) ?? "just now"}
          </span>
          {project && (
            <button
              type="button"
              className="tp-view-options"
              onClick={() => setImportOpen((v) => !v)}
            >
              <span className="tp-sync-icon">⇪</span>
              {importOpen ? "Close Import" : "Import Tickets"}
            </button>
          )}
          <button type="button" className="tp-view-options">
            <span className="tp-sync-icon">⚙</span>
            View Options
          </button>
        </div>
      </div>

      {project && importOpen && (
        <div className="tp-import-wrap">
          <TicketImportPanel
            project={project}
            onClose={() => setImportOpen(false)}
          />
        </div>
      )}

      {error && (
        <div className="banner banner-err">
          {error}{" "}
          {unauthorized && (
            <Link to="/connections">
              Open Connections to authorize a ticket source
            </Link>
          )}
        </div>
      )}

      {notReady && (
        <div className="banner banner-warn">
          This project is not ready to run yet.{" "}
          {project && (
            <Link to={`/projects/${project.id}`}>Fix these on the project</Link>
          )}
          <ul className="issue-list">
            {blockingIssues.map((issue) => (
              <li key={issue}>
                {project ? (
                  <Link to={readinessHref(project.id, issue)}>{issue}</Link>
                ) : (
                  issue
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading && <p className="muted">Loading tickets…</p>}

      {!loading && tickets.length === 0 && !error && (
        <div className="card empty-state">
          <h3>No tickets</h3>
          <p>
            {ticketSourceLabel(project)} returned no tickets for this project.
            Check the project source settings and try again.
          </p>
          <Link className="btn" to="/connections">
            Review connections
          </Link>
        </div>
      )}

      {!loading && tickets.length > 0 && (
        <div className="tp-layout">
          {/* LEFT PANE: Ticket list + filters */}
          <div className="tp-left">
            <div className="tp-filter-card">
              <div className="tp-search">
                <span className="tp-search-icon">⌕</span>
                <input
                  className="tp-search-input"
                  placeholder="Search tickets by key, summary, or text..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <kbd className="tp-kbd">↵</kbd>
              </div>
              <div className="tp-filter-grid">
                <div className="tp-filter-col">
                  <label>Status</label>
                  <div className="tp-select-wrap">
                    <select
                      className="tp-select"
                      value={statusFilter}
                      onChange={(e) =>
                        setStatusFilter(e.target.value as TicketStatusFilter)
                      }
                    >
                      <option value="">All</option>
                      <option value="todo">To Do</option>
                      <option value="in-progress">In Progress</option>
                      <option value="agent-complete">Agent Complete</option>
                      <option value="completed">Completed</option>
                    </select>
                    <span className="tp-select-caret">▾</span>
                  </div>
                </div>
                <div className="tp-filter-col">
                  <label>Priority</label>
                  <div className="tp-select-wrap">
                    <select
                      className="tp-select"
                      value={priorityFilter}
                      onChange={(e) => setPriorityFilter(e.target.value)}
                    >
                      <option value="">All</option>
                      {["Highest", "High", "Medium", "Low"].map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                    <span className="tp-select-caret">▾</span>
                  </div>
                </div>
                <div className="tp-filter-col">
                  <label>Assignee</label>
                  <div className="tp-select-wrap">
                    <select
                      className="tp-select"
                      value={assigneeFilter}
                      onChange={(e) => setAssigneeFilter(e.target.value)}
                    >
                      <option value="">All</option>
                      {assignees.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                      <option value="__none__">Unassigned</option>
                    </select>
                    <span className="tp-select-caret">▾</span>
                  </div>
                </div>
                <div className="tp-filter-col">
                  <label>Type</label>
                  <div className="tp-select-wrap">
                    <select
                      className="tp-select"
                      value={typeFilter}
                      onChange={(e) => setTypeFilter(e.target.value)}
                    >
                      <option value="">All</option>
                      {types.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    <span className="tp-select-caret">▾</span>
                  </div>
                </div>
              </div>
              <div className="tp-filter-metrics">
                <span>
                  {matchedCount} matching ticket{matchedCount === 1 ? "" : "s"}
                </span>
                <button
                  type="button"
                  className="tp-clear"
                  onClick={clearFilters}
                >
                  Clear filters
                </button>
              </div>
            </div>

            {epicCount > 0 && (
              <p className="muted small tp-epic-note">
                {epicCount} epic{epicCount === 1 ? "" : "s"} hidden — epics are
                containers, pick a story.
              </p>
            )}

            <div className="tp-list" role="region" aria-label="Ticket list" tabIndex={0}>
              {filtered.map((t) => {
                const isActive = selected?.key === t.key;
                const prio = priorityMeta(t.priority);
                return (
                  <div key={t.key} className="tp-group">
                    <button
                      type="button"
                      className={`tp-row ${isActive ? "tp-row-active" : ""}`}
                      onClick={() => selectTicket(t.key)}
                    >
                      {isActive && <span className="tp-row-bar" />}
                      <div className="tp-row-top">
                        <div className="tp-row-id">
                          <span className="tp-key-badge">
                            <span className="tp-key-icon">
                              {issueIcon(t.issue_type)}
                            </span>
                            {t.key}
                          </span>
                          {prio.icon && (
                            <span className="tp-prio" title={prio.title}>
                              {prio.icon}
                            </span>
                          )}
                          {isActive && (
                            <span className="tp-ready">
                              <span className="tp-ready-dot" />
                              Ready for Pipeline
                            </span>
                          )}
                        </div>
                        <span className="tp-when">
                          {relativeWhen(t.updated) ?? ""}
                        </span>
                      </div>
                      <h3 className="tp-summary">{t.summary}</h3>
                      <div className="tp-row-bottom">
                        <div className="tp-assignee">
                          <span className="tp-avatar">
                            {initials(t.assignee)}
                          </span>
                          <span className={t.assignee ? "" : "tp-unassigned"}>
                            {t.assignee ?? "Unassigned"}
                          </span>
                        </div>
                        {t.status && (
                          <span className="tp-status">{t.status}</span>
                        )}
                      </div>
                    </button>
                    {t.subtasks.length > 0 && (
                      <div className="ticket-subtasks tp-subtasks">
                        {t.subtasks.map((s) => (
                          <div className="subtask-row" key={s.key}>
                            <span className="key">{s.key}</span>
                            <span className="summary">{s.summary}</span>
                            {s.status && (
                              <span
                                className={`badge ${/done/i.test(s.status) ? "badge-ok" : ""}`}
                              >
                                {s.status}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
              {filtered.length === 0 && (
                <p className="muted small tp-empty">
                  No tickets match that search.
                </p>
              )}
            </div>
          </div>

          {/* RIGHT PANE: Ticket detail */}
          <div className="tp-right">
            {!selected && <p className="muted">Select a ticket to read it.</p>}
            {selected && detail && (
              <>
                {/* Detail header */}
                <div className="tp-detail-card">
                  <div className="tp-detail-top">
                    <div className="tp-id-breadcrumb">
                      <span className="tp-key-icon tp-id-mark">⊞</span>
                      <span>{project?.name ?? "Project"}</span>
                      <span className="tp-crumb-sep">/</span>
                      <span className="tp-crumb-dim">Backlog</span>
                      <span className="tp-crumb-sep">/</span>
                      <span className="tp-key-badge">{detail.key}</span>
                    </div>
                    <div className="tp-detail-actions">
                      {detail.url && (
                        <a
                          className="tp-action-btn"
                          href={detail.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          View in {ticketSourceLabel(project)}
                          <span className="tp-action-icon">↗</span>
                        </a>
                      )}
                      <span className="tp-action-btn tp-action-ghost">✎</span>
                    </div>
                  </div>
                  <h1 className="tp-title">{detail.summary}</h1>

                  {/* Primary execution header */}
                  <div className="tp-exec">
                    <div className="tp-exec-left">
                      <span className="tp-exec-icon">⚡</span>
                      <div>
                        <span className="tp-exec-title">
                          Ready for Autonomous Execution
                        </span>
                        <span className="tp-exec-sub">
                          Telemetry verified:{" "}
                          {skillNames.join(", ") || "skills matched"}
                        </span>
                      </div>
                    </div>
                    <button
                      className="tp-run-btn"
                      disabled={starting === detail.key || notReady}
                      title={
                        notReady
                          ? "Fix the project issues above first"
                          : undefined
                      }
                      onClick={() => startPipeline(detail.key)}
                    >
                      <span className="tp-run-btn-icon">✦</span>
                      {starting === detail.key
                        ? "Starting…"
                        : "Create Pipeline Run"}
                    </button>
                  </div>

                  {/* Metadata grid */}
                  <div className="tp-meta-grid">
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Status</span>
                      <div className="tp-meta-value">
                        <span className="tp-color-dot tp-dot-primary" />
                        {detail.status ?? "—"}
                      </div>
                    </div>
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Priority</span>
                      <div className="tp-meta-value">
                        {priorityMeta(detail.priority).icon && (
                          <span className="tp-prio">
                            {priorityMeta(detail.priority).icon}
                          </span>
                        )}
                        {detail.priority ?? "—"}
                      </div>
                    </div>
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Assignee</span>
                      <div className="tp-meta-value">
                        <span className="tp-avatar tp-avatar-sm">
                          {initials(detail.assignee)}
                        </span>
                        {detail.assignee ?? "Unassigned"}
                      </div>
                    </div>
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Reporter</span>
                      <div className="tp-meta-value">
                        <span className="tp-avatar tp-avatar-sm">
                          {initials(detail.reporter)}
                        </span>
                        {detail.reporter ?? "—"}
                      </div>
                    </div>
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Components</span>
                      <div className="tp-meta-value tp-meta-tags">
                        {detail.labels.length > 0
                          ? detail.labels.slice(0, 2).map((label) => (
                              <span className="tp-tag" key={label}>
                                {label}
                              </span>
                            ))
                          : "—"}
                      </div>
                    </div>
                    <div className="tp-meta-cell">
                      <span className="tp-meta-label">Issue Type</span>
                      <div className="tp-meta-value">
                        {detail.issue_type ?? "—"}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Description body */}
                <div className="tp-detail-card tp-description-card">
                  <div className="tp-section-head">
                    <span className="tp-section-icon">◈</span>
                    <h1>Details</h1>
                  </div>
                  <div className="tp-description-scroll" aria-label="Ticket business context and details">
                    {detailLoading && !detail.description ? (
                      <p className="muted">Loading description…</p>
                    ) : detail.description ? (
                      isManualGroupTicket(project, detail) ? (
                        <div
                          className="tp-desc tp-desc-rich"
                          dangerouslySetInnerHTML={{ __html: detail.description }}
                        />
                      ) : (
                        <div className="tp-desc tp-desc-markdown">
                          <MarkdownBody text={detail.description} />
                        </div>
                      )
                    ) : (
                      <div className="tp-desc">No description on this ticket.</div>
                    )}

                    {detail.attachments.length > 0 && (
                      <>
                        <h4 className="tp-subhead">
                          Attachments ({detail.attachments.length})
                        </h4>
                        <div className="tp-att-grid">
                          {detail.attachments.map((attachment) => (
                            <div className="tp-att" key={attachment.id}>
                              <span className="tp-att-icon">▤</span>
                              <div className="tp-att-info">
                                {attachment.url ? (
                                  <a
                                    className="tp-att-link"
                                    href={`/api/projects/${projectId}/tickets/${encodeURIComponent(detail.key)}/attachments/${encodeURIComponent(attachment.id)}`}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    {attachment.filename}
                                  </a>
                                ) : (
                                  <span className="tp-att-link">
                                    {attachment.filename}
                                  </span>
                                )}
                                {attachment.size !== null && (
                                  <span className="tp-att-size">
                                    {formatFileSize(attachment.size)}
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </>
                    )}

                    {detail.subtasks.length > 0 && (
                      <>
                        <h4 className="tp-subhead">
                          Subtasks ({detail.subtasks.length} linked)
                        </h4>
                        <div className="tp-subtable">
                          <div className="tp-subtable-head">
                            <span>Key</span>
                            <span>Summary</span>
                            <span className="tp-right-al">Status</span>
                          </div>
                          {detail.subtasks.map((s) => (
                            <div className="tp-subtable-row" key={s.key}>
                              <span className="tp-subkey">{s.key}</span>
                              <span className="tp-subsummary">{s.summary}</span>
                              <span
                                className={`tp-substatus ${/done/i.test(s.status ?? "") ? "tp-substatus-done" : ""}`}
                              >
                                {s.status ?? "—"}
                              </span>
                            </div>
                          ))}
                        </div>
                        <p className="muted small tp-subnote">
                          Running the pipeline below covers this ticket and all
                          subtasks above together - no need to run them
                          separately.
                        </p>
                      </>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Sticky pipeline footer */}
      {/* {!loading && tickets.length > 0 && selected && detail && (
        <div className="tp-footer">
          <div className="tp-footer-left">
            <span className="tp-footer-icon">⚙</span>
            <div>
              <div className="tp-footer-title">
                Maverick Agent Pipeline is ready
                <span className="tp-live-dot tp-dot-pulse" />
              </div>
              <p className="tp-footer-sub">
                {skillNames.length > 0
                  ? `Skills detected: ${skillNames.map((s) => (
                      <span key={s} className="tp-skill">
                        {s}
                      </span>
                    ))}`
                  : "Project skills will be applied at run time"}
              </p>
            </div>
          </div>
          <button
            className="tp-run-btn tp-run-btn-lg"
            disabled={starting === detail.key || notReady}
            title={notReady ? "Fix the project issues above first" : undefined}
            onClick={() => startPipeline(detail.key)}
          >
            <span className="tp-run-btn-icon">⚡</span>
            {starting === detail.key ? "Starting…" : "Create Pipeline Run"}
          </button>
        </div>
      )} */}
    </div>
  );
}
