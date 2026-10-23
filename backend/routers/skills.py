"""Catalog of Codex/opencode skills that projects can attach and inject into
a sandbox, plus importing new skills from the wider Agent Skills ecosystem
(see agents/skills/importer.py) behind a risk-tiered review gate.
"""

from __future__ import annotations

import re
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from agents.skills import service
from agents.skills.importer import import_skill, list_importable
from agents.skills.packaging import parse_skill_frontmatter, read_zip_skill
from backend.schemas import (
    SkillApproveIn,
    SkillContentIn,
    SkillContentOut,
    SkillCreateIn,
    SkillImportCandidateOut,
    SkillImportIn,
    SkillImportOut,
    SkillOut,
    SkillProjectOut,
    SkillUploadOut,
    SkillVersionOut,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _skill_out(skill) -> SkillOut:
    versions = service.list_versions(skill.slug)
    return SkillOut(
        slug=skill.slug,
        name=skill.name,
        description=skill.description,
        origin=skill.origin,
        origin_ref=skill.origin_ref,
        risk_notes=skill.risk_notes,
        status=skill.status,
        active_version=service.resolved_version_label(skill),
        latest_version=versions[0].version if versions else None,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.get("", response_model=list[SkillOut])
def list_skills() -> list[SkillOut]:
    return [_skill_out(s) for s in service.list_skills()]


@router.get("/{slug}", response_model=SkillOut)
def get_skill(slug: str) -> SkillOut:
    skill = service.get_skill(slug)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_out(skill)


@router.post("", response_model=SkillOut)
def create_skill_endpoint(payload: SkillCreateIn) -> SkillOut:
    if service.get_skill(payload.slug) is not None:
        raise HTTPException(status_code=400, detail=f"Skill {payload.slug!r} already exists")
    try:
        service.save_skill_content(payload.slug, payload.content, name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(service.get_skill(payload.slug))


@router.get("/{slug}/content", response_model=SkillContentOut)
def get_skill_content_endpoint(slug: str, version: str | None = Query(default=None)) -> SkillContentOut:
    if service.get_skill(slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    try:
        content = service.get_skill_content(slug, version=version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return SkillContentOut(slug=slug, content=content, version=version)


@router.get("/{slug}/projects", response_model=list[SkillProjectOut])
def list_skill_projects(slug: str) -> list[SkillProjectOut]:
    if service.get_skill(slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return [SkillProjectOut(id=p.id, name=p.name) for p in service.list_projects_using_skill(slug)]


@router.put("/{slug}/content", response_model=SkillOut)
def update_skill_content_endpoint(slug: str, payload: SkillContentIn) -> SkillOut:
    """Editor's "save" action for an existing skill - always edits in place:
    auto-bumps to the next version and activates it, no version picker
    exposed (see agents/skills/service.py's save_skill_content docstring).
    """
    if service.get_skill(slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    try:
        service.save_skill_content(slug, payload.content, name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(service.get_skill(slug))


@router.delete("/{slug}")
def delete_skill_endpoint(slug: str) -> dict:
    try:
        service.delete_skill(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"status": "ok"}


@router.post("/upload", response_model=SkillUploadOut)
async def upload_skill_file(file: UploadFile = File(...)) -> SkillUploadOut:
    """Extracts SKILL.md text from an uploaded .md/.txt/.zip file for the
    editor to preview - does NOT save anything. For a zip, also lists the
    other files alongside SKILL.md (references/, scripts/, etc.) so the
    user can see what will be preserved; saving one still requires
    re-sending the zip to /from-zip (nothing is staged server-side between
    preview and save).
    """
    raw = await file.read()
    filename = (file.filename or "").lower()
    bundled_files: list[str] = []

    if filename.endswith(".zip"):
        try:
            content, bundled_files = read_zip_skill(raw)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Not a valid zip file") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
    else:
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="File is not valid UTF-8 text") from None

    frontmatter = parse_skill_frontmatter(content)
    suggested_slug = None
    if frontmatter.name:
        suggested_slug = re.sub(r"[^a-z0-9]+", "-", frontmatter.name.lower()).strip("-")[:64] or None

    return SkillUploadOut(suggested_slug=suggested_slug, content=content, bundled_files=bundled_files)


@router.post("/from-zip", response_model=SkillOut)
async def create_skill_from_zip(
    file: UploadFile = File(...), slug: str = Form(...), content: str = Form(...), name: str | None = Form(None)
) -> SkillOut:
    """Create a skill from an uploaded zip - unlike POST /skills (plain
    text), this preserves every file alongside SKILL.md (references/,
    scripts/, etc.), not just SKILL.md itself. content overrides the zip's
    own SKILL.md text with whatever the user edited in the preview step.
    """
    if service.get_skill(slug) is not None:
        raise HTTPException(status_code=400, detail=f"Skill {slug!r} already exists")
    raw = await file.read()
    try:
        service.save_skill_from_zip(slug, raw, content, name=name)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(service.get_skill(slug))


@router.put("/{slug}/from-zip", response_model=SkillOut)
async def update_skill_from_zip(
    slug: str, file: UploadFile = File(...), content: str = Form(...)
) -> SkillOut:
    """Update an existing skill from a re-uploaded zip - its files fully
    REPLACE what the skill previously had (unlike PUT /{slug}/content's
    plain-text edit, which carries forward the existing bundled files
    unchanged). Use this when the zip itself changed, not just SKILL.md's
    wording.
    """
    if service.get_skill(slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    raw = await file.read()
    try:
        service.save_skill_from_zip(slug, raw, content)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(service.get_skill(slug))


@router.get("/import/search", response_model=list[SkillImportCandidateOut])
def search_importable_skills(source: str, query: str | None = None) -> list[SkillImportCandidateOut]:
    """source: a GitHub repo URL (or owner/repo) to enumerate skills from -
    see agents/skills/importer.py. Nothing is fetched into the catalog yet;
    this is read-only discovery.
    """
    try:
        candidates = list_importable(source, query=query)
    except Exception as exc:  # noqa: BLE001 - external CLI boundary, surfaced to the caller
        raise HTTPException(status_code=502, detail=f"Could not search {source!r}: {exc}") from None
    return [SkillImportCandidateOut(name=c.name, description=c.description) for c in candidates]


@router.post("/import", response_model=SkillImportOut)
def import_skill_endpoint(payload: SkillImportIn) -> SkillImportOut:
    """Publishes into the catalog with origin="skills_sh". Auto-activates
    only when the source's own risk scan reads clean; otherwise lands as
    pending_review and needs POST /{slug}/approve before it's attachable.
    """
    try:
        result = import_skill(payload.source, payload.skill_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - external CLI boundary, surfaced to the caller
        raise HTTPException(status_code=502, detail=f"Import failed: {exc}") from None
    return SkillImportOut(
        slug=result.slug, version=result.version, status=result.status, risk_notes=result.risk_notes
    )


@router.post("/{slug}/approve", response_model=SkillOut)
def approve_skill_endpoint(slug: str, payload: SkillApproveIn) -> SkillOut:
    """Moves a pending_review skill to active - the review step a human
    takes after reading its SKILL.md (and any bundled scripts).
    """
    try:
        skill = service.approve_skill(slug, payload.version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(skill)


@router.post("/{slug}/reject", response_model=SkillOut)
def reject_skill_endpoint(slug: str) -> SkillOut:
    """Archives a pending_review (or any) skill - it stays in the catalog
    for old runs that used it, but can't be newly attached to a project.
    """
    try:
        skill = service.archive_skill(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _skill_out(skill)


@router.get("/{slug}/versions", response_model=list[SkillVersionOut])
def list_skill_versions(slug: str) -> list[SkillVersionOut]:
    if service.get_skill(slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return [
        SkillVersionOut(
            version=v.version,
            storage_backend=v.storage_backend,
            storage_key=v.storage_key,
            checksum=v.checksum,
            byte_size=v.byte_size,
            skill_md_name=v.skill_md_name,
            skill_md_description=v.skill_md_description,
            created_at=v.created_at,
        )
        for v in service.list_versions(slug)
    ]
