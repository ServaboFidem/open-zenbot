---
name: Weekly digest
slug: weekly-digest
use: Make this week's album digest for the user's record collection.
access:
  - music_profile
  - music_candidates
  - music_check
  - music_album
  - music_genre
  - music_save_digest
---
Make this week's digest: N overlooked albums the user does not own, chosen for their taste. N is `count` from `music_profile`. You have 15 minutes; stop and save what you have if you run out.

1. Call `music_profile`. Note `count`, `era_mix`, `obscurity`, the focus genres (the user's picks, including their narrower genres), the top genres and shares, the decades, and the recent reactions. Also use what you remember of the user's taste. Learn from passes and wants.
2. Brainstorm about 3N albums of your own, from what you know, in the focus genres and the era mix. Favour records few people own. Real albums only.
3. Call `music_candidates` for N or more albums the harness found and already checked.
4. Call `music_check` on each of your own ideas. Keep only the ones with `ok: true`. Drop the rest; do not argue with the reasons.
5. Choose N from the passing set (candidates and your own ideas together):
   - follow the era mix: an album is a "forgotten classic" when its year <= current year - 15, else a "newer discovery". `era_mix` (0 to 1) is the share of newer discoveries, so round(era_mix x N) cards are newer and the rest are classics. If one side is short, let it run short and do not fill it from the other side;
   - no two albums by the same artist;
   - spread across the focus genres: no focus genre gets more than half the cards when other genres have passing albums.
6. If fewer than N pass, widen once: move to the next related genres (`music_genre` shows the closest ones), call `music_candidates(widen=true)` once (it looks further down the related genres and relaxes the obscurity setting one step), check new ideas with `music_check`. Do not widen a second time. Then go with what passes. The save knows which albums came from that widened call; every other album gets the normal check.
7. For each chosen album, call `music_album` with its master id and read the verified track list. Pick two standout tracks from that list only, copied exactly.
8. Write each `why` in two or three sentences, in the voice of a record shop clerk: what the record sounds like, and why this user in particular. Mention the user's shelf only when the profile shows it. Never claim a check or fact the tools did not return.
9. Call `music_save_digest` with the cards: `artist`, `album`, `why`, `standout_tracks` (two titles), `focus_genre`. If fewer than N albums passed, pass `note` with one line such as "Three this week: the rest didn't pass the checks".
10. If the save is refused, the reply lists the failing albums and reasons. Replace only those cards with other passing albums (check them first) and save again.
11. If no album passes at all, do not save. Stop and report the reasons the albums failed.

## On-demand picks

When the task says `kind=on_demand` and names a genre, it is not this week's digest. Follow the steps above with these changes:

- Call `music_candidates(genre="<the genre>")` instead of `music_candidates` (and `music_candidates(genre="<the genre>", widen=true)` if you widen).
- Brainstorm albums in that genre only (its narrower genres count), not across the focus genres.
- Skip the focus-genre spread rule in step 5; the era mix and one album per artist still hold.
- Save with `kind="on_demand"`. A weekly save from an on-demand run is refused.

If any tool result starts with "Stopped:", stop at once, save nothing, and report that reason. The one exception is "Stopped: the 15-minute run budget is used up": then save the passing cards you already have with `music_save_digest` (it stays open two more minutes), or report if none passed.

Finish with one or two lines: how many albums you saved.
