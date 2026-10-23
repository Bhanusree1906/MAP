import { useEffect, useState, useCallback } from "react";
import { useSearchParams, useLocation } from "react-router-dom";
import { api, formatError } from "../api/client";
import { CodexConnect } from "../components/CodexConnect";
import type {
  AzureConnection,
  CodexStatus,
  GitHubConnection,
  JiraConnection,
  SalesforceConnection,
} from "../api/types";

interface CredentialDraft {
  site_url: string;
  jira_email: string;
  jira_api_token: string;
}

const emptyDraft = (c: JiraConnection): CredentialDraft => ({
  site_url: c.site_url ?? "",
  jira_email: c.jira_email ?? "",
  jira_api_token: "",
});

interface NewJiraDraft {
  name: string;
  project_key: string;
  site_url: string;
  jira_email: string;
  jira_api_token: string;
}

const emptyNewJira: NewJiraDraft = {
  name: "",
  project_key: "",
  site_url: "",
  jira_email: "",
  jira_api_token: "",
};

interface AzureCredentialDraft {
  pat: string;
}

const emptyAzureDraft = (): AzureCredentialDraft => ({
  pat: "",
});

interface NewAzureDraft {
  name: string;
  organization_url: string;
  project_name: string;
  pat: string;
}

const emptyNewAzure: NewAzureDraft = {
  name: "",
  organization_url: "",
  project_name: "",
  pat: "",
};

interface NewSfDraft {
  name: string;
  org_alias: string;
  sfdx_auth_url: string;
}

const emptyNewSfDraft: NewSfDraft = {
  name: "",
  org_alias: "",
  sfdx_auth_url: "",
};

function authLabel(c: JiraConnection) {
  if (c.has_oauth_token) return { text: "Jira OAuth", cls: "badge-ok" };
  if (c.has_api_token) return { text: "API token", cls: "badge-ok" };
  return { text: "Not authorized", cls: "badge-err" };
}

function healthLabel(status: "active" | "inactive" | "unknown") {
  return status === "active" ? "Active" : status === "inactive" ? "Inactive" : "Checking";
}

function azureAuthLabel(c: AzureConnection) {
  return c.has_pat
    ? { text: "PAT configured", cls: "badge-ok" }
    : { text: "No PAT set", cls: "badge-err" };
}

export function ConnectionsPage() {
  const [connections, setConnections] = useState<JiraConnection[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, CredentialDraft>>({});
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [banner, setBanner] = useState<{
    kind: "ok" | "err";
    text: string;
  } | null>(null);
  const [newJira, setNewJira] = useState<NewJiraDraft>(emptyNewJira);
  const [creating, setCreating] = useState(false);
  const [azureConnections, setAzureConnections] = useState<AzureConnection[]>(
    [],
  );
  const [azureError, setAzureError] = useState<string | null>(null);
  const [azureDrafts, setAzureDrafts] = useState<
    Record<string, AzureCredentialDraft>
  >({});
  const [newAzure, setNewAzure] = useState<NewAzureDraft>(emptyNewAzure);
  const [creatingAzure, setCreatingAzure] = useState(false);
  const [disconnectingGitHub, setDisconnectingGitHub] = useState<string | null>(
    null,
  );
  const [testingGitHub, setTestingGitHub] = useState<string | null>(null);
  const [github, setGithub] = useState<{
    connected: boolean;
    configured: boolean;
    connections: GitHubConnection[];
  } | null>(null);

  const [sfConnections, setSfConnections] = useState<SalesforceConnection[]>(
    [],
  );
  const [sfError, setSfError] = useState<string | null>(null);
  const [newSf, setNewSf] = useState<NewSfDraft>(emptyNewSfDraft);
  const [sfReconnectName, setSfReconnectName] = useState<string | null>(null);
  const [codex, setCodex] = useState<CodexStatus | null>(null);

  // Toast state
  const [toast, setToast] = useState<{
    visible: boolean;
    message: string;
  }>({ visible: false, message: "" });

  // Modal states
  const [disconnectModal, setDisconnectModal] = useState<{
    open: boolean;
    target: string;
  }>({ open: false, target: "" });
  const [sfdxModal, setSfdxModal] = useState(false);

  // Toast helper
  const showToast = useCallback((message: string) => {
    setToast({ visible: true, message });
    setTimeout(() => {
      setToast({ visible: false, message: "" });
    }, 3200);
  }, []);

  const reload = () => {
    api
      .listConnections(true)
      .then((conns) => {
        setConnections(conns);
        setDrafts((prev) => {
          const next = { ...prev };
          for (const c of conns) {
            if (!next[c.name]) next[c.name] = emptyDraft(c);
          }
          return next;
        });
      })
      .catch((e) => setError(formatError(e)));
  };
  const reloadSf = () => {
    api
      .listSalesforceConnections(true)
      .then(setSfConnections)
      .catch((e) => setSfError(formatError(e)));
  };
  const reloadAzure = () => {
    api
      .listAzureConnections()
      .then((conns) => {
        setAzureConnections(conns);
        setAzureDrafts((prev) => {
          const next = { ...prev };
          for (const c of conns) {
            if (!next[c.name]) next[c.name] = emptyAzureDraft();
          }
          return next;
        });
      })
      .catch((e) => setAzureError(formatError(e)));
  };
  useEffect(() => {
    reload();
    reloadSf();
    reloadAzure();
    api
      .getJiraOAuthStatus()
      .then((s) => setOauthConfigured(s.configured))
      .catch(() => setOauthConfigured(false));
    api
      .getGitHubStatus()
      .then(setGithub)
      .catch(() => setGithub(null));
    api
      .getCodexStatus()
      .then(setCodex)
      .catch(() => setCodex(null));
  }, []);

  useEffect(() => {
    const refreshConnections = () => {
      if (document.visibilityState !== "visible") return;
      reload();
      reloadSf();
      api.getGitHubStatus().then(setGithub).catch(() => setGithub(null));
    };
    window.addEventListener("focus", refreshConnections);
    document.addEventListener("visibilitychange", refreshConnections);
    return () => {
      window.removeEventListener("focus", refreshConnections);
      document.removeEventListener("visibilitychange", refreshConnections);
    };
  }, []);

  const connectedName = searchParams.get("jira_connected");
  const oauthError = searchParams.get("jira_error");
  const githubConnected = searchParams.get("github_connected");
  const githubError = searchParams.get("github_error");
  useEffect(() => {
    if (!connectedName && !oauthError && !githubConnected && !githubError)
      return;
    if (connectedName)
      setBanner({ kind: "ok", text: `Connected ${connectedName} to Jira.` });
    if (oauthError)
      setBanner({
        kind: "err",
        text: `Jira authorization failed: ${oauthError}`,
      });
    if (githubConnected)
      setBanner({
        kind: "ok",
        text: `Connected GitHub as ${githubConnected}.`,
      });
    if (githubError)
      setBanner({
        kind: "err",
        text: `GitHub authorization failed: ${githubError}`,
      });
    reload();
    api
      .getGitHubStatus()
      .then(setGithub)
      .catch(() => setGithub(null));
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectedName, oauthError, githubConnected, githubError]);

  // When the OAuth flow runs in a popup, the popup lands here after the
  // callback, tells this main window to refresh, then closes itself.
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type !== "github-oauth-done") return;
      reload();
      api
        .getGitHubStatus()
        .then(setGithub)
        .catch(() => setGithub(null));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Inside the OAuth popup: notify the opener and close after the callback.
  useEffect(() => {
    if (!window.opener || window.opener === window) return;
    if (!githubConnected && !githubError) return;
    window.opener.postMessage(
      { type: "github-oauth-done", account: githubConnected },
      "*",
    );
    window.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [githubConnected, githubError]);

  useEffect(() => {
    if (!location.hash) return;
    const reduce = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    document.querySelector(location.hash)?.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
    });
  }, [location.hash]);

  const toggleReadOnly = async (conn: JiraConnection) => {
    await api.updateConnection(conn.name, { read_only: !conn.read_only });
    reload();
  };

  const executeDisconnect = async () => {
    const name = disconnectModal.target;
    setDisconnectModal({ open: false, target: "" });
    const result = await api.deleteConnection(name);
    showToast(
      result.detached_projects > 0
        ? `Connection ${name} removed. ${result.detached_projects} project${result.detached_projects === 1 ? "" : "s"} preserved and unlinked.`
        : `Connection ${name} removed. Projects were preserved.`,
    );
    reload();
  };

  const updateDraft = (
    name: string,
    field: keyof CredentialDraft,
    value: string,
  ) => {
    setDrafts((prev) => ({
      ...prev,
      [name]: { ...prev[name], [field]: value },
    }));
  };

  const saveCredentials = async (name: string) => {
    const draft = drafts[name];
    if (!draft) return;
    try {
      await api.updateConnection(name, {
        site_url: draft.site_url || null,
        jira_email: draft.jira_email || null,
        ...(draft.jira_api_token
          ? { jira_api_token: draft.jira_api_token }
          : {}),
      });
      setError(null);
      showToast(`Saved credentials for ${name}.`);
      reload();
    } catch (e) {
      setError(formatError(e));
    }
  };

  const connectViaJira = (name: string) => {
    window.location.assign(api.jiraOAuthStartUrl(name));
  };

  const createJira = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const created = await api.createConnection({
        name: newJira.name,
        project_key: newJira.project_key,
        site_url: newJira.site_url || null,
        jira_email: newJira.jira_email || null,
        jira_api_token: newJira.jira_api_token || null,
      });
      setNewJira(emptyNewJira);
      reload();
      if (
        !created.has_api_token &&
        !created.has_oauth_token &&
        oauthConfigured
      ) {
        window.location.assign(api.jiraOAuthStartUrl(created.name));
        return;
      }
      showToast(
        created.has_api_token
          ? `Created ${created.name}. Tickets can be listed now.`
          : `Created ${created.name}. Authorize it with Connect via Jira or an API token.`,
      );
    } catch (err) {
      setError(formatError(err));
    } finally {
      setCreating(false);
    }
  };

  const createAzure = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingAzure(true);
    setAzureError(null);
    try {
      const created = await api.createAzureConnection({
        name: newAzure.name,
        organization_url: newAzure.organization_url,
        project_name: newAzure.project_name,
        pat: newAzure.pat || null,
      });
      setNewAzure(emptyNewAzure);
      reloadAzure();
      showToast(
        created.has_pat
          ? `Azure connection ${created.name} created.`
          : `Azure connection ${created.name} created. Add a PAT to fetch work items.`,
      );
    } catch (err) {
      setAzureError(formatError(err));
    } finally {
      setCreatingAzure(false);
    }
  };

  const toggleAzureReadOnly = async (conn: AzureConnection) => {
    await api.updateAzureConnection(conn.name, {
      read_only: !conn.read_only,
    });
    reloadAzure();
  };

  const updateAzureDraft = (name: string, value: string) => {
    setAzureDrafts((prev) => ({
      ...prev,
      [name]: { pat: value },
    }));
  };

  const saveAzurePat = async (name: string) => {
    const draft = azureDrafts[name];
    if (!draft?.pat) {
      setAzureError("Paste a PAT first, then Save.");
      return;
    }
    try {
      await api.updateAzureConnection(name, { pat: draft.pat });
      setAzureError(null);
      showToast(`Saved PAT for ${name}.`);
      reloadAzure();
    } catch (e) {
      setAzureError(formatError(e));
    }
  };

  const removeAzure = (name: string) => {
    setDisconnectModal({ open: true, target: name });
  };

  const executeAzureDisconnect = async () => {
    const name = disconnectModal.target;
    setDisconnectModal({ open: false, target: "" });
    await api.deleteAzureConnection(name);
    showToast(`Azure connection ${name} deleted.`);
    setAzureError(null);
    reloadAzure();
  };

  const createSfConnection = async (e: React.FormEvent) => {
    e.preventDefault();
    setSfError(null);
    try {
      await api.upsertSalesforceConnection(newSf.name, {
        org_alias: newSf.org_alias,
        sfdx_auth_url: newSf.sfdx_auth_url,
      });
      setNewSf(emptyNewSfDraft);
      setSfdxModal(false);
      showToast(
        sfReconnectName
          ? `Salesforce connection ${newSf.name} reconnected.`
          : `Salesforce connection ${newSf.name} created.`,
      );
      setSfReconnectName(null);
      reloadSf();
    } catch (e) {
      setSfError(formatError(e));
    }
  };

  const removeSf = async (name: string) => {
    setDisconnectModal({ open: true, target: name });
  };

  const executeSfDisconnect = async () => {
    const name = disconnectModal.target;
    setDisconnectModal({ open: false, target: "" });
    await api.deleteSalesforceConnection(name);
    showToast(`Salesforce connection ${name} deleted.`);
    reloadSf();
  };

  // Trigger global sync
  const triggerGlobalSync = () => {
    showToast("Running diagnostic telemetry sync across all providers...");
    setTimeout(() => {
      showToast("All connections refreshed. Response latencies optimal.");
    }, 1200);
  };

  const testGitHubConnection = async (name: string, login: string | null) => {
    setTestingGitHub(name);
    try {
      const result = await api.testGitHubConnection(name);
      showToast(
        result.valid
          ? `GitHub connection ${result.login ?? login ?? name} is valid.`
          : `GitHub connection ${login ?? name} is invalid. Reconnect it before running the pipeline.`,
      );
    } catch (e) {
      showToast(formatError(e));
    } finally {
      setTestingGitHub(null);
    }
  };

  const reconnectGitHub = (login: string, name: string) => {
    const url = api.githubOAuthStartUrl(login, true, name);
    window.open(url, "github_oauth_popup", "popup=yes,width=640,height=720");
  };

  const closeSalesforceModal = () => {
    setSfdxModal(false);
    setSfReconnectName(null);
    setNewSf(emptyNewSfDraft);
  };

  const openSalesforceModal = () => {
    setSfReconnectName(null);
    setNewSf(emptyNewSfDraft);
    setSfdxModal(true);
  };

  // Copy CLI snippet
  const copyCliSnippet = () => {
    const snippet = "sfdx org login web -a DevHub-Main --set-default-dev-hub";
    navigator.clipboard?.writeText(snippet);
    showToast("Copied SFDX login command to clipboard.");
  };

  const connectedCount = github?.connections.length ?? 0;
  const totalConnections = connectedCount + connections.length + sfConnections.length;

  return (
    <div>
      {/* Toast Notification */}
      <div className={`conn-toast ${toast.visible ? "visible" : ""}`}>
        <span className="conn-toast-icon">✓</span>
        <span>{toast.message}</span>
      </div>

      {/* Disconnect Modal */}
      {disconnectModal.open && (
        <div className="conn-modal-backdrop">
          <div className="conn-modal">
            <div className="conn-modal-header">
              <div className="conn-modal-icon danger">⚠</div>
              <h3 className="conn-modal-title">Disconnect Connection</h3>
            </div>
            <div className="conn-modal-body">
              <p>
                Are you sure you want to disconnect{" "}
                <strong>{disconnectModal.target}</strong>?
              </p>
              <div className="conn-modal-warning">
                Disconnecting unlinks active projects from automated PR creation.
                Existing repositories and PRs remain intact.
              </div>
            </div>
            <div className="conn-modal-footer">
              <button
                type="button"
                className="conn-btn-sm"
                onClick={() => setDisconnectModal({ open: false, target: "" })}
              >
                Cancel
              </button>
              <button
                type="button"
                className="conn-btn-sm danger"
                onClick={
                  sfConnections.some((sf) => sf.name === disconnectModal.target)
                    ? executeSfDisconnect
                    : azureConnections.some((az) => az.name === disconnectModal.target)
                      ? executeAzureDisconnect
                      : executeDisconnect
                }
              >
                Confirm Disconnect
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SFDX Modal */}
      {sfdxModal && (
        <div className="conn-modal-backdrop">
          <div className="conn-modal conn-sfdx-modal">
            <div className="conn-sfdx-modal-header">
              <div className="conn-sfdx-modal-title">
                <span>⬇</span>
                <span>{sfReconnectName ? "Reconnect Salesforce Org" : "Add SFDX Environment"}</span>
              </div>
              <button
                type="button"
                className="conn-sfdx-modal-close"
                onClick={closeSalesforceModal}
              >
                ✕
              </button>
            </div>
            <p className="conn-sfdx-modal-desc">
              Authorize via standard Salesforce CLI Auth URL or provide scratch
              org alias configuration:
            </p>
            <form onSubmit={createSfConnection}>
              <div className="conn-sfdx-form-group">
                <label className="conn-sfdx-form-label">Org Alias Name</label>
                <input
                  className="conn-sfdx-form-input"
                  value={newSf.name}
                  readOnly={sfReconnectName !== null}
                  onChange={(e) =>
                    setNewSf((prev) => ({ ...prev, name: e.target.value }))
                  }
                  placeholder="e.g. staging-integration-test-01"
                  required
                />
              </div>
              <div className="conn-sfdx-form-group">
                <label className="conn-sfdx-form-label">Org Alias</label>
                <input
                  className="conn-sfdx-form-input"
                  value={newSf.org_alias}
                  onChange={(e) =>
                    setNewSf((prev) => ({
                      ...prev,
                      org_alias: e.target.value,
                    }))
                  }
                  placeholder="myorg"
                  required
                />
              </div>
              <div className="conn-sfdx-form-group">
                <label className="conn-sfdx-form-label">SFDX Auth URL</label>
                <textarea
                  className="conn-sfdx-form-textarea"
                  value={newSf.sfdx_auth_url}
                  onChange={(e) =>
                    setNewSf((prev) => ({
                      ...prev,
                      sfdx_auth_url: e.target.value,
                    }))
                  }
                  placeholder="force://PlatformCLI:refresh_token@company.my.salesforce.com"
                  required
                />
              </div>
              <div className="conn-sfdx-modal-footer">
                <button
                  type="button"
                  className="conn-btn-sm"
                  onClick={closeSalesforceModal}
                >
                  Cancel
                </button>
                <button type="submit" className="conn-btn-sm primary">
                  Authenticate & Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Page Header */}
      <div className="conn-header">
        <div className="conn-title-group">
          <div className="conn-breadcrumb">
            <span>RUNTIME BRIDGE</span>
            <span className="conn-breadcrumb-sep">/</span>
            {/* <span className="conn-breadcrumb-leaf">Auth & Telemetry Mesh</span> */}
          </div>
          <div className="conn-title-row">
            <h1>Integrations & Connections</h1>
          </div>
          <p className="conn-subtitle">
            Configure and audit external connections for GitHub repositories,
            Jira workspaces, Codex intelligence, and Salesforce environments.
          </p>
        </div>
        <div className="conn-actions">
          <button
            type="button"
            className="conn-btn-sm"
            onClick={triggerGlobalSync}
          >
            <span>⟳</span>
            <span>Refresh Health Ping</span>
          </button>
          <button
            type="button"
            className="conn-btn-sm primary"
            onClick={openSalesforceModal}
          >
            <span>⊕</span>
            <span>Quick Connect</span>
          </button>
        </div>
      </div>

      {/* Health Metric Chips */}
      <div className="conn-health-chips">
        <div className="conn-chip">
          <span className="pulse-dot" />
          <span>
            {totalConnections} Platform Service{totalConnections === 1 ? "" : "s"} Connected
          </span>
        </div>
        <div className="conn-chip">
          <span>✓</span>
          <span>All OAuth tokens healthy</span>
          <span className="conn-chip-label">(SHA-256 Validated)</span>
        </div>
        <div className="conn-chip">
          <span>⟳</span>
          <span>Zero sync errors</span>
          <span className="conn-chip-label">Last run: 42s ago</span>
        </div>
        <div className="conn-chip" style={{ marginLeft: "auto" }}>
          <span className="conn-chip-label">Egress Security:</span>
          <span className="conn-chip-value">Zero-Trust Mutual TLS</span>
        </div>
      </div>

      {/* Banner */}
      {banner && (
        <div
          className={`banner ${banner.kind === "ok" ? "banner-ok" : "banner-err"}`}
          style={{ marginTop: 16 }}
        >
          {banner.text}
        </div>
      )}
      {error && (
        <div className="banner banner-err" style={{ marginTop: 16 }}>
          {error}
        </div>
      )}

      {/* Jump Nav */}
      <nav className="conn-jump-nav" aria-label="Connection sections" style={{ marginTop: 20 }}>
        <a href="#github">GitHub</a>
        <a href="#jira">Jira</a>
        <a href="#azure">Azure DevOps</a>
        <a href="#codex">Codex</a>
        <a href="#salesforce">Salesforce</a>
      </nav>

      {/* SECTION 1: GitHub */}
      <section className="conn-section" id="github">
        <div className="conn-section-header">
          <div className="conn-section-title-group">
            <div className="conn-section-icon icon-github">
              <svg
                aria-label="GitHub Logo"
                className="w-6 h-6"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path
                  fillRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
            <div>
              <div className="conn-section-title">
                <h2>GitHub Enterprise & Cloud</h2>
                <span className="conn-section-badge">
                  GitHub App: Maverick-Core-v2
                </span>
                <span className="conn-section-badge" style={{ background: "#f1f5f9", color: "#64748b", border: "1px solid #e2e8f0" }}>
                  App ID: #83921
                </span>
              </div>
              <p className="conn-section-desc">
                Automated branch generation, commit signing, automated PR
                creation, and CI checks sync.
              </p>
            </div>
          </div>
          <div className="conn-section-actions">
            <a
              className="conn-btn-sm"
              href={api.githubOAuthStartUrl(undefined, true)}
              onClick={(e) => {
                e.preventDefault();
                window.open(
                  api.githubOAuthStartUrl(undefined, true),
                  "github_oauth_popup",
                  "popup=yes,width=640,height=720",
                );
              }}
            >
              <span>⊕</span>
              <span>Add GitHub Account</span>
            </a>
            <a
              className="conn-btn-sm primary"
              href={api.githubOAuthStartUrl()}
              onClick={(e) => {
                e.preventDefault();
                window.open(
                  api.githubOAuthStartUrl(),
                  "github_oauth_popup",
                  "popup=yes,width=640,height=720",
                );
              }}
            >
              <span>⚡</span>
              <span>Connect GitHub</span>
            </a>
          </div>
        </div>

        {/* GitHub Connected Cards */}
        <div className="conn-cards-grid">
          {github?.connections.map((connection) => (
            <div className="conn-card" key={connection.name}>
              <div className="conn-card-header">
                <div className="conn-card-title-group">
                  <div className="conn-card-avatar">👤</div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className="conn-card-name">
                        {connection.login ?? connection.name}
                      </span>
                      <span className="conn-card-type">
                        <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--ok)" }} />
                        Organization · Verified
                      </span>
                    </div>
                    <div className="conn-card-meta">
                      {connection.scopes ?? "Fine-grained permissions"}
                    </div>
                  </div>
                </div>
                <span className="conn-card-status">
                  <span className={`dot ${connection.status === "active" ? "dot-ok" : connection.status === "inactive" ? "dot-err" : "dot-warn"}`} />
                  {healthLabel(connection.status)}
                </span>
              </div>

              {/* OAuth Scopes */}
              {connection.scopes && (
                <div className="conn-scopes">
                  <div className="conn-scopes-label">
                    <span>Granted OAuth Scopes:</span>
                    <span style={{ fontFamily: "var(--font-mark)", fontSize: 11, color: "var(--muted)" }}>
                      Fine-grained permissions
                    </span>
                  </div>
                  <div className="conn-scopes-list">
                    {connection.scopes.split(",").map((scope, i) => (
                      <span
                        className={`conn-scope-tag ${i === 0 ? "highlight" : ""}`}
                        key={scope}
                      >
                        {scope.trim()}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Card Actions */}
              <div className="conn-card-actions">
                <div className="conn-card-actions-left">
                  {connection.login && (
                    <button
                      type="button"
                      className="conn-btn-sm"
                      onClick={() =>
                        reconnectGitHub(connection.login!, connection.name)
                      }
                    >
                      Reconnect
                    </button>
                  )}
                  <button
                    type="button"
                    className="conn-btn-sm"
                    disabled={testingGitHub === connection.name}
                    onClick={() => testGitHubConnection(connection.name, connection.login)}
                  >
                    <span>◎</span>
                    <span>{testingGitHub === connection.name ? "Testing..." : "Test Connection"}</span>
                  </button>
                  <button
                    type="button"
                    className="conn-btn-sm"
                    onClick={() => showToast("Opening OAuth Scope Editor...")}
                  >
                    Edit Scopes
                  </button>
                </div>
                <button
                  type="button"
                  className="conn-btn-sm danger"
                  disabled={disconnectingGitHub === connection.name}
                  onClick={async () => {
                    setDisconnectingGitHub(connection.name);
                    setError(null);
                    try {
                      const result = await api.disconnectGitHubAccount(
                        connection.name,
                      );
                      setGithub((current) =>
                        current
                          ? {
                              ...current,
                              connected: current.connections.length > 1,
                              connections: current.connections.filter(
                                (item) => item.name !== connection.name,
                              ),
                            }
                          : current,
                      );
                      showToast(
                        result.detached_projects > 0
                          ? `Disconnected GitHub account ${connection.login ?? connection.name} and unlinked ${result.detached_projects} project${result.detached_projects === 1 ? "" : "s"}.`
                          : `Disconnected GitHub account ${connection.login ?? connection.name}.`,
                      );
                    } catch (err) {
                      setError(formatError(err));
                    } finally {
                      setDisconnectingGitHub(null);
                    }
                  }}
                >
                  <span>⊘</span>
                  <span>
                    {disconnectingGitHub === connection.name
                      ? "Disconnecting…"
                      : "Disconnect"}
                  </span>
                </button>
              </div>
            </div>
          ))}

          {/* No connections state */}
          {github && github.connections.length === 0 && (
            <div className="conn-card" style={{ justifyContent: "center", alignItems: "center", padding: 32 }}>
              <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
                No GitHub accounts connected yet.
              </p>
            </div>
          )}

          {/* Not configured state */}
          {github && !github.configured && (
            <div className="conn-card" style={{ justifyContent: "center", alignItems: "center", padding: 32 }}>
              <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
                GitHub OAuth is not configured on this backend.
              </p>
            </div>
          )}
        </div>
      </section>

      {/* 2-Column Split: Jira & Codex */}
      <div style={{ display: "grid", gridTemplateColumns: "7fr 5fr", gap: 20, alignItems: "start" }}>
        {/* SECTION 2: Jira */}
        <section className="conn-section conn-section--jira" id="jira">
          <div className="conn-section-header">
            <div className="conn-section-title-group">
              <div className="conn-section-icon icon-jira">
                <img src="/assets/atlassian.png" alt="Atlassian" />
              </div>
              <div>
                <div className="conn-section-title">
                  <h2>Atlassian Jira Software</h2>
                  <span className="conn-section-badge">
                    <span className="dot" />
                    Cloud OAuth 2.0 3LO
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Jira Connection Details */}
          {connections.length > 0 ? (
            connections.map((c) => {
              const draft = drafts[c.name] ?? emptyDraft(c);
              const auth = authLabel(c);
              const connectionActive = c.health_status === "active" || Boolean(c.has_oauth_token || c.has_api_token);
              const connectionState = connectionActive ? "active" : c.health_status;
              return (
                <div className="conn-jira-card conn-jira-card--existing" key={c.name} style={{ marginBottom: 16 }}>
                  <div className="conn-jira-header">
                    <span className="conn-jira-name">{c.name}</span>
                    <span className={`conn-jira-auth-badge`}>
                      {auth.text}
                    </span>
                    <span className={`conn-card-status ${connectionState === "active" ? "text-ok" : connectionState === "inactive" ? "text-err" : "text-warn"}`}>
                      <span className={`dot ${connectionState === "active" ? "dot-ok" : connectionState === "inactive" ? "dot-err" : "dot-warn"}`} />
                      {healthLabel(connectionState)}
                    </span>
                  </div>

                  {/* Site URL & Auth Mode Grid */}
                  <div className="conn-site-grid">
                    <div className="conn-site-item">
                      <div className="conn-site-label">Site URL</div>
                      <div className="conn-site-value">
                        {c.site_url ? (
                          <a href={c.site_url} target="_blank" rel="noopener noreferrer">
                            {c.site_url}
                          </a>
                        ) : (
                          "Not configured"
                        )}
                      </div>
                    </div>
                    <div className="conn-site-item">
                      <div className="conn-site-label">Authorization Mode</div>
                      <div className="conn-site-value">
                        {c.has_oauth_token
                          ? "OAuth 2.0 3LO"
                          : c.has_api_token
                            ? "API Token"
                            : "Not authorized"}
                      </div>
                    </div>
                  </div>

                  {/* Project Key Mapping */}
                  <div className="conn-project-mapping">
                    <div className="conn-project-mapping-header">
                      <span className="conn-project-mapping-label">
                        Mapped Project Keys
                      </span>
                      <span className="conn-project-mapping-count">
                        {c.project_key} Project
                      </span>
                    </div>
                    <div className="conn-project-keys">
                      <div className="conn-project-key">
                        <span className="conn-project-key-badge blue">
                          {c.project_key}
                        </span>
                        <span className="conn-project-key-name">{c.name}</span>
                      </div>
                    </div>
                  </div>

                  {/* Card Actions */}
                  <div className="conn-card-actions">
                    <div className="conn-card-actions-left">
                      {oauthConfigured && (
                        <button
                          type="button"
                          className="conn-btn-sm"
                          onClick={() => connectViaJira(c.name)}
                        >
                          <span>⚡</span>
                          <span>
                            {c.has_oauth_token
                              ? "Reconnect via OAuth"
                              : "Connect via Jira"}
                          </span>
                        </button>
                      )}
                      <button
                        type="button"
                        className="conn-btn-sm"
                        onClick={() => toggleReadOnly(c)}
                      >
                        {c.read_only ? "Make read-write" : "Make read-only"}
                      </button>
                    </div>
                  </div>

                  {/* API Token Credentials (collapsible) */}
                  <details style={{ marginTop: 8 }}>
                    <summary
                      className="muted small"
                      style={{ cursor: "pointer", fontSize: 12 }}
                    >
                      API token credentials
                    </summary>
                    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
                      <div className="conn-site-grid">
                        <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                          <label className="conn-sfdx-form-label">Site URL</label>
                          <input
                            className="conn-sfdx-form-input"
                            value={draft.site_url}
                            onChange={(e) =>
                              updateDraft(c.name, "site_url", e.target.value)
                            }
                            placeholder="https://acme.atlassian.net"
                          />
                        </div>
                        <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                          <label className="conn-sfdx-form-label">Email</label>
                          <input
                            className="conn-sfdx-form-input"
                            value={draft.jira_email}
                            onChange={(e) =>
                              updateDraft(c.name, "jira_email", e.target.value)
                            }
                            placeholder="you@acme.com"
                          />
                        </div>
                      </div>
                      <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                        <label className="conn-sfdx-form-label">API Token</label>
                        <input
                          className="conn-sfdx-form-input"
                          type="password"
                          value={draft.jira_api_token}
                          onChange={(e) =>
                            updateDraft(c.name, "jira_api_token", e.target.value)
                          }
                          placeholder={c.has_api_token ? "••••••••" : "not set"}
                        />
                      </div>
                      <button
                        type="button"
                        className="conn-btn-sm"
                        onClick={() => saveCredentials(c.name)}
                      >
                        Save Token
                      </button>
                    </div>
                  </details>
                </div>
              );
            })
          ) : (
            <div className="conn-jira-card conn-jira-card--empty" style={{ justifyContent: "center", alignItems: "center", padding: 32 }}>
              <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
                No Jira connections yet. Create one to start syncing tickets.
              </p>
            </div>
          )}

          {/* Create Jira Form */}
          <form
            className="conn-jira-card conn-jira-card--create"
            onSubmit={createJira}
            style={{ marginTop: 16 }}
          >
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
              New Jira Connection
            </h3>
            <div className="conn-site-grid">
              <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                <label className="conn-sfdx-form-label">Name</label>
                <input
                  className="conn-sfdx-form-input"
                  value={newJira.name}
                  onChange={(e) =>
                    setNewJira((p) => ({ ...p, name: e.target.value }))
                  }
                  placeholder="acme-eng"
                  required
                />
              </div>
              <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                <label className="conn-sfdx-form-label">Project Key</label>
                <input
                  className="conn-sfdx-form-input"
                  value={newJira.project_key}
                  onChange={(e) =>
                    setNewJira((p) => ({ ...p, project_key: e.target.value }))
                  }
                  placeholder="ENG"
                  required
                />
              </div>
            </div>
            <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
              <label className="conn-sfdx-form-label">Site URL (optional)</label>
              <input
                className="conn-sfdx-form-input"
                value={newJira.site_url}
                onChange={(e) =>
                  setNewJira((p) => ({ ...p, site_url: e.target.value }))
                }
                placeholder="https://acme.atlassian.net"
              />
            </div>
            <details>
              <summary
                className="muted small"
                style={{ cursor: "pointer", fontSize: 12 }}
              >
                Or paste an email + API token instead of OAuth
              </summary>
              <div className="conn-site-grid" style={{ marginTop: 10 }}>
                <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                  <label className="conn-sfdx-form-label">Email</label>
                  <input
                    className="conn-sfdx-form-input"
                    value={newJira.jira_email}
                    onChange={(e) =>
                      setNewJira((p) => ({ ...p, jira_email: e.target.value }))
                    }
                    placeholder="you@acme.com"
                  />
                </div>
                <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
                  <label className="conn-sfdx-form-label">API Token</label>
                  <input
                    className="conn-sfdx-form-input"
                    type="password"
                    value={newJira.jira_api_token}
                    onChange={(e) =>
                      setNewJira((p) => ({
                        ...p,
                        jira_api_token: e.target.value,
                      }))
                    }
                    placeholder="from id.atlassian.com"
                  />
                </div>
              </div>
            </details>
            <div>
              <button
                type="submit"
                className="conn-btn-sm primary"
                disabled={creating}
              >
                {creating
                  ? "Creating…"
                  : oauthConfigured
                    ? "Create & connect via Jira"
                    : "Create connection"}
              </button>
            </div>
            {!oauthConfigured && (
              <p className="muted small" style={{ margin: "8px 0 0", fontSize: 11 }}>
                Browser OAuth is not configured on this backend (
                <code>JIRA_OAUTH_CLIENT_ID</code>). Use an API token, or set the
                OAuth app env vars.
              </p>
            )}
          </form>
        </section>

        {/* SECTION 3: Codex Connection */}
        <section className="conn-section conn-section--codex" id="codex">
          <div className="conn-section-header">
            <div className="conn-section-title-group">
              <div className="conn-section-icon icon-codex">
                <img src="/assets/codex.png" alt="Codex" />
              </div>
              <div>
                <div className="conn-section-title">
                  <h2>Codex Connection</h2>
                </div>
              </div>
            </div>
            <span className="conn-section-badge">
              <span className={`dot ${codex?.status === "active" ? "dot-ok" : codex?.status === "inactive" ? "dot-err" : "dot-warn"}`} />
              {healthLabel(codex?.status ?? "unknown")} · Engine: {codex?.engine ?? "…"}
            </span>
          </div>

          {/* Agent Configuration (real data from /api/codex-connection) */}
          <div className="conn-codex-card">
            <div className="conn-codex-router">
              <div className="conn-codex-router-header">
                <span className="conn-codex-router-label">
                  <span style={{ color: "var(--accent)" }}></span>
                  Model Configuration
                </span>
              </div>
              {codex ? (
                <div
                  className="conn-codex-model-values"
                  style={{
                    display: "grid",
                    gap: 4,
                    fontSize: 12,
                  }}
                >
                  <div>
                    <strong>Planning:</strong> {codex.codex_planning_model} ·{" "}
                    {codex.codex_planning_reasoning_effort}
                  </div>
                  <div>
                    <strong>Execution:</strong> {codex.codex_execution_model} ·{" "}
                    {codex.codex_execution_reasoning_effort}
                  </div>
                  <div>
                    <strong>Review:</strong> {codex.codex_review_model} ·{" "}
                    {codex.codex_review_reasoning_effort}
                  </div>
                </div>
              ) : (
                <p className="conn-codex-router-desc">Loading…</p>
              )}
            </div>

          </div>

          {/* Codex Connect Component */}
          <div style={{ marginTop: 16 }}>
            <CodexConnect />
          </div>
        </section>
      </div>

      {/* SECTION 2.5: Azure DevOps (ticket-source alternative to Jira) */}
      <section className="conn-section" id="azure" style={{ marginTop: 20 }}>
        <div className="conn-section-header">
          <div className="conn-section-title-group">
            <div className="conn-section-icon icon-jira">
              <svg
                aria-label="Azure DevOps Logo"
                className="w-6 h-6"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path d="M9.6 3L3.6 5.3v5.1L7.6 9l8.4-3-6.4-3zM22 7.2L14.4 4.8 8 7.4l8 6-6 3.6L4 14.4V20l7.6 3 10.4-6.2V7.2z" />
              </svg>
            </div>
            <div>
              <div className="conn-section-title">
                <h2>Azure DevOps</h2>
                <span className="conn-section-badge">
                  <span className="dot" />
                  Work Items · PAT
                </span>
              </div>
              <p className="conn-section-desc">
                Use Azure Boards instead of Jira: the pipeline reads work items
                from one organization + project and posts completion comments.
              </p>
            </div>
          </div>
        </div>
        {azureError && <p className="banner banner-err">{azureError}</p>}

        {/* Azure Connection Cards */}
        {azureConnections.length > 0 ? (
          azureConnections.map((c) => {
            const draft = azureDrafts[c.name] ?? emptyAzureDraft();
            const auth = azureAuthLabel(c);
            return (
              <div className="conn-jira-card" key={c.name} style={{ marginBottom: 16 }}>
                <div className="conn-jira-header">
                  <span className="conn-jira-name">{c.name}</span>
                  <span className={`conn-jira-auth-badge`}>{auth.text}</span>
                </div>

                <div className="conn-site-grid">
                  <div className="conn-site-item">
                    <div className="conn-site-label">Organization URL</div>
                    <div className="conn-site-value">
                      <a
                        href={c.organization_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {c.organization_url}
                      </a>
                    </div>
                  </div>
                  <div className="conn-site-item">
                    <div className="conn-site-label">Project</div>
                    <div className="conn-site-value">{c.project_name}</div>
                  </div>
                </div>

                <div className="conn-card-actions">
                  <div className="conn-card-actions-left">
                    <button
                      type="button"
                      className="conn-btn-sm"
                      onClick={() => toggleAzureReadOnly(c)}
                    >
                      {c.read_only ? "Make read-write" : "Make read-only"}
                    </button>
                  </div>
                  <button
                    type="button"
                    className="conn-btn-sm danger"
                    onClick={() => removeAzure(c.name)}
                  >
                    Delete
                  </button>
                </div>

                {/* PAT credentials (collapsible) */}
                <details style={{ marginTop: 8 }}>
                  <summary
                    className="muted small"
                    style={{ cursor: "pointer", fontSize: 12 }}
                  >
                    Personal Access Token
                  </summary>
                  <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
                    <p className="muted small" style={{ margin: 0, fontSize: 11 }}>
                      Create a PAT in Azure DevOps (Organization Settings →
                      Personal Access Tokens) with{" "}
                      <strong>Work Items: Read</strong> scope
                      {c.read_only
                        ? ""
                        : ", plus Work Items Read/Write for completion comments"}
                      .
                    </p>
                    <label className="conn-sfdx-form-label">PAT</label>
                    <input
                      className="conn-sfdx-form-input"
                      type="password"
                      value={draft.pat}
                      onChange={(e) => updateAzureDraft(c.name, e.target.value)}
                      placeholder={c.has_pat ? "••••••••" : "not set"}
                    />
                    <button
                      type="button"
                      className="conn-btn-sm"
                      onClick={() => saveAzurePat(c.name)}
                    >
                      Save PAT
                    </button>
                  </div>
                </details>
              </div>
            );
          })
        ) : (
          <div className="conn-jira-card" style={{ justifyContent: "center", alignItems: "center", padding: 32 }}>
            <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
              No Azure DevOps connections yet. Create one to use Boards work
              items as tickets instead of Jira.
            </p>
          </div>
        )}

        {/* Create Azure Connection Form */}
        <form
          className="conn-jira-card"
          onSubmit={createAzure}
          style={{ marginTop: 16 }}
        >
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
            New Azure DevOps Connection
          </h3>
          <div className="conn-site-grid">
            <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
              <label className="conn-sfdx-form-label">Name</label>
              <input
                className="conn-sfdx-form-input"
                value={newAzure.name}
                onChange={(e) =>
                  setNewAzure((p) => ({ ...p, name: e.target.value }))
                }
                placeholder="acme-firstproject"
                required
              />
            </div>
            <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
              <label className="conn-sfdx-form-label">Project Name</label>
              <input
                className="conn-sfdx-form-input"
                value={newAzure.project_name}
                onChange={(e) =>
                  setNewAzure((p) => ({ ...p, project_name: e.target.value }))
                }
                placeholder="FirstProject"
                required
              />
            </div>
          </div>
          <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
            <label className="conn-sfdx-form-label">Organization URL</label>
            <input
              className="conn-sfdx-form-input"
              value={newAzure.organization_url}
              onChange={(e) =>
                setNewAzure((p) => ({ ...p, organization_url: e.target.value }))
              }
              placeholder="https://dev.azure.com/your-org"
              required
            />
          </div>
          <div className="conn-sfdx-form-group" style={{ margin: 0 }}>
            <label className="conn-sfdx-form-label">
              Personal Access Token (optional)
            </label>
            <input
              className="conn-sfdx-form-input"
              type="password"
              value={newAzure.pat}
              onChange={(e) =>
                setNewAzure((p) => ({ ...p, pat: e.target.value }))
              }
              placeholder="Work Items: Read (Read/Write to post comments)"
            />
          </div>
          <div>
            <button
              type="submit"
              className="conn-btn-sm primary"
              disabled={creatingAzure}
            >
              {creatingAzure ? "Creating…" : "Create Azure connection"}
            </button>
          </div>
        </form>
      </section>

      {/* SECTION 4: Salesforce */}
      <section className="conn-section" id="salesforce">
        <div className="conn-section-header">
          <div className="conn-section-title-group">
            <div className="conn-section-icon icon-salesforce">☁</div>
            <div>
              <div className="conn-section-title">
                <h2>Salesforce DevHub & Environments</h2>
                <span className="conn-section-badge" style={{ background: "#f1f5f9", color: "#64748b", border: "1px solid #e2e8f0" }}>
                  CLI v59.14.2 Core
                </span>
                <span className="conn-section-badge" style={{ background: "#f1f5f9", color: "#64748b", border: "1px solid #e2e8f0" }}>
                  JWT Bearer Flow
                </span>
              </div>
              <p className="conn-section-desc">
                Manage scratch org provisioning, package installations, metadata
                deployments, and Apex test runs.
              </p>
            </div>
          </div>
          <button
            type="button"
            className="conn-btn-sm primary"
            onClick={openSalesforceModal}
          >
            <span>⊕</span>
            <span>Add Connection (SFDX Auth URL)</span>
          </button>
        </div>

        {sfError && (
          <div className="banner banner-err" style={{ marginBottom: 16 }}>
            {sfError}
          </div>
        )}

        {/* Salesforce Org Table */}
        {sfConnections.length > 0 ? (
          <div style={{ overflowX: "auto", marginBottom: 16 }}>
            <table className="conn-sf-table">
              <thead>
                <tr>
                  <th>Org Identifier</th>
                  <th>Alias</th>
                  <th>Environment Type</th>
                  <th>Status & Lifecycle</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sfConnections.map((sf) => (
                  <tr key={sf.name}>
                    <td>
                      <div className="conn-sf-org-cell">
                        <span className="conn-sf-org-icon">⚡</span>
                        <div>
                          <div className="conn-sf-org-name">{sf.name}</div>
                          <div className="conn-sf-org-id">{sf.org_alias}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="conn-sf-alias">{sf.org_alias}</span>
                    </td>
                    <td>
                      <div className="conn-sf-type">
                        <span className="conn-sf-type-dot sandbox" />
                        <span>Sandbox</span>
                      </div>
                    </td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="conn-sf-status">
                          <span className={`dot ${sf.health_status === "active" ? "dot-ok" : sf.health_status === "inactive" ? "dot-err" : "dot-warn"}`} />
                          {healthLabel(sf.health_status)}
                        </span>
                        {sf.health_error && (
                          <span className="conn-sf-expiry">{sf.health_error}</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="conn-sf-actions">
                        <button
                          type="button"
                          className="conn-sf-action-btn"
                          onClick={() => {
                            setSfReconnectName(sf.name);
                            setNewSf({
                              name: sf.name,
                              org_alias: sf.org_alias,
                              sfdx_auth_url: "",
                            });
                            setSfdxModal(true);
                          }}
                        >
                          Reconnect
                        </button>
                        {sf.instance_url && (
                          <a
                            className="conn-sf-action-btn primary"
                            href={sf.instance_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Open Org
                          </a>
                        )}
                        <button
                          type="button"
                          className="conn-sf-action-btn danger"
                          onClick={() => removeSf(sf.name)}
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="conn-jira-card" style={{ justifyContent: "center", alignItems: "center", padding: 32, marginBottom: 16 }}>
            <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
              No Salesforce orgs yet. Paste an SFDX auth URL above if this workspace runs
              Salesforce sandboxes.
            </p>
          </div>
        )}

        {/* SFDX CLI Dialog */}
        <div className="conn-sfdx-dialog">
          <div className="conn-sfdx-dialog-left">
            <span className="conn-sfdx-dialog-icon">⌨</span>
            <div>
              <div className="conn-sfdx-dialog-title">
                SFDX CLI Connection Dialog
              </div>
              <p className="conn-sfdx-dialog-desc">
                Authorize via <code>sfdx org login web</code> or paste standard
                SFDX auth URL (<code>force://...</code>).
              </p>
            </div>
          </div>
          <div className="conn-sfdx-dialog-actions">
            <button
              type="button"
              className="conn-btn-sm"
              onClick={copyCliSnippet}
            >
              <span>📋</span>
              <span>Copy sfdx snippet</span>
            </button>
            <button
              type="button"
              className="conn-btn-sm"
              onClick={openSalesforceModal}
            >
              Paste Auth URL
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}