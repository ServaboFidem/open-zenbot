# Slap-fight code debate 2f1554fc-2fd4-4227-a6a0-a26c25da7fec

- job: browser-helper2
- motion: This staged result achieves the job goal and every slice objective, and is safe to promote.
- contract: 2.0.0
- opened: 2026-11-20T20:50:38.665Z
- pins: defender `opencode-go/muse-spark-1.3-contributor`, attacker_a `claude-opus-5-5`, attacker_b `gpt-6.1-sol`
- binding staged_sha: `1020b7063fb01710674ba55c7304088703c4c5d6`
- binding examined_sha: `1020b7063fb01710674ba55c7304088703c4c5d6`
- binding base_sha: `06c705723b0e841766e15b0715dba1dc199e2c94`

**Verdict: FIX_FIRST**, closed by judged at 2026-11-20T20:59:11.580Z

## Turns

| round | role | phase | model | durationMs | exit | truncated | timedOut |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | attacker_a | attack | claude-opus-5-5 | 91240 | 0 | yes | no |
| 0 | attacker_b | attack | gpt-6.1-sol | 179766 | 0 | yes | no |
| 0 | defender | defense | opencode-go/muse-spark-1.3-contributor | 66205 | 0 | yes | no |
| 1 | attacker_a | cross | claude-opus-5-5 | 48938 | 0 | yes | no |
| 1 | attacker_b | cross | gpt-6.1-sol | 66749 | 0 | yes | no |
| 1 | defender | defense | opencode-go/muse-spark-1.3-contributor | 46379 | 0 | yes | no |

## Findings

| id | author | severity | category | path:line | status | judge mark |
| --- | --- | --- | --- | --- | --- | --- |
| A-1 | attacker_a | blocker | buildability | tests/events/test_facebook_helper.py:443 | confirmed | REAL_BLOCKER |
| A-2 | attacker_a | blocker | correctness | src/events/facebook_routes.py:291 | confirmed | REAL_BLOCKER |
| A-3 | attacker_a | blocker | correctness | src/events/birthdays.py:233 | confirmed | REAL_BLOCKER |
| A-4 | attacker_a | blocker | correctness | src/gui/events/EventsArea.jsx:27 | confirmed | REAL_BLOCKER |
| A-5 | attacker_a | warning | correctness | src/gui/events/FacebookPanels.jsx:126 | confirmed | REAL_BLOCKER |
| A-6 | attacker_a | warning | correctness | src/events/facebook_routes.py:286 | confirmed | REAL_BLOCKER |
| A-7 | attacker_a | warning | data_loss | src/events/facebook_pool.py:337 | confirmed | REAL_BLOCKER |
| A-8 | attacker_a | warning | security | tests/events/test_facebook_posts.py:84 | confirmed | REAL_NON_BLOCKING |
| A-9 | attacker_a | warning | other | research/slap-fight-browser-helper2.md:3 | confirmed | WRONG |
| A-10 | attacker_a | info | correctness | src/events/birthdays.py:209 | confirmed | - |
| B-F1 | attacker_b | blocker | correctness | src/events/facebook_pool.py:156 | confirmed | REAL_BLOCKER |
| B-F2 | attacker_b | blocker | correctness | src/gui/events/EventsArea.jsx:27 | confirmed | REAL_BLOCKER |
| B-F3 | attacker_b | blocker | correctness | src/events/birthdays.py:233 | confirmed | REAL_BLOCKER |
| B-F4 | attacker_b | blocker | correctness | src/events/facebook_routes.py:337 | confirmed | REAL_BLOCKER |
| B-F5 | attacker_b | blocker | correctness | src/events/facebook_lifecycle.py:175 | confirmed | REAL_BLOCKER |
| B-F6 | attacker_b | blocker | correctness | src/events/facebook_routes.py:284 | confirmed | REAL_BLOCKER |
| B-F7 | attacker_b | warning | test_gap | tests/events/test_facebook_helper.py:443 | confirmed | REAL_BLOCKER |

## Finding details

- A-1 (attacker_a): The validation gate's own anti-goal test fails at HEAD. The api-mount slice imports and calls `facebook_router` in src/harness/api.py, but `test_no_facebook_tool_is_registered_anywhere` treats any `facebook_router` hit outside src/events/facebook_routes.py as unexpected. Either the suite is red or it was never run on the integrated tree.
  - judge note: Confirmed by running pytest: test_no_facebook_tool_is_registered_anywhere fails because api-mount imports facebook_router in src/harness/api.py, which the validation gate did not allow.
- A-2 (attacker_a): The RSVP/personal split can never put a production pool row into the approval bucket. The helper schema has no RSVP field. The intake does attach the inviter, but `map_event` drops it, and the pool table has fixed columns with no inviter or rsvp. So `classify` sends every real Facebook event to the feed, which breaks the 'hold RSVP and personal events for approval' goal.
  - judge note: Helper schema has no RSVP field and map_event drops inviter; production pool rows lack the fields the splitter reads, so RSVP/personal approval cards cannot be created.
- A-3 (attacker_a): No production code calls the split, birthday holds, nudges, routine registration or lifecycle sync. Nothing in src/ invokes `split`, `hold_birthdays`, `send_nudges`, `birthdays.ensure_routine` or `sync_hold`. As a result, birthdays are never held or nudged, approval cards are never made, and cancelled or changed events never update their holds. The rsvp-split, birthday-holds and facebook-lifecycle objectives exist only as library functions under unit tests.
  - judge note: Grep confirms no production caller of split, hold_birthdays, send_nudges, ensure_routine, or sync_hold in src/; the feature's core logic is library-only.
- A-4 (attacker_a): The Facebook GUI tab is mounted without any data. EventsArea passes only api/online/notify, and FacebookPanels fetches nothing itself. `status` is therefore undefined, so the helper line always says 'not ready', and the invites, approvals and birthdays lists are always empty. The panel never reads `/intake/facebook/status`. This is client/server drift, and the gui-panels/gui-mount objective is not met.
  - judge note: EventsArea passes only api/online/notify to FacebookPanels; the component expects invites/approvals/birthdays/status props and has no data loading of its own.
- A-5 (attacker_a): Approve on an approval card sends the pool row id to the digest-card add route, `/discover/cards/{id}/add`. That route resolves discovery digest card ids, not Facebook pool ids like `fb-123`, so approving fails or targets the wrong record.
  - judge note: ApprovalRow calls discover.add(card.id), which POSTs to /discover/cards/{id}/add; approval cards carry pool ids (fb-*), not digest card ids, so approval targets the wrong route.
- A-6 (attacker_a): Every timed Facebook event lands in the pool without a time zone. As a result, adding it through the shared builder always fails with 422 NO_ZONE. The helper schema has no timezone field, and `to_raw` never passes one, so `_zone` returns "". listing_times refuses any timed listing that has no timezone.
  - judge note: Helper event schema lacks timezone; to_raw supplies none; _zone returns ''; calendar_add.listing_times rejects empty timezone, making timed events unaddable.
- A-7 (attacker_a): Updated reports can never refresh existing pool rows, so the stored state goes stale. The intake gathers every id already in the pool and passes it to map_batch as `existing_ids`. map_batch then drops any re-observed row with a known id as a `duplicate` before upsert. A time, place or title change on an existing Facebook event is discarded, so the lifecycle slice's premise that 'pool updates propagate to holds' cannot happen through the intake.
  - judge note: FacebookIntake passes every existing pool id as existing_ids to map_batch, which drops re-observed ids as duplicates; time/place/title updates never reach upsert_pool.
- A-8 (attacker_a): The postwalk gate does not prove that simple cross-site sends are refused. It tests a no-Content-Type body only with an invalid report (`{"helper":...}`), so the 422 comes from the schema check rather than any body or content-type rule. FastAPI parses a body with no Content-Type as JSON. A cross-site `fetch(..., {mode:'no-cors', body:new Blob([validReport])})` sends no Content-Type and needs no preflight, so a valid report from that page would be accepted. Likewise `{}` would satisfy NothingIn on /refresh. The same app guards /media/consent with an explicit content-type check for this reason, but the Facebook routes do not.
  - judge note: The walk test uses invalid bodies, so 422 comes from schema validation rather than proving refusal of a valid cross-site body with no Content-Type; real gap but does not break user-facing functionality.
- A-9 (attacker_a): The plan-review slice objective, 'Judge this plan through a three-model debate and record the verdict', was not met. The debate never reached a judge verdict. The slice records a waiver instead of a verdict, and no JSONL was produced.
  - judge note: MISSION block records plan=waived with a human waiver; that is an accepted resolution, not a staged-code defect.
- A-10 (attacker_a): Nothing guarantees that the nudge fires only once. `send_nudges` is stateless and fires whenever `today` is exactly seven days out. A second run on the same day nudges again, and a missed day skips the nudge. The objective says 'nudge once a week before'.
- B-F1 (attacker_b): The intake loses the information required for approval routing. map_event discards inviter and RSVP fields, the helper schema cannot carry RSVP signals, and intake never invokes the splitter. Personal events therefore enter ordinary discovery instead of waiting as approval cards.
  - judge note: Same as A-2: intake loses inviter/RSVP, so the splitter cannot route personal or RSVP events to approval.
- B-F2 (attacker_b): The mounted Facebook dashboard always shows empty lists and an unready helper. EventsArea supplies none of the invites, approvals, birthdays, or status props, and FacebookPanels contains no data-loading mechanism. Successful intake reports cannot appear on this tab.
  - judge note: Same as A-4: mounted Facebook tab has no data props and renders empty/unready.
- B-F3 (attacker_b): Birthday holds and nudges have no production execution path. Nothing registers the birthday routine or calls the hold and delivery functions, and the named birthday-bot and birthday skill are absent. Intake acknowledges birthdays without arranging their calendar holds or week-before notifications.
  - judge note: Same as A-3: no production execution path registers birthday routine or calls hold/send_nudges.
- B-F4 (attacker_b): Dedupe permanently freezes existing source rows. Every stored ID is supplied as an existing duplicate, so a later report changing that event's date, title, or place never reaches upsert_pool. The intake still reports a successful read while retaining stale information.
  - judge note: Same as A-7: dedupe against all existing ids freezes source rows; updates are discarded.
- B-F5 (attacker_b): Source disappearance never cancels an approved calendar hold. Empty reports retain previous pool rows, and no production caller invokes sync_hold. The lifecycle tests call this function directly, leaving the actual intake-to-calendar handoff unimplemented.
  - judge note: Same as A-3: sync_hold is never called; source disappearance or un-RSVP cannot remove an approved hold.
- B-F6 (attacker_b): Timed helper events cannot be approved or added. The accepted helper contract has no timezone field, and to_raw supplies none, so every timed row gets an empty timezone. Both the GUI and shared calendar builder refuse these rows.
  - judge note: Same as A-6: no timezone in helper contract means timed events cannot be added.
- B-F7 (attacker_b): The supplied validation gate fails after the required API mount. Its tool-registration scan treats any facebook_router reference as a forbidden tool, but allows only the router-definition file. The legitimate harness import triggers the assertion.
  - judge note: Same as A-1: the validation gate's own test fails at HEAD, keeping the suite red.

## Scoreboard

| metric | attacker_a | attacker_b |
| --- | --- | --- |
| raised | 10 | 7 |
| dropped_no_evidence | 0 | 0 |
| confirmed | 10 | 7 |
| killed_refuted | 0 | 0 |
| killed_outvoted | 0 | 0 |
| withdrawn | 0 | 0 |
| judged_real | 8 | 7 |
| judged_wrong_or_stale | 1 | 0 |
| rejects_that_held | 0 | 0 |
| rejects_overturned | 0 | 2 |

| metric | defender |
| --- | --- |
| conceded | 17 |
| refuted_held | 0 |
| refuted_overturned | 0 |
| refute_no_evidence | 0 |
