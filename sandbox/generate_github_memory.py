#!/usr/bin/env python3
"""
generate_github_memory.py

Parses a GitHub repository (public or private) via the GitHub REST API and
generates a `github-memory/` folder of Markdown files summarizing the repo:

github-memory/
├── README.md
├── AGENTS.md               (NEW: agent-facing project brief — tech stack,
│                             structure, conventions, do/don't for AI agents)
├── repository.md
├── branches.md
├── pull-requests.md
├── commits.md
├── activity.md
├── releases.md
├── insights.md
├── code-structure.md       (real file trees + dependency manifests)
├── pr-diffs.md             (actual file-level PR diffs)
└── config/
    ├── branch-rules.md
    └── thresholds.md

USAGE (basic — process/history memory only):
    python3 generate_github_memory.py \
        --token <GITHUB_ACCESS_TOKEN> \
        --username <GITHUB_USERNAME> \
        --repo-url <GITHUB_REPO_URL> \
        --out github-memory

USAGE (with code context — recommended before Codex writes/pushes code):
    python3 generate_github_memory.py \
        --token <GITHUB_ACCESS_TOKEN> \
        --repo-url <GITHUB_REPO_URL> \
        --branches "feature/ag-105-x,feature/ag-73-y" \
        --dup-pr-diffs \
        --out github-memory

    --branches      Extra branches (beyond default) to pull full file trees +
                     dependency manifests for. Use this for branches Codex is
                     about to build on top of.
    --pr-diffs      Explicit PR numbers to fetch file diffs for (e.g. "12,28").
    --dup-pr-diffs  Auto-fetch diffs for every PR that shares a duplicate
                     ticket ID with another PR (same duplicate-detection used
                     in insights.md). Capped at 15 PRs to limit API usage.
    --patch-lines   Max diff lines shown per file (default: 25).

    Or set environment variables instead of flags:
        export GH_TOKEN="ghp_xxx"
        export GH_USERNAME="your-username"
        export GH_REPO_URL="https://github.com/owner/repo"
        python3 generate_github_memory.py

REQUIREMENTS:
    pip install requests

NOTES:
    - Token needs "repo" (read) scope for private repos.
    - The script never writes your token to disk.
    - code-structure.md, pr-diffs.md, and AGENTS.md are a snapshot, not live —
      re-run before Codex starts a new task if the branch may have changed
      since.
    - AGENTS.md is written using the conventions read by AI coding agents
      (Codex, Claude Code, etc). Copy/symlink it to your repo root
      (`cp github-memory/AGENTS.md ./AGENTS.md`) if you want agents working
      directly in the repo to pick it up automatically.
"""

import argparse
import base64
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("This script requires the 'requests' library. Install it with:")
    print("    pip install requests")
    sys.exit(1)


# --------------------------------------------------------------------------
# Argument / env parsing
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Generate github-memory/ from a GitHub repo.")
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN"),
                         help="GitHub personal access token (or set GH_TOKEN env var)")
    parser.add_argument("--username", default=os.environ.get("GH_USERNAME"),
                         help="GitHub username (or set GH_USERNAME env var) — used for display only")
    parser.add_argument("--repo-url", default=os.environ.get("GH_REPO_URL"),
                         help="GitHub repo URL, e.g. https://github.com/owner/repo (or set GH_REPO_URL env var)")
    parser.add_argument("--out", default="github-memory",
                         help="Output folder name (default: github-memory)")
    parser.add_argument("--branches", default="",
                         help="Comma-separated extra branch names to fetch file trees for, "
                              "beyond the default branch (e.g. 'feature/ag-105-x,feature/ag-73-y')")
    parser.add_argument("--pr-diffs", default="",
                         help="Comma-separated PR numbers to fetch file diffs for (e.g. '12,13,28')")
    parser.add_argument("--dup-pr-diffs", action="store_true",
                         help="Auto-fetch file diffs for all PRs that share a duplicate ticket ID "
                              "(same heuristic used in insights.md). Capped at 15 PRs total.")
    parser.add_argument("--patch-lines", type=int, default=25,
                         help="Max lines of each file's diff patch to include per PR file (default: 25)")
    args = parser.parse_args()

    if not args.token:
        parser.error("Missing --token (or GH_TOKEN env var)")
    if not args.repo_url:
        parser.error("Missing --repo-url (or GH_REPO_URL env var)")

    return args


def parse_owner_repo(repo_url: str):
    """Extract owner/repo from a GitHub URL, with or without .git suffix."""
    repo_url = repo_url.strip().rstrip("/")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", repo_url)
    if not m:
        raise ValueError(f"Could not parse owner/repo from URL: {repo_url}")
    return m.group(1), m.group(2)


# --------------------------------------------------------------------------
# GitHub API client
# --------------------------------------------------------------------------

class GitHubClient:
    def __init__(self, token: str, owner: str, repo: str):
        self.owner = owner
        self.repo = repo
        self.base = f"https://api.github.com/repos/{owner}/{repo}"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        })

    def get(self, path: str, params=None):
        if path == "":
            url = self.base
        elif path.startswith("/"):
            url = f"{self.base}{path}"
        else:
            url = path
        resp = self.session.get(url, params=params)
        if resp.status_code == 404:
            return None
        # 409 "Git Repository is empty" - a brand-new repo with no commits yet.
        # Every list endpoint (/commits, /branches, /git/trees) 409s until the
        # first push; treat it as "no data" so a snapshot of an empty repo
        # still succeeds instead of aborting the whole planning run.
        if resp.status_code == 409:
            return None
        # Secondary rate limiting comes back as 403/429 with a Retry-After or
        # an exhausted rate-limit budget. Skip this section rather than kill
        # the snapshot - a partial snapshot beats none.
        if resp.status_code in (403, 429) and (
            resp.headers.get("Retry-After")
            or resp.headers.get("X-RateLimit-Remaining") == "0"
        ):
            sys.stderr.write(
                f"WARNING: rate-limited on {url} (status {resp.status_code}); "
                "skipping this section of the snapshot.\n"
            )
            return None
        resp.raise_for_status()
        return resp.json()

    def get_paginated(self, path: str, params=None, max_pages=10):
        """Fetch all pages of a list endpoint (up to max_pages * 100 items)."""
        params = dict(params or {})
        params["per_page"] = 100
        results = []
        page = 1
        while page <= max_pages:
            params["page"] = page
            data = self.get(path, params=params)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results

    def repo_info(self):
        return self.get("")

    def branches(self):
        return self.get_paginated("/branches")

    def pulls(self):
        return self.get_paginated("/pulls", params={"state": "all"})

    def commits(self, sha="main"):
        return self.get_paginated("/commits", params={"sha": sha})

    def releases(self):
        return self.get_paginated("/releases")

    def tags(self):
        return self.get_paginated("/tags")

    def contributors(self):
        return self.get_paginated("/contributors") or []

    def languages(self):
        return self.get("/languages") or {}

    def issues(self):
        return self.get_paginated("/issues", params={"state": "all"})

    def root_contents(self):
        return self.get("/contents") or []

    def tree(self, branch: str):
        """Full recursive file tree for a given branch/sha."""
        data = self.get(f"/git/trees/{branch}", params={"recursive": "1"})
        if not data:
            return []
        return data.get("tree", [])

    def pr_files(self, pr_number: int, max_pages=5):
        """Files changed in a specific PR (path, status, +/- counts, patch)."""
        return self.get_paginated(f"/pulls/{pr_number}/files", max_pages=max_pages)

    def file_content(self, path: str, ref: str = None):
        """Raw text content of a single file at a given ref (branch/sha)."""
        params = {"ref": ref} if ref else None
        data = self.get(f"/contents/{path}", params=params)
        if not data or "content" not in data:
            return None
        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            return None


# --------------------------------------------------------------------------
# Markdown generators
# --------------------------------------------------------------------------

def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def build_repository_md(repo, contributors, languages, root_contents):
    fields = [
        ("Name", repo.get("name")),
        ("Owner", f"[{repo['owner']['login']}]({repo['owner']['html_url']})"),
        ("Full name", repo.get("full_name")),
        ("Visibility", repo.get("visibility")),
        ("URL", repo.get("html_url")),
        ("Description", repo.get("description") or "*(none set)*"),
        ("Default branch", f"`{repo.get('default_branch')}`"),
        ("Created", repo.get("created_at", "")[:10]),
        ("Last updated (metadata)", repo.get("updated_at", "")[:10]),
        ("Last pushed", repo.get("pushed_at", "")[:10]),
        ("Size", f"{repo.get('size')} KB"),
        ("Fork", repo.get("fork")),
        ("Archived / Disabled", f"{repo.get('archived')} / {repo.get('disabled')}"),
        ("License", (repo.get("license") or {}).get("name") or "None"),
        ("Topics", ", ".join(repo.get("topics") or []) or "None"),
        ("Stars / Watchers / Forks", f"{repo.get('stargazers_count')} / {repo.get('watchers_count')} / {repo.get('forks_count')}"),
        ("Has Issues / Projects / Wiki", f"{repo.get('has_issues')} / {repo.get('has_projects')} / {repo.get('has_wiki')}"),
        ("Open issues (incl. PRs, GitHub API convention)", repo.get("open_issues_count")),
    ]
    lines = ["# Repository\n", md_table(["Field", "Value"], fields), ""]

    lines.append("## Languages\n")
    if languages:
        rows = [(lang, bytes_) for lang, bytes_ in languages.items()]
        lines.append(md_table(["Language", "Bytes"], rows))
    else:
        lines.append("No language breakdown is reported by GitHub for this repo.")
    lines.append("")

    lines.append("## Contributors\n")
    if contributors:
        rows = [(f"[{c['login']}]({c['html_url']})", c.get("contributions")) for c in contributors]
        lines.append(md_table(["Login", "Contributions"], rows))
    else:
        lines.append("No contributor data available.")
    lines.append("")

    lines.append(f"## Root contents (`{repo.get('default_branch')}`)\n")
    if root_contents:
        for item in root_contents:
            lines.append(f"- `{item['name']}` ({item['type']})")
    else:
        lines.append("*(empty or inaccessible)*")
    lines.append("")

    return "\n".join(lines)


def build_branches_md(branches, pulls, default_branch):
    head_to_pr = defaultdict(list)
    for p in pulls:
        head_to_pr[p["head"]["ref"]].append(p)

    lines = ["# Branches\n"]
    lines.append(f"Total branches: **{len(branches)}**\n")
    lines.append("## All branches\n")

    rows = []
    for b in sorted(branches, key=lambda x: x["name"].lower()):
        name = b["name"]
        prs = head_to_pr.get(name, [])
        if name == default_branch:
            pr_str = "*(default branch)*"
        else:
            pr_str = ", ".join(f"#{p['number']} ({p['state']})" for p in prs) if prs else "—"
        rows.append((f"`{name}`", b.get("protected"), pr_str))

    lines.append(md_table(["Branch", "Protected", "Associated PR(s)"], rows))
    lines.append("")
    return "\n".join(lines)


def build_pull_requests_md(pulls):
    pulls_sorted = sorted(pulls, key=lambda p: p["number"], reverse=True)
    open_prs = [p for p in pulls if p["state"] == "open"]
    closed_prs = [p for p in pulls if p["state"] == "closed"]
    merged_prs = [p for p in pulls if p.get("merged_at")]

    lines = ["# Pull Requests\n"]
    lines.append(
        f"Total: **{len(pulls)}** | Open: **{len(open_prs)}** | "
        f"Closed: **{len(closed_prs)}** | Merged: **{len(merged_prs)}**\n"
    )
    lines.append("## All pull requests (newest first)\n")

    rows = []
    for p in pulls_sorted:
        title = p["title"].replace("|", "/")
        rows.append((
            f"#{p['number']}", p["state"], title,
            f"`{p['head']['ref']}` → `{p['base']['ref']}`",
            p["created_at"][:10], p["updated_at"][:10],
        ))
    lines.append(md_table(["#", "State", "Title", "Head → Base", "Created", "Updated"], rows))
    lines.append("")
    return "\n".join(lines)


def build_commits_md(commits, default_branch):
    lines = ["# Commits\n", f"## `{default_branch}` branch history\n"]
    if not commits:
        lines.append("No commits found.")
        return "\n".join(lines)

    rows = []
    for c in commits:
        sha = c["sha"][:7]
        author = (c.get("commit", {}).get("author") or {}).get("name", "unknown")
        date = (c.get("commit", {}).get("author") or {}).get("date", "")[:16].replace("T", " ")
        msg = c.get("commit", {}).get("message", "").split("\n")[0][:80].replace("|", "/")
        rows.append((f"`{sha}`", author, date, msg))
    lines.append(md_table(["SHA", "Author", "Date", "Message"], rows))
    lines.append(f"\nTotal commits fetched: **{len(commits)}** (most recent {len(commits)} shown; "
                  f"increase `--max-pages` in the script for more history).\n")
    return "\n".join(lines)


def build_activity_md(pulls, commits, repo):
    by_day = Counter(p["created_at"][:10] for p in pulls)
    lines = ["# Activity\n"]
    lines.append(
        f"Timeline reconstructed from pull-request creation dates "
        f"(repo created {repo.get('created_at','')[:10]}, last push {repo.get('pushed_at','')[:10]}).\n"
    )
    lines.append("## PRs opened per day\n")
    rows = [(day, by_day[day]) for day in sorted(by_day)]
    if rows:
        lines.append(md_table(["Date", "PRs opened"], rows))
    else:
        lines.append("No pull request activity found.")
    lines.append("")
    return "\n".join(lines)


def build_releases_md(releases, tags):
    lines = ["# Releases\n"]
    if not releases and not tags:
        lines.append("No releases or tags exist for this repository yet.\n")
        lines.append(f"- Releases: 0\n- Tags: 0\n")
        return "\n".join(lines)

    if releases:
        lines.append("## Releases\n")
        rows = [(r.get("tag_name"), r.get("name") or "", r.get("published_at", "")[:10],
                  "Yes" if r.get("prerelease") else "No") for r in releases]
        lines.append(md_table(["Tag", "Name", "Published", "Prerelease"], rows))
        lines.append("")

    if tags:
        lines.append("## Tags\n")
        for t in tags:
            lines.append(f"- `{t['name']}`")
        lines.append("")

    return "\n".join(lines)


def build_insights_md(branches, pulls, commits, repo):
    lines = ["# Insights\n"]
    lines.append(f"Observations from parsing this repo's current state "
                 f"(snapshot: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}).\n")

    # Duplicate ticket detection (very rough heuristic: shared prefix before first dash/slash)
    ticket_map = defaultdict(list)
    ticket_pattern = re.compile(r"([A-Za-z]+-\d+)")
    for p in pulls:
        m = ticket_pattern.search(p["head"]["ref"])
        if m:
            ticket_map[m.group(1)].append(p)

    dup_tickets = {k: v for k, v in ticket_map.items() if len(v) > 1}
    lines.append("## Duplicate ticket branches\n")
    if dup_tickets:
        for ticket, prs in sorted(dup_tickets.items(), key=lambda x: -len(x[1])):
            nums = ", ".join(f"#{p['number']}" for p in prs)
            lines.append(f"- **{ticket}**: {len(prs)} branches/PRs ({nums})")
    else:
        lines.append("No duplicate ticket-ID branches detected.")
    lines.append("")

    merged = [p for p in pulls if p.get("merged_at")]
    closed_unmerged = [p for p in pulls if p["state"] == "closed" and not p.get("merged_at")]
    lines.append("## Merge status\n")
    lines.append(f"- Merged: {len(merged)} / {len(pulls)}")
    lines.append(f"- Closed without merging: {len(closed_unmerged)}")
    lines.append("")

    unprotected = [b["name"] for b in branches if not b.get("protected")]
    lines.append("## Branch protection\n")
    lines.append(f"- {len(unprotected)} / {len(branches)} branches are unprotected.")
    lines.append("")

    authors = set(p["user"]["login"] for p in pulls if p.get("user"))
    lines.append("## Contributors to PRs\n")
    lines.append(f"- Unique PR authors: {', '.join(sorted(authors)) if authors else 'none'}")
    lines.append("")

    return "\n".join(lines)


def build_branch_rules_md(branches):
    unprotected = [b["name"] for b in branches if not b.get("protected")]
    protected = [b["name"] for b in branches if b.get("protected")]
    lines = ["# Branch Rules\n"]
    lines.append(f"Protected branches: **{len(protected)}** | Unprotected: **{len(unprotected)}**\n")
    if protected:
        lines.append("## Protected branches\n")
        for b in protected:
            lines.append(f"- `{b}`")
        lines.append("")
    lines.append("## Suggested rules (not yet applied)\n")
    lines.append("1. Protect the default branch: require at least 1 review before merge.")
    lines.append("2. Require PRs to pass CI before merge.")
    lines.append("3. Prevent force-pushes to protected branches.")
    lines.append("4. Adopt a policy for stale/duplicate branches (see `../insights.md`).\n")
    lines.append("These are recommendations only — nothing has been changed on GitHub.")
    return "\n".join(lines)


def build_thresholds_md(pulls, branches, commits):
    open_prs = len([p for p in pulls if p["state"] == "open"])
    merged = len([p for p in pulls if p.get("merged_at")])
    closed_unmerged = len([p for p in pulls if p["state"] == "closed" and not p.get("merged_at")])
    merge_ratio = f"{(merged / len(pulls) * 100):.0f}%" if pulls else "N/A"

    lines = ["# Thresholds\n"]
    lines.append(
        "GitHub does not expose a native \"thresholds\" concept via the API, "
        "so this file is a set of suggested monitoring thresholds based on the "
        "repo's current state. Nothing here is enforced by GitHub — edit freely.\n"
    )
    rows = [
        ("Open PRs", open_prs, "Alert if > 40 (backlog growing)"),
        ("Commits fetched (default branch)", len(commits), "Informational"),
        ("Merged PR ratio", merge_ratio, "Flag if 0% after > 20 PRs opened"),
        ("Closed-without-merge PRs", closed_unmerged, "Flag if > 20% of closed PRs"),
        ("Unprotected branches", len([b for b in branches if not b.get("protected")]), "Should trend toward 0 for key branches"),
    ]
    lines.append(md_table(["Metric", "Current value", "Suggested watch threshold"], rows))
    lines.append("\n## How to refresh\n")
    lines.append("Re-run `generate_github_memory.py` to regenerate this snapshot and compare against these thresholds.")
    return "\n".join(lines)


DEPENDENCY_MANIFESTS = [
    "requirements.txt", "pyproject.toml", "Pipfile",
    "package.json", "package-lock.json", "yarn.lock",
    "Gemfile", "go.mod", "pom.xml", "build.gradle",
    "Cargo.toml", "composer.json",
]

# Manifest -> conventional install/test commands, used to seed AGENTS.md
# "Setup" and "Testing" sections without guessing blindly.
MANIFEST_HINTS = {
    "requirements.txt": {"install": "pip install -r requirements.txt", "lang": "Python"},
    "pyproject.toml": {"install": "pip install -e . (or `poetry install` if Poetry is used)", "lang": "Python"},
    "Pipfile": {"install": "pipenv install", "lang": "Python"},
    "package.json": {"install": "npm install (or `yarn` / `pnpm install`)", "lang": "JavaScript/TypeScript"},
    "package-lock.json": {"install": "npm ci", "lang": "JavaScript/TypeScript"},
    "yarn.lock": {"install": "yarn install", "lang": "JavaScript/TypeScript"},
    "Gemfile": {"install": "bundle install", "lang": "Ruby"},
    "go.mod": {"install": "go mod download", "lang": "Go"},
    "pom.xml": {"install": "mvn install", "lang": "Java"},
    "build.gradle": {"install": "./gradlew build", "lang": "Java/Kotlin"},
    "Cargo.toml": {"install": "cargo build", "lang": "Rust"},
    "composer.json": {"install": "composer install", "lang": "PHP"},
}


def build_code_structure_md(client, branch_trees, manifest_contents):
    """
    branch_trees: dict of {branch_name: [tree_entries]}
    manifest_contents: dict of {branch_name: {manifest_filename: content_str}}
    """
    lines = ["# Code Structure\n"]
    lines.append(
        "Real file trees pulled directly from GitHub (not inferred). Use this "
        "instead of guessing repo layout before writing new code.\n"
    )

    for branch, tree in branch_trees.items():
        blobs = [t for t in tree if t.get("type") == "blob"]
        dirs = sorted(set(os.path.dirname(t["path"]) for t in blobs if os.path.dirname(t["path"])))

        lines.append(f"## Branch: `{branch}`\n")
        lines.append(f"Total tracked files: **{len(blobs)}**\n")

        if dirs:
            lines.append("### Directories\n")
            for d in dirs:
                lines.append(f"- `{d}/`")
            lines.append("")

        lines.append("### All files\n")
        for t in sorted(blobs, key=lambda x: x["path"]):
            size = t.get("size")
            size_str = f" ({size} B)" if size is not None else ""
            lines.append(f"- `{t['path']}`{size_str}")
        lines.append("")

        manifests_here = manifest_contents.get(branch, {})
        if manifests_here:
            lines.append("### Dependency manifests found\n")
            for fname, content in manifests_here.items():
                lines.append(f"**`{fname}`**")
                lines.append("```")
                # cap manifest preview to avoid huge dumps
                preview = "\n".join(content.splitlines()[:60])
                lines.append(preview)
                if len(content.splitlines()) > 60:
                    lines.append(f"... ({len(content.splitlines()) - 60} more lines, truncated)")
                lines.append("```")
                lines.append("")

    return "\n".join(lines)


def build_pr_diffs_md(pr_diffs, patch_lines_cap, capped_note=None):
    """
    pr_diffs: dict of {pr_number: {"pr": pr_obj, "files": [file_diff_entries]}}
    """
    lines = ["# Pull Request Diffs\n"]
    lines.append(
        "Actual file-level changes for selected PRs, pulled from GitHub. Use this "
        "to see what a branch actually changed before assuming duplicate work is "
        "identical, or before writing new code that might collide with it.\n"
    )
    if capped_note:
        lines.append(f"> {capped_note}\n")

    if not pr_diffs:
        lines.append("No PR diffs were requested. Use `--pr-diffs` or `--dup-pr-diffs` to populate this file.")
        return "\n".join(lines)

    for pr_number in sorted(pr_diffs.keys()):
        entry = pr_diffs[pr_number]
        pr = entry["pr"]
        files = entry["files"]

        lines.append(f"## PR #{pr_number}: {pr['title']}\n")
        lines.append(f"`{pr['head']['ref']}` → `{pr['base']['ref']}` | state: {pr['state']} | "
                      f"files changed: {len(files)}\n")

        for f in files:
            lines.append(f"### `{f['filename']}`")
            lines.append(f"Status: {f['status']} | +{f.get('additions', 0)} / -{f.get('deletions', 0)}\n")
            patch = f.get("patch")
            if patch:
                patch_snippet_lines = patch.splitlines()[:patch_lines_cap]
                lines.append("```diff")
                lines.append("\n".join(patch_snippet_lines))
                if len(patch.splitlines()) > patch_lines_cap:
                    lines.append(f"... ({len(patch.splitlines()) - patch_lines_cap} more lines, truncated)")
                lines.append("```")
            else:
                lines.append("*(no inline patch available — likely a binary file or too large)*")
            lines.append("")

    return "\n".join(lines)


def build_agents_md(repo, branches, pulls, contributors, languages,
                     branch_trees, manifest_contents, default_branch):
    """
    Generates an AGENTS.md — a project brief written for AI coding agents
    (Codex, Claude Code, etc.), not humans. Follows the common AGENTS.md
    convention: short, factual, oriented around "what an agent needs before
    touching this repo".

    All content here is derived directly from data already fetched from the
    GitHub API — nothing is guessed or invented.
    """
    lines = ["# AGENTS.md\n"]
    lines.append(
        f"> Auto-generated from the live state of "
        f"**{repo.get('full_name')}** on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        f"Re-run `generate_github_memory.py` to refresh. If this file is at "
        f"the repo root, agents that read `AGENTS.md` will pick it up "
        f"automatically.\n"
    )

    # --- Overview ---
    lines.append("## Project overview\n")
    desc = repo.get("description") or "*(no description set on GitHub)*"
    lines.append(f"{desc}\n")
    lines.append(f"- Repo: [{repo.get('full_name')}]({repo.get('html_url')}) ({repo.get('visibility')})")
    lines.append(f"- Default branch: `{default_branch}`")
    if repo.get("topics"):
        lines.append(f"- Topics: {', '.join(repo['topics'])}")
    lines.append("")

    # --- Tech stack ---
    lines.append("## Tech stack\n")
    if languages:
        total = sum(languages.values()) or 1
        ranked = sorted(languages.items(), key=lambda x: -x[1])
        lang_line = ", ".join(f"{lang} ({bytes_/total*100:.0f}%)" for lang, bytes_ in ranked)
        lines.append(f"By bytes of code: {lang_line}\n")
    else:
        lines.append("No language breakdown reported by GitHub for this repo.\n")

    default_manifests = manifest_contents.get(default_branch, {})
    if default_manifests:
        lines.append("Dependency manifests found at repo root:")
        for fname in default_manifests:
            hint = MANIFEST_HINTS.get(fname)
            tag = f" — {hint['lang']}" if hint else ""
            lines.append(f"- `{fname}`{tag}")
        lines.append("")

    # --- Setup ---
    lines.append("## Setup\n")
    if default_manifests:
        for fname in default_manifests:
            hint = MANIFEST_HINTS.get(fname)
            if hint:
                lines.append(f"- `{fname}` detected → typically: `{hint['install']}`")
        lines.append(
            "\n*(Commands above are conventions inferred from the manifest "
            "filename found in the repo — verify against any README or CI "
            "config before running.)*\n"
        )
    else:
        lines.append("No recognized dependency manifest found at the repo root "
                      "(checked: " + ", ".join(f"`{m}`" for m in DEPENDENCY_MANIFESTS) + "). "
                      "Check `code-structure.md` for the full file tree.\n")

    # --- Structure ---
    lines.append("## Repository structure\n")
    tree = branch_trees.get(default_branch, [])
    blobs = [t for t in tree if t.get("type") == "blob"]
    top_dirs = sorted(set(t["path"].split("/")[0] for t in blobs if "/" in t["path"]))
    if top_dirs:
        lines.append(f"Top-level directories on `{default_branch}`:")
        for d in top_dirs:
            lines.append(f"- `{d}/`")
        lines.append("")
    lines.append(f"Full file tree: see `code-structure.md` ({len(blobs)} tracked files on `{default_branch}`).\n")

    # --- Branching / PR conventions ---
    lines.append("## Branching & PR conventions\n")
    ticket_pattern = re.compile(r"([A-Za-z]+-\d+)")
    ticketed = [p for p in pulls if ticket_pattern.search(p["head"]["ref"])]
    if ticketed:
        sample = ticket_pattern.search(ticketed[0]["head"]["ref"]).group(1)
        lines.append(f"- Branch names commonly embed a ticket ID (e.g. `{ticketed[0]['head']['ref']}` "
                      f"→ ticket `{sample}`). Follow this pattern for new branches where applicable.")
    protected = [b["name"] for b in branches if b.get("protected")]
    if protected:
        lines.append(f"- Protected branches (do not force-push, do not commit directly): "
                      + ", ".join(f"`{b}`" for b in protected))
    else:
        lines.append("- No branches are currently marked protected on GitHub — treat "
                      f"`{default_branch}` as protected by convention anyway.")
    merged = len([p for p in pulls if p.get("merged_at")])
    lines.append(f"- {merged}/{len(pulls)} pull requests fetched have been merged. "
                  "See `pull-requests.md` and `insights.md` for full history and merge patterns.")
    lines.append("")

    # --- Duplicate work warning ---
    dup_ticket_map = defaultdict(list)
    for p in pulls:
        m = ticket_pattern.search(p["head"]["ref"])
        if m:
            dup_ticket_map[m.group(1)].append(p)
    dup_tickets = {k: v for k, v in dup_ticket_map.items() if len(v) > 1}
    if dup_tickets:
        lines.append("## ⚠ Known duplicate-ticket branches\n")
        lines.append(
            "These ticket IDs have more than one branch/PR against them. Check "
            "`pr-diffs.md` (or run with `--dup-pr-diffs`) to see what each one "
            "actually changed before starting new work on the same ticket, to "
            "avoid redoing or conflicting with existing work.\n"
        )
        for ticket, prs in sorted(dup_tickets.items(), key=lambda x: -len(x[1])):
            nums = ", ".join(f"#{p['number']}" for p in prs)
            lines.append(f"- **{ticket}**: {nums}")
        lines.append("")

    # --- Contributors ---
    if contributors:
        lines.append("## Contributors\n")
        lines.append(", ".join(f"`{c['login']}`" for c in contributors[:15]))
        lines.append("")

    # --- Do / Don't for agents ---
    lines.append("## Notes for agents\n")
    lines.append(f"- Read `code-structure.md` before creating new files, to match existing layout and naming.")
    lines.append(f"- Read `pr-diffs.md` before touching an area another branch already changed on the same ticket.")
    lines.append(f"- Do not commit directly to `{default_branch}`" +
                  (" or any other protected branch." if protected else "."))
    lines.append("- This file and the rest of `github-memory/` are a point-in-time snapshot, "
                  "not live data — re-run the generator script if the repo may have changed.")
    lines.append("")

    return "\n".join(lines)


def build_readme_md(repo, branches, pulls, commits, releases, username):
    open_prs = len([p for p in pulls if p["state"] == "open"])
    closed_prs = len([p for p in pulls if p["state"] == "closed"])
    merged_prs = len([p for p in pulls if p.get("merged_at")])

    lines = ["# github-memory\n"]
    lines.append(
        f"A structured, point-in-time snapshot of the GitHub repository "
        f"**[{repo['full_name']}]({repo['html_url']})** "
        f"({repo.get('visibility')}), parsed via the GitHub API"
        + (f" by `{username}`" if username else "") + ".\n"
    )
    lines.append(f"**Snapshot taken:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n")

    lines.append("## Structure\n")
    structure_rows = [
        ("`AGENTS.md`", "Agent-facing project brief — tech stack, structure, conventions, do/don't"),
        ("`repository.md`", "Core repo metadata — visibility, size, contributors, languages, root contents"),
        ("`branches.md`", "All branches, protection status, and their associated PRs"),
        ("`pull-requests.md`", "Every PR with state, head/base branches, and dates"),
        ("`commits.md`", "Commit history on the default branch"),
        ("`activity.md`", "Timeline of PR activity"),
        ("`releases.md`", "Releases and tags"),
        ("`insights.md`", "Patterns and observations pulled from the raw data"),
        ("`code-structure.md`", "Real file trees and dependency manifests for fetched branches"),
        ("`pr-diffs.md`", "Actual file-level diffs for selected/duplicate-ticket PRs"),
        ("`config/branch-rules.md`", "Branch protection status and suggestions"),
        ("`config/thresholds.md`", "Suggested monitoring thresholds for future snapshots"),
    ]
    lines.append(md_table(["File", "Contents"], structure_rows))
    lines.append("")

    lines.append("## Quick facts\n")
    lines.append(f"- {len(branches)} branches, {len(pulls)} pull requests "
                  f"({open_prs} open, {closed_prs} closed, {merged_prs} merged)")
    lines.append(f"- {len(commits)} commits fetched on `{repo.get('default_branch')}`")
    lines.append(f"- {len(releases)} releases")
    lines.append("")

    lines.append("## Regenerating\n")
    lines.append("Run `generate_github_memory.py` again whenever you want an up-to-date snapshot.")
    return "\n".join(lines)


def build_skill_md(repo):
    return (
        "---\n"
        "name: github-memory\n"
        "description: Use this GitHub repository snapshot for codebase context, "
        "branch history, pull requests, and repository conventions before planning or implementing.\n"
        "---\n\n"
        f"Read the files in this directory when the task requires repository history or context for "
        f"{repo['full_name']}. This is a generated snapshot, not live GitHub data.\n"
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    args = parse_args()
    owner, repo_name = parse_owner_repo(args.repo_url)

    print(f"Connecting to {owner}/{repo_name} ...")
    client = GitHubClient(args.token, owner, repo_name)

    repo = client.repo_info()
    if repo is None:
        print("ERROR: Repo not found or token lacks access.")
        sys.exit(1)

    default_branch = repo.get("default_branch", "main")

    print("Fetching branches...")
    branches = client.branches() or []
    print("Fetching pull requests...")
    pulls = client.pulls() or []
    print("Fetching commits...")
    commits = client.commits(sha=default_branch) or []
    print("Fetching releases...")
    releases = client.releases() or []
    print("Fetching tags...")
    tags = client.tags() or []
    print("Fetching contributors...")
    contributors = client.contributors() or []
    print("Fetching languages...")
    languages = client.languages() or {}
    print("Fetching root contents...")
    root_contents = client.root_contents() or []

    # ---- Code structure: file trees + dependency manifests ----
    branches_to_tree = [default_branch]
    if args.branches:
        branches_to_tree += [b.strip() for b in args.branches.split(",") if b.strip()]
    branches_to_tree = list(dict.fromkeys(branches_to_tree))  # dedupe, keep order

    branch_trees = {}
    manifest_contents = {}
    for b in branches_to_tree:
        print(f"Fetching file tree for branch '{b}'...")
        tree = client.tree(b)
        branch_trees[b] = tree

        found_manifests = {}
        top_level_names = {os.path.basename(t["path"]): t["path"]
                            for t in tree if t.get("type") == "blob" and "/" not in t["path"]}
        for manifest_name in DEPENDENCY_MANIFESTS:
            if manifest_name in top_level_names:
                content = client.file_content(top_level_names[manifest_name], ref=b)
                if content is not None:
                    found_manifests[manifest_name] = content
        manifest_contents[b] = found_manifests

    # ---- PR diffs ----
    pr_diffs = {}
    capped_note = None
    requested_pr_numbers = set()
    if args.pr_diffs:
        requested_pr_numbers |= {int(n.strip()) for n in args.pr_diffs.split(",") if n.strip()}

    if args.dup_pr_diffs:
        ticket_map = defaultdict(list)
        ticket_pattern = re.compile(r"([A-Za-z]+-\d+)")
        for p in pulls:
            m = ticket_pattern.search(p["head"]["ref"])
            if m:
                ticket_map[m.group(1)].append(p)
        dup_pr_numbers = [p["number"] for prs in ticket_map.values() if len(prs) > 1 for p in prs]
        requested_pr_numbers |= set(dup_pr_numbers)

    requested_pr_numbers = sorted(requested_pr_numbers)
    if len(requested_pr_numbers) > 15:
        capped_note = (f"{len(requested_pr_numbers)} PRs matched, capped to the first 15 "
                        f"to limit API usage. Use --pr-diffs to target specific PRs instead.")
        requested_pr_numbers = requested_pr_numbers[:15]

    pulls_by_number = {p["number"]: p for p in pulls}
    for pr_number in requested_pr_numbers:
        pr_obj = pulls_by_number.get(pr_number)
        if not pr_obj:
            print(f"  Skipping PR #{pr_number}: not found in fetched pull list.")
            continue
        print(f"Fetching file diffs for PR #{pr_number}...")
        files = client.pr_files(pr_number) or []
        pr_diffs[pr_number] = {"pr": pr_obj, "files": files}

    out_dir = args.out
    config_dir = os.path.join(out_dir, "config")
    os.makedirs(config_dir, exist_ok=True)

    files = {
        "SKILL.md": build_skill_md(repo),
        "repository.md": build_repository_md(repo, contributors, languages, root_contents),
        "branches.md": build_branches_md(branches, pulls, default_branch),
        "pull-requests.md": build_pull_requests_md(pulls),
        "commits.md": build_commits_md(commits, default_branch),
        "activity.md": build_activity_md(pulls, commits, repo),
        "releases.md": build_releases_md(releases, tags),
        "insights.md": build_insights_md(branches, pulls, commits, repo),
        "code-structure.md": build_code_structure_md(client, branch_trees, manifest_contents),
        "pr-diffs.md": build_pr_diffs_md(pr_diffs, args.patch_lines, capped_note),
        "AGENTS.md": build_agents_md(repo, branches, pulls, contributors, languages,
                                      branch_trees, manifest_contents, default_branch),
    }

    config_files = {
        "branch-rules.md": build_branch_rules_md(branches),
        "thresholds.md": build_thresholds_md(pulls, branches, commits),
    }

    readme = build_readme_md(repo, branches, pulls, commits, releases, args.username)

    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme + "\n")

    for fname, content in files.items():
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(content + "\n")

    for fname, content in config_files.items():
        with open(os.path.join(config_dir, fname), "w", encoding="utf-8") as f:
            f.write(content + "\n")

    print(f"\nDone. Generated '{out_dir}/' with:")
    for root, _, filenames in os.walk(out_dir):
        for fn in filenames:
            print(" -", os.path.relpath(os.path.join(root, fn)))


if __name__ == "__main__":
    main()