import { useEffect, useState } from "react";
import { api, formatError } from "../api/client";
import type {
  ImportedTicket,
  ImportSource,
  Project,
  Ticket,
  TicketGroup,
} from "../api/types";

function sourceLabel(source: ImportSource): string {
  if (source === "group") return "Jira Group";
  if (source === "jira") return "Jira";
  return "Azure DevOps";
}

export function TicketImportPanel({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const jiraLinked = Boolean(project.jira_connection_name);
  const azureLinked = Boolean(project.azure_connection_name);

  const [source, setSource] = useState<ImportSource>(() =>
    jiraLinked ? "jira" : azureLinked ? "azure" : "group",
  );
  const [groupId, setGroupId] = useState("");
  const [groups, setGroups] = useState<TicketGroup[]>([]);
  const [jql, setJql] = useState("");
  const [candidates, setCandidates] = useState<Ticket[] | null>(null);
  const [allKeys, setAllKeys] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [imported, setImported] = useState<ImportedTicket[]>([]);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const loadImported = () => {
    api
      .listImportedTickets(project.id)
      .then(setImported)
      .catch(() => {});
  };

  useEffect(() => {
    setError(null);
    setInfo(null);
    setCandidates(null);
    setSelected(new Set());
    setAllKeys([]);
    loadImported();
    api
      .listTicketGroups()
      .then((rows) => {
        setGroups(rows);
        setGroupId((current) => current || rows[0]?.id || "");
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const browse = async () => {
    setLoading(true);
    setError(null);
    setInfo(null);
    try {
      const rows = await api.fetchImportableTickets(project.id, {
        source,
        ...(source === "group" ? { group_id: groupId } : {}),
        ...(source === "jira" && jql.trim() ? { jql: jql.trim() } : {}),
      });
      setCandidates(rows);
      setAllKeys(rows.map((t) => t.key));
      setSelected(new Set());
      setInfo(
        source === "group"
          ? `Group "${groups.find((g) => g.id === groupId)?.name ?? groupId}" has ${rows.length} ticket(s)`
          : `Found ${rows.length} ticket(s)`,
      );
    } catch (e) {
      setError(formatError(e));
      setCandidates(null);
      setAllKeys([]);
      setSelected(new Set());
    } finally {
      setLoading(false);
    }
  };

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === allKeys.length && allKeys.length > 0
        ? new Set<string>()
        : new Set(allKeys),
    );
  };

  const doImport = async () => {
    const keys = [...selected];
    if (keys.length === 0) {
      setError("Select at least one ticket to import");
      return;
    }
    setImporting(true);
    setError(null);
    setInfo(null);
    try {
      const result = await api.importTickets(project.id, {
        source,
        keys,
        ...(source === "group" ? { group_id: groupId } : {}),
      });
      setInfo(
        `Imported ${result.imported.length} ticket(s)${
          result.skipped.length > 0
            ? ` · ${result.skipped.length} already imported (skipped)`
            : ""
        }`,
      );
      setSelected(new Set());
      await loadImported();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setImporting(false);
    }
  };

  const hasCandidates = candidates !== null && candidates.length > 0;
  const importedCount = imported.length;

  return (
    <div className="tp-import">
      <div className="tp-import-top">
        <span className="tp-import-title">
          Import Tickets into {project.name}
        </span>
        <button
          type="button"
          className="tp-import-close"
          title="Close import panel"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <div className="tp-import-controls">
        <div className="tp-import-field">
          <label>Source</label>
          <div className="tp-import-source">
            <button
              type="button"
              className={`tp-import-opt ${
                source === "jira" ? "tp-import-opt-active" : ""
              }`}
              disabled={!jiraLinked}
              title={
                jiraLinked
                  ? undefined
                  : "This project has no Jira connection linked"
              }
              onClick={() => setSource("jira")}
            >
              {jiraLinked ? "Jira" : "Jira (not linked)"}
            </button>
            <button
              type="button"
              className={`tp-import-opt ${
                source === "azure" ? "tp-import-opt-active" : ""
              }`}
              disabled={!azureLinked}
              title={
                azureLinked
                  ? undefined
                  : "This project has no Azure DevOps connection linked"
              }
              onClick={() => setSource("azure")}
            >
              {azureLinked ? "Azure DevOps" : "Azure (not linked)"}
            </button>
            <button
              type="button"
              className={`tp-import-opt ${
                source === "group" ? "tp-import-opt-active" : ""
              }`}
              onClick={() => setSource("group")}
            >
              Jira Group
            </button>
          </div>
        </div>

        {source === "group" && (
          <div className="tp-import-field">
            <label>Ticket group</label>
            <div className="tp-select-wrap">
              <select
                className="tp-select"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
              >
                {groups.length === 0 && <option value="">No groups yet</option>}
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({g.ticket_count})
                  </option>
                ))}
              </select>
              <span className="tp-select-caret">▾</span>
            </div>
          </div>
        )}

        {source === "jira" && (
          <div className="tp-import-field tp-import-grow">
            <label>JQL filter (optional)</label>
            <input
              className="tp-search-input"
              placeholder="project = PGD AND status != Done"
              value={jql}
              onChange={(e) => setJql(e.target.value)}
            />
          </div>
        )}

        {source === "azure" && (
          <p className="tp-import-hint">
            Pulls work items from this project&apos;s Azure DevOps connection.
            Azure work items are referenced by numeric id.
          </p>
        )}

        <button
          type="button"
          className="btn"
          onClick={browse}
          disabled={loading || (source === "group" && !groupId)}
        >
          {loading ? "Loading…" : "Browse tickets"}
        </button>
      </div>

      {error && (
        <div className="banner banner-err" role="alert">
          {error}
        </div>
      )}
      {info && (
        <div className="banner banner-warn" role="status">
          {info}
        </div>
      )}

      {candidates !== null && (
        <div className="tp-import-lists">
          {hasCandidates ? (
            <>
              <div className="tp-import-summary-row">
                <span>
                  {candidates.length} ticket
                  {candidates.length === 1 ? "" : "s"} available ·{" "}
                  {selected.size} selected
                </span>
                <button type="button" className="tp-clear" onClick={toggleAll}>
                  {selected.size === allKeys.length
                    ? "Clear all"
                    : "Select all"}
                </button>
              </div>
              <div className="tp-import-candidates">
                {candidates.map((t) => (
                  <div className="tp-import-row" key={t.key}>
                    <label>
                      <input
                        type="checkbox"
                        checked={selected.has(t.key)}
                        onChange={() => toggle(t.key)}
                      />
                      <div className="tp-import-body">
                        <span className="tp-key-badge">{t.key}</span>
                        <span className="tp-import-summary">{t.summary}</span>
                        <span className="tp-import-meta">
                          {[
                            t.issue_type,
                            t.priority,
                            t.assignee ?? "Unassigned",
                          ]
                            .filter(Boolean)
                            .join(" · ") || "No metadata"}
                        </span>
                      </div>
                    </label>
                  </div>
                ))}
              </div>
              <div>
                <button
                  type="button"
                  className="tp-run-btn"
                  onClick={doImport}
                  disabled={importing || selected.size === 0}
                >
                  <span className="tp-run-btn-icon">⇪</span>
                  {importing
                    ? "Importing…"
                    : `Import selected (${selected.size}) as ${sourceLabel(source)}`}
                </button>
              </div>
            </>
          ) : (
            <p className="muted small">No tickets found for this source.</p>
          )}
        </div>
      )}

      <div className="tp-import-imported-head">
        <span className="tp-import-title">Imported into this project</span>
        {importedCount > 0 && (
          <span className="mi-tag">{importedCount} imported</span>
        )}
      </div>
      {imported.length === 0 ? (
        <p className="muted small tp-import-hint">
          Nothing imported yet. Browse a source above and import the tickets you
          want to run pipelines against.
        </p>
      ) : (
        <div className="tp-import-imported">
          {imported.map((t) => (
            <span className="tp-import-chip" key={t.id}>
              <span className="tp-import-chip-key">{t.key}</span>
              <span className="tp-import-chip-src">
                {sourceLabel(t.source)}
                {t.source_connection ? ` · ${t.source_connection}` : ""}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
