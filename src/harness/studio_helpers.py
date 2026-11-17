"""A helper bot's turn on a design another bot owns: the worksheet it reads, and applying the changes it
placed to the design's working file. The harness wires these into a task (`Harness.start_helper`)."""
from __future__ import annotations

import base64
import binascii
import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import design_images, design_parts, design_studio
from .design_parts import Change, PartRefused, Slot

RULES = ("You are helping on a design another bot owns. Change only what the list above names, through the tools. "
         "Say in your reply what you changed.")
CHANGED = "The design changed while I was working, so my changes were not applied."
TOO_LARGE = "The design would be too large to comment on, so my changes were not applied."
ATTACH_REFUSED = ("Not attached: the design is attached for you when you finish. "
                  "Place your changes with the design tools.")


@dataclass
class PartTurn:
    kind: str  # "text" | "images"
    slug: str
    name: str  # the design file's name
    version: int  # the numbered copy the user was looking at
    source: str  # the working file's text when the worksheet was read
    slots: dict[str, Slot]
    sheet: list[str]  # ids on the worksheet
    changes: dict[str, Change] = field(default_factory=dict)


@dataclass
class Applied:
    copy: Path | None  # the new numbered copy, or None when no new copy was made
    refused: str = ""  # for the ledger: why nothing was written
    sentence: str = ""  # for the reply: what the user is told when nothing was written
    kept_before: bool = False  # a refused copy whose working file could not be put back: the kept source stays


def design_dir(vault_root: Path, slug: str) -> Path | None:
    """`<vault>/designs/<slug>`, resolved; None when it is a link, not a folder, or resolves elsewhere."""
    designs = vault_root.resolve() / "designs"
    folder = designs / slug
    try:
        if (designs.is_symlink() or folder.is_symlink() or not folder.is_dir()
                or folder.resolve() != folder or len(folder.relative_to(designs).parts) != 1):
            return None
    except (OSError, ValueError):
        return None
    return folder


def newest_revision(vault_root: Path, conv: Any) -> tuple[dict[str, str], tuple[str, int, str]] | None:
    """The conversation's newest design revision: (its attachment, (slug, version, file name))."""
    return next(((a, rev) for ex in reversed(conv.exchanges) for a in reversed(ex.attachments)
                 if (rev := design_studio.revision_of(vault_root, Path(a["path"])))), None)


def _read_source(live: Path) -> bytes | None:
    try:
        if live.is_symlink() or not live.is_file():
            return None
        return live.read_bytes()
    except OSError:
        return None


def _decode_uri(src: str) -> bytes | None:
    head, _, body = src.partition(",")
    if not head.lower().endswith(";base64"):
        return None
    try:
        data = base64.b64decode(re.sub(r"\s", "", body), validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if len(data) <= design_images.PICTURE_MAX_BYTES and design_images.sniff(data) else None


def _pictures(folder: Path, sheet: list[Slot], all_slots: dict[str, Slot]) -> tuple[list[Slot], dict[str, str]]:
    """Write each picture still embedded as `data:` out to `images/`, and read each picture's size. A picture
    that cannot be written out leaves the worksheet."""
    kept: list[Slot] = []
    sizes: dict[str, str] = {}
    for slot in sheet:
        if slot.src == "data:":
            data = _decode_uri(slot.element.attrs.get("src", ""))
            if data is None:
                continue
            try:
                ref = design_images.write_out(folder, data, slot.id)
            except (design_images.ImageRefused, OSError):
                continue
            slot = dataclasses.replace(slot, src=ref)
            all_slots[slot.id] = slot
        try:
            dims = design_images.dimensions(design_images.load(folder, slot.src[len(design_images.IMAGES) + 1:])[1])
        except design_images.ImageRefused:
            dims = None
        if dims:
            sizes[slot.id] = f"{dims[0]}x{dims[1]} pixels"
        kept.append(slot)
    return kept, sizes


def prepare(vault_root: Path, conv: Any, places: list[str],
            comments: list[dict[str, str]]) -> tuple[PartTurn, str] | None:
    """The turn and its worksheet block for a helper that places `places` in this conversation's newest
    design, or None: no design, a working file that is not the newest copy, or nothing on the worksheet.
    With comments, the worksheet holds the picked elements that are slots; without, every slot of the kind."""
    found = newest_revision(vault_root, conv)
    if found is None:
        return None
    att, (slug, version, name) = found
    folder = design_dir(vault_root, slug)
    if folder is None:
        return None
    live = folder / name
    raw = _read_source(live)
    if raw is None or not design_studio.is_current(vault_root, live, Path(att["path"])) or _read_source(live) != raw:
        return None
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    all_slots = design_parts.slots(source, folder)
    picked = [c.get("element_id", "") for c in comments] if comments else None
    for kind in places:
        sheet = design_parts.worksheet(all_slots, kind, picked)
        sizes: dict[str, str] = {}
        if kind == "images" and sheet:
            sheet, sizes = _pictures(folder, sheet, all_slots)
        if sheet:
            break
    else:
        return None
    turn = PartTurn(kind, slug, name, version, source, all_slots, [s.id for s in sheet])
    return turn, design_parts.block(f"designs/{slug}/{name}", version, sheet, kind, sizes)


def _replace(live: Path, data: bytes) -> None:
    """Write the file whole or not at all: a temporary file beside it, then a rename over it."""
    tmp = live.with_name(f".{live.name}.{uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "xb") as out:
            out.write(data)
        os.replace(tmp, live)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def apply(vault_root: Path, turn: PartTurn) -> Applied:
    """Splice the turn's changes into the working file, keep the source it replaced, and take a numbered
    copy. Writes nothing unless the working file is still the one the worksheet was read from and the copy
    stays within BRIDGE_MAX_BYTES; a failure part-way puts the working file back."""
    folder = design_dir(vault_root, turn.slug)
    if folder is None:
        return Applied(None, "folder", "My changes were not applied: the design folder is not a plain folder.")
    live = folder / turn.name
    raw = _read_source(live)
    if raw is None or raw != turn.source.encode("utf-8"):
        return Applied(None, "changed", CHANGED)
    try:
        new = design_parts.apply(turn.source, turn.slots, turn.changes).encode("utf-8")
    except PartRefused as e:
        return Applied(None, "refused", f"My changes were not applied: {e}.")
    if new == raw:
        return Applied(None, "no change")
    if len(design_images.embed(new, folder)[0]) > design_studio.BRIDGE_MAX_BYTES:
        return Applied(None, "too large", TOO_LARGE)
    try:
        n = design_studio.next_number(vault_root, live)
        before = design_studio.keep_before(vault_root, live, n, raw)
    except (design_studio.SnapshotRefused, OSError):
        return Applied(None, "not kept", "My changes were not applied: the earlier version could not be kept.")
    try:
        _replace(live, new)
    except OSError:
        before.unlink(missing_ok=True)
        return Applied(None, "not written", "My changes were not applied: the design file could not be written.")
    try:
        copy = design_studio.snapshot(vault_root, live)
        if copy is None:
            raise design_studio.SnapshotRefused("not a design file")
    except (design_studio.SnapshotRefused, OSError) as e:
        why = str(e) if isinstance(e, design_studio.SnapshotRefused) else "the copy could not be written"
        try:
            _replace(live, raw)
        except OSError:  # the working file holds the changes: keep the source it replaced
            return Applied(None, "no copy", f"My changes were not applied: {why}.", kept_before=True)
        before.unlink(missing_ok=True)
        return Applied(None, "no copy", f"My changes were not applied: {why}.")
    rev = design_studio.revision_of(vault_root, copy)
    if rev is None or rev[1] != n:
        # the page renders as the newest copy already does (a `data:` picture written out with the same bytes):
        # no new revision, so the kept source belongs to none and nothing is attached
        before.unlink(missing_ok=True)
        return Applied(None, "no change")
    return Applied(copy)
