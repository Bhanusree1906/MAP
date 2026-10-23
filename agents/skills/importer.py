"""Bulk-import skills from the wider Agent Skills ecosystem into our own
versioned, reviewed catalog (see service.py).

Discovery and fetch are delegated to the `skills` CLI (github.com/
vercel-labs/skills, npm package "skills") - the same tool already baked
into sandbox/Dockerfile at build time. It already knows how to enumerate
and materialize any skill from a huge range of sources (GitHub repos,
skills.sh package ids) in the universal SKILL.md format; we don't
reimplement a registry crawler.

Nothing from that CLI is trusted directly, though - Snyk's ToxicSkills study
found ~36% of skills in public registries have security flaws, and skills
"run with full agent permissions" (the CLI's own warning on every install).
publish_version() is the only way a skill becomes attachable to a project,
and import_skill() only auto-activates when every risk signal the CLI
itself surfaces reads clean; anything else lands as "pending_review" until
a human calls approve_skill().

The CLI has no machine-readable output for the two operations we need
(`add --list` to enumerate a source, and the risk table shown during
`add`) - `--json` only covers the unrelated `list`/`ls` subcommand (already-
installed skills). So this module parses its human-facing text output.
That's inherently a little fragile against upstream CLI changes; parsing
fails closed (empty list / pending_review) rather than raising, since a
degraded result is far better than either crashing the catalog or - worse -
silently treating an unparseable risk table as safe.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .packaging import validate_slug
from .service import publish_version

_SKILLS_CLI_TIMEOUT_SECONDS = 180

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_LIST_START_RE = re.compile(r"Available Skills")
_LIST_END_RE = re.compile(r"^└")
# `add --list`'s output box-draws each entry as a 4-space-indented name line,
# a blank separator, then a 6-space-indented single-line description -
# verified empirically against a 341-skill source repo, not documented.
_NAME_LINE_RE = re.compile(r"^\S+ {4}(\S.*)$")
_DESC_LINE_RE = re.compile(r"^\S+ {6}(\S.*)$")

# The "Security Risk Assessments" table's three columns (Gen/Socket/Snyk),
# matched by content rather than fixed column offsets so it survives the
# table's box-drawing width changing with the skill name's length.
_GEN_RE = re.compile(r"\b(Safe|Risky|Malicious|Unknown)\b")
_SOCKET_RE = re.compile(r"(\d+)\s+alerts?", re.IGNORECASE)
_SNYK_RE = re.compile(r"\b(Low|Medium|High)\s+Risk\b", re.IGNORECASE)
_SOURCE_RE = re.compile(r"(?:https://github\.com/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$")


@dataclass(frozen=True)
class ImportCandidate:
    name: str
    description: str


@dataclass(frozen=True)
class ImportResult:
    slug: str
    version: str
    status: str  # "active" | "pending_review"
    risk_notes: str | None


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _run_skills_cli(args: list[str], cwd: Path | None = None) -> str:
    executable = shutil.which("npx.cmd") or shutil.which("npx")
    if executable is None:
        raise RuntimeError("Skills import requires Node.js and npx on the backend PATH")
    result = subprocess.run(
        [executable, "--yes", "skills", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_SKILLS_CLI_TIMEOUT_SECONDS,
    )
    # The CLI writes its TUI to stdout even on a hard failure - only fall
    # back to stderr when stdout is empty, so a real crash isn't swallowed.
    return _strip_ansi(result.stdout or result.stderr)


def list_importable(source: str, query: str | None = None) -> list[ImportCandidate]:
    """Enumerate every skill in a source (GitHub URL or owner/repo).

    `add --list --skill <query>` does NOT actually filter its output by
    `--skill` (verified: it always lists the whole repo) - so this always
    fetches the full list and filters client-side instead.
    """
    source = source.strip()
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("Source must be a GitHub repository URL or owner/repository, not an npx command")
    output = _run_skills_cli(["add", source, "--list", "--yes"])
    lines = output.splitlines()

    start = next((i for i, line in enumerate(lines) if _LIST_START_RE.search(line)), None)
    if start is None:
        return []
    end = next(
        (i for i in range(start, len(lines)) if _LIST_END_RE.match(lines[i].strip())),
        len(lines),
    )

    candidates: list[ImportCandidate] = []
    pending_name: str | None = None
    for line in lines[start + 1 : end]:
        name_match = _NAME_LINE_RE.match(line)
        desc_match = _DESC_LINE_RE.match(line)
        if name_match:
            pending_name = name_match.group(1).strip()
        elif desc_match and pending_name:
            candidates.append(ImportCandidate(name=pending_name, description=desc_match.group(1).strip()))
            pending_name = None

    if query:
        q = query.strip().lower()
        candidates = [c for c in candidates if q in c.name.lower() or q in c.description.lower()]
    return candidates


def _parse_risk(text: str) -> tuple[str | None, bool]:
    """Returns (risk_notes, is_low_risk).

    is_low_risk is True only when all three signals are present and every
    one reads clean - a missing or unrecognized signal always resolves to
    False (pending_review), never the reverse, per this module's docstring.
    """
    gen = _GEN_RE.search(text)
    socket = _SOCKET_RE.search(text)
    snyk = _SNYK_RE.search(text)
    if not (gen and socket and snyk):
        return None, False
    gen_val, socket_alerts, snyk_val = gen.group(1), int(socket.group(1)), snyk.group(1)
    risk_notes = f"Gen: {gen_val} | Socket: {socket_alerts} alerts | Snyk: {snyk_val} Risk"
    is_low_risk = gen_val == "Safe" and socket_alerts == 0 and snyk_val.lower() == "low"
    return risk_notes, is_low_risk


def import_skill(source: str, skill_name: str, version: str = "1") -> ImportResult:
    """Materialize one skill from `source` and publish it into our catalog.

    Lands with origin="skills_sh". Auto-activates only when every risk
    signal the `skills` CLI surfaces reads clean; otherwise "pending_review"
    - see service.py's approve_skill for the human review step.
    """
    slug = validate_slug(skill_name)
    with tempfile.TemporaryDirectory(prefix="skill-import-") as tmp:
        tmp_path = Path(tmp)
        output = _run_skills_cli(
            ["add", source, "--skill", skill_name, "--agent", "claude-code", "--copy", "-y"],
            cwd=tmp_path,
        )
        skill_dir = tmp_path / ".claude" / "skills" / slug
        if not (skill_dir / "SKILL.md").is_file():
            raise ValueError(
                f"Import of {skill_name!r} from {source!r} did not produce a SKILL.md at "
                f"{skill_dir} - CLI output:\n{output}"
            )

        risk_notes, is_low_risk = _parse_risk(output)
        published = publish_version(
            slug,
            name=None,
            version=version,
            skill_dir=skill_dir,
            activate=is_low_risk,
            origin="skills_sh",
            origin_ref=f"{source}@{skill_name}",
            initial_status="active" if is_low_risk else "pending_review",
            risk_notes=risk_notes,
        )

    return ImportResult(
        slug=slug,
        version=published.version,
        status="active" if is_low_risk else "pending_review",
        risk_notes=risk_notes,
    )
