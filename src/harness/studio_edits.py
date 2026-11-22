"""The user's own changes to a design, saved by the harness as one new numbered version: texts, pictures and
tweak values, with no bot in between. Also the copy taken of a working file the Designer changed without
attaching it, and the list of what may be edited. The routes record the exchange for each copy made here.

Everything is checked before anything is written; the working file is written whole and a numbered copy
taken, and a copy that cannot be taken puts the working file back (the order of `studio_helpers.apply`).
A user's text reaches the file only through `design_parts.check_text` and `design_parts.apply`, and a tweak
value only as `design_tweaks.fmt` formats it. Plain functions, no `await`: a route calls them between its
first check and its last write."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import design_images, design_parts, design_studio, design_tweaks
from .design_parts import Change, PartRefused
from .studio_helpers import _read_source, _replace, _too_large, design_dir, newest_revision

MAX_TEXTS = 50
MAX_IMAGES = 10
CHANGED = "The design has changed. Reload and try again."
SHOW_AGAIN = "Ask the Designer to show the design again."
TOO_LARGE = design_images.TOO_LARGE  # "the design would be too large"
NOT_SAVED = "The design could not be saved."
NOT_PICTURE = "A picture must be a PNG, JPEG, WebP or GIF file."
NOT_TEXT = "That is not a text of this design that can be edited."
NOT_IMAGE = "That is not a picture of this design that can be changed."
NOT_TWEAK = "That is not a tweak of this design."


class EditRefused(Exception):
    """A change that was not saved: `status` 409 (the design or its state is not as expected) or 422 (the
    change itself is refused), and the sentence the user is shown (never a disk path or typed text)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Picture:
    name: str  # the file name the user chose; only a stem is taken from it
    data: bytes
    alt: str | None  # None keeps the alt the picture has


@dataclass
class Made:
    copy: Path  # the new numbered copy, for the routes to attach
    version: int
    texts: list[str]  # the ids changed
    images: list[str]
    tweaks: list[str]


@dataclass
class _Design:
    folder: Path
    live: Path
    version: int
    raw: bytes  # the working file as read
    source: str


def _design(vault_root: Path, conv: Any) -> tuple[Path, Path, Path, int] | None:
    """(folder, working file, newest copy, its version) of the conversation's newest design."""
    found = newest_revision(vault_root, conv)
    if found is None:
        return None
    att, (slug, version, name) = found
    folder = design_dir(vault_root, slug)
    if folder is None:
        return None
    return folder, folder / name, Path(att["path"]), version


def _current(vault_root: Path, conv: Any) -> _Design:
    """The newest design, read, when the working file is still its newest copy; else EditRefused."""
    found = _design(vault_root, conv)
    if found is None:
        raise EditRefused(409, SHOW_AGAIN)
    folder, live, copy, version = found
    raw = _read_source(live)
    if raw is None or not design_studio.is_current(vault_root, live, copy) or _read_source(live) != raw:
        raise EditRefused(409, CHANGED)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise EditRefused(409, SHOW_AGAIN) from None
    return _Design(folder, live, version, raw, source)


def catch_up(vault_root: Path, conv: Any) -> Path | None:
    """When the working file is not the newest copy (the Designer changed it and was stopped before
    attaching), take a numbered copy of it as it is and return it; None when it is current."""
    found = _design(vault_root, conv)
    if found is None:
        raise EditRefused(409, SHOW_AGAIN)
    _folder, live, newest, _version = found
    if _read_source(live) is None:  # gone, a link or not a file
        raise EditRefused(409, SHOW_AGAIN)
    if design_studio.is_current(vault_root, live, newest):
        return None
    try:
        copy = design_studio.snapshot(vault_root, live)
    except (design_studio.SnapshotRefused, OSError):
        copy = None
    if copy is None or design_studio.revision_of(vault_root, copy) is None:
        raise EditRefused(409, SHOW_AGAIN)
    return copy


def slots_view(vault_root: Path, conv: Any) -> dict:
    """What the pane may offer to edit on the newest version. Texts as the worksheet shows them (a newline
    for each `<br>`); a picture's `name` is its file under `images/`, empty while it is still embedded."""
    d = _current(vault_root, conv)
    found = design_parts.slots(d.source, d.folder)
    prefix = design_images.IMAGES + "/"
    return {
        "version": d.version,
        "texts": [{"id": s.id, "text": s.text} for s in found.values() if s.kind == "text"],
        "images": [{"id": s.id, "name": s.src[len(prefix):] if s.src.startswith(prefix) else "", "alt": s.text}
                   for s in found.values() if s.kind == "images"],
        "tweaks": design_tweaks.view(design_tweaks.knobs(d.source)),
    }


def _limits(texts: dict[str, str], images: dict[str, Picture], tweaks: dict[str, object]) -> None:
    if len(texts) > MAX_TEXTS:
        raise EditRefused(422, f"At most {MAX_TEXTS} texts can be saved at once.")
    if len(images) > MAX_IMAGES:
        raise EditRefused(422, f"At most {MAX_IMAGES} pictures can be saved at once.")
    if len(tweaks) > design_tweaks.MAX_KNOBS:
        raise EditRefused(422, f"At most {design_tweaks.MAX_KNOBS} tweaks can be saved at once.")
    for pic in images.values():
        if not isinstance(pic.data, bytes) or len(pic.data) > design_images.PICTURE_MAX_BYTES:
            raise EditRefused(422, f"A picture can be at most {design_images.PICTURE_MAX_BYTES:,} bytes.")


def _flat(s: str) -> str:
    return " ".join(s.split())


def _stem(pic: Picture, sid: str) -> str:
    """The name a picture is saved under: the user's file name, or the slot's id when that has no letter or
    digit `design_images` would keep."""
    name = pic.name if isinstance(pic.name, str) else ""
    return name if re.search(r"[A-Za-z0-9]", Path(name).stem) else sid


def save(vault_root: Path, conv: Any, version: int, texts: dict[str, str], images: dict[str, Picture],
         tweaks: dict[str, object]) -> Made | None:
    """Save the user's texts, pictures and tweak values on `version`, the newest copy, as one new numbered
    copy. None when nothing changed. Nothing is written unless every change passes; a picture already
    written into `images/` may stay when a later step fails, as with the helpers."""
    _limits(texts, images, tweaks)
    d = _current(vault_root, conv)
    if d.version != version:
        raise EditRefused(409, CHANGED)
    slots = design_parts.slots(d.source, d.folder)

    # every check before any write
    changes: dict[str, Change] = {}
    for sid, text in texts.items():
        slot = slots.get(sid)
        if slot is None or slot.kind != "text":
            raise EditRefused(422, NOT_TEXT)
        if not isinstance(text, str):
            raise EditRefused(422, "That text cannot be saved.")
        try:
            checked = design_parts.check_text(text)
        except PartRefused as e:
            raise EditRefused(422, f"That text cannot be saved: {e}.") from None
        if checked != slot.text:  # the same words as now are no change
            changes[sid] = Change(text=checked)
    knobs = {k.var: k for k in design_tweaks.knobs(d.source)}
    values: dict[str, object] = {}
    for var, value in tweaks.items():
        knob = knobs.get(var)
        if knob is None:
            raise EditRefused(422, NOT_TWEAK)
        try:
            formatted = design_tweaks.fmt(knob, value)
        except PartRefused as e:
            raise EditRefused(422, str(e)) from None
        if formatted != design_tweaks.fmt(knob, knob.value if knob.type == "color" else float(knob.value)):
            values[var] = value  # the value it has now, however it is written, is no change
    for sid, pic in images.items():
        slot = slots.get(sid)
        if slot is None or slot.kind != "images":
            raise EditRefused(422, NOT_IMAGE)
        if design_images.sniff(pic.data) is None:
            raise EditRefused(422, NOT_PICTURE)
    if images:  # each alt, and the slots' src and alt spans, as the real apply will meet them
        try:
            design_parts.apply(d.source, slots, {sid: Change(src=f"{design_images.IMAGES}/check.png", alt=pic.alt)
                                                 for sid, pic in images.items()})
        except PartRefused as e:
            raise EditRefused(422, f"That picture cannot be saved: {e}.") from None

    for sid, pic in images.items():
        try:
            ref = design_images.write_out(d.folder, pic.data, _stem(pic, sid))
        except design_images.ImageRefused as e:
            raise EditRefused(409, f"That picture cannot be saved: {e}.") from None
        slot = slots[sid]
        change = Change(src=ref if ref != slot.src else None,
                        alt=pic.alt if pic.alt is not None and _flat(pic.alt) != slot.text else None)
        if change.src is not None or change.alt is not None:
            changes[sid] = change
    if not changes and not values:
        return None

    try:
        html = design_parts.apply(d.source, slots, changes)
        # the text splice moved every offset after it: read the tweak spans again from its result
        html = design_tweaks.apply(html, design_tweaks.knobs(html), values)
    except PartRefused as e:
        raise EditRefused(422, f"That change cannot be saved: {e}.") from None
    new = html.encode("utf-8")
    if new == d.raw:
        return None
    if _too_large(new, d.folder):
        raise EditRefused(422, TOO_LARGE)
    made = _write_version(vault_root, d, new)
    if made is None:
        return None
    copy, n = made
    return Made(copy, n,
                texts=[i for i in changes if slots[i].kind == "text"],
                images=[i for i in changes if slots[i].kind == "images"],
                tweaks=list(values))


def _write_version(vault_root: Path, d: _Design, new: bytes) -> tuple[Path, int] | None:
    """Write `new` over the working file whole and take its numbered copy. A copy that cannot be taken puts
    the working file back. None when the page renders as the version it was read from already does."""
    if _read_source(d.live) != d.raw:
        raise EditRefused(409, CHANGED)
    try:
        _replace(d.live, new)
    except OSError:
        raise EditRefused(409, NOT_SAVED) from None
    try:
        copy = design_studio.snapshot(vault_root, d.live)
        rev = design_studio.revision_of(vault_root, copy) if copy is not None else None
        if rev is None:
            raise design_studio.SnapshotRefused("not a design file")
    except (design_studio.SnapshotRefused, OSError):
        try:
            _replace(d.live, d.raw)
        except OSError:
            pass  # the working file keeps the change with no copy: the next request catches it up as a version
        raise EditRefused(409, NOT_SAVED) from None
    if rev[1] == d.version:
        # the page renders as the version already does (a `data:` picture written out with the same bytes)
        return None
    return copy, rev[1]
