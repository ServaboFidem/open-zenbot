"""Browser recorder that drafts a skill.

The recorder drives a browser the harness controls, headed, and records
what the person does as actions by intent: navigation, clicks, typed
input by field name, selections, plus a trace. It never reads the host
screen. Every captured field value passes the exclusion filter; a match
is stored as a vault handle name (`vault:FIELD`), never the value, and
the value is registered so it is redacted everywhere else too. The
length guide is config. A draft writes a SKILL.md through the same
loader the vertical slice uses, with steps that read by intent, and
reports the loader's states and reasons. Replay runs the draft headless
on whatever provider is given: the mock offline, OpenRouter with a key.

The browser is behind a small driver protocol. Playwright is the real
driver and is imported lazily; tests use a fake. Install it with
`uv pip install playwright && playwright install chromium`.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

import yaml

from src.memory.exclusion import exclusion

from .loader import SKILL_FILENAME, LoadResult, Reason, Skill, SkillConfig, SkillState, _slugify, load_skill
from .recording import ACTION_KINDS, Action, Recording, _now

OnAction = Callable[..., Any]


class RecorderError(RuntimeError):
    pass


class BrowserDriver(Protocol):
    kind: str

    async def start(self, on_action: OnAction, *, headed: bool) -> None: ...
    async def stop(self) -> list[dict[str, Any]]: ...
    async def run(self, action: Action) -> str: ...


@dataclass
class RecorderConfig:
    max_seconds: float = field(default_factory=lambda: float(os.environ.get("ZEN_RECORD_MAX_S", "600")))
    headed: bool = field(default_factory=lambda: os.environ.get("ZEN_RECORD_HEADED", "1") not in ("0", "false", "no"))


SECRET_TOKENS = {
    "password", "passwd", "passphrase", "passcode", "pass", "pwd", "pw", "secret", "token", "apikey", "api_key",
    "otp", "pin", "credential", "credentials", "auth_token", "access_token", "client_secret", "private_key", "cvv", "ssn",
}
JOINED_SECRET = {"apikey", "accesstoken", "authtoken", "clientsecret", "privatekey", "secretkey", "apitoken"}


def field_tokens(field_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", field_name.lower()) if t]


def is_secret_field(field_name: str) -> bool:
    """Whole tokens only: `password`, `pwd`, `api key` are secret fields;
    `author` and `oauth_provider` are not."""
    toks = field_tokens(field_name)
    joined = "".join(toks)
    return any(t in SECRET_TOKENS for t in toks) or joined in JOINED_SECRET or ("api" in toks and "key" in toks)


def is_secret(field_name: str, value: str, *, flagged: bool = False) -> bool:
    """A value is a secret when the browser flagged the field (type=password),
    the exclusion filter matches the value, or the field is named like one."""
    return flagged or bool(exclusion.find(value)) or is_secret_field(field_name)


def clean_url(url: str) -> str:
    """Drop query and fragment: that is where tokens travel."""
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, u.path, "", "")) if u.scheme else url


def clean_text(text: str) -> str:
    return exclusion.redact(text)[0] if text else text


def vault_handle(field_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", field_name).strip("_").upper() or "FIELD"


class Recorder:
    def __init__(self, driver: BrowserDriver, ledger: Any, config: RecorderConfig | None = None) -> None:
        self.driver = driver
        self.ledger = ledger
        self.config = config or RecorderConfig()
        self.recording: Recording | None = None
        self.last: Recording | None = None
        self._t0 = 0.0
        self._url = ""

    @property
    def state(self) -> str:
        return "recording" if self.recording is not None else "idle"

    async def start(self, name: str) -> Recording:
        if self.recording is not None:
            raise RecorderError("already recording")
        self.recording = Recording(name=name, max_s=self.config.max_seconds)
        self._t0, self._url = time.monotonic(), ""
        await self.driver.start(self.on_action, headed=self.config.headed)
        self.ledger.append("recording", "user", self.recording.id, event="started", name=name, driver=self.driver.kind,
                           headed=self.config.headed, max_s=self.config.max_seconds)
        return self.recording

    def on_action(self, kind: str, target: str, value: str | None = None, intent: str = "", url: str = "",
                  secret: bool = False) -> Action | None:
        """Called by the driver (or the API) for each thing the person did."""
        rec = self.recording
        if rec is None:
            raise RecorderError("not recording")
        if kind not in ACTION_KINDS:
            raise RecorderError(f"unknown action kind {kind!r}")
        at = time.monotonic() - self._t0
        if at > rec.max_s:
            if not rec.truncated:
                rec.truncated = True
                self.ledger.append("recording", "user", rec.id, event="truncated", max_s=rec.max_s)
            return None
        if value is not None and is_secret(target, value, flagged=secret):
            exclusion.register_secret_value(value)
            value = f"vault:{vault_handle(target)}"
            rec.redactions += 1
        # everything else that gets persisted goes through the filter too
        target = clean_url(target) if kind == "navigate" else clean_text(target)
        intent, url = clean_text(intent), clean_url(url) if url else url
        if kind == "navigate":
            self._url = target
        action = Action(kind=kind, target=target, value=value, intent=intent, url=url or self._url, at=round(at, 3))
        rec.actions.append(action)
        return action

    async def stop(self) -> Recording:
        rec = self.recording
        if rec is None:
            raise RecorderError("not recording")
        rec.trace = list(await self.driver.stop() or [])
        rec.duration_s = round(time.monotonic() - self._t0, 3)
        rec.stopped_at = _now()
        self.recording, self.last = None, rec
        self.ledger.append("recording", "user", rec.id, event="stopped", source=rec.source, actions=len(rec.actions),
                           redactions=rec.redactions, truncated=rec.truncated, duration_s=rec.duration_s)
        return rec

    def status(self) -> dict[str, Any]:
        rec = self.recording
        return {
            "state": self.state, "driver": self.driver.kind, "headed": self.config.headed, "max_s": self.config.max_seconds,
            "name": rec.name if rec else None, "actions": len(rec.actions) if rec else 0,
            "elapsed_s": round(time.monotonic() - self._t0, 3) if rec else 0.0,
            "redactions": rec.redactions if rec else 0, "truncated": rec.truncated if rec else False,
            "last": self.last.id if self.last else None,
        }


# -- draft -------------------------------------------------------------------


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def step_for(a: Action) -> str:
    if a.kind == "navigate":
        u = urlsplit(a.target)
        return f"Open {u.netloc}{u.path}".rstrip("/") if u.netloc else f"Open {a.target}"
    if a.intent:
        return _cap(a.intent.strip())
    if a.kind == "click":
        return f"Click {a.target}"
    if a.kind == "type":
        return f"Enter {a.target}"
    if a.kind == "select":
        return f"Choose {a.value} in {a.target}"
    return "Wait for the page"


def steps_by_intent(recording: Recording) -> list[str]:
    out: list[str] = []
    for a in recording.actions:
        s = step_for(a)
        if not out or out[-1] != s:
            out.append(s)
    return out


def render_skill_md(recording: Recording, *, name: str, use: str) -> str:
    inputs = [{a.target: a.value} for a in recording.actions if a.kind in ("type", "select") and a.value is not None]
    meta: dict[str, Any] = {
        "name": name,
        "slug": _slugify(name),
        "use": use,
        "source": recording.source,
        "recording": recording.id,
        "inputs": inputs,
        "access": ["browser"],
        "steps": steps_by_intent(recording),
        "validation": ["Every step completes in the browser without an error.", "The final page shows the expected result."],
        "return": "A short note of what the browser showed at the end.",
    }
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000).rstrip("\n")
    lines = [f"# {name}", "", "Recorded from a driven browser. Replay these actions in order with the browser tool.", "", "## Actions", ""]
    for a in recording.actions:
        val = f" = {a.value}" if a.value is not None else ""
        lines.append(f"- {step_for(a)}: `{a.kind}` `{a.target}`{val}")
    return f"---\n{head}\n---\n" + "\n".join(lines) + "\n"


def draft_skill(
    recording: Recording,
    *,
    name: str,
    use: str,
    skills_dir: Path,
    catalog: Any | None = None,
    config: SkillConfig | None = None,
    taken: frozenset[str] | set[str] = frozenset(),
) -> LoadResult:
    """Write the draft and read it back through the loader. States and
    reasons are the loader's own: nothing/empty, nothing/too-large,
    nothing/unsupported, nothing/too-many-skills, failed/invalid,
    staged (on disk), added (in the catalog)."""
    config = config or SkillConfig()
    slug = _slugify(name)
    base, n = name, 1
    while slug in taken:  # a slug in `taken` (a shipped skill's) is never reused: "Name 2", "Name 3"...
        n += 1
        name = f"{base} {n}"
        slug = _slugify(name)
    path = Path(skills_dir) / slug / SKILL_FILENAME
    if recording.source != "browser-actions":
        return LoadResult(SkillState.nothing, Reason.unsupported, path=path, detail=recording.source)
    if not recording.actions:
        return LoadResult(SkillState.nothing, Reason.empty, path=path, detail="no actions recorded")
    if catalog is not None and len(catalog.skills()) >= config.max_per_bot:
        return LoadResult(SkillState.nothing, Reason.too_many_skills, path=path, detail=f"{len(catalog.skills())} >= {config.max_per_bot}")
    text = render_skill_md(recording, name=name, use=use)
    size = len(text.encode("utf-8"))
    if size > config.max_bytes:
        return LoadResult(SkillState.nothing, Reason.too_large, path=path, detail=f"{size} > {config.max_bytes}")
    if path.exists() and not _recorder_made(path):
        return LoadResult(SkillState.nothing, Reason.duplicate, path=path, detail="a skill that was not recorded already lives there")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    res = load_skill(path, config)
    if not res.ok:
        return res
    res.skill.source = "recorder"
    if catalog is not None:
        catalog.remove_skill(res.skill.slug)
        added = catalog.add_skill(res.skill)
        res.state, res.reason = added.state, added.reason
    else:
        res.state = SkillState.staged
    return res


def _recorder_made(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:2000]
    except OSError:
        return False
    return "source: browser-actions" in head


# -- replay -------------------------------------------------------------------

BROWSER_TOOL = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTION_KINDS)},
        "target": {"type": "string", "description": "url for navigate; selector or field name otherwise"},
        "value": {"type": "string", "description": "text to type or option to select"},
    },
    "required": ["action", "target"],
}


async def replay(skill: Skill, provider: Any, driver: BrowserDriver, ledger: Any, *, cwd: Path, max_steps: int = 20) -> Any:
    """Run the draft headless: a bot with only this skill and a browser tool."""
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import Bot
    from src.harness.loop import TaskLoop
    from src.harness.tools import ToolRunner

    await driver.start(lambda *a, **k: None, headed=False)
    tools = ToolRunner(cwd)

    async def browser(action: str, target: str, value: str | None = None) -> str:
        return await driver.run(Action(kind=action, target=target, value=value))

    tools.register("browser", "Drive the headless browser: navigate, click, type, select, wait.", BROWSER_TOOL, browser)
    bot = Bot(name="Replay", role="Replay a recorded browser skill exactly as its steps say, using the browser tool.", id="replay")
    bot.attach_skill(skill)
    loop = TaskLoop(provider, ledger, ApprovalStore(), tools, max_steps=max_steps)
    try:
        return await loop.run(bot, f"Replay the skill {skill.name!r} step by step with the browser tool, then report what you saw.")
    finally:
        await driver.stop()


# -- playwright ---------------------------------------------------------------

INIT_JS = """
(() => {
  const name = el => el.getAttribute('name') || el.getAttribute('aria-label') || el.id || el.getAttribute('placeholder') || el.tagName.toLowerCase();
  const sel = el => el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).trim().split(/\\s+/).join('.') : '');
  document.addEventListener('click', e => { const t = e.target.closest('a,button,[role=button],input[type=submit]') || e.target;
    window.__zen_action('click', sel(t), null, (t.innerText || t.value || '').trim().slice(0, 60), false); }, true);
  document.addEventListener('change', e => { const t = e.target; if (!t) return;
    const secret = t.type === 'password' || /password|one-time-code/.test(t.autocomplete || '');
    if (t.tagName === 'SELECT') window.__zen_action('select', name(t), t.value, '', false);
    else if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') window.__zen_action('type', name(t), t.value, '', secret); }, true);
})();
"""


class PlaywrightDriver:
    """Headed Chromium through Playwright. Actions arrive through an
    exposed binding; navigation through the page event. Nothing reads
    pixels: no screenshots, no video."""

    kind = "playwright"

    def __init__(self) -> None:
        self._pw = self._browser = self._page = None
        self._on_action: OnAction | None = None
        self._trace: list[dict[str, Any]] = []
        self._t0 = 0.0

    @staticmethod
    def available() -> bool:
        try:
            import playwright.async_api  # noqa: F401
        except ImportError:
            return False
        return True

    async def start(self, on_action: OnAction, *, headed: bool) -> None:
        from playwright.async_api import async_playwright

        self._on_action, self._trace, self._t0 = on_action, [], time.monotonic()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=not headed)
        self._page = await self._browser.new_page()

        async def binding(source, kind, target, value, intent, secret=False):
            self._trace.append({"t": round(time.monotonic() - self._t0, 3), "event": kind, "target": clean_text(target)})
            self._on_action(kind, target, value if value else None, intent or "", source["page"].url, bool(secret))

        await self._page.expose_binding("__zen_action", binding)
        await self._page.add_init_script(INIT_JS)
        self._page.on("framenavigated", lambda frame: frame.parent_frame is None and self._nav(frame.url))

    def _nav(self, url: str) -> None:
        self._trace.append({"t": round(time.monotonic() - self._t0, 3), "event": "navigate", "target": clean_url(url)})
        if self._on_action:
            self._on_action("navigate", url, None, "", url)

    async def stop(self) -> list[dict[str, Any]]:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = self._pw = self._page = None
        return list(self._trace)

    async def run(self, action: Action) -> str:
        page = self._page
        if page is None:
            raise RecorderError("browser not started")
        if action.kind == "navigate":
            await page.goto(action.target)
        elif action.kind == "click":
            await page.click(action.target)
        elif action.kind == "type":
            await page.fill(_field_selector(action.target), action.value or "")
        elif action.kind == "select":
            await page.select_option(_field_selector(action.target), action.value or "")
        elif action.kind == "wait":
            await page.wait_for_load_state()
        return f"ok {action.kind} {action.target} @ {page.url}"


def _field_selector(name: str) -> str:
    if name.startswith(("#", ".", "[")) or " " in name or ">" in name:
        return name
    return f"[name={name!r}], #{name}, [aria-label={name!r}], [placeholder={name!r}]"
