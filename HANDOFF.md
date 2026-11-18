# Handoff: Events calendar on `ui-enhancements`

Written 2026-11-17, at head `d7ecb29`; updated 2026-11-18 for event discovery (piece 2). Delete this file before the branch is merged.

## What is on this branch

1. A fix for the chat heading wrapping badly in a narrow window (`77578ff`).
2. Piece 1 of 5 of the Events area: a built-in calendar the harness owns, an Events page, a Calendar
   settings panel, and exchange with Google Calendar (pull, and push once switched on). The built-in
   calendar is the master; Google is only pulled from and pushed to. How it works is documented in
   `AGENTS.md`, section "The calendar and Google".

3. Piece 2 of 5: event discovery. A Discover tab on the Events page, an Event discovery section in Settings > Calendar,
   and the harness side: an Eventbrite reader, a pool of events, digests of cards, a closed scout bot with three tools and a
   weekly routine, the `/discover` routes, and adding a card to the calendar. Commits `da9d8f7..f3e0dd0` (the build, 12 tasks), then
   the Task 12 commits. How it works and its rules are in `AGENTS.md`, section "Event discovery"; the design is in
   `docs/superpowers/specs/2026-11-17-event-discovery-design.md`.

Pieces 3 to 5 are not designed or built: travel-aware location, and music-matched local shows (and the reactions that teach
later picks, a non-goal of piece 2).

**The owner's harness must be restarted to load this code**, and the GUI rebuilt (`npm run build`). Then pressing "Find events
now" on the Discover tab, in the owner's own app, is the first real test of the scout: it has never run with a model.

## Where it stopped

Piece 1 (the Events calendar and Google):

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

Piece 2 (event discovery), built 2026-11-18 by a subagent plan of 12 tasks, each reviewed on its own, with fix rounds on
Tasks 9 and 10. There has been no whole-branch review of piece 2 yet (the final review is next).

- Checks at the end of Task 12: `uv run pytest -q`: 16 failed, 2099 passed, 4 skipped, 20 errors (the same 16 failures and
  20 errors as before this work: Windows-only, none in `tests/events/`). `npm test`: 457 pass, 2 fail (the same two Electron
  tests, which read a source file with CRLF line ends). `npm run build`: clean.
- One live refresh against Eventbrite, in a throwaway vault, on 2026-11-18: place `oh--columbus`, 40 pages in about 100 s,
  727 rows read, 650 events kept, no error, no refusal. Details in `AGENTS.md`.
- The Discover tab and the settings section were seen running in a browser (Chrome by Playwright) against a throwaway vault
  and port, with a digest of six cards saved by hand through `Discovery.save`, not by the scout. Seen: the digest with
  pictures, a card with none, a three-line name; following a link (it opens the right Eventbrite page) and the dialog on
  return; "Not yet" (not asked again), "Yes, add it" and Add (the event reached My Calendar's store at the listing's time:
  10:00 New York is 15:00Z), "Not for me" (the card leaves); a bad address refused with its sentence and a good one saved;
  a 390 px window with no sideways scroll. Fixed from this: every category label ticked the first box (a shared id); the
  City field stretched beside State; the dialog's "Not for me" wrapped.
- **Not seen:** the "not set up", "no digest yet", "the scout is looking" and failed-refresh states; the Electron window's real
  focus event (the dialog was brought up by a script-sent `focus`); the My Calendar page showing an added event (only the
  store was read); the weekly routine firing; anything the scout does.

## Open items for discovery

- Eventbrite's terms on automated access were not read.
- The scout has never run with a model. Whether its turns reach the user's memory by the usual paths was not checked.
- The page cap reads Eventbrite's top-ranked 40 pages, not everything. Per-category page addresses were not tried.
- The harness API has no authentication, so a bot with `shell` can call the `/discover` routes, as it can `/calendar`
  (`POST /discover/cards/{id}/add` makes a calendar event that push then sends to Google).
- Settings > Calendar's tile and page description still speak of Google only, though the page now holds Event discovery too.
- A cancelled event stays a candidate until its date passes: a row seen as cancelled on a later refresh is skipped, not
  removed from the pool.
- Google may render markup in a calendar description; the description is the listing's summary. Not verified.
- The weekly run's day and time are shown read-only in the settings section, with a pointer to the Routines page; the
  design asked for them there.
- `discover-images/` is never pruned.
- The generalist bot holds the `events-digest` skill text (it gets every shipped skill); it cannot get the tools, but asked
  for events it may try to browse Eventbrite itself.

## Minor findings left for the final review

- Closed bot: the `mcps` compare is order-sensitive; a refused call logs no arguments; a saved room holding a closed bot cannot
  have its members edited until the bot is removed.
- Settings: `parse_place` with a non-string and `validated` with a non-dict are guarded but untested; `load` does not cap
  city or state.
- Eventbrite reader: the host-trick tests lack backslash, upper-case scheme and tab cases; an all-day event's `end_date` is
  inclusive in the pool (the page converts).
- The store does not itself stop a repeated event in one digest (the gate does).
- Scout skill says "what you remember"; the generalist bot holds the events-digest skill text but not the tools.
- Adding: no test at 14 hours and one second.
- Page: a far-future date (9999-12-31) gives an extended-year instant the harness would refuse; `parseInt` and `Number` are
  used inconsistently.
- Settings section: every save failure shows under the address field; the "Tick none to see them all" wording is unconfirmed.
- A failed refresh has no back-off beyond 10 minutes; the settings are loaded once at start.

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

- **Google.** Sign-in and a first pull were run by hand against Google on 2026-11-17 and worked: a
  Desktop-app client accepted the redirect `http://127.0.0.1:8765/calendar/google/callback`, and events
  arrived from the primary, shared and holiday calendars. Everything else has only run against the test
  fake (`tests/events/conftest.py` `FakeGoogle`): push has never reached Google, and no pull by sync token
  was watched. `AGENTS.md` lists the assumptions still to check. The one most likely to bite: whether sync
  tokens work with `singleEvents=true` the way the code expects.
- **The connected screens.** In a browser, only the states reachable without Google were seen: the Events
  page with made-here events (add, edit, delete, narrow widths), and the settings panel up to Connect
  opening Google's sign-in. The connected and needs-reconnect states, the calendar list, the push switch,
  Refresh, and a pulled event's details were read in code only.

Manual check to run with a real Google Desktop-app client. Done: paste the credentials, connect, tick a
calendar and see its events arrive. Still to do: change and delete an event on Google and see each arrive
on Refresh (the sync-token read), then switch push on, make an event here and see it on Google, edit it, delete it,
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
