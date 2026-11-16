# Design studio

Status: draft for approval. Date: 2026-11-15.

## Intent

Gordon has built real designs with OpenDesign (nexu-io/open-design) and wants the part of it that
made that work, inside this harness: talk to a bot, watch the design appear beside the chat, point at
a part of it and say what to change. This is project 1 of three. Delegating to Claude Code or Codex
(project 2) and critique, direct edits, versions and export (project 3) come later.

Lines marked **(call)** are the author's choices, not things Gordon decided.

## Goals

1. A designer bot that produces a self-contained HTML design and shows it in the conversation.
2. A preview docked beside the chat that follows the design as the bot revises it.
3. Click an element in the preview, add a note, and the bot changes that element and nothing else.
4. The bot can ask its opening questions as a form the user fills in, not as a wall of text.
5. A design system (a brand contract) can be picked per conversation and the bot follows it.

## Non-goals

- Running external agent CLIs. The harness's own bots write the HTML.
- Editing the design without a bot turn, tweak sliders, critique, version history, export.
- Rooms. The studio works in a one-to-one conversation only.
- Multi-file designs, external fonts, images or scripts. One HTML file, everything inline.
- Decks, video, a plugin catalog, shipping brand design systems.

## Anti-goals

- The auto-shown preview reaching the network.
- The app trusting anything the previewed page tells it.
- A new way to write files outside `allowed_folders.reachable`.
- Anything that grants access being stored inside the vault.

## Constraints from the code as it is

- Attachments are served by `serve_attachment` (`src/harness/api.py:848`). With `?preview=1` the page
  gets `PREVIEW_CSP` (`api.py:94`): sandboxed, inline script and style only, images and fonts only as
  `data:`. Nothing loads from anywhere. The studio preview keeps exactly this policy.
- The GUI frames bot pages with `sandbox="allow-scripts"`, never same-origin (`src/gui/MediaCards.jsx`).
- Bot replies render through `react-markdown`, which drops raw HTML (`src/gui/Markdown.jsx`). A form
  therefore travels as a fenced code block, not as an XML tag.
- A task arrives as `TaskIn` (`api.py:713`); a bot's per-task context is `Bot.context_text`
  (`src/harness/bot.py`), which today carries recalled facts and selected skills.
- Skills are chosen per task by the selector, so a skill named in a bot file is not guaranteed to be
  loaded on every task. The same holds for MCP servers unless the bot file pins them with `mcps`.
- An attachment is `{id, title, path}` and is served from the live path, so re-attaching a revised
  file makes every earlier attachment of it show the new content.
- `PREVIEW_CSP` stops a framed page loading anything, but not navigating itself (`location = ...`, a
  meta refresh, a clicked link). Electron's `will-navigate` handler (`src/electron/main.cjs`) covers
  only the main frame. So a bot-made page, including today's thumbnails, can send what it holds to
  any URL by navigating. This is an existing gap, found while reviewing this design.
- The GUI runs in the Electron shell (`app://openzenbot`). A plain browser is not a supported way to
  use it, so the navigation block below is Electron's **(call)**.

## Approaches

- **A. Extend the pop-up viewer.** Add comment mode to `MediaViewer`. Cheapest. The viewer covers the
  chat, so the user cannot watch or talk while looking.
- **B. Dock the preview beside the chat.** Chosen. It gives the side-by-side loop and reuses the
  attachment and preview plumbing.
- **C. A separate Design page with projects.** Closest to OpenDesign. It duplicates conversations and
  is much more work before anything is usable.

Gordon said "start with 1" and left the how to the author; B is the author's pick **(call)**.

## Design

Four slices, each usable when it lands, built in this order.

### Slice 1: designer bot and docked preview

- `bot-prompts/designer.md`, with a new frontmatter flag `studio: true` and `skills: [design-studio]`.
  The bot file itself holds the protocol, because a skill may not be selected on a given task
  **(call)**: write one self-contained HTML file to `<vault>/designs/<slug>/index.html`;
  put a stable kebab-case `data-zen-id` on every meaningful element and keep ids across revisions;
  inline all style, script, fonts and images; call `attach_file` after every change; revise the same
  file rather than starting a new one. The bot file also pins `mcps: [files]`, so the bot always
  writes through the `files` server and never through `shell`, whose write check could stop each
  revision for approval. Designs live under the vault, not the shell working folder, because `files`
  is rooted at the vault and `ZEN_SHELL_CWD` may point elsewhere **(call)**.
- Revisions **(call)**: when a `studio` bot attaches an HTML file, the harness copies it to
  `<vault>/designs/<slug>/.versions/<n>.<name>.html` and the attachment points at that copy. Each attachment
  is then one fixed revision. This closes the "bad revision replaces a good one" gap cheaply: the
  copies sit where the bot can read them, so "go back to version 3" is an ordinary request. A restore
  button, diffing and pruning stay in project 3.
- Navigation: the Electron shell blocks every subframe navigation away from the harness's own
  address (`will-frame-navigate`). This covers the pane and the existing thumbnails and viewer.
- `skills/design-studio/` holds craft rules as reference files (avoiding generic AI look, typography,
  colour, accessibility baseline, covering empty/loading/error states). These may be adapted from
  OpenDesign's `craft/` (Apache-2.0) with a NOTICE in the skill folder and a credit in `README.md`.
- GUI: in a conversation with a `studio` bot, a pane beside the chat (`src/gui/DesignPane.jsx`) frames
  the newest HTML attachment of the conversation, and reloads when a newer one arrives. A menu lists
  the earlier revisions. The pane can be hidden. Other bots are unchanged.
- The pane loads the attachment with `?studio=1`: same headers as `?preview=1`, plus the bridge
  script from slice 2 inserted by the harness before `</body>` (appended when there is none). Files
  over 5 MB are served without the bridge and the pane says picking is off for this file **(call)**.

### Slice 2: point and comment

- The bridge is a small inline script. The pane tells it, by `postMessage`, when pick mode is on and
  which pins exist. In pick mode it outlines the element under the pointer, swallows the click, and
  posts one message: `data-zen-id` (or empty), a CSS selector path, tag name, the first 160 characters
  of text, and the bounding box. It draws numbered pin markers.
- The pane accepts a message only when `event.source` is its own frame's window, the shape is exact,
  and every field is within its cap. Everything in it is shown and stored as plain text. A pick does
  one thing: it adds a numbered chip to the composer, where the user writes a note and can remove it.
  Nothing is sent until the user sends. This is what makes a forged message from a bot-made page
  harmless: the worst it can do is add a chip the user sees.
- `TaskIn` gains `design_comments`: a list of `{attachment_id, element_id, selector, tag, text, note}`.
  Caps **(call)**: 20 per task, note 1000 characters, text 160, selector 300; over a cap is a 422.
- The harness, not the GUI, turns the comments into the text the bot reads, appended to the task: the
  file's path, then per comment its number, id or selector, current text and the note, under the rule
  "Change only these elements. Leave the rest of the file as it is unless a change requires it, and
  say so if it does." The structured comments are kept on the conversation's exchange so the chat
  shows chips, not the raw block. Picking works on the newest revision only; an earlier one is
  view-only, so a comment always refers to the file as the bot last left it **(call)**.

### Slice 3: question form

- The bot may end a reply with a fenced block whose language is `question-form` holding JSON:
  `{id, title, questions: [{id, label, type, options?, default?, allow_custom?}]}`. Types **(call)**:
  `radio`, `checkbox`, `select`, `text`, `textarea`, `range`, `color`. At most 5 questions.
- `Markdown.jsx` renders a valid block as a form; an invalid one stays a code block. Only the form in
  the newest reply is live; older ones show read-only.
- Submitting sends an ordinary user message: `Form answers (<id>):` and one `- label: value` line per
  question. No API change. The designer prompt says: on a new brief, ask with one form and stop; skip
  it when the brief already answers the questions.
- The renderer is generic, so any bot can use it later; only the designer is taught it now.

### Slice 4: design systems

- A design system is a folder: `DESIGN.md` (required), `tokens.css` (optional).
- Shipped in the repo under `design-systems/`: two neutral starters, `default` and `warm-editorial`.
  No brand systems ship **(call)**: their provenance and trademark position is not ours to vouch for.
- The user's own live in `<vault>/design-systems/<slug>/`. This is inside the vault on purpose: a
  design system grants nothing, and the bot should be able to read one and help write one. A user
  folder replaces a shipped one with the same slug. Any `DESIGN.md` folder dropped in works, including
  one copied from an OpenDesign install.
- `GET /design-systems` lists slug, name, source (`shipped` or `yours`) and a one-line summary.
- The choice is per conversation, stored on the conversation record, set by
  `PUT /conversations/{id}/design-system` with a slug or null. The picker sits in the chat heading, so a
  system can be chosen before the first design exists.
- When set, the task's context gets one more block after facts and skills: `DESIGN.md` in full, then
  `tokens.css`, introduced as authoritative ("use these tokens; do not invent colours, type or
  spacing"). Together over 40,000 characters is refused when picked, with the reason **(call)**:
  OpenDesign's own systems run from 3,000 to 30,000 characters of `DESIGN.md` plus 11,000 to 17,000
  of tokens, so this admits most of them. The builder must check how this block counts against
  `ZEN_CONTEXT_BUDGET_CHARS` (120,000) so a large system does not force compaction every turn.

## Tests

- pytest: `?studio=1` headers equal the preview policy and the bridge is inserted once; the size cut;
  a `studio` attach makes a numbered copy and the attachment serves that copy after the file changes;
  comment caps and the rendered block; a comment naming an attachment of another conversation is
  refused; design-system listing, override by slug, size refusal, and the context block.
- node (`tests/gui/`): form parsing and the answer message; pick-message validation (wrong source,
  extra fields, over-long fields); the Electron handler refuses a subframe navigation to another host.

## Left to the builder

Pane layout and resizing, the bridge's exact code and marker drawing, how `studio` is read from the
bot file and exposed to the GUI, how the pane learns of a new attachment during a live task, the
wording of the designer prompt and craft files, and `AGENTS.md` and wiki updates.
