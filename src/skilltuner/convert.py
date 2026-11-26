"""SkillTuner's conversion: cleaning the model's two answers and building the skill folder's files.

The downloaded body and the model's answers are untrusted text. The frontmatter is written here from a dict of our
own (`yaml.safe_dump`), the downloaded body goes below a provenance note and is never read as frontmatter, and the
result must pass the loader's `parse_skill` and `SkillConfig.max_bytes`. Nothing here logs or keeps a text in an error:
`build_skill` raises `ValueError` with only a reason (`licence`, `invalid`, `too_large`)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from src.skills.loader import SkillConfig, parse_skill
from src.skilltuner.casemark import SLUG, Downloaded

MAX_NEEDS = 8
TOPIC_MAX = 200
WORDS_MAX_WORDS = 6
WORDS_MAX = 80
NAME_MAX = 80
USE_MAX = 300
INPUTS_MAX = 8
INPUT_MAX = 200
RETURN_MAX = 300
OWN_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

LICENCES = frozenset({"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0"})
SOURCE = "agentskills.finance"
SOURCE_URL = "https://agentskills.finance/skills/"
_BAD = ("Cc", "Zl", "Zp", "Cs")


def _plain(value: object, limit: int) -> str | None:
    """A trimmed single-line string with no control character, or None when it fails or is empty or over `limit`."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or any(unicodedata.category(c) in _BAD for c in text):
        return None
    return text


def _line(value: object) -> str:
    """Any text flattened to one line (for the note and the notice, which carry downloaded text)."""
    text = "".join(" " if unicodedata.category(c) in _BAD or c.isspace() else c for c in str(value))
    return " ".join(text.split())


def clean_needs(raw: Any) -> list[tuple[str, str]]:
    """(topic, words) pairs from the model's answer, at most 8; an entry failing a cap is dropped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("needs"), list):
        raise ValueError("needs must be a list")
    out: list[tuple[str, str]] = []
    for item in raw["needs"]:
        if not isinstance(item, dict):
            continue
        topic = _plain(item.get("topic"), TOPIC_MAX)
        words = _plain(item.get("words"), WORDS_MAX)
        if topic is None or words is None or len(words.split()) > WORDS_MAX_WORDS:
            continue
        out.append((topic, words))
        if len(out) == MAX_NEEDS:
            break
    return out


def clean_conversion(raw: Any, taken: set[str]) -> dict[str, Any]:
    """The new frontmatter fields (name, slug, use, inputs?, return?); any failure is a ValueError.

    `taken` holds slugs and case-folded names already in the catalog."""
    if not isinstance(raw, dict):
        raise ValueError("conversion must be an object")
    name = _plain(raw.get("name"), NAME_MAX)
    use = _plain(raw.get("use"), USE_MAX)
    slug = raw.get("slug")
    if name is None or use is None or not isinstance(slug, str) or not OWN_SLUG.fullmatch(slug):
        raise ValueError("conversion fields are not usable")
    if slug in taken or name.casefold() in taken:
        raise ValueError("name or slug is taken")
    out: dict[str, Any] = {"name": name, "slug": slug, "use": use}
    if raw.get("inputs") is not None:
        items = raw["inputs"]
        if not isinstance(items, list) or len(items) > INPUTS_MAX:
            raise ValueError("inputs are not usable")
        cleaned = [_plain(i, INPUT_MAX) for i in items]
        if any(i is None for i in cleaned):
            raise ValueError("inputs are not usable")
        if cleaned:
            out["inputs"] = cleaned
    if raw.get("return") is not None:
        ret = _plain(raw["return"], RETURN_MAX)
        if ret is None:
            raise ValueError("return is not usable")
        out["return"] = ret
    return out


def licence_ok(spdx: str) -> bool:
    return isinstance(spdx, str) and spdx.strip().casefold() in LICENCES


@dataclass
class BuiltSkill:
    slug: str
    files: dict[str, str]  # relative path -> text


def _apache_text() -> str:
    return resources.files("src.skilltuner").joinpath("apache-2.0.txt").read_text(encoding="utf-8")


def build_skill(d: Downloaded, conv: dict, references: list[tuple[str, str]], today: str,
                config: SkillConfig) -> BuiltSkill:
    """The skill folder's files for a downloaded skill; ValueError(reason) with `licence`, `invalid` or `too_large`."""
    if not licence_ok(d.license):
        raise ValueError("licence")
    if not SLUG.fullmatch(d.slug or ""):
        raise ValueError("invalid")
    licence = _line(d.license)
    url = SOURCE_URL + d.slug
    meta: dict[str, Any] = {"name": conv["name"], "slug": conv["slug"], "use": conv["use"]}
    if conv.get("inputs"):
        meta["inputs"] = list(conv["inputs"])
    if conv.get("return"):
        meta["return"] = conv["return"]
    meta.update(source=SOURCE, source_slug=d.slug, source_version=_line(d.version), source_url=url, license=licence)
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    note = (f"> From {SOURCE} ({d.slug}, version {_line(d.version)}), {licence}. Converted by SkillTuner on "
            f"{_line(today)}: the frontmatter was rewritten; the text below is as published.")
    body = note + "\n\n" + d.body
    for path, text in references:
        body += f"\n\n## Reference: {path}\n\n{text}"
    skill_md = f"---\n{head}---\n\n{body}"
    try:
        parsed = parse_skill(skill_md)
    except ValueError:
        raise ValueError("invalid") from None
    if parsed.name != conv["name"] or parsed.use != conv["use"] or parsed.slug != conv["slug"]:
        raise ValueError("invalid")
    if len(skill_md.encode("utf-8")) > config.max_bytes:
        raise ValueError("too_large")
    if licence.casefold() == "apache-2.0":
        licence_txt = _apache_text()
    else:
        licence_txt = f"Licensed under {licence} (SPDX id). See {url}"
    notice = (f"This skill is from {SOURCE} ({url}), by {_line(d.author)}, under {licence}. "
              f"Modified by SkillTuner on {_line(today)}: frontmatter rewritten.")
    return BuiltSkill(conv["slug"], {"SKILL.md": skill_md, "LICENSE.txt": licence_txt, "NOTICE.txt": notice})
