# Facebook helper (Chrome-only v1 scaffold)

Observe-only MV3 extension. It watches three named corners — the events
dashboard, the invites page and the birthdays page — and reports cleaned
observations to the harness on this machine. No News Feed scan. No action as
the user: no presses, no RSVP answers, no posts, no messages.

## What lives here

- `manifest.json` — MV3, Chrome-only. Least privilege: `alarms` only, posts
  only to `http://127.0.0.1:8765/*`, content scripts only on the three
  corners, `script-src 'self'`.
- `corners.js` — the named-corner allowlist (exact host, exact paths).
- `clean.js` — text/link hygiene copied from `src/events/eventbrite.py`.
- `corner-events.js`, `corner-invites.js`, `corner-birthdays.js` — classic
  content scripts. Read-only DOM reads of the open page, cleaned and capped,
  handed to the background worker. They reach no network themselves.
- `background.js` — receives reports, re-checks the sender page is still a
  named corner, drops repeats, forwards via `sender.js`. Never opens,
  navigates or scripts a tab. The alarm is housekeeping only (sweeps the
  seen-digest cache).
- `sender.js` — the only file that posts anywhere, and only to
  `http://127.0.0.1:<port>/intake/facebook`.
- `schema.js` — the payload contract the intake slice consumes (see below).
- `helper.test.mjs` — static checks + behavior. Run
  `node --test src/helper/helper.test.mjs`.

## Rhythm (builder's pick, polite bounds)

Re-read at most every 240 minutes (about four sweeps through the day), only
in tabs the user already has open; sends paced at least 1 s apart. One
rhythm constant (`CHECK_INTERVAL_MIN`), shared by the background worker and
the readers and pinned by the tests.

## Intake contract (for the intake slice)

POST JSON to `/intake/facebook`:
`{helper: "fb-events-v1", observed_at, corner, url, items}` where `corner`
is `events_dashboard` | `invites` | `birthdays`, `url` is that corner's page,
and each item carries `kind: "event" | "invite" | "birthday"` with the fields
in `schema.js`. Unknown keys are refused. Text arrives cleaned and capped;
links are https on exactly `www.facebook.com`.
