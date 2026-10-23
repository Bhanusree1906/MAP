import type {
  AgentRunEvent,
  AzureConnection,
  AzureGitRepo,
  CodexLoginStatus,
  CodexStatus,
  GitHubRepo,
  GitHubConnection,
  ImportSource,
  ImportTicketsOut,
  ImportedTicket,
  JiraConnection,
  ManualTicket,
  ManualWorkItem,
  PipelineRun,
  Project,
  ProjectMemory,
  QuestionResponse,
  RunDetail,
  Plan,
  SalesforceBootstrapJob,
  SalesforceConnection,
  SandboxKind,
  Skill,
  SkillImportCandidate,
  SkillImportResult,
  SkillPhase,
  SkillUpload,
  SkillVersion,
  Ticket,
  TicketGroup,
} from "./types";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, readDetail(body, res.statusText));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function formatError(e: unknown): string {
  if (e instanceof Error) return e.message.replace(/^Error:\s*/i, "");
  return String(e).replace(/^Error:\s*/i, "");
}

function readDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : JSON.stringify(item),
      )
      .join("; ");
  }
  return fallback;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });
// No Content-Type override here (unlike post/put) - fetch sets the
// multipart boundary itself when the body is a FormData instance; setting
// 'application/json' (the request() default) would break the upload.
const uploadFile = <T>(path: string, file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return request<T>(path, { method: "POST", body: formData, headers: {} });
};
// Same no-Content-Type reasoning as uploadFile, but for the /from-zip
// endpoints that also need extra text fields (slug, content, name)
// alongside the file - FastAPI reads those as Form(...) params.
const uploadForm = <T>(
  path: string,
  method: "POST" | "PUT",
  file: File,
  fields: Record<string, string>,
) => {
  const formData = new FormData();
  formData.append("file", file);
  for (const [key, value] of Object.entries(fields))
    formData.append(key, value);
  return request<T>(path, { method, body: formData, headers: {} });
};
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
// Multipart form fields - for the manual-ticket create endpoint, which reads
// fields via Form(...) and takes an optional UploadFile. Only appends the
// file when present. Same no-Content-Type reasoning as uploadForm.
const postForm = <T>(
  path: string,
  fields: Record<string, string>,
  file?: File | null,
) => {
  const formData = new FormData();
  for (const [key, value] of Object.entries(fields))
    formData.append(key, value);
  if (file) formData.append("file", file);
  return request<T>(path, { method: "POST", body: formData, headers: {} });
};

export const api = {
  listProjects: () => get<Project[]>("/projects"),
  createOrUpdateProject: (payload: {
    name: string;
    repository_url: string;
    default_branch?: string;
    jira_connection_name?: string | null;
    azure_connection_name?: string | null;
    ticket_group_name?: string | null;
    github_connection_name?: string | null;
    azure_git_connection_name?: string | null;
    sandbox_kind?: SandboxKind | null;
    salesforce_connection_name?: string | null;
    description?: string | null;
  }) => post<Project>("/projects", payload),
  getProject: (id: string) => get<Project>(`/projects/${id}`),
  deleteProject: (id: string) => del<{ status: string }>(`/projects/${id}`),
  setGitToken: (id: string, access_token: string) =>
    post<{ status: string }>(`/projects/${id}/git-token`, { access_token }),
  deleteGitToken: (id: string) =>
    del<{ status: string }>(`/projects/${id}/git-token`),
  getGitHubStatus: () =>
    get<{
      connected: boolean;
      configured: boolean;
      connections: GitHubConnection[];
    }>("/github/oauth/status"),
  listGitHubRepos: (connection: string) =>
    get<{ connection: string; repos: GitHubRepo[] }>(
      `/github/repos?connection=${encodeURIComponent(connection)}`,
    ),
  githubOAuthStartUrl: (
    login?: string,
    selectAccount = false,
    reconnectName?: string,
  ) => {
    const query = new URLSearchParams();
    if (login) query.set("login", login);
    if (selectAccount) query.set("prompt", "select_account");
    if (reconnectName) query.set("connection_name", reconnectName);
    const suffix = query.size ? `?${query}` : "";
    return `/api/github/oauth/start${suffix}`;
  },
  disconnectGitHub: () =>
    del<{ status: string; removed: boolean; detached_projects: number }>(
      "/github/oauth",
    ),
  disconnectGitHubAccount: (name: string) =>
    del<{ status: string; removed: boolean; detached_projects: number }>(
      `/github/oauth/${encodeURIComponent(name)}`,
    ),
  testGitHubConnection: (name: string) =>
    post<{ valid: boolean; login: string | null; scopes: string | null }>(
      `/github/oauth/${encodeURIComponent(name)}/test`,
    ),
  listTickets: (projectId: string) =>
    get<Ticket[]>(`/projects/${projectId}/tickets`),
  getTicket: (projectId: string, ticketKey: string) =>
    get<Ticket>(
      `/projects/${projectId}/tickets/${encodeURIComponent(ticketKey)}`,
    ),
  fetchImportableTickets: (
    projectId: string,
    payload: { source: ImportSource; group_id?: string; jql?: string },
  ) => post<Ticket[]>(`/projects/${projectId}/tickets/fetch`, payload),
  importTickets: (
    projectId: string,
    payload: { source: ImportSource; keys: string[]; group_id?: string },
  ) => post<ImportTicketsOut>(`/projects/${projectId}/tickets/import`, payload),
  listImportedTickets: (projectId: string) =>
    get<ImportedTicket[]>(`/projects/${projectId}/tickets/imported`),

  listTicketGroups: () => get<TicketGroup[]>("/ticket-groups"),
  createTicketGroup: (name: string, description?: string) =>
    post<TicketGroup>("/ticket-groups", {
      name,
      ...(description ? { description } : {}),
    }),
  getTicketGroup: (id: string) => get<TicketGroup>(`/ticket-groups/${id}`),
  deleteTicketGroup: (id: string) =>
    del<{ status: string }>(`/ticket-groups/${id}`),
  createManualTicket: (
    groupId: string,
    fields: {
      ticket_id: string;
      summary: string;
      description?: string;
      issue_type?: string;
      priority?: string;
      assignee?: string;
    },
    file?: File | null,
  ) => postForm<ManualTicket>(`/ticket-groups/${groupId}/tickets`, fields, file),
  createRichManualTicket: (groupId: string, title: string, body_html: string) =>
    post<ManualTicket>(`/ticket-groups/${groupId}/rich-tickets`, {
      title,
      body_html,
    }),
  updateRichManualTicket: (
    groupId: string,
    ticketId: string,
    title: string,
    body_html: string,
  ) =>
    patch<ManualTicket>(
      `/ticket-groups/${groupId}/tickets/${encodeURIComponent(ticketId)}/content`,
      { title, body_html },
    ),
  deleteManualTicket: (groupId: string, ticketId: string) =>
    del<{ status: string }>(
      `/ticket-groups/${groupId}/tickets/${encodeURIComponent(ticketId)}`,
    ),
  updateManualTicketStatus: (
    groupId: string,
    ticketId: string,
    status: ManualTicket["status"],
  ) =>
    patch<ManualTicket>(
      `/ticket-groups/${groupId}/tickets/${encodeURIComponent(ticketId)}/status`,
      { status },
    ),
  manualTicketAttachmentUrl: (groupId: string, ticketId: string) =>
    `/api/ticket-groups/${groupId}/tickets/${encodeURIComponent(ticketId)}/attachment`,

  listConnections: (refreshHealth = false) =>
    get<JiraConnection[]>(
      `/jira-connections${refreshHealth ? "?refresh_health=true" : ""}`,
    ),
  getJiraOAuthStatus: () =>
    get<{ configured: boolean }>("/jira-connections/oauth/status"),
  createConnection: (payload: {
    name: string;
    project_key: string;
    site_url?: string | null;
    jira_email?: string | null;
    jira_api_token?: string | null;
    read_only?: boolean;
  }) => post<JiraConnection>("/jira-connections", payload),
  jiraOAuthStartUrl: (name: string) =>
    `/api/jira-connections/${name}/oauth/start`,
  getConnection: (name: string) =>
    get<JiraConnection>(`/jira-connections/${name}`),
  updateConnection: (
    name: string,
    payload: {
      project_key?: string;
      site_url?: string | null;
      read_only?: boolean;
      jira_email?: string | null;
      jira_api_token?: string | null;
    },
  ) => put<JiraConnection>(`/jira-connections/${name}`, payload),
  deleteConnection: (name: string) =>
    del<{
      status: string;
      removed: boolean;
      detached_projects: number;
      projects_deleted: number;
    }>(`/jira-connections/${name}`),

  listAzureConnections: () =>
    get<AzureConnection[]>("/azure-connections"),
  createAzureConnection: (payload: {
    name: string;
    organization_url: string;
    project_name: string;
    pat?: string | null;
    read_only?: boolean;
  }) => post<AzureConnection>("/azure-connections", payload),
  getAzureConnection: (name: string) =>
    get<AzureConnection>(`/azure-connections/${name}`),
  updateAzureConnection: (
    name: string,
    payload: {
      organization_url?: string;
      project_name?: string;
      pat?: string | null;
      read_only?: boolean;
    },
  ) => put<AzureConnection>(`/azure-connections/${name}`, payload),
  deleteAzureConnection: (name: string) =>
    del<{ status: string }>(`/azure-connections/${name}`),
  listAzureGitRepos: (name: string) =>
    get<AzureGitRepo[]>(`/azure-connections/${encodeURIComponent(name)}/repos`),

  createRun: (project_id: string, ticket_key: string) =>
    post<PipelineRun>("/pipeline-runs", { project_id, ticket_key }),
  listManualWorkItems: (projectId: string, status?: ManualWorkItem["status"]) =>
    get<ManualWorkItem[]>(
      `/projects/${projectId}/manual-tickets${status ? `?status=${status}` : ""}`,
    ),
  createManualWorkItem: (projectId: string, title: string, body_html: string) =>
    post<ManualWorkItem>(`/projects/${projectId}/manual-tickets`, {
      title,
      body_html,
    }),
  updateManualWorkItem: (
    projectId: string,
    publicId: string,
    title: string,
    body_html: string,
  ) =>
    patch<ManualWorkItem>(
      `/projects/${projectId}/manual-tickets/${encodeURIComponent(publicId)}`,
      { title, body_html },
    ),
  updateManualWorkItemStatus: (
    projectId: string,
    publicId: string,
    status: ManualWorkItem["status"],
  ) =>
    patch<ManualWorkItem>(
      `/projects/${projectId}/manual-tickets/${encodeURIComponent(publicId)}/status`,
      { status },
    ),
  deleteManualWorkItem: (projectId: string, publicId: string) =>
    del<{ status: string }>(
      `/projects/${projectId}/manual-tickets/${encodeURIComponent(publicId)}`,
    ),
  listRuns: (projectId?: string) =>
    get<PipelineRun[]>(
      projectId ? `/pipeline-runs?project_id=${projectId}` : "/pipeline-runs",
    ),
  getRun: (id: string) => get<RunDetail>(`/pipeline-runs/${id}`),
  approveRun: (
    id: string,
    payload: {
      approve: boolean;
      action?: "approve" | "reject";
      plan?: Plan;
      rejection_reason?: string;
      question_responses?: QuestionResponse[];
      reviewer_answers?: string;
      escalate_to_jira?: boolean;
    },
  ) => post<PipelineRun>(`/pipeline-runs/${id}/approval`, payload),
  pauseRun: (id: string) => post<PipelineRun>(`/pipeline-runs/${id}/pause`),
  resumeRun: (id: string) => post<PipelineRun>(`/pipeline-runs/${id}/resume`),
  cancelRun: (id: string) => post<PipelineRun>(`/pipeline-runs/${id}/cancel`),
  retryRun: (id: string) => post<PipelineRun>(`/pipeline-runs/${id}/retry`),
  getAgentRunEvents: (agentRunId: string, afterSeq: number) =>
    get<AgentRunEvent[]>(
      `/pipeline-runs/agent-runs/${agentRunId}/events?after_seq=${afterSeq}`,
    ),

  getCodexStatus: () => get<CodexStatus>("/codex-connection"),
  startCodexLogin: () =>
    post<{ session_id: string }>("/codex-connection/start"),
  getCodexLoginStatus: (sessionId: string) =>
    get<CodexLoginStatus>(`/codex-connection/status/${sessionId}`),
  disconnectCodex: () =>
    del<{ status: string; removed: boolean }>("/codex-connection"),

  listSalesforceConnections: (refreshHealth = false) =>
    get<SalesforceConnection[]>(
      `/salesforce-connections${refreshHealth ? "?refresh_health=true" : ""}`,
    ),
  upsertSalesforceConnection: (
    name: string,
    payload: {
      org_alias?: string;
      sfdx_auth_url?: string;
      instance_url?: string | null;
    },
  ) => put<SalesforceConnection>(`/salesforce-connections/${name}`, payload),
  deleteSalesforceConnection: (name: string) =>
    del<{ status: string }>(`/salesforce-connections/${name}`),

  listSkills: () => get<Skill[]>("/skills"),
  getSkill: (slug: string) => get<Skill>(`/skills/${slug}`),
  setProjectSkills: (
    projectId: string,
    items: { slug: string; phase: SkillPhase }[],
  ) => put<Project>(`/projects/${projectId}/skills`, { items }),
  searchImportableSkills: (source: string, query?: string) =>
    get<SkillImportCandidate[]>(
      `/skills/import/search?source=${encodeURIComponent(source)}` +
        (query ? `&query=${encodeURIComponent(query)}` : ""),
    ),
  importSkill: (source: string, skill_name: string) =>
    post<SkillImportResult>("/skills/import", { source, skill_name }),
  approveSkill: (slug: string, version: string) =>
    post<Skill>(`/skills/${slug}/approve`, { version }),
  rejectSkill: (slug: string) => post<Skill>(`/skills/${slug}/reject`),
  createSkill: (slug: string, content: string, name?: string) =>
    post<Skill>("/skills", { slug, content, name }),
  getSkillContent: (slug: string, version?: string) =>
    get<{ slug: string; content: string; version: string | null }>(
      `/skills/${slug}/content${version ? `?version=${encodeURIComponent(version)}` : ""}`,
    ),
  listSkillVersions: (slug: string) =>
    get<SkillVersion[]>(`/skills/${slug}/versions`),
  listSkillProjects: (slug: string) =>
    get<{ id: string; name: string }[]>(`/skills/${slug}/projects`),
  updateSkillContent: (slug: string, content: string, name?: string) =>
    put<Skill>(`/skills/${slug}/content`, { content, name }),
  deleteSkill: (slug: string) => del<{ status: string }>(`/skills/${slug}`),
  uploadSkillFile: (file: File) =>
    uploadFile<SkillUpload>("/skills/upload", file),
  createSkillFromZip: (
    slug: string,
    file: File,
    content: string,
    name?: string,
  ) =>
    uploadForm<Skill>(
      "/skills/from-zip",
      "POST",
      file,
      name ? { slug, content, name } : { slug, content },
    ),
  updateSkillContentFromZip: (slug: string, file: File, content: string) =>
    uploadForm<Skill>(`/skills/${slug}/from-zip`, "PUT", file, { content }),
  getProjectMemory: (projectId: string) =>
    get<ProjectMemory | null>(`/projects/${projectId}/memory`),
  putProjectMemory: (projectId: string, markdown: string) =>
    put<ProjectMemory>(`/projects/${projectId}/memory`, { markdown }),
  clearProjectMemory: (projectId: string) =>
    del<{ status: string }>(`/projects/${projectId}/memory`),
  bootstrapSalesforce: (projectId: string) =>
    post<SalesforceBootstrapJob>(`/projects/${projectId}/salesforce-bootstrap`),
  getSalesforceBootstrapJob: (projectId: string, jobId: string) =>
    get<SalesforceBootstrapJob>(
      `/projects/${projectId}/salesforce-bootstrap/${jobId}`,
    ),
  getSalesforceBootstrapEvents: (
    projectId: string,
    jobId: string,
    afterSeq: number,
  ) =>
    get<AgentRunEvent[]>(
      `/projects/${projectId}/salesforce-bootstrap/${jobId}/events?after_seq=${afterSeq}`,
    ),
  uploadProjectContext: (projectId: string, file: File) =>
    uploadFile<Project>(`/projects/${projectId}/context`, file),
};

export { ApiError };
