"""Update and cancel Facebook calendar holds when pool rows change.

Each hold tracks its source pool row by the pool event id: callers pass the
row as the pool last read it (never a card snapshot — cards are snapshots, so
pool updates propagate to holds here, not from a card's older copy).

Lifecycle follows calendar sync:

- Time or place change updates the hold, through the shared lock and builder
  (:data:`src.events.calendar_add.add_lock`,
  :func:`src.events.calendar_add.listing_times`). The harness never turns a
  wall-clock time into an instant, so a timed update needs the fresh instants
  (the page's, as the add routes take); without them the hold's own instants
  are reused, which covers place-only changes. When the wall time moved beyond
  what the hold's instants still match, the hold is removed rather than left
  stale; the approval card stays, and re-add through the existing add routes
  makes a fresh hold.
- Cancel (the row is gone from the pool, or carries a cancelled marker) or
  un-RSVP (the row no longer holds: the tap was removed, liked, or is no
  longer personal) removes the hold per dirty and tombstone rules, through
  :meth:`src.events.store.CalendarStore.delete`: a hold with a Google copy
  becomes a tombstone (deleted, dirty) for push to send, one without is
  purged. Google push follows locked and kick rules unchanged: ``kick`` is
  called once after any write so the service sends it.

Results carry ids, reasons and counts only, never listing text; failures are
logged by exception class name only.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import HTTPException

from src.events import calendar_add
from src.events.facebook_cards import classify
from src.events.facebook_pool import SOURCE
from src.events.store import EventError

log = logging.getLogger(__name__)

__all__ = [
    "CANCELLED",
    "STALE_TIMING",
    "UN_RSVP",
    "hold_fields",
    "hold_text",
    "live_holds",
    "removal_reason",
    "sync_hold",
    "wants_hold",
]

#: The source row is gone or carries a cancelled marker.
CANCELLED = "cancelled"
#: The wall time moved beyond what the hold's instants still match.
STALE_TIMING = "stale_timing"
#: The row is still here but no longer holds (tap removed, liked, not personal).
UN_RSVP = "un_rsvp"

_FRIENDLY_CANCEL_KEYS = ("is_cancelled", "cancelled")


def wants_hold(row: dict[str, Any], people: Any = None) -> bool:
    """Whether ``row`` still waits as an approval card (RSVP or personal)."""
    return isinstance(row, dict) and classify(row, people)[0] == "approval"


def _cancelled(row: dict[str, Any]) -> bool:
    if any(row.get(key) for key in _FRIENDLY_CANCEL_KEYS):
        return True
    return row.get("status") == "cancelled"


def removal_reason(row: Any, people: Any = None) -> str:
    """Why ``row`` no longer holds: ``""`` when it still wants a hold."""
    if row is None:
        return CANCELLED
    if not isinstance(row, dict):
        raise ValueError("not a pool row")
    if row.get("source") != SOURCE:
        raise ValueError("not a Facebook event row")
    if _cancelled(row):
        return CANCELLED
    if not wants_hold(row, people):
        return UN_RSVP
    return ""


def live_holds(calendar_store: Any, calendar_ids: list[str] | None) -> list[dict]:
    """The calendar events in ``calendar_ids`` that still exist, not deleted."""
    out: list[dict] = []
    for cid in calendar_ids or []:
        try:
            event = calendar_store.event(cid)
        except Exception as e:  # noqa: BLE001 - a lookup never fails a sync
            log.warning("facebook hold lookup failed: %s", type(e).__name__)
            continue
        if event is not None and not event.get("deleted"):
            out.append(event)
    return out


def hold_text(row: dict[str, Any]) -> tuple[str, str, str]:
    """The ``(title, location, description)`` for ``row`` (add-route shape)."""
    place = ", ".join(p for p in (row.get("venue", ""), row.get("address", "")) if p)
    summary = row.get("summary", "") or ""
    url = row.get("url", "") or ""
    return row.get("name", ""), place, f"{summary}\n\n{url}" if summary else url


def _next_day(day: str) -> str | None:
    from datetime import date

    try:
        return date.fromordinal(date.fromisoformat(day).toordinal() + 1).isoformat()
    except ValueError:
        return None


def hold_fields(row: dict[str, Any], body: Any, *, today: str, now: str) -> dict | None:
    """The :meth:`CalendarStore.update` fields for ``row`` at ``body``'s instants.

    The shared builder's past and timing rules: None when the listing already
    happened (history is left alone), ``HTTPException`` 422 when the instants
    are not the listing's — the same sentences the add routes answer.
    """
    if (row.get("end_date") or row.get("start_date")) < today:
        return None
    try:
        timing = calendar_add.listing_times(row, body.start, body.end)
        if not timing["all_day"] and timing["end"] <= now:
            return None
    except EventError as e:
        raise HTTPException(422, str(e)) from None
    title, location, description = hold_text(row)
    return {"title": title, "location": location, "description": description, **timing}


def _same(hold: dict, fields: dict) -> bool:
    return all(hold.get(k) == v for k, v in fields.items())


def _remove(
    calendar_store: Any, holds: list[dict], reason: str, event_id: str
) -> dict[str, Any]:
    removed: list[str] = []
    tombstoned: list[str] = []
    errors = 0
    for hold in holds:
        try:
            linked = bool(hold.get("google_id"))
            calendar_store.delete(hold["id"])
        except Exception as e:  # noqa: BLE001 - one hold never blocks the rest
            errors += 1
            log.warning("facebook hold not removed: %s", type(e).__name__)
            continue
        removed.append(hold["id"])
        if linked:
            tombstoned.append(hold["id"])
    return {
        "status": "removed",
        "event_id": event_id,
        "reason": reason,
        "updated": [],
        "removed": removed,
        "tombstoned": tombstoned,
        "errors": errors,
    }


async def sync_hold(
    calendar_store: Any,
    event_id: str,
    calendar_ids: list[str] | None,
    new_row: Any,
    people: Any = None,
    *,
    body: Any = None,
    today: str,
    now: str,
    kick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Bring the holds for pool event ``event_id`` up to date with ``new_row``.

    ``new_row`` is the pool row as last read (None when the pool no longer
    lists it); ``calendar_ids`` are the calendar events made from it. ``body``
    carries fresh ``start``/``end`` instants for a timed change (the page's,
    as the add routes take); without one each hold keeps its own instants, so
    only place and text changes apply and a moved wall time removes the hold
    as stale instead. ``kick`` runs once after any write so push sends it.

    Answers ``updated`` (holds rewritten), ``unchanged``, ``removed``
    (``reason`` one of ``cancelled``, ``un_rsvp``, ``stale_timing``),
    ``missing`` (still wanted but no live hold: creation belongs to the add
    routes) or ``past`` (already happened: history left alone).
    """
    if new_row is not None and not isinstance(new_row, dict):
        raise ValueError("not a pool row")
    async with calendar_add.add_lock:
        live = live_holds(calendar_store, calendar_ids)
        reason = removal_reason(new_row, people)
        if reason:
            out = _remove(calendar_store, live, reason, event_id)
            if out["removed"] and kick is not None:
                kick()
            return out
        assert isinstance(new_row, dict)
        if not live:
            return {
                "status": "missing",
                "event_id": event_id,
                "reason": "",
                "updated": [],
                "removed": [],
                "tombstoned": [],
                "errors": 0,
            }
        if (new_row.get("end_date") or new_row.get("start_date")) < today:
            return {
                "status": "past",
                "event_id": event_id,
                "reason": "",
                "updated": [],
                "removed": [],
                "tombstoned": [],
                "errors": 0,
            }
        first = live[0]
        if body is None and not new_row.get("start_time"):
            end = _next_day(new_row.get("start_date", ""))
            effective: Any = (
                SimpleNamespace(start=new_row.get("start_date"), end=end)
                if end is not None
                else SimpleNamespace(start=first["start"], end=first["end"])
            )
        else:
            effective = (
                body
                if body is not None
                else SimpleNamespace(start=first["start"], end=first["end"])
            )
        try:
            fields = hold_fields(new_row, effective, today=today, now=now)
        except HTTPException as e:
            if body is None and e.status_code == 422:
                out = _remove(calendar_store, live, STALE_TIMING, event_id)
                if out["removed"] and kick is not None:
                    kick()
                return out
            raise
        if fields is None:
            return {
                "status": "past",
                "event_id": event_id,
                "reason": "",
                "updated": [],
                "removed": [],
                "tombstoned": [],
                "errors": 0,
            }
        updated: list[str] = []
        errors = 0
        for hold in live:
            if _same(hold, fields):
                continue
            try:
                calendar_store.update(hold["id"], fields)
            except Exception as e:  # noqa: BLE001 - one hold never blocks the rest
                errors += 1
                log.warning("facebook hold not updated: %s", type(e).__name__)
                continue
            updated.append(hold["id"])
        if updated and kick is not None:
            kick()
        return {
            "status": "updated" if updated else "unchanged",
            "event_id": event_id,
            "reason": "",
            "updated": updated,
            "removed": [],
            "tombstoned": [],
            "errors": errors,
        }
