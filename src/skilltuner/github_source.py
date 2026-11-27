"""Reading a GitHub repository's list of skills (search and fetching a winner come later).

Texts come from strangers: names and descriptions are cleaned and cut by `index`, and nothing from a skill's text
goes into an exception. Nothing here logs.
"""
from __future__ import annotations

import re
from dataclasses import replace
from collections.abc import Callable
from datetime import datetime, timezone

from src.events.eventbrite import SourceError

from .casemark import FILE_PATH, Downloaded, Found, _clean
from .github import GitHub, Tree
from .index import (SKILL_FILE, Listed, extras_by_folder, find_index, frontmatter, parse_index, skill_folders,
                    split_frontmatter)
from .licences import canonical, licence_of_text
from .match import Matcher, top
from .repos import OWNER, REPO, SEGMENT, RepoEntry, RepoList, RepoStore

INDEX_LIMIT = 10_000_000
SKILL_LIMIT = 1_000_000
ONE_BY_ONE_MAX = 300
SKILL_FILES_MAX = 5_000
REFRESH_DAYS = 7
GITHUB_WIDE = ("network", "refused", "budget")
PER_FILE = ("not_found", "too_large")
GH_ID = re.compile(rf"gh:({OWNER})/({REPO}):({SEGMENT}(?:/{SEGMENT})*)")
ID_MAX = 512
RESULTS = 5
REFERENCES_MAX = 20
FILE_LIMIT = 200_000
NAME_MAX = 120
SUMMARY_MAX = 1_000
LICENCE_FILES = ("license", "license.md", "license.txt", "copying")


def gh_id(owner: str, repo: str, path: str) -> str:
    return f"gh:{owner}/{repo}:{path}"


def parse_gh_id(text: str) -> tuple[str, str, str]:
    """(owner, repo, path) of a source id this module made, else ValueError."""
    m = GH_ID.fullmatch(text) if isinstance(text, str) and len(text) <= ID_MAX else None
    if m is None or {".", ".."} & set(m.group(3).split("/")) or m.group(2) in (".", ".."):
        raise ValueError("Not a GitHub skill id.")
    return m.group(1), m.group(2), m.group(3)


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
        self.errors: dict[str, str] = {}  # id -> kind of the last read's GitHub-wide failure (memory only)
        self.changed = False
        self._matcher: tuple[frozenset, Matcher] | None = None
        self._files: dict[str, set[str]] = {}

    def _stored(self) -> list[tuple[RepoEntry, RepoList]]:
        out = []
        for entry in self.entries():
            stored = self.store.get(entry.id)
            if stored is not None and stored.commit:
                out.append((entry, stored))
        return out

    def matcher(self) -> Matcher:
        """IDF weights over every stored skill; rebuilt only when the stored lists change."""
        return self._matcher_of(self._stored())

    def _matcher_of(self, stored: list[tuple[RepoEntry, RepoList]]) -> Matcher:
        key = frozenset((e.id, s.commit, s.read_at) for e, s in stored)
        if self._matcher is None or self._matcher[0] != key:
            self._matcher = (key, Matcher((k.name, k.description, k.category) for _, s in stored for k in s.skills))
        return self._matcher[1]

    def search(self, words: str, keep: Callable[[Found], bool] | None = None) -> list[Found]:
        """The best skills of the stored lists for a need's words; no network. `keep` is asked of each skill in rank
        order and the first RESULTS it accepts are returned, so skills the caller would drop never take a place."""
        stored = self._stored()
        items = [(e, k) for e, s in stored for k in s.skills]
        matcher = self._matcher_of(stored)
        ranked = top(words, items, key=lambda it: (it[1].name, it[1].description, it[1].category),
                     n=len(items), matcher=matcher)
        out: list[Found] = []
        for e, k in ranked:
            found = Found(gh_id(e.owner, e.repo, k.path), k.name, k.description, [k.category] if k.category else [],
                          matcher.score(words, k.name, k.description, k.category))
            if keep is None or keep(found):
                out.append(found)
                if len(out) == RESULTS:
                    break
        return out

    def _find(self, id: str) -> tuple[RepoEntry, RepoList, Listed]:
        owner, repo, path = parse_gh_id(id)
        for entry, stored in self._stored():
            if entry.owner == owner and entry.repo == repo:
                for k in stored.skills:
                    if k.path == path:
                        return entry, stored, k
        raise _fail("not_found")

    async def read(self, id: str) -> Downloaded:
        """The skill's SKILL.md and references at the stored commit, with the licence the rules chose."""
        entry, stored, listed = self._find(id)
        sha, path = stored.commit, listed.path
        blobs = await self._blobs(entry, sha, lambda: None)
        prefix = path + "/"
        inside = [p[len(prefix):] for p, _ in blobs if p.startswith(prefix)]
        if "SKILL.md" not in inside:
            raise _fail("not_found")
        raw = await self.client.raw(entry.owner, entry.repo, sha, f"{path}/SKILL.md", SKILL_LIMIT)
        text = raw.decode("utf-8", errors="replace")
        meta = frontmatter(text)
        parts = split_frontmatter(text)
        body = _clean(parts[1] if parts else text, 200_000)
        files = sorted(p for p in inside if FILE_PATH.fullmatch(p))[:REFERENCES_MAX]
        self._files[id] = set(files)
        refs = [p for p in inside if p.startswith("references/")]
        left_out = (listed.has_extra or any(p.startswith("scripts/") for p in inside)
                    or len(refs) > len(files) or any(FILE_PATH.fullmatch(p) is None for p in refs))
        licence = await self._licence(entry, sha, path, meta.get("license", ""), inside)
        return Downloaded(
            id, _clean(meta.get("name") or listed.name, NAME_MAX),
            _clean(meta.get("description") or listed.description, SUMMARY_MAX),
            body, [listed.category] if listed.category else [], sha[:12], entry.owner, licence,
            [(p, p) for p in files], f"github.com/{entry.owner}/{entry.repo}",
            f"https://github.com/{entry.owner}/{entry.repo}/tree/{sha}/{path}", left_out, sha)

    async def _licence(self, entry: RepoEntry, sha: str, path: str, declared: str, inside: list[str]) -> str:
        """The spec's rules in order: (1) the frontmatter's `license` when one of the five; a `license` that names
        anything else drops the skill; (2) a licence file in the skill's own folder, wholly one of the five texts, and
        one that is not recognised drops the skill; (3) only with neither, the licence the owner chose."""
        if declared:
            return canonical(declared)  # "" when it names something outside the five
        names = sorted(p for p in inside if "/" not in p and p.casefold() in LICENCE_FILES)
        if names:
            try:
                raw = await self.client.raw(entry.owner, entry.repo, sha, f"{path}/{names[0]}", FILE_LIMIT)
            except SourceError as e:
                if e.kind not in PER_FILE:
                    raise
            else:
                return licence_of_text(raw.decode("utf-8", errors="replace"))
        return canonical(entry.licence)

    async def read_file(self, id: str, path: str) -> str:
        if path not in self._files.get(id, set()) or FILE_PATH.fullmatch(path) is None:
            raise ValueError("Not a file of this skill.")
        entry, stored, listed = self._find(id)
        raw = await self.client.raw(entry.owner, entry.repo, stored.commit, f"{listed.path}/{path}", FILE_LIMIT)
        return _clean(raw.decode("utf-8", errors="replace"), FILE_LIMIT)

    def has_skills(self) -> bool:
        """Whether any stored list holds a skill, so a search could find something."""
        for entry in self.entries():
            stored = self.store.get(entry.id)
            if stored is not None and stored.commit and stored.skills:
                return True
        return False

    def stale(self, now: float, skip: frozenset[str] | set[str] = frozenset()) -> RepoEntry | None:
        """The repository most due a read; ids in `skip` (ones already tried) are passed over."""
        never: RepoEntry | None = None
        oldest: tuple[float, RepoEntry] | None = None
        for entry in self.entries():
            if entry.id in self.reading or entry.id in skip:
                continue
            stored = self.store.get(entry.id)
            at = _when(stored.read_at) if stored else None
            if at is None:
                never = never or entry
            elif now - at >= REFRESH_DAYS * 86400 and (oldest is None or at < oldest[0]):
                oldest = (at, entry)
        return never or (oldest[1] if oldest else None)

    def listed(self, id: str) -> bool:
        return any(e.id == id for e in self.entries())

    def _put(self, id: str, stored: RepoList) -> bool:
        """Keep a read's list only while the owner still lists the repository: one removed during the read stays
        removed (no stored list is left behind)."""
        if self.listed(id):
            self.store.put(id, stored)
            return True
        return False

    async def read_repo(self, entry: RepoEntry, go: Callable[[], None] = lambda: None) -> RepoList:
        """Read the repository; `go` is called before each request. A repository the owner removes meanwhile ends the
        read at the next request (`removed`), whoever started it, so it spends no more of the day's budget."""
        def alive() -> None:
            if not self.listed(entry.id):
                raise _fail("removed")
            go()

        self.reading.add(entry.id)
        try:
            got = await self._read_checked(entry, alive)
            self.errors.pop(entry.id, None)  # any completed read, the pass's or the owner's, clears it
            return got
        except SourceError as e:
            if self.listed(entry.id):
                if github_wide(e):
                    self.errors[entry.id] = e.kind
                else:
                    self.errors.pop(entry.id, None)  # the stored list carries this error
                    self.store.set_error(entry.id, e.kind)
            raise
        finally:
            self.reading.discard(entry.id)

    async def _read_checked(self, entry: RepoEntry, go: Callable[[], None]) -> RepoList:
        try:
            return await self._read(entry, go)
        except ValueError:  # the client refused a path or an id it would not build a request for
            raise _fail("source_changed") from None

    async def _read(self, entry: RepoEntry, go: Callable[[], None]) -> RepoList:
        owner, repo = entry.owner, entry.repo
        go()
        info = await self.client.repo(owner, repo)
        go()
        sha = await self.client.commit(owner, repo, entry.ref or info.default_branch)
        old = self.store.get(entry.id)
        at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")
        if old is not None and old.commit == sha and old.read_at:
            old = replace(old, licence_label=info.licence, read_at=at, error="")  # the stored copy is shared
            self._put(entry.id, old)
            return old
        blobs = await self._blobs(entry, sha, go)
        if sum(1 for p, _ in blobs if p == SKILL_FILE or p.endswith("/" + SKILL_FILE)) > SKILL_FILES_MAX:
            raise _fail("too_large")
        folders = skill_folders(blobs, entry.folder)
        extra = extras_by_folder(blobs, set(folders))
        index = find_index(blobs, entry.folder, entry.index)
        skills: list[Listed] = []
        skipped, used = 0, ""
        if index:
            skills, skipped = await self._from_index(entry, sha, index, extra, folders, go)
            used = index if skills else ""
        one_by_one = not skills
        if one_by_one:
            if len(folders) > ONE_BY_ONE_MAX:
                raise _fail("no_index")
            skills, skipped = await self._one_by_one(entry, sha, folders, extra, go)
        stored = RepoList(sha, info.licence, at, "", used, skipped, skills, one_by_one)
        if self._put(entry.id, stored) and (old is None or old.skills != skills):
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

    async def _from_index(self, entry, sha, index, extra, folders, go) -> tuple[list[Listed], int]:
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
            k.has_extra = extra.get(k.path, False)
        return listed, skipped

    async def _one_by_one(self, entry, sha, folders, extra, go) -> tuple[list[Listed], int]:
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
            skills.append(Listed(path, meta["name"], meta["description"], "", extra.get(path, False)))
        return skills, skipped
