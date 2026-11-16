# Design Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A designer bot whose one-to-one conversation has a live preview docked beside the chat, where the user clicks an element, adds a note, and the bot changes only that element.

**Architecture:** The harness's own bots write one self-contained HTML file under `<vault>/designs/<slug>/` through the `files` server. Each attach makes a numbered copy, which the GUI frames with the existing network-closed preview policy plus a small injected bridge script. Picks travel to the bot as structured comments on the task; forms travel as fenced blocks in the reply; a design system is one more block in the task's context.

**Tech Stack:** Python 3 / FastAPI / pydantic / pytest (`uv run pytest`); React 18 + Fluent 2, esbuild, Electron 45; node's built-in test runner (`npm test`).

**Spec:** `docs/superpowers/specs/2026-11-15-design-studio-design.md`. Read it first.

## Global Constraints

- The studio preview is served with exactly `PREVIEW_CSP` (`src/harness/api.py:94`). Nothing loads from anywhere.
- The GUI never trusts a message from the framed page. A pick only ever adds a chip the user sees.
- One-to-one conversations only. Rooms are untouched.
- One HTML file per design, everything inline.
- No new write path outside `allowed_folders.reachable`. The harness's own copies go under `<vault>/designs/`.
- Nothing that grants access is stored inside the vault. Design systems grant nothing and may live there.
- Caps, verbatim from the spec: 20 comments per task; note 1000 characters, text 160, selector 300; 5 questions per form; bridge off above 5 MB; design system 40,000 characters.
- Work on a branch named `design-studio`, never on `main`. Do not edit `CHANGELOG.md`. End each commit message with the session attribution line the harness gives you.
- Per `AGENTS.md`: find code with `graft ask` / `graft skeleton` before opening files; run `graft build` after the last task.
- Windows is the development machine: use `pathlib`, compare resolved paths, never string-compare with `/`.

## Review Focus

1. **A design-system file that is a symlink out of its folder** (a bot can write the vault, so it could plant `DESIGN.md` pointing at `vault-settings/providers.json`): refused, never read into a prompt. Test in Task 9.
2. **HTML with no `</body>`, an upper-case `</BODY>`, or the string `</body>` inside a script**: the bridge is inserted exactly once, before the last case-insensitive `</body>`, else appended. Test in Task 4.
3. **The same unchanged file attached twice, or a `.versions/` copy attached directly**: no new copy is made. Test in Task 3.
4. **A comment that names an attachment that is not the newest design revision of this conversation**: 422, nothing starts. Test in Task 6.
5. **A `question-form` block that is malformed, has duplicate question ids, or a radio with no options**: stays an ordinary code block and never throws. Test in Task 8.

## File map

| File | Responsibility |
| --- | --- |
| `src/harness/design_studio.py` (new) | Revision copies, the bridge insert, the comment block |
| `src/harness/design_bridge.js` (new) | The script inserted into a studio preview |
| `src/harness/design_systems.py` (new) | Find, check and render design systems |
| `src/harness/bot_prompts.py`, `bot.py` | The `studio` flag |
| `src/harness/api.py`, `conversations.py`, `loop.py` | Routes, records, context |
| `src/electron/frame-nav.cjs` (new), `main.cjs` | Subframe navigation block |
| `src/gui/design.js` (new) | Pure helpers: revisions, frame URL, pick validation |
| `src/gui/DesignPane.jsx` (new) | The docked pane |
| `src/gui/question-form.js`, `QuestionForm.jsx` (new) | Form parsing and rendering |
| `bot-prompts/designer.md`, `skills/design-studio/`, `design-systems/` (new) | Shipped content |

---

### Task 1: The `studio` flag on a bot

**Files:**
- Modify: `src/harness/bot_prompts.py` (`BotPrompt`, `parse`, `render`), `src/harness/bot.py` (`Bot`, `to_dict`), `src/harness/api.py` (where bots are built from prompts, and the save at `api.py:2265`)
- Test: `tests/test_bot_prompts.py` (create if absent; otherwise add), `tests/test_api.py`

**Interfaces:**
- Produces: `BotPrompt.studio: bool = False`; `Bot.studio: bool = False`; `"studio"` key in `Bot.to_dict()` and so in `GET /bots`.

- [ ] **Step 1: Write the failing tests**

```python
def test_studio_flag_round_trips():
    p = parse("---\nname: D\nrole: r\nstudio: true\n---\nbody", "d")
    assert p.studio is True
    assert parse(render(p), "d").studio is True
    assert "studio" not in render(parse("---\nname: D\nrole: r\n---\nbody", "d"))

def test_studio_must_be_a_boolean():
    with pytest.raises(ValueError, match="studio must be true or false"):
        parse("---\nname: D\nrole: r\nstudio: yes please\n---\n", "d")
```

In `tests/test_api.py`: load a harness whose bot file has `studio: true`, `PATCH /bots/{id}` with a new role, then assert `GET /bots` still reports `"studio": True` and the file on disk still has `studio: true`.

- [ ] **Step 2: Run** `uv run pytest tests/test_bot_prompts.py tests/test_api.py -k studio -v`. Expected: FAIL (`BotPrompt` has no `studio`).
- [ ] **Step 3: Implement.** Carry `studio` through every place a `BotPrompt` becomes a `Bot` and back; `render` writes the key only when true.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: studio flag on bot files`.

### Task 2: The designer bot and its craft skill

**Files:**
- Create: `bot-prompts/designer.md`, `skills/design-studio/SKILL.md`, `skills/design-studio/references/{anti-slop,typography,colour,accessibility,states}.md`, `skills/design-studio/NOTICE.txt`
- Modify: `README.md` (credits section, beside the Soundlore credit)
- Test: `tests/test_bot_prompts.py`

**Interfaces:**
- Consumes: `studio` from Task 1.
- Produces: bot id `designer`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_designer_bot_file_is_a_studio_bot():
    p = parse(Path("bot-prompts/designer.md").read_text(encoding="utf-8"), "designer")
    assert p.studio is True and p.skills == ["design-studio"] and p.mcps == ["files"]
    for must in ("designs/", "data-zen-id", "attach_file", "question-form"):
        assert must in p.instructions
```

- [ ] **Step 2: Run** `uv run pytest tests/test_bot_prompts.py -k designer -v`. Expected: FAIL (no file).
- [ ] **Step 3: Write `bot-prompts/designer.md`.** Frontmatter: `name: Designer`, a one-line `role`, `skills: [design-studio]`, `mcps: [files]`, `studio: true`. The body states the protocol, in the plain imperative voice of `bot-prompts/explainer.md`:
  - write one self-contained HTML file at `designs/<slug>/index.html` in the vault, with the `files` tools, never `shell`;
  - all style, script, fonts and images inline (`data:` URIs); nothing loaded from the network;
  - a stable kebab-case `data-zen-id` on every meaningful element, kept across revisions;
  - call `attach_file` after every change; revise the same file;
  - when a message carries "Comments on the design", change only those elements;
  - earlier versions are in `designs/<slug>/.versions/` and can be read back on request;
  - on a new brief that leaves audience, tone or content open, ask once with a `question-form` block (shape in Task 8) and stop; skip it when the brief already answers;
  - follow a design system when one is in the context.
- [ ] **Step 4: Write the skill.** `SKILL.md` in the shape of `skills/example-brief/SKILL.md`, pointing at the five reference files. Adapt the references from OpenDesign's `craft/` (`anti-ai-slop.md`, `typography.md`, `color.md`, `accessibility-baseline.md`, `state-coverage.md`, at commit `59e4f2a` of `nexu-io/open-design`, Apache-2.0), each condensed to 120 lines or fewer. `NOTICE.txt` names the source, the commit and the licence. Add one credit line to `README.md`.
- [ ] **Step 5: Run** `uv run pytest tests/test_bot_prompts.py tests/ -k "designer or skill" -v`. Expected: PASS, including the existing skill-loader tests.
- [ ] **Step 6: Commit** `feat: designer bot and design-studio craft skill`.

### Task 3: A numbered copy on every attach

**Files:**
- Create: `src/harness/design_studio.py`, `tests/test_design_studio.py`
- Modify: `src/harness/api.py` (`Harness.attach` at `:477`; the `attached` list at `:549`; `get_conversation` at `:1875`)
- Test: `tests/test_design_studio.py`, `tests/test_api.py`

**Interfaces:**
- Produces:
  - `snapshot(vault_root: Path, target: Path) -> Path | None`: for `<vault>/designs/<slug>/<stem>.html` (or `.htm`) directly in the slug folder, copies it to `<vault>/designs/<slug>/.versions/<n>.<stem>.html` and returns the copy. `n` is one more than the highest existing. When the newest copy of that stem has the same bytes, returns that copy and writes nothing. Any other path returns `None`.
  - `revision_of(vault_root: Path, path: Path) -> tuple[str, int, str] | None`: `(slug, n, "<stem>.html")` for a copy path, else `None`.
  - Stored attachment dicts stay `{id, title, path}`. `GET /conversations/{id}` adds `"design": slug` and `"version": n` to an attachment whose path is a copy, and `"name"` is then the original file name, not the copy's.

- [ ] **Step 1: Write the failing tests** in `tests/test_design_studio.py`

```python
def test_snapshot_numbers_copies_and_skips_an_unchanged_file(tmp_path):
    f = tmp_path / "designs" / "landing" / "index.html"; f.parent.mkdir(parents=True)
    f.write_text("<p>one</p>", encoding="utf-8")
    a = snapshot(tmp_path, f)
    assert a == tmp_path / "designs" / "landing" / ".versions" / "1.index.html"
    assert snapshot(tmp_path, f) == a and len(list(a.parent.iterdir())) == 1
    f.write_text("<p>two</p>", encoding="utf-8")
    b = snapshot(tmp_path, f)
    assert b.name == "2.index.html" and a.read_text(encoding="utf-8") == "<p>one</p>"
    assert revision_of(tmp_path, b) == ("landing", 2, "index.html")

@pytest.mark.parametrize("rel", ["designs/x.html", "designs/a/b/index.html", "designs/a/notes.md",
                                 "designs/a/.versions/1.index.html", "other/a/index.html"])
def test_snapshot_leaves_other_paths_alone(tmp_path, rel):
    f = tmp_path / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("x", encoding="utf-8")
    assert snapshot(tmp_path, f) is None
```

In `tests/test_api.py`, beside `_attach_task`: with a `studio` bot, script the provider to attach `designs/landing/index.html` (written under the harness's vault), then overwrite the file and assert the served attachment still returns the first content and `GET /conversations/{cid}` reports `{"design": "landing", "version": 1, "name": "index.html"}` on it. A second test: the same with a non-studio bot serves the live file and has no `version`.

- [ ] **Step 2: Run** `uv run pytest tests/test_design_studio.py tests/test_api.py -k "snapshot or version" -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the two functions, then in `Harness.attach`, when the session's bot is `studio`, replace `target` with `snapshot(vault_root, target) or target`.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: keep a numbered copy of each attached design revision`.

### Task 4: The studio preview and its bridge

**Files:**
- Create: `src/harness/design_bridge.js`
- Modify: `src/harness/design_studio.py`, `src/harness/api.py` (`serve_attachment`, `conversation_attachment`)
- Test: `tests/test_design_studio.py`, `tests/test_api.py`

**Interfaces:**
- Produces:
  - `BRIDGE_MAX_BYTES = 5 * 1024 * 1024`; `with_bridge(html: bytes) -> bytes`.
  - `GET /conversations/{id}/attachments/{att}?studio=1`: `PREVIEW_CSP`, `nosniff`, HTML with the bridge. Over the size cap, or not `.html`/`.htm`: the same headers, the file unchanged.
  - Messages, all plain objects. Frame to host: `{zen: 'ready'}` once on load; `{zen: 'pick', elementId, selector, tag, text, rect: {x, y, w, h}}`. Host to frame: `{zen: 'mode', pick: boolean}`; `{zen: 'pins', pins: [{n, elementId, selector}]}`.

- [ ] **Step 1: Write the failing tests**

```python
MARK = b"<script data-zen-bridge>"

@pytest.mark.parametrize("html, tail", [
    (b"<html><body><p>x</p></body></html>", b"</body></html>"),
    (b"<BODY><p>x</p></BODY>", b"</BODY>"),
    (b"<body><script>var s='</body>';</script></body>", b"</body>"),
])
def test_bridge_goes_once_before_the_last_body_close(html, tail):
    out = with_bridge(html)
    assert out.count(MARK) == 1 and out.endswith(tail) and out.rindex(MARK) < out.rindex(tail)

def test_bridge_is_appended_when_there_is_no_body_close():
    assert with_bridge(b"<p>x</p>").startswith(b"<p>x</p>" + MARK)
```

In `tests/test_api.py`: `?studio=1` on an HTML attachment has `content-security-policy == PREVIEW_CSP` and one `data-zen-bridge` in the body; a 5 MB + 1 byte file has the header and no bridge; without `studio` nothing changes from today.

- [ ] **Step 2: Run** `uv run pytest tests/test_design_studio.py tests/test_api.py -k "bridge or studio" -v`. Expected: FAIL.
- [ ] **Step 3: Write `design_bridge.js`.** In pick mode it outlines the element under the pointer and, on click, calls `preventDefault` and `stopPropagation` (capture phase) and posts one `pick` with `parent.postMessage(msg, '*')`. `elementId` is the nearest ancestor's `data-zen-id` or `''`; `selector` is a tag and `:nth-of-type` path from `body`, cut at 300 characters; `text` is the first 160 characters of `innerText`, whitespace collapsed. It listens for `mode` and `pins` only from `window.parent` and draws numbered markers for the pins. Outside pick mode it still cancels clicks on links whose `href` is not a `#fragment`.
- [ ] **Step 4: Implement `with_bridge` and the route.** `with_bridge` reads the script file once at import and wraps it as `<script data-zen-bridge>…</script>`.
- [ ] **Step 5: Run the same command.** Expected: PASS.
- [ ] **Step 6: Commit** `feat: studio preview with the pick bridge`.

### Task 5: Block framed pages from navigating away

**Files:**
- Create: `src/electron/frame-nav.cjs`, `tests/gui/frame-nav.test.mjs`
- Modify: `src/electron/main.cjs` (`createWindow`)

**Interfaces:**
- Produces: `allowFrameNavigation(url: string) -> boolean`, true only for `http:` or `https:` with host `127.0.0.1`, `localhost` or `[::1]`.

- [ ] **Step 1: Write the failing test** (load the module the way `tests/gui/shell.test.mjs` loads `.cjs` files)

```js
test('a framed page may only move within this machine', () => {
  assert.equal(allowFrameNavigation('http://127.0.0.1:8765/conversations/c/attachments/a?studio=1'), true);
  assert.equal(allowFrameNavigation('http://localhost:9999/'), true);
  for (const u of ['https://example.com/?d=secret', 'http://127.0.0.1.evil.test/', 'file:///C:/x', 'javascript:1', 'not a url'])
    assert.equal(allowFrameNavigation(u), false, u);
});
```

- [ ] **Step 2: Run** `node --test tests/gui/frame-nav.test.mjs`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement**, and in `createWindow` add a `will-frame-navigate` handler that calls `event.preventDefault()` when `!event.isMainFrame && !allowFrameNavigation(event.url)`.
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Check in the running app.** Attach an HTML file containing `<script>location='https://example.com/?x=1'</script>` and confirm its thumbnail and viewer stay on the harness address. Confirm the Knowledge page's memory dashboard still loads and navigates.
- [ ] **Step 6: Commit** `fix: framed bot pages cannot navigate off this machine`.

### Task 6: Comments on a task

**Files:**
- Modify: `src/harness/design_studio.py`, `src/harness/api.py` (`TaskIn`, `start_task` route, `Harness.start_task`, `get_conversation`), `src/harness/conversations.py` (`Exchange`, `Conversations.begin`, `Conversation.history`)
- Test: `tests/test_design_studio.py`, `tests/test_api.py`

**Interfaces:**
- Produces:
  - `class DesignCommentIn(BaseModel)`: `attachment_id: str`; `element_id: str = ""` (max 120); `selector: str` (1 to 300); `tag: str` (1 to 40); `text: str = ""` (max 160); `note: str` (1 to 1000).
  - `TaskIn.design_comments: list[DesignCommentIn]`, at most 20, default empty.
  - `comment_block(file: Path, version: int, comments: list[dict[str, str]]) -> str`.
  - `Exchange.comments: list[dict[str, str]]` (what the chat shows) and `Exchange.addendum: str` (what the bot read). `Conversation.history` gives the user text followed by a blank line and the addendum when there is one. `GET /conversations/{id}` returns `comments` and never `addendum`.
  - `Conversations.begin(..., comments=None, addendum="")`; `Harness.start_task(..., comments=None, addendum="")`. The loop receives `task + "\n\n" + addendum`; the exchange's `user`, the title and the protected task use the user's text alone.

- [ ] **Step 1: Write the failing tests**

```python
def test_comment_block_is_exact(tmp_path):
    f = tmp_path / "designs" / "landing" / "index.html"
    out = comment_block(f, 3, [
        {"element_id": "hero-title", "selector": "body > h1", "tag": "h1", "text": "Ship faster", "note": "Make this bigger"},
        {"element_id": "", "selector": "body > p:nth-of-type(2)", "tag": "p", "text": "", "note": "Cut this"},
    ])
    assert out == (
        f"Comments on the design ({f}, version 3):\n"
        '1. [hero-title] <h1> "Ship faster": Make this bigger\n'
        "2. [body > p:nth-of-type(2)] <p>: Cut this\n"
        "Change only these elements. Leave the rest of the file as it is unless a change requires it, "
        "and say so if it does."
    )
```

In `tests/test_api.py` (studio bot, one design attached as version 1, then version 2):
- a task with one comment on the newest attachment runs; the provider's last user message ends with the block naming `designs/landing/index.html` and `version 2`; `GET /conversations/{cid}` shows the exchange's `user` as the plain text, `comments` with one entry, and no `addendum` key or vault path anywhere in the response text;
- the following task's history contains that block;
- a comment naming the version 1 attachment, an attachment of another conversation, or a non-design attachment: 422 and no new session;
- 21 comments, a 1001-character note, or an empty note: 422.

- [ ] **Step 2: Run** `uv run pytest tests/test_design_studio.py tests/test_api.py -k comment -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The route resolves the conversation (named, else the bot's current), finds its newest attachment for which `revision_of` is not `None`, and refuses any comment whose `attachment_id` differs. The file named in the block is `<vault>/designs/<slug>/<name>`.
- [ ] **Step 4: Run the same command, then** `uv run pytest tests/test_api.py tests/test_conversations.py -v` for regressions (old conversation files without the new fields must still load). Expected: PASS.
- [ ] **Step 5: Commit** `feat: design comments on a task`.

### Task 7: The docked pane, picking and chips

**Files:**
- Create: `src/gui/design.js`, `src/gui/DesignPane.jsx`, `tests/gui/design.test.mjs`
- Modify: `src/gui/api.js` (`startTask`), `src/gui/app.jsx` (bot conversation layout at `:728`, `send`, `start`), `src/gui/styles.css`

**Interfaces:**
- Consumes: Task 3's `design`/`version` on attachments, Task 4's messages and `?studio=1`, Task 6's `design_comments` and `comments`.
- Produces, in `design.js`:
  - `designRevisions(exchanges) -> [{attId, title, design, version}]`, oldest first, only attachments with a `version`.
  - `studioFrameUrl(src) -> string` (adds `studio=1` the way `previewFrameUrl` adds `preview=1`).
  - `acceptPick(event, frameWindow) -> {elementId, selector, tag, text, rect} | null`.
  - `commentsFor(attId, picks) -> [{attachment_id, element_id, selector, tag, text, note}]`, dropping picks with a blank note.
- `api.startTask(botId, task, { ..., designComments })` sends `design_comments` when the list is not empty.

- [ ] **Step 1: Write the failing tests** in `tests/gui/design.test.mjs`

```js
const win = {};
const ok = { zen: 'pick', elementId: 'hero', selector: 'body > h1', tag: 'h1', text: 'Hi', rect: { x: 1, y: 2, w: 3, h: 4 } };
test('a pick is accepted only from the pane\'s own frame and in the exact shape', () => {
  assert.deepEqual(acceptPick({ source: win, data: ok }, win), { elementId: 'hero', selector: 'body > h1', tag: 'h1', text: 'Hi', rect: ok.rect });
  assert.equal(acceptPick({ source: {}, data: ok }, win), null);
  for (const bad of [{ ...ok, extra: 1 }, { ...ok, selector: 'x'.repeat(301) }, { ...ok, text: 'x'.repeat(161) },
    { ...ok, tag: '<img onerror=1>' }, { ...ok, selector: '' }, { ...ok, rect: { x: 1, y: 2, w: 3, h: NaN } },
    { ...ok, elementId: 7 }, null, 'pick'])
    assert.equal(acceptPick({ source: win, data: bad }, win), null);
});
test('revisions come oldest first and skip plain attachments', () => {
  const ex = [{ attachments: [{ id: 'a', title: 'L', design: 'landing', version: 1 }, { id: 'p', title: 'doc' }] },
              { attachments: [{ id: 'b', title: 'L', design: 'landing', version: 2 }] }];
  assert.deepEqual(designRevisions(ex).map(r => r.attId), ['a', 'b']);
});
test('only picks with a note are sent', () => {
  const picks = [{ elementId: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: ' bigger ' }, { elementId: '', selector: 't', tag: 'p', text: '', note: ' ' }];
  assert.deepEqual(commentsFor('b', picks), [{ attachment_id: 'b', element_id: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: 'bigger' }]);
});
```

Add to `tests/gui/api.test.mjs`: `startTask` with `designComments` posts `design_comments`; without, the key is absent.

- [ ] **Step 2: Run** `node --test tests/gui/design.test.mjs tests/gui/api.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement `design.js` and the `api.js` change.** `tag` must match `/^[a-z][a-z0-9-]{0,39}$/`. Run the tests. Expected: PASS.
- [ ] **Step 4: Build `DesignPane.jsx`.** Props: `revisions`, `urlFor(attId)`, `picks`, `onPicks`, `disabled`. It shows only when the bot is `studio` and there is at least one revision, between the chat and the activity panel. It frames the newest revision with `sandbox="allow-scripts"` and `studioFrameUrl`; a menu lists `Version n`; a "Comment" toggle sends `mode`; a "Hide" button collapses it. Picking is enabled only on the newest revision, only after `ready` arrives, and not while a task is live; otherwise the toggle is disabled with the reason as its title. Each accepted pick is appended to `picks` and the pane sends `pins`.
- [ ] **Step 5: Wire `app.jsx`.** Keep `picks` in state, cleared on send and on conversation switch. Above the composer textarea, one row per pick: its number, `[elementId or tag]`, a one-line note input and a remove button. `send` passes `commentsFor(newestAttId, picks)`; with picks present and an empty composer, the message text is `See my comments on the design.`. A sent exchange's `comments` show as chips under the user's message.
- [ ] **Step 6: Build and try it.** `npm run build`, start the harness and the app, ask the designer for a landing page, then pick two elements, note them and send. Confirm the bot's reply changes only those, and the pane moves to the new version. Also confirm at a 900 px wide window that chat and pane are both usable.
- [ ] **Step 7: Commit** `feat: docked design pane with point-and-comment`.

### Task 8: Question forms

**Files:**
- Create: `src/gui/question-form.js`, `src/gui/QuestionForm.jsx`, `tests/gui/question-form.test.mjs`
- Modify: `src/gui/Markdown.jsx` (`Code`, the `Markdown` props), `src/gui/app.jsx` (pass `form` on the newest reply), `src/gui/styles.css`

**Interfaces:**
- Produces:
  - `parseQuestionForm(text: string) -> form | null`. Valid: an object with `id` (1 to 64 characters), optional `title` (max 120), `questions` of 1 to 5 items with unique `id`s, each `{id, label (1 to 200), type}` where `type` is one of `radio`, `checkbox`, `select`, `text`, `textarea`, `range`, `color`; `radio`, `checkbox` and `select` need `options` of 2 to 12 strings; `range` needs numeric `min < max`. Optional `default`, `allow_custom`.
  - `answersMessage(form, values) -> string`: `Form answers (<id>):` then one `- <label>: <value>` line per question; a checkbox's values joined with `, `; a blank answer is `(no answer)`.
  - `<Markdown text attachments form={{ live, onAnswer }}>`: a fenced block with language `question-form` that parses renders as `<QuestionForm>`; `live: false` renders it read-only; anything that does not parse stays a code block.

- [ ] **Step 1: Write the failing tests**

```js
const form = { id: 'brief', title: 'A few questions', questions: [
  { id: 'tone', label: 'Tone', type: 'radio', options: ['Calm', 'Bold'] },
  { id: 'pages', label: 'Sections', type: 'checkbox', options: ['Hero', 'Pricing', 'FAQ'] },
  { id: 'name', label: 'Product name', type: 'text' }] };
test('a valid form parses and answers read back as a message', () => {
  assert.deepEqual(parseQuestionForm(JSON.stringify(form)), form);
  assert.equal(answersMessage(form, { tone: 'Bold', pages: ['Hero', 'FAQ'], name: '' }),
    'Form answers (brief):\n- Tone: Bold\n- Sections: Hero, FAQ\n- Product name: (no answer)');
});
test('anything else stays a code block', () => {
  const q = form.questions;
  for (const bad of ['{', '[]', JSON.stringify({ ...form, questions: [] }), JSON.stringify({ ...form, questions: [q[0], q[0]] }),
    JSON.stringify({ ...form, questions: [{ id: 't', label: 'T', type: 'radio' }] }),
    JSON.stringify({ ...form, questions: [{ id: 't', label: 'T', type: 'file' }] }),
    JSON.stringify({ ...form, questions: Array.from({ length: 6 }, (_, i) => ({ id: `q${i}`, label: 'L', type: 'text' })) })])
    assert.equal(parseQuestionForm(bad), null);
});
```

- [ ] **Step 2: Run** `node --test tests/gui/question-form.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement `question-form.js`.** Run the test. Expected: PASS.
- [ ] **Step 4: Build `QuestionForm.jsx`** with Fluent controls, labels tied to inputs, and one "Send answers" button; `allow_custom` adds an "Other" text field. Wire `Markdown.jsx` and `app.jsx`: only the newest exchange's reply, in a bot conversation with no live task, gets `live: true` and `onAnswer: values => send(answersMessage(form, values))`.
- [ ] **Step 5: Build and try it.** `npm run build`; give the designer a one-line brief, fill in the form, send, and confirm the answers arrive as the next message and the earlier form turns read-only.
- [ ] **Step 6: Commit** `feat: question forms in bot replies`.

### Task 9: Design systems in the harness

**Files:**
- Create: `src/harness/design_systems.py`, `tests/test_design_systems.py`, `design-systems/default/{DESIGN.md,tokens.css}`, `design-systems/warm-editorial/{DESIGN.md,tokens.css}`, `design-systems/NOTICE.txt`
- Modify: `src/harness/config.py` (a `design_systems_dir`, set the way `bot_prompts_dir` is), `src/harness/conversations.py` (`Conversation.design_system: str = ""`), `src/harness/loop.py` (`Session.design: str = ""`; the context build at `:195`), `src/harness/api.py` (routes; `Harness.start_task`)

**Interfaces:**
- Produces:
  - `MAX_CHARS = 40_000`; `SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")`.
  - `@dataclass DesignSystem`: `slug`, `name`, `source` (`"shipped"` or `"yours"`), `summary`, `design: str`, `tokens: str`.
  - `class DesignSystemError(ValueError)`.
  - `available(shipped_dir: Path, vault_root: Path) -> list[dict[str, str]]`: `{slug, name, source, summary}`, sorted by name; a folder in `<vault>/design-systems/` replaces a shipped one with the same slug; unreadable or invalid folders are left out.
  - `load(shipped_dir: Path, vault_root: Path, slug: str) -> DesignSystem`: raises `KeyError` when there is none, `DesignSystemError` with the reason when it is over `MAX_CHARS` or a file resolves outside its own folder.
  - `block(ds: DesignSystem) -> str`: starts `Design system: <name>. This is authoritative: use these tokens; do not invent colours, type or spacing.` then `DESIGN.md`, then `tokens.css` in a fenced `css` block when present.
  - `name` is the first `# ` heading, else the slug; `summary` is the first non-heading, non-blank line, cut to 140 characters.
  - `GET /design-systems`; `PUT /conversations/{conv_id}/design-system` with `{"slug": str | null}`: 404 for an unknown conversation or slug, 422 with the reason for a `DesignSystemError`, 200 `{"design_system": slug or ""}`.
  - On every task in a conversation with a design system that still loads, `session.design = block(ds)` and the loop appends it to the context after the note. One that no longer loads is skipped and the task still runs.

- [ ] **Step 1: Write the failing tests** in `tests/test_design_systems.py`

```python
def make(root, slug, design="# Acme\n\nCalm and plain.\n", tokens=":root { --ink: #111; }"):
    d = root / slug; d.mkdir(parents=True); (d / "DESIGN.md").write_text(design, encoding="utf-8")
    if tokens is not None: (d / "tokens.css").write_text(tokens, encoding="utf-8")
    return d

def test_yours_replaces_shipped_by_slug(tmp_path):
    shipped, vault = tmp_path / "s", tmp_path / "v"
    make(shipped, "default"); make(shipped, "warm"); make(vault / "design-systems", "default", "# Mine\n\nMy brand.\n")
    got = {d["slug"]: d for d in available(shipped, vault)}
    assert got["default"] == {"slug": "default", "name": "Mine", "source": "yours", "summary": "My brand."}
    assert got["warm"]["source"] == "shipped"

def test_block_carries_both_files(tmp_path):
    make(tmp_path / "s", "acme")
    b = block(load(tmp_path / "s", tmp_path / "v", "acme"))
    assert b.startswith("Design system: Acme. This is authoritative")
    assert "Calm and plain." in b and "--ink: #111" in b

def test_too_large_is_refused_with_the_size(tmp_path):
    make(tmp_path / "s", "big", design="# Big\n\n" + "x" * 40_000)
    with pytest.raises(DesignSystemError, match="40,000"):
        load(tmp_path / "s", tmp_path / "v", "big")

def test_a_file_that_points_out_of_its_folder_is_refused(tmp_path):
    secret = tmp_path / "vault-settings" / "providers.json"; secret.parent.mkdir(); secret.write_text("KEY", encoding="utf-8")
    d = tmp_path / "v" / "design-systems" / "sly"; d.mkdir(parents=True)
    try:
        (d / "DESIGN.md").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privilege on this machine")
    assert available(tmp_path / "s", tmp_path / "v") == []
    with pytest.raises(DesignSystemError, match="outside"):
        load(tmp_path / "s", tmp_path / "v", "sly")

@pytest.mark.parametrize("slug", ["../x", "A", "", "a/b", "x" * 65])
def test_bad_slugs_are_unknown(tmp_path, slug):
    with pytest.raises(KeyError):
        load(tmp_path / "s", tmp_path / "v", slug)
```

In `tests/test_api.py`: `GET /design-systems` lists the two shipped starters; `PUT` then a task puts the block in the provider's context message and not in the system message; `PUT` with `null` removes it; unknown slug 404; oversize 422; deleting the folder after picking still lets a task run.

- [ ] **Step 2: Run** `uv run pytest tests/test_design_systems.py tests/test_api.py -k "design_system or design-system" -v`. Expected: FAIL.
- [ ] **Step 3: Implement the module, the fields and the routes.**
- [ ] **Step 4: Write the two starters.** `default` is neutral and modern; `warm-editorial` is a serif, warm-paper system. Each `DESIGN.md` has at least: Overview, Colour, Typography, Spacing and layout, Components, Motion, Voice. They may be adapted from OpenDesign's two starters of the same names (commit `59e4f2a`, Apache-2.0), named in `design-systems/NOTICE.txt`. Each folder must total under 20,000 characters.
- [ ] **Step 5: Check the compaction budget.** Read how `session.context` counts against `ZEN_CONTEXT_BUDGET_CHARS` in `src/context/compaction.py`. If it counts, add a test that a 40,000-character design system does not force a compaction on a fresh conversation, and fix what it finds; if it does not count, say so in the commit message.
- [ ] **Step 6: Run the Step 2 command.** Expected: PASS.
- [ ] **Step 7: Commit** `feat: design systems as per-conversation context`.

### Task 10: The picker, the docs and the full run

**Files:**
- Modify: `src/gui/api.js`, `src/gui/DesignPane.jsx`, `src/gui/app.jsx`, `tests/gui/api.test.mjs`, `AGENTS.md`

**Interfaces:**
- Consumes: Task 9's routes; `design_system` on the conversation record.
- Produces: `api.designSystems()`, `api.setDesignSystem(convId, slug)`.

- [ ] **Step 1: Write the failing test** in `tests/gui/api.test.mjs`: `designSystems()` GETs `/design-systems`; `setDesignSystem('c1', 'default')` PUTs `/conversations/c1/design-system` with `{"slug":"default"}`, and with `null` sends `{"slug":null}`.
- [ ] **Step 2: Run** `node --test tests/gui/api.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement the two calls, then the picker.** For a `studio` bot the chat heading gets a "Design system" dropdown: "None", then each system with `yours` ones first. It shows before any design exists. With no current conversation, choosing one first creates a conversation through the existing new-conversation call. A 422 shows its reason through the app's notice.
- [ ] **Step 4: Update `AGENTS.md`.** Add `design-systems/` and the new modules to the table; add a short "Design studio" section under "What bots can and cannot reach" covering: where designs and their copies live, that the studio preview is `PREVIEW_CSP` plus the bridge, that the GUI never trusts frame messages, that design-system files must resolve inside their own folder, and the Electron navigation block.
- [ ] **Step 5: Run everything.** `uv run pytest` and `npm test` and `npm run build`. Expected: all pass, no new warnings. Then `graft build`.
- [ ] **Step 6: Walk the whole loop in the app.** Pick `warm-editorial`, give a one-line brief, answer the form, get a design, comment on two elements, get version 2, open version 1 from the menu, ask the bot to go back to version 1.
- [ ] **Step 7: Commit** `feat: design system picker; document the design studio`.
