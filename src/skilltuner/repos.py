"""GitHub repositories SkillTuner may read skills from: the stored entry and the address parser.

The address is a security boundary: only the owner, repo, ref and folder this module returns are used afterwards, and
every request address is rebuilt from them, never taken from what the owner pasted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
REPO = r"[A-Za-z0-9._-]{1,100}"
SEGMENT = r"[A-Za-z0-9._-]{1,100}"
ADDRESS_MAX = 500
INDEX_MAX = 200
BAD_ADDRESS = ("Paste a GitHub address like https://github.com/owner/repo or "
               "https://github.com/owner/repo/tree/main/skills.")
BAD_INDEX = "Give a plain path inside the repository, like data/skills_index.json."


def _control(s: str) -> bool:
    return any(ord(c) < 32 or 127 <= ord(c) < 160 for c in s)


def _segment(s: str) -> bool:
    return re.fullmatch(SEGMENT, s) is not None and s not in (".", "..")


@dataclass
class RepoEntry:
    id: str
    owner: str
    repo: str
    ref: str
    folder: str
    index: str
    licence: str

    def address(self) -> str:
        base = f"https://github.com/{self.owner}/{self.repo}"
        if not self.ref:
            return base
        return f"{base}/tree/{self.ref}" + (f"/{self.folder}" if self.folder else "")

    def label(self) -> str:
        return f"github.com/{self.owner}/{self.repo}"


def parse_address(text: str) -> tuple[str, str, str, str]:
    """(owner, repo, ref, folder) of a github.com address, else `ValueError(BAD_ADDRESS)`."""
    try:
        if not isinstance(text, str) or len(text) > ADDRESS_MAX or any(c in text for c in "%\\") or _control(text):
            raise ValueError
        u = urlsplit(text.strip())
        if (u.scheme != "https" or u.hostname != "github.com" or u.username is not None
                or u.password is not None or u.port is not None or u.netloc != u.hostname
                or u.query or u.fragment):
            raise ValueError
        parts = u.path.strip("/").split("/")
        owner, repo = parts[0], parts[1] if len(parts) > 1 else ""
        if repo.endswith(".git"):
            repo = repo[:-4]
        if re.fullmatch(OWNER, owner) is None or re.fullmatch(REPO, repo) is None or repo in (".", ".."):
            raise ValueError
        rest = parts[2:]
        if not rest:
            return owner, repo, "", ""
        if rest[0] != "tree" or len(rest) < 2 or not all(_segment(s) for s in rest[1:]):
            raise ValueError
        return owner, repo, rest[1], "/".join(rest[2:])
    except ValueError:
        raise ValueError(BAD_ADDRESS) from None


def parse_index_path(text: str) -> str:
    """A plain relative path inside the repository, or "" for none."""
    if not isinstance(text, str) or len(text) > INDEX_MAX or _control(text):
        raise ValueError(BAD_INDEX)
    t = text.strip()
    if t and not all(_segment(s) for s in t.split("/")):
        raise ValueError(BAD_INDEX)
    return t
