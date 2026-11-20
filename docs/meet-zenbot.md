# Meet Zenbot

**Title for SEO (keep verbatim):** Meet Zenbot — open-source local-first desktop AI workspace
**Plain-text fallback:** Meet Zenbot: open-source local-first desktop AI workspace
**Version:** v0.1.1-beta, experimental. Passion project by Gordon Mott, MIT. Breaking changes expected.

> Open-source AI workspace for your desktop: local-first, self-learning, actually useful.
>
> Zenbot is the front door. You talk to him first. He hands work to bot buddies in the same room. Files stay local. You stay in charge.

**Hero spec:** `src/assets/zenbot-logo.png` at 240px max, then one screenshot: `docs/screenshots/overview.png`. No stock robots. No illustration behind logo. No text over screenshot.

> Palette TBD — verify by opening zenbot-logo.png. A11y TBD — Avery review required post-palette.

## What Zenbot is

Zenbot is the personal assistant bot (`bot-prompts/personal-assistant.md`). You talk to him first.

Open-Zenbot itself is an open-source, local-first desktop harness for organizing daily life and running day-to-day business from one place. Files, memory, tasks, calendar, and creative work stay on your machine. You can open the ledger and read it.

How it is built, in plain words:

* A desktop app (Electron + React 18 / Fluent 2 GUI in `src/gui/`). It talks to a local helper on your machine (`src/harness/main.py` on `http://127.0.0.1:8765`).
* Your files live in `vault/` under `~/.zen-agents/`. Bots can only reach `vault/` plus Allowed Folders.
* Every run is written to `vault-ledger/`. Plain JSONL you can open and read.

Sayable contrast: one task-loop, one router, one approval and ledger path, one vault, one scheduler, one GUI. No per-tool logins. No outside orchestrator.

## What Zenbot does — repo verbs only

We only use these verbs, because that is what the repo does:

**remember** (first-run interview plus imports plus Hindsight recall. Memory stays local on `127.0.0.1:18888/5556/9999`), **schedule** (routines plus built-in calendar in `vault-calendar/calendar.db` plus optional Google sync), **draft** (copy, docs and PDFs, delegated code drafts in `vault/delegated/`), **design** (Design Studio single-file HTML drafts in `vault/designs/<slug>/`), **organize** (vault plus rooms plus weekly digests), **summarize** (the generator only summarizes. The scorer never writes words), **approve** (you approve before shell writes or risky runs), **review** (you review every step in the readable JSONL ledger).

If it is not remember / schedule / draft / design / organize / summarize / approve / review, it does not go in this doc.

## Who it is for

**1. Local-first organizer — daily life**

When you juggle music, events, files, notes across the week, you want one desktop workspace that remembers and schedules for you.

You get: files stay in `~/.zen-agents/`. You can read the ledger. You stay in charge.

**2. Solo operator / freelancer — business day-to-day**

When you run your own day, you want one GUI plus task-loop that routes work to the right helper, so you do not re-explain context.

You get: delegate in `vault/delegated/`. Approve before shell writes. Rooms, Office and PDF skills, Design Studio drafts for client work.

We do not claim: CRM, invoicing, inventory, accounting, POS, payroll, compliance, SLA, support automation, or revenue outcomes. Not in 0.1.1.

**3. Self-hosting tinkerer — BYO-keys**

When you want AI help without handing over files, you want installer-simple local-first with clear guards, so you can see what ran.

You get: `.exe` / `.deb` installer, bring-your-own keys, `127.0.0.1:8765`, JSONL ledger you can open.

Not for: teams buying a production multi-user platform. That is Agno's job.

## Benefits — what this means for you

**1. Your files and memory stay local, readable.**

You work in `vault/` under `~/.zen-agents/`. Bots reach only `vault/` plus Allowed Folders. Every run lands in `vault-ledger/` JSONL you can open. Approvals gate shell writes.

*Picture it with: settings / file-path UI, not a cloud icon.*

**2. One task-loop, scored routing, you stay in charge.**

GUI talks to `127.0.0.1:8765` (`/bots`, `/sessions`, `/approvals`, `/ledger`). The decider scores each `use` line. Skills band 0.1 max 3. MCPs floor 0.5 max 3. Scorer never writes, generator never decides. Scheduler runs weekly work.

Guards you can point to: `allowed_folders.py`, `write_guard.py` at 0.8, magick guard plus `policy.xml`, closed `event-scout`, confined `delegate` to Claude Code.

*Picture it with: approvals queue crop, not a shield stock photo.*

**3. Daily-life workspace out of the box.**

Rooms plus supervisor every 5 steps plus routines. No setup puzzle to get something useful on day one.

*Picture it with: routines / rooms grid, not a single robot.*

## Bot buddies — the team in the room

Zenbot plus 8 bot buddies. 9 total in `bot-prompts/`, verified:

`personal-assistant.md` (Zenbot himself), `designer.md`, `music-librarian.md`, `event-scout.md`, `copywriter.md`, `darkroom-bot.md`, `picasso-bot.md`, `explainer.md`, `research-partner.md`.

No invented personalities. No backstories.

Zenbot plus buddies hand work to each other in rooms (`src/rooms/rooms.py`, `room.py`, `orchestrator.py`, 2 to 6 bots). That is it.

Visual rule: buddies as small, equal-weight cards in a grid. No hero robot towering over you. Friendly means rounded cards, soft dividers, human-scale copy.

## Grounded feature highlights — only what the repo shows

* **Task-loop + guards:** GUI talks to harness on `127.0.0.1:8765`. You approve before shell writes. Shell-write pass at 0.8 in `src/harness/write_guard.py`. `allowed_folders.py` limits where bots can go.
* **Scored routing:** decider `microsoft-decision-1` on OpenRouter Decisions API. Skills band width 0.1 max 3. MCPs floor 0.5 max 3. Fail-open `ZEN_ROUTER_FAIL_OPEN=1`. See `src/decider/router.py`, `registry.py`, `supervisor.py`.
* **9 bots / rooms:** 9 files in `bot-prompts/` (listed above) + `src/rooms/orchestrator.py`. Rooms run 2 to 6 bots.
* **Ledger:** every run in `vault-ledger/` JSONL. You can open and read it.
* **Vault paths:** `~/.zen-agents/vault*`, `vault-ledger/`, `vault-settings/mcp_keys.json` (owner-only + exclusion filter), `vault/delegated/`, `vault-music/`, `vault-calendar/calendar.db`, `vault/designs/<slug>/`.
* **Installer:** `OpenZenBot-Setup-<v>.exe` / `OpenZenBot_<v>_amd64.deb` from `ServaboFidem/open-zenbot` `v0.1.1`, about 5GB on first start. See `electron-builder.yml`, `README.md:Install`, `src/electron/setup.cjs`. No macOS app in 0.1.1. No Python/Node needed for installer.
* **Providers / keys:** bring-your-own keys. `mcp.json` holds 8 entries with `use` lines (`exa,time,files,browser,toolsmonk,hound,pexels,magick`). Decider needs OpenRouter + internet, else safe-defaults (all skills load, shell writes ask).
* **Music:** taste-map plus Friday 09:00 digest, 4 albums from Discogs + Soundlore atlas in `vault-music/`. See `src/music/atlas.py`, `digest.py`.
* **Events:** Thursday 09:00, 6 to 8 picks from Eventbrite + FindLocal to `vault-calendar/` + optional Google sync. See `src/events/store.py`, `google.py`, `sync.py`, `src/gui/events/`.
* **Design Studio:** single-file HTML drafts with sandboxed preview, point-comments, question forms, numbered versions, `design-systems/` with `DESIGN.md` / `tokens.css`. See `src/harness/design_studio.py`, `src/gui/DesignPane.jsx`.
* **Routines + scheduler:** rooms + supervisor every 5 steps + routines run weekly work. See `docs/screenshots/routines.png`.

No patchwork numbers. No invented quotes.

## How Zenbot is different

Sourced 2026-11-19 from upstream web docs. Re-check URLs before publishing comparison.

* **vs OpenClaw:** OpenClaw is the multi-channel gateway. One Gateway puts the same assistant in WhatsApp / Telegram / Slack / Discord / iMessage + mobile at once. Zenbot is a desktop workspace, not a gateway.
* **vs Hermes:** Hermes is the CLI-to-cloud harness. One agent + local search + many tools runs in terminal or hardened cloud with parallel subagents + per-session isolation. Zenbot is desktop GUI + scored router, not CLI-cloud.
* **vs Agno:** Agno is the production platform layer. SDK + API turns agents into secured, durable, observable multi-user APIs. Zenbot is single-user desktop, not a platform.

Do not use as differentiators: Hermes skill-file format, Agno Control Plane open vs proprietary split, OpenClaw sandbox posture. Do not claim safer-than-rivals. Harness API has no auth, claim explicit guards only.

## Caveats — read before you share

v0.1.1-beta experimental. Passion project by Gordon Mott. MIT. Breaking changes expected. Decider needs OpenRouter key plus internet. Without it, safe-defaults only. No macOS app in 0.1.1. No claim ImageMagick is included. No offline-only claim. No pricing, limits, load, perf, cost, or accuracy comparisons. No CRM and related claims. Visual colors unverified. Rival lines need re-verification before publishing.

## Voice guidance — how this should sound

Trusted learned friend at the bar. Ninth-grade level. Plain words. Short runs.

* Say: "stays in `~/.zen-agents/`", "you can read the ledger", "approve before shell writes", "delegate in `vault/delegated/`".
* Do not say: game-changing, revolutionary, disruptive, innovative, long-lasting. Say what it does: "Friday 09:00 digest, 4 albums."
* Translate code into human words, but keep the path so a tinkerer can check it.
* Read it aloud. If it sounds like marketing, rewrite.
* Tone in order: Calm, Companionable, Honest, Tidy, Quietly capable. Experimental badge stays visible. Screenshots over mockups.

Accessibility: functional alt under about 125 chars, no "screenshot of / image of". Logo next to H1 uses `alt=""` to avoid hearing it twice, else `alt="Zenbot logo"`. Decorative enso uses `alt=""`. Detail goes in caption/body, not alt.

## Reusable blocks for website + social

**Boilerplate (50 words):** Open-Zenbot is an open-source, local-first desktop AI workspace. Zenbot is the front door, the personal assistant bot you talk to first. Files stay in `~/.zen-agents/`. Every run lands in `vault-ledger/` JSONL you can open. v0.1.1-beta experimental by Gordon Mott, MIT.

**One-liner:** Open-source AI workspace for your desktop: local-first, self-learning, actually useful.

**Taglines — pick one per placement, do not stack:**

* Zenbot is the front door. You talk to him first.
* Files stay local. You stay in charge.
* Your bots, your routines, on your machine.
* Does the work. [Full variant "Does the work, not just the talking." held for Sebastian — flagged as negative parallelism]

**Website hero:** Left: headline + 3 bullets (Local-first / Self-learning / Useful for daily life) verbatim from README. Right: `overview.png` in a Fluent card, no browser-chrome fakery. Narrow: stacked, logo to headline to screenshot. Never text-over-screenshot — blocked until palette tokens lock.

**Social visuals:** 1:1 avatar: enso icon (`zenbot-enso.svg` preferred) centered on flat background only. No text in avatar. 16:9 share card: left logo + one claim, right single UI crop, generous margin. 9:16: logo top, one screenshot card middle, one line bottom. No 3+ collage. Caption experimental status where space allows.

**Repo verbs only:** remember / schedule / draft / design / organize / summarize / approve / review.

**Do-not-claim list:** CRM, invoicing, inventory, accounting, POS, payroll, compliance, SLA, support automation, revenue outcomes, pricing, limits, load, perf, cost, accuracy comparisons, offline-only, macOS app in 0.1.1, ImageMagick included, safer-than-rivals, patchwork counts, invented quotes.

## Social drafts — 4 posts, reuse verbatim

**Post 1 — Intro / hero (all audiences)**
Meet Zenbot, your bots, your routines, on your machine. Open-source AI workspace for your desktop: local-first, self-learning, actually useful. Files stay in `~/.zen-agents/`. You can read the ledger. You stay in charge. v0.1.1-beta, experimental by Gordon Mott, MIT. Breaking changes expected. Get the installer plus docs: ServaboFidem/open-zenbot `v0.1.1`.
Visual: logo plus `overview.png` only. No stock robots.

**Post 2 — Life organizer**
Juggling music, events, files, notes across the week? Zenbot remembers and schedules for you. One desktop workspace. Files stay local. You stay in charge. remember / schedule / organize / review. Every run lands in `vault-ledger/` JSONL you can open. v0.1.1-beta experimental.
Visual: settings or file-path UI, or `routines.png`. Not a cloud icon.

**Post 3 — Solo operator**
You run your own day. One GUI plus task-loop routes work to the right helper, so you do not re-explain context. Local-first. You stay in charge. Delegate in `vault/delegated/`. Approve before shell writes. Rooms, Office and PDF skills, Design Studio drafts for client work. v0.1.1-beta experimental. No CRM claims.
Visual: approvals queue crop.

**Post 4 — Tinkerer**
AI help without handing over files. One workspace. Your files stay local. `.exe` / `.deb` installer, bring-your-own keys in `vault-settings/mcp_keys.json`, talks to `127.0.0.1:8765`, JSONL ledger you can open and read. Single-user desktop, not a platform. v0.1.1-beta experimental. About 5GB on first start.
Visual: 16:9 share card, left logo plus one claim, right single UI crop. Caption only: 9 in `bot-prompts/`, rooms run 2-6 bots.
