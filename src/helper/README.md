# Facebook helper (Chrome-only v1 scaffold)

Observe-only MV3 extension. It watches three named corners — the events
dashboard, the invites page and the birthdays page — and reports cleaned
observations to the harness on this machine. No News Feed scan. No action as
the user: no presses, no RSVP answers, no posts, no messages.

## What lives here

- `manifest.json` — MV3, Chrome-only. Permissions `alarms` and `scripting`
  only; posts only to `http://127.0.0.1:8765/*`; Facebook host access only on
  `/events*` and `/birthdays*` (so the background can start a corner's reader,
  below); content scripts only on the three corners, with or without a query
  (`?ref=...`); `script-src 'self'`.
- `corners.js` — the named-corner allowlist (exact host, exact paths). The
  birthdays corner is `/birthdays/` and `/events/birthdays/`, where Facebook
  now sends it.
- `clean.js` — text/link hygiene copied from `src/events/eventbrite.py`.
- `page-text.js` — the shared text readers (date lines, cards, cleaning),
  loaded before each corner reader in the extension's isolated world. Reads
  text only; reports nothing itself.
- `corner-events.js`, `corner-invites.js`, `corner-birthdays.js` — classic
  content scripts. Read-only DOM reads of the open page, cleaned and capped,
  handed to the background worker. They reach no network themselves. Loaded
  a second time in the same tab, a reader reads again instead of running
  twice.
- `background.js` — receives reports, re-checks the sender page is still a
  named corner, drops repeats, forwards via `sender.js`. It also starts a
  corner's reader (`startReader`) when a tab's address becomes a named corner
  without a page load (Facebook moves between pages by script, so a click to
  Events inside Facebook never gets the content script), and in tabs already
  on a corner when the helper is installed or reloaded. Only the files in
  `READER_FILES`, only on a named corner; it never opens, navigates or reloads
  a tab. The alarm is housekeeping only (sweeps the seen-digest cache).
- `sender.js` — the only file that posts anywhere, and only to
  `http://127.0.0.1:<port>/intake/facebook`.
- `schema.js` — the payload contract the intake slice consumes (see below).
- `icons/` — the extension's icon at 16, 32, 48 and 128 px, made from
  `src/assets/zenbot-enso-icon.png` (padded to a square).
- `helper.test.mjs` — static checks + behavior. Run
  `node --test src/helper/helper.test.mjs`.

## Loading it in Chrome

1. Start the harness so it answers on `http://127.0.0.1:8765`.
2. Open `chrome://extensions`, switch on **Developer mode**, click **Load
   unpacked** and pick this folder (`src/helper`, the one with
   `manifest.json`).
3. Sign in to Facebook in that Chrome and open
   `https://www.facebook.com/events/` (and, if wanted, `/events/invites/` and
   `/events/birthdays/`). The Facebook tab on the Events page fills within
   about 30 seconds.

After a change here, click the reload arrow on the helper's card. Tabs already
on a corner are read again without a page reload (`onInstalled` in
`background.js`). A version that adds a permission makes Chrome ask again.

## Rhythm (builder's pick, polite bounds)

Re-read at most every 240 minutes (about four sweeps through the day), only
in tabs the user already has open; sends paced at least 1 s apart. Each reader
also reads 3, 10 and 30 s after it starts, since Facebook draws the page after
it settles; a read that finds nothing new is dropped as a repeat by the
background worker. One rhythm constant (`CHECK_INTERVAL_MIN`), shared by the
background worker and the readers and pinned by the tests.

## What the readers read (seen 2026-11-20)

Facebook's pages have no `<time>` elements, and the events pages no headings
or articles around a card. Dates are English words, read by `page-text.js`:
"Tomorrow at 10 AM", "Sat, Nov 28 at 12 PM", "Fri, Dec 11 - Dec 12",
"Happening now" (today, no time).

- **Events dashboard**: a card is the block around one event's links; its
  text runs date line, title, venue. Only the main region is read (the
  notification flyout links to events too).
- **Invites**: read as the dashboard is, with the inviter from a line like
  "Sam Rivera invited you". The page was seen only empty, so a card with an
  invitation in it has not been checked against the real page.
- **Birthdays**: one heading per person, inside the link to their profile,
  under "Today's", "Recent" and "Upcoming birthdays"; then a date line
  ("November 23, 1992"), except under Today's, which takes today's date. The
  month sections ("A, B and 35 others") are not read. Profile links lose
  Facebook's tracking query.

A card whose date line it cannot read is dropped, and so is everything on a
Facebook set to another language.

## Intake contract (for the intake slice)

POST JSON to `/intake/facebook`:
`{helper: "fb-events-v1", observed_at, corner, url, items, timezone}` where
`timezone` is the browser's own zone (added by `background.js`; the harness
takes every timed Facebook event to be in it), `corner`
is `events_dashboard` | `invites` | `birthdays`, `url` is that corner's page,
and each item carries `kind: "event" | "invite" | "birthday"` with the fields
in `schema.js`. Unknown keys are refused. Text arrives cleaned and capped;
links are https on exactly `www.facebook.com`.
