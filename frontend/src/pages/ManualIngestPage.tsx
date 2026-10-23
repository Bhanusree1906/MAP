import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatError } from "../api/client";
import { Breadcrumb, type Crumb } from "../components/Breadcrumb";
import { usePolling } from "../hooks/usePolling";
import type {
  ManualTicket,
  ManualTicketStatus,
  ManualWorkItem,
  ManualWorkItemStatus,
  PipelineRun,
  Project,
  TicketGroup,
} from "../api/types";

const ISSUE_TYPES = ["Story", "Task", "Sec Fix", "Spike"];
const PRIORITIES = ["P3 Low", "P2 Med", "P1 High", "P0 Blocker"];
const TICKET_STATUSES = [
  { value: "todo", label: "To Do" },
  { value: "progress", label: "In Progress" },
  { value: "agentcompleted", label: "Agent Completed" },
  { value: "completed", label: "Completed" },
] as const;

const SELECTED_GROUP_KEY = "mi.selected.group.v1";
const SELECTED_PROJECT_KEY = "mi.manual.project.v1";

const WORK_ITEM_STATUSES: { value: ManualWorkItemStatus; label: string }[] = [
  { value: "todo", label: "To Do" },
  { value: "progress", label: "In Progress" },
  { value: "agentcompleted", label: "Agent Completed" },
  { value: "completed", label: "Completed" },
];

function loadSavedProjectId(): string {
  try {
    return localStorage.getItem(SELECTED_PROJECT_KEY) ?? "";
  } catch {
    return "";
  }
}

function loadSavedGroupId(): string {
  try {
    const raw = localStorage.getItem(SELECTED_GROUP_KEY);
    return raw && typeof raw === "string" ? raw : "";
  } catch {
    return "";
  }
}

function formatFileSize(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function ManualIngestPage() {
  return <GroupManualIngest />;
}

function GroupManualIngest() {
  const { data: groupsData, error: groupsError, refetch: reloadGroups } =
    usePolling(() => api.listTicketGroups(), 30000, true, []);
  const { data: projectsData, error: projectsError } = usePolling(
    () => api.listProjects(),
    30000,
    true,
    [],
  );
  const groups = groupsData ?? [];
  const projects = projectsData ?? [];
  const [groupId, setGroupId] = useState(loadSavedGroupId);
  const [projectId, setProjectId] = useState(loadSavedProjectId);
  const group = groups.find((item) => item.id === groupId) ?? groups[0] ?? null;
  const project = projects.find((item) => item.id === projectId) ?? projects[0] ?? null;
  const [title, setTitle] = useState("");
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupDescription, setNewGroupDescription] = useState("");
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<ManualTicketStatus | "all">("all");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!group) return;
    setGroupId(group.id);
    try { localStorage.setItem(SELECTED_GROUP_KEY, group.id); } catch { /* optional */ }
  }, [group]);

  useEffect(() => {
    if (!project) return;
    setProjectId(project.id);
    try { localStorage.setItem(SELECTED_PROJECT_KEY, project.id); } catch { /* optional */ }
  }, [project]);

  const clearEditor = () => {
    setTitle("");
    setEditingId(null);
    if (editorRef.current) editorRef.current.innerHTML = "";
  };

  const createGroup = async () => {
    const name = newGroupName.trim();
    if (!name) {
      setError("Group name is required");
      return;
    }
    setCreatingGroup(true);
    setError(null);
    try {
      const created = await api.createTicketGroup(
        name,
        newGroupDescription.trim() || undefined,
      );
      await reloadGroups();
      setGroupId(created.id);
      setNewGroupName("");
      setNewGroupDescription("");
      setShowCreateGroup(false);
      setToast(`${created.name} created`);
    } catch (e) {
      setError(formatError(e));
    } finally {
      setCreatingGroup(false);
    }
  };

  const exec = (command: string) => {
    editorRef.current?.focus();
    document.execCommand(command);
  };

  const save = async () => {
    if (!group) return setError("Create or select a Jira Group first");
    if (!title.trim()) return setError("Ticket title is required");
    const bodyHtml = editorRef.current?.innerHTML ?? "";
    setSaving(true);
    setError(null);
    try {
      if (editingId) {
        await api.updateRichManualTicket(group.id, editingId, title, bodyHtml);
        setToast(`${editingId} updated in ${group.name}`);
      } else {
        const ticket = await api.createRichManualTicket(group.id, title, bodyHtml);
        setToast(`${ticket.ticket_id} saved in ${group.name}`);
      }
      clearEditor();
      await reloadGroups();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSaving(false);
    }
  };

  const edit = (ticket: ManualTicket) => {
    setEditingId(ticket.ticket_id);
    setTitle(ticket.summary);
    if (editorRef.current) editorRef.current.innerHTML = ticket.description ?? "";
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const updateStatus = async (ticket: ManualTicket, status: ManualTicketStatus) => {
    if (!group) return;
    try {
      await api.updateManualTicketStatus(group.id, ticket.ticket_id, status);
      await reloadGroups();
    } catch (e) { setError(formatError(e)); }
  };

  const startRun = async (ticket: ManualTicket) => {
    if (!project) return setError("Select a project before starting a run");
    try {
      await api.createRun(project.id, ticket.ticket_id);
      setToast(`${ticket.ticket_id} sent to planning`);
      await reloadGroups();
    } catch (e) { setError(formatError(e)); }
  };

  const remove = async (ticket: ManualTicket) => {
    if (!group || !window.confirm(`Delete ${ticket.ticket_id}?`)) return;
    try {
      await api.deleteManualTicket(group.id, ticket.ticket_id);
      await reloadGroups();
    } catch (e) { setError(formatError(e)); }
  };

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const tickets = group?.tickets ?? [];
  const visibleTickets = filter === "all" ? tickets : tickets.filter((ticket) => ticket.status === filter);
  const count = (status: ManualTicketStatus) => tickets.filter((ticket) => ticket.status === status).length;

  return (
    <div className="mi mi-work-items">
      <header className="mi-head">
        <div className="mi-head-main">
          <Breadcrumb items={[{ label: "Maverick", to: "/" }, { label: "Manual Ingest" }]} />
          <div className="mi-title-row"><h1 className="mi-title">Manual Jira Group Editor</h1><span className="mi-pill"><span className="mi-pill-dot" /> Reusable Jira Group</span></div>
          <p className="mi-subtitle">Create tickets once in a group, then attach that group to any project as Jira Group, Jira, or Azure DevOps.</p>
        </div>
        <div className="mi-head-actions">
          <label className="mi-project-picker"><span>Ticket group</span><select value={group?.id ?? ""} onChange={(event) => setGroupId(event.target.value)}><option value="">Select group</option>{groups.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.ticket_count})</option>)}</select></label>
          <button type="button" className="mi-btn" onClick={() => setShowCreateGroup((visible) => !visible)}>＋ Create group</button>
          <label className="mi-project-picker"><span>Run project</span><select value={project?.id ?? ""} onChange={(event) => setProjectId(event.target.value)}><option value="">Select project</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <button type="button" className="mi-btn" onClick={clearEditor}>New ticket</button>
        </div>
      </header>
      {(error || groupsError || projectsError) && <div className="banner banner-err mi-banner" role="alert">{error ?? formatError(groupsError ?? projectsError)}</div>}
      {showCreateGroup && <section className="mi-card mi-create-group-card">
        <div className="mi-card-head"><div className="mi-card-title"><span className="mi-card-ico mi-ico-blue">＋</span><span>Create a reusable Jira Group</span></div><button type="button" className="mi-btn" onClick={() => setShowCreateGroup(false)}>Cancel</button></div>
        <div className="mi-create-group-fields">
          <input className="mi-input" placeholder="Group name" value={newGroupName} onChange={(event) => setNewGroupName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createGroup(); }} />
          <input className="mi-input" placeholder="Description (optional)" value={newGroupDescription} onChange={(event) => setNewGroupDescription(event.target.value)} />
          <button type="button" className="mi-btn mi-btn-primary" onClick={() => void createGroup()} disabled={creatingGroup}>{creatingGroup ? "Creating..." : "Create group"}</button>
        </div>
      </section>}
      <section className="mi-work-editor mi-card">
        <div className="mi-card-head"><div className="mi-card-title"><span className="mi-card-ico mi-ico-blue">◇</span><span>{editingId ? `Edit ${editingId}` : "New Jira Group ticket"}</span></div><span className="mi-tag">{group ? `Saving to ${group.name}` : "Select a group"}</span></div>
        <input className="mi-input mi-work-title" placeholder="Ticket title" value={title} onChange={(event) => setTitle(event.target.value)} />
        <div className="mi-editor-toolbar" role="toolbar" aria-label="Formatting"><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("bold")} aria-label="Bold"><strong>B</strong></button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("italic")} aria-label="Italic"><em>I</em></button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("insertUnorderedList")} aria-label="Bulleted list">• List</button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("insertOrderedList")} aria-label="Numbered list">1. List</button><span className="mi-editor-divider" /><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyLeft")} aria-label="Align left">Left</button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyCenter")} aria-label="Align center">Center</button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyRight")} aria-label="Align right">Right</button></div>
        <div ref={editorRef} className="mi-rich-editor" contentEditable role="textbox" aria-label="Ticket specification" data-placeholder="Write the objective, requirements, acceptance criteria, and guardrails..." suppressContentEditableWarning />
        <div className="mi-form-actions"><button type="button" className="mi-btn mi-btn-primary" onClick={save} disabled={saving || !group}>{saving ? "Saving..." : editingId ? "Update ticket" : "Save ticket"}</button>{editingId && <button type="button" className="mi-btn" onClick={clearEditor}>Cancel edit</button>}</div>
      </section>
      <section className="mi-card mi-monitor-card">
        <div className="mi-card-head"><div className="mi-card-title"><span className="mi-card-ico mi-ico-violet">▤</span><span>{group ? `${group.name} ticket monitor` : "Ticket monitor"}</span></div><span className="mi-tag">{tickets.length} ticket{tickets.length === 1 ? "" : "s"}</span></div>
        <div className="mi-status-tabs" role="tablist" aria-label="Ticket status filter"><button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All <b>{tickets.length}</b></button>{TICKET_STATUSES.map((status) => <button key={status.value} type="button" className={filter === status.value ? "active" : ""} onClick={() => setFilter(status.value as ManualTicketStatus)}>{status.label} <b>{count(status.value as ManualTicketStatus)}</b></button>)}</div>
        {visibleTickets.length === 0 ? <p className="mi-group-empty">No tickets in this group view.</p> : <div className="mi-work-table-wrap"><table className="mi-work-table"><thead><tr><th>ID</th><th>Ticket</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{visibleTickets.map((ticket) => <tr key={ticket.id}><td className="mi-ticket-key">{ticket.ticket_id}</td><td><strong>{ticket.summary}</strong><span className="mi-work-preview">{stripHtml(ticket.description ?? "").slice(0, 120)}</span></td><td><select className={`mi-status-select mi-status-${ticket.status}`} value={ticket.status} onChange={(event) => updateStatus(ticket, event.target.value as ManualTicketStatus)}>{TICKET_STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}</select></td><td>{new Date(ticket.updated_at).toLocaleString()}</td><td className="mi-ticket-actions"><button type="button" className="mi-ticket-btn" onClick={() => edit(ticket)}>Edit</button>{ticket.status === "todo" && <button type="button" className="mi-ticket-btn mi-ticket-btn-primary" onClick={() => startRun(ticket)}>Run</button>}<button type="button" className="mi-ticket-btn mi-ticket-btn-danger" onClick={() => remove(ticket)}>Delete</button></td></tr>)}</tbody></table></div>}
      </section>
      {toast && <div className="mi-toast" role="status">{toast}</div>}
    </div>
  );
}

function stripHtml(value: string): string {
  const element = document.createElement("div");
  element.innerHTML = value;
  return element.textContent ?? "";
}

export function StandaloneManualIngest() {
  const { data: projectsData, error: projectsError } = usePolling(
    () => api.listProjects(),
    30000,
    true,
    [],
  );
  const projects: Project[] = projectsData ?? [];
  const [projectId, setProjectId] = useState(loadSavedProjectId);
  const project = projects.find((item) => item.id === projectId) ?? projects[0] ?? null;
  const [items, setItems] = useState<ManualWorkItem[]>([]);
  const [filter, setFilter] = useState<ManualWorkItemStatus | "all">("all");
  const [title, setTitle] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  const loadItems = async (selectedProjectId = project?.id) => {
    if (!selectedProjectId) {
      setItems([]);
      return;
    }
    try {
      setItems(await api.listManualWorkItems(selectedProjectId));
      setError(null);
    } catch (e) {
      setError(formatError(e));
    }
  };

  useEffect(() => {
    if (!project) return;
    setProjectId(project.id);
    try {
      localStorage.setItem(SELECTED_PROJECT_KEY, project.id);
    } catch {
      // Project selection still works when localStorage is unavailable.
    }
    void loadItems(project.id);
  }, [project]);

  const clearEditor = () => {
    setTitle("");
    setEditingId(null);
    if (editorRef.current) editorRef.current.innerHTML = "";
  };

  const exec = (command: string) => {
    editorRef.current?.focus();
    document.execCommand(command);
  };

  const save = async () => {
    if (!project) return setError("Select a project first");
    if (!title.trim()) return setError("Ticket title is required");
    const bodyHtml = editorRef.current?.innerHTML ?? "";
    setSaving(true);
    setError(null);
    try {
      if (editingId) {
        await api.updateManualWorkItem(project.id, editingId, title, bodyHtml);
        setToast(`${editingId} updated`);
      } else {
        const item = await api.createManualWorkItem(project.id, title, bodyHtml);
        setToast(`${item.public_id} saved`);
      }
      clearEditor();
      await loadItems(project.id);
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSaving(false);
    }
  };

  const edit = (item: ManualWorkItem) => {
    setEditingId(item.public_id);
    setTitle(item.title);
    if (editorRef.current) editorRef.current.innerHTML = item.body_html;
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const updateStatus = async (item: ManualWorkItem, status: ManualWorkItemStatus) => {
    if (!project) return;
    try {
      await api.updateManualWorkItemStatus(project.id, item.public_id, status);
      await loadItems(project.id);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const startRun = async (item: ManualWorkItem) => {
    if (!project) return;
    try {
      await api.createRun(project.id, item.public_id);
      setToast(`${item.public_id} sent to planning`);
      await loadItems(project.id);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const remove = async (item: ManualWorkItem) => {
    if (!project || !window.confirm(`Delete ${item.public_id}?`)) return;
    try {
      await api.deleteManualWorkItem(project.id, item.public_id);
      await loadItems(project.id);
    } catch (e) {
      setError(formatError(e));
    }
  };

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const visibleItems = filter === "all" ? items : items.filter((item) => item.status === filter);
  const count = (status: ManualWorkItemStatus) => items.filter((item) => item.status === status).length;

  return (
    <div className="mi mi-work-items">
      <header className="mi-head">
        <div className="mi-head-main">
          <Breadcrumb items={[{ label: "Maverick", to: "/" }, { label: "Manual Ingest" }]} />
          <div className="mi-title-row">
            <h1 className="mi-title">Manual Ticket Editor</h1>
            <span className="mi-pill"><span className="mi-pill-dot" /> Local work queue</span>
          </div>
          <p className="mi-subtitle">
            Write the ticket specification, save it once, and monitor its progress through the pipeline.
          </p>
        </div>
        <div className="mi-head-actions">
          <label className="mi-project-picker">
            <span>Project</span>
            <select value={project?.id ?? ""} onChange={(event) => setProjectId(event.target.value)}>
              <option value="">Select project</option>
              {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <button type="button" className="mi-btn" onClick={clearEditor}>New ticket</button>
        </div>
      </header>

      {(error || projectsError) && <div className="banner banner-err mi-banner" role="alert">{error ?? formatError(projectsError)}</div>}
      <section className="mi-work-editor mi-card">
        <div className="mi-card-head">
          <div className="mi-card-title"><span className="mi-card-ico mi-ico-blue">◇</span><span>{editingId ? `Edit ${editingId}` : "New ticket"}</span></div>
          <span className="mi-tag">Saved tickets get a generated ID</span>
        </div>
        <input className="mi-input mi-work-title" placeholder="Ticket title" value={title} onChange={(event) => setTitle(event.target.value)} />
        <div className="mi-editor-toolbar" role="toolbar" aria-label="Formatting">
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("bold")} aria-label="Bold"><strong>B</strong></button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("italic")} aria-label="Italic"><em>I</em></button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("insertUnorderedList")} aria-label="Bulleted list">• List</button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("insertOrderedList")} aria-label="Numbered list">1. List</button>
          <span className="mi-editor-divider" />
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyLeft")} aria-label="Align left">Left</button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyCenter")} aria-label="Align center">Center</button>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => exec("justifyRight")} aria-label="Align right">Right</button>
        </div>
        <div ref={editorRef} className="mi-rich-editor" contentEditable role="textbox" aria-label="Ticket specification" data-placeholder="Write the objective, requirements, acceptance criteria, and guardrails..." suppressContentEditableWarning />
        <div className="mi-form-actions">
          <button type="button" className="mi-btn mi-btn-primary" onClick={save} disabled={saving || !project}>{saving ? "Saving..." : editingId ? "Update ticket" : "Save ticket"}</button>
          {editingId && <button type="button" className="mi-btn" onClick={clearEditor}>Cancel edit</button>}
        </div>
      </section>

      <section className="mi-card mi-monitor-card">
        <div className="mi-card-head">
          <div className="mi-card-title"><span className="mi-card-ico mi-ico-violet">▤</span><span>Ticket monitor</span></div>
          <span className="mi-tag">{items.length} ticket{items.length === 1 ? "" : "s"}</span>
        </div>
        <div className="mi-status-tabs" role="tablist" aria-label="Ticket status filter">
          <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All <b>{items.length}</b></button>
          {WORK_ITEM_STATUSES.map((status) => <button key={status.value} type="button" className={filter === status.value ? "active" : ""} onClick={() => setFilter(status.value)}>{status.label} <b>{count(status.value)}</b></button>)}
        </div>
        {visibleItems.length === 0 ? <p className="mi-group-empty">No tickets in this view.</p> : <div className="mi-work-table-wrap"><table className="mi-work-table"><thead><tr><th>ID</th><th>Ticket</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{visibleItems.map((item) => <tr key={item.id}><td className="mi-ticket-key">{item.public_id}</td><td><strong>{item.title}</strong><span className="mi-work-preview">{item.body_text.slice(0, 120)}</span></td><td><select className={`mi-status-select mi-status-${item.status}`} value={item.status} onChange={(event) => updateStatus(item, event.target.value as ManualWorkItemStatus)}>{WORK_ITEM_STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}</select></td><td>{new Date(item.updated_at).toLocaleString()}</td><td className="mi-ticket-actions"><button type="button" className="mi-ticket-btn" onClick={() => edit(item)}>Edit</button>{item.status === "todo" && <button type="button" className="mi-ticket-btn mi-ticket-btn-primary" onClick={() => startRun(item)}>Run</button>}<button type="button" className="mi-ticket-btn mi-ticket-btn-danger" onClick={() => remove(item)}>Delete</button></td></tr>)}</tbody></table></div>}
      </section>
      {toast && <div className="mi-toast" role="status">{toast}</div>}
    </div>
  );
}

export function LegacyManualIngestPage() {
  const {
    data: groupsData,
    error: groupsError,
    refetch: reloadGroups,
  } = usePolling(() => api.listTicketGroups(), 30000, true, []);
  const { data: runsData } = usePolling(() => api.listRuns(), 30000, true, []);

  const groups: TicketGroup[] = groupsData ?? [];
  const runs = runsData ?? [];

  const [selectedGroupId, setSelectedGroupId] =
    useState<string>(loadSavedGroupId);
  const selectedGroup =
    groups.find((g) => g.id === selectedGroupId) ?? groups[0] ?? null;

  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupDesc, setNewGroupDesc] = useState("");
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);

  const [ticketId, setTicketId] = useState("");
  const [ticketTitle, setTicketTitle] = useState("");
  const [issueType, setIssueType] = useState<string>("Story");
  const [priority, setPriority] = useState<string>("P2 Med");
  const [assignee, setAssignee] = useState("");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [savingTicket, setSavingTicket] = useState(false);
  const [ticketError, setTicketError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedGroup) {
      setSelectedGroupId("");
      return;
    }
    try {
      localStorage.setItem(SELECTED_GROUP_KEY, selectedGroup.id);
    } catch {
      // localStorage may be unavailable (private mode) - selection still works.
    }
  }, [selectedGroup]);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(id);
  }, [toast]);

  const crumbs: Crumb[] = [
    { label: "Maverick", to: "/" },
    { label: "Ticket Ingestion", to: "/" },
    { label: "Manual Ticket Dispatch" },
  ];

  const recentRuns = recentDispatches(runs);

  const handleCreateGroup = async () => {
    const name = newGroupName.trim();
    if (!name) {
      setGroupError("Group name is required");
      return;
    }
    setCreatingGroup(true);
    setGroupError(null);
    try {
      const group = await api.createTicketGroup(
        name,
        newGroupDesc.trim() || undefined,
      );
      await reloadGroups();
      setNewGroupName("");
      setNewGroupDesc("");
      setSelectedGroupId(group.id);
      setToast(`Ticket group ${group.name} created`);
    } catch (e) {
      setGroupError(formatError(e));
    } finally {
      setCreatingGroup(false);
    }
  };

  const handleDeleteGroup = async (group: TicketGroup) => {
    try {
      await api.deleteTicketGroup(group.id);
      if (selectedGroupId === group.id) setSelectedGroupId("");
      await reloadGroups();
      setToast(`Group ${group.name} deleted`);
    } catch (e) {
      setGroupError(formatError(e));
    }
  };

  const handleSaveTicket = async () => {
    if (!selectedGroup) {
      setTicketError("Create or select a ticket group first");
      return;
    }
    const id = ticketId.trim();
    const summary = ticketTitle.trim();
    if (!id) {
      setTicketError("Ticket ID is required");
      return;
    }
    if (!summary) {
      setTicketError("Ticket summary is required");
      return;
    }
    if (file && file.size > 25 * 1024 * 1024) {
      setTicketError("Attachment exceeds the 25MB limit");
      return;
    }
    setSavingTicket(true);
    setTicketError(null);
    try {
      await api.createManualTicket(
        selectedGroup.id,
        {
          ticket_id: id,
          summary,
          description: description.trim() || undefined,
          issue_type: issueType || undefined,
          priority: priority || undefined,
          assignee: assignee.trim() || undefined,
        },
        file,
      );
      setTicketId("");
      setTicketTitle("");
      setDescription("");
      setAssignee("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await reloadGroups();
      setToast(`Ticket ${id} saved to ${selectedGroup.name}`);
    } catch (e) {
      setTicketError(formatError(e));
    } finally {
      setSavingTicket(false);
    }
  };

  const handleDeleteTicket = async (
    ticket: ManualTicket,
    group: TicketGroup,
  ) => {
    try {
      await api.deleteManualTicket(group.id, ticket.ticket_id);
      await reloadGroups();
      setToast(`Ticket ${ticket.ticket_id} removed from ${group.name}`);
    } catch (e) {
      setTicketError(formatError(e));
    }
  };

  const handleUpdateTicketStatus = async (
    ticket: ManualTicket,
    group: TicketGroup,
    status: ManualTicket["status"],
  ) => {
    try {
      await api.updateManualTicketStatus(group.id, ticket.ticket_id, status);
      await reloadGroups();
      setToast(`${ticket.ticket_id} marked ${statusLabel(status)}`);
    } catch (e) {
      setTicketError(formatError(e));
    }
  };

  const groupTotal = groups.reduce((sum, g) => sum + g.ticket_count, 0);

  return (
    <div className="mi">
      {groupError && (
        <div className="banner banner-err mi-banner" role="alert">
          {groupError}
        </div>
      )}
      {ticketError && (
        <div className="banner banner-err mi-banner" role="alert">
          {ticketError}
        </div>
      )}

      {/* Top context header */}
      <header className="mi-head">
        <div className="mi-head-main">
          <Breadcrumb items={crumbs} />
          <div className="mi-title-row">
            <h1 className="mi-title">
              Manual Ticket Ingestion
            </h1>
            <span
              className="mi-pill"
              title="Manually ingested tickets are imported into a project under the 'Jira Group' source"
            >
              <span className="mi-pill-dot" />
              Air-Gapped Ingestion Active
              <span className="mi-pill-shield">⛨</span>
            </span>
          </div>
          <p className="mi-subtitle">
            Collect offline work into ticket groups, capture the specification
            each one carries, and import the group&apos;s tickets into any
            project&apos;s Ticket Browser as a Jira Group source.
          </p>
        </div>
        <div className="mi-head-actions">
          <button
            type="button"
            className="mi-btn"
            onClick={() =>
              document
                .getElementById("mi-group-name")
                ?.scrollIntoView({ behavior: "smooth", block: "center" })
            }
          >
            <span className="mi-ico">＋</span>
            New Ticket Group
          </button>
          <button
            type="button"
            className="mi-btn mi-btn-primary"
            onClick={handleSaveTicket}
            disabled={savingTicket || !selectedGroup}
            title={
              selectedGroup
                ? undefined
                : "Create a ticket group before adding tickets"
            }
          >
            <span className="mi-ico">▸</span>
            {savingTicket ? "Saving…" : "Save Ticket to Group"}
          </button>
        </div>
      </header>

      {/* Primary two-column split */}
      <div className="mi-grid">
        {/* LEFT COLUMN */}
        <div className="mi-col-main">
          {/* Panel 1: Ticket groups */}
          <section className="mi-card">
            <div className="mi-card-head">
              <div className="mi-card-title">
                <span className="mi-card-ico mi-ico-blue">▣</span>
                <span>1. Ticket Groups</span>
              </div>
              <span className="mi-tag">
                {groups.length} group{groups.length === 1 ? "" : "s"} ·{" "}
                {groupTotal} ticket{groupTotal === 1 ? "" : "s"}
              </span>
            </div>

            <div className="mi-meta-grid">
              <div className="mi-field mi-col-7">
                <label className="mi-label" htmlFor="mi-group-name">
                  New group name
                </label>
                <input
                  id="mi-group-name"
                  className="mi-input"
                  placeholder="e.g. SEG-1 · Splunk Intake Backlog"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateGroup();
                  }}
                />
              </div>
              <div className="mi-field mi-col-5">
                <label className="mi-label" htmlFor="mi-group-desc">
                  Description (optional)
                </label>
                <input
                  id="mi-group-desc"
                  className="mi-input"
                  placeholder="Source or context of this batch"
                  value={newGroupDesc}
                  onChange={(e) => setNewGroupDesc(e.target.value)}
                />
              </div>
            </div>

            <div className="mi-form-actions">
              <button
                type="button"
                className="mi-btn mi-btn-primary"
                onClick={handleCreateGroup}
                disabled={creatingGroup}
              >
                <span className="mi-ico">＋</span>
                {creatingGroup ? "Creating…" : "Create Group"}
              </button>
            </div>

            {groups.length === 0 ? (
              <p className="mi-group-empty">
                No ticket groups yet. Create one above - each group becomes a
                batch you can import into a project as a &quot;Jira Group&quot;
                source.
              </p>
            ) : (
              <div className="mi-groups">
                {groups.map((group) => (
                  <button
                    key={group.id}
                    type="button"
                    className={`mi-group-chip ${
                      selectedGroupId === group.id ? "mi-group-chip-active" : ""
                    }`}
                    onClick={() => setSelectedGroupId(group.id)}
                  >
                    <span className="mi-group-chip-name">{group.name}</span>
                    <span className="mi-group-count">{group.ticket_count}</span>
                    <span
                      className="mi-group-remove"
                      role="button"
                      tabIndex={0}
                      title={`Delete ${group.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteGroup(group);
                      }}
                      onKeyDown={(e) => {
                        e.stopPropagation();
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          handleDeleteGroup(group);
                        }
                      }}
                    >
                      ✕
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* Panel 2: Add a manual ticket */}
          <section className="mi-card">
            <div className="mi-card-head">
              <div className="mi-card-title">
                <span className="mi-card-ico mi-ico-blue">◇</span>
                <span>2. Add a Manual Ticket</span>
              </div>
              <span className="mi-tag">
                {selectedGroup
                  ? `Target group: ${selectedGroup.name}`
                  : "No group selected"}
              </span>
            </div>

            {!selectedGroup ? (
              <p className="mi-group-empty">
                Create and select a ticket group above, then add its tickets
                here. Tickets cannot exist outside a group.
              </p>
            ) : (
              <div className="mi-meta-grid">
                <div className="mi-field mi-col-6">
                  <label className="mi-label" htmlFor="mi-ticket-key">
                    <span>External Ticket Key / ID</span>
                    <span className="mi-label-note">
                      required · e.g. SEC-402
                    </span>
                  </label>
                  <div className="mi-input-wrap">
                    <span className="mi-input-ico">#</span>
                    <input
                      id="mi-ticket-key"
                      className="mi-input mi-input-mono"
                      placeholder="e.g. SEC-402, ACME-8841"
                      value={ticketId}
                      onChange={(e) => setTicketId(e.target.value)}
                    />
                  </div>
                </div>
                <div className="mi-field mi-col-6">
                  <label className="mi-label" htmlFor="mi-ticket-title">
                    <span>Ticket Summary</span>
                    <span className="mi-label-note">required</span>
                  </label>
                  <input
                    id="mi-ticket-title"
                    className="mi-input mi-input-lg"
                    placeholder="Short, objective-driven summary"
                    value={ticketTitle}
                    onChange={(e) => setTicketTitle(e.target.value)}
                  />
                </div>
                <div className="mi-field mi-col-6">
                  <span className="mi-label">Issue Type</span>
                  <div className="mi-seg">
                    {ISSUE_TYPES.map((t) => (
                      <button
                        key={t}
                        type="button"
                        className={`mi-seg-btn ${issueType === t ? "mi-seg-active" : ""}`}
                        onClick={() => setIssueType(t)}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="mi-field mi-col-6">
                  <span className="mi-label">Priority</span>
                  <div className="mi-seg">
                    {PRIORITIES.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className={`mi-seg-btn ${priority === p ? "mi-seg-danger" : ""}`}
                        onClick={() => setPriority(p)}
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="mi-field mi-col-6">
                  <label className="mi-label" htmlFor="mi-ticket-assignee">
                    Assignee (optional)
                  </label>
                  <input
                    id="mi-ticket-assignee"
                    className="mi-input"
                    placeholder="e.g. ada@acme.com"
                    value={assignee}
                    onChange={(e) => setAssignee(e.target.value)}
                  />
                </div>
                <div className="mi-field mi-col-6">
                  <span className="mi-label">
                    Reference Attachment (optional)
                  </span>
                  <div className="mi-file-zone">
                    <span className="mi-file-ico">⇪</span>
                    <input
                      ref={fileInputRef}
                      type="file"
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    />
                    {file ? (
                      <div className="mi-file-body">
                        <span className="mi-file-name">{file.name}</span>
                        <span className="mi-file-note">
                          {formatFileSize(file.size)} · Max 25MB each · used by
                          the local planning run
                        </span>
                      </div>
                    ) : (
                      <div className="mi-file-body">
                        <span className="mi-file-name">
                          Click to choose an attachment
                        </span>
                        <span className="mi-file-note">
                          Diagrams, JSON payloads, OpenAPI YAML - max 25MB
                        </span>
                      </div>
                    )}
                    {file && (
                      <button
                        type="button"
                        className="mi-file-clear"
                        title="Remove file"
                        onClick={() => {
                          setFile(null);
                          if (fileInputRef.current)
                            fileInputRef.current.value = "";
                        }}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                </div>
                <div className="mi-field mi-col-12">
                  <label className="mi-label" htmlFor="mi-ticket-desc">
                    Specification / Description (optional markdown)
                  </label>
                  <textarea
                    id="mi-ticket-desc"
                    className="mi-editor"
                    rows={8}
                    spellCheck={false}
                    placeholder="Business objective, functional requirements, acceptance criteria, guardrails..."
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
              </div>
            )}
          </section>

          {/* Panel 3: Tickets in the selected group */}
          <section className="mi-card">
            <div className="mi-card-head">
              <div className="mi-card-title">
                <span className="mi-card-ico mi-ico-violet">▤</span>
                <span>
                  {selectedGroup
                    ? `3. Tickets in ${selectedGroup.name}`
                    : "3. Tickets in Group"}
                </span>
              </div>
              <span className="mi-tag">
                {selectedGroup
                  ? `${selectedGroup.ticket_count} ticket(s)`
                  : "—"}
              </span>
            </div>

            {!selectedGroup ? (
              <p className="mi-group-empty">
                Select a group to inspect its tickets.
              </p>
            ) : selectedGroup.tickets.length === 0 ? (
              <p className="mi-group-empty">
                No tickets captured yet. Add the first one above - later, import
                the whole group from this project&apos;s Ticket Browser.
              </p>
            ) : (
              <div className="mi-ticket-list">
                {selectedGroup.tickets.map((ticket) => (
                  <div key={ticket.id} className="mi-ticket-row">
                    <div className="mi-ticket-left">
                      <span className="mi-ticket-key">{ticket.ticket_id}</span>
                      <div className="mi-ticket-body">
                        <span className="mi-ticket-summary">
                          {ticket.summary}
                        </span>
                        <span className="mi-ticket-meta">
                          {ticket.issue_type && (
                            <span className="mi-ticket-tag">
                              {ticket.issue_type}
                            </span>
                          )}
                          {ticket.priority && (
                            <span className="mi-ticket-tag">
                              {ticket.priority}
                            </span>
                          )}
                          <span>{ticket.assignee ?? "Unassigned"}</span>
                          {ticket.has_attachment && (
                            <span>↗ {ticket.attachment_filename}</span>
                          )}
                        </span>
                      </div>
                    </div>
                    <div className="mi-ticket-actions">
                      <label className="mi-status-control">
                        <span className="sr-only">Status for {ticket.ticket_id}</span>
                        <select
                          className={`mi-status-select mi-status-${ticket.status}`}
                          value={ticket.status}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) =>
                            handleUpdateTicketStatus(
                              ticket,
                              selectedGroup,
                              e.target.value as ManualTicket["status"],
                            )
                          }
                        >
                          {TICKET_STATUSES.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {ticket.has_attachment && ticket.attachment_url && (
                        <a
                          className="mi-ticket-btn"
                          href={api.manualTicketAttachmentUrl(
                            ticket.group_id,
                            ticket.ticket_id,
                          )}
                          target="_blank"
                          rel="noreferrer"
                          title="Download attachment"
                        >
                          <span className="mi-ico">⇩</span>
                          Attachment
                        </a>
                      )}
                      <button
                        type="button"
                        className="mi-ticket-btn mi-ticket-btn-danger"
                        title={`Delete ${ticket.ticket_id}`}
                        onClick={() =>
                          handleDeleteTicket(ticket, selectedGroup)
                        }
                      >
                        <span className="mi-ico">x</span>
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* RIGHT COLUMN */}
        <div className="mi-col-rail">
          {/* Panel: How this flows */}
          <section className="mi-card">
            <div className="mi-card-head">
              <div className="mi-card-title">
                <span className="mi-card-ico mi-ico-muted">◇</span>
                <span>How This Flow Works</span>
              </div>
            </div>
            <div className="mi-steps">
              <div className="mi-step">
                <span className="mi-step-num">1</span>
                <span>
                  Create a <strong>ticket group</strong> for one intake batch.
                </span>
              </div>
              <div className="mi-step">
                <span className="mi-step-num">2</span>
                <span>
                  Add each <strong>manual ticket</strong> with its specification
                  and one optional reference attachment.
                </span>
              </div>
              {/* <div className="mi-step">
                <span className="mi-step-num">3</span>
                <span>
                  Open a project&apos;s Ticket Browser and{" "}
                  <strong>Import → Jira Group</strong> to pull the batch in as a
                  project source.
                </span>
              </div> */}
              {/* <div className="mi-step">
                <span className="mi-step-num">4</span>
                <span>
                  Start a run on an imported ticket - planning uses the stored
                  copy, no live Jira fetch needed.
                </span>
              </div> */}
            </div>
          </section>

          {/* Panel: Recent dispatches */}
          <section className="mi-card">
            <div className="mi-card-head">
              <div className="mi-card-title">
                <span className="mi-card-ico mi-ico-muted">◷</span>
                <span>Recent Pipeline Dispatches</span>
              </div>
              <Link className="mi-view-all" to="/runs">
                View All
              </Link>
            </div>
            <div className="mi-history">
              {recentRuns.length === 0 && (
                <p className="mi-group-empty">No runs dispatched yet.</p>
              )}
              {recentRuns.map((d) => (
                <div key={d.id} className="mi-history-row">
                  <div className="mi-history-top">
                    <span className="mi-history-key">{d.key}</span>
                    <span
                      className={`mi-badge ${
                        d.badgeKind === "ok" ? "mi-badge-ok" : "mi-badge-warn"
                      }`}
                    >
                      {d.badge}
                    </span>
                  </div>
                  <span className="mi-history-title">{d.title}</span>
                  <div className="mi-history-meta">
                    <span>{d.run}</span>
                    <span
                      className={
                        d.badgeKind === "ok" ? "mi-history-meta-ok" : ""
                      }
                    >
                      {d.meta}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Panel: Info note */}
          {/* <section className="mi-note">
            <span className="mi-note-ico">⛨</span>
            <div className="mi-note-body">
              <span className="mi-note-title">
                Air-Gapped Deterministic Ingest
              </span>
              <p className="mi-note-text">
                Offline work is registered here with deterministic acceptance
                criteria, then routed into a project as a Jira Group source.
                Even with outbound Jira webhooks blocked, imported tickets run
                end-to-end from their stored specification.
              </p>
            </div>
          </section> */}
        </div>
      </div>

      {toast && (
        <div className="mi-toast" role="status">
          <span className="mi-ico mi-ico-tertiary">✓</span>
          {toast}
        </div>
      )}

      {groupsError && !groupsError.includes("Failed to fetch") && (
        <p className="muted small mi-err-note">{formatError(groupsError)}</p>
      )}
    </div>
  );
}

function statusLabel(status: ManualTicket["status"]): string {
  return TICKET_STATUSES.find((option) => option.value === status)?.label ?? status;
}

interface DispatchItem {
  id: string;
  key: string;
  badge: string;
  badgeKind: "ok" | "warn";
  title: string;
  run: string;
  meta: string;
}

function recentDispatches(runs: PipelineRun[]): DispatchItem[] {
  return runs
    .filter((r) => r.ticket_key)
    .sort((a, b) => {
      const at = new Date(a.created_at).getTime();
      const bt = new Date(b.created_at).getTime();
      if (Number.isNaN(at)) return 1;
      if (Number.isNaN(bt)) return -1;
      return bt - at;
    })
    .slice(0, 4)
    .map(runToDispatch);
}

function runToDispatch(run: PipelineRun): DispatchItem {
  const statusLabel: Record<string, string> = {
    planning: "Planning",
    replanning: "Replanning",
    awaiting_approval: "Awaiting Sign-off",
    approved: "Approved",
    running: "Running",
    paused: "Paused",
    completed: "PR Merged",
    failed: "Failed",
    rejected: "Rejected",
    cancelled: "Cancelled",
  };
  const badgeKind: "ok" | "warn" =
    run.status === "completed" || run.status === "approved" ? "ok" : "warn";
  return {
    id: run.id,
    key: run.ticket_key ?? "RUN",
    badge: statusLabel[run.status] ?? run.status,
    badgeKind,
    title: run.current_stage ?? run.status,
    run: run.started_at
      ? `Run · ${run.current_stage ?? run.status}`
      : "Run queued",
    meta: `id ${run.id.slice(0, 8)}`,
  };
}
