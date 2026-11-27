"""Suggestions waiting for the owner's approval, kept in the private `<vault>-skilltuner/` folder.

One folder a suggestion: `<root>/<id>/record.json` and `<root>/<id>/skill/<files>`. A suggestion is written
whole into `.tmp-<id>/` and renamed into place, so a crash never leaves a half one that `list()` shows.
Nothing is read or written through a link."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from collections.abc import Callable
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path

from src.skilltuner.convert import BuiltSkill

ID_RE = re.compile(r"[0-9a-f]{32}")
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._ -]{0,99}")
MAX_RECORD = 2_000_000
MAX_FILE = 2_000_000


@dataclass
class Suggestion:
    id: str
    slug: str
    source_slug: str
    name: str
    use: str
    topic: str
    score: float
    license: str
    source_url: str
    original: str
    converted: str
    at: str
    source: str = "agentskills.finance"  # the library the skill came from, as the file's `source` says
    source_name: str = ""  # the downloaded skill's own name (the reject route remembers it)
    left_out: bool = False  # the skill came with scripts or other files that were not brought over


_STR = {f.name for f in fields(Suggestion) if f.name not in ("score", "left_out")}
_REQUIRED = {f.name for f in fields(Suggestion) if f.default is MISSING}


def _kind(p: Path) -> int | None:
    """The file type of `p` itself (a link is not followed), or None when it is not there."""
    try:
        return stat.S_IFMT(p.lstat().st_mode)
    except OSError:
        return None


def _is_reparse(p: Path) -> bool:
    """A Windows reparse point (a junction or a link), told apart from a plain folder."""
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _is_dir(p: Path) -> bool:
    return _kind(p) == stat.S_IFDIR


def _is_file(p: Path) -> bool:
    return _kind(p) == stat.S_IFREG


def _read(p: Path, limit: int) -> str | None:
    if not _is_file(p):
        return None
    try:
        with open(p, "rb") as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load(raw: str, folder_id: str) -> Suggestion | None:
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict) or not _REQUIRED <= set(d) <= {f.name for f in fields(Suggestion)}:
        return None  # an older record may lack the fields that have defaults
    if any(not isinstance(d[k], str) for k in _STR if k in d) or not isinstance(d.get("left_out", False), bool):
        return None
    if isinstance(d["score"], bool) or not isinstance(d["score"], (int, float)):
        return None
    if d["id"] != folder_id:
        return None
    return Suggestion(**d)


class SuggestionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _root_ok(self) -> bool:
        return _is_dir(self.root)

    def _folder(self, id: str) -> Path | None:
        if not isinstance(id, str) or not ID_RE.fullmatch(id) or not self._root_ok():
            return None
        p = self.root / id
        return p if _is_dir(p) else None

    def add(self, s: Suggestion, built: BuiltSkill) -> None:
        if not ID_RE.fullmatch(s.id or ""):
            raise ValueError("a suggestion id is 32 lowercase hex characters")
        for name in built.files:
            if not FILE_NAME.fullmatch(name) or name in (".", ".."):
                raise ValueError("a skill file name must be one plain name")
        if _kind(self.root) is not None and not self._root_ok():
            raise OSError("the suggestions folder is not a real folder")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self._root_ok():
            raise OSError("the suggestions folder is not a real folder")
        final = self.root / s.id
        if _kind(final) is not None:
            raise FileExistsError("that suggestion already exists")
        tmp = self.root / f".tmp-{s.id}"
        if _kind(tmp) is not None:
            if _is_dir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                tmp.unlink()
        tmp.mkdir()
        try:
            skill = tmp / "skill"
            skill.mkdir()
            for name, text in built.files.items():
                with open(skill / name, "x", encoding="utf-8", newline="") as f:
                    f.write(text)
            rec = tmp / "record.json.part"
            rec.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(rec, tmp / "record.json")
            os.rename(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def get(self, id: str) -> Suggestion | None:
        folder = self._folder(id)
        if folder is None:
            return None
        raw = _read(folder / "record.json", MAX_RECORD)
        return _load(raw, id) if raw is not None else None

    def list(self) -> list[Suggestion]:
        if not self._root_ok():
            return []
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out = []
        for n in names:
            if ID_RE.fullmatch(n):
                try:
                    s = self.get(n)
                except Exception:
                    s = None
                if s is not None:
                    out.append(s)
        out.sort(key=lambda s: (s.at, s.id), reverse=True)
        return out

    def count(self) -> int:
        return len(self.list())

    def files(self, id: str) -> dict[str, str]:
        folder = self._folder(id)
        skill = folder / "skill" if folder else None
        if skill is None or not _is_dir(skill):
            return {}
        out = {}
        try:
            names = sorted(os.listdir(skill))
        except OSError:
            return {}
        for n in names:
            text = _read(skill / n, MAX_FILE)
            if text is not None and FILE_NAME.fullmatch(n):
                out[n] = text
        return out

    def sweep_temp(self, on_error: Callable[[str], None] | None = None) -> None:
        """Remove the `.tmp-*` staging folders a crash left behind; a link or junction is removed itself, never
        followed. Run once, at harness start, before any pass can be writing one. `on_error` gets a failure's
        exception class name."""
        if not self._root_ok():
            return
        try:
            names = [n for n in os.listdir(self.root) if n.startswith(".tmp-")]
        except OSError as e:
            if on_error:
                on_error(type(e).__name__)
            return
        for n in names:
            p = self.root / n
            try:
                if _is_reparse(p):
                    if _kind(p) == stat.S_IFDIR:
                        os.rmdir(p)  # a junction: removes the link only
                    else:
                        p.unlink()
                elif _is_dir(p):
                    shutil.rmtree(p)
                else:
                    p.unlink()  # a file or a symbolic link, never its target
            except OSError as e:
                if on_error:
                    on_error(type(e).__name__)

    def remove(self, id: str) -> bool:
        folder = self._folder(id)
        if folder is None:
            return False
        try:
            shutil.rmtree(folder)
        except OSError:
            return False
        return not _kind(folder)
