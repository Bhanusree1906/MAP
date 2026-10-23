"""CRUD + publish/activate for skills and project memory.

Storage is injected (default: local disk - see storage.py) so S3 is a new
backend class, not a rewrite of this module.
"""

from __future__ import annotations

import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from database.models.project import Project
from database.models.project_memory import ProjectMemory
from database.models.project_skill import ProjectSkill
from database.models.skill import Skill
from database.models.skill_version import SkillVersion
from database.session import SessionLocal, init_db

from .packaging import (
    extract_package,
    extract_skill_md,
    extract_zip_context,
    extract_zip_skill,
    package_skill_dir,
    sha256_checksum,
    validate_slug,
    validate_version,
    verify_checksum,
)
from .storage import LocalBlobStorage, default_storage

_DEFAULT_SANDBOX_SKILLS_DIR = "/home/agentuser/.codex/skills"
# opencode has no built-in skills directory of its own - service.py callers
# collect each installed skill's SKILL.md path from here and merge it into
# opencode.json's `instructions` array instead (see
# agents/pipeline_agent/sandbox_runtime.py), since that's the one documented
# way to guarantee opencode actually reads the content.
_SANDBOX_SKILLS_DIR = {
    "codex": _DEFAULT_SANDBOX_SKILLS_DIR,
    "opencode": "/home/agentuser/.agents/skills",
}
_PROJECT_MEMORY_SLUG = "project-memory"
_NEXT_VERSION_RE = re.compile(r"^\d+$")

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def skill_storage_key(slug: str, version: str) -> str:
    return f"skills/{slug}/{version}/skill.tar.gz"


def memory_storage_key(project_id: str, version: str) -> str:
    return f"memories/{project_id}/{version}/MEMORY.md"


@dataclass(frozen=True)
class SkillInstall:
    """One package to extract into ~/.codex/skills/<slug>/ at sandbox boot."""

    slug: str
    version: str
    name: str
    phase: str = "both"


_ALLOWED_PHASES = frozenset({"planning", "implementation", "both"})


@dataclass(frozen=True)
class SkillAttachment:
    """One project<->skill link for set_project_skills - phase scopes which
    sandbox boot installs it (see list_project_installs's phase filter).
    """

    slug: str
    phase: str = "both"


def list_skills() -> list[Skill]:
    _ensure_schema()
    with SessionLocal() as db:
        skills = db.query(Skill).order_by(Skill.slug).all()
        for s in skills:
            db.expunge(s)
        return skills


def get_skill(slug: str) -> Skill | None:
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is not None:
            db.expunge(skill)
        return skill


def list_versions(slug: str) -> list[SkillVersion]:
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            return []
        versions = (
            db.query(SkillVersion)
            .filter(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.created_at.desc())
            .all()
        )
        for v in versions:
            db.expunge(v)
        return versions


def get_version(slug: str, version: str) -> SkillVersion | None:
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            return None
        skill_version = (
            db.query(SkillVersion)
            .filter(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
            .first()
        )
        if skill_version is not None:
            db.expunge(skill_version)
        return skill_version


def get_version_by_id(version_id: str) -> SkillVersion | None:
    _ensure_schema()
    with SessionLocal() as db:
        skill_version = db.query(SkillVersion).filter(SkillVersion.id == version_id).first()
        if skill_version is not None:
            db.expunge(skill_version)
        return skill_version


def publish_version(
    slug: str,
    name: str | None,
    version: str,
    skill_dir: Path,
    activate: bool = True,
    origin: str = "custom",
    origin_ref: str | None = None,
    storage: LocalBlobStorage | None = None,
    initial_status: str = "active",
    risk_notes: str | None = None,
) -> SkillVersion:
    """Packages skill_dir and publishes it as an immutable new version.

    initial_status only applies when this is a brand-new skill (first
    publish for this slug) and activate=False - e.g. agents/skills/
    importer.py passes "pending_review" for an import whose risk signals
    didn't read clean, so it's not attachable until approve_skill(). Existing
    skills keep whatever status they already have unless activate=True
    (which always forces "active", same as before this param existed).
    """
    slug = validate_slug(slug)
    version = validate_version(version)
    if origin not in {"custom", "skills_sh", "image"}:
        raise ValueError(f"Unknown skill origin {origin!r}")
    storage = storage or default_storage()
    _ensure_schema()

    package_bytes, frontmatter = package_skill_dir(Path(skill_dir))
    display_name = name or frontmatter.name or slug
    description = frontmatter.description
    checksum = sha256_checksum(package_bytes)
    storage_key = skill_storage_key(slug, version)

    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            skill = Skill(
                slug=slug,
                name=display_name,
                description=description,
                origin=origin,
                origin_ref=origin_ref,
                risk_notes=risk_notes,
                status=initial_status,
            )
            db.add(skill)
            db.flush()
        else:
            skill.name = display_name
            if description:
                skill.description = description
            if origin_ref:
                skill.origin_ref = origin_ref
            if risk_notes:
                skill.risk_notes = risk_notes
            skill.origin = origin

        existing = (
            db.query(SkillVersion)
            .filter(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
            .first()
        )
        if existing is not None:
            raise ValueError(
                f"{slug!r} version {version!r} is already published - versions are immutable, bump the version"
            )

        storage.put(storage_key, package_bytes)
        try:
            skill_version = SkillVersion(
                skill_id=skill.id,
                version=version,
                storage_backend=storage.backend,
                storage_key=storage_key,
                checksum=checksum,
                byte_size=len(package_bytes),
                skill_md_name=frontmatter.name,
                skill_md_description=frontmatter.description,
            )
            db.add(skill_version)
            db.flush()
            if activate:
                skill.active_version_id = skill_version.id
                skill.status = "active"
            db.commit()
            db.refresh(skill_version)
            db.expunge(skill_version)
            return skill_version
        except Exception:
            storage.delete(storage_key)
            raise


def set_active_version(slug: str, version: str) -> Skill:
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            raise ValueError(f"No skill named {slug!r}")
        skill_version = (
            db.query(SkillVersion)
            .filter(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
            .first()
        )
        if skill_version is None:
            raise ValueError(f"{slug!r} has no published version {version!r}")
        skill.active_version_id = skill_version.id
        skill.status = "active"
        db.commit()
        db.refresh(skill)
        db.expunge(skill)
        return skill


def approve_skill(slug: str, version: str) -> Skill:
    """Move a pending_review import to active at the given version - the
    only way it becomes attachable to a project (list_project_installs /
    set_project_skills both require status == "active"). A thin alias for
    set_active_version, kept separate so call sites read as an explicit
    human review-and-approve action, not a routine version bump.
    """
    return set_active_version(slug, version)


def archive_skill(slug: str) -> Skill:
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            raise ValueError(f"No skill named {slug!r}")
        skill.status = "archived"
        db.commit()
        db.refresh(skill)
        db.expunge(skill)
        return skill


def get_skill_content(slug: str, version: str | None = None, storage: LocalBlobStorage | None = None) -> str:
    """SKILL.md text for the editor. Defaults to the active version."""
    return extract_skill_md(fetch_package(slug, version=version, storage=storage))


def _next_skill_version(db, skill_id: str) -> str:
    """Numeric auto-increment, same pattern as _next_memory_version -
    save_skill_content callers never think about version numbers (the UI
    edits in place; see this module's docstring on that design choice),
    but versions stay immutable underneath so pinned_version_id on a
    ProjectSkill still means something.
    """
    versions = [row.version for row in db.query(SkillVersion).filter(SkillVersion.skill_id == skill_id).all()]
    numbers = [int(v) for v in versions if _NEXT_VERSION_RE.fullmatch(v)]
    return str(max(numbers, default=0) + 1)


def save_skill_content(
    slug: str, content: str, name: str | None = None, storage: LocalBlobStorage | None = None
) -> SkillVersion:
    """Create-or-update a skill from raw SKILL.md text - the editor's
    "save" action. Auto-picks the next version and activates it; no version
    picker is exposed anywhere above this, by design (see this module's
    docstring), even though the storage layer underneath is still the
    normal immutable SkillVersion chain.

    If the skill already exists, every other file its current version has
    (references/, scripts/, whatever a zip upload brought in - see
    save_skill_from_zip) is carried forward unchanged; only SKILL.md is
    replaced. Otherwise a plain-text edit of SKILL.md in this single-file
    editor would silently delete bundled files on every save.
    """
    slug = validate_slug(slug)
    if not content.strip():
        raise ValueError("Skill content cannot be empty")
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        version = _next_skill_version(db, skill.id) if skill is not None else "1"
        has_existing_version = skill is not None and skill.active_version_id is not None

    with tempfile.TemporaryDirectory(prefix="skill-edit-") as tmp:
        skill_dir = Path(tmp)
        if has_existing_version:
            extract_package(fetch_package(slug, storage=storage), skill_dir)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return publish_version(
            slug,
            name=name,
            version=version,
            skill_dir=skill_dir,
            activate=True,
            origin="custom",
            storage=storage,
        )


def save_skill_from_zip(
    slug: str,
    zip_bytes: bytes,
    content: str,
    name: str | None = None,
    storage: LocalBlobStorage | None = None,
) -> SkillVersion:
    """Create-or-update a skill from an uploaded zip (SKILL.md plus any
    bundled files, e.g. references/*.md or scripts/) - unlike
    save_skill_content, the zip's own files fully REPLACE whatever the
    skill previously had, since this is an explicit re-upload rather than a
    plain text edit. content overrides the zip's own SKILL.md text (the
    user may have tweaked it in the editor before saving).
    """
    slug = validate_slug(slug)
    if not content.strip():
        raise ValueError("Skill content cannot be empty")
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        version = _next_skill_version(db, skill.id) if skill is not None else "1"

    with tempfile.TemporaryDirectory(prefix="skill-zip-") as tmp:
        skill_dir = Path(tmp)
        extract_zip_skill(zip_bytes, skill_dir)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return publish_version(
            slug,
            name=name,
            version=version,
            skill_dir=skill_dir,
            activate=True,
            origin="custom",
            storage=storage,
        )


def save_project_context_from_zip(
    slug: str,
    zip_bytes: bytes,
    storage: LocalBlobStorage | None = None,
) -> SkillVersion:
    """Publish an Agents.MD project-context archive as a Codex skill."""
    slug = validate_slug(slug)
    _ensure_schema()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        version = _next_skill_version(db, skill.id) if skill is not None else "1"

    with tempfile.TemporaryDirectory(prefix="project-context-zip-") as tmp:
        skill_dir = Path(tmp)
        extract_zip_context(zip_bytes, skill_dir)
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        if not content.lstrip().startswith("---"):
            content = (
                "---\n"
                "name: project-context\n"
                "description: Project-specific linked context for Codex.\n"
                "---\n\n"
                + content
            )
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return publish_version(
            slug,
            name="Project context",
            version=version,
            skill_dir=skill_dir,
            activate=True,
            origin="custom",
            storage=storage,
        )


def delete_skill(slug: str, storage: LocalBlobStorage | None = None) -> None:
    """Hard delete - removes the skill, every published version, and their
    blobs. Refuses if any project still has it attached (ProjectSkill's FK
    is ondelete=RESTRICT for exactly this reason) so deleting a skill can't
    silently break a project that depends on it; detach first.
    """
    _ensure_schema()
    storage = storage or default_storage()
    with SessionLocal() as db:
        skill = db.query(Skill).filter(Skill.slug == slug).first()
        if skill is None:
            raise ValueError(f"No skill named {slug!r}")
        attached = db.query(ProjectSkill).filter(ProjectSkill.skill_id == skill.id).count()
        if attached:
            raise ValueError(
                f"Skill {slug!r} is attached to {attached} project(s) - detach it before deleting"
            )
        versions = db.query(SkillVersion).filter(SkillVersion.skill_id == skill.id).all()
        storage_keys = [v.storage_key for v in versions]
        skill.active_version_id = None
        db.flush()
        for v in versions:
            db.delete(v)
        db.delete(skill)
        db.commit()
    for key in storage_keys:
        storage.delete(key)


def fetch_package(slug: str, version: str | None = None, storage: LocalBlobStorage | None = None) -> bytes:
    storage = storage or default_storage()
    skill = get_skill(slug)
    if skill is None:
        raise ValueError(f"No skill named {slug!r}")
    if version:
        skill_version = get_version(slug, version)
    elif skill.active_version_id:
        skill_version = get_version_by_id(skill.active_version_id)
    else:
        raise ValueError(f"Skill {slug!r} has no active version and none was specified")
    if skill_version is None:
        raise ValueError(f"Skill {slug!r} has no published version {version or skill.active_version_id!r}")
    data = storage.get(skill_version.storage_key)
    verify_checksum(data, skill_version.checksum)
    return data


def resolved_version_label(skill: Skill) -> str | None:
    if not skill.active_version_id:
        return None
    row = get_version_by_id(skill.active_version_id)
    return row.version if row else None


def list_project_installs(
    project_id: str, *, strict: bool = True, phase: str | None = None
) -> list[SkillInstall]:
    """Resolve each attached skill to a concrete version for sandbox install.

    phase, if given ("planning" or "implementation"), skips links scoped to
    the other phase - links with phase="both" (the default) always install.
    """
    _ensure_schema()
    installs: list[SkillInstall] = []
    with SessionLocal() as db:
        links = (
            db.query(ProjectSkill)
            .filter(ProjectSkill.project_id == project_id)
            .order_by(ProjectSkill.sort_order, ProjectSkill.created_at)
            .all()
        )
        for link in links:
            if phase is not None and link.phase != "both" and link.phase != phase:
                continue
            skill = db.query(Skill).filter(Skill.id == link.skill_id).first()
            if skill is None or skill.status != "active":
                if strict:
                    raise ValueError(
                        f"Project {project_id} is attached to a missing or archived skill {link.skill_id}"
                    )
                continue
            version_id = link.pinned_version_id or skill.active_version_id
            if not version_id:
                if strict:
                    raise ValueError(f"Skill {skill.slug!r} has no active version to install")
                continue
            skill_version = db.query(SkillVersion).filter(SkillVersion.id == version_id).first()
            if skill_version is None:
                if strict:
                    raise ValueError(f"Skill {skill.slug!r} points at a missing version")
                continue
            installs.append(
                SkillInstall(slug=skill.slug, version=skill_version.version, name=skill.name, phase=link.phase)
            )
    return installs


def list_projects_using_skill(slug: str) -> list[Project]:
    skill = get_skill(slug)
    if skill is None:
        return []
    _ensure_schema()
    with SessionLocal() as db:
        rows = (
            db.query(Project)
            .join(ProjectSkill, ProjectSkill.project_id == Project.id)
            .filter(ProjectSkill.skill_id == skill.id)
            .order_by(Project.name)
            .all()
        )
        for project in rows:
            db.expunge(project)
        return rows


def list_project_skills(project_id: str) -> list[ProjectSkill]:
    _ensure_schema()
    with SessionLocal() as db:
        links = (
            db.query(ProjectSkill)
            .filter(ProjectSkill.project_id == project_id)
            .order_by(ProjectSkill.sort_order, ProjectSkill.created_at)
            .all()
        )
        for link in links:
            db.expunge(link)
        return links


def set_project_skills(project_id: str, attachments: list[SkillAttachment]) -> list[ProjectSkill]:
    """Replace a project's attached skills (order and phase included).
    Follows each skill's active version (no pin) so catalog roll-forward
    applies on the next sandbox. Pass an empty list to detach all.
    """
    _ensure_schema()
    seen: set[str] = set()
    normalized: list[SkillAttachment] = []
    for raw in attachments:
        slug = validate_slug(raw.slug)
        if slug in seen:
            continue
        phase = raw.phase if raw.phase in _ALLOWED_PHASES else "both"
        seen.add(slug)
        normalized.append(SkillAttachment(slug=slug, phase=phase))

    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            raise ValueError(f"No project {project_id}")
        db.query(ProjectSkill).filter(ProjectSkill.project_id == project_id).delete()
        links: list[ProjectSkill] = []
        for order, attachment in enumerate(normalized):
            skill = db.query(Skill).filter(Skill.slug == attachment.slug).first()
            if skill is None:
                raise ValueError(f"No skill named {attachment.slug!r}")
            if skill.status != "active":
                raise ValueError(
                    f"Skill {attachment.slug!r} is {skill.status!r}, not active, and cannot be attached"
                )
            if not skill.active_version_id:
                raise ValueError(f"Skill {attachment.slug!r} has no active version")
            link = ProjectSkill(
                project_id=project_id, skill_id=skill.id, sort_order=order, phase=attachment.phase
            )
            db.add(link)
            links.append(link)
        db.commit()
        for link in links:
            db.refresh(link)
            db.expunge(link)
        return links


def install_into_sandbox(
    sandbox: Any,
    slug: str,
    version: str | None = None,
    skills_dir: str | None = None,
    storage: LocalBlobStorage | None = None,
    engine: str = "codex",
) -> dict[str, Any]:
    """Extract a skill package into <skills_dir>/<slug>/ - the target agent's
    user-level skills directory (see _SANDBOX_SKILLS_DIR). Repo-local
    .agents/skills is a different location; we use the user-level one so
    skills are available before the project repo is cloned.

    Pass skills_dir explicitly to override; otherwise it's resolved from
    engine. Returns "skill_md_path" on success - opencode has no directory
    of its own it reads automatically, so callers (sandbox_runtime.py) fold
    that path into opencode.json's `instructions` array instead.
    """
    slug = validate_slug(slug)
    skills_dir = skills_dir or _SANDBOX_SKILLS_DIR.get(engine, _DEFAULT_SANDBOX_SKILLS_DIR)
    package_bytes = fetch_package(slug, version, storage=storage)
    tmp_archive = f"/tmp/skill-{slug}.tar.gz"

    write_result = sandbox.write_bytes(tmp_archive, package_bytes)
    if not write_result["success"]:
        return write_result

    target_dir = f"{skills_dir.rstrip('/')}/{slug}"
    mkdir_result = sandbox.execute(f"mkdir -p {shlex.quote(target_dir)}")
    if not mkdir_result["success"]:
        return {"success": False, "error": mkdir_result["stderr"] or mkdir_result["stdout"]}

    extract_result = sandbox.execute(f"tar -xzf {shlex.quote(tmp_archive)} -C {shlex.quote(target_dir)}")
    sandbox.execute(f"rm -f {shlex.quote(tmp_archive)}")
    if not extract_result["success"]:
        return {"success": False, "error": extract_result["stderr"] or extract_result["stdout"]}

    skill_md_path = f"{target_dir}/SKILL.md"
    check = sandbox.read_file(skill_md_path)
    if not check["success"]:
        return {"success": False, "error": f"Extracted {slug!r} but SKILL.md is missing at {target_dir}"}

    return {
        "success": True,
        "message": f"Installed skill {slug!r} into {target_dir}",
        "skill_md_path": skill_md_path,
        "skill_md": check.get("content") or "",
    }


def _next_memory_version(db, project_id: str) -> str:
    versions = [
        row.version
        for row in db.query(ProjectMemory).filter(ProjectMemory.project_id == project_id).all()
    ]
    numbers = [int(v) for v in versions if _NEXT_VERSION_RE.fullmatch(v)]
    return str(max(numbers, default=0) + 1)


def get_active_memory(project_id: str) -> ProjectMemory | None:
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None or not project.active_memory_id:
            return None
        memory = db.query(ProjectMemory).filter(ProjectMemory.id == project.active_memory_id).first()
        if memory is not None:
            db.expunge(memory)
        return memory


def deactivate_memory(project_id: str) -> bool:
    """Stop using project memory without deleting stored versions."""
    _ensure_schema()
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            return False
        project.active_memory_id = None
        db.commit()
        return True


def fetch_memory_markdown(project_id: str, storage: LocalBlobStorage | None = None) -> str | None:
    memory = get_active_memory(project_id)
    if memory is None:
        return None
    storage = storage or default_storage()
    data = storage.get(memory.storage_key)
    verify_checksum(data, memory.checksum)
    return data.decode("utf-8")


def publish_memory(
    project_id: str,
    markdown: str,
    version: str | None = None,
    commit_sha: str | None = None,
    activate: bool = True,
    storage: LocalBlobStorage | None = None,
) -> ProjectMemory:
    if not markdown.strip():
        raise ValueError("Project memory cannot be empty")
    storage = storage or default_storage()
    _ensure_schema()
    data = markdown.encode("utf-8")
    checksum = sha256_checksum(data)

    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project is None:
            raise ValueError(f"No project {project_id}")
        resolved_version = validate_version(version) if version else _next_memory_version(db, project_id)
        existing = (
            db.query(ProjectMemory)
            .filter(ProjectMemory.project_id == project_id, ProjectMemory.version == resolved_version)
            .first()
        )
        if existing is not None:
            raise ValueError(f"Project already has memory version {resolved_version!r}")
        storage_key = memory_storage_key(project_id, resolved_version)
        storage.put(storage_key, data)
        try:
            memory = ProjectMemory(
                project_id=project_id,
                version=resolved_version,
                storage_backend=storage.backend,
                storage_key=storage_key,
                checksum=checksum,
                byte_size=len(data),
                commit_sha=commit_sha,
            )
            db.add(memory)
            db.flush()
            if activate:
                project.active_memory_id = memory.id
            db.commit()
            db.refresh(memory)
            db.expunge(memory)
            return memory
        except Exception:
            storage.delete(storage_key)
            raise


def install_memory_into_sandbox(
    sandbox: Any,
    project_id: str,
    skills_dir: str | None = None,
    storage: LocalBlobStorage | None = None,
    engine: str = "codex",
) -> dict[str, Any] | None:
    """Write active project memory as <skills_dir>/project-memory/SKILL.md.

    Returns None when the project has no memory (not an error). Returns
    "skill_md_path" on success, same reason as install_into_sandbox above.
    """
    markdown = fetch_memory_markdown(project_id, storage=storage)
    if markdown is None:
        return None
    skills_dir = skills_dir or _SANDBOX_SKILLS_DIR.get(engine, _DEFAULT_SANDBOX_SKILLS_DIR)
    target_dir = f"{skills_dir.rstrip('/')}/{_PROJECT_MEMORY_SLUG}"
    mkdir_result = sandbox.execute(f"mkdir -p {shlex.quote(target_dir)}")
    if not mkdir_result["success"]:
        return {"success": False, "error": mkdir_result["stderr"] or mkdir_result["stdout"]}
    skill_md = (
        "---\n"
        f"name: {_PROJECT_MEMORY_SLUG}\n"
        "description: Project-specific codebase memory — layout, conventions, "
        "how to run tests, and domain notes. Use when planning or implementing "
        "tickets against this repository.\n"
        "---\n\n"
        f"{markdown.rstrip()}\n"
    )
    skill_md_path = f"{target_dir}/SKILL.md"
    written = sandbox.write_file(skill_md_path, skill_md)
    if not written["success"]:
        return written
    return {
        "success": True,
        "message": f"Installed project memory into {target_dir}",
        "skill_md_path": skill_md_path,
        "skill_md": skill_md,
    }
