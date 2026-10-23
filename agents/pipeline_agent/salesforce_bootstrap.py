"""One-shot Salesforce project bootstrap: org catalog + official DX generate.

For an empty GitHub repo, `sf project generate` (Salesforce's own scaffold)
is committed to the default branch. Project memory is the org catalog plus
links to the DX docs — not a homemade style guide.
"""

from __future__ import annotations

import os
import shlex
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from agents.skills import service as skill_service
from agents.skills.service import SkillAttachment
from sandbox.manager import authenticated_clone_url

from ._timing import timed
from .credentials import get_effective_git_token, get_project_by_id
from .org_catalog import collect_catalog, render_catalog_markdown
from .sandbox_kinds import resolve_sandbox_image
from .sandbox_runtime import open_sf_cli_sandbox

_DEFAULT_SF_SKILLS = ("salesforce-developer", "salesforce-apex-quality")


def _cli_settings(project) -> SimpleNamespace:
    git_token = get_effective_git_token(project.id)
    return SimpleNamespace(
        salesforce_connection_name=project.salesforce_connection_name,
        sandbox_image=resolve_sandbox_image(project.sandbox_kind),
        sandbox_workdir=os.getenv("PIPELINE_AGENT_SANDBOX_WORKDIR", "/workspace"),
        git_token=git_token,
        repository_url=project.repository_url,
        default_branch=project.default_branch,
        bot_git_name=os.getenv("PIPELINE_AGENT_BOT_NAME", "pipeline-agent"),
        bot_git_email=os.getenv(
            "PIPELINE_AGENT_BOT_EMAIL", "pipeline-agent@users.noreply.github.com"
        ),
    )


def compose_project_memory(settings, catalog_markdown: str) -> str:
    """Org catalog plus pointers to Salesforce's own DX docs. Do not invent a
    second style guide — the agent should follow the official project layout."""
    branch = settings.default_branch or "main"
    return "\n".join(
        [
            "# Project memory (Bootstrap Salesforce — org catalog)",
            "",
            f"Default git branch: `{branch}`.",
            "",
            "Follow Salesforce DX, not a custom layout:",
            "- Create a project: https://developer.salesforce.com/docs/atlas.en-us.sfdx_dev.meta/sfdx_dev/sfdx_dev_ws_create_new.htm",
            "- Develop against any org (retrieve the metadata you change, not the whole org): "
            "https://developer.salesforce.com/docs/atlas.en-us.sfdx_dev.meta/sfdx_dev/sfdx_dev_develop_any_org.htm",
            "- CLI: `sf project generate`, `sf project retrieve start`, `sf project deploy start`",
            "",
            "Open `salesforce-org-harness` in this sandbox — the org is already logged in.",
            "",
            "## Org catalog",
            "",
            catalog_markdown.strip() or "_Catalog dump was empty._",
            "",
        ]
    )


def _ensure_salesforce_skills(project_id: str) -> list[str]:
    """Attach the Salesforce skills if they exist in the catalog and are not
    already on the project. Does not wipe operator-attached skills."""
    installs = skill_service.list_project_installs(project_id, strict=False)
    attachments = [SkillAttachment(slug=i.slug, phase=i.phase) for i in installs]
    have = {i.slug for i in installs}
    added: list[str] = []
    for slug in _DEFAULT_SF_SKILLS:
        if slug in have:
            continue
        if skill_service.get_skill(slug) is None:
            continue
        attachments.append(SkillAttachment(slug=slug, phase="both"))
        added.append(slug)
    if added:
        skill_service.set_project_skills(project_id, attachments)
    return added


def bootstrap_salesforce_project(project_id: str, on_event=None) -> dict[str, Any]:
    """Org catalog → project memory; if the repo has no sfdx-project.json,
    run `sf project generate` and push to the default branch."""
    def emit(message: str) -> None:
        if on_event:
            on_event("step", message)

    project = get_project_by_id(project_id)
    if project is None:
        raise RuntimeError("Project not found")
    if project.sandbox_kind != "salesforce":
        raise RuntimeError("Org catalog is only for Salesforce projects")

    settings = _cli_settings(project)
    emit("Attaching default Salesforce skills")
    skills_added = _ensure_salesforce_skills(project_id)
    if skills_added:
        emit("Attached skills: " + ", ".join(skills_added))
    out: dict[str, Any] = {
        "catalog_objects": 0,
        "api_version": "",
        "scaffolded": False,
        "scaffold_skipped": False,
        "message": "",
    }

    with open_sf_cli_sandbox(settings, on_event=on_event) as sandbox:
        catalog_md = ""
        try:
            emit("Collecting org catalog")
            with timed(on_event, "org catalog"):
                catalog = collect_catalog(sandbox, on_event=on_event)
            catalog_md = render_catalog_markdown(catalog)
            out["catalog_objects"] = int(catalog.get("custom_object_count") or 0)
            out["api_version"] = str((catalog.get("org") or {}).get("api_version") or "")
            emit(
                f"Catalog saved: {out['catalog_objects']} custom objects"
                + (f", API {out['api_version']}" if out["api_version"] else "")
            )
        except Exception as exc:  # noqa: BLE001 - still publish conventions
            catalog_md = f"_Org catalog dump failed: {exc}_\n"
            emit(f"Org catalog dump failed: {exc}")

        emit("Saving project memory")
        skill_service.publish_memory(project_id, compose_project_memory(settings, catalog_md))

        if not settings.git_token or not settings.repository_url:
            out["scaffold_skipped"] = True
            out["message"] = _bootstrap_message(out, settings, skills_added, "No git token/repo — skipped DX scaffold.")
            emit(out["message"])
            return out

        emit("Opening GitHub repository")
        repo = _ensure_git_repo(sandbox, settings)
        if not repo["ok"]:
            out["scaffold_skipped"] = True
            out["message"] = _bootstrap_message(
                out, settings, skills_added, f"Could not open the GitHub repo: {repo.get('error')}"
            )
            emit(out["message"])
            return out
        if repo.get("initialized"):
            emit("GitHub repo was empty — initialized a new git tree")

        api_version = out["api_version"] or "62.0"
        wrote_dx = False
        if not sandbox.read_file("sfdx-project.json").get("success"):
            emit(f"Generating DX scaffold (API {api_version})")
            with timed(on_event, "sf project generate"):
                _write_dx_scaffold(sandbox, api_version)
            wrote_dx = True
        else:
            emit("Repo already has sfdx-project.json — scaffold skipped")

        emit("Committing and pushing to the default branch")
        with timed(on_event, "git push"):
            push = _commit_and_push_scaffold(sandbox, settings)
        if not push["success"]:
            raise RuntimeError(f"DX scaffold commit/push failed: {push.get('error')}")
        if push.get("skipped") and not wrote_dx:
            out["scaffold_skipped"] = True
            out["message"] = _bootstrap_message(
                out, settings, skills_added,
                "Repo already has sfdx-project.json — scaffold skipped.",
            )
        else:
            out["scaffolded"] = True
            out["message"] = _bootstrap_message(
                out, settings, skills_added,
                f"`sf project generate` output pushed to `{settings.default_branch}` (API {api_version}).",
            )
        emit(out["message"])
    return out


def _bootstrap_message(out: dict, settings, skills_added: list[str], rest: str) -> str:
    bits = [
        f"Org catalog saved ({out['catalog_objects']} custom objects).",
        rest,
    ]
    if skills_added:
        bits.append("Attached skills: " + ", ".join(skills_added) + ".")
    return " ".join(bits)


def _ensure_git_repo(sandbox, settings) -> dict[str, Any]:
    """Clone, or git init + origin when the GitHub repo has no commits yet."""
    clone = sandbox.clone_repository(settings.repository_url, token=settings.git_token)
    if clone.get("success"):
        return {"ok": True}
    url = authenticated_clone_url(settings.repository_url, settings.git_token)
    branch = settings.default_branch
    for command in (
        "git init",
        f"git checkout -B {shlex.quote(branch)}",
    ):
        result = sandbox.execute(command, timeout_seconds=30)
        if not result.get("success"):
            return {
                "ok": False,
                "error": (clone.get("error") or "") + " | " + _exec_err(result, command),
            }
    sandbox.execute("git remote remove origin", timeout_seconds=15)
    add = sandbox.execute(f"git remote add origin {shlex.quote(url)}", timeout_seconds=15)
    if not add.get("success"):
        return {"ok": False, "error": _exec_err(add, "git remote add")}
    return {"ok": True, "initialized": True, "clone_error": clone.get("error")}


def _write_dx_scaffold(sandbox, api_version: str) -> None:
    gen = sandbox.execute(
        "rm -rf /tmp/dx-gen && mkdir -p /tmp/dx-gen && "
        "sf project generate --name app --output-dir /tmp/dx-gen --template standard",
        timeout_seconds=120,
    )
    if not gen.get("success"):
        raise RuntimeError(
            f"sf project generate failed: {gen.get('stderr') or gen.get('stdout')}"
        )
    copy = sandbox.execute("cp -an /tmp/dx-gen/app/. /workspace/")
    if not copy.get("success"):
        raise RuntimeError(f"Failed to copy DX scaffold: {copy.get('stderr')}")
    patch = sandbox.execute(
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        "p = Path('sfdx-project.json')\n"
        "data = json.loads(p.read_text())\n"
        f"data['sourceApiVersion'] = {api_version!r}\n"
        "p.write_text(json.dumps(data, indent=2) + '\\n')\n"
        "PY"
    )
    if not patch.get("success"):
        raise RuntimeError(f"Failed to set sourceApiVersion: {patch.get('stderr')}")


def _commit_and_push_scaffold(sandbox, settings) -> dict[str, Any]:
    """git add / commit / push as separate commands (no shell script)."""
    name = settings.bot_git_name
    email = settings.bot_git_email
    branch = settings.default_branch
    steps = [
        f"git config user.name {shlex.quote(name)}",
        f"git config user.email {shlex.quote(email)}",
        "git add -A",
    ]
    for command in steps:
        result = sandbox.execute(command, timeout_seconds=60)
        if not result.get("success"):
            return {"success": False, "error": _exec_err(result, command)}

    staged = sandbox.execute("git diff --cached --quiet", timeout_seconds=30)
    if staged.get("success"):
        return {"success": True, "skipped": True}

    commit = sandbox.execute(
        "git commit -m 'chore: Salesforce DX project scaffold'",
        timeout_seconds=60,
    )
    if not commit.get("success"):
        return {"success": False, "error": _exec_err(commit, "git commit")}
    renamed = sandbox.execute(f"git branch -M {shlex.quote(branch)}", timeout_seconds=30)
    if not renamed.get("success"):
        return {"success": False, "error": _exec_err(renamed, "git branch -M")}
    push = sandbox.execute("git push -u origin HEAD", timeout_seconds=120)
    if not push.get("success"):
        return {"success": False, "error": _exec_err(push, "git push")}
    return {"success": True, "skipped": False}


def _exec_err(result: dict, command: str) -> str:
    blob = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
    return f"{command}: {blob[:800]}"


# ---------------------------------------------------------------------------
# Background job so the UI can poll a live log the same way planning does.
# In-memory is enough: bootstrap is a one-minute Docker job, not a pipeline run.
# ---------------------------------------------------------------------------


@dataclass
class BootstrapEvent:
    seq: int
    kind: str
    content: str
    created_at: datetime


@dataclass
class BootstrapJob:
    id: str
    project_id: str
    status: str
    events: list[BootstrapEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


_jobs: dict[str, BootstrapJob] = {}
_lock = threading.Lock()


def _append_event(job: BootstrapJob, kind: str, content: str) -> None:
    with _lock:
        job.events.append(
            BootstrapEvent(
                seq=len(job.events) + 1,
                kind=kind,
                content=(content or "")[:4000],
                created_at=datetime.now(timezone.utc),
            )
        )


def start_bootstrap_job(project_id: str) -> BootstrapJob:
    with _lock:
        for existing in _jobs.values():
            if existing.project_id == project_id and existing.status == "running":
                return existing
        job = BootstrapJob(id=uuid.uuid4().hex, project_id=project_id, status="running")
        _jobs[job.id] = job

    def run() -> None:
        def on_event(kind: str, content: str) -> None:
            _append_event(job, kind, content)

        try:
            on_event("step", "Starting Salesforce bootstrap")
            result = bootstrap_salesforce_project(project_id, on_event=on_event)
            with _lock:
                job.result = result
                job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001 - surfaced on the job for the UI
            with _lock:
                job.error = str(exc)
                job.status = "failed"
            on_event("step", f"Bootstrap failed: {exc}")

    threading.Thread(target=run, daemon=True, name=f"sf-bootstrap-{job.id[:8]}").start()
    return job


def get_bootstrap_job(project_id: str, job_id: str) -> BootstrapJob | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.project_id != project_id:
            return None
        return job


def list_bootstrap_events(project_id: str, job_id: str, after_seq: int = 0) -> list[BootstrapEvent]:
    job = get_bootstrap_job(project_id, job_id)
    if job is None:
        return []
    with _lock:
        return [event for event in job.events if event.seq > after_seq]


def reset_bootstrap_jobs_for_tests() -> None:
    with _lock:
        _jobs.clear()
