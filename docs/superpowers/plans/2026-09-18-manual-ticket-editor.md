# Manual Ticket Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Jira Group-first manual-ingest workflow with a simple rich-text ticket editor and a monitorable table whose records move through `todo`, `progress`, `agentcompleted`, and `completed`.

**Architecture:** Add standalone manual work-item records linked directly to a project, with a generated public ID and a sanitized rich-text document. Keep the existing Jira Group read/import path temporarily so existing data and projects continue to work, but make the new editor and monitor table use the standalone records. Pipeline status updates remain backend-owned and are idempotent.

**Tech Stack:** React 19, TypeScript, native `contentEditable` with a small toolbar, FastAPI, Pydantic, SQLAlchemy, SQLite additive migrations, pytest.

**Spec:** User request in chat: simple text editor with bold, italic, bullets, alignment, saved DB record/ID, and a table monitor for Todo, Completed, Agent Completed, and Progress.

## Global Constraints

- Preserve current Jira Group data and existing project imports during the transition.
- Allow only the editor features explicitly requested: bold, italic, unordered/ordered lists, and left/center/right alignment.
- Store sanitized HTML plus plain text; never render unsanitized user HTML.
- Status values are exactly `todo`, `progress`, `agentcompleted`, and `completed`.
- Pipeline transitions are authoritative; UI status edits are allowed only before execution begins or for operational correction with an audit timestamp.
- Existing attachment support remains optional and is not required for the first editor release.
- Do not add a rich-text dependency unless native editing fails an accessibility or browser compatibility check.

---

### Task 1: Define The Standalone Work-Item Contract

**Files:**
- Create: `database/models/manual_work_item.py`
- Modify: `database/models/__init__.py`
- Modify: `database/session.py`
- Modify: `backend/schemas.py`
- Test: `tests/test_manual_work_items.py`

**Interfaces:**
- Produces `ManualWorkItem` with `id`, `public_id`, `project_id`, `title`, `body_html`, `body_text`, `status`, `created_at`, `updated_at`, and optional `started_at`, `agent_completed_at`, `completed_at`.
- Produces `ManualWorkItemStatus = Literal["todo", "progress", "agentcompleted", "completed"]`.
- `public_id` is generated server-side in the form `MT-000001` using a database-safe sequence strategy; clients never provide it.

- [ ] **Step 1: Write the failing persistence tests**

```python
def test_create_work_item_gets_id_and_todo_status(client):
    project = _create_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/manual-tickets",
        json={"title": "Add lead field", "body_html": "<p><strong>Verify</strong> metadata.</p>"},
    )
    assert response.status_code == 201
    item = response.json()
    assert item["public_id"].startswith("MT-")
    assert item["status"] == "todo"
    assert item["body_text"] == "Verify metadata."
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/test_manual_work_items.py -q`
Expected: FAIL because the standalone table and endpoint do not exist.

- [ ] **Step 3: Add the SQLAlchemy model and additive migration**

Use a UUID primary key for internal references, a unique `public_id`, a foreign key to `projects.id`, and a check constraint for the four statuses. Add the table to `init_db()` imports and make the SQLite migration additive so existing databases are not rebuilt.

- [ ] **Step 4: Add schemas and implement the persistence test setup**

Define create, update, list, and status response schemas. Derive `body_text` from the sanitized HTML on the server; do not trust a client-supplied plain-text copy.

- [ ] **Step 5: Run the focused test**

Run: `pytest tests/test_manual_work_items.py -q`
Expected: PASS for creation, generated ID, default status, duplicate-safe IDs, and project scoping.

---

### Task 2: Add Safe Rich-Text Normalization

**Files:**
- Create: `backend/services/manual_work_items.py`
- Test: `tests/test_manual_work_items.py`

**Interfaces:**
- Produces `sanitize_manual_html(raw_html: str) -> tuple[str, str]`.
- Allowed tags: `p`, `br`, `strong`, `em`, `ul`, `ol`, `li`, `div`.
- Allowed alignment representation: `data-align="left|center|right"` on block elements; reject all style, class, URL, script, event, iframe, and image attributes.
- Returns `(safe_html, plain_text)`.

- [ ] **Step 1: Add failing sanitizer tests**

```python
def test_sanitizer_keeps_editor_markup_and_removes_scripts():
    safe_html, plain_text = sanitize_manual_html(
        '<p style="color:red"><strong>Build</strong> <em>field</em></p>'
        '<script>alert(1)</script><ul><li>Verify</li></ul>'
    )
    assert safe_html == "<p><strong>Build</strong> <em>field</em></p><ul><li>Verify</li></ul>"
    assert plain_text == "Build field\nVerify"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_manual_work_items.py::test_sanitizer_keeps_editor_markup_and_removes_scripts -q`
Expected: FAIL because the sanitizer does not exist.

- [ ] **Step 3: Implement the allowlist sanitizer**

Use `html.parser.HTMLParser` or an already-installed sanitizer. Preserve only the explicit tags and alignment values, escape text, and normalize whitespace. Keep this helper shared by create and update endpoints.

- [ ] **Step 4: Run sanitizer and regression tests**

Run: `pytest tests/test_manual_work_items.py -q`
Expected: PASS, including malformed HTML, script removal, and empty-body rejection.

---

### Task 3: Add CRUD And Monitoring Endpoints

**Files:**
- Create: `backend/routers/manual_work_items.py`
- Modify: `backend/main.py`
- Test: `tests/test_manual_work_items.py`

**Interfaces:**
- `POST /api/projects/{project_id}/manual-tickets` creates a `todo` record.
- `GET /api/projects/{project_id}/manual-tickets?status=todo` lists records newest first.
- `GET /api/projects/{project_id}/manual-tickets/{public_id}` returns the editor document and timestamps.
- `PATCH /api/projects/{project_id}/manual-tickets/{public_id}` updates title/body and resets no status automatically.
- `PATCH /api/projects/{project_id}/manual-tickets/{public_id}/status` changes status with validation.
- `DELETE /api/projects/{project_id}/manual-tickets/{public_id}` deletes only records not currently `progress`.

- [ ] **Step 1: Add endpoint tests**

Cover create, list-by-status, update HTML sanitization, missing project/ID, invalid status `422`, and deletion protection while `progress`.

- [ ] **Step 2: Implement the router and register it**

Resolve by `project_id` and `public_id`, return `404` for cross-project access, and use `201` for creation. Centralize status transition validation so UI and pipeline use the same function.

- [ ] **Step 3: Run backend tests**

Run: `pytest tests/test_manual_work_items.py tests/test_ticket_ingestion.py -q`
Expected: PASS; existing Jira Group behavior remains intact.

---

### Task 4: Replace The Manual-Ingest UI With Editor + Monitor Table

**Files:**
- Modify: `frontend/src/pages/ManualIngestPage.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Add API methods matching Task 3.
- Add `ManualWorkItem` and `ManualWorkItemStatus` TypeScript types.
- Editor state tracks title, `bodyHtml`, selected alignment, and save state.

- [ ] **Step 1: Build the editor shell**

Use a labeled title input and a `contentEditable` region. Toolbar buttons call `document.execCommand` only for the supported commands (`bold`, `italic`, `insertUnorderedList`, `insertOrderedList`, `justifyLeft`, `justifyCenter`, `justifyRight`), preserve selection, and expose `aria-label` plus visible focus styles.

- [ ] **Step 2: Add save and edit behavior**

Save sends the HTML to the API, clears the editor after a new record is created, and reloads the monitor table. Editing an existing row loads its sanitized HTML into the editor and preserves its public ID.

- [ ] **Step 3: Add the monitoring table**

Columns: ID, title, status, created/updated, and actions. Add status filter tabs or a compact segmented control for All, To Do, In Progress, Agent Completed, and Completed. Show counts for each status and make the status badge readable without color alone.

- [ ] **Step 4: Add empty/loading/error states**

Use explicit states for no tickets, saving, loading, failed save, and stale polling. Poll the project’s list endpoint using the existing `usePolling` pattern.

- [ ] **Step 5: Run frontend validation**

Run: `npm run build` from `frontend/` and `npm run lint` from `frontend/`.
Expected: both pass with no TypeScript errors.

---

### Task 5: Connect Pipeline Runs To Standalone Work Items

**Files:**
- Modify: `backend/services/pipeline_runner.py`
- Modify: `backend/routers/runs.py`
- Modify: `backend/routers/tickets.py`
- Modify: `agents/pipeline_agent/ingestion.py`
- Test: `tests/test_manual_work_items.py`

**Interfaces:**
- Pipeline start resolves a standalone manual work item by project and public ID.
- Status transitions are idempotent: start -> `progress`, successful implementation -> `agentcompleted`, successful finalization -> `completed`.
- Failure/cancel does not falsely mark `completed`; it remains `progress` or is explicitly extended later with a `failed` state only if product requirements change.

- [ ] **Step 1: Add transition tests**

```python
def test_pipeline_status_transitions_are_idempotent():
    item = create_item()
    set_status(item, "progress")
    set_status(item, "agentcompleted")
    set_status(item, "completed")
    assert get_item(item.public_id).status == "completed"
```

- [ ] **Step 2: Resolve standalone records in ticket fetch/run creation**

Teach project ticket listing and ticket detail to return `ManualWorkItem` records as the local source. Keep the Jira Group fallback for old projects and existing imported tickets.

- [ ] **Step 3: Move lifecycle updates into the shared run transitions**

Update status at the same backend boundaries that update `PipelineRun`, and make the Jira completion callback conditional on `settings.jira is not None` so local manual tickets never call a live Jira API.

- [ ] **Step 4: Run the backend regression suite**

Run: `pytest tests/test_manual_work_items.py tests/test_ticket_ingestion.py tests/test_pipeline_concurrency.py -q`
Expected: PASS with existing Jira/Azure behavior unchanged.

---

### Task 6: Migrate And Deprecate The Jira Group UI

**Files:**
- Modify: `frontend/src/pages/ManualIngestPage.tsx`
- Modify: `backend/routers/ticket_groups.py`
- Modify: `docs/PIPELINE_PLAN.md` or the relevant project documentation
- Test: `tests/test_manual_work_items.py`

- [ ] **Step 1: Add a compatibility migration test**

Create an existing Jira Group ticket, link a project to it, and verify it remains visible and runnable after standalone manual tickets are enabled.

- [ ] **Step 2: Make the new editor the primary screen**

Remove group creation and group deletion from the main workflow. Keep an unobtrusive legacy view or backend endpoints for old records until migration is complete.

- [ ] **Step 3: Add an explicit migration path**

Provide a one-time backend migration/helper that copies each existing group ticket to a standalone record, preserves its external key in a `legacy_ticket_id` field, preserves content/attachments where applicable, and does not duplicate records on rerun.

- [ ] **Step 4: Document the new flow**

Document: create ticket -> save generated ID -> monitor table -> start pipeline from the ticket -> status progression. State that Jira Group is retained only for compatibility during migration.

- [ ] **Step 5: Run the full relevant suite**

Run: `pytest -q` and `npm run build` from `frontend/`.
Expected: all relevant tests pass and the application builds cleanly.

---

## Self-Review

- **Spec coverage:** The editor, formatting, save-generated-ID behavior, DB persistence, monitoring table, four statuses, pipeline synchronization, Jira Group compatibility, migration, and validation are covered by Tasks 1-6.
- **Security:** HTML is sanitized on the server and rendered only after sanitization; arbitrary attributes and scripts are rejected.
- **Data safety:** Existing Jira Group records are preserved and have a compatibility path before the UI is removed.
- **Status semantics:** `todo`, `progress`, `agentcompleted`, and `completed` are shared by DB, API, UI, and pipeline; no UI-only status is introduced.
- **Open product decision:** Decide whether failed/cancelled runs should keep `progress` or add a fifth `failed` status. The current plan keeps the user-requested four statuses and leaves the item in `progress`.
