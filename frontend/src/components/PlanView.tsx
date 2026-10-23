import { useEffect, useRef, useState } from "react";
import type { Plan } from "../api/types";
import { MarkdownBody } from "./MarkdownBody";

interface PlanViewProps {
  plan: Plan;
  editable?: boolean;
  onPlanChange?: (plan: Plan) => void;
  attachments?: { filename: string; url: string; mime_type: string | null }[];
}

export function PlanView({
  plan,
  editable = false,
  onPlanChange,
  attachments = [],
}: PlanViewProps) {
  const [editing, setEditing] = useState(false);
  const [initialDocument, setInitialDocument] = useState(() =>
    planToHtml(plan),
  );
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!editing) setInitialDocument(planToHtml(plan));
  }, [editing, plan]);

  const save = () => {
    onPlanChange?.(
      htmlToPlan(editorRef.current?.innerHTML ?? initialDocument, plan),
    );
    setEditing(false);
  };

  const cancel = () => {
    setEditing(false);
  };

  const format = (command: string, value?: string) => {
    editorRef.current?.focus();
    document.execCommand(command, false, value);
  };

  const addHeading = () => {
    editorRef.current?.focus();
    document.execCommand(
      "insertHTML",
      false,
      '<h2 contenteditable="true">New section</h2><p>Write here...</p>',
    );
  };

  if (editing) {
    return (
      <div className="card">
        <div className="card-head">
          <h3>Edit plan for {plan.ticket_key}</h3>
          <div className="page-actions">
            <button type="button" className="btn-secondary" onClick={cancel}>
              Cancel
            </button>
            <button type="button" onClick={save}>
              Save plan
            </button>
          </div>
        </div>
        <AttachmentPreview attachments={attachments} />
        <div
          className="editor-toolbar"
          role="toolbar"
          aria-label="Plan formatting tools"
        >
          <button
            type="button"
            className="editor-tool editor-tool-bold"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => format("bold")}
            title="Bold"
          >
            B
          </button>
          <button
            type="button"
            className="editor-tool"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => format("formatBlock", "h2")}
            title="Heading"
          >
            Heading
          </button>
          <button
            type="button"
            className="editor-tool"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => format("formatBlock", "h3")}
            title="Small heading"
          >
            Subheading
          </button>
          <button
            type="button"
            className="editor-tool"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => format("insertUnorderedList")}
            title="Bulleted list"
          >
            &bull; List
          </button>
          <button
            type="button"
            className="editor-tool editor-tool-add"
            onClick={addHeading}
            title="Add a new section"
          >
            + Add heading
          </button>
        </div>
        <div
          ref={editorRef}
          className="plan-editor"
          contentEditable
          suppressContentEditableWarning
          dangerouslySetInnerHTML={{ __html: initialDocument }}
          aria-label="Implementation plan editor"
          role="textbox"
          aria-multiline="true"
          spellCheck
        />
      </div>
    );
  }

  const steps = plan.steps;
  const hasRisks = plan.risks && plan.risks.trim().length > 0;
  const hasScope = plan.out_of_scope && plan.out_of_scope.trim().length > 0;
  const hasQuestions =
    plan.open_questions && plan.open_questions.length > 0;
  const addressedWarnings = plan.addressed_warnings ?? [];

  return (
    <div className="card">
      <div className="card-head">
        <h3>Plan for {plan.ticket_key}</h3>
        {editable && (
          <button
            type="button"
            className="rd-btn"
            onClick={() => setEditing(true)}
          >
            Edit plan
          </button>
        )}
      </div>

      <AttachmentPreview attachments={attachments} />

      <div className="rd-plan">
        {/* 01. Summary */}
        <div className="rd-plan-section">
          <span className="rd-plan-num">01. Summary</span>
          <div className="rd-plan-card">
            <MarkdownBody text={plan.summary} />
          </div>
        </div>

        {/* 02. Proposed Architecture & Approach */}
        <div className="rd-plan-section">
          <span className="rd-plan-num">
            02. Proposed Architecture &amp; Approach
          </span>
          <MarkdownBody text={plan.approach} />
        </div>

        {addressedWarnings.length > 0 && (
          <div className="rd-plan-section">
            <span className="rd-plan-num">03. Warning Measures</span>
            <div className="rd-warning-measures">
              {addressedWarnings.map((item, i) => (
                <div className="rd-warning-measure" key={`${item.warning}-${i}`}>
                  <div className="rd-warning-measure-warning">
                    <span className="rd-warning-measure-label">Warning found in planning</span>
                    <MarkdownBody text={item.warning} />
                  </div>
                  <div className="rd-warning-measure-resolution">
                    <span className="rd-warning-measure-label">Updated plan to address this warning</span>
                    <MarkdownBody text={item.resolution} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 03. Concrete Steps */}
        {steps.length > 0 && (
          <div className="rd-plan-section">
            <span className="rd-plan-num">
              04. Concrete Steps &amp; Execution
            </span>
            <div className="rd-plan" style={{ gap: 8 }}>
              {steps.map((step, i) => (
                <div key={i} className="rd-plan-step">
                  <div className="rd-plan-step-header">
                    <div className="rd-plan-step-left">
                      <span className="rd-plan-step-badge">{i + 1}</span>
                      <span className="rd-plan-step-title">{step.title}</span>
                    </div>
                  </div>
                  {step.detail && (
                    <div className="rd-plan-step-body">
                      <p className="rd-plan-step-desc">{step.detail}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 04. Risks & Mitigations */}
        {hasRisks && (
          <div className="rd-plan-section">
            <span className="rd-plan-num">04. Risks &amp; Mitigations</span>
            <div className="rd-plan-card">
              <MarkdownBody text={plan.risks!} />
            </div>
          </div>
        )}

        {/* 05/06. Out of Scope & Open Questions */}
        {(hasScope || hasQuestions) && (
          <div className="rd-plan-footer-grid">
            {hasScope && (
              <div className="rd-plan-footer-card scope">
                <span className="rd-plan-footer-label scope">
                  Out of Scope
                </span>
                <div className="rd-plan-footer-desc">
                  <MarkdownBody text={plan.out_of_scope!} />
                </div>
              </div>
            )}
            {hasQuestions && (
              <div className="rd-plan-footer-card questions">
                <span className="rd-plan-footer-label questions">
                  Open Questions
                </span>
                <div className="rd-plan-footer-desc">
                  {plan.open_questions.map((q) => (
                    <div key={q.id} style={{ marginBottom: 6 }}>
                      <strong>{q.question}</strong>
                      {q.why_it_matters && (
                        <span> — {q.why_it_matters}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Custom sections */}
        {plan.sections.map((section) => (
          <div
            key={`${section.title}-${section.content}`}
            className="rd-plan-section"
          >
            <span className="rd-plan-num">{section.title}</span>
            <div className="rd-plan-card">
              <MarkdownBody text={section.content} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AttachmentPreview({
  attachments,
}: {
  attachments: PlanViewProps["attachments"];
}) {
  const images =
    attachments?.filter((attachment) =>
      attachment.mime_type?.startsWith("image/"),
    ) ?? [];
  if (images.length === 0) return null;
  return (
    <div className="plan-visual-references">
      <h4>Visual reference</h4>
      <p className="muted small">
        Mandatory reference: replicate these Salesforce UI designs exactly using
        LWC.
      </p>
      <div className="plan-visual-grid">
        {images.map((attachment) => (
          <figure key={attachment.filename}>
            <img
              src={attachment.url}
              alt={`Visual reference: ${attachment.filename}`}
            />
            <figcaption>{attachment.filename}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

function planToHtml(plan: Plan): string {
  const steps = plan.steps
    .map(
      (step) =>
        `<li><strong>${escapeHtml(step.title)}</strong>: ${escapeHtml(step.detail)}</li>`,
    )
    .join("");
  const sections = plan.sections.map(
    (section) =>
      `<h2 contenteditable="true">${escapeHtml(section.title)}</h2><p>${escapeHtml(section.content)}</p>`,
  );
  return [
    `<h2 contenteditable="false">Summary</h2><p>${escapeHtml(plan.summary)}</p>`,
    `<h2 contenteditable="false">Approach</h2><p>${escapeHtml(plan.approach)}</p>`,
    `<h2 contenteditable="false">Steps</h2><ul>${steps}</ul>`,
    `<h2 contenteditable="false">Risks</h2><p>${escapeHtml(plan.risks ?? "")}</p>`,
    `<h2 contenteditable="false">Out of scope</h2><p>${escapeHtml(plan.out_of_scope ?? "")}</p>`,
    ...sections,
  ].join("");
}

function htmlToPlan(html: string, original: Plan): Plan {
  const parsed = new DOMParser().parseFromString(
    `<body>${html}</body>`,
    "text/html",
  ).body;
  const sections = new Map<string, string[]>();
  const customSections: { title: string; content: string }[] = [];
  const knownSections = new Set([
    "summary",
    "approach",
    "steps",
    "risks",
    "out of scope",
  ]);
  let currentSection = "";

  for (const element of Array.from(parsed.children)) {
    if (/^H[1-3]$/.test(element.tagName)) {
      currentSection = element.textContent?.trim().toLowerCase() ?? "";
      sections.set(currentSection, []);
      if (currentSection && !knownSections.has(currentSection)) {
        customSections.push({
          title: element.textContent?.trim() ?? "",
          content: "",
        });
      }
    } else if (currentSection) {
      const content =
        element.tagName === "UL" || element.tagName === "OL"
          ? Array.from(element.querySelectorAll(":scope > li"))
              .map((item) => item.textContent?.trim() ?? "")
              .join("\n")
          : (element.textContent?.trim() ?? "");
      if (content) {
        sections.get(currentSection)?.push(content);
        const customSection = customSections[customSections.length - 1];
        if (customSection && !knownSections.has(currentSection)) {
          customSection.content = [
            ...(customSection.content ? [customSection.content] : []),
            content,
          ].join("\n");
        }
      }
    }
  }

  const content = (name: string) =>
    (sections.get(name) ?? []).join("\n").trim();
  const steps = (sections.get("steps") ?? [])
    .join("\n")
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const separator = line.indexOf(":");
      return separator < 0
        ? { title: line, detail: "" }
        : {
            title: line.slice(0, separator).trim(),
            detail: line.slice(separator + 1).trim(),
          };
    });

  return {
    ticket_key: original.ticket_key,
    summary: content("summary"),
    approach: content("approach"),
    steps,
    sections: customSections.filter(
      (section) => section.title && section.content,
    ),
    open_questions: original.open_questions ?? [],
    risks: content("risks") || null,
    out_of_scope: content("out of scope") || null,
    addressed_warnings: original.addressed_warnings ?? [],
  };
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
