// Mirrors backend/schemas.py

export type SandboxKind = "default" | "salesforce";

export type SkillPhase = "planning" | "implementation" | "both";

export interface ProjectSkill {
  slug: string;
  version: string;
  name: string;
  phase: SkillPhase;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  repository_url: string | null;
  default_branch: string;
  jira_connection_name: string | null;
  azure_connection_name: string | null;
  ticket_group_name: string | null;
  github_connection_name: string | null;
  azure_git_connection_name: string | null;
  sandbox_kind: SandboxKind;
  salesforce_connection_name: string | null;
  skills: ProjectSkill[];
  has_memory: boolean;
  has_git_token: boolean;
  readiness_issues: string[];
  created_at: string;
  updated_at: string;
}

export interface JiraConnection {
  name: string;
  project_key: string;
  site_url: string | null;
  jira_email: string | null;
  has_api_token: boolean;
  has_oauth_token: boolean;
  read_only: boolean;
  created_at: string;
  updated_at: string;
  health_status: "active" | "inactive" | "unknown";
  health_checked_at: string | null;
  health_error: string | null;
}

export interface AzureConnection {
  name: string;
  organization_url: string;
  project_name: string;
  has_pat: boolean;
  read_only: boolean;
  created_at: string;
  updated_at: string;
}

export interface SalesforceConnection {
  name: string;
  org_alias: string;
  instance_url: string | null;
  has_auth_url: boolean;
  created_at: string;
  updated_at: string;
  health_status: "active" | "inactive" | "unknown";
  health_checked_at: string | null;
  health_error: string | null;
}

export interface GitHubRepo {
  name: string;
  full_name: string;
  clone_url: string;
  default_branch: string | null;
  private: boolean;
}

export interface AzureGitRepo {
  id: string;
  name: string;
  clone_url: string;
  web_url: string;
  default_branch: string;
}

export type GitSource = "github" | "azure";

export interface TicketSubtask {
  key: string;
  summary: string;
  status: string | null;
  url: string | null;
}

export interface TicketAttachment {
  id: string;
  filename: string;
  url: string | null;
  mime_type: string | null;
  size: number | null;
}

export interface Ticket {
  key: string;
  summary: string;
  status: string | null;
  assignee: string | null;
  description: string | null;
  priority: string | null;
  issue_type: string | null;
  labels: string[];
  reporter: string | null;
  created: string | null;
  updated: string | null;
  url: string | null;
  parent_key: string | null;
  subtasks: TicketSubtask[];
  attachments: TicketAttachment[];
}

// Manual Ticket Ingestion: a named batch of manual tickets collected on the
// Manual Ingest page. Mirrors backend/schemas.py TicketGroupOut.
export interface ManualTicket {
  id: string;
  group_id: string;
  ticket_id: string;
  summary: string;
  description: string | null;
  issue_type: string | null;
  priority: string | null;
  assignee: string | null;
  status: ManualTicketStatus;
  has_attachment: boolean;
  attachment_filename: string | null;
  attachment_size: number | null;
  attachment_url: string | null;
  created_at: string;
  updated_at: string;
}

export type ManualTicketStatus =
  | "todo"
  | "progress"
  | "agentcompleted"
  | "completed";

export type ManualWorkItemStatus = ManualTicketStatus;

export interface ManualWorkItem {
  id: string;
  public_id: string;
  project_id: string;
  title: string;
  body_html: string;
  body_text: string;
  status: ManualWorkItemStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  agent_completed_at: string | null;
  completed_at: string | null;
}

export interface TicketGroup {
  id: string;
  name: string;
  description: string | null;
  ticket_count: number;
  created_at: string;
  updated_at: string;
  tickets: ManualTicket[];
}

// The project import flow's "Jira Group" source is a TicketGroup; "jira" and
// "azure" map to the project's own linked connections.
export type ImportSource = "jira" | "azure" | "group";

// What the project's settings form links as its ticket source: a live Jira
// connection, a live Azure DevOps connection, or a manual ticket group.
export type TicketSource = "jira" | "azure" | "group";

export interface ImportedTicket {
  id: string;
  project_id: string;
  source: ImportSource;
  source_connection: string | null;
  key: string;
  summary: string;
  description: string | null;
  status: string | null;
  issue_type: string | null;
  priority: string | null;
  assignee: string | null;
  labels: string[];
  reporter: string | null;
  url: string | null;
  attachment_filename: string | null;
  attachment_size: number | null;
  imported_at: string;
}

export interface ImportTicketsOut {
  imported: ImportedTicket[];
  skipped: string[];
}

export interface PlanStep {
  title: string;
  detail: string;
}

export interface PlanSection {
  title: string;
  content: string;
}

export interface OpenQuestion {
  id: string;
  question: string;
  why_it_matters: string;
  assumed_default: string;
  blocking: boolean;
}

export interface AddressedWarning {
  warning: string;
  resolution: string;
}

export interface Plan {
  ticket_key: string;
  summary: string;
  approach: string;
  steps: PlanStep[];
  sections: PlanSection[];
  open_questions: OpenQuestion[];
  risks: string | null;
  out_of_scope: string | null;
  addressed_warnings: AddressedWarning[];
}

export interface QuestionResponse {
  question_id: string;
  choice: "default" | "answer";
  answer_text?: string;
}

export interface AgentRun {
  id: string;
  agent_name: string;
  stage: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface AgentRunEvent {
  seq: number;
  kind: "step" | "timing" | "codex_stdout" | "codex_stderr";
  content: string;
  created_at: string;
}

export interface SalesforceBootstrapResult {
  catalog_objects: number;
  api_version: string;
  scaffolded: boolean;
  scaffold_skipped: boolean;
  message: string;
}

export interface SalesforceBootstrapJob {
  job_id: string;
  status: "running" | "succeeded" | "failed" | string;
  error: string | null;
  result: SalesforceBootstrapResult | null;
}

export interface Artifact {
  id: string;
  artifact_type: string;
  name: string;
  version: number;
  content: string | null;
  created_by: string;
  created_at: string;
}

export interface CostLog {
  id: string;
  agent_name: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_cost: string;
  currency: string;
}

export type PipelineRunStatus =
  | "planning"
  | "replanning"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "warnings_unresolved";

export interface PipelineRun {
  id: string;
  project_id: string;
  status: PipelineRunStatus;
  current_stage: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  ticket_key: string | null;
  retried_from_run_id: string | null;
  pr_url?: string | null;
  total_cost?: string | null;
  review_warnings?: string[] | null;
  warning_validation?: {
    addressed: string[];
    unresolved: string[];
    unmatched: string[];
    validated_at_round?: number;
  } | null;
}

export interface RunDetail {
  run: PipelineRun;
  agent_runs: AgentRun[];
  artifacts: Artifact[];
  cost_logs: CostLog[];
  plan: Plan | null;
  pr_url: string | null;
  total_cost: string;
  skills_used: SkillUsage[];
}

export const ACTIVE_RUN_STATUSES: PipelineRunStatus[] = [
  "planning",
  "replanning",
  "running",
  "paused",
];

export const LIVE_RUN_STATUSES: PipelineRunStatus[] = [
  "planning",
  "replanning",
  "awaiting_approval",
  "approved",
  "running",
  "paused",
];

export type CodexLoginState =
  "starting" | "awaiting_user" | "connected" | "failed" | "timed_out";

export interface Skill {
  slug: string;
  name: string;
  description: string | null;
  origin: string;
  origin_ref: string | null;
  risk_notes: string | null;
  status: string;
  active_version: string | null;
  latest_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillImportCandidate {
  name: string;
  description: string;
}

export interface SkillImportResult {
  slug: string;
  version: string;
  status: string;
  risk_notes: string | null;
}

export interface SkillUpload {
  suggested_slug: string | null;
  content: string;
  bundled_files: string[];
}

export interface SkillUsage {
  slug: string;
  name: string;
  used: boolean;
}

export interface SkillVersion {
  version: string;
  storage_backend: string;
  storage_key: string;
  checksum: string;
  byte_size: number;
  skill_md_name: string | null;
  skill_md_description: string | null;
  created_at: string;
}

export interface ProjectMemory {
  version: string;
  storage_key: string;
  checksum: string;
  byte_size: number;
  commit_sha: string | null;
  markdown: string;
  created_at: string;
}

export interface CodexLoginStatus {
  status: CodexLoginState;
  url: string | null;
  code: string | null;
  error: string | null;
}

export interface CodexStatus {
  connected: boolean;
  api_key_configured: boolean;
  engine: string;
  opencode_model: string | null;
  codex_planning_model: string;
  codex_execution_model: string;
  codex_review_model: string;
  codex_planning_reasoning_effort: string;
  codex_execution_reasoning_effort: string;
  codex_review_reasoning_effort: string;
  codex_max_threads: number;
  status: "active" | "inactive" | "unknown";
  checked_at: string | null;
  error: string | null;
}

export interface GitHubConnection {
  name: string;
  login: string | null;
  scopes: string | null;
  status: "active" | "inactive" | "unknown";
  checked_at: string | null;
  error: string | null;
}
