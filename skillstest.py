#!/usr/bin/env python3
"""Publish and manage Codex skills, attach them to projects, and write
project codebase memory. Packages are stored under ./storage (see
agents/skills/storage.py); the database only keeps metadata and the
backend-relative storage_key.

Publish a skill folder as a new immutable version (SKILL.md required):
    python skillstest.py --publish ./my-skill --slug stripe-integration --version 1.0.0
    python skillstest.py --publish ./my-skill --slug stripe-integration --name "Stripe Integration" --version 1.1.0 --no-activate

List / versions / activate:
    python skillstest.py --list
    python skillstest.py --versions stripe-integration
    python skillstest.py --activate stripe-integration --version 1.0.0

Attach skills to a project (injected into ~/.codex/skills when a sandbox
opens). Order is preserved:
    python skillstest.py --project acme-eng --attach stripe-integration,jira-conventions

Write project memory (injected as ~/.codex/skills/project-memory/SKILL.md):
    python skillstest.py --project acme-eng --memory-file ./MEMORY.md

Import skills from the wider ecosystem (GitHub repo or skills.sh source).
Auto-activates only when the source's own Socket/Snyk risk scan reads
clean; otherwise lands as pending_review until --approve:
    python skillstest.py --find https://github.com/owner/repo --query jira
    python skillstest.py --import-from https://github.com/owner/repo --skill jira-expert
    python skillstest.py --approve jira-expert --version 1
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from agents.pipeline_agent.credentials import get_project
from agents.skills import service
from agents.skills.importer import import_skill, list_importable


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish and manage Codex skills and project memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--publish", metavar="DIR", help="Path to a local skill folder to publish.")
    parser.add_argument("--slug", help="Skill slug, e.g. stripe-integration.")
    parser.add_argument("--name", help="Skill display name (defaults to SKILL.md frontmatter name).")
    parser.add_argument("--version", help="Version being published or activated, e.g. 1.0.0.")
    parser.add_argument("--origin-ref", help="Optional origin locator, e.g. a skills.sh package id.")
    parser.add_argument(
        "--no-activate", action="store_true", help="Publish without making this the active version."
    )

    parser.add_argument("--list", action="store_true", help="List published skills, then exit.")
    parser.add_argument("--versions", metavar="SLUG", help="List a skill's published versions, then exit.")
    parser.add_argument("--activate", metavar="SLUG", help="Set a skill's active version (needs --version).")

    parser.add_argument(
        "--find", metavar="SOURCE", help="List skills available in a source (GitHub repo URL), then exit."
    )
    parser.add_argument("--query", help="Filter --find results by name/description substring.")
    parser.add_argument(
        "--import-from", metavar="SOURCE", help="Source to import a skill from (needs --skill)."
    )
    parser.add_argument("--skill", help="Skill name to import from --import-from.")
    parser.add_argument(
        "--approve", metavar="SLUG", help="Move a pending_review skill to active (needs --version)."
    )

    parser.add_argument("--project", "-p", help="Project name for --attach / --memory-file.")
    parser.add_argument(
        "--attach",
        metavar="SLUGS",
        help="Comma-separated skill slugs to attach to --project (replaces the current set).",
    )
    parser.add_argument(
        "--phase",
        choices=["planning", "implementation", "both"],
        default="both",
        help="Sandbox phase --attach installs these skills for (default: both).",
    )
    parser.add_argument("--memory-file", metavar="PATH", help="Markdown file to publish as project memory.")

    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = _parse_args()

    if args.list:
        skills = service.list_skills()
        if not skills:
            print("No skills published yet.")
            return
        for skill in skills:
            active = service.resolved_version_label(skill) or "-"
            print(
                f"- {skill.slug}: {skill.name!r} (active: {active}, origin: {skill.origin}, status: {skill.status})"
            )
        return

    if args.versions:
        versions = service.list_versions(args.versions)
        if not versions:
            print(f"No versions published for {args.versions!r}.")
            return
        for v in versions:
            print(f"- {v.version} ({v.storage_backend}:{v.storage_key}, {v.checksum}, {v.byte_size} bytes)")
        return

    try:
        if args.activate:
            if not args.version:
                raise SystemExit("--activate requires --version")
            skill = service.set_active_version(args.activate, args.version)
            print(f"{skill.slug!r} active version is now {service.resolved_version_label(skill)!r}.")
            return

        if args.publish:
            if not (args.slug and args.version):
                raise SystemExit("--publish requires --slug and --version")
            origin = "skills_sh" if args.origin_ref else "custom"
            skill_version = service.publish_version(
                slug=args.slug,
                name=args.name,
                version=args.version,
                skill_dir=args.publish,
                activate=not args.no_activate,
                origin=origin,
                origin_ref=args.origin_ref,
            )
            print(
                f"Published {args.slug!r} version {skill_version.version} -> "
                f"{skill_version.storage_backend}:{skill_version.storage_key}"
            )
            return

        if args.find:
            candidates = list_importable(args.find, query=args.query)
            if not candidates:
                print(f"No skills found in {args.find!r}" + (f" matching {args.query!r}" if args.query else ""))
                return
            for c in candidates:
                print(f"- {c.name}: {c.description}")
            return

        if args.import_from:
            if not args.skill:
                raise SystemExit("--import-from requires --skill")
            result = import_skill(args.import_from, args.skill)
            print(
                f"Imported {result.slug!r} v{result.version} -> status={result.status!r} "
                f"(risk: {result.risk_notes or 'unscored'})"
            )
            if result.status == "pending_review":
                print(f"Not attachable until reviewed - see SKILL.md, then: --approve {result.slug} --version {result.version}")
            return

        if args.approve:
            if not args.version:
                raise SystemExit("--approve requires --version")
            skill = service.approve_skill(args.approve, args.version)
            print(f"{skill.slug!r} approved - active version is now {service.resolved_version_label(skill)!r}.")
            return

        if args.attach is not None:
            if not args.project:
                raise SystemExit("--attach requires --project")
            project = get_project(args.project)
            if project is None:
                raise SystemExit(f"No project named {args.project!r}.")
            slugs = [part.strip() for part in args.attach.split(",") if part.strip()]
            attachments = [service.SkillAttachment(slug=s, phase=args.phase) for s in slugs]
            service.set_project_skills(project.id, attachments)
            installs = service.list_project_installs(project.id)
            labels = ", ".join(f"{i.slug}@{i.version}" for i in installs) or "(none)"
            print(f"Project {project.name!r} skills: {labels}")
            return

        if args.memory_file:
            if not args.project:
                raise SystemExit("--memory-file requires --project")
            project = get_project(args.project)
            if project is None:
                raise SystemExit(f"No project named {args.project!r}.")
            markdown = Path(args.memory_file).read_text(encoding="utf-8")
            memory = service.publish_memory(project.id, markdown)
            print(f"Published memory v{memory.version} -> {memory.storage_backend}:{memory.storage_key}")
            return
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    raise SystemExit("Nothing to do - see --help for usage.")


if __name__ == "__main__":
    main()
