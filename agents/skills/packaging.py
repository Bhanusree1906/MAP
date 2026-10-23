"""Packages a local skill folder into the tar.gz SkillVersion.storage_key
points at, and reads SKILL.md frontmatter for the catalog.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SKIP_NAMES = {".DS_Store", ".git", "__pycache__", "node_modules", ".venv"}
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,48}[A-Za-z0-9]$|^[A-Za-z0-9]$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str | None
    description: str | None


def validate_slug(slug: str) -> str:
    slug = slug.strip()
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"Invalid skill slug {slug!r}. Use 1-64 chars of lowercase letters, "
            "digits, and hyphens (must start and end alphanumeric)."
        )
    return slug


def validate_version(version: str) -> str:
    version = version.strip()
    if not _VERSION_RE.fullmatch(version) or ".." in version:
        raise ValueError(
            f"Invalid version {version!r}. Use a short semver-like label "
            "(letters, digits, dots, underscores, hyphens)."
        )
    return version


def parse_skill_frontmatter(skill_md: str) -> SkillFrontmatter:
    match = _FRONTMATTER_RE.match(skill_md)
    if match is None:
        return SkillFrontmatter(name=None, description=None)
    name = None
    description = None
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key == "name" and value:
            name = value
        elif key == "description" and value:
            description = value
    return SkillFrontmatter(name=name, description=description)


def package_skill_dir(skill_dir: Path) -> tuple[bytes, SkillFrontmatter]:
    """Tars the *contents* of skill_dir (not skill_dir itself as a named
    entry), so extracting the result directly into .codex/skills/<slug>/
    reproduces the folder's contents at that path.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise ValueError(f"{skill_dir} is not a directory")
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.is_file():
        raise ValueError(f"{skill_dir} has no SKILL.md - not a valid skill package")

    frontmatter = parse_skill_frontmatter(skill_md_path.read_text(encoding="utf-8"))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for entry in sorted(skill_dir.iterdir()):
            if entry.name in _SKIP_NAMES:
                continue
            tar.add(entry, arcname=entry.name, filter=_tar_filter)
    return buf.getvalue(), frontmatter


def extract_skill_md(package_bytes: bytes) -> str:
    """Reads SKILL.md's text back out of a published package - the inverse
    of package_skill_md/package_skill_dir, for the editor's "load current
    content" view.
    """
    with tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:gz") as tar:
        member = tar.getmember("SKILL.md")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError("SKILL.md is not a regular file in this package")
        return extracted.read().decode("utf-8")


def extract_package(package_bytes: bytes, target_dir: Path) -> None:
    """Full extract of every file in a published package (not just
    SKILL.md) - used to carry bundled files (references/, scripts/, etc.)
    forward when a plain SKILL.md text edit doesn't touch them, so editing
    in the simple single-file UI can't silently drop them (see
    service.py's save_skill_content).
    """
    target_dir = Path(target_dir)
    with tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() and not member.isdir():
                continue  # skip symlinks/devices/etc - not something a skill package should contain
            if _is_unsafe_member_path(member.name):
                continue
            tar.extract(member, path=target_dir)


def _zip_skill_prefix_and_members(zf: zipfile.ZipFile) -> tuple[str, list[str]]:
    """Finds SKILL.md in an open zip and returns (prefix, other_member_rel_paths)
    - prefix is everything up to and including the last "/" before SKILL.md
    (empty string if it's at the zip root), used to strip whatever wrapper
    folder the zip has (skills.sh downloads commonly nest one level deep,
    e.g. salesforce-developer/SKILL.md). Raises if no SKILL.md is found.
    """
    names = zf.namelist()
    skill_md_name = next((n for n in names if not n.endswith("/") and Path(n).name.lower() == "skill.md"), None)
    if skill_md_name is None:
        raise ValueError("Zip has no SKILL.md file in it")
    prefix = skill_md_name[: -len("SKILL.md")]

    others = []
    for member in names:
        if member.endswith("/") or not member.startswith(prefix) or member == skill_md_name:
            continue
        rel = member[len(prefix) :]
        if rel and not _is_unsafe_member_path(rel):
            others.append(rel)
    return prefix, others


def read_zip_skill(zip_bytes: bytes) -> tuple[str, list[str]]:
    """Preview a zip without extracting anything to disk - returns
    (SKILL.md text, other member relative paths). Used by the upload
    preview endpoint so the UI can show what will be preserved before the
    user commits to saving (see extract_zip_skill for the actual save).
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        prefix, others = _zip_skill_prefix_and_members(zf)
        content = zf.read(prefix + "SKILL.md").decode("utf-8")
    return content, others


def extract_zip_skill(zip_bytes: bytes, target_dir: Path) -> None:
    """Extracts a zip's SKILL.md and everything alongside it (references/,
    scripts/, etc.) into target_dir - see _zip_skill_prefix_and_members for
    the wrapper-folder stripping. Raises if no SKILL.md member exists.
    """
    target_dir = Path(target_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        prefix, others = _zip_skill_prefix_and_members(zf)
        for rel in [*others, "SKILL.md"]:
            member = prefix + rel
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())


def extract_zip_context(zip_bytes: bytes, target_dir: Path) -> None:
    """Extract an Agents.MD archive and normalize its entry file to SKILL.md."""
    target_dir = Path(target_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        entry = next(
            (name for name in names if Path(name).name.lower() in {"agents.md", "skill.md"}),
            None,
        )
        if entry is None:
            raise ValueError("Zip must contain Agents.MD or SKILL.md")
        prefix = entry[: -len(Path(entry).name)]
        for member in names:
            if not member.startswith(prefix):
                continue
            relative = member[len(prefix) :]
            if not relative or _is_unsafe_member_path(relative):
                continue
            destination = target_dir / ("SKILL.md" if member == entry else relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, destination.open("wb") as out:
                out.write(src.read())


def _is_unsafe_member_path(name: str) -> bool:
    """Zip-slip / tar-slip guard: refuse absolute paths and any ".." segment
    before ever joining the name onto a real filesystem path.
    """
    if not name or name.startswith("/") or name.startswith("\\"):
        return True
    return ".." in PurePosixPath(name).parts


def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = Path(tarinfo.name).name
    if name in _SKIP_NAMES:
        return None
    return tarinfo


def sha256_checksum(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def verify_checksum(data: bytes, expected: str) -> None:
    actual = sha256_checksum(data)
    if actual != expected:
        raise ValueError(f"Checksum mismatch: expected {expected}, got {actual}")
