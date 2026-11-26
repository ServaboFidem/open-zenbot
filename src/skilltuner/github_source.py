"""Reading a GitHub repository's list of skills (search and fetching a winner come later).

Texts come from strangers: names and descriptions are cleaned and cut by `index`, and nothing from a skill's text
goes into an exception. Nothing here logs.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from src.events.eventbrite import SourceError

from .github import GitHub, Tree
from .index import Listed, extras, find_index, frontmatter, parse_index, skill_folders
from .repos import RepoEntry, RepoList, RepoStore

INDEX_LIMIT = 10_000_000
SKILL_LIMIT = 1_000_000
ONE_BY_ONE_MAX = 300
REFRESH_DAYS = 7
GITHUB_WIDE = ("network", "refused", "budget")
PER_FILE = ("not_found", "too_large")


def _fail(kind: str) -> SourceError:
    error = SourceError(kind)
    error.status = 0  # type: ignore[attr-defined]
    return error


def github_wide(error: SourceError) -> bool:
    """A failure about GitHub or this computer, not one repository."""
    return error.kind in GITHUB_WIDE or (error.kind == "provider" and getattr(error, "status", 0) >= 500)


def _when(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text).timestamp() if text else None
    except ValueError:
        return None


class GitHubSkills:
    def __init__(self, client: GitHub, store: RepoStore, entries: Callable[[], list[RepoEntry]],
                 clock: Callable[[], float]):
        self.client = client
        self.store = store
        self.entries = entries
        self.clock = clock
        self.reading: set[str] = set()
        self.changed = False

    def stale(self, now: float) -> RepoEntry | None:
        never: RepoEntry | None = None
        oldest: tuple[float, RepoEntry] | None = None
        for entry in self.entries():
            if entry.id in self.reading:
                continue
            stored = self.store.get(entry.id)
            at = _when(stored.read_at) if stored else None
            if at is None:
                never = never or entry
            elif now - at >= REFRESH_DAYS * 86400 and (oldest is None or at < oldest[0]):
                oldest = (at, entry)
        return never or (oldest[1] if oldest else None)

    async def read_repo(self, entry: RepoEntry, go: Callable[[], None] = lambda: None) -> RepoList:
        self.reading.add(entry.id)
        try:
            return await self._read(entry, go)
        except SourceError as e:
            if not github_wide(e):
                self.store.set_error(entry.id, e.kind)
            raise
        finally:
            self.reading.discard(entry.id)

    async def _read(self, entry: RepoEntry, go: Callable[[], None]) -> RepoList:
        owner, repo = entry.owner, entry.repo
        go()
        info = await self.client.repo(owner, repo)
        go()
        sha = await self.client.commit(owner, repo, entry.ref or info.default_branch)
        old = self.store.get(entry.id)
        at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")
        if old is not None and old.commit == sha and old.read_at:
            old.licence_label, old.read_at, old.error = info.licence, at, ""
            self.store.put(entry.id, old)
            return old
        blobs = await self._blobs(entry, sha, go)
        folders = skill_folders(blobs, entry.folder)
        index = find_index(blobs, entry.folder, entry.index)
        skills: list[Listed] = []
        skipped, used = 0, ""
        if index:
            skills, skipped = await self._from_index(entry, sha, index, blobs, folders, go)
            used = index if skills else ""
        one_by_one = not skills
        if one_by_one:
            if len(folders) > ONE_BY_ONE_MAX:
                raise _fail("no_index")
            skills, skipped = await self._one_by_one(entry, sha, folders, blobs, go)
        stored = RepoList(sha, info.licence, at, "", used, skipped, skills, one_by_one)
        self.store.put(entry.id, stored)
        if old is None or old.skills != skills:
            self.changed = True
        return stored

    async def _blobs(self, entry: RepoEntry, sha: str, go: Callable[[], None]) -> list[tuple[str, int]]:
        go()
        tree = await self.client.tree(entry.owner, entry.repo, sha)
        if not tree.truncated:
            return tree.blobs
        if not entry.folder:
            raise _fail("too_large")
        cur = sha
        for segment in entry.folder.split("/"):
            go()
            level: Tree = await self.client.tree(entry.owner, entry.repo, cur, recursive=False)
            cur = next((s for p, s in level.trees if p == segment), "")
            if not cur:
                raise _fail("not_found")
        go()
        sub = await self.client.tree(entry.owner, entry.repo, cur)
        if sub.truncated:
            raise _fail("too_large")
        return [(f"{entry.folder}/{p}", size) for p, size in sub.blobs]

    async def _from_index(self, entry, sha, index, blobs, folders, go) -> tuple[list[Listed], int]:
        go()
        try:
            raw = await self.client.raw(entry.owner, entry.repo, sha, index, INDEX_LIMIT)
        except SourceError as e:
            if e.kind == "too_large":
                return [], 0
            raise
        try:
            listed, skipped = parse_index(raw, index, set(folders))
        except ValueError:
            return [], 0
        for k in listed:
            k.has_extra = extras(blobs, k.path)
        return listed, skipped

    async def _one_by_one(self, entry, sha, folders, blobs, go) -> tuple[list[Listed], int]:
        skills: list[Listed] = []
        skipped = 0
        for path in folders:
            go()
            try:
                raw = await self.client.raw(entry.owner, entry.repo, sha, f"{path}/SKILL.md", SKILL_LIMIT)
            except SourceError as e:
                if e.kind not in PER_FILE:
                    raise
                skipped += 1
                continue
            meta = frontmatter(raw.decode("utf-8", errors="replace"))
            if not meta.get("name") or not meta.get("description"):
                skipped += 1
                continue
            skills.append(Listed(path, meta["name"], meta["description"], "", extras(blobs, path)))
        return skills, skipped
