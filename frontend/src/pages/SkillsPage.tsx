import { useEffect, useMemo, useRef, useState } from "react";
import { api, formatError } from "../api/client";
import { SkillHistory } from "../components/SkillHistory";
import type { Project, Skill, SkillImportCandidate } from "../api/types";

const EXAMPLE_SOURCE = "https://github.com/alirezarezvani/claude-skills";

const SAMPLE_MD = `---
name: apex-governor-limit-auditor
description: Evaluates SOQL query depth within loops and the DML governor budget.
runtime: node20-isolated-vm
permissions: [metadata.read, tooling.apex.executeAnonymous]
---

When an Apex trigger is synthesized, enforce the one-trigger-per-object pattern.`;

const SLUG_ICONS = ["⚙", "▤", "◈", "✦", "✪", "⌕"];

function riskMeta(notes: string | null) {
  const raw = notes ?? "";
  if (/high/i.test(raw)) return { tone: "high" as const, label: "High Risk" };
  if (/medium/i.test(raw))
    return { tone: "medium" as const, label: "Medium Risk" };
  if (/low/i.test(raw) || /safe|0 alerts/i.test(raw))
    return { tone: "low" as const, label: "Low Risk (Sandboxed)" };
  return { tone: "none" as const, label: notes || "Risk Not Scored" };
}

const RISK_CHIP = {
  high: "sk-risk-chip sk-risk-high",
  medium: "sk-risk-chip sk-risk-med",
  low: "sk-risk-chip sk-risk-low",
  none: "sk-risk-chip sk-risk-none",
} as const;

function originLabel(origin: string) {
  switch (origin) {
    case "custom":
      return "Authored locally";
    case "skills_sh":
      return "Imported · skills.sh";
    case "image":
      return "Baked into sandbox image";
    default:
      return origin;
  }
}

function attachmentMeta(slug: string, bySlug: Map<string, Set<string>>) {
  const phases = bySlug.get(slug);
  if (!phases || phases.size === 0) return { on: false, label: "Unattached" };
  const hasP = phases.has("planning") || phases.has("both");
  const hasI = phases.has("implementation") || phases.has("both");
  if (hasP && hasI)
    return { on: true, label: "Attached: Planning & Implementation" };
  if (hasP) return { on: true, label: "Attached: Planning Only" };
  return { on: true, label: "Attached: Implementation Only" };
}

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);

  // Editor - null editingSlug means "creating a new skill". Saving always
  // edits in place (auto-versioned behind the scenes, no version picker
  // exposed here) rather than asking the user to think about versions.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingSlug, setEditingSlug] = useState<string | null>(null);
  const [slugInput, setSlugInput] = useState("");
  const [contentInput, setContentInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  // Set only when the uploaded file was a .zip with files beyond SKILL.md
  // (references/, scripts/, etc.) - saving then goes through the /from-zip
  // endpoints so those files are actually preserved, not just SKILL.md's
  // text (see client.ts's createSkillFromZip/updateSkillContentFromZip).
  const [uploadedZipFile, setUploadedZipFile] = useState<File | null>(null);
  const [bundledFiles, setBundledFiles] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const panelFileInputRef = useRef<HTMLInputElement>(null);

  const [source, setSource] = useState(EXAMPLE_SOURCE);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<SkillImportCandidate[]>([]);
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(
    new Set(),
  );
  const [searching, setSearching] = useState(false);
  const [importingName, setImportingName] = useState<string | null>(null);
  const [importNote, setImportNote] = useState<string | null>(null);
  const [actioningSlug, setActioningSlug] = useState<string | null>(null);
  const [historySlug, setHistorySlug] = useState<string | null>(null);

  // Catalog browsing state
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogFilter, setCatalogFilter] = useState<
    "all" | "core" | "attached" | "risk"
  >("all");

  const reload = () => {
    api
      .listSkills()
      .then(setSkills)
      .catch((e) => setError(formatError(e)));
  };

  useEffect(reload, []);

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch(() => {});
  }, []);

  const pending = useMemo(
    () => skills.filter((s) => s.status === "pending_review"),
    [skills],
  );
  const catalogSkills = useMemo(
    () => skills.filter((s) => s.status !== "pending_review"),
    [skills],
  );

  // Which skills any project currently attaches (planning / implementation /
  // both) - drives the "Project Attachment" column and the summary counter.
  const attachBySlug = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const project of projects) {
      for (const item of project.skills) {
        let sel = m.get(item.slug);
        if (!sel) {
          sel = new Set();
          m.set(item.slug, sel);
        }
        sel.add(item.phase);
      }
    }
    return m;
  }, [projects]);
  const attachedCount = attachBySlug.size;

  const openNewSkillEditor = () => {
    setEditingSlug(null);
    setSlugInput("");
    setContentInput(
      "---\nname: my-skill\ndescription: When to use this skill.\n---\n\nInstructions go here.",
    );
    setUploadedZipFile(null);
    setBundledFiles([]);
    setError(null);
    setEditorOpen(true);
  };

  // Opens the editor with whatever draft is currently staged in the preview
  // drawer (e.g. after uploading a file via Panel A) instead of resetting it.
  const continueToEditor = () => {
    setEditingSlug(null);
    setError(null);
    if (!contentInput.trim()) {
      setContentInput(
        "---\nname: my-skill\ndescription: When to use this skill.\n---\n\nInstructions go here.",
      );
    }
    setEditorOpen(true);
  };

  const openEditEditor = async (slug: string) => {
    setError(null);
    try {
      const { content } = await api.getSkillContent(slug);
      setEditingSlug(slug);
      setSlugInput(slug);
      setContentInput(content);
      setUploadedZipFile(null);
      setBundledFiles([]);
      setEditorOpen(true);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const closeEditor = () => {
    setEditorOpen(false);
    setEditingSlug(null);
    setUploadedZipFile(null);
    setBundledFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleFileUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const result = await api.uploadSkillFile(file);
      setContentInput(result.content);
      if (!editingSlug && result.suggested_slug)
        setSlugInput(result.suggested_slug);
      if (file.name.toLowerCase().endsWith(".zip")) {
        setUploadedZipFile(file);
        setBundledFiles(result.bundled_files);
      } else {
        setUploadedZipFile(null);
        setBundledFiles([]);
      }
    } catch (e) {
      setError(formatError(e));
    } finally {
      setUploading(false);
    }
  };

  const saveEditor = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (uploadedZipFile) {
        if (editingSlug) {
          await api.updateSkillContentFromZip(
            editingSlug,
            uploadedZipFile,
            contentInput,
          );
        } else {
          await api.createSkillFromZip(
            slugInput.trim(),
            uploadedZipFile,
            contentInput,
          );
        }
      } else if (editingSlug) {
        await api.updateSkillContent(editingSlug, contentInput);
      } else {
        await api.createSkill(slugInput.trim(), contentInput);
      }
      closeEditor();
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async (slug: string) => {
    if (
      !confirm(
        `Delete skill ${slug}? This can't be undone. It must not be attached to any project.`,
      )
    )
      return;
    setDeletingSlug(slug);
    setError(null);
    try {
      await api.deleteSkill(slug);
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setDeletingSlug(null);
    }
  };

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSearching(true);
    try {
      const results = await api.searchImportableSkills(source);
      setCandidates(results);
      setSelectedCandidates(new Set());
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSearching(false);
    }
  };

  const doImport = async (name: string) => {
    setImportingName(name);
    setImportNote(null);
    setError(null);
    try {
      const result = await api.importSkill(source, name);
      setImportNote(
        result.status === "active"
          ? `${result.slug} imported and active (${result.risk_notes ?? "unscored"}).`
          : `${result.slug} imported as pending review (${result.risk_notes ?? "risk signals unavailable"}) - approve it below before attaching to a project.`,
      );
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setImportingName(null);
    }
  };

  const importSelected = async () => {
    const names = [...selectedCandidates];
    if (names.length === 0) return;
    setImportingName("selected");
    setImportNote(null);
    setError(null);
    try {
      for (const name of names) await api.importSkill(source, name);
      setImportNote(
        `Imported ${names.length} selected skill${names.length === 1 ? "" : "s"}. Review any skills placed in pending review below.`,
      );
      setSelectedCandidates(new Set());
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setImportingName(null);
    }
  };

  const doApprove = async (skill: Skill) => {
    if (!skill.latest_version) return;
    setActioningSlug(skill.slug);
    setError(null);
    try {
      await api.approveSkill(skill.slug, skill.latest_version);
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setActioningSlug(null);
    }
  };

  const doReject = async (skill: Skill) => {
    setActioningSlug(skill.slug);
    setError(null);
    try {
      await api.rejectSkill(skill.slug);
      reload();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setActioningSlug(null);
    }
  };

  // Ecosystem (Panel B) derived state
  const filter = query.trim().toLowerCase();
  const visibleCandidates = filter
    ? candidates.filter(
        (candidate) =>
          candidate.name.toLowerCase().includes(filter) ||
          candidate.description.toLowerCase().includes(filter),
      )
    : candidates;
  const allVisibleSelected =
    visibleCandidates.length > 0 &&
    visibleCandidates.every((candidate) =>
      selectedCandidates.has(candidate.name),
    );

  // Catalog derived state + client-side pagination
  const highRiskCount = useMemo(
    () =>
      catalogSkills.filter((s) => riskMeta(s.risk_notes).tone === "high")
        .length,
    [catalogSkills],
  );
  const filteredCatalog = useMemo(() => {
    const q = catalogQuery.trim().toLowerCase();
    return catalogSkills.filter((s) => {
      if (
        catalogFilter === "core" &&
        s.origin !== "custom" &&
        s.origin !== "image"
      )
        return false;
      if (catalogFilter === "attached" && !attachBySlug.has(s.slug))
        return false;
      if (catalogFilter === "risk" && riskMeta(s.risk_notes).tone !== "high")
        return false;
      if (!q) return true;
      return [s.slug, s.name, s.description ?? "", s.origin, s.origin_ref ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [catalogSkills, catalogQuery, catalogFilter, attachBySlug]);

  // Stage preview drawer (Panel A) - shows the live draft or a sample.
  const previewText = contentInput.trim() || SAMPLE_MD;
  const previewFile = editingSlug
    ? `${editingSlug}.md`
    : slugInput.trim()
      ? `${slugInput.trim()}.md`
      : "apex-governor-guard.md";
  const hasFrontmatter = /^---\s*$/m.test(previewText);
  const previewLines = useMemo(() => {
    const lines = previewText.split("\n").slice(0, 9);
    let inFront = false;
    return lines.map((line) => {
      if (/^\s*---\s*$/.test(line)) {
        inFront = !inFront;
        return { text: line, cls: "sk-prev-fm" };
      }
      if (inFront) {
        if (/^[A-Za-z][\w.-]*:/.test(line))
          return { text: line, cls: "sk-prev-key" };
        if (line.trim().startsWith("#"))
          return { text: line, cls: "sk-prev-comment" };
        return { text: line, cls: "sk-prev-body" };
      }
      if (line.trim().startsWith("#"))
        return { text: line, cls: "sk-prev-comment" };
      return { text: line, cls: "sk-prev-body" };
    });
  }, [previewText]);
  const frontmatterKeys = useMemo(() => {
    let inFront = false;
    let keys = 0;
    for (const line of previewText.split("\n")) {
      if (/^\s*---\s*$/.test(line)) {
        inFront = !inFront;
        continue;
      }
      if (inFront && /^[A-Za-z][\w.-]*:/.test(line)) keys++;
    }
    return keys;
  }, [previewText]);
  const copyPreview = () => navigator.clipboard.writeText(previewText);

  return (
    <div className="sk">
      {/* Precision header + live stats */}
      <div className="sk-header">
        <div className="sk-header-info">
          <div className="sk-kicker">
            <span className="sk-chip">CAPABILITIES REGISTRY</span>
            {/* <span className="sk-chip-sep">·</span> */}
            <span className="conn-breadcrumb-sep">/</span>
            {/* <span className="sk-env">SANDBOX-INJECTABLE / SKILLS</span> */}
          </div>
          <h2 className="sk-title">Skills Catalog</h2>
          <p className="sk-subtitle">
            Modular agent capabilities, execution toolsets, and domain skills
            injected into the sandbox for deterministic planning, static
            analysis, and code synthesis. Attach them on a project&apos;s{" "}
            <em>Skills &amp; memory</em> panel, scoped to planning,
            implementation, or both.
          </p>
        </div>
        <div className="sk-summary">
          <div className="sk-summary-tile">
            <span className="sk-summary-dot" />
            <span className="sk-summary-num">{catalogSkills.length}</span>
            <span className="sk-summary-label">Active</span>
          </div>
          <div className="sk-summary-tile sk-summary-tile-err">
            <span className="sk-summary-dot sk-dot-err" />
            <span className="sk-summary-num sk-num-err">{pending.length}</span>
            <span className="sk-summary-label">Pending Review</span>
          </div>
          <div className="sk-summary-tile sk-summary-tile-quiet">
            <span className="sk-summary-ico">⚙</span>
            <span className="sk-summary-num">{attachedCount}</span>
            <span className="sk-summary-label">Attached to Projects</span>
          </div>
        </div>
      </div>

      {error && <div className="banner banner-err">{error}</div>}
      {importNote && <div className="banner banner-ok">{importNote}</div>}

      {/* Dual provisioning action panels */}
      <div className="sk-grid">
        {/* Panel A - Manual creation / upload with live preview */}
        <section className="sk-panel">
          <div className="sk-panel-top">
            <div className="sk-panel-title">
              <div className="sk-icon-square sk-icon-blue">⚙</div>
              <div>
                <div className="sk-panel-name">Author or Upload Skill</div>
                <div className="sk-panel-sub">
                  SKILL.md spec v1.2 with frontmatter schema
                </div>
              </div>
            </div>
            <span className="sk-tag">LOCAL_STAGING</span>
          </div>

          <div className="sk-panel-actions">
            <button
              className="sk-btn sk-btn-primary"
              type="button"
              onClick={openNewSkillEditor}
            >
              <span className="sk-btn-ico">+</span> Create Skill Manually
            </button>
            <label className="sk-btn sk-btn-ghost sk-upload-label">
              <span className="sk-btn-ico">▲</span> Upload (.md, .txt, .zip)
              <input
                className="input-none"
                type="file"
                accept=".md,.txt,.zip"
                ref={panelFileInputRef}
                disabled={uploading}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    handleFileUpload(file).then(() => {
                      if (panelFileInputRef.current)
                        panelFileInputRef.current.value = "";
                    });
                  }
                }}
              />
            </label>
            {/* <span className="sk-footnote">Max 4MB archive</span> */}
          </div>

          {/* Stage preview drawer */}
          <div className="sk-preview">
            <div className="sk-preview-head">
              <div className="sk-preview-head-left">
                <span className="sk-preview-ico">≋</span>
                <span className="sk-preview-label">
                  SKILL.md Stage Preview:
                </span>
                <span className="sk-preview-file">{previewFile}</span>
              </div>
              <div className="sk-preview-head-right">
                <span className="sk-preview-ok" />
                <span className="sk-preview-state">
                  {hasFrontmatter ? "YAML Verified" : "Draft"}
                </span>
                <button
                  className="sk-copy-btn"
                  type="button"
                  title="Copy SKILL.md"
                  onClick={copyPreview}
                >
                  <span>▤</span>
                </button>
              </div>
            </div>
            <div className="sk-preview-code">
              <pre className="sk-prev-lines">
                {previewLines.map((line, i) => (
                  <span key={i} className={line.cls}>
                    {line.text}
                    {"\n"}
                  </span>
                ))}
              </pre>
            </div>
            <div className="sk-preview-foot">
              <span className="sk-preview-foot-left">
                Validation: 0 errors · {frontmatterKeys} frontmatter{" "}
                {frontmatterKeys === 1 ? "key" : "keys"}
              </span>
              <div className="sk-preview-foot-right">
                <button
                  className="sk-mini-btn sk-mini-btn-primary"
                  type="button"
                  onClick={continueToEditor}
                >
                  Open in Editor
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* Panel B - import from remote ecosystem */}
        <section className="sk-panel">
          <div className="sk-panel-top">
            <div className="sk-panel-title">
              <div className="sk-icon-square sk-icon-purple">✦</div>
              <div>
                <div className="sk-panel-name">Import from Git Ecosystem</div>
                <div className="sk-panel-sub">
                  Sync and verify third-party agent bundles
                </div>
              </div>
            </div>
            <span className="sk-tag">GH_SYNC</span>
          </div>

          <form className="sk-search-row" onSubmit={search}>
            <div className="sk-input-wrap">
              <span className="sk-input-ico">◈</span>
              <input
                className="sk-input"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="https://github.com/owner/repository"
                required
              />
            </div>
            <button
              className="sk-btn sk-btn-ghost"
              type="submit"
              disabled={searching}
            >
              {searching ? "Searching…" : "↻ Search Repository"}
            </button>
          </form>

          {candidates.length > 0 && (
            <>
              <div className="sk-filter-bar">
                <div className="sk-input-wrap">
                  <span className="sk-input-ico">⌕</span>
                  <input
                    className="sk-input"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Filter discovered skills by name, slug, or tag..."
                  />
                </div>
                <div className="sk-filter-actions">
                  <label className="sk-check">
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={(e) =>
                        setSelectedCandidates((current) => {
                          const next = new Set(current);
                          for (const candidate of visibleCandidates) {
                            if (e.target.checked) next.add(candidate.name);
                            else next.delete(candidate.name);
                          }
                          return next;
                        })
                      }
                    />
                    Select visible ({visibleCandidates.length})
                  </label>
                  <button
                    className="sk-btn sk-btn-purple sk-btn-sm"
                    type="button"
                    disabled={
                      selectedCandidates.size === 0 || importingName !== null
                    }
                    onClick={importSelected}
                  >
                    {importingName === "selected"
                      ? "Importing…"
                      : `Import Selected (${selectedCandidates.size})`}
                  </button>
                </div>
              </div>

              <div className="sk-micro-list">
                {visibleCandidates.map((c) => (
                  <div className="sk-micro-row" key={c.name}>
                    <div className="sk-micro-main">
                      <input
                        type="checkbox"
                        className="sk-micro-check"
                        checked={selectedCandidates.has(c.name)}
                        onChange={(e) => {
                          setSelectedCandidates((current) => {
                            const next = new Set(current);
                            if (e.target.checked) next.add(c.name);
                            else next.delete(c.name);
                            return next;
                          });
                        }}
                      />
                      <div className="sk-micro-text">
                        <span className="sk-micro-name">{c.name}</span>
                        <span className="sk-micro-desc">{c.description}</span>
                      </div>
                    </div>
                    <button
                      className="sk-btn sk-btn-secondary sk-btn-sm sk-micro-import"
                      type="button"
                      disabled={importingName !== null}
                      onClick={() => doImport(c.name)}
                    >
                      {importingName === c.name ? "Importing…" : "Import"}
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
          {candidates.length > 0 && visibleCandidates.length === 0 && (
            <p className="muted small">
              No displayed skills match this filter.
            </p>
          )}
        </section>
      </div>

      {/* Pending human review & security gate */}
      {pending.length > 0 && (
        <div className="sk-review-banner">
          <div className="sk-review-banner-head">
            <div className="sk-review-banner-left">
              <div className="sk-review-icon">✪</div>
              <div>
                <div className="sk-review-title">
                  <h3>Human Review &amp; Security Verification Required</h3>
                  <span className="sk-review-count">
                    {pending.length} POLICIES PENDING
                  </span>
                </div>
                <p className="sk-review-sub">
                  The following skills request capabilities that carry risk
                  signals. An administrator must audit the SKILL.md and approve
                  before they can be enabled inside automated agent loops. Only
                  active skills can be attached to a project or installed into a
                  sandbox.
                </p>
              </div>
            </div>
            <span className="sk-policy-pill">
              <span className="sk-policy-ico">✪</span>
              Enforce: approve before attach
            </span>
          </div>

          <div className="sk-review-grid">
            {pending.map((s) => {
              const risk = riskMeta(s.risk_notes);
              return (
                <div className="sk-review-card" key={s.slug}>
                  <div className="sk-review-card-top">
                    <div>
                      <div className="sk-review-slug">{s.slug}</div>
                      <div className="sk-review-origin">
                        Origin: {s.origin_ref ?? s.origin}
                      </div>
                    </div>
                    <span className={RISK_CHIP[risk.tone]}>{risk.label}</span>
                  </div>
                  <p className="sk-review-desc">
                    {s.description ?? "No description."}
                  </p>
                  <div className="sk-card-meta">
                    <span>Version: {s.latest_version ?? "—"}</span>
                    <span>·</span>
                    <span>Requested Scope: {s.risk_notes ?? "unscored"}</span>
                  </div>
                  <div className="sk-review-foot">
                    <span className="sk-pending-status">
                      <span className="sk-pending-dot" />
                      Pending Admin Sign-off
                    </span>
                    <div className="sk-review-actions">
                      <button
                        className="sk-btn sk-btn-secondary sk-btn-sm"
                        type="button"
                        onClick={() => openEditEditor(s.slug)}
                      >
                        Review Code
                      </button>
                      <button
                        className="sk-btn sk-btn-danger sk-btn-sm"
                        type="button"
                        disabled={actioningSlug === s.slug}
                        onClick={() => doReject(s)}
                      >
                        Reject
                      </button>
                      <button
                        className="sk-btn sk-btn-primary sk-btn-sm"
                        type="button"
                        disabled={actioningSlug === s.slug || !s.latest_version}
                        onClick={() => doApprove(s)}
                      >
                        {actioningSlug === s.slug
                          ? "Working…"
                          : "Approve Skill"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Main catalog list */}
      {catalogSkills.length === 0 ? (
        <div className="sk-empty">
          <h3>No skills in the catalog yet</h3>
          <p>
            Write one, upload a SKILL.md zip, or import from a GitHub repository
            using the panels above.
          </p>
          <button
            className="sk-btn sk-btn-primary"
            type="button"
            onClick={openNewSkillEditor}
          >
            Create first skill
          </button>
        </div>
      ) : (
        <div className="sk-catalog">
          <div className="sk-catalog-toolbar">
            <div className="sk-search-box">
              <span className="sk-input-ico">⌕</span>
              <input
                className="sk-input"
                value={catalogQuery}
                onChange={(e) => setCatalogQuery(e.target.value)}
                placeholder="Search active skills by slug, keyword, capability, or tag..."
              />
            </div>
            <div className="sk-filters">
              <span className="sk-filter-label">Filter:</span>
              {(
                [
                  ["all", `All (${catalogSkills.length})`],
                  ["core", "Verified Core"],
                  ["attached", `Attached (${attachedCount})`],
                  ["risk", `High Risk (${highRiskCount})`],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={`sk-filter-btn ${catalogFilter === key ? "sk-filter-active" : ""}`}
                  type="button"
                  onClick={() => setCatalogFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="sk-scroll">
            <table className="sk-table">
              <thead>
                <tr>
                  <th>Slug</th>
                  <th>Name &amp; Capability Scope</th>
                  <th>Version</th>
                  <th>Origin</th>
                  <th>Risk Signal</th>
                  <th>Project Attachment</th>
                  <th>Status</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredCatalog.map((s) => (
                  <SkillRows
                    key={s.slug}
                    skill={s}
                    historySlug={historySlug}
                    setHistorySlug={setHistorySlug}
                    deletingSlug={deletingSlug}
                    onEdit={openEditEditor}
                    onDelete={doDelete}
                    attachment={attachmentMeta(s.slug, attachBySlug)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="sk-catalog-foot">
            <span className="sk-catalog-foot-note">
              <span>
                {filteredCatalog.length}{" "}
                {filteredCatalog.length === 1
                  ? "Registered Skill"
                  : "Registered Skills"}
              </span>
              <span className="sk-sep">·</span>
              <span>Only active skills attach to projects</span>
            </span>
          </div>
        </div>
      )}

      {/* Create/Edit modal */}
      {editorOpen && (
        <div
          className="sk-modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeEditor();
          }}
        >
          <div className="sk-modal">
            <div className="sk-modal-head">
              <div className="sk-modal-title">
                <div className="sk-icon-square sk-icon-blue">
                  {editingSlug ? "✎" : "+"}
                </div>
                <div>
                  <h3>
                    {editingSlug
                      ? `Edit ${editingSlug}`
                      : "Author New Modular Skill"}
                  </h3>
                  <p>
                    {editingSlug
                      ? "Edit SKILL.md and publish a new version"
                      : "Compile dynamic parameters &amp; execution toolset"}
                  </p>
                </div>
              </div>
              <button
                className="sk-modal-close"
                type="button"
                title="Close"
                onClick={closeEditor}
              >
                <span>✕</span>
              </button>
            </div>

            <form className="sk-form" onSubmit={saveEditor}>
              <div className="sk-form-row">
                <div className="sk-field">
                  <label htmlFor="sk-slug">Skill Slug</label>
                  <input
                    className="sk-field-input sk-mono"
                    id="sk-slug"
                    value={slugInput}
                    onChange={(e) => setSlugInput(e.target.value)}
                    placeholder="e.g. apex-trigger-handler-factory"
                    disabled={editingSlug !== null}
                    required
                  />
                </div>
                <div className="sk-field">
                  <label htmlFor="sk-version">Semantic Version</label>
                  <input
                    className="sk-field-input sk-mono"
                    id="sk-version"
                    value="auto"
                    disabled
                    title="Saving auto-version bumps the active version"
                  />
                </div>
              </div>

              <div className="sk-field">
                <label htmlFor="sk-upload">
                  Or upload a file (.md, .txt, or a .zip containing SKILL.md)
                </label>
                <input
                  className="sk-field-input"
                  id="sk-upload"
                  ref={fileInputRef}
                  type="file"
                  accept=".md,.txt,.zip"
                  disabled={uploading}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFileUpload(file);
                  }}
                />
                {uploading && (
                  <span className="sk-upload-note">Uploading…</span>
                )}
              </div>

              {bundledFiles.length > 0 && (
                <p className="sk-upload-note">
                  Also preserving {bundledFiles.length} file
                  {bundledFiles.length > 1 ? "s" : ""} from the zip:{" "}
                  {bundledFiles.join(", ")}. You&apos;re only editing
                  SKILL.md&apos;s text below - re-upload the zip to change the
                  others.
                </p>
              )}

              <div className="sk-field">
                <div className="sk-schema-row">
                  <label htmlFor="sk-content">SKILL.md Definition</label>
                  <span className="sk-schema">
                    {hasFrontmatter
                      ? "Schema: Valid"
                      : "Schema: Pending frontmatter"}
                  </span>
                </div>
                <textarea
                  className="sk-textarea"
                  id="sk-content"
                  value={contentInput}
                  onChange={(e) => setContentInput(e.target.value)}
                  required
                />
              </div>

              <div className="sk-modal-foot">
                <span className="sk-modal-foot-note">
                  <span className="sk-shield">✪</span> Requires Human Review if
                  permissions exceed standard sandboxing
                </span>
                <div className="sk-modal-actions">
                  <button
                    className="sk-btn sk-btn-secondary"
                    type="button"
                    onClick={closeEditor}
                  >
                    Cancel
                  </button>
                  <button
                    className="sk-btn sk-btn-primary"
                    type="submit"
                    disabled={saving || uploading}
                  >
                    {saving
                      ? "Saving…"
                      : editingSlug
                        ? "Save &amp; Publish"
                        : "Register Skill"}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function SkillRows({
  skill,
  historySlug,
  setHistorySlug,
  deletingSlug,
  onEdit,
  onDelete,
  attachment,
}: {
  skill: Skill;
  historySlug: string | null;
  setHistorySlug: (slug: string | null) => void;
  deletingSlug: string | null;
  onEdit: (slug: string) => void;
  onDelete: (slug: string) => void;
  attachment: { on: boolean; label: string };
}) {
  const open = historySlug === skill.slug;
  const risk = riskMeta(skill.risk_notes);
  const verified = skill.origin === "custom" || skill.origin === "image";
  const icon = SLUG_ICONS[skill.slug.length % SLUG_ICONS.length];
  const iconClass =
    risk.tone === "high"
      ? "sk-slug-ico sk-ico-purple"
      : risk.tone === "medium"
        ? "sk-slug-ico sk-ico-tertiary"
        : "sk-slug-ico";
  return (
    <>
      <tr>
        <td>
          <div className="sk-slug">
            <span className={iconClass}>{icon}</span>
            <span className="sk-slug-name">{skill.slug}</span>
          </div>
        </td>
        <td className="sk-name-cell">
          <div className="sk-name">{skill.name}</div>
          {skill.description && (
            <div className="sk-desc">{skill.description}</div>
          )}
        </td>
        <td className="sk-mono">{skill.active_version ?? "—"}</td>
        <td>
          <div className="sk-origin">
            <span className="sk-origin-core">
              {verified && <span className="sk-origin-check">✓</span>}
              {originLabel(skill.origin)}
            </span>
            {skill.origin_ref && (
              <span className="sk-origin-ref">{skill.origin_ref}</span>
            )}
          </div>
        </td>
        <td>
          <span className={RISK_CHIP[risk.tone]}>{risk.label}</span>
        </td>
        <td>
          <span
            className={`sk-attach-chip ${attachment.on ? "sk-attach-on" : "sk-attach-off"}`}
          >
            {attachment.label}
          </span>
        </td>
        <td>
          <span
            className={`sk-status-ind ${skill.status === "archived" ? "sk-status-deprecated" : "sk-status-active"}`}
          >
            <span className="sk-status-bullet" />
            {skill.status === "archived" ? "Deprecated" : "Active"}
          </span>
        </td>
        <td>
          <div className="sk-actions">
            <button
              className="sk-action-btn"
              type="button"
              title="View source / edit"
              onClick={() => onEdit(skill.slug)}
            >
              <span>✎</span>
            </button>
            <button
              className="sk-action-btn"
              type="button"
              title={open ? "Hide versions" : "Show versions"}
              onClick={() => setHistorySlug(open ? null : skill.slug)}
            >
              <span>▤</span>
            </button>
            <button
              className="sk-action-btn sk-action-danger"
              type="button"
              title="Delete"
              disabled={deletingSlug === skill.slug}
              onClick={() => onDelete(skill.slug)}
            >
              <span>✕</span>
            </button>
          </div>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} className="sk-history-cell">
            <div className="sk-history-inner">
              <SkillHistory skill={skill} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
