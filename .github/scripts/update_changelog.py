"""Add the commits of one push to main to CHANGELOG.md.

Run by .github/workflows/changelog.yml with the push's `before` and `after`
shas. A merged pull request is itself a push to main, so the range is walked by
first parent: a merge commit (or a squash) is one entry, titled by its pull
request, not one entry per commit inside it.

Each entry is the pull request's title when the commit belongs to a merged pull
request into main, else the commit's subject, with links. Entries are grouped
under a `## YYYY-MM-DD` heading (commit date, UTC), newest first. The range
starts at the newest commit the file already records, so a cancelled or failed
run leaves no gap: the next one catches up. A commit already in the file is
skipped, so a rerun adds nothing twice. A commit with `[skip changelog]` in its
subject or on a line of its own, or that only touches CHANGELOG.md, is left out.

Standard library only. Without GITHUB_TOKEN (a local run) pull requests are not
looked up and every entry uses the commit subject.

    python .github/scripts/update_changelog.py --before <sha> --after <sha> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ZERO = "0" * 40
SKIP_MARK = "[skip changelog]"
HEADER = "# Changelog\n\nWhat changed on `main`, newest first. Updated automatically on every push to `main`.\n"
DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
SHA_LINK = re.compile(r"/commit/([0-9a-f]{40})\)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _is_ancestor(sha: str, of: str) -> bool:
    try:
        git("merge-base", "--is-ancestor", sha, of)
        return True
    except subprocess.CalledProcessError:
        return False  # not an ancestor, or not in this clone at all


def commits_in(before: str, after: str) -> list[str]:
    """The pushed commits, oldest first, following first parents only."""
    if before == ZERO or not before:
        return [after]  # a new branch: only the tip is known to be new
    if not _is_ancestor(before, after):
        return [after]  # a force push: the old tip is not in the new history
    out = git("rev-list", "--first-parent", "--reverse", f"{before}..{after}")
    return out.split() if out else []


def commit_info(sha: str) -> dict:
    subject, date, body = git("show", "-s", "--format=%s%x00%cI%x00%b", sha).split("\x00", 2)
    files = git("show", "--first-parent", "--format=", "--name-only", sha).split()
    return {"sha": sha, "subject": subject.strip(), "date": date[:10], "body": body, "files": files}


def skipped(info: dict) -> bool:
    """The marker counts in the subject or on a line of its own, so a message that merely mentions it
    (like the one that added this script) is not left out."""
    return SKIP_MARK in info["subject"] or any(line.strip() == SKIP_MARK for line in info["body"].splitlines())


def merged_pr(repo: str, sha: str, token: str, base: str) -> dict | None:
    """The merged pull request into `base` that this commit belongs to, if any."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{sha}/pulls",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            pulls = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"pull request lookup for {sha[:7]} failed ({e}); using the commit subject", file=sys.stderr)
        return None
    return next((p for p in pulls if p.get("merged_at") and p.get("base", {}).get("ref") == base), None)


def entry(info: dict, pr: dict | None, server: str, repo: str) -> str:
    sha = info["sha"]
    commit = f"[`{sha[:7]}`]({server}/{repo}/commit/{sha})"
    if pr:
        return f"- {pr['title'].strip()} ([#{pr['number']}]({pr['html_url']}), {commit})"
    return f"- {info['subject']} ({commit})"


def parse(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """The text before the first date heading, and each date's entry lines in file order."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        m = DATE_HEADING.match(line)
        if m:
            sections.append((m.group(1), []))
        elif sections:
            if line.strip():
                sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble).strip() + "\n", sections


def render(preamble: str, sections: list[tuple[str, list[str]]]) -> str:
    parts = [preamble.rstrip("\n")]
    for date, lines in sections:
        parts.append(f"## {date}\n\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n"


def merge(text: str, new: list[tuple[str, str]]) -> str:
    """Add (date, entry) pairs, given oldest first, at the top of their date's section."""
    preamble, sections = parse(text) if text.strip() else (HEADER, [])
    by_date = dict(sections)
    for date, line in new:  # oldest first, each inserted at the top, so the newest ends up first
        by_date.setdefault(date, []).insert(0, line)
    ordered = sorted(by_date.items(), key=lambda kv: kv[0], reverse=True)
    return render(preamble, ordered)


def main() -> int:
    ap = argparse.ArgumentParser(description="Add the commits of one push to main to CHANGELOG.md.")
    ap.add_argument("--before", default="", help="the push's old tip; used only when the file records no commit in this history")
    ap.add_argument("--after", default="HEAD")
    ap.add_argument("--file", default="CHANGELOG.md")
    ap.add_argument("--base", default="main")
    ap.add_argument("--dry-run", action="store_true", help="print the new file instead of writing it")
    a = ap.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    token = os.environ.get("GITHUB_TOKEN") or ""
    path = Path(a.file)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    recorded = SHA_LINK.findall(text)  # file order: newest first
    seen = set(recorded)
    after = git("rev-parse", a.after)
    # Start from the newest commit the file already has, when it is in this history: a run that was
    # cancelled or failed leaves a gap, and the next run fills it. The push's own `before` is the fallback.
    before = next((s for s in recorded if _is_ancestor(s, after)), a.before)

    new: list[tuple[str, str]] = []
    for sha in commits_in(before, after):
        if sha in seen:
            continue
        info = commit_info(sha)
        if skipped(info):
            continue
        if info["files"] and all(f == a.file for f in info["files"]):
            continue
        pr = merged_pr(repo, sha, token, a.base) if token and repo else None
        new.append((info["date"], entry(info, pr, server, repo)))

    if not new:
        print("nothing to add")
        return 0
    out = merge(text, new)
    if a.dry_run:
        sys.stdout.write(out)
    else:
        path.write_text(out, encoding="utf-8", newline="\n")
        print(f"added {len(new)} entr{'y' if len(new) == 1 else 'ies'} to {a.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
