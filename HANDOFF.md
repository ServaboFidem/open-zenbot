# Handoff: Events calendar on `ui-enhancements`

Written 2026-11-17, at head `d7ecb29`. Delete this file before the branch is merged.

## What is on this branch

1. A fix for the chat heading wrapping badly in a narrow window (`77578ff`).
2. Piece 1 of 5 of the Events area: a built-in calendar the harness owns, an Events page, a Calendar
   settings panel, and exchange with Google Calendar (pull, and push once switched on). The built-in
   calendar is the master; Google is only pulled from and pushed to. How it works is documented in
   `AGENTS.md`, section "The calendar and Google".

Pieces 2 to 5 are not designed or built: finding local events (Discover), adding a found event to the
calendar, travel-aware location, and music-matched local shows.

## Where it stopped

- All 11 planned tasks are built, each reviewed on its own, with fix rounds. Done.
- A final whole-branch review ran at `f340de8`: no critical findings, 11 important, 10 minor.
- One fix pass followed, in four commits (`020721a`, `6c9fc30`, `b81122b`, `d7ecb29`).
  `uv run pytest -q`: 1925 passed, 15 skipped. `npm test`: 417 passing. `npm run build`: clean.
- The fix pass (`f340de8..d7ecb29`) was then re-checked on a second computer, against the eight points
  below. Two defects were fixed, in the commit that follows `b0f39ec`:
  - Point 3: a sign-in refused while an account is linked no longer revokes the new token (unless it is
    another account's), so a failed write upgrade cannot end the working connection.
  - Point 5: an acknowledgement that reached the harness but whose answer was lost left the page thinking
    that count was still unacknowledged, so the next lost edit was acknowledged without a notice.
  The other six points held. One small thing was left as it is: a full read answered 200 with a body that
  is not a calendar listing counts as an empty listing, so `keep_pulled` drops that calendar's clean pulled
  events until the next run reads them again.
- Next: the manual check against real Google (below), then the owner's decisions.

## What the re-check looked at

New behaviour from the fix pass:

1. `CalendarStore.keep_pulled`: after a full read, clean pulled rows Google no longer lists are dropped.
   Confirm it can never drop a dirty or deleted-here row, a made-here row, a row on another calendar, or
   anything when the read failed or ended part-way.
2. Linking a pulled event to a made-here one by its id shape, and the clean-up in `mark_pushed`: confirm
   neither can link or delete the wrong row.
3. A sign-in refused while already connected (write access unticked, a read permission missing, a
   different account) revokes the newly issued token. If Google treats that as ending the whole grant,
   the working connection dies. Skipping the revoke while a link exists may be the safer behaviour.
4. Every `/calendar` POST now needs a JSON body: confirm every call in `src/gui/calendar-api.js` sends one.
5. The lost-edit notice in `src/gui/events/CalendarView.jsx`: it must not acknowledge a count it did not
   show, must not loop, and must survive a failed acknowledge. It is not unit-tested or seen in a browser.
6. The saved target calendar for a retried insert: no path may send a second copy to a different calendar.
7. Six existing tests were changed to match new behaviour: confirm none was loosened.
8. `AGENTS.md` still says nothing false about the code.

## Decisions waiting for the owner

- **The harness API has no authentication.** A bot with `shell` or the `browser` server can call the
  `/calendar` routes: read events, or create one that push then sends to Google. This is true of every
  harness route already, but automatic push is the first feature to rely on "only the user makes events
  here". Only the docs were corrected. A real fix is harness-wide (for example a per-launch token the GUI
  holds).
- A pushed event whose Google calendar later becomes read-only is retried every sync and refused each
  time. It keeps its "Not yet on Google" label. No cap on retries was added.
- A sign-in that fails in the browser (cancelled, wrong account) leaves the settings panel waiting until
  Cancel is pressed or 10 minutes pass; the harness does not report the outcome.

## Never checked against the real thing

- **Google.** The whole exchange has only run against the test fake (`tests/events/conftest.py`
  `FakeGoogle`). `AGENTS.md` lists the assumptions to check by hand. The two most likely to bite:
  whether a Desktop-app client accepts the redirect `http://127.0.0.1:8765/calendar/google/callback`, and
  whether sync tokens work with `singleEvents=true` the way the code expects.
- **The connected screens.** In a browser, only the states reachable without Google were seen: the Events
  page with made-here events (add, edit, delete, narrow widths), and the settings panel up to Connect
  opening Google's sign-in. The connected and needs-reconnect states, the calendar list, the push switch,
  Refresh, and a pulled event's details were read in code only.

Manual check to run with a real Google Desktop-app client: paste the credentials, connect, tick a calendar
and see its events arrive, switch push on, make an event here and see it on Google, edit it, delete it,
switch an event between all-day and timed, delete one on Google and sync.

## Decisions made during the build

Each was made without asking, to keep the work moving. Rework any that are wrong.

- A full re-read forced by Google (its sync token expired) removes only pulled events with no unsent
  change; unsent edits and deletions stay queued. Cost if wrong: a stale edited event lingers until pushed.
- Unselecting a calendar removes all its pulled events, including unsent changes to them (the
  confirmation says so). Cost if wrong: a silently lost unsent edit.
- A token answer from Google that does not say which permissions were granted is a failed sign-in.
  Cost if wrong: a rare false failure.
- An edit that lands while that event is being sent stays queued and is sent next time.
- An unsent change to an event on a calendar that cannot be written is discarded and Google's copy shows
  again, rather than the event staying hidden.
- An event re-created on Google after being deleted there counts as "added"; the preview calls it an
  update. Cosmetic.
- A sync where some events were refused still counts as a successful sync; each refused event shows "Not
  yet on Google". Cost if wrong: a slightly optimistic "Synced" line.
- Editing an event keeps its own time zone; the computer's zone is used only for display and new events.
- A pulled event that Google deleted while it had an unsent edit here is removed and counted as a lost
  edit; the count stays until the Events page has shown it.
- Each calendar is read in full once a week, so events entering the one-year window appear.
- Pull stores Google's text unshortened; an over-long title is refused only when edited here.
- The settings panel has a Disconnect button in the needs-reconnect state, waits up to 10 minutes for a
  sign-in, and offers only writable Google calendars as the push destination.
- `tests/test_provider_registry.py` exempts `src/events/`, as it does `src/music/`: the Google client is
  not a model provider.
- Zero-length events from Google are stored as they are.
- Not fixed from the final review: the sign-in outcome is not reported to the panel, and per-event push
  retries have no cap.

## Not on this branch

These are git-ignored and exist only on the computer the work was done on:

- The design: `docs/superpowers/specs/2026-11-17-events-calendar-design.md`
- The build plan: `docs/superpowers/plans/2026-11-17-events-calendar.md`
- The build ledger, task reports, the final review and the fix report:
  `.superpowers/sdd/2026-11-17-events-calendar/`
- The mockup the page was built from: `research/events-handoff/`

Copy them across by hand if they are wanted for pieces 2 to 5.

## Running it

`uv sync --group dev`, then `uv run python -m src.harness.main`; `npm run build` for the GUI. The calendar
needs nothing until Google is connected in Settings > Calendar. `ruff` was not installed, so lint was
never run on the new files.
