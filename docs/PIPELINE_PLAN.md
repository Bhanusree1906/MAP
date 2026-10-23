# Pipeline Improvement Plan — living document

Owner: pipeline team · Branch: `dev/langgraph-manager` · Last updated: 2026-09-02

This is the single source of truth for the ticket→PR→org pipeline rework. Every
change goes through a task here. Keep the **Status** column current and add a line
to the Changelog when a task moves to `done` or `verified`.

## Status legend

| Mark | Meaning |
|---|---|
| `todo` | not started |
| `wip` | being implemented |
| `done` | code merged, unit tests pass |
| `verified` | proven on a real ticket run (trace in Changelog) |
| `blocked` | waiting on something (note it) |

## How we work each task

1. Read **Why** + **Where** + **Done when**.
2. Implement on `dev/langgraph-manager` (or a short-lived branch off it).
3. Add/extend unit tests named in **Done when**. `pytest -q test_manager_agent.py test_salesforce_bootstrap.py` must stay green.
4. Move Status to `done`, add a Changelog line.
5. Run one real ticket through the pipeline. If the trace confirms the win, move to `verified` and record the run id + before/after numbers.
6. If a task changes behaviour we later regret, revert and log it — the plan is allowed to change.

## Baseline numbers (2026-09-02, project `171ccc17`)

| phase | now |
|---|---|
| planning engine exec | 70–118 s |
| execution `npm ci` | 27–35 s |
| execution: Codex worker sessions per ticket | 1–5 (5 for a one-field ticket) |
| total wall / ticket | 7–16 min |
| PR merges by the pipeline, ever | 0 |
| real (non-dry-run) org deploys by the pipeline, ever | 0 |
| planning runs that raised a `risks` blob | 15 / 15 |

Targets: metadata-only ticket **≤ 3 min**, Apex ticket **≤ 7 min**, LWC ticket **≤ 10 min**,
every green ticket ends **merged + deployed to the org**, most tickets approve in **one click**.

---

# WS-0 — Already shipped (commit `d7d366a`, 2026-09-02)

Recorded here so we don't redo it. All `done`, none `verified` yet.

| ID | Change | Status |
|---|---|---|
| 0.1 | `review` uses `_range_base()` (local `<default>` only if it resolves, else `origin/<default>`); no more empty-diff false "nothing delivered" (AG-195) | done |
| 0.2 | `_do_review` feeds the committed diff + commit list + stat + file list; tells reviewer the tree is clean because it's committed | done |
| 0.3 | Empty filtered diff while branch has commits → `ERROR` (git-range problem), manager told NOT to re-delegate | done |
| 0.4 | Identical `delegate_worker` brief is REFUSED with the last gate text | done |
| 0.5 | `.pipeline/ORG_CATALOG.md` written once per run from project memory; worker brief points at it | done |
| 0.6 | `_tooling_block` — real "TOOLS ALREADY SET UP" list; dropped the dead `salesforce-org-harness` reference | done |
| 0.7 | `CODEX_REVIEW_MODEL` (default `gpt-5.6-terra`) / `CODEX_REVIEW_REASONING_EFFORT` (default `medium`) | done |
| 0.8 | `_exclude_pipeline_files` — `.git/info/exclude` + `git update-index --skip-worktree` for tracked scaffolding (TICKET.md), incl. `package-lock.json`, `node_modules/`; runs AFTER branch checkout | done |
| 0.9 | `_npm_install_project` — `npm ci` (never rewrites lockfile); fallback `--no-save --package-lock=false`; `git checkout -- package-lock.json` after | done |
| 0.10 | `generate_github_memory` skipped in the implementation phase (`PIPELINE_AGENT_GITHUB_MEMORY=always` to force) | done |
| 0.11 | `salesforce.Dockerfile` — OpenJDK 17 + `JAVA_HOME`, `sf` code-analyzer plugin pre-installed, global `prettier`/`prettier-plugin-apex`/`@prettier/plugin-xml`/`sfdx-lwc-jest`/`eslint`, `NODE_PATH`, `HUSKY=0`, npm cache warm | done |

**Action: rebuild the salesforce image before the next run.**
`docker build --no-cache -t agent-sandbox-salesforce:latest -f sandbox/salesforce.Dockerfile .`

---

# WS-1 — Full delivery cycle: merge + real org deploy

The pipeline currently stops at "PR opened". Your org is empty because nothing is
ever deployed to it.

### 1.1 `ship` tool — real deploy then merge  ·  Status: `done` (uncommitted)

- **Why:** 0 real deploys, 0 merges in the whole DB. A PR never touches the org.
- **Where:** `agents/pipeline_agent/manager.py` — `class ship`, `_do_ship`, `_auto_ship_enabled`, `_TOOLS`, `_SYSTEM_PROMPT` step 6, `ManagerOutcome.merged/.deployed`, `_Session.__init__` (`merged`/`deployed`/`last_ship`).
- **Behaviour:** after `deliver` on a passing review → real `sf project deploy start --source-dir force-app --wait 33 --ignore-conflicts` to the connected (default) org → then `gh pr merge --squash || --merge` → `git switch -C <default> origin/<default>`. Deploy fail = real org-rejection errors → manager re-delegates once (`gate_passed=None` unblocks it). Merge blocked by branch protection = fine, org already has the code, note it.
- **Env:** `PIPELINE_AGENT_AUTO_SHIP=0` for PR-only; `PIPELINE_AGENT_DEPLOY_TIMEOUT` (default 2400 s).
- **Done when:** `test_ship_deploys_to_org_then_merges`, `test_ship_deploy_failure_reopens_delegation`, `test_ship_refused_before_deliver`, `test_delivered_refused_when_pr_open_but_not_shipped` — all present, green.
- **Verify:** run a metadata ticket end to end; confirm `sf` shows the component in the org (`sf org list metadata --metadata-type <Type>`), the PR is merged, Jira comment has the deploy id.

### 1.2 Never report `blocked` without a worker attempt  ·  Status: `done` (uncommitted)

- **Why:** AG-217 went `inspect_context → report_to_ticket(blocked)` in 2 steps because a default value was unspecified.
- **Where:** `manager.py` `_do_report_to_ticket` guard; `_SYSTEM_PROMPT` "MAKE THE CALL" paragraph; `_skill_prefix` "DECIDE, DO NOT STALL".
- **Done when:** `test_blocked_refused_without_a_single_worker_attempt` green.
- **Verify:** re-run AG-217 — it should implement with `Lead_Priority__c` default = `Normal` (documented assumption), not block.

### 1.3 Commit WS-1 tail  ·  Status: `done`

- Committed as `f5f0c2a` "Manager: ship step (real deploy + merge) + no-blocked-without-trying".

---

# WS-2 — Environment baked into the image

### 2.1 Keep `node_modules` in the image, symlink at boot  ·  Status: `done` (needs image rebuild to take effect)

- **Why:** `npm ci` is 27–35 s every execution, even for tickets that touch zero `.js`. The pipeline generates the repo (`sf project generate --template standard`), so it controls `package.json`.
- **Where:** `sandbox/salesforce.Dockerfile` — canonical SF DX devDeps `npm install`ed into `/opt/sf-toolchain/node_modules` and KEPT (was `rm -rf`d). `agents/pipeline_agent/sandbox_runtime.py` `_npm_install_project` — `PIPELINE_AGENT_NPM_MODE=auto|link|ci` (default auto): if the baked dir exists and the repo has no `node_modules`, `ln -s` it (~0.5 s); else `npm ci`.
- **Done when:** `TestNpmInstallProject::test_symlinks_the_baked_toolchain_when_present` + `test_falls_back_to_npm_ci_when_no_baked_toolchain`. ✓
- **Verify (after rebuild):** metadata ticket run shows a "Linked baked npm toolchain" step and NO `npm ci` timing event.

### 2.2 `sf` non-interactive env  ·  Status: `done` (needs image rebuild)

- **Why:** 18–27 ANSI spinner-spam events per run in the DB; `sf` telemetry + auto-update checks add cold-start cost.
- **Done:** `salesforce.Dockerfile` `ENV SF_DISABLE_TELEMETRY / SF_AUTOUPDATE_DISABLE / SF_DISABLE_AUTOUPDATE / SF_NO_SPINNER / SF_DISABLE_LOG_FILE = true`. Belt-and-suspenders with `_clean()` (WS-5.4).
- **Still todo:** switch pipeline-issued `sf deploy` to `--json` + parse for deploy id / pass-fail; pre-warm code-analyzer engines at build.
- **Verify (after rebuild):** a real deploy event has no `^[[`; `sf --help` starts faster.

### 2.3 Rebuild + document  ·  Status: `todo`

- Update `Readme.md` build section with the new env + timings. Rebuild both images. Record image digest in Changelog.

---

# WS-3 — Setup cuts (small, cumulative)

### 3.1 Parallelize boot  ·  Status: `partial`

- **Done:** `configure_sandbox_git` collapsed 7 serial `sandbox.execute` calls into one `&&` script.
- **Deferred:** threaded skill-install / clone overlap — risk vs ~3 s payoff; revisit after the classifier (5.3) lands.

### 3.2 Scope the stale-branch check server-side  ·  Status: `done`

- **Where:** `_delete_stale_feature_branches` — `git ls-remote --heads origin 'refs/heads/feature/<TICKET>' 'refs/heads/feature/<TICKET>-*'` so the server returns only this ticket's refs (no full-repo head list); kept a client-side guard so a non-globbing git can't delete `main`.
- **Done when:** `TestStaleFeatureBranchCleanup` (3 tests) green. ✓

### 3.3 `github memory` off by default (both phases)  ·  Status: `done`

- **Where:** `open_pipeline_sandbox` — `PIPELINE_AGENT_GITHUB_MEMORY` default `off` (was `auto`=planning). `always` = both phases, `planning` = planning only. The planner inspects the repo directly anyway.
- **Verify:** planning timing events have no `github memory` line. Saves 5–7 s.

---

# WS-4 — Context & prompt cuts (kills per-session re-discovery)

### 4.1 Fold skills into `~/.codex/AGENTS.md`  ·  Status: `done`

- **Why:** Codex only auto-loads `~/.codex/AGENTS.md`. The 7–10 `SKILL.md` files sat unused in `~/.codex/skills/`; the worker `cat`ed them by hand every session and guessed wrong paths (`.system/`, then `find`).
- **Where:** `agents/skills/service.py` — `install_into_sandbox` / `install_memory_into_sandbox` now return `skill_md` (the body). `agents/pipeline_agent/sandbox_runtime.py` — `_fold_skills_into_agents_md(persona, [(slug, body), …])` rebuilds `~/.codex/AGENTS.md` = persona + `# Loaded skills` + `## Skill: <slug>` sections; called after skill+memory install, **codex engine only** (opencode keeps its `opencode.json` `instructions` path list). `manager._tooling_block` line updated to "already in AGENTS.md, don't cat".
- **Done when:** `TestFoldSkillsIntoAgentsMd` green. ✓
- **Verify:** next run trace — 0 `cat …/SKILL.md`, 0 `find … SKILL.md` in worker sessions.

### 4.2 Slim the org catalog  ·  Status: `done`

- **Why:** `render_catalog_markdown` printed `sf org list metadata --metadata-type CustomObject` unfiltered → ~800 standard/system names; permission-set/app/apex/lwc sections dumped every managed-package entry. ~12.5 KB, re-read every session.
- **Where:** `agents/pipeline_agent/org_catalog.py` — new `_slim(names, cap=40)` drops namespaced/managed entries + caps with an overflow note; custom-objects section = genuinely-local `*__c`/`__mdt`/… only (`_is_local_custom`); `_section` uses `_slim` and a comma list instead of a bullet per line.
- **Done when:** `test_custom_objects_section_drops_standard_and_managed_entries`, `test_slim_caps_and_reports_overflow` green. ✓
- **NOTE:** takes effect on the **next Bootstrap Salesforce run** per project (it regenerates stored project memory). Existing projects keep the old 12 KB catalog until re-bootstrapped. Also feed the slim catalog into `_opening()` (already done in 4.3 — it reads `fetch_memory_markdown`).
- **Verify:** re-bootstrap project `171ccc17`, check `project_memories.byte_size` < 4000.

### 4.3 Front-load the manager; forbid early `validate`  ·  Status: `done`

- **Why:** AG-212 tool sequence was `inspect_context ×2 → validate → inspect_context ×2 → delegate_worker`. Each is a Gemini round-trip on nothing.
- **Where:** `manager._opening(session)` now inlines TICKET.md + approved plan + org catalog + skill list. `dispatch` REFUSES `validate` before the first `delegate_worker`. `_SYSTEM_PROMPT` step 1 rewritten. `inspect_context` still available for `branch` / `validation`.
- **Done when:** `test_validate_refused_before_first_delegate` green; 5 existing `test_validate_*` seeded with a delegate marker. ✓
- **Verify:** next run trace — `delegate_worker` is the 1st or 2nd manager tool call, no `inspect_context(ticket|plan|org_memory|skills)`.

### 4.4 Conditional skill install by ticket type  ·  Status: `deferred`

- **Why:** `experience-lwc-generate` (246 KB) installs on field-only tickets.
- **Deferred:** `open_pipeline_sandbox` installs skills BEFORE TICKET.md is written, so it has no ticket type yet. Needs the classifier plumbed from `agent.open_execution_workspace` (which fetches the ticket markdown) into `open_pipeline_sandbox` as a skill allowlist. The AGENTS.md fold (WS-4.1) already cut the per-skill read cost; do this after WS-7.

---

# WS-5 — Loop & model cuts

### 5.1 Worker on `gpt-5.6-terra`  ·  Status: `done`

- **Why:** `luna` gets cut off and restarts 4–5×; each restart re-pays discovery. One `terra` session that finishes is faster and cheaper.
- **Done:** `config._DEFAULT_CODEX_EXECUTION_MODEL = "gpt-5.6-terra"`; `CODEX_EXECUTION_MODEL` / `CODEX_MODEL` still override. Planning stays `luna`.
- **Verify:** an Apex ticket completes in ≤ 2 worker sessions.

### 5.2 Single validation owner  ·  Status: `done`

- **Why:** worker dry-runs, then `_run_validate` dry-runs, then every re-delegate dry-runs. 3+ deploys/run.
- **Done:** `_tooling_block` + `_SYSTEM_PROMPT` step 2 + COMMIT DISCIPLINE now say: worker runs the ONE focused Apex test + `sf code-analyzer` once, commits/pushes, and does NOT run `sf project deploy --dry-run` — `_run_validate` is the single owner.
- **Done when:** `test_prefix_tells_worker_to_commit_before_verifying` updated. ✓
- **Verify:** a run trace has one `--dry-run` per validate call, none from the worker.

### 5.3 Ticket classifier + per-type budgets  ·  Status: `done`

- **Done:** `agents/pipeline_agent/classify.py` — `classify_ticket(ticket_md, plan_text)` → `metadata-single | metadata | apex | lwc | flow | mixed`; `budget_for()` → `(max_turns, light_review)`. `_Session` reads it in `__init__` (from plan_text) and `run_managed_pipeline` refines it from TICKET.md. `after_agent`/`after_tools` use `session.max_turns` (8 for metadata-single … 24 for mixed). `_review_model()` returns the planning model when `light_review`.
- **Done when:** `TestClassifyTicket` (2 tests) green. ✓
- **Not yet wired:** "skip planning for metadata-single" — that lives in `pipeline_runner` and ties into WS-7; do it there.
- **Verify:** manager "taking the ticket" event shows the type + turn cap; metadata ticket ends in ≤ 8 turns.

### 5.4 Strip noise from everything fed back to the model  ·  Status: `done`

- **Where:** `manager._clean(text, keep_last_lines=None)` — strips ANSI, drops `sf` spinner lines (`Elapsed Time:`, `Waiting for the org…`, `Deploying Metadata … N%`), collapses blank runs, optional tail-trim. Applied in `_run_engine` (stdout/stderr), `_note` (diary), `_run_validate` (dry-run output, keep_last_lines=40), `_do_ship` (deploy output, keep_last_lines=50).
- **Done when:** `TestClean` (2 tests) green; `_run_validate`/diary output has no `^[[`. ✓
- **Verify:** next real run — diary events contain no `^[[` bytes.

---

# WS-6 — Readable PR body & Jira comment

### 6.1 Dedicated PR body template  ·  Status: `done` (PR body); `todo` (Jira comment tightening)

- **Why:** `deliver` used `self.last_validation` verbatim as the PR body — `PASSED` + git dumps + 4 000 chars of `sf` output incl. ANSI.
- **Where:** `manager._pr_body()` + `manager._assumptions()`. `_do_deliver` now calls `_pr_body()`.
  - **PR body:** `## <TICKET>` / one-line intro / `### Commits` (from `git log --format=%s <base>..HEAD`) / `### Files changed` (`git diff --name-status <base>...HEAD` minus the `:(exclude)` scaffolding) / `### Verified` (dry-run pass · review verdict) / `### Assumptions made for unspecified details` (from `Assumption:` lines in the diary).
- **Done when:** `TestPrBody::test_pr_body_is_structured_no_ansi_no_branch_dump` green. ✓
- **Still todo:** the Jira comment is LLM-written prose today (mostly OK). Add a light post-process: ensure it has a Deploy line + PR link + Assumptions section, strip any code fences of git output.
- **Verify:** eyeball the next real PR on GitHub.

---

# WS-7 — Planning: fewer questions, real answers, re-plan loop

### 7.1 Split `risks` into typed `open_questions` + `notes`  ·  Status: `done`

- **Why:** 15/15 planning runs emitted a dense `risks` paragraph; most are decidable defaults or standard-practice reminders, not blockers.
- **Where:** `agents/pipeline_agent/schemas.py`:
  ```python
  class OpenQuestion(BaseModel):
      id: str            # stable slug: "default-priority-value"
      question: str
      why_it_matters: str
      assumed_default: str      # REQUIRED — what planner does if unanswered
      blocking: bool            # true only when no safe default exists
  # ImplementationPlan: open_questions: list[OpenQuestion] = []
  # keep `risks` -> rename role to `notes` (FYI prose, never a checkbox)
  ```
  `PLAN_JSON_INSTRUCTIONS` + `runner._codex_plan_instruction`: *"Raise an open_question ONLY when there is no reasonable default or the choice materially changes the deliverable. Otherwise pick the conventional default, put it in assumed_default, reflect it in the steps. 'Retrieve metadata before editing' is a step. A ticket prerequisite the org lacks IS a blocking question."*
- Feed the planner the slim catalog (4.2) so it stops asking about listed permission sets.
- **Done when:** `test_plan_schema_open_questions`; re-run AG-212/AG-217 planning → 0 blocking questions.

### 7.2 Per-question responses in `ApprovalIn` + UI  ·  Status: `done`

- **Where:** `backend/schemas.py` `ApprovalIn`:
  ```python
  action: Literal["approve", "revise", "reject"] = "approve"
  question_responses: list[QuestionResponse] = []   # {question_id, choice: "default"|"answer", answer_text?}
  ```
  `reviewer_answers` stays optional, no longer load-bearing.
  `frontend/src/pages/RunDetailPage.tsx` — one card per `open_question`: question, why, `assumed_default`, radio **○ Use planner's default / ○ Answer: [text]**. Blocking → forced to "Answer", blocks submit until filled. Delete the "confirm each risk" checkbox strip and `splitRiskItems`.
  Buttons: **Approve with defaults** · **Revise plan** · **Reject**.
- **Done when:** `test_approval_in_question_responses`; manual: answer one question, dismiss another.

### 7.3 Re-plan loop  ·  Status: `done`

- **Where:**
  - `agents/pipeline_agent/run_states.py` — `REPLANNING = "replanning"`, add to `ACTIVE_RUN_STATUSES`.
  - `backend/routers/runs.py` `approve_or_reject_run` — `action == "revise"` → `status = REPLANNING`, call `start_replanning_phase(run_id, project_id, ticket_key, prev_plan_json, question_responses, edited_plan)`.
  - `agents/pipeline_agent/runner.py` — `run_codex_replanning`: same sandbox setup as planning, **revise** prompt: *"Here is the current PLAN.json and the reviewer's decisions. Produce a REVISED PLAN.json that already bakes every decision into approach + steps. Remove every resolved open_question. Only add a new one if a resolution surfaced a genuinely new unknown. Edit, do not re-derive."* Lower reasoning, capped output → target 30–40 s.
  - `backend/services/pipeline_runner.py` — `_run_replanning_phase` mirrors `_run_planning_phase`, new `ARTIFACT_PLAN` version, back to `AWAITING_APPROVAL`.
  - Round cap: 2 (counter in `run.metadata_`); after that only "Approve with defaults" / "Reject".
- **Skip re-plan** when `action == "approve"` with no edits and no answered blocking questions → straight to execution (planner defaults already in steps).
- **Done when:** `test_revise_triggers_replanning`, `test_replan_round_cap`; manual: v1 with a question → answer → v2 with the decision in the steps and no question.

### 7.4 Plan edits that persist  ·  Status: `done`

- **Where:** `backend/routers/runs.py` — on `revise` or `approve` with a changed plan, `create_artifact(ARTIFACT_PLAN, …)` a new version immediately.
  `frontend/src/components/PlanView.tsx` — replace `contentEditable` with real inputs (`<textarea>` per step detail, `<input>` per title, add/remove/reorder).
- **Done when:** edit a step, refetch, see the edit; `test_edited_plan_persisted_as_new_version`.

### 7.5 Drop the `plan_to_text` band-aid  ·  Status: `done` (reworded to "reviewer overrode these defaults")

- **Where:** `agents/pipeline_agent/schemas.py` `plan_to_text` — remove the *"Reviewer's answers to the above — follow these over your own assumptions"* block. Keep only a short optional `reviewer_answers` note.
- **Done when:** `test_plan_to_text_no_reviewer_override_block`.

---

# Build order (dependency-aware)

| Wave | Tasks | Rough effort | Payoff |
|---|---|---|---|
| A | 1.3 (commit), 2.3 (rebuild image), 6.1, 5.4 | 0.5 d | readable output, ship cycle live |
| B | 4.3, 4.2, 4.1 | 2 d | worker stops re-discovering every session |
| C | 2.1, 2.2, 3.1–3.3 | 1.5 d | ~40 s off every run |
| D | 5.1, 5.2, 5.3, 4.4 | 2 d | 5 sessions → 1–2; per-type budgets |
| E | 7.1, 7.2 | 2 d | one-click approve for most tickets |
| F | 7.3, 7.4, 7.5 | 3 d | real re-plan loop |

Waves A–D get a metadata ticket to ~3 min and every green ticket merged + deployed.
Waves E–F fix the approval experience.

---

# WS-8 — Fixes from the first real run (AG-214, run `df1198c7`, blocked in 24 turns, ~15 min)

Trace showed: ~10 `delegate_worker` cycles because a ListView wouldn't deploy;
worker **deleted existing `force-app/` metadata** trying to make it pass; the
baked `node_modules` symlink got staged as a change + broke global prettier's
plugin resolution; classifier said `mixed` (24-turn budget) off the negated
phrase "without changing … Flow … or code"; `_clean` left braille/glyph spinner
spam in the Jira comment; token usage recorded as **0** (not tracked at all for
the manager path).

### 8.1 Validate-fail retry cap  ·  Status: `done`
`_MAX_VALIDATE_RETRIES = 3`. `_do_validate` counts failures; after 3, both
`_do_validate`'s return and the `dispatch` `delegate_worker` guard tell the
manager to `report_to_ticket(blocked)`. Test: `test_delegate_refused_after_the_validate_retry_cap`.

### 8.2 Worker must not delete repo files  ·  Status: `done`
`_skill_prefix`: "NEVER delete or `git rm` an existing file under `force-app/` … never stage `node_modules`, `package-lock.json`, TICKET.md, `.pipeline/`."

### 8.3 `node_modules` symlink off by default  ·  Status: `done`
`PIPELINE_AGENT_NPM_MODE` default `ci` (was `auto`). `node_modules` (no slash) added to `_PIPELINE_EXCLUDE_PATHS` so the symlink is git-ignored. Re-enable `link` after: image rebuilt with the real exclude (`bc997cd`) + a local-prettier wrapper.

### 8.4 Classifier ignores negated mentions  ·  Status: `done`
`_mentions()` skips an apex/lwc/flow match preceded by no/not/without/never/avoid. Test extended.

### 8.5 `_clean` v2  ·  Status: `done`
Strips braille spinner frames `[⠀-⣿]`, status glyph lines `◼◯✔▸`, `Components: N/M`, `Status: In Progress`, `Preparing/Done … Nms`. Keeps the final `Status:` line and the component table.

### 8.6 `_fallback_report` cleaned  ·  Status: `done`
Short message with attempt/failure counts + a `_clean`ed 12-line gate tail, not the raw `last_validation` dump.

### 8.7 Token / cost tracking for the manager path  ·  Status: `todo`
`run_managed_pipeline` returns `UsageTotals()` (empty) and Codex worker tokens are never captured → `cost_logs` shows 0. Capture: Gemini manager usage from the LangGraph messages, and Codex `run_codex` result token counts (parse from `_last_engine_result` / stdout), sum into `create_cost_log`.

### 8.8 Prettier plugin resolution with the baked toolchain  ·  Status: `todo`
Global `prettier` can't load `prettier-plugin-apex` from a symlinked local `node_modules` (ESM dir-import error). Options: a `/usr/local/bin/prettier` wrapper that execs the local `.bin/prettier`; or `NODE_PATH` prepend; or keep `mode=ci`. Blocks re-enabling 8.3's `link` mode.

# Changelog

- **2026-09-02** — Plan created. WS-0 baseline captured from commit `d7d366a`.
- **2026-09-02** — **WS-1.3 done** (`f5f0c2a`): `ship` step + blocked/delivered guards + MAKE THE CALL committed.
- **2026-09-02** — **WS-5.4 + WS-6.1 (PR body) done** (`2827291`): `manager._clean()` strips ANSI + `sf` spinner spam; `manager._pr_body()` structured PR description.
- **2026-09-02** — **WS-4.3 done** (`b95f0c5`): `_opening()` front-loads ticket + plan + catalog + skills; `validate` REFUSED before first `delegate_worker`.
- **2026-09-02** — **WS-4.2 done** (`29ed352`): `org_catalog._slim()` + `_is_local_custom()` drop ~800 standard/managed metadata names. Needs per-project re-bootstrap.
- **2026-09-02** — **WS-4.1 done** (`d15124a`): SKILL.md bodies folded into `~/.codex/AGENTS.md` (codex only). `install_into_sandbox` returns `skill_md`.
- **2026-09-02** — **WS-2.1, 2.2, 3.1(partial), 3.2, 3.3 done** (`80e4057`): baked `/opt/sf-toolchain/node_modules` + `_npm_install_project` link mode; `sf` non-interactive `ENV`s; `configure_sandbox_git` one round-trip; stale-branch server-side glob; `github memory` default `off`.
- **2026-09-02** — **WS-5.1, 5.2, 5.3 done** (`3e3ab95`): worker default `gpt-5.6-terra`; single validation owner; `classify.py` ticket classifier → `session.max_turns` 8–24 + `light_review`. WS-4.4 deferred.
- **2026-09-02** — **shell-wrap fix** (`bc997cd`): `sandbox.execute()` shlex-splits (no shell) unless `timeout_seconds` is passed — so `&&` / `||` / `>>` silently didn't work. This had been breaking `.git/info/exclude` appends since `d7d366a` (why `.pipeline/`/TICKET.md leaked into diffs) and my WS-3.1 `configure_sandbox_git` `&&` collapse broke git config entirely. Fixes: `configure_sandbox_git` back to per-command loop; `_exclude_pipeline_files` uses `read_file`+`write_file`; `_npm_install_project` link/checkout cmds + `_checkout_ticket_branch` get `timeout_seconds`; `_do_ship` post-merge fetch+switch gets `timeout_seconds`. 93 tests green.
- **2026-09-02** — **WS-8 done** (`0f1707f`): first real-run (AG-214) fixes — validate-fail retry cap (3), worker "never delete force-app files", npm symlink OFF by default, classifier ignores negated mentions, `_clean` v2, `_fallback_report` cleaned. 95 tests green.
- **2026-09-03** — ✅ **VERIFIED — epic of 4 tickets, all merged + deployed to the org.** Project `171ccc17`, image rebuilt + re-bootstrapped by the user.

  | ticket | type | run | time | worker sessions | outcome | deploy id | PR |
  |---|---|---|---|---|---|---|---|
  | AG-212 | metadata-single | `2dd64833` | **3.8 min** | 1 | delivered (field pre-existed) | `0Affj00000PWHLtCAP` | #5 merged |
  | AG-213 | flow | `26c60f29` | 6.8 min | 2 | delivered, review **pass** | `0Affj00000PWJHFCA5` | #6 merged |
  | AG-214 | metadata | `a81bc049` | 6.7 min | **1** (was 24-turn blocked in `df1198c7`) | delivered, review pass | `0Affj00000PVb0FCAT` | #7 merged |
  | AG-216 | lwc | `6c7de6e1` | 14.6 min | 2 | delivered, review warn (shipped, not re-delegated) | `0Affj00000PWSvdCAH` | #8 merged |

  Full cycle confirmed: `delegate → validate → review → deliver → ship (real deploy) → merge → report`. No thrash, no false blocked, readable summaries with Assumptions sections.

  **Carry-forward polish (not blocking):**
  - Worker guesses a bad `sf code-analyzer` flag ("unsupported argument") — pin the exact command.
  - AG-216 worker touched `eslint.config.js` + `README.md` unrelated to the ticket (review flagged `warn`) — strengthen the "don't touch unrelated files" rule.
  - `Custom: Sales Profile` / Flow show as "Changed" in every deploy even on an empty diff — SF metadata-compare quirk, summaries handle it, but noisy.

**WS-0 → WS-8 done & verified.**

- **2026-09-03** — **WS-7 done (code)** (`3a8653d`, `dec6bf3`, `<pending>`): typed `open_questions` in the plan schema + "MAKE THE CALL" planning prompt; `ApprovalIn.action` (approve/revise/reject) + `question_responses`; `run_states.REPLANNING`; `runner.run_codex_replanning` (seed PLAN.json, EDIT it with the decisions); `pipeline_runner.start_replanning_phase` / `_run_replanning_phase`; `runs.approve_or_reject_run` routes `revise` → re-plan (cap 2 rounds) and folds per-question answers into `reviewer_answers` on approve; edited plans persisted as new plan-artifact versions; frontend — per-open_question cards (default/answer radio), "Approve with these answers" / "Revise plan" / "Reject" buttons, `replanning` status. `tsc -b` clean; 103 backend tests green. **Not yet run on a real ticket** (frontend `npm run build` blocked by a locked `dist/` in this env — user should build + smoke-test).

## Next waves

- **Verify WS-7** on a ticket that has a real ambiguity (e.g. AG-217): plan v1 shows an open_question → answer it → "Revise plan" → v2 has the decision baked into the steps and no question → Approve → execution.
- **Later:** WS-8.7 (token/cost tracking — the one real gap: `cost_logs` shows 0 for the manager path), WS-8.8 (prettier plugin → re-enable npm `link` mode), WS-4.4 (conditional skill install — `experience-lwc-generate` is 246 KB), WS-2.2 tail (`sf deploy --json`), WS-2.3 (Readme + image digest).
