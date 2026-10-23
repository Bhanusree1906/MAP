import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, formatError } from "../api/client";
import { Breadcrumb } from "../components/Breadcrumb";
import { LiveActivityLog } from "../components/LiveActivityLog";
import {
  ProjectFields,
  draftFromProject,
  type ProjectDraft,
} from "../components/ProjectFields";
import { readinessHref } from "../readiness";
import type {
  AgentRunEvent,
  AzureConnection,
  JiraConnection,
  Project,
  SalesforceConnection,
  Skill,
  SkillPhase,
  TicketGroup,
} from "../api/types";

const PHASE_LABELS: Record<SkillPhase, string> = {
  both: "Both phases",
  planning: "Planning only",
  implementation: "Implementation only",
};

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [connections, setConnections] = useState<JiraConnection[]>([]);
  const [azureConnections, setAzureConnections] = useState<AzureConnection[]>(
    [],
  );
  const [sfConnections, setSfConnections] = useState<SalesforceConnection[]>(
    [],
  );
  const [ticketGroups, setTicketGroups] = useState<TicketGroup[]>([]);
  const [githubConnections, setGithubConnections] = useState<
    { name: string; login: string | null }[]
  >([]);
  const [catalog, setCatalog] = useState<Skill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProjectDraft | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [editItems, setEditItems] = useState<
    { slug: string; phase: SkillPhase }[]
  >([]);
  const [memoryDraft, setMemoryDraft] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [uploadingContext, setUploadingContext] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(false);
  const [bootstrapNote, setBootstrapNote] = useState<string | null>(null);
  const [bootstrapJobId, setBootstrapJobId] = useState<string | null>(null);
  const [bootstrapEvents, setBootstrapEvents] = useState<AgentRunEvent[]>([]);
  const bootstrapSeqRef = useRef(0);
  const bootstrapDoneRef = useRef(false);

  const reload = () => {
    api
      .getProject(projectId)
      .then((p) => {
        setProject(p);
        setDraft(draftFromProject(p));
        setEditItems(p.skills.map((s) => ({ slug: s.slug, phase: s.phase })));
      })
      .catch((e) => setError(formatError(e)));
    api
      .listConnections()
      .then(setConnections)
      .catch(() => {});
    api
      .listAzureConnections()
      .then(setAzureConnections)
      .catch(() => {});
    api
      .listSalesforceConnections()
      .then(setSfConnections)
      .catch(() => {});
    api
      .listTicketGroups()
      .then(setTicketGroups)
      .catch(() => {});
    api
      .getGitHubStatus()
      .then((s) => setGithubConnections(s.connections))
      .catch(() => {});
    api
      .listSkills()
      .then(setCatalog)
      .catch(() => {});
    api
      .getProjectMemory(projectId)
      .then((memory) => setMemoryDraft(memory?.markdown ?? ""))
      .catch(() => setMemoryDraft(""));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (!location.hash) return;
    const reduce = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    document.querySelector(location.hash)?.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "start",
    });
  }, [location.hash, project]);

  useEffect(() => {
    if (!bootstrapJobId) return undefined;
    let cancelled = false;

    const poll = async () => {
      try {
        const [events, job] = await Promise.all([
          api.getSalesforceBootstrapEvents(
            projectId,
            bootstrapJobId,
            bootstrapSeqRef.current,
          ),
          api.getSalesforceBootstrapJob(projectId, bootstrapJobId),
        ]);
        if (cancelled) return "done";
        if (events.length > 0) {
          bootstrapSeqRef.current = events[events.length - 1].seq;
          setBootstrapEvents((prev) => [...prev, ...events]);
        }
        if (job.status === "succeeded" || job.status === "failed") {
          const rest = await api.getSalesforceBootstrapEvents(
            projectId,
            bootstrapJobId,
            bootstrapSeqRef.current,
          );
          if (!cancelled && rest.length > 0) {
            bootstrapSeqRef.current = rest[rest.length - 1].seq;
            setBootstrapEvents((prev) => [...prev, ...rest]);
          }
          if (job.status === "succeeded") {
            if (!bootstrapDoneRef.current) {
              bootstrapDoneRef.current = true;
              setBootstrapNote(job.result?.message ?? "Bootstrap finished.");
              setBootstrapping(false);
              reload();
            }
          } else if (!bootstrapDoneRef.current) {
            bootstrapDoneRef.current = true;
            setError(job.error || "Bootstrap failed");
            setBootstrapping(false);
          }
          return "done";
        }
      } catch {
        // Transient poll failure — next tick retries.
      }
      return "running";
    };

    let intervalId: number | undefined;
    const start = async () => {
      const state = await poll();
      if (cancelled || state === "done") return;
      intervalId = window.setInterval(() => {
        void poll().then((next) => {
          if (next === "done" && intervalId !== undefined)
            window.clearInterval(intervalId);
        });
      }, 1200);
    };
    void start();
    return () => {
      cancelled = true;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrapJobId, projectId]);

  const saveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    setSavingSettings(true);
    setError(null);
    try {
      const updated = await api.createOrUpdateProject({
        name: draft.name,
        description: draft.description.trim() || null,
        repository_url: draft.repoUrl,
        default_branch: draft.branch,
        jira_connection_name:
          draft.ticketSource === "jira" ? draft.connectionName || "" : "",
        azure_connection_name:
          draft.ticketSource === "azure" ? draft.connectionName || "" : "",
        ticket_group_name:
          draft.ticketSource === "group"
            ? draft.ticketGroupName || null
            : null,
        sandbox_kind: draft.sandboxKind,
        salesforce_connection_name:
          draft.sandboxKind === "salesforce"
            ? draft.sfConnectionName || ""
            : "",
        github_connection_name:
          draft.gitSource === "github" ? draft.githubConnectionName || "" : "",
        azure_git_connection_name:
          draft.gitSource === "azure" ? draft.azureGitConnectionName || "" : "",
      });
      setProject(updated);
      setDraft(draftFromProject(updated));
    } catch (err) {
      setError(formatError(err));
    } finally {
      setSavingSettings(false);
    }
  };

  const saveConfig = async () => {
    setSavingConfig(true);
    setError(null);
    try {
      await api.setProjectSkills(projectId, editItems);
      if (memoryDraft.trim())
        await api.putProjectMemory(projectId, memoryDraft);
      reload();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setSavingConfig(false);
    }
  };

  const uploadContext = async (file: File) => {
    setUploadingContext(true);
    setError(null);
    try {
      await api.uploadProjectContext(projectId, file);
      reload();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setUploadingContext(false);
    }
  };

  const removeProject = async () => {
    if (!project) return;
    if (
      !confirm(
        `Delete project ${project.name}? This removes its runs, artifacts, skills, and memory too.`,
      )
    )
      return;
    try {
      await api.deleteProject(project.id);
      navigate("/");
    } catch (err) {
      setError(formatError(err));
    }
  };

  if (!project || !draft) {
    return error ? (
      <p className="error-text">{error}</p>
    ) : (
      <p className="muted">Loading project…</p>
    );
  }

  const notReady = project.readiness_issues.length > 0;

  return (
    <div>
      <div className="page-header">
        <div>
          <Breadcrumb
            items={[{ label: "Projects", to: "/" }, { label: project.name }]}
          />
          <h2>
            {project.name}{" "}
            {notReady ? (
              <span className="badge badge-err">
                {project.readiness_issues.length} issue
                {project.readiness_issues.length > 1 ? "s" : ""}
              </span>
            ) : (
              <span className="badge badge-ok">Ready</span>
            )}
          </h2>
          {project.description && <p>{project.description}</p>}
          <p className="mono small muted">{project.repository_url}</p>
        </div>
        <div className="page-actions">
          <Link className="btn" to={`/projects/${project.id}/tickets`}>
            Pick a ticket
          </Link>
          <button type="button" className="btn-danger" onClick={removeProject}>
            Delete
          </button>
        </div>
      </div>

      {error && <div className="banner banner-err">{error}</div>}

      {notReady && (
        <div className="banner banner-warn">
          Fix these before starting a pipeline.
          <ul className="issue-list">
            {project.readiness_issues.map((issue) => (
              <li key={issue}>
                <Link to={readinessHref(project.id, issue)}>{issue}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form className="card" id="settings" onSubmit={saveSettings}>
        <h3>Settings</h3>
        <p className="muted small">
          The name is how this project is stored. Change the repo, branch, and
          connections here.
        </p>
        <ProjectFields
          draft={draft}
          onChange={setDraft}
          connections={connections}
          azureConnections={azureConnections}
          sfConnections={sfConnections}
          githubConnections={githubConnections}
          ticketGroups={ticketGroups}
          nameLocked
        />
        <button type="submit" disabled={savingSettings}>
          {savingSettings ? "Saving…" : "Save settings"}
        </button>
      </form>

      {project.sandbox_kind === "salesforce" && (
        <div className="card" id="org-catalog">
          <h3>Salesforce bootstrap</h3>
          <p className="muted small">
            Dumps the connected org into project memory and, if the GitHub repo
            has no sfdx-project.json, runs official{" "}
            <code>sf project generate</code> and pushes that tree to the default
            branch. Run once before the first ticket so planning and every later
            ticket start from real API names.
          </p>
          <span
            className={`badge ${project.has_memory ? "badge-ok" : "badge-err"}`}
          >
            {project.has_memory ? "Org catalog saved" : "No org catalog"}
          </span>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button
              type="button"
              disabled={bootstrapping}
              onClick={async () => {
                setBootstrapping(true);
                setError(null);
                setBootstrapNote(null);
                setBootstrapEvents([]);
                bootstrapSeqRef.current = 0;
                bootstrapDoneRef.current = false;
                setBootstrapJobId(null);
                try {
                  const job = await api.bootstrapSalesforce(project.id);
                  setBootstrapJobId(job.job_id);
                } catch (err) {
                  setError(formatError(err));
                  setBootstrapping(false);
                }
              }}
            >
              {bootstrapping ? "Bootstrapping…" : "Bootstrap Salesforce"}
            </button>
          </div>
          {(bootstrapping || bootstrapEvents.length > 0) && (
            <LiveActivityLog
              title={bootstrapping ? "Live activity" : "Activity log"}
              events={bootstrapEvents}
              isLive={bootstrapping}
            />
          )}
          {bootstrapNote && <p className="muted small">{bootstrapNote}</p>}
        </div>
      )}

      <div className="card" id="skills">
        <h3>Skills &amp; memory</h3>
        <h4>Codex skills</h4>
        {catalog.length === 0 && (
          <p className="muted small">
            No skills in the catalog yet. Publish one on the{" "}
            <Link to="/skills">Skills</Link> page.
          </p>
        )}
        {catalog
          .filter((s) => s.status === "active")
          .map((s) => {
            const item = editItems.find((i) => i.slug === s.slug);
            return (
              <div
                key={s.slug}
                className="field"
                style={{
                  marginBottom: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <label style={{ flex: 1 }}>
                  <input
                    type="checkbox"
                    checked={item !== undefined}
                    onChange={() =>
                      setEditItems((prev) =>
                        prev.some((i) => i.slug === s.slug)
                          ? prev.filter((i) => i.slug !== s.slug)
                          : [...prev, { slug: s.slug, phase: "both" }],
                      )
                    }
                  />{" "}
                  {s.name}{" "}
                  <span className="mono small">
                    ({s.slug}@{s.active_version})
                  </span>
                </label>
                {item && (
                  <select
                    value={item.phase}
                    onChange={(e) =>
                      setEditItems((prev) =>
                        prev.map((i) =>
                          i.slug === s.slug
                            ? { ...i, phase: e.target.value as SkillPhase }
                            : i,
                        ),
                      )
                    }
                  >
                    {(Object.keys(PHASE_LABELS) as SkillPhase[]).map(
                      (phase) => (
                        <option key={phase} value={phase}>
                          {PHASE_LABELS[phase]}
                        </option>
                      ),
                    )}
                  </select>
                )}
              </div>
            );
          })}
        <h4>Project memory</h4>
        <p className="muted small">
          Repo layout, conventions, and how to run tests.
        </p>
        <label className="field" style={{ display: "block", marginBottom: 12 }}>
          Upload Agents.MD context (.zip)
          <input
            type="file"
            accept=".zip"
            disabled={uploadingContext}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void uploadContext(file);
            }}
          />
        </label>
        <textarea
          value={memoryDraft}
          onChange={(e) => setMemoryDraft(e.target.value)}
          rows={8}
          placeholder="# Codebase notes"
        />
        <div className="btn-row">
          <button type="button" disabled={savingConfig} onClick={saveConfig}>
            {savingConfig ? "Saving…" : "Save skills & memory"}
          </button>
          {project.has_memory && (
            <button
              type="button"
              className="btn-secondary"
              disabled={savingConfig}
              onClick={async () => {
                setSavingConfig(true);
                setError(null);
                try {
                  await api.clearProjectMemory(projectId);
                  setMemoryDraft("");
                  reload();
                } catch (err) {
                  setError(formatError(err));
                } finally {
                  setSavingConfig(false);
                }
              }}
            >
              Clear memory
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
