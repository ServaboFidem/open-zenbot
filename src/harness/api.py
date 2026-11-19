"""Local HTTP API. The GUI boundary.

Loopback only. Never returns a key value: the provider is the only object
that holds one, and nothing serializes the provider.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import base64
import binascii
import hashlib
import json
import re
import secrets
import shutil
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi import Request
from starlette.background import BackgroundTask
from uuid import uuid4
from pydantic import BaseModel, Field

from src.decider.fit import FitConfig, FitEngine
from src.decider.interface import BandConfig, ConfirmState, Scorer
from src.decider.router import CatalogRouter, RouterConfig
from src.memory.dream import DreamMarks, Dreamer
from src.memory.exclusion import ExclusionError, exclusion
from src.memory.graph import SHARED, MemoryGraph, SessionCache, Tier, make_store
from src.memory.import_dir import DirImporter, ScanStore, scan_dir, scan_file, unchanged_in
from src.memory.ingest import CompositeConverter, IngestError, Ingestor, MarkdownifyConverter, MarkitdownConverter, PlainConverter
from src.memory.learn import Learner
from src.memory.ledger import Kind, Ledger, ledger_path_for
from src.memory.recall import MemoryRecall, RecallConfig
from src.memory.warmup import Warmup
import os

import src.decider.adapters.decision_model  # noqa: F401 - registers the built-in scorer kind
import src.providers.openrouter  # noqa: F401 - registers the built-in provider kind
from src.providers import registry as providers
from src.decider import registry as scorers
from src.providers.interface import Provider, ProviderAuthError, ProviderError, UnboundProvider
from src.skills.catalog import Catalog
from src.skills.loader import SkillConfig

from .approvals import ApprovalStore
from .bot import Bot
from .bot_prompts import BotPrompt, save as save_bot_prompt
from . import mcp_keys
from .host_deps import refresh_windows_path
from .mcp_client import McpRegistry, load_config as load_mcp_config
from .mcp_keys import McpKeys
from . import telemetry
from . import allowed_folders, tracing_settings
from .allowed_folders import AllowedFolders
from .magick_guard import Folders as GuardFolders, MagickGuard, MagickRunner
from .write_guard import ShellWriteCheck
from . import agent_clis, design_images, design_parts, design_studio, design_systems, studio_help, studio_helpers, tool_paths
from .studio_helpers import PartTurn
from .agent_clis import Agents
from .delegation import Delegator, Runner as DelegateRunner
from src.vault_settings import settings_dir
from src.providers import tracing as llm_tracing
from .onboarding import ASSISTANT_ID, NEW_TOPICS, TOPICS, Interviewer
from .config import HarnessConfig
from .conversations import Conversation, ConversationStore, Conversations
from .dream_routes import dream_router
from .loop import Session, SessionState, TaskLoop
from .scheduler import Routine, RoutineError, RoutineStore, Schedule, Scheduler, next_run, parse_schedule
from .media import MediaConsent, MediaProxy, MediaRefused, trusted_hosts
from .pause import PauseController
from src.context.compaction import Compactor
from src.context.protected import ProtectedBlock, ScopeGuard
from src.decider.supervisor import LoopSupervisor
from src.generation.generator import Generator
from src.events.discover_routes import discover_router
from src.events.routes import calendar_router
from src.events.service import CalendarService
from src.events.discovery import MANAGED_BY as EVENTS_MANAGED_BY, Discovery
from src.music.routes import music_router
from src.music.service import MANAGED_BY as MUSIC_MANAGED_BY, MusicService
from src.generation.images import DEFAULT_MODEL as DEFAULT_IMAGE_MODEL, ImageConfig, ImageGenerator
from src.providers.models import ModelInfo, ModelLister, guess_kinds
from src.providers.probe import ProbeJobs
from src.providers.openrouter import OpenRouterHttp
from src.providers.secrets import Secret
from src.providers.store import KINDS as PROVIDER_KINDS, OPENROUTER_BASE, PRESETS as PROVIDER_PRESETS, ProviderStore, ProviderStoreError
from .model_roles import OPTIONAL_ROLES, ROLE_LABELS, ROLES, Choice, ModelRoles, load_bot_models, save_bot_models
from .models_setup import build as build_models, check_choice, default_models, dream_provider, make_provider, roles_from_config
from .stubs import InjectAllSelector
from src.rooms.orchestrator import Orchestrator
from src.rooms.room import Attachment, RoomConfig, RoomError, RoomService, workspace_file
from src.rooms.store import RoomStore
from src.skills.recorder import PlaywrightDriver, Recorder, RecorderError, draft_skill
from .tools import ToolRunner
from .turn_notes import TurnNotes, outcome_of

# A bot-made page shown as a thumbnail: its own inline script, style and images run; nothing loads from anywhere.
PREVIEW_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data: blob:; font-src data:")
OPEN_POLICY = "sandbox allow-scripts"  # an opened page: off this origin, but its scripts may reach the network
# every type a browser will run as a page (script, or markup that can carry it): served under PREVIEW_CSP unless opened
CLOSED_SUFFIXES = (".html", ".htm", ".xhtml", ".xht", ".shtml", ".xml", ".svg", ".svgz", ".mht", ".mhtml")


class OpenTokens:
    """Single-use tokens that open one bot-made page under OPEN_POLICY. Only the viewer asks for one, on the
    user's click; a page served closed cannot reach the harness to ask. In memory: a restart drops them all."""

    def __init__(self, ttl: float = 60.0, cap: int = 256) -> None:
        self.ttl, self.cap = ttl, cap
        self.clock: Callable[[], float] = time.monotonic
        self._tokens: OrderedDict[str, tuple[tuple[str, str, str], float]] = OrderedDict()

    def issue(self, key: tuple[str, str, str]) -> str:
        """A token for one attachment: `key` is (route kind, owner id, attachment id)."""
        token = secrets.token_urlsafe(24)
        self._tokens[token] = (key, self.clock())
        while len(self._tokens) > self.cap:
            self._tokens.popitem(last=False)  # the oldest goes first
        return token

    def redeem(self, token: str, key: tuple[str, str, str]) -> bool:
        """Consumes `token` whatever it names: a page that reloads with a spent token is served closed."""
        found = self._tokens.pop(token, None)
        return found is not None and found[0] == key and self.clock() - found[1] <= self.ttl

if TYPE_CHECKING:  # built only on a store that can attach
    from src.memory.extract_queue import ExtractQueue
    from src.memory.profile import ProfileMemory

OWNER_FACT = re.compile(r"The user's name is (.+)\.")  # the old interview's name fact (source "onboarding:name")


@dataclass
class Harness:
    config: HarnessConfig
    provider: Provider | None  # None -> UnboundProvider until Settings binds one
    ledger: Ledger
    catalog: Catalog
    scorer: Scorer | None = None  # None -> inject-all selector, no fit engine
    approvals: ApprovalStore = field(default_factory=ApprovalStore)
    tools: ToolRunner | None = None
    pause: PauseController | None = None
    router: CatalogRouter | None = None
    fit: FitEngine | None = None
    loop: TaskLoop | None = None
    compactor: Compactor | None = None
    supervisor: LoopSupervisor | None = None
    rooms: RoomService | None = None
    room_store: RoomStore | None = None  # built with the first RoomService; a rebind reuses it
    conversations: Conversations | None = None  # each bot's running threads of tasks; a rebind reuses it
    turn_notes: TurnNotes | None = None  # each bot's note to its next turn in a conversation (one-to-one and rooms)
    orchestrator: Orchestrator | None = None  # routes ZenBot messages into topic rooms; needs a scorer
    memory: MemoryGraph | None = None
    cache: SessionCache = field(default_factory=SessionCache)
    recall: MemoryRecall | None = None
    ingestor: Ingestor | None = None
    importer: DirImporter | None = None  # directory imports; each run gets its own Ingestor scoped to the picked dir
    interviews: Interviewer | None = None  # the onboarding interview
    scans: ScanStore = field(default_factory=ScanStore)
    markdownify: Any = None  # async call(tool_name, filepath) -> markdown, bound to the markdownify MCP when connected
    mcps: McpRegistry | None = None  # built in the app lifespan from config.mcp_config
    recorder: Recorder | None = None
    recorder_driver: Any = None  # a BrowserDriver; None -> Playwright when installed
    recorder_autodetect: bool = True
    bots: dict[str, Bot] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    scope_guards: dict[str, ScopeGuard] = field(default_factory=dict)
    warmup: Warmup | None = None  # the memory store's cold start, run as a task when the app starts
    # the owner's profile (people and org registries) is built on every memory backend; the background
    # extraction queue only on a store that can attach, so it is None on the local JSON store, where shared
    # writes go straight to memory as they always did
    profile: ProfileMemory | None = None
    extract_queue: ExtractQueue | None = None
    learner: Learner | None = None  # the learn_page tool: a fetched page teaches memory what touches a known org, person, or product
    images: ImageGenerator | None = None  # the image.generate tool; the decider offers it like an MCP server
    agents: Agents | None = None  # Claude Code and Codex: where they are, sign-in, switches (agent_clis.py)
    delegator: Delegator | None = None  # the delegate tool: hands a job to an agent that is on; a rebind keeps it
    music: MusicService | None = None  # music discovery: settings, Discogs token, atlas, digests, the /music routes
    calendar: CalendarService | None = None  # the built-in calendar: its store, Google sync, the /calendar routes
    discover: Discovery | None = None  # event discovery: the Eventbrite pool, the scout's tools and routine, the digests
    dreamer: Dreamer | None = None  # dreaming: learns facts from the user's own messages while idle; built once, a rebind keeps it
    summary_provider: Provider | None = None  # runs the summary model when it lives on another provider than chat; None: the chat provider
    provider_store: ProviderStore | None = None  # vault-settings/providers.json: providers, their keys, their enabled models
    model_lister: ModelLister = field(default_factory=ModelLister)
    probes: ProbeJobs = field(default_factory=ProbeJobs)  # one model capability test at a time (id-only providers)
    openrouter_ref: str | None = None  # the key ref for what only OpenRouter serves (Hindsight's LLM); None: config.key_ref
    bot_models: dict[str, Choice] | None = None  # bot id -> its own model (Edit bot), from vault-settings/bot-models.json
    _bot_providers: dict[str, tuple[tuple, Provider]] = field(default_factory=dict, init=False, repr=False)  # provider id -> (its settings, a client)
    _dream_gen: tuple[tuple, Generator] | None = field(default=None, init=False, repr=False)  # (the dream choice's settings, its generator)
    media: MediaProxy | None = None  # remote images, video and PDFs for the chat; trusted hosts at once, others on a click
    task_attachments: dict[str, list[Attachment]] = field(default_factory=dict)  # session id -> files attached in a one-to-one task
    part_turns: dict[str, PartTurn] = field(default_factory=dict)  # session id -> a helper's turn on a design: its worksheet and changes
    _warm_tasks: set = field(default_factory=set, init=False, repr=False)
    _last_routed_at: datetime | None = field(default=None, init=False)  # last /orchestrate that put a message in a room

    def __post_init__(self) -> None:
        c = self.config
        self.provider = self.provider or UnboundProvider()
        if self.provider_store is None:
            self.provider_store = ProviderStore(c.vault_root).load()
            self.provider_store.seed(c.key_ref, default_models(c))
        if self.bot_models is None:
            self.bot_models = load_bot_models(c.vault_root)
        summaries = self.summary_provider or self.provider
        if self.tools is None:
            c.shell_cwd.mkdir(parents=True, exist_ok=True)  # <vault>/workspace by default: the bots' working folder
            self.tools = ToolRunner(c.shell_cwd, c.shell_timeout)
        self.pause = self.pause or PauseController(self.ledger, self.provider)
        self.media = self.media or MediaProxy(trusted_hosts())
        if self.scorer is not None:
            band = BandConfig(c.band_width, c.band_k_skill, c.low_confidence)
            self.router = self.router or CatalogRouter(
                self.scorer, self.catalog, self.ledger,
                RouterConfig(band, {"skill": c.band_k_skill, "mcp": c.band_k_mcp}, c.router_fail_open, {"mcp": c.mcp_floor}),
            )
            self.fit = self.fit or FitEngine(self.scorer, self.ledger, FitConfig(c.fit_tie_width, max_per_bot=c.max_skills_per_bot))
            self.supervisor = self.supervisor or LoopSupervisor(self.scorer, self.ledger, self.pause)
        self.compactor = self.compactor or Compactor(Generator(summaries, model=c.summary_model), self.ledger)
        selector = self.router or InjectAllSelector()
        self.memory = self.memory or MemoryGraph(make_store(c.vault_root / 'memory'), self.ledger)
        summarizer = self.ingestor.generator if self.ingestor else Generator(summaries, model=c.summary_model)
        if self.profile is None:
            self.profile, self.extract_queue = self._profile_memory(summarizer.chunk_chars)
        self.recall = self.recall or MemoryRecall(self.memory, self.cache, self.scorer, self.ledger, RecallConfig())
        self.recall.profile, self.recall.extract_queue = self.profile, self.extract_queue
        self.warmup = self.warmup or Warmup(self.memory, self.ledger, bound=lambda: self.bound)
        if self.ingestor is None:
            converters: list[Any] = [PlainConverter(), MarkitdownConverter()] + ([MarkdownifyConverter(self.markdownify)] if self.markdownify else [])
            self.ingestor = Ingestor(c.vault_root, CompositeConverter(converters), summarizer, self.memory, self.ledger,
                                     extract_queue=self.extract_queue)
        if self.learner is None:
            self.learner = Learner(c.vault_root, summarizer, self.profile, self.extract_queue, self.approvals, self.ledger,
                                   bound=lambda: self.bound)
        else:  # __post_init__ runs again on a rebind (new provider, same ToolRunner) and when the markdownify MCP connects
            self.learner.generator = summarizer
        self.learner.register(self.tools)
        if self.importer is None:
            harness = self  # the lifespan rebuilds self.ingestor once the MCPs connect: resolve it per import, not at build time

            def make_ingestor(allowed: list[Path]) -> Ingestor:
                shared = harness.ingestor
                return Ingestor(c.vault_root, shared.converter, shared.generator, shared.graph, harness.ledger, sync_log=shared.sync_log,
                                allowed=allowed, offload=True, extract_queue=shared.extract_queue)

            self.importer = DirImporter(make_ingestor, self.ledger)
        if self.agents is None:
            self.agents = Agents(c.vault_root)
        if self.delegator is None:  # kept across a rebind, so a run in flight stays stoppable
            self.delegator = Delegator(c.vault_root, self.agents, self.reach,
                                       DelegateRunner(self.agents, self.ledger, timeout=c.delegate_timeout),
                                       in_routine=self._in_routine, conversation_of=self._conversation_of)
        self.loop = self.loop or TaskLoop(
            self.provider, self.ledger, self.approvals, self.tools, self.pause, selector=selector, max_steps=c.max_steps,
            compactor=self.compactor, supervisor=self.supervisor, recall=self.recall, model_for=self._model_for,
            write_check=ShellWriteCheck(lambda: self.scorer, self.reach, self.tools.cwd, self.ledger, c.shell_write_pass),
            delegate_check=self.delegator.needs_approval,
        )
        self.interviews = self.interviews or Interviewer(self.provider, self.memory, self.ledger, model=c.model,
                                                       profile=self.profile, extract_queue=self.extract_queue)
        if self.rooms is None:
            first = self.room_store is None
            self.room_store = self.room_store or RoomStore(ledger_path_for(c.vault_root) / "rooms")
            self.rooms = RoomService(self.ledger, self.loop, self.bots, RoomConfig(), sessions=self.sessions, tasks=self.tasks,
                                     store=self.room_store)
            if first:  # a rebind carries the room dict across instead of reloading
                self.rooms.load()
        if self.conversations is None:
            self.conversations = Conversations(ConversationStore(ledger_path_for(c.vault_root) / "conversations"), self.ledger)
            self.conversations.load()
        self.rooms.attachable = self.attachable
        self.rooms.attach_target = self.attach_target
        self.rooms.register_tools(self.tools, elsewhere=self.attach)  # idempotent: a rebind re-registers them on the new rooms
        if self.turn_notes is None:
            self.turn_notes = TurnNotes(self.compactor.generator, self.ledger)
        self.turn_notes.generator = self.compactor.generator  # the summary model writes notes too
        self.rooms.notes = self.turn_notes
        if self.images is None:
            self.images = ImageGenerator(ImageConfig.load(c.vault_root), self.tools.cwd, key_ref=self._image_key_ref,
                                         bound=lambda: self._image_key_ref() is not None, ledger=self.ledger,
                                         provider_kind=self._provider_kind)
            if self.images.config.enabled and self.images.provider is None:
                print(f"image generation off: unknown image provider {self.images.config.provider!r}", flush=True)
        self.images.workspace = self.tools.cwd
        self.images.attach = self.tools.fn("attach_file")
        self.images.register(self.tools, self.catalog)  # idempotent, like the room tools
        self.delegator.register(self.tools)  # idempotent; offered only while an agent is on
        self.tools.register(  # idempotent; offered only to a studio bot in its own design conversation
            "design_add_image",
            "Put a picture in this design's images folder so the page can show it. Give the path of a PNG, JPEG, "
            "WebP or GIF you may read (an upload, or a file in the vault or an allowed folder). The answer is the "
            "name to use: reference it as <img src=\"images/<name>\"> with the quotes, and never write a picture "
            "as data: text.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "the picture: a full path, or relative to your working folder"},
                "name": {"type": "string", "description": "optional name for the picture; it is made lower case and kept safe"},
            }, "required": ["path"]},
            self._design_add_image_tool,
            offered=lambda _bot, session_id: (self._studio_session(session_id) is not None
                                              or self._part_turn(session_id, "images") is not None),
        )
        self.tools.register(  # idempotent; offered only to a helper's turn on a design's text
            "design_set_text",
            "Set the text of one element on your list in the design. Give its id (the word in brackets) and the new "
            "text, as plain text: no HTML. A later call for the same id replaces the earlier one. The design is "
            "changed when you finish.",
            {"type": "object", "properties": {
                "id": {"type": "string", "description": "the element's id from your list, without the brackets"},
                "text": {"type": "string", "description": "the new text, plain"},
            }, "required": ["id", "text"]},
            self._design_set_text_tool,
            offered=lambda _bot, session_id: self._part_turn(session_id, "text") is not None,
        )
        self.tools.register(  # idempotent; offered only to a helper's turn on a design's pictures
            "design_set_image",
            "Set the picture of one element on your list in the design. Give its id (the word in brackets) and the "
            "path of a PNG, JPEG, WebP or GIF: one already in the design's images folder, or one you may read, which "
            "is copied in. Optionally give new alt text, plain. A later call for the same id replaces the earlier "
            "one. The design is changed when you finish.",
            {"type": "object", "properties": {
                "id": {"type": "string", "description": "the element's id from your list, without the brackets"},
                "path": {"type": "string", "description": "the picture: a full path, designs/<folder>/images/<name>, "
                                                          "or relative to your working folder"},
                "alt": {"type": "string", "description": "optional new alt text, plain"},
            }, "required": ["id", "path"]},
            self._design_set_image_tool,
            offered=lambda _bot, session_id: self._part_turn(session_id, "images") is not None,
        )
        if self.music is None:
            self.music = MusicService(c, self.ledger, self.memory, self.tools, self.catalog)
        self.music.register()  # the music tools appear only when music is on and the token is set
        if self.calendar is None:
            self.calendar = CalendarService(c, self.ledger)  # no network until Google is connected
        if self.discover is None:
            self.discover = Discovery(c, self.ledger, self.calendar)
            self.discover.tools, self.discover.catalog = self.tools, self.catalog
            self.discover.bot_lookup = lambda bot_id: self.bots.get(bot_id)
        self.discover.register()  # the scout's tools appear only while discovery is on with a place set
        if self.dreamer is None:  # once: a rebind keeps it, and what it asks for is read from the harness at call time
            self.dreamer = Dreamer(vault_root=c.vault_root, marks=DreamMarks(ledger_path_for(c.vault_root) / "dreams.json"),
                                   ledger=self.ledger, conversations=self._dream_conversations,
                                   generator=self._dream_generator, profile=lambda: self.profile,
                                   queue=lambda: self.extract_queue, busy=self.busy)
        if self.scorer is not None:
            self.orchestrator = self.orchestrator or Orchestrator(
                self.scorer, self.rooms, self.bots, self.ledger, Generator(summaries, model=c.summary_model).room_topic, ASSISTANT_ID)
            self.rooms.on_lead_post = self.orchestrator.refresh_topic
        if self.recorder is None:
            driver = self.recorder_driver
            if driver is None and self.recorder_autodetect and PlaywrightDriver.available():
                driver = PlaywrightDriver()
            self.recorder = Recorder(driver, self.ledger) if driver is not None else None

    def _profile_memory(self, chunk_chars: int) -> tuple[ProfileMemory, ExtractQueue | None]:
        """The registries sit under `<memory>/<backend>/` (`hindsight/` on the Hindsight store;
        `local/` on the JSON store). The queue's pending
        file sits with them, and the queue exists only on a store that can attach (Hindsight): the
        local store has no graph extraction to run. The routing call uses the
        extraction model (spec 6a), named without the `openrouter/` prefix
        litellm needs, through the harness provider; a store without a
        config falls back to the summary model."""
        from src.memory.extract_queue import ExtractQueue, vault_text
        from src.memory.profile import ProfileMemory
        from src.memory.registry import OrgRegistry, PeopleRegistry
        from src.memory.router import StoreRouter

        store = self.memory.store
        root = store.root / self.memory.kind
        if self.memory.kind == "hindsight":
            self._carry_over_registries(store.root / "cognee", root)  # the old cognee data folder
        llm_model = getattr(getattr(store, "config", None), "llm_model", None)
        model = llm_model.removeprefix("openrouter/") if llm_model else self.config.summary_model
        router = StoreRouter(self.summary_provider or self.provider, model, ledger=self.ledger, chunk_chars=chunk_chars)
        profile = ProfileMemory(self.memory, PeopleRegistry(root / "people.json"), OrgRegistry(root / "orgs.json"), router, self.ledger)
        if hasattr(store, "owner_source"):
            store.owner_source = profile.people.owner  # the store reads the live owner when it builds a retain's context
        if hasattr(store, "text_source"):
            vault_root = self.config.vault_root  # a resent document body is read from its vault copy, as a rebuild does
            store.text_source = lambda fact: vault_text(fact, vault_root)
        if not hasattr(store, "attach"):
            return profile, None
        return profile, ExtractQueue(root / "pending.json", profile, self.config.vault_root, self.ledger)

    def _carry_over_registries(self, old: Path, new: Path) -> None:
        """First run on Hindsight: the people and organization registries move
        over from the old cognee data folder. Only a file that is missing is copied, so
        this runs once and never overwrites; `pending.json` stays behind (its
        jobs name facts of that store)."""
        for name in ("people.json", "orgs.json"):
            src, dst = old / name, new / name
            if dst.exists() or not src.is_file():
                continue
            new.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            self.ledger.append(Kind.settings, "", "", event="registry_carried_over", file=name)

    def owner(self) -> dict[str, Any] | None:
        e = self.profile.people.owner() if self.profile else None
        return {"name": e.name, "aliases": list(e.aliases)} if e else None

    def profile_status(self) -> dict[str, Any]:
        return {"owner": self.owner(), "people": len(self.profile.people.all()),
                "orgs": [{"slug": o.slug, "name": o.name, "relation": o.relation} for o in self.profile.orgs.all()],
                "extraction": self.extract_queue.progress() if self.extract_queue else None}

    def bootstrap_owner(self) -> None:
        """An install from before the people registry: the old interview's
        "The user's name is X." fact names the owner, read once (the latest
        such fact wins). Once the owner is known this does nothing."""
        if self.profile is None or self.profile.people.owner() is not None:
            return
        named = [(f, m) for f in self.memory.facts(SHARED)
                 if f.source == "onboarding:name" and (m := OWNER_FACT.fullmatch(f.text.strip()))]
        if not named:
            return
        fact, m = max(named, key=lambda fm: fm[0].at)
        self.profile.people.set_owner(m.group(1).strip(), [])
        self.ledger.append(Kind.settings, "", "", event="owner_from_fact", fact_id=fact.id)

    async def remember(self, text: str, namespace: str, tier: Tier, *, bot_id: str, entities: list[str], source: str) -> Any:
        """A write from the API: `shared` goes through the profile when there is one; anything else as it always did."""
        if namespace == SHARED and self.profile is not None:
            from src.memory.extract_queue import remember_shared

            return await remember_shared(self.profile, self.extract_queue, text, bot_id=bot_id, source=source, entities=entities, tier=tier)
        # a store write can take tens of seconds: off the event loop, or every other request waits behind it
        return await asyncio.to_thread(self.memory.remember, text, namespace, tier, bot_id=bot_id, entities=entities, source=source)

    @property
    def skill_config(self) -> SkillConfig:
        return SkillConfig(self.config.max_skill_bytes, self.config.max_skills_per_bot)

    def add_bot(self, bot: Bot) -> Bot:
        self.bots[bot.id] = bot
        if bot.model is None and bot.id in (self.bot_models or {}):
            bot.model = self.bot_models[bot.id].to_dict()
        return bot

    def set_bot_model(self, bot: Bot, choice: Choice | None) -> None:
        """Give the bot its own model (an enabled text model on a usable provider), or None for the chat model.
        Raises ProviderStoreError when the choice cannot run."""
        if choice is not None:
            check_choice(self.provider_store, choice, "text", f"{bot.name}'s model")
            self.bot_models[bot.id] = choice
        else:
            self.bot_models.pop(bot.id, None)
        bot.model = choice.to_dict() if choice else None
        save_bot_models(self.config.vault_root, self.bot_models)

    def _model_for(self, bot: Bot) -> tuple[Provider, str | None]:
        """The provider and model a bot's task runs on: its own when it has one that can still run (the model
        enabled, the provider usable), else the harness's chat model."""
        choice = self.bot_models.get(bot.id) if self.bot_models else None
        if choice is None or self.provider_store is None:
            return self.provider, None
        try:
            e = check_choice(self.provider_store, choice, "text", f"{bot.name}'s model")
        except ProviderStoreError as ex:
            self.ledger.append(Kind.error, bot.id, "", where="bot_model", error=str(ex), action="running on the chat model")
            return self.provider, None
        chat = self.model_roles().chat
        if chat is not None and e.id == chat.provider:
            return self.provider, choice.model  # same provider as chat: the model rides on the request
        sig = (e.kind, e.base_url, self.provider_store.key_ref(e.id), e.no_key)
        hit = self._bot_providers.get(e.id)
        if hit is None or hit[0] != sig:
            hit = (sig, make_provider(self.provider_store, e, choice.model))
            self._bot_providers[e.id] = hit
        return hit[1], choice.model

    def _dream_generator(self) -> Generator | None:
        """The dreaming model, or None with no Dreaming model chosen (never filled from another role); raises
        ProviderStoreError when the choice cannot run. The dreamer asks for it often (its rules are checked every
        few seconds during a call), so the client is built once and again only when the choice, its provider's
        settings or its stored key change."""
        roles = self.model_roles()
        choice = roles.dream
        if choice is None:
            self._dream_gen = None
            return None
        e = check_choice(self.provider_store, choice, "text", ROLE_LABELS["dream"])
        key = hashlib.sha256(e.key.encode()).hexdigest() if e.key else ""  # a changed key, without keeping it here
        sig = (choice.provider, choice.model, e.kind, e.base_url, self.provider_store.key_ref(e.id), key, e.no_key)
        if self._dream_gen is None or self._dream_gen[0] != sig:
            provider, model = dream_provider(self.provider_store, roles)
            self._dream_gen = (sig, Generator(provider, model=model))
        return self._dream_gen[1]

    def _dream_conversations(self) -> list[Conversation]:
        """The one-to-one conversations dreaming may read."""
        return [c for c in self.conversations.all() if c.kind == "bot"] if self.conversations is not None else []

    def busy(self) -> bool:
        """Something is under way that dreaming waits for: a task, room turn or routine run (each has a session),
        a task still writing its exchange after its session ended, a room turn waiting for the floor, an import, a
        delegation, or extraction work in the queue."""
        if any(s.state not in _ENDED for s in self.sessions.values()):
            return True
        if any(not t.done() for t in self.tasks.values()):
            return True
        if self.rooms is not None and self.rooms.turns_running():
            return True
        if self.importer is not None and self.importer.running() is not None:
            return True
        if self.delegator is not None and self.delegator.runner.live:
            return True
        return self.extract_queue is not None and bool(self.extract_queue.pending())

    @property
    def bound(self) -> bool:
        return self.provider.kind != "unbound"

    def _provider_kind(self, provider_id: str) -> str | None:
        e = self.provider_store.entries.get(provider_id) if self.provider_store else None
        return e.kind if e else None

    def _image_key_ref(self) -> str | None:
        """The image tool's key: its provider's, from providers.json."""
        if self.images is None or self.provider_store is None or self.images.config.provider not in self.provider_store.entries:
            return None
        return self.provider_store.key_ref(self.images.config.provider)

    def model_roles(self) -> ModelRoles:
        return ModelRoles.load(self.config.vault_root) or roles_from_config(self.config)

    def apply_models(self, roles: ModelRoles) -> None:
        """Run on the providers and scorer Settings chose; raises ProviderStoreError and changes nothing when
        a choice cannot run (a model not enabled, a provider with no key...)."""
        b = build_models(self.provider_store, roles)
        c = self.config
        c.provider_kind, c.key_ref, c.model = b.provider.kind, b.key_ref, b.chat.model
        c.summary_model = b.summary.model
        c.scorer_kind = roles.scorer or c.scorer_kind
        c.decision_model = b.decision.model if b.decision else c.decision_model
        self.summary_provider, self.openrouter_ref = b.summary_provider, b.openrouter_ref
        self.rebind(b.provider, b.scorer)

    def bind_saved_key(self) -> bool:
        """While unbound, try to run on the keys kept in providers.json: the saved model choices, else the
        defaults. A key saved on a provider must bind without a key in the environment or a saved
        harness.json. Says whether the harness is bound; a choice that cannot run leaves it unbound."""
        if not self.bound:
            try:
                self.apply_models(self.model_roles())
            except ProviderStoreError:
                return False
        return True

    def rebind(self, provider: Provider, scorer: Scorer | None) -> None:
        """Swap provider and scorer and rebuild everything that holds them.
        Bots, sessions, approvals, ledger, memory, the cache, the pause
        controller (a paused session keeps its event), rooms, fit
        suggestions, pending promotions, the profile and the extraction
        queue stay; the profile's router moves to the new provider. The
        ToolRunner stays too: the MCP registry and the music service hold it
        and register into it, so a new one would silently drop their tools;
        the tools built here re-register onto it against the rebuilt services."""
        self.provider, self.scorer = provider, scorer
        self._bot_providers.clear()  # a cached bot provider holds the key it was built with
        self._dream_gen = None  # and so does the dreaming client: a key replaced in the environment arrives here
        rooms, fits, pending = self.rooms.rooms, (self.fit._suggestions if self.fit else {}), self.recall.pending
        room_tasks = self.rooms._tasks  # turns still running on the old service stay visible to busy()
        interviews, interview_bots = self.interviews.interviews, self.interviews._bots
        if self.importer is not None:
            self.importer.stop_all("provider rebound")
        for name in ("router", "fit", "supervisor", "compactor", "loop", "recall", "ingestor", "importer", "interviews", "rooms", "orchestrator"):
            setattr(self, name, None)
        self.__post_init__()
        if self.profile is not None:  # the routing call goes through the provider just bound
            self.profile.router.provider = self.summary_provider or self.provider
        store = self.memory.store
        if hasattr(store, "rebind_key"):
            store.rebind_key(self.openrouter_ref or self.config.key_ref)
        self._rewarm_after_bind()
        self.rooms.rooms, self.recall.pending = rooms, pending
        self.rooms._tasks = room_tasks
        self.interviews.interviews, self.interviews._bots = interviews, interview_bots
        if self.fit:
            self.fit._suggestions = fits

    def _rewarm_after_bind(self) -> None:
        """A Hindsight store starts only in warmup, which skipped when no key was bound at
        start. Binding one now runs it again, as a background task on the running loop
        (Settings calls rebind from the loop; with no loop there is nothing to schedule on)."""
        w = self.warmup
        if w is None or not hasattr(self.memory.store, "start") or not self.bound or w.state not in ("skipped", "failed"):
            return
        try:
            task = asyncio.get_running_loop().create_task(w.run())
        except RuntimeError:
            return
        self._warm_tasks.add(task)  # a reference, or the task can be collected mid-run
        task.add_done_callback(self._warm_tasks.discard)

    def recent_exchange(self, bot_id: str) -> str:
        """The bot's latest finished exchange in its current conversation, so a new room can start with it.
        Only while it is fresh: begun within ZEN_ROUTE_CONTEXT_MINUTES (default 30), with no message routed to a room since
        (a saved routed exchange counts as well as the in-memory time, so the rule survives a restart)."""
        bot = self.bots.get(bot_id)
        conv = self.conversations.current(bot_id)
        exchanges = conv.exchanges if conv else []
        last_routed = max((i for i, e in enumerate(exchanges) if e.outcome == "routed"), default=-1)
        done = [e for e in exchanges[last_routed + 1:] if e.outcome == "done" and e.reply]
        if bot is None or not done:
            return ""
        ex = done[-1]
        try:
            minutes = float(os.environ.get("ZEN_ROUTE_CONTEXT_MINUTES", "30"))
        except ValueError:
            minutes = 30.0
        at = datetime.fromisoformat(ex.at)
        if datetime.now(timezone.utc) - at > timedelta(minutes=minutes):
            return ""
        if self._last_routed_at is not None and self._last_routed_at > at:
            return ""
        return f"User: {ex.user[:500]}\n{bot.name}: {ex.reply[:1500]}"

    def settings(self) -> dict[str, Any]:
        c = self.config
        return {
            "provider": self.provider.kind, "model": c.model, "scorer": self.scorer.kind if self.scorer else None,
            "decision_model": c.decision_model, "key_set": self.bound, "openrouter_key": self.has_openrouter_key(),
            "provider_kinds": providers.kinds(), "scorer_kinds": scorers.kinds(), "memory": self.memory.kind if self.memory else None,
            "owner": self.owner(),
            "media_trusted_hosts": list(self.media.hosts) if self.media else [],
        }

    def has_openrouter_key(self) -> bool:
        """Whether any OpenRouter provider in providers.json has a key that resolves (the first-start screen asks otherwise)."""
        store = self.provider_store
        if store is None:
            return False
        for e in store.entries.values():
            if e.kind == "openrouter" and (secret := store.secret(e.id)) is not None and secret.reveal():
                return True
        return False

    def _design_folder(self, bot: Bot, conv: Any, brief: str, session_id: str) -> str:
        """The slug of this studio conversation's own design folder. The harness picks it, not the bot, so two
        designs with the same name never share a folder: an older conversation keeps the folder of its newest
        design, a new one gets a fresh folder made now. Empty when none can be made (the ledger says why)."""
        if conv.design_folder:
            return conv.design_folder
        slug = next((rev[0] for ex in reversed(conv.exchanges) for a in reversed(ex.attachments)
                     if (rev := design_studio.revision_of(self.config.vault_root, Path(a["path"])))), "")
        if not slug:
            try:
                slug = design_studio.fresh_folder(self.config.vault_root, brief)
            except (OSError, design_studio.SnapshotRefused) as e:
                why = str(e) if isinstance(e, design_studio.SnapshotRefused) else "the folder cannot be made"
                self.ledger.append(Kind.error, bot.id, session_id, where="design_folder", error=why,
                                   action="running without a design folder")
                return ""
        self.conversations.set_design_folder(conv.id, slug)
        return slug

    def attach(self, session_id: str, path: str, title: str) -> str:
        """`attach_file` from a one-to-one task: the file joins the task's exchange when it ends. A room
        turn's attachments are the room's (RoomService); a routine has no conversation to show them in."""
        session = self.sessions.get(session_id)
        if session is None or not session.conversation_id or session.room_id:
            return "attach_file works only in a conversation or a room turn."
        if session_id in self.part_turns and self._in_designs(path):
            return studio_helpers.ATTACH_REFUSED  # the live file would be attached; the apply attaches the copy
        conv = self.conversations.get(session.conversation_id)
        target = self.attach_target(session.bot_id, path, folder=conv.design_folder if conv else "")
        if isinstance(target, str):
            return target
        att = Attachment(uuid4().hex[:10], title or Path(path).name, str(target))  # the file's own name, not its numbered copy's
        self.task_attachments.setdefault(session_id, []).append(att)
        msg = f"Attached {att.title!r}; it will show under your reply."
        rev = design_studio.revision_of(self.config.vault_root, target)
        if rev:  # a studio bot's design: say which pictures stayed as written
            notes = design_studio.embed_notes(self.config.vault_root, self.config.vault_root / "designs" / rev[0] / rev[2])
            if notes:
                msg += " Not embedded: " + "; ".join(notes) + "."
        return msg

    def _studio_session(self, session_id: str) -> tuple[Any, str] | None:
        """(conversation, design folder slug) for a one-to-one task of a studio bot in a conversation it owns
        that has a design folder; None for a room turn, a routine or anything else."""
        session = self.sessions.get(session_id)
        if session is None or session.room_id or not session.conversation_id:
            return None
        bot = self.bots.get(session.bot_id)
        conv = self.conversations.get(session.conversation_id)
        if bot is None or not bot.studio or conv is None or conv.owner != bot.id or not conv.design_folder:
            return None
        return conv, conv.design_folder

    def _design_add_image_tool(self, path: str, name: str = "") -> str:
        from .tools import current_call
        return self.design_add_image(current_call.get()[1], path, name)

    def design_add_image(self, session_id: str, path: str, name: str = "") -> str:
        """`design_add_image`: copy a picture the studio bot (or an image helper) may read into the design's
        `images/` folder."""
        found = self._studio_session(session_id)
        turn = self._part_turn(session_id, "images")
        if found is None and turn is None:
            return "design_add_image works only in a design conversation."
        slug = found[1] if found is not None else turn.slug
        source = self.attachable(path)
        if isinstance(source, str):
            return source
        folder = studio_helpers.design_dir(self.config.vault_root, slug)
        if folder is None:
            return "Not added: the design folder is not a plain folder."
        try:
            ref = design_images.add(folder, source, name)
        except design_images.ImageRefused as e:
            return f"Not added: {e}."
        if found is None:  # a helper does not write the page: it places the picture
            return f"Added as {ref}. Place it with design_set_image, path \"{ref}\"."
        return f"Added as {ref}. Reference it as <img src=\"{ref}\"> (keep the quotes); do not write it as data: text."

    def _part_turn(self, session_id: str, kind: str) -> PartTurn | None:
        """The helper's turn this session is, when it places `kind` ("text" or "images")."""
        turn = self.part_turns.get(session_id)
        return turn if turn is not None and turn.kind == kind else None

    def _in_designs(self, path: str) -> bool:
        """True when `path`, read as any tool might (the vault's `designs/...`, the working folder, absolute),
        resolves inside `<vault>/designs/`."""
        designs = self.config.vault_root.resolve() / "designs"
        p = Path(path).expanduser()
        for c in ([p] if p.is_absolute() else [self.config.vault_root / p, self.tools.cwd / p]):
            try:
                if c.resolve().is_relative_to(designs):
                    return True
            except (OSError, RuntimeError):
                return True  # cannot tell: treat it as the design
        return False

    def _design_set_text_tool(self, id: str, text: str) -> str:  # noqa: A002 - the name the model sees
        from .tools import current_call
        return self.design_set_text(current_call.get()[1], id, text)

    def _design_set_image_tool(self, id: str, path: str, alt: str = "") -> str:  # noqa: A002
        from .tools import current_call
        return self.design_set_image(current_call.get()[1], id, path, alt)

    def _on_sheet(self, turn: PartTurn, slot_id: str) -> str:
        """The refusal for an id that is not on the turn's worksheet; empty when it is."""
        if slot_id in turn.sheet:
            return ""
        shown = " ".join(str(slot_id).split())[:64]
        return f"Not set: [{shown}] is not on your list."

    def design_set_text(self, session_id: str, slot_id: str, text: str) -> str:
        """`design_set_text`: hold a new text for one text slot on this helper turn's worksheet."""
        turn = self._part_turn(session_id, "text")
        if turn is None:
            return "design_set_text works only when you are helping with a design's text."
        if refused := self._on_sheet(turn, slot_id):
            return refused
        try:
            clean = design_parts.check_text(text)
        except design_parts.PartRefused as e:
            return f"Not set: {e}."
        turn.changes[slot_id] = design_parts.Change(text=clean)
        session = self.sessions.get(session_id)
        self.ledger.append(Kind.design_part, session.bot_id if session else "", session_id, event="set_text",
                           id=slot_id, chars=len(clean), version=turn.version)
        return f"Set [{slot_id}]."

    def design_set_image(self, session_id: str, slot_id: str, path: str, alt: str = "") -> str:
        """`design_set_image`: hold a new picture (and alt) for one picture slot on this helper turn's worksheet.
        A picture outside the design's `images/` folder is copied in, as `design_add_image` does."""
        turn = self._part_turn(session_id, "images")
        if turn is None:
            return "design_set_image works only when you are helping with a design's pictures."
        if refused := self._on_sheet(turn, slot_id):
            return refused
        new_alt = None
        if alt.strip():
            try:
                new_alt = design_parts.check_text(alt)
            except design_parts.PartRefused as e:
                return f"Not set: the alt text: {e}."
        folder = studio_helpers.design_dir(self.config.vault_root, turn.slug)
        if folder is None:
            return "Not set: the design folder is not a plain folder."
        if not Path(path).is_absolute() and Path(path).parts[:1] == ("designs",):
            path = str(self.config.vault_root / path)  # designs live in the vault, not the working folder
        elif not Path(path).is_absolute() and Path(path).parts[:1] == (design_images.IMAGES,):
            path = str(folder / path)  # `images/<name>`, as design_add_image answers: this design's pictures
        source = self.attachable(path)
        if isinstance(source, str):
            if "outside the folders" in source:
                return "Not set: that picture is outside the folders you may read."
            return f"Not set: {source.removeprefix('Refused: ').rstrip('.')}."
        # the file the page's source already names for this slot (an older `data:` picture names none: setting
        # the file `prepare` wrote it out to is a real change, and the kept source still holds the picture)
        shown = turn.slots[slot_id].element.attrs.get("src", "")
        try:
            same = shown.startswith(design_images.IMAGES + "/") and os.path.samefile(source, folder / shown)
        except (OSError, ValueError):
            same = False
        if same:
            return ("Not set: that is the picture already there. Save your edit under a new name in images/ "
                    "and set that.")
        try:
            if source.parent == folder / design_images.IMAGES and design_images.NAME.fullmatch(source.name):
                ref = f"{design_images.IMAGES}/{source.name}"  # already the design's: used as it is
            else:
                ref = design_images.add(folder, source)
            size = len(design_images.load(folder, ref[len(design_images.IMAGES) + 1:])[1])
        except design_images.ImageRefused as e:
            return f"Not set: {e}."
        turn.changes[slot_id] = design_parts.Change(src=ref, alt=new_alt)
        session = self.sessions.get(session_id)
        self.ledger.append(Kind.design_part, session.bot_id if session else "", session_id, event="set_image",
                           id=slot_id, bytes=size, name=ref, version=turn.version)
        return f"Set [{slot_id}]."

    def start_helper(self, helper: Bot, owner: Bot, conv: Any, task: str, comments: list[dict[str, str]],
                     addendum: str, protected: dict[str, Any] | None = None, answers_to: str = "") -> Session | None:
        """Run `helper` on the newest design of the owner's conversation `conv`: it reads a worksheet of the
        slots it may change (the commented ones, or all of its kind) and places changes with the design tools;
        the harness applies them when the task ends well. None when there is no worksheet for this helper
        (the caller then gives the task to the owner). `addendum` is the comment block, or empty; it is all the
        exchange keeps, so the worksheet and the rules reach this task only, never a later turn's history.
        `protected` is a confirmed scope change, carried as for the owner."""
        found = studio_helpers.prepare(self.config.vault_root, conv, helper.places, comments)
        if found is None:
            return None
        turn, sheet = found
        return self.start_task(helper, task, conversation_id=conv.id, owner=owner, comments=comments,
                               addendum=addendum, brief=f"{sheet}\n\n{studio_helpers.RULES}", part=turn,
                               protected=protected, answers_to=answers_to)

    def _apply_part(self, bot: Bot, session: Session, turn: PartTurn | None) -> str:
        """At a helper turn's end: apply what it placed when the task ended well. Returns the sentence the
        reply gets when the changes were not applied; never raises, so the exchange is always finished."""
        if turn is None:
            self.ledger.append(Kind.design_part, bot.id, session.id, event="apply", refused="no turn state")
            return ""
        if session.state != SessionState.done or not turn.changes:
            return ""  # a task that failed, was stopped or placed nothing writes nothing
        ids = sorted(turn.changes)
        try:
            done = studio_helpers.apply(self.config.vault_root, turn)
        except Exception as e:  # noqa: BLE001 - the exchange must still be finished
            self.ledger.append(Kind.error, bot.id, session.id, where="design_part", error=type(e).__name__)
            done = studio_helpers.Applied(None, "error", "My changes were not applied: something went wrong.")
        if done.copy is None:
            extra = {"kept_before": True} if done.kept_before else {}
            self.ledger.append(Kind.design_part, bot.id, session.id, event="apply", ids=ids, version=turn.version,
                               refused=done.refused, **extra)
            return done.sentence
        rev = design_studio.revision_of(self.config.vault_root, done.copy)
        try:
            size = done.copy.stat().st_size
        except OSError:
            size = 0
        self.task_attachments.setdefault(session.id, []).append(
            Attachment(uuid4().hex[:10], turn.name, str(done.copy)))
        self.ledger.append(Kind.design_part, bot.id, session.id, event="apply", ids=ids, bytes=size,
                           version=rev[1] if rev else 0, name=turn.name)
        return ""

    def _in_routine(self, session_id: str) -> bool:
        """A session with no conversation and no room (a routine), or one the harness does not know: nobody is
        there to approve or see a hand-off. The same sessions `attach` refuses, room turns aside."""
        session = self.sessions.get(session_id)
        return session is None or not (session.conversation_id or session.room_id)

    def _conversation_of(self, session_id: str) -> str:
        """What a session's hand-offs are counted against: its conversation or room, so two tasks in one
        conversation cannot both have a run going."""
        session = self.sessions.get(session_id)
        if session is None:
            return ""
        return (f"conversation:{session.conversation_id}" if session.conversation_id
                else f"room:{session.room_id}" if session.room_id else "")

    def reach(self) -> allowed_folders.Folders:
        """Where bots may reach now: the workspace (the shell's working folder) with Full Access, then the
        `files` server's folders. Read fresh, so a change on Settings > Allowed Folders applies at once."""
        f = allowed_folders.reachable(self.config.vault_root)
        f.full.insert(0, self.tools.cwd)
        return f

    def attach_target(self, bot_id: str, path: str, folder: str = "") -> Path | str:
        """The file `attach_file` attaches for this bot, in a one-to-one task or a room turn, or the refusal.
        A studio bot's relative `designs/...` path is the vault's, and its design is attached as a numbered copy.
        `folder` is the conversation's own design folder: a file in another design's folder is refused."""
        bot = self.bots.get(bot_id)
        studio = bot is not None and bot.studio
        if studio and not Path(path).is_absolute() and Path(path).parts[:1] == ("designs",):
            path = str(self.config.vault_root / path)  # designs live in the vault, not the working folder
        target = self.attachable(path)
        if isinstance(target, str) or not studio:
            return target
        if folder:
            try:
                parts = target.resolve().relative_to(self.config.vault_root.resolve() / "designs").parts
            except ValueError:
                parts = ()
            if parts and parts[0] != folder:
                return (f"Not attached: this conversation's design folder is designs/{folder}/, and designs/{parts[0]}/ "
                        f"belongs to another design. Write the file to designs/{folder}/{target.name}, then attach that.")
        # a design is attached as its fixed copy or not at all: the live file would change under the revision
        try:
            copy = design_studio.snapshot(self.config.vault_root, target)
        except design_studio.SnapshotRefused as e:
            return f"Not attached: {e}."
        return target if copy is None else self.attachable(str(copy))

    def attachable(self, path: str) -> Path | str:
        """`attach_file`'s check, for one-to-one tasks and rooms alike, and again when the file is served."""
        return workspace_file(self.tools.cwd, path, self.reach())

    def start_task(
        self, bot: Bot, task: str, *, conversation_id: str | None = None, in_conversation: bool = True,
        protected: dict[str, Any] | None = None, pinned_skills: list[str] | None = None,
        comments: list[dict[str, str]] | None = None, addendum: str = "",
        owner: Bot | None = None, part: PartTurn | None = None, brief: str = "", recall_query: str = "",
        answers_to: str = "",
    ) -> Session:
        """Run a task for a bot. `owner` is the conversation's owner when it is another bot: `bot` then acts
        in that conversation as a helper (no design folder or design system block, no turn note). It joins the bot's current conversation (or
        the one named) unless `in_conversation` is False (routines): its
        exchange is written now, pending, and completed with the outcome
        when the task ends. Raises KeyError for a named conversation that is
        not this bot's. `part` makes the task a helper's turn on the conversation's design (`start_helper`).
        `brief` is read after the addendum and pinned with it, but not kept with the exchange (a helper's
        worksheet and rules: a later turn must not replay them). `recall_query`, when given, is what memory recall
        searches instead of the task (the events routine's is the user's interests text)."""
        session = Session(bot_id=bot.id, task=task, pinned_skills=list(pinned_skills or []))
        conv = None
        helper = owner is not None and owner.id != bot.id
        if in_conversation:
            conv, _ = self.conversations.begin((owner or bot).id, task, session_id=session.id, conv_id=conversation_id,
                                               comments=comments, addendum=addendum, bot=bot.id if helper else "",
                                               answers_to=answers_to)
            session.conversation_id = conv.id
            session.conversation_owner = conv.owner
            if not helper and self.turn_notes is not None and self.turn_notes.config.enabled:
                session.note = conv.note  # what this bot's last task here found, made and left
        if conv is not None and not helper and conv.design_system:
            why = ""
            try:
                ds = design_systems.load(self.config.design_systems_dir, self.config.vault_root, conv.design_system)
                if conv.design_system_source and ds.source != conv.design_system_source:
                    # a bot can write <vault>/design-systems/<slug>/, replacing the shipped one the user picked
                    why = (f"changed from {conv.design_system_source} to {ds.source} since it was picked; "
                           "pick it again to use it")
                else:
                    session.design = design_systems.block(ds)
            except (KeyError, OSError, design_systems.DesignSystemError) as e:
                why = "no longer exists" if isinstance(e, KeyError) else str(e) if isinstance(e, design_systems.DesignSystemError) else "cannot be read"
            if why:
                # gone, no longer safe or replaced: the task runs without it, and the ledger says why (never a disk path)
                self.ledger.append(Kind.error, bot.id, session.id, where="design_system",
                                   error=f"design system {conv.design_system}: {why}", action="running without the design system")
        if conv is not None and not helper and bot.studio:
            folder = self._design_folder(bot, conv, task, session.id)
            if folder:  # first in the context, before any design system
                session.design = "\n\n".join(p for p in (design_studio.folder_block(folder), session.design) if p)
        if addendum or brief:
            session.recall_query = task  # recall searches the user's words, not the comment block
        if recall_query:
            session.recall_query = recall_query
        self.sessions[session.id] = session
        if part is not None:
            self.part_turns[session.id] = part  # before the task runs: the design tools are offered by it

        async def _run() -> Session:
            try:
                history = None
                if conv is not None:
                    if self.compactor is not None:
                        cfg = self.compactor.config
                        with telemetry.span("conversation.fold"):
                            await self.conversations.fold_if_needed(
                                conv, self.compactor.generator, budget_chars=cfg.conversation_budget_chars, keep=cfg.conversation_keep)
                    history = conv.history(exclude=session.id)
                try:
                    # the bot reads the comments after the task; the pinned task stays the user's words alone, and
                    # the comment block's lines are pinned as constraints so a mid-task compaction cannot fold them away
                    pinned = protected or ({"task": task, "constraints": [ln for ln in addendum.splitlines() if ln.strip()]}
                                           if addendum else None)
                    if brief:
                        base = pinned or {"task": task, "constraints": []}
                        pinned = {**base, "constraints": [*base.get("constraints", []),
                                                          *(ln for ln in brief.splitlines() if ln.strip())]}
                    told = "\n\n".join(p for p in (addendum, brief) if p)
                    await self.loop.run(bot, f"{task}\n\n{told}" if told else task, session, history=history,
                                        protected=pinned)
                except asyncio.CancelledError:
                    if session.state not in _ENDED:  # cancelled before the loop could mark it
                        session.state = SessionState.stopped
                    raise
                except Exception as e:  # noqa: BLE001 - the loop records its own failures; this one is from before its guard
                    session.state, session.error = SessionState.failed, f"{type(e).__name__}: {e}"
                    self.ledger.append(Kind.error, bot.id, session.id, error=session.error)
                    return session
                # tier 3: the result is a session fact, no model call. Not for a closed bot: it reads untrusted listing text
                if session.state == SessionState.done and session.result and not bot.closed:
                    self.cache.add(session.id, bot.id, session.result)
                return session
            finally:  # also on a stop that cancels the task, so the next message knows how this one ended
                if conv is not None and not helper and self.turn_notes is not None:
                    cid = conv.id
                    await self.turn_notes.write(bot, session, outcome=outcome_of(session), previous=session.note,
                                                save=lambda n: self.conversations.set_note(cid, n))
                turn = self.part_turns.pop(session.id, None)
                note = self._apply_part(bot, session, turn) if part is not None else ""
                if conv is not None:
                    # attached files stay with the exchange whatever the outcome: the files exist either way
                    attached = [{"id": a.id, "title": a.title, "path": a.path} for a in self.task_attachments.pop(session.id, [])]
                    outcome, reply = _outcome(session)
                    if note:  # the helper's changes were refused: the user reads why under its reply
                        reply = f"{reply}\n\n{note}" if reply else note
                        if session.state == SessionState.done:
                            session.result = reply
                    self.conversations.finish(conv.id, session.id, outcome, reply, attachments=attached)

        link = telemetry.trace_link()  # now, while the starting request's span is still open

        async def run() -> Session:
            # the request that started this has returned: the run is its own transaction, in the same trace
            with telemetry.background_transaction("session.run", "task", link, bot_id=bot.id, session_id=session.id) as tx:
                s = await _run()
                tx.set_tag("state", str(s.state))
                tx.set_data("steps", s.steps)
                return s

        self.tasks[session.id] = asyncio.create_task(run())
        return session

    def scope_guard(self, session: Session) -> ScopeGuard:
        if session.id not in self.scope_guards:
            block = ProtectedBlock(**session.protected) if session.protected else ProtectedBlock(task=session.task)
            self.scope_guards[session.id] = ScopeGuard(self.scorer, self.ledger, block, on_confirm=lambda: self.router and self.router.bump_scope())
        return self.scope_guards[session.id]

    def stop(self, session_id: str) -> Session:
        session = self.sessions[session_id]
        session.stop_requested = True
        self.delegator.stop(session_id)  # a hand-off in flight is killed; the loop then sees stop_requested
        self.pause.release_for_stop(session_id)
        task = self.tasks.get(session_id)
        if task and not task.done() and session.state == SessionState.waiting_approval:
            task.cancel()
        return session


_ENDED = (SessionState.done, SessionState.failed, SessionState.stopped)


def _outcome(session: Session) -> tuple[str, str]:
    """How a task ended, for its conversation exchange: (outcome, reply)."""
    if session.state == SessionState.done:
        return "done", session.result or ""
    if session.state == SessionState.failed:
        return "failed", session.error or ""
    return "stopped", ""


# -- request models -------------------------------------------------------


class MediaConsentIn(BaseModel):
    urls: list[Annotated[str, Field(max_length=4096)]] = Field(max_length=20)


class SettingsIn(BaseModel):
    kind: str
    key: str
    model: str | None = None
    scorer: str | None = None  # a scorer kind; omitted keeps the current one, "" clears it
    decision_model: str | None = None


class OpenRouterKeyIn(BaseModel):
    key: str


class ImageToolIn(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None  # "" goes back to the default model


class AgentIn(BaseModel):
    enabled: bool | None = None
    ask: bool | None = None
    model: str | None = None


class ProgramPathIn(BaseModel):
    path: str


class SentrySettingsIn(BaseModel):
    dsn: str = ""  # "" turns Sentry off


class McpKeysIn(BaseModel):
    keys: dict[str, str]  # variable name -> key; a blank one keeps what is saved


class FolderIn(BaseModel):
    path: str  # an absolute folder path, typed or from the desktop app's folder picker
    access: Literal["full", "read_only"] = "full"  # Full Access (read and write) or Read-Only


class LangfuseSettingsIn(BaseModel):
    public_key: str = ""  # "" turns Langfuse off
    secret_key: str = ""  # "" keeps the one in force: the page never holds it
    base_url: str = ""
    environment: str = ""


class ProviderIn(BaseModel):
    name: str
    kind: str
    base_url: str | None = None
    key: str | None = None  # stored in providers.json; never sent back
    key_ref: str | None = None  # or env:NAME / vault:NAME
    no_key: bool = False  # a server that needs no key


class ProviderPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    key: str | None = None
    key_ref: str | None = None
    no_key: bool | None = None


class EnabledModelsIn(BaseModel):
    enabled: list[str]


class ProbeIn(BaseModel):
    model: str


class ChoiceIn(BaseModel):
    provider: str
    model: str


class ModelRolesIn(BaseModel):
    chat: ChoiceIn | None = None
    summary: ChoiceIn | None = None
    decision: ChoiceIn | None = None
    dream: ChoiceIn | None = None  # None keeps it
    clear_dream: bool = False
    scorer: str | None = None  # "" for none


class ScopeIn(BaseModel):
    text: str


class ScopeConfirmIn(BaseModel):
    task: str | None = None
    constraints: list[str] | None = None


class CacheFactIn(BaseModel):
    text: str
    entities: list[str] = []


class BotModelIn(BaseModel):
    provider: str
    model: str


class BotIn(BaseModel):
    name: str
    role: str
    instructions: str = ""
    skills: list[str] = []
    model: BotModelIn | None = None  # None: the harness's chat model
    mcps: list[str] = []  # MCP servers on every task; the rest are routed


class BotPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    instructions: str | None = None
    skills: list[str] | None = None
    mcps: list[str] | None = None  # left out: unchanged
    model: BotModelIn | None = None  # sent as null: back to the harness's chat model; left out: unchanged


class ProtectedIn(BaseModel):
    task: str
    constraints: list[str] = []


class DesignCommentIn(BaseModel):
    attachment_id: str
    element_id: str = Field("", max_length=120)
    selector: str = Field(min_length=1, max_length=300)
    tag: str = Field(min_length=1, max_length=40)
    text: str = Field("", max_length=160)
    note: str = Field(min_length=1, max_length=1000)


class DesignSystemIn(BaseModel):
    slug: str | None


class TaskIn(BaseModel):
    task: str
    conversation_id: str | None = None  # omitted: the bot's current conversation
    conversation: bool = True  # False: the task joins no conversation (routines)
    protected: ProtectedIn | None = None  # a scope check's carried block; omitted: the task itself
    design_comments: list[DesignCommentIn] = Field(default_factory=list, max_length=20)  # on the conversation's newest design revision
    route: Literal["auto", "owner"] = "auto"  # "owner": the Designer takes a revision, whatever the decider would say
    answers_to: str | None = None  # the session id of the reply whose question form this answers
    redo_of: str | None = None  # the session id of a helper's reply the user sends back (Task 10)


class ScheduleIn(BaseModel):
    kind: Literal["weekly", "daily", "interval"]
    days: list[int] = []  # 0=Mon .. 6=Sun
    time: str = "09:00"
    hours: int = 0


class RoutineIn(BaseModel):
    name: str
    bot: str
    skill: str  # a skill slug
    enabled: bool = True
    schedule: ScheduleIn
    managed_by: str = ""


class RoutineImportIn(BaseModel):
    routines: list[dict[str, Any]]  # the GUI's localStorage routines: name, bot, skill, schedule (a string), enabled


class UploadIn(BaseModel):
    name: str
    text: str | None = None
    content_b64: str | None = None


class IngestIn(BaseModel):
    path: str


class ScanIn(BaseModel):
    dir: str


class OnboardingStartIn(BaseModel):
    topics: Literal["all", "new"] = "all"  # "new": only the topics added with the personal graph


class ReplyIn(BaseModel):
    text: str


class ImportIn(BaseModel):
    token: str


class ImportFileIn(BaseModel):
    path: str


class OwnerIn(BaseModel):
    name: str
    aliases: list[str] = []


class FactIn(BaseModel):
    text: str
    namespace: str = 'shared'
    tier: str = 'project'
    bot_id: str = ''
    entities: list[str] = []
    source: str = ''


class DecisionIn(BaseModel):
    approved: bool


class FitConfirmIn(BaseModel):
    bot_id: str


class RoomIn(BaseModel):
    name: str
    members: list[str]


class MembersIn(BaseModel):
    members: list[str]


class OrchestrateIn(BaseModel):
    text: str
    new_topic: bool = False


class ArchivedIn(BaseModel):
    archived: bool


class PostIn(BaseModel):
    text: str
    reply_to: str | None = None
    author: str = "user"


class RecordStartIn(BaseModel):
    name: str


class RecordActionIn(BaseModel):
    kind: str
    target: str
    value: str | None = None
    intent: str = ""
    url: str = ""
    secret: bool = False  # the browser says the field is a password-type input


class DraftIn(BaseModel):
    name: str
    use: str


# -- app ------------------------------------------------------------------


def _looks_like_text(raw: bytes) -> bool:
    if bytes([0]) in raw[:4096]:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def create_app(h: Harness) -> FastAPI:
    def _attachment_view(a: dict[str, Any]) -> dict[str, Any]:
        """What the GUI sees of an attachment: a name, never the path; a design copy gives its original name."""
        rev = design_studio.revision_of(h.config.vault_root, Path(a["path"]))
        view: dict[str, Any] = {"id": a["id"], "title": a["title"], "name": rev[2] if rev else Path(a["path"]).name}
        if rev:
            view["design"], view["version"] = rev[0], rev[1]
        return view

    def _conversation_view(c: Any) -> dict[str, Any]:
        """A conversation as any route returns it: attachments by name (never the path on disk), and
        no addendum (what the bot read after the user's text)."""
        record = c.to_record()
        for ex in record["exchanges"]:
            ex["attachments"] = [_attachment_view(a) for a in ex.get("attachments", [])]
            ex.pop("addendum", None)
            ex["bot_name"] = h.bots[ex["bot"]].name if ex.get("bot") in h.bots else ex.get("bot", "")
        d, _ = exclusion.redact_obj(record)
        return d

    def _redo_target(bot: Bot, body: TaskIn) -> tuple[Any, dict[str, str], tuple[str, int, str], Path, int]:
        """What a redo sends back, or a 422: (the conversation, the version the helper started from as an
        attachment and its (slug, number, name), the helper's numbered copy, its number). Only the newest
        exchange, a helper's, that made a revision whose kept source is still there."""
        conv = h.conversations.get(body.conversation_id) if body.conversation_id else h.conversations.current(bot.id)
        if not body.conversation or conv is None or conv.owner != bot.id or not conv.exchanges \
                or conv.exchanges[-1].session_id != body.redo_of:
            raise HTTPException(422, "only the newest change can be redone")
        ex = conv.exchanges[-1]
        helper = h.bots.get(ex.bot) if ex.bot else None
        if helper is None or not helper.places or ex.set_aside:
            raise HTTPException(422, "only a helper's change can be redone")
        root = h.config.vault_root
        mine = next(((a, rev) for a in ex.attachments if (rev := design_studio.revision_of(root, Path(a["path"])))), None)
        if mine is None:
            raise HTTPException(422, "that change made no new version of the design")
        att, (slug, number, name) = mine
        earlier = [(a, rev) for e in conv.exchanges for a in e.attachments
                   if (rev := design_studio.revision_of(root, Path(a["path"]))) and rev[0] == slug and rev[2] == name
                   and rev[1] < number]
        if not earlier or design_studio.before_of(root, Path(att["path"])) is None:
            raise HTTPException(422, studio_helpers.NO_EARLIER)
        prev, prev_rev = max(earlier, key=lambda t: t[1][1])
        return conv, prev, prev_rev, Path(att["path"]), number

    def _comment_addendum(bot: Bot, body: TaskIn, comments: list[dict[str, str]],
                          redo: tuple[Any, dict[str, str], tuple[str, int, str], Path, int] | None = None,
                          conv: Any = None) -> str:
        """The block for a task's design comments, or a 422: they must name the newest design revision
        of the conversation the task joins (`conv` when the route resolved it; a redo: the version the helper
        started from). Checked before anything is created, and again after the routing await."""
        if redo is not None:
            _, att, (slug, version, name), _, _ = redo
            if any(c["attachment_id"] != att["id"] for c in comments):
                raise HTTPException(422, "comments belong on the version the helper started from")
            return design_studio.comment_block(f"designs/{slug}/{name}", version, comments)
        if not body.conversation:
            raise HTTPException(422, "design comments need a conversation")
        conv = conv if conv is not None else _task_conv(bot, body)
        if conv is not None and conv.owner != bot.id:
            conv = None
        newest = next(((a, rev) for ex in reversed(conv.exchanges if conv else []) for a in reversed(ex.attachments)
                       if (rev := design_studio.revision_of(h.config.vault_root, Path(a["path"])))), None)
        if newest is None:
            raise HTTPException(422, "this conversation has no design to comment on")
        att, (slug, version, name) = newest
        if any(c["attachment_id"] != att["id"] for c in comments):
            raise HTTPException(422, "comments belong on the newest version of the design")
        return design_studio.comment_block(f"designs/{slug}/{name}", version, comments)

    def serve_attachment(path: str, *, opened: bool = False, preview: bool = False, studio: bool = False) -> FileResponse | Response:
        """A file a bot attached, in a room or a one-to-one task. A page that runs script (HTML, SVG) may run
        its inline script and style but reach no network, so it cannot send anything out: not as a thumbnail,
        not in the design pane, and not by navigating its own frame to the bare URL. Only `opened` (a valid
        open token, which the viewer asks for on the user's click) serves it under OPEN_POLICY. `preview`
        closes any other file type too; `studio` adds the pick bridge and is always closed."""
        # re-check at serve time: attach_file vetted the path, but it may have become a symlink or moved since
        try:
            target = Path(path).resolve(strict=True)
        except OSError:
            raise HTTPException(404, "the file is gone") from None
        if not isinstance(h.attachable(str(target)), Path):
            raise HTTPException(404, "the file is gone")
        headers = {"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff"}
        if studio:
            # the design pane's frame: preview policy plus the pick bridge; an oversized or non-HTML file goes as is
            try:
                size = target.stat().st_size
            except OSError:
                raise HTTPException(404, "the file is gone") from None
            if target.suffix.lower() in (".html", ".htm") and size <= design_studio.BRIDGE_MAX_BYTES:
                return Response(design_studio.with_bridge(target.read_bytes()), media_type="text/html",
                                headers={**headers, "Content-Disposition": "inline"})
            preview = True
        page = Path(path).suffix.lower() in CLOSED_SUFFIXES or target.suffix.lower() in CLOSED_SUFFIXES
        closed = preview or (page and not opened)
        # shown inline, not downloaded; the sandbox keeps a bot-made page off this origin
        return FileResponse(target, filename=target.name, content_disposition_type="inline",
                            headers={"Content-Security-Policy": PREVIEW_CSP if closed else OPEN_POLICY,
                                     "X-Content-Type-Options": "nosniff"})

    def mcp_vars() -> dict[str, str | list[str]]:
        """What the harness itself hands mcp.json's ${VAR}s; never a key the user enters."""
        return {"ZEN_VAULT_ROOT": str(h.config.vault_root), **AllowedFolders.load(h.config.vault_root).server_args()}

    def guard_folders() -> GuardFolders:
        """The folders a guarded server (magick) may reach: the files server's, read fresh on each call."""
        return allowed_folders.reachable(h.config.vault_root)

    def known_bins() -> dict[str, str]:
        """bin -> the full path saved in Settings > Harness Tools, for the MCP servers' `requires` probes."""
        vault = h.config.vault_root
        return {p.bin: s for k, p in tool_paths.PROGRAMS.items() if (s := tool_paths.saved(vault, k))}

    routines = RoutineStore.load(h.config.vault_root)
    for err in routines.errors:  # once per start: the store keeps a bad routine, and a bad file, as it found it
        h.ledger.append(Kind.error, "", "", error=err)

    def start_routine(r: Routine) -> Session:
        bot = h.bots.get(r.bot)
        if bot is None:
            raise RoutineError(f"Routine {r.name!r}: bot {r.bot!r} does not exist.")
        skill = next((sk for sk in bot.skills if sk.slug == r.skill), None)
        if skill is None:
            raise RoutineError(f"Routine {r.name!r}: skill {r.skill!r} is not attached to {bot.id}.")
        # the events routine's task text is the skill's, so recall is keyed to what the user said they like instead
        recall = h.discover.settings.interests if r.managed_by == EVENTS_MANAGED_BY else ""
        return h.start_task(bot, f"{r.name}: {skill.use}", in_conversation=False, pinned_skills=[r.skill],
                            recall_query=recall)

    scheduler = Scheduler(lambda: routines, start_routine, h.ledger)
    scheduler.verifiers[MUSIC_MANAGED_BY] = h.music.run_failure  # a digest run that saved no digest failed
    scheduler.verifiers[EVENTS_MANAGED_BY] = h.discover.run_failure  # a scout run that saved no digest failed
    h.music.routines, h.music.scheduler = routines, scheduler
    h.discover.routines, h.discover.scheduler = routines, scheduler
    app_routers = [music_router(h.music, h), calendar_router(h.calendar), discover_router(h.discover, h), dream_router(h)]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # warm the memory store first, so it overlaps the MCP connects; the GUI shows an overlay until it ends
        warming = asyncio.create_task(h.warmup.run())
        # MCP servers live as long as the app: connect here, close on shutdown
        try:
            servers = load_mcp_config(h.config.mcp_config, vars=mcp_vars(), saved=McpKeys.load(h.config.vault_root).servers)
        except ValueError as e:
            servers = []
            h.ledger.append(Kind.error, "", "", error=f"mcp config: {e}")
        vault = h.config.vault_root
        # magick runs from the path in Settings > Harness Tools (install-deps fills it in), else PATH
        runner = MagickRunner(settings_dir(vault) / "magick",
                              binary=lambda: [p] if (p := tool_paths.path_for(vault, "imagemagick")) else None)
        h.mcps = McpRegistry(servers, guards={"magick": MagickGuard(guard_folders, runner)}, known_bins=known_bins)
        await h.mcps.connect(h.tools, h.catalog)
        for name, st in h.mcps.status().items():
            h.ledger.append(Kind.mcp, "", "", event=st["state"], server=name, transport=st["transport"], tools=st["tools"], error=st["error"])
            if st["state"] == "needs_install":
                print(f"mcp {name}: needs install \u2014 {st['error']}", flush=True)
        if h.mcps.has("markdownify") and h.markdownify is None:  # ingest's converter for PDF, Office, images, audio
            reg = h.mcps
            h.markdownify = lambda tool, filepath: reg.call("markdownify", tool, {"filepath": filepath})
            h.ingestor = None
            h.__post_init__()
        async def refresh_agents() -> None:
            await h.agents.refresh()
            h.delegator.register(h.tools)  # the tool appears once an agent that is on is found

        refreshing = asyncio.create_task(refresh_agents())  # a hung CLI must not hold startup; GET /agents waits for its own
        h.bootstrap_owner()
        h.learner.restore()  # learn holds waiting in Approvals come back after a restart
        scheduling = asyncio.create_task(scheduler.run())
        h.music.spawn(h.music.start())  # tools, the atlas check and a first scan, when music is on
        await h.discover.start()  # the scout's tools and weekly routine, when discovery is on with a place
        h.calendar.spawn(h.calendar.start())  # a sync now and every 15 minutes, while Google is connected
        stop = asyncio.Event()
        extracting = asyncio.create_task(h.extract_queue.run(stop)) if h.extract_queue else None
        dreaming = asyncio.create_task(h.dreamer.run(stop))  # a pass when the machine is idle and nothing is under way
        try:
            yield
        finally:
            warming.cancel()
            refreshing.cancel()
            await h.delegator.stop_all()  # every hand-off in flight ends, its tree killed and its end logged
            scheduler.stop()
            await scheduling
            stop.set()
            # a dream's model call in flight is cancelled and its conversation's mark stays; a write in flight finishes
            await h.dreamer.close()
            await dreaming
            if extracting is not None:  # the store in flight finishes; the rest of its job runs again at the next start
                await h.extract_queue.shutdown(extracting)
            await h.mcps.close()
            await h.music.close()
            await h.discover.close()  # first: its background refresh reads through the calendar's HTTP client
            await h.calendar.close()
            await h.learner.drain(timeout=30)  # background learn writes land before memory closes; a hung one does not hold shutdown
            # after the worker: the store stops its server and closes; blocks, so off the loop
            await asyncio.to_thread(h.memory.close)

    app = FastAPI(title="zen-agents harness", version="0.0.1", lifespan=lifespan)
    app.state.open_tokens = open_tokens = OpenTokens()
    media_consent = MediaConsent()
    for router in app_routers:
        app.include_router(router)
    app.add_middleware(CORSMiddleware, allow_origins=list(h.config.gui_origins), allow_methods=["*"], allow_headers=["*"])
    # a page that rebinds DNS to 127.0.0.1 is same-origin to the browser, so CORS never applies; the Host header still names the attacker
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.config.host, "127.0.0.1", "localhost"])

    # settings: the GUI binds provider and key over localhost; the key lives in this process only

    @app.get("/settings")
    async def get_settings() -> dict[str, Any]:
        return h.settings()

    @app.get("/media")
    async def get_media(url: str, request: Request, ok: str = "") -> StreamingResponse:
        """A remote image, video or PDF for the chat. A host off the trusted list needs `ok`, the consent
        token for this URL from `POST /media/consent`: the user clicked."""
        try:
            stream = await h.media.open(url, clicked=media_consent.valid(url, ok), range_header=request.headers.get("range"))
        except MediaRefused as e:
            raise HTTPException(e.status, str(e)) from None
        return StreamingResponse(stream.body, status_code=stream.status, headers=stream.headers,
                                 background=BackgroundTask(stream.close))

    @app.post("/media/consent")
    async def post_media_consent(body: MediaConsentIn, request: Request) -> dict[str, Any]:
        """The GUI, on the user's click: a consent token for each URL. JSON only: a page elsewhere can send
        a form or plain text across origins without a preflight, never `application/json`."""
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "send JSON")
        return {"tokens": {u: media_consent.issue(u) for u in body.urls}}

    def image_providers() -> list[str]:
        return [e.id for e in h.provider_store.entries.values() if e.kind == "openrouter"]

    def in_use(provider_id: str, model_id: str | None = None) -> list[str]:
        """The Settings roles that run on this provider (and model, when given)."""
        roles = h.model_roles()
        used = [ROLE_LABELS[r] for r in ROLES if (ch := roles.get(r)) and ch.provider == provider_id
                and (model_id is None or ch.model == model_id) and (r != "decision" or roles.scorer)]
        if h.images and h.images.config.enabled and h.images.config.provider == provider_id and model_id in (None, h.images.config.model):
            used.append("Image generation")
        used += [f"bot {h.bots[b].name}" for b, ch in (h.bot_models or {}).items()
                 if b in h.bots and ch.provider == provider_id and model_id in (None, ch.model)]
        return used

    @app.get("/settings/tools")
    async def get_tool_settings() -> dict[str, Any]:
        """Harness tools the Settings page can turn on and off and configure: today, image generation."""
        return {"tools": [h.images.status(image_providers())] if h.images else []}

    @app.put("/settings/tools/image")
    async def set_image_tool(body: ImageToolIn) -> dict[str, Any]:
        if h.images is None:
            raise HTTPException(503, "image generation is not available on this harness")
        cur = h.images.config
        provider = cur.provider if body.provider is None else body.provider.strip()
        model = cur.model if body.model is None else (body.model.strip() or DEFAULT_IMAGE_MODEL)
        if body.provider is not None or body.model is not None:  # a new choice must be an enabled image model
            if provider not in image_providers():
                raise HTTPException(422, f"{provider!r} is not a provider that makes images; known: {image_providers()}")
            m = h.provider_store.entries[provider].model(model)
            if m is None or "image" not in m.kinds:
                raise HTTPException(422, f"{model} is not an enabled image model on {h.provider_store.entries[provider].name}; "
                                         "enable it on the Model providers page")
        new = ImageConfig(provider=provider, model=model, enabled=cur.enabled if body.enabled is None else body.enabled)
        new.save(h.config.vault_root)
        h.images.reconfigure(new)
        h.images.register(h.tools, h.catalog)
        h.ledger.append(Kind.settings, "", "", event="tool", tool="image", enabled=new.enabled, provider=new.provider, model=new.model)
        return h.images.status(image_providers())

    # host programs the harness runs by full path (Settings > Harness Tools): today ImageMagick

    def program(key: str) -> tool_paths.Program:
        prog = tool_paths.PROGRAMS.get(key)
        if prog is None:
            raise HTTPException(404, f"no host program setting named {key}")
        return prog

    async def program_changed(key: str) -> dict[str, Any]:
        """Reconnect the servers that require this program (a needs_install one may now start); its status."""
        prog = tool_paths.PROGRAMS[key]
        try:
            servers = load_mcp_config(h.config.mcp_config, vars=mcp_vars(), saved=McpKeys.load(h.config.vault_root).servers)
        except ValueError:
            servers = []
        st = h.mcps.status() if h.mcps else {}
        for c in servers:
            if c.name in st and any(r.setting == key or r.bin == prog.bin for r in c.requires):
                await reconnect_mcp(c.name)
        if key in agent_clis.AGENTS:  # a coding agent: found, moved or gone now, so offered or withdrawn now
            await h.agents.refresh(key)
            h.delegator.register(h.tools)
        return await asyncio.to_thread(tool_paths.status, h.config.vault_root, key)

    @app.get("/settings/tools/programs/{key}")
    async def get_program(key: str) -> dict[str, Any]:
        program(key)
        return await asyncio.to_thread(tool_paths.status, h.config.vault_root, key)

    @app.put("/settings/tools/programs/{key}")
    async def set_program(key: str, body: ProgramPathIn) -> dict[str, Any]:
        prog = program(key)
        path = body.path.strip().strip('"').strip("'")
        v = await asyncio.to_thread(tool_paths.verify, prog, path)
        if not v["ok"]:
            raise HTTPException(422, tool_paths.problem_text(prog, v["problem"], v["version"]))
        tool_paths.save(h.config.vault_root, key, path)
        h.ledger.append(Kind.settings, "", "", event="tool", tool=key, path=tool_paths.saved(h.config.vault_root, key),
                        version=v["version"])
        return await program_changed(key)

    @app.delete("/settings/tools/programs/{key}")
    async def clear_program(key: str) -> dict[str, Any]:
        program(key)
        tool_paths.save(h.config.vault_root, key, None)
        h.ledger.append(Kind.settings, "", "", event="tool", tool=key, path=None)
        return await program_changed(key)

    # delegating to Claude Code and Codex (Settings > Coding agents): detection and switches; running
    # is elsewhere
    @app.get("/agents")
    async def get_agents() -> dict[str, Any]:
        agents = await h.agents.refresh()
        h.delegator.register(h.tools)  # an agent found, signed in or out since start is offered or withdrawn now
        return {"agents": agents}

    @app.put("/settings/agents/{key}")
    async def set_agent(key: str, body: AgentIn) -> dict[str, Any]:
        if key not in agent_clis.AGENTS:
            raise HTTPException(404, f"no agent named {key}")
        model = None if body.model is None else body.model.strip()
        if model and not agent_clis.MODEL.fullmatch(model):
            raise HTTPException(422, "A model name may hold only letters, digits, dots, dashes and underscores (64 at most).")
        started = h.agents.begin(key)
        now = await asyncio.to_thread(agent_clis.probe, h.config.vault_root, key)  # fresh: it may have been signed out since
        if body.enabled and not now["usable"]:
            raise HTTPException(422, now["reason"])
        # read and merged only now, with no await before the save: a request that waited on its probe while
        # another switched the agent off must not write the old switch back
        cur = h.agents.settings(key)
        new = agent_clis.AgentSettings(
            enabled=cur.enabled if body.enabled is None else body.enabled,
            ask=cur.ask if body.ask is None else body.ask,
            model=cur.model if model is None else model)
        agent_clis.save_settings(h.config.vault_root, key, new)
        h.ledger.append(Kind.settings, "", "", event="agent", agent=key, enabled=new.enabled, ask=new.ask, model=new.model)
        switches = {"enabled": new.enabled, "ask": new.ask, "model": new.model}
        if not h.agents.land(key, started, {**now, **switches}):  # a later probe has started: keep what it found
            h.agents.statuses[key] = {**h.agents.statuses.get(key, now), **switches}
        h.delegator.register(h.tools)  # on, off, or another agent's name in the tool's list
        return h.agents.statuses[key]

    # tracing (Settings > Tracing): saved over .env, applied now; the desktop app reads the DSN at launch

    def tracing_out() -> dict[str, Any]:
        eff = tracing_settings.effective(h.config.vault_root)
        return tracing_settings.public(eff, sentry_on=telemetry.sentry_on(), langfuse_on=llm_tracing.enabled())

    async def apply_sentry() -> None:
        dsn = tracing_settings.effective(h.config.vault_root)["sentry"]["dsn"]
        await asyncio.to_thread(telemetry.init_tracing, dsn=dsn)  # closing the old client flushes it

    async def apply_langfuse() -> None:
        lf = tracing_settings.effective(h.config.vault_root)["langfuse"]
        await asyncio.to_thread(llm_tracing.restart, tracing_settings.langfuse_env(lf))

    @app.get("/settings/tracing")
    async def get_tracing() -> dict[str, Any]:
        return tracing_out()

    @app.put("/settings/tracing/sentry")
    async def set_sentry(body: SentrySettingsIn) -> dict[str, Any]:
        dsn = body.dsn.strip()
        if dsn and not re.match(r"^https?://[^/@\s]+@[^/\s]+/\S+$", dsn):
            raise HTTPException(422, "a DSN looks like https://<key>@<host>/<project id>")
        tracing_settings.TracingSettings.load(h.config.vault_root).with_sentry(dsn).save(h.config.vault_root)
        await apply_sentry()
        h.ledger.append(Kind.settings, "", "", event="tracing", service="sentry", on=bool(dsn))  # never the DSN
        return tracing_out()

    @app.delete("/settings/tracing/sentry")
    async def clear_sentry() -> dict[str, Any]:
        tracing_settings.TracingSettings.load(h.config.vault_root).with_sentry(None).save(h.config.vault_root)
        await apply_sentry()
        h.ledger.append(Kind.settings, "", "", event="tracing", service="sentry", cleared=True)
        return tracing_out()

    @app.put("/settings/tracing/langfuse")
    async def set_langfuse(body: LangfuseSettingsIn) -> dict[str, Any]:
        cur = tracing_settings.effective(h.config.vault_root)["langfuse"]
        new = tracing_settings.TracingSettings.load(h.config.vault_root).with_langfuse(body.model_dump(), current_secret=cur["secret_key"])
        lf = new.langfuse or {}
        if lf.get("public_key"):
            if not lf.get("secret_key"):
                raise HTTPException(422, "enter the secret key")
            if lf["base_url"] and not re.match(r"^https?://\S+$", lf["base_url"]):
                raise HTTPException(422, "the server URL starts with https:// (or http://)")
            why = await asyncio.to_thread(llm_tracing.check_keys, lf["public_key"], lf["secret_key"], lf["base_url"])
            if why:  # nothing saved, the running client untouched
                raise HTTPException(422, f"Langfuse settings not saved: {why}")
        new.save(h.config.vault_root)
        await apply_langfuse()
        h.ledger.append(Kind.settings, "", "", event="tracing", service="langfuse", on=bool(lf.get("public_key")))
        return tracing_out()

    @app.delete("/settings/tracing/langfuse")
    async def clear_langfuse() -> dict[str, Any]:
        tracing_settings.TracingSettings.load(h.config.vault_root).without_langfuse().save(h.config.vault_root)
        await apply_langfuse()
        h.ledger.append(Kind.settings, "", "", event="tracing", service="langfuse", cleared=True)
        return tracing_out()

    # model providers (providers.json) and the models enabled on each

    def provider_out(e: Any) -> dict[str, Any]:
        return {**e.public(), "in_use": in_use(e.id)}

    @app.get("/settings/providers")
    async def list_providers() -> dict[str, Any]:
        for e in list(h.provider_store.entries.values()):  # models enabled before their facts were read (the first-start seed)
            if e.kind == "openrouter" and any(m.cost is None and m.name == m.id and not m.guessed for m in e.models):
                try:
                    h.provider_store.refresh_snapshots(e.id, await h.model_lister.list(e.id, e.kind, e.base_url, h.provider_store.secret(e.id)))
                except ProviderError:
                    pass  # offline: the pickers show ids until the list can be read
        return {"providers": [provider_out(e) for e in h.provider_store.entries.values()], "kinds": list(PROVIDER_KINDS), "presets": PROVIDER_PRESETS}

    @app.post("/settings/providers", status_code=201)
    async def add_provider(body: ProviderIn) -> dict[str, Any]:
        try:
            e = h.provider_store.add(body.name, body.kind, body.base_url, body.key, body.key_ref, body.no_key)
        except ProviderStoreError as ex:
            raise HTTPException(422, str(ex)) from None
        h.ledger.append(Kind.settings, "", "", event="provider_added", provider=e.id, provider_kind=e.kind, base_url=e.base_url,
                        key_source="stored" if e.key else e.key_ref, no_key=e.no_key)
        return provider_out(e)

    @app.patch("/settings/providers/{provider_id}")
    async def update_provider(provider_id: str, body: ProviderPatch) -> dict[str, Any]:
        try:
            e = h.provider_store.update(provider_id, body.name, body.base_url, body.key, body.key_ref, body.no_key)
        except ProviderStoreError as ex:
            raise HTTPException(404 if ex.args[0].startswith("no provider") else 422, str(ex)) from None
        h.model_lister.forget(provider_id)
        h.bind_saved_key()  # the first key saved here starts the harness on it
        h.ledger.append(Kind.settings, "", "", event="provider_changed", provider=e.id, key_changed=body.key is not None or body.key_ref is not None,
                        **({"no_key": e.no_key} if body.no_key is not None else {}))
        return provider_out(e)

    @app.delete("/settings/providers/{provider_id}", status_code=204)
    async def delete_provider(provider_id: str) -> None:
        if provider_id not in h.provider_store.entries:
            raise HTTPException(404, f"no provider {provider_id!r}")
        if used := in_use(provider_id):
            raise HTTPException(409, f"in use by {', '.join(used)}; pick other models on the Harness settings page first")
        h.provider_store.remove(provider_id)
        h.model_lister.forget(provider_id)
        h.ledger.append(Kind.settings, "", "", event="provider_removed", provider=provider_id)

    @app.get("/settings/providers/{provider_id}/models")
    async def provider_models(provider_id: str, refresh: bool = False) -> dict[str, Any]:
        """Everything the provider offers, with what it says about each, and which are enabled."""
        try:
            e = h.provider_store.get(provider_id)
        except ProviderStoreError as ex:
            raise HTTPException(404, str(ex)) from None
        try:
            listed = await h.model_lister.list(e.id, e.kind, e.base_url, h.provider_store.secret(e.id), refresh=refresh)
        except ProviderError as ex:
            raise HTTPException(502, exclusion.redact(f"{e.name}: {ex}")[0]) from None
        h.provider_store.refresh_snapshots(e.id, listed)
        # an enabled model shows its saved snapshot: for a probed one, that is what the probe found
        rows = [(e.model(m.id), True) if e.model(m.id) else (m, False) for m in listed]
        return {"provider": e.id, "models": [{**e.model_out(m), "enabled": on, "unusable": m.id in e.unusable} for m, on in rows]}

    @app.put("/settings/providers/{provider_id}/models")
    async def set_provider_models(provider_id: str, body: EnabledModelsIn) -> dict[str, Any]:
        """Replace the enabled set. Each model's details come from the provider's list, so the pickers
        can show kind, reasoning and cost; an id the list lacks (or a list that cannot be fetched) keeps
        what is already known, else is recorded as a text model."""
        try:
            e = h.provider_store.get(provider_id)
        except ProviderStoreError as ex:
            raise HTTPException(404, str(ex)) from None
        wanted = list(dict.fromkeys(m.strip() for m in body.enabled if m.strip()))
        if e.kind != "openrouter" and (new := [m for m in wanted if e.model(m) is None]):
            raise HTTPException(422, f"{e.name} lists model ids only, so each model is tested as it is enabled: "
                                     f"enable one at a time ({', '.join(new)})")
        dropped = [m.id for m in e.models if m.id not in wanted]
        if blocked := [f"{mid} ({', '.join(u)})" for mid in dropped if (u := in_use(provider_id, mid))]:
            raise HTTPException(409, f"in use: {'; '.join(blocked)}; pick other models on the Harness settings page first")
        try:
            listed = {m.id: m for m in await h.model_lister.list(e.id, e.kind, e.base_url, h.provider_store.secret(e.id))}
        except ProviderError:
            listed = {}
        known = {m.id: m for m in e.models}
        models = [listed.get(mid) or known.get(mid) or ModelInfo(id=mid, name=mid, kinds=guess_kinds(mid), guessed=True) for mid in wanted]
        h.provider_store.set_models(provider_id, models)
        h.bind_saved_key()
        h.ledger.append(Kind.settings, "", "", event="models_enabled", provider=provider_id, enabled=len(models), dropped=dropped)
        return provider_out(e)

    # -- testing a model as it is enabled, on a provider that lists ids only --

    @app.post("/settings/providers/{provider_id}/models/probe")
    async def start_probe(provider_id: str, body: ProbeIn) -> dict[str, Any]:
        """Test one model's capabilities, then enable it with what was found. Follow it on the events stream."""
        try:
            e = h.provider_store.get(provider_id)
        except ProviderStoreError as ex:
            raise HTTPException(404, str(ex)) from None
        mid = body.model.strip()
        if e.kind == "openrouter":
            raise HTTPException(422, f"{e.name} publishes its models' capabilities; enable models with Save")
        if not mid or e.model(mid) is not None:
            raise HTTPException(422, f"{mid or 'a model id'} is already enabled on {e.name}" if mid else "a model id is required")
        try:
            listed = {m.id: m for m in await h.model_lister.list(e.id, e.kind, e.base_url, h.provider_store.secret(e.id))}
        except ProviderError:
            listed = {}
        model = listed.get(mid) or ModelInfo(id=mid, name=mid, kinds=guess_kinds(mid), guessed=True)

        def enable(probed: ModelInfo) -> dict[str, Any]:
            cur = h.provider_store.get(provider_id)
            h.provider_store.set_models(provider_id, [*cur.models, probed])
            h.ledger.append(Kind.settings, "", "", event="models_enabled", provider=provider_id, enabled=len(cur.models),
                            dropped=[], probed=True, model=probed.id)
            return provider_out(cur)

        def hide(model_id: str) -> dict[str, Any]:
            h.ledger.append(Kind.settings, "", "", event="model_unusable", provider=provider_id, model=model_id)
            return provider_out(h.provider_store.mark_unusable(provider_id, model_id))

        try:
            job = h.probes.start(e.id, e.base_url, h.provider_store.secret(e.id), model, enable, hide)
        except RuntimeError as ex:
            raise HTTPException(409, str(ex)) from None
        return {"probe_id": job.id, "model": mid, "steps": job.steps}

    def _probe(job_id: str):
        job = h.probes.get(job_id)
        if job is None:
            raise HTTPException(404, f"unknown probe {job_id}")
        return job

    @app.post("/settings/providers/{provider_id}/models/probe/{job_id}/stop")
    async def stop_probe(provider_id: str, job_id: str) -> dict[str, Any]:
        _probe(job_id)
        job = h.probes.stop(job_id)
        return {"probe_id": job.id, "state": job.state}

    @app.get("/settings/providers/{provider_id}/models/probe/{job_id}/events")
    async def probe_events(provider_id: str, job_id: str) -> StreamingResponse:
        _probe(job_id)
        q = h.probes.subscribe(job_id)

        async def gen():
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if ev.get("kind") == "end" and ev.get("error"):
                        ev = {**ev, "error": exclusion.redact(ev["error"])[0]}
                    yield _sse(ev)
                    if ev.get("kind") == "end":
                        return
            finally:
                h.probes.unsubscribe(job_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # which provider and model the harness uses for each job

    def models_out() -> dict[str, Any]:
        return {**h.model_roles().to_dict(), "roles": ROLES, "labels": ROLE_LABELS, "optional": sorted(OPTIONAL_ROLES), "scorer_kinds": scorers.kinds(),
                "bound": h.bound}

    @app.get("/settings/models")
    async def get_model_roles() -> dict[str, Any]:
        return models_out()

    @app.put("/settings/models")
    async def set_model_roles(body: ModelRolesIn) -> dict[str, Any]:
        cur = h.model_roles()
        pick = lambda v, old: old if v is None else Choice(v.provider, v.model)  # noqa: E731
        roles = ModelRoles(chat=pick(body.chat, cur.chat), summary=pick(body.summary, cur.summary), decision=pick(body.decision, cur.decision),
                           dream=None if body.clear_dream else pick(body.dream, cur.dream),
                           scorer=cur.scorer if body.scorer is None else body.scorer)
        try:
            dream_provider(h.provider_store, roles)  # the dream model is checked, never bound into the harness
            h.apply_models(roles)
        except ProviderStoreError as ex:
            raise HTTPException(422, str(ex)) from None
        roles.save(h.config.vault_root)
        if roles.dream != cur.dream and h.dreamer is not None:  # a new dreaming model (or none) deserves a fresh try
            h.dreamer.reset_attempts()
        h.ledger.append(Kind.settings, "", "", event="models", **{k: v for k, v in roles.to_dict().items()})
        return models_out()

    @app.post("/settings/provider")
    async def set_provider(body: SettingsIn) -> dict[str, Any]:
        key = body.key.strip()
        if not key:
            raise HTTPException(422, "key is empty")
        if not providers.is_registered(body.kind):
            raise HTTPException(422, f"unknown provider kind {body.kind!r}; known: {providers.kinds()}")
        scorer_kind = h.scorer.kind if (body.scorer is None and h.scorer) else (body.scorer or None)
        if scorer_kind and not scorers.is_registered(scorer_kind):
            raise HTTPException(422, f"unknown scorer kind {scorer_kind!r}; known: {scorers.kinds()}")
        c = h.config
        exclusion.register_secret_value(key)  # before anything can log it
        os.environ[c.gui_key_env] = key
        ref = f"env:{c.gui_key_env}"
        try:
            provider = providers.get(body.kind, key_ref=ref, model=body.model)
            scorer = scorers.get(scorer_kind, key_ref=ref, model=body.decision_model) if scorer_kind else None
        except Exception as e:  # noqa: BLE001 - a factory refused; say why, without the key
            raise HTTPException(422, exclusion.redact(f"{type(e).__name__}: {e}")[0]) from None
        c.provider_kind, c.key_ref, c.model, c.decision_model = body.kind, ref, body.model, body.decision_model
        c.scorer_kind = scorer_kind or c.scorer_kind
        h.summary_provider, h.openrouter_ref = None, None
        h.rebind(provider, scorer)
        h.ledger.append(Kind.settings, "", "", event="provider", provider=body.kind, model=body.model, scorer=scorer_kind, key_ref=ref)
        return h.settings()

    @app.post("/settings/openrouter-key")
    async def set_openrouter_key(body: OpenRouterKeyIn) -> dict[str, Any]:
        """The first-start screen's one step: check the key with OpenRouter, keep it in providers.json (so it
        survives a restart) and bind the models on it. A refused or unchecked key leaves the store untouched; an
        accepted key stays saved even when the models then fail to bind (422 with the reason)."""
        key = body.key.strip()
        if not key:
            raise HTTPException(422, "key is empty")
        exclusion.register_secret_value(key)  # before anything can log it
        store = h.provider_store
        http = OpenRouterHttp(Secret(key), base_url=OPENROUTER_BASE, timeout=30.0, transport=h.model_lister.transport)
        try:  # the authenticated key endpoint: the model list is public and would take any key
            await http.check_key()
        except ProviderAuthError:
            raise HTTPException(422, "OpenRouter did not accept that key.") from None
        except ProviderError:
            raise HTTPException(503, "Could not reach OpenRouter. Check your connection and try again.") from None
        finally:
            await http.aclose()
        # looked up after the check, with no await before the save: another request cannot remove it in between
        entry = next((e for e in store.entries.values() if e.kind == "openrouter"), None)
        if entry is None:  # removed since the first start: add it back as the seed made it
            entry = store.add("OpenRouter", "openrouter", key=key)
            store.set_models(entry.id, default_models(h.config))
        else:
            store.update(entry.id, key=key)
        h.model_lister.forget(entry.id)
        h.ledger.append(Kind.settings, "", "", event="provider_changed", provider=entry.id, key_changed=True)
        roles = h.model_roles()
        try:
            h.apply_models(roles)
        except ProviderStoreError as ex:
            raise HTTPException(422, str(ex)) from None
        roles.save(h.config.vault_root)
        return h.settings()
    app.state.harness = h

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True, "provider": h.provider.kind, "scorer": h.scorer.kind if h.scorer else None,
            "selector": h.loop.selector.kind, "supervisor": h.supervisor.kind if h.supervisor else None,
            "compaction": h.compactor is not None, "bots": len(h.bots), "sessions": len(h.sessions),
            "memory": h.memory.kind, "recall": h.recall.kind, "mcps": len(h.mcps.connected()) if h.mcps else 0,
            "warmup": h.warmup.state,
        }

    # the owner's profile: who the owner is, the people and organizations known, and background extraction

    def _extraction() -> None:
        if h.extract_queue is None:
            raise HTTPException(503, f"graph extraction needs the Hindsight memory store; this one is {h.memory.kind}")

    @app.get("/profile")
    async def get_profile() -> dict[str, Any]:
        return h.profile_status()

    @app.put("/profile/owner")
    async def set_owner(body: OwnerIn) -> dict[str, Any]:
        try:
            h.profile.people.set_owner(body.name, body.aliases)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        h.ledger.append(Kind.settings, "", "", event="owner")
        if h.extract_queue is not None:
            h.extract_queue.wake()  # work waiting for the name can run now
        return h.profile_status()

    @app.get("/knowledge/ui")
    async def knowledge_ui() -> dict[str, Any]:
        """Where the memory store's own dashboard (Hindsight's control plane) is, and whether it is up."""
        status = getattr(h.memory.store, "ui_status", None)
        if status is None:
            return {"url": "", "state": "unavailable", "reason": "this memory store has no control plane"}
        return status()

    # rebuild: every shared fact is extracted again. The shared stores' memories go; the facts, and every
    # bot's private memories, stay.

    NAME_FIRST = "Add your name in Settings first: the rebuild re-learns your people under it"
    rebuilding = asyncio.Lock()  # the queue check, the drop, and the queueing are one step

    def _rebuild_plan() -> list[tuple[Any, int]]:
        from src.memory.extract_queue import rebuild_jobs

        return rebuild_jobs(h.memory.facts(SHARED), h.config.vault_root)

    @app.get("/memory/rebuild")
    async def rebuild_preview() -> dict[str, Any]:
        """What a rebuild would queue: the shared facts, and about how many model calls extracting them takes."""
        _extraction()
        plan = _rebuild_plan()
        return {"facts": len(plan), "extraction_calls": sum(n for _, n in plan)}

    @app.post("/memory/rebuild")
    async def rebuild() -> dict[str, Any]:
        """Drop the shared datasets, then queue every shared fact for extraction.
        Refused (409) while any extraction is queued (a rebuild's own jobs, or
        others that would re-extract into the datasets being dropped) and while
        the owner's name is unknown: every rebuild job waits for it, so the
        graph would be cleared and never refilled."""
        _extraction()
        from src.memory.hindsight_http import HindsightTimeout

        async with rebuilding:
            if n := len(h.extract_queue.pending()):
                if h.extract_queue.waiting_for_owner():
                    raise HTTPException(409, NAME_FIRST)
                raise HTTPException(409, f"{n} extraction job{'s are' if n != 1 else ' is'} still queued; rebuild once extraction finishes")
            if h.profile.people.owner() is None:
                raise HTTPException(409, NAME_FIRST)
            try:
                dropped = await asyncio.to_thread(h.memory.drop_shared)  # the store deletes under its write lock: off the loop
            except HindsightTimeout as e:
                raise HTTPException(504, str(e)) from None
            except Exception as e:  # noqa: BLE001 - some datasets may be gone already; the drop is safe to repeat
                raise HTTPException(502, f"the memory store failed while clearing the shared memory ({type(e).__name__}: {e}); rebuild again to finish") from None
            plan = _rebuild_plan()  # after the drop: a shared write that landed during it is queued too
            for job, _ in plan:
                h.extract_queue.add(job)
            h.ledger.append(Kind.rebuild, "", "", datasets=dropped, queued=len(plan), extraction_calls=sum(n for _, n in plan))
            return {"queued": len(plan)}

    @app.get("/warmup")
    async def warmup_status() -> dict[str, Any]:
        return h.warmup.status()

    @app.get("/mcps")
    async def list_mcps() -> dict[str, Any]:
        return mcps_out()

    # MCP server keys (the MCP servers page): saved per server over .env, the server reconnected at once

    def mcps_out() -> dict[str, Any]:
        st = h.mcps.status() if h.mcps else {}
        saved = McpKeys.load(h.config.vault_root)
        for name, s in st.items():
            s["keys"] = mcp_keys.public(s.pop("key_vars", []), saved.for_server(name))
        return st

    async def reconnect_mcp(name: str) -> None:
        """Load this server's entry again, with the keys now saved, and swap it in."""
        try:
            servers = load_mcp_config(h.config.mcp_config, vars=mcp_vars(), saved=McpKeys.load(h.config.vault_root).servers)
        except ValueError as e:
            raise HTTPException(422, f"mcp.json: {e}") from None
        config = next((c for c in servers if c.name == name), None)
        if config is None or h.mcps is None:
            raise HTTPException(404, f"no MCP server named {name}")
        await h.mcps.reconnect(config)
        st = h.mcps.status()[name]
        h.ledger.append(Kind.mcp, "", "", event=st["state"], server=name, transport=st["transport"], tools=st["tools"], error=st["error"])

    def mcp_key_vars(name: str) -> list[str]:
        st = (h.mcps.status() if h.mcps else {}).get(name)
        if st is None:
            raise HTTPException(404, f"no MCP server named {name}")
        return st["key_vars"]

    # allowed folders (Settings > Allowed Folders): what the files MCP server may read and write, beyond the vault

    def folders_out() -> dict[str, Any]:
        return allowed_folders.public(AllowedFolders.load(h.config.vault_root), h.config.vault_root)

    async def reconnect_folder_servers() -> None:
        """Every server whose args take the allowed folders (either list) gets the new lists now."""
        st = h.mcps.status() if h.mcps else {}
        try:
            servers = load_mcp_config(h.config.mcp_config, vars=mcp_vars(), saved=McpKeys.load(h.config.vault_root).servers)
        except ValueError as e:
            raise HTTPException(422, f"mcp.json: {e}") from None
        for c in servers:
            if any(v in c.arg_vars for v in allowed_folders.VARS) and c.name in st:
                await reconnect_mcp(c.name)

    def full_access_roots() -> list[Path]:
        """What the files server always gets with Full Access: the vault, and ZEN_FS_ROOT when set."""
        return allowed_folders.full_access_roots(h.config.vault_root)

    def folder_error(e: allowed_folders.FolderRefused) -> HTTPException:
        return HTTPException(409 if isinstance(e, allowed_folders.FolderConflict) else 422, str(e))

    @app.get("/settings/folders")
    async def get_folders() -> dict[str, Any]:
        return folders_out()

    @app.post("/settings/folders")
    async def add_folder(body: FolderIn) -> dict[str, Any]:
        try:
            p = allowed_folders.check(body.path, private=allowed_folders.private_folders(h.config.vault_root))
            new = AllowedFolders.load(h.config.vault_root).place(p, body.access, full_access_roots())
        except allowed_folders.FolderRefused as e:
            raise folder_error(e) from None
        new.save(h.config.vault_root)
        h.ledger.append(Kind.settings, "", "", event="allowed_folders", added=str(p), access=body.access)
        await reconnect_folder_servers()
        return folders_out()

    @app.patch("/settings/folders")
    async def set_folder_access(body: FolderIn) -> dict[str, Any]:
        """Switch a folder between Full Access and Read-Only; the files server restarts with it."""
        cur = AllowedFolders.load(h.config.vault_root)
        if cur.get(body.path) is None:
            raise HTTPException(404, f"{body.path} is not an allowed folder")
        try:
            new = cur.with_access(body.path, body.access, full_access_roots())
        except allowed_folders.FolderRefused as e:
            raise folder_error(e) from None
        if new.folders != cur.folders:
            new.save(h.config.vault_root)
            h.ledger.append(Kind.settings, "", "", event="allowed_folders", path=body.path, access=body.access)
            await reconnect_folder_servers()
        return folders_out()

    @app.delete("/settings/folders")
    async def remove_folder(path: str) -> dict[str, Any]:
        cur = AllowedFolders.load(h.config.vault_root)
        nxt = cur.without(path)
        if nxt.folders == cur.folders:
            raise HTTPException(404, f"{path} is not an allowed folder")
        nxt.save(h.config.vault_root)
        h.ledger.append(Kind.settings, "", "", event="allowed_folders", removed=path)
        await reconnect_folder_servers()
        return folders_out()

    def routine_out(r: Routine) -> dict[str, Any]:
        nxt, error = None, ""
        if r.enabled:
            try:
                nxt = next_run(r.schedule, scheduler.clock())
            except ValueError as e:  # a routines.json edited by hand: still listed, so it can be fixed or deleted
                error = str(e)
        return {**asdict(r), "next_run": nxt.isoformat() if nxt else None, "error": error}

    def unattached(bot_id: str, skill: str) -> str:
        """Why a routine cannot pin this skill (a run pins only a skill attached to its bot), or ""."""
        bot = h.bots.get(bot_id)
        return "" if bot and any(sk.slug == skill for sk in bot.skills) else f"Skill '{skill}' is not attached to {bot_id}"

    def routine_from(rid: str, body: RoutineIn, old: Routine | None = None) -> Routine:
        sc = body.schedule
        if not (body.name.strip() and body.bot.strip() and body.skill.strip()):
            raise HTTPException(422, "name, bot and skill are required")
        if sc.kind == "weekly" and not sc.days:
            raise HTTPException(422, "a weekly routine needs at least one day")
        if sc.kind != "interval" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", sc.time):
            raise HTTPException(422, "time must be HH:MM, 24-hour")
        if sc.kind == "interval" and sc.hours < 1:
            raise HTTPException(422, "an interval routine needs hours of 1 or more")
        if any(d < 0 or d > 6 for d in sc.days):
            raise HTTPException(422, "days are 0 (Monday) to 6 (Sunday)")
        if problem := unattached(body.bot, body.skill):
            raise HTTPException(422, problem)
        return Routine(rid, body.name.strip(), body.bot, body.skill, body.enabled,
                       Schedule(sc.kind, sorted(set(sc.days)), sc.time, sc.hours), old.last_run if old else None,
                       body.managed_by, old.last_skip if old else None)

    def writable() -> None:
        if routines.read_only:
            raise HTTPException(409, routines.errors[0] if routines.errors else "routines are read-only")

    @app.get("/routines")
    async def list_routines() -> list[dict[str, Any]]:
        return [routine_out(r) for r in routines.all()]

    @app.get("/routines/store")
    async def routines_store() -> dict[str, Any]:
        """What the load could not read: `read_only` when routines.json could not be read at all (nothing is saved)."""
        return {"read_only": routines.read_only, "errors": list(routines.errors)}

    @app.post("/routines", status_code=201)
    async def create_routine(body: RoutineIn) -> dict[str, Any]:
        writable()
        r = routines.upsert(routine_from(uuid4().hex[:10], body))
        routines.save()
        return routine_out(r)

    @app.put("/routines/{rid}")
    async def update_routine(rid: str, body: RoutineIn) -> dict[str, Any]:
        writable()
        old = routines.get(rid)
        if old is None:
            raise HTTPException(404, f"no routine {rid}")
        r = routines.upsert(routine_from(rid, body, old))
        routines.save()
        return routine_out(r)

    @app.delete("/routines/{rid}", status_code=204)
    async def delete_routine(rid: str) -> None:
        writable()
        if not routines.delete(rid):
            raise HTTPException(404, f"no routine {rid}")
        routines.save()

    @app.post("/routines/{rid}/run")
    async def run_routine(rid: str) -> dict[str, Any]:
        try:
            session = await scheduler.run_now(rid)
        except KeyError:
            raise HTTPException(404, f"no routine {rid}") from None
        except RoutineError as e:
            raise HTTPException(409, str(e)) from None
        return {"session_id": session.id, "routine": routine_out(routines.get(rid))}

    @app.post("/routines/import")
    async def import_routines(body: RoutineImportIn) -> dict[str, Any]:
        """Take the GUI's old localStorage routines. One that matches a saved routine by name and bot is left as
        it is (`existing`: the user made it again here); one whose schedule string parses is kept, paused (it never
        ran before, and turning it on silently would start paid model runs); the rest come back as `unparsed`."""
        writable()  # a 409 keeps them in the browser for the next load
        imported, existing, unparsed = [], [], []
        for item in body.routines:
            name, bot, skill = (str(item.get(k) or "").strip() for k in ("name", "bot", "skill"))
            if name and bot and any(r.name == name and r.bot == bot for r in routines.all()):
                existing.append(name)
                continue
            sch = parse_schedule(str(item.get("schedule", "")))
            if sch is None or not (name and bot and skill):
                unparsed.append(item)
            else:
                r = routines.upsert(Routine(uuid4().hex[:10], name, bot, skill, False, sch))
                problem = unattached(bot, skill)  # kept, paused, so the user can attach the skill or edit it
                imported.append({**routine_out(r), **({"note": f"{problem}: attach it or edit the routine before turning it on."}
                                                       if problem else {})})
        routines.save()
        return {"imported": imported, "existing": existing, "unparsed": unparsed}

    @app.post("/mcps/{name}/recheck")
    async def recheck_mcp(name: str) -> dict[str, Any]:
        """Probe the server's host requirements again (after an install) and reconnect it."""
        refresh_windows_path()
        await reconnect_mcp(name)
        return mcps_out()

    @app.put("/mcps/{name}/keys")
    async def set_mcp_keys(name: str, body: McpKeysIn) -> dict[str, Any]:
        allowed = mcp_key_vars(name)
        unknown = [k for k in body.keys if k not in allowed]
        if unknown:
            raise HTTPException(422, f"{name} does not take {', '.join(unknown)}; its mcp.json entry names {', '.join(allowed) or 'no keys'}")
        given = {k: v.strip() for k, v in body.keys.items() if v.strip()}
        if not given:
            raise HTTPException(422, "enter a key")
        if any(any(ch.isspace() or ord(ch) < 32 for ch in v) for v in given.values()):
            raise HTTPException(422, "a key has no spaces or line breaks")
        McpKeys.load(h.config.vault_root).with_keys(name, given).save(h.config.vault_root)
        h.ledger.append(Kind.settings, "", "", event="mcp_keys", server=name, saved=sorted(given))  # names, never values
        await reconnect_mcp(name)
        return mcps_out()

    @app.delete("/mcps/{name}/keys/{var}")
    async def clear_mcp_key(name: str, var: str) -> dict[str, Any]:
        if var not in mcp_key_vars(name):
            raise HTTPException(404, f"{name} does not take {var}")
        McpKeys.load(h.config.vault_root).without_key(name, var).save(h.config.vault_root)
        h.ledger.append(Kind.settings, "", "", event="mcp_keys", server=name, cleared=[var])
        await reconnect_mcp(name)
        return mcps_out()

    # vault: work docs

    @app.post("/vault/upload")
    async def upload_doc(body: UploadIn) -> dict[str, Any]:
        name = Path(body.name).name
        if not name or name != body.name.replace("\\", "/").split("/")[-1] or name.startswith(".") or "/" in body.name or "\\" in body.name:
            raise HTTPException(422, "name must be a bare file name")
        if (body.text is None) == (body.content_b64 is None):
            raise HTTPException(422, "send exactly one of text or content_b64")
        dest = h.config.vault_root / "uploads" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if body.text is not None:
            raw = body.text.encode("utf-8")
        else:
            try:
                raw = base64.b64decode(body.content_b64, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(422, "content_b64 is not base64")
        text = raw.decode("utf-8", errors="strict") if _looks_like_text(raw) else None
        staged_redactions = 0
        if text is not None:  # the staged copy is already redacted: the vault never holds the raw secret
            clean, found = exclusion.redact(text)
            staged_redactions = len(found)
            dest.write_text(clean, encoding="utf-8")
        else:
            dest.write_bytes(raw)
        try:
            out = (await h.ingestor.ingest(dest)).to_dict(h.config.vault_root)
            out["redactions"] += staged_redactions
            return out
        except IngestError as e:
            raise HTTPException(422, str(e))

    @app.post("/vault/ingest")
    async def ingest_path(body: IngestIn) -> dict[str, Any]:
        try:
            return (await h.ingestor.ingest(Path(body.path))).to_dict(h.config.vault_root)
        except IngestError as e:
            raise HTTPException(422, str(e))

    @app.post("/vault/scan")
    async def scan_directory(body: ScanIn) -> dict[str, Any]:
        try:
            scan = await asyncio.to_thread(scan_dir, Path(body.dir), h.ingestor.converter.accepts,  # a home folder walk takes seconds
                                           unchanged=unchanged_in(h.ingestor.sync_log, lambda fid: h.memory.get(fid) is not None))
            return h.scans.put(scan).to_dict()
        except IngestError as e:
            raise HTTPException(422, str(e))

    @app.post("/vault/import")
    async def start_import(body: ImportIn) -> dict[str, Any]:
        scan = h.scans.get(body.token)
        if scan is None:
            raise HTTPException(404, "unknown or expired scan token; scan the directory again")
        try:
            job = h.importer.start(scan)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"import_id": job.id}

    @app.post("/vault/import-file")
    async def import_file(body: ImportFileIn) -> dict[str, Any]:
        """One picked file, run as an import job: no count-and-confirm,
        the same progress events and stop as a directory."""
        try:
            scan = scan_file(Path(body.path), h.ingestor.converter.accepts)
        except IngestError as e:
            raise HTTPException(422, str(e))
        try:
            job = h.importer.start(scan)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"import_id": job.id, "dir": str(scan.dir), "files": 1}

    def _import(job_id: str):
        job = h.importer.get(job_id)
        if job is None:
            raise HTTPException(404, f"unknown import {job_id}")
        return job

    @app.get("/vault/import/{job_id}")
    async def import_status(job_id: str) -> dict[str, Any]:
        return _import(job_id).to_dict()

    @app.post("/vault/import/{job_id}/stop")
    async def stop_import(job_id: str) -> dict[str, Any]:
        _import(job_id)
        return h.importer.stop(job_id).to_dict()

    @app.get("/vault/import/{job_id}/events")
    async def import_events(job_id: str) -> StreamingResponse:
        _import(job_id)
        q = h.importer.subscribe(job_id)

        async def gen():
            try:
                while True:
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse(e)
                    if e.get("kind") == "end":
                        return
            finally:
                h.importer.unsubscribe(job_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # -- onboarding --------------------------------------------------------

    @app.post("/onboarding/start")
    async def onboarding_start(body: OnboardingStartIn | None = None) -> dict[str, Any]:
        if not h.bound:
            raise HTTPException(503, "no provider bound; enter a provider and key in Settings")
        bot = h.bots.get(ASSISTANT_ID)
        if bot is None:
            raise HTTPException(404, f"no {ASSISTANT_ID} bot: add bot-prompts/{ASSISTANT_ID}.md")
        try:
            iv, message = await h.interviews.start(bot, NEW_TOPICS if body and body.topics == "new" else TOPICS)
        except ProviderError as e:
            raise HTTPException(502, exclusion.redact(f"{type(e).__name__}: {e}")[0])
        return {"interview": iv.to_dict(), "message": message}

    def _interview(interview_id: str):
        iv = h.interviews.get(interview_id)
        if iv is None:
            raise HTTPException(404, f"unknown interview {interview_id}")
        return iv

    @app.get("/onboarding/{interview_id}")
    async def onboarding_status(interview_id: str) -> dict[str, Any]:
        return _interview(interview_id).to_dict()

    @app.post("/onboarding/{interview_id}/reply")
    async def onboarding_reply(interview_id: str, body: ReplyIn) -> dict[str, Any]:
        iv = _interview(interview_id)
        if iv.done:
            raise HTTPException(409, "the interview is done")
        try:
            return await h.interviews.reply(interview_id, body.text.strip())
        except ProviderError as e:
            raise HTTPException(502, exclusion.redact(f"{type(e).__name__}: {e}")[0])

    @app.get("/vault/sync")
    async def sync_entries() -> list[dict[str, Any]]:
        return [e.to_dict() for e in h.ingestor.sync_log.entries()]

    @app.post("/vault/rescan")
    async def rescan() -> list[dict[str, Any]]:
        return [r.to_dict(h.config.vault_root) for r in await h.ingestor.rescan()]

    # memory

    @app.get("/memory")
    async def list_facts(namespace: str = "shared") -> list[dict[str, Any]]:
        return [f.to_dict() for f in h.memory.facts(namespace)]

    @app.post("/memory")
    async def remember(body: FactIn) -> dict[str, Any]:
        try:
            tier = Tier(body.tier)
        except ValueError:
            raise HTTPException(422, f"unknown tier {body.tier!r}")
        try:
            return (await h.remember(body.text, body.namespace, tier, bot_id=body.bot_id, entities=body.entities, source=body.source)).to_dict()
        except ExclusionError as e:
            raise HTTPException(422, str(e))

    @app.delete("/memory/{fact_id}")
    async def forget(fact_id: str) -> dict[str, Any]:
        if not await asyncio.to_thread(h.memory.forget, fact_id, bot_id="user"):
            raise HTTPException(404, "no such fact")
        return {"forgotten": fact_id}

    @app.get("/memory/recall")
    async def recall_facts(bot: str, q: str, session_id: str = "") -> dict[str, Any]:
        b = _bot(h, bot)
        return (await h.recall.recall_detail(b, q, session_id)).to_dict()

    @app.post("/memory/{fact_id}/promote")
    async def promote_fact(fact_id: str, bot_id: str = "user", session_id: str = "") -> dict[str, Any]:
        try:
            gate = await h.recall.promote(fact_id, session_id=session_id, bot_id=bot_id)
        except KeyError:
            raise HTTPException(404, "no such fact")
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"fact_id": fact_id, "allowed": gate.allowed, "probability": gate.probability, "from_tier": gate.from_tier,
                "to_tier": gate.to_tier, "confirm": str(gate.confirm)}

    @app.post("/memory/{fact_id}/promote/confirm")
    async def confirm_promotion(fact_id: str) -> dict[str, Any]:
        try:
            return {"fact_id": fact_id, "confirm": str((await h.recall.confirm_promotion(fact_id)).confirm)}
        except KeyError:
            raise HTTPException(404, "no pending promotion")

    @app.post("/memory/{fact_id}/promote/deny")
    async def deny_promotion(fact_id: str) -> dict[str, Any]:
        try:
            return {"fact_id": fact_id, "confirm": str(h.recall.deny_promotion(fact_id).confirm)}
        except KeyError:
            raise HTTPException(404, "no pending promotion")

    # bots

    @app.get("/bots")
    async def list_bots() -> list[dict[str, Any]]:
        return [b.to_dict() for b in h.bots.values()]

    @app.get("/bots/{bot_id}/conversations")
    async def list_conversations(bot_id: str) -> dict[str, Any]:
        _bot(h, bot_id)
        cur = h.conversations.current(bot_id)
        d, _ = exclusion.redact_obj({
            "current": cur.id if cur else None,
            "conversations": [c.brief() for c in h.conversations.for_owner(bot_id)],
        })
        return d

    @app.post("/bots/{bot_id}/conversations", status_code=201)
    async def new_conversation(bot_id: str) -> dict[str, Any]:
        _bot(h, bot_id)
        d, _ = exclusion.redact_obj(h.conversations.start_new(bot_id).brief())
        return d

    @app.get("/conversations")
    async def recent_conversations(limit: int = 5) -> dict[str, Any]:
        """The newest one-to-one conversations across every bot, for the Conversations page."""
        recent = h.conversations.recent(set(h.bots), max(1, min(limit, 50)))
        d, _ = exclusion.redact_obj({"conversations": [{**c.brief(), "owner": c.owner} for c in recent]})
        return d

    @app.get("/conversations/{conv_id}")
    async def get_conversation(conv_id: str) -> dict[str, Any]:
        c = h.conversations.get(conv_id)
        if c is None:
            raise HTTPException(404, "no such conversation")
        return _conversation_view(c)

    @app.get("/designs")
    async def list_designs() -> dict[str, Any]:
        """Every design made in a studio bot's own conversation, newest conversation first, for the Design page:
        one entry per conversation and design, with its newest numbered copy. Names only, never a path."""
        found: list[dict[str, Any]] = []
        studio = {bid for bid, b in h.bots.items() if b.studio}
        for c in h.conversations.recent(studio, 10_000):
            by_design: dict[str, dict[str, Any]] = {}
            for ex in c.exchanges:
                for a in ex.attachments:
                    rev = design_studio.revision_of(h.config.vault_root, Path(a["path"]))
                    if rev is None:
                        continue
                    slug, version, name = rev
                    d = by_design.setdefault(slug, {"seen": set()})
                    d["seen"].add((name, version))
                    d.update(title=a["title"], name=name, version=version, attachment_id=a["id"])  # the latest attached
            found += [{"conversation_id": c.id, "bot_id": c.owner, "design": slug, "title": d["title"], "name": d["name"],
                       "version": d["version"], "versions": len(d["seen"]), "attachment_id": d["attachment_id"],
                       "updated_at": c.updated_at} for slug, d in by_design.items()]
        out, _ = exclusion.redact_obj({"designs": found})
        return out

    @app.get("/design-systems")
    async def list_design_systems() -> dict[str, Any]:
        return {"design_systems": design_systems.available(h.config.design_systems_dir, h.config.vault_root)}

    @app.put("/conversations/{conv_id}/design-system")
    async def set_design_system(conv_id: str, body: DesignSystemIn) -> dict[str, str]:
        if h.conversations.get(conv_id) is None:
            raise HTTPException(404, "no such conversation")
        source = ""
        if body.slug is not None:  # picking loads it, so a too-large or unsafe one is refused now with its reason
            try:
                source = design_systems.load(h.config.design_systems_dir, h.config.vault_root, body.slug).source
            except KeyError:
                raise HTTPException(404, "no such design system") from None
            except design_systems.DesignSystemError as e:
                raise HTTPException(422, str(e)) from None
        h.conversations.set_design_system(conv_id, body.slug or "", source)
        return {"design_system": body.slug or ""}

    # Open tokens. A page served closed (PREVIEW_CSP) cannot obtain one: its fetch and XHR are blocked by
    # `default-src 'none'`, and the sandbox (no allow-forms) refuses its form posts. So a closed page cannot
    # open itself; only the GUI's viewer asks, when the user opens an attachment.
    @app.post("/conversations/{conv_id}/attachments/{attachment_id}/open")
    async def open_conversation_attachment(conv_id: str, attachment_id: str) -> dict[str, str]:
        if h.conversations.attachment(conv_id, attachment_id) is None:
            raise HTTPException(404, "no such attachment")
        return {"token": open_tokens.issue(("conversation", conv_id, attachment_id))}

    @app.get("/conversations/{conv_id}/attachments/{attachment_id}")
    async def conversation_attachment(conv_id: str, attachment_id: str, preview: str = "",
                                      studio: str = "", open_: str = Query("", alias="open")) -> Response:
        att = h.conversations.attachment(conv_id, attachment_id)
        if att is None:
            raise HTTPException(404, "no such attachment")
        # a token is spent by the first GET that shows it, even one it does not open (wrong file, studio)
        opened = bool(open_) and open_tokens.redeem(open_, ("conversation", conv_id, attachment_id))
        return serve_attachment(att["path"], opened=opened and studio != "1", preview=preview == "1",
                                studio=studio == "1")

    @app.post("/bots/{bot_id}/conversations/{conv_id}/current")
    async def select_conversation(bot_id: str, conv_id: str) -> dict[str, Any]:
        _bot(h, bot_id)
        try:
            c = h.conversations.select(bot_id, conv_id)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None
        return _conversation_view(c)

    @app.post("/bots", status_code=201)
    async def create_bot(body: BotIn) -> dict[str, Any]:
        bot = Bot(name=body.name, role=body.role, instructions=body.instructions, id="", mcps=list(body.mcps))
        _attach(h, bot, body.skills)
        choice = Choice(body.model.provider, body.model.model) if body.model else None
        if choice is not None:
            try:
                check_choice(h.provider_store, choice, "text", "The bot's model")
            except ProviderStoreError as ex:
                raise HTTPException(422, str(ex)) from None
        _persist(h, bot)  # assigns the id from the file name
        h.add_bot(bot)
        if choice is not None:
            h.set_bot_model(bot, choice)
        return bot.to_dict()

    @app.get("/bots/{bot_id}")
    async def get_bot(bot_id: str) -> dict[str, Any]:
        return _bot(h, bot_id).to_dict()

    @app.patch("/bots/{bot_id}")
    async def patch_bot(bot_id: str, body: BotPatch) -> dict[str, Any]:
        bot = _bot(h, bot_id)
        if bot.closed and body.mcps is not None and list(body.mcps) != list(bot.mcps):
            raise HTTPException(422, "This bot's tools are fixed in its file.")
        if body.name is not None:
            bot.name = body.name
        if body.role is not None:
            bot.role = body.role
        if body.instructions is not None:
            bot.instructions = body.instructions
        if "model" in body.model_fields_set:
            try:
                h.set_bot_model(bot, Choice(body.model.provider, body.model.model) if body.model else None)
            except ProviderStoreError as ex:
                raise HTTPException(422, str(ex)) from None
        if body.skills is not None:
            bot.skills = []
            _attach(h, bot, body.skills)
        if body.mcps is not None:
            bot.mcps = list(body.mcps)
        _persist(h, bot)
        return bot.to_dict()

    # skills

    @app.get("/skills")
    async def list_skills() -> list[dict[str, Any]]:
        return [e.to_dict() for e in h.catalog.entries()]

    # skill fit: suggest, then the user confirms. Never auto attach.

    @app.post("/skills/{slug}/fit", status_code=201)
    async def suggest_fit(slug: str) -> dict[str, Any]:
        if h.fit is None:
            raise HTTPException(503, "no scorer configured; fit is unavailable")
        skill = h.catalog.skill(slug)
        if skill is None:
            raise HTTPException(404, "no such skill")
        if not h.bots:
            raise HTTPException(409, "no bots to fit against")
        return (await h.fit.suggest(skill, list(h.bots.values()))).to_dict()

    @app.get("/fits")
    async def list_fits(state: str | None = None) -> list[dict[str, Any]]:
        return [s.to_dict() for s in h.fit.list(state)] if h.fit else []

    @app.post("/fits/{fit_id}/confirm")
    async def confirm_fit(fit_id: str, body: FitConfirmIn) -> dict[str, Any]:
        if h.fit is None or h.fit.get(fit_id) is None:
            raise HTTPException(404, "no such fit")
        bot = _bot(h, body.bot_id)
        try:
            return h.fit.confirm(fit_id, bot).to_dict()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/fits/{fit_id}/deny")
    async def deny_fit(fit_id: str) -> dict[str, Any]:
        if h.fit is None or h.fit.get(fit_id) is None:
            raise HTTPException(404, "no such fit")
        try:
            return h.fit.deny(fit_id).to_dict()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    # tasks and sessions

    def _task_conv(bot: Bot, body: TaskIn) -> Any:
        """The conversation a task joins (the one named, else the bot's current one), or None."""
        return h.conversations.get(body.conversation_id) if body.conversation_id else h.conversations.current(bot.id)

    def _require_idle(bot: Bot, body: TaskIn, conv: Any = None) -> None:
        """409 while a studio conversation has a task that has not ended (one task at a time). `conv` is the
        conversation the route already resolved; without it the task's conversation is looked up now."""
        if bot.studio and body.conversation:
            conv = conv if conv is not None else _task_conv(bot, body)
            if conv is not None and conv.owner == bot.id and any(ex.outcome == "pending" for ex in conv.exchanges):
                raise HTTPException(409, "a task is already running in this conversation")

    async def _helper_turn(bot: Bot, body: TaskIn, conv: Any, comments: list[dict[str, str]],
                           addendum: str) -> Session | None:
        """A helper's turn on the design for this task in `conv` (resolved by the route before any await), or
        None when the owner takes it (the route then goes on as before). Only a studio bot's own conversation
        that has a design is routed; the answers to a question form go to the bot that asked."""
        if not (bot.studio and body.conversation):
            return None
        if conv is None or conv.owner != bot.id:
            if body.answers_to:
                raise HTTPException(422, "answers_to is not a reply in this conversation")
            return None
        asked = None
        if body.answers_to:
            asked = next((ex for ex in conv.exchanges if ex.session_id == body.answers_to), None)
            if asked is None:
                raise HTTPException(422, "answers_to is not a reply in this conversation")
        newest = studio_helpers.newest_revision(h.config.vault_root, conv)
        if newest is None:
            return None
        helpers = [b for b in h.bots.values() if b.places]
        if not helpers:
            return None
        task = body.task.strip()
        protected = body.protected.model_dump() if body.protected else None
        if asked is not None:
            helper = h.bots.get(asked.bot) if asked.bot else None
            if helper is None or not helper.places:
                return None
            att, (slug, version, name) = newest
            scope = asked.comments if asked.comments and all(c.get("attachment_id") == att["id"] for c in asked.comments) else []
            block = design_studio.comment_block(f"designs/{slug}/{name}", version, scope) if scope else ""
            return h.start_helper(helper, bot, conv, task, scope, block, protected=protected, answers_to=body.answers_to)
        if h.scorer is None or body.route != "auto" or body.redo_of:
            return None
        chosen = await studio_help.pick(h.scorer, h.ledger, bot, helpers, task, comments)
        # the scorer call is an await: another task may have started (or ended, adding a revision) meanwhile
        _require_idle(bot, body, conv)
        if comments:
            _comment_addendum(bot, body, comments, conv=conv)  # still the newest revision, or the same 422
        if chosen is None:
            return None
        return h.start_helper(h.bots[chosen], bot, conv, task, comments, addendum, protected=protected)

    @app.post("/bots/{bot_id}/tasks", status_code=202)
    async def start_task(bot_id: str, body: TaskIn) -> dict[str, Any]:
        bot = _bot(h, bot_id)
        if not body.task.strip():
            raise HTTPException(422, "task is empty")
        if not h.bound:
            raise HTTPException(503, UnboundProvider.MESSAGE)
        _require_idle(bot, body)
        comments = [c.model_dump() for c in body.design_comments]
        redo = _redo_target(bot, body) if body.redo_of else None
        addendum = _comment_addendum(bot, body, comments, redo) if comments else ""
        conversation_id = body.conversation_id or None
        if redo is None:
            # resolved once: a change of the bot's current conversation during the await cannot move the task
            conv = _task_conv(bot, body) if bot.studio and body.conversation else None
            if conv is not None and conv.owner == bot.id:
                conversation_id = conv.id
            helped = await _helper_turn(bot, body, conv, comments, addendum)
            if helped is not None:
                return {"session_id": helped.id, "state": str(helped.state)}
            _require_idle(bot, body, conv)  # the routing await may have let another task start
        else:
            # everything is checked; from here no await until the task starts, so nothing else can start
            conv, prev, (_, back, _), copy, number = redo
            if why := studio_helpers.restore(h.config.vault_root, copy, Path(prev["path"])):
                raise HTTPException(422, why)
            h.conversations.set_aside(conv.id, body.redo_of)
            h.ledger.append(Kind.design_part, conv.exchanges[-1].bot, body.redo_of, event="redo",
                            set_aside=number, restored=back)
        try:
            session = h.start_task(bot, body.task.strip(), conversation_id=conversation_id,
                                   in_conversation=body.conversation,
                                   protected=body.protected.model_dump() if body.protected else None,
                                   comments=comments,
                                   addendum=addendum, answers_to=body.answers_to or "")
        except KeyError:
            raise HTTPException(404, "no such conversation") from None
        return {"session_id": session.id, "state": str(session.state)}

    @app.get("/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return [_session_brief(s) for s in h.sessions.values()]

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return _session(h, session_id).to_dict(redact=exclusion.redact_obj)

    @app.post("/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict[str, Any]:
        _session(h, session_id)
        return _session_brief(h.stop(session_id))

    @app.post("/sessions/{session_id}/resume")
    async def resume_session(session_id: str) -> dict[str, Any]:
        s = _session(h, session_id)
        if not h.pause.resume(session_id, reason="user"):
            raise HTTPException(409, f"session is {s.state}, not paused")
        return _session_brief(s)

    @app.post("/sessions/{session_id}/retry")
    async def retry_session(session_id: str) -> dict[str, Any]:
        s = _session(h, session_id)
        if not h.pause.is_paused(session_id):
            raise HTTPException(409, f"session is {s.state}, not paused")
        resumed = await h.pause.retry(session_id)
        return {**_session_brief(s), "resumed": resumed}

    # scope: the decider spots a change of goal or constraints; the user confirms before the block is rewritten

    @app.post("/sessions/{session_id}/scope")
    async def scope_check(session_id: str, body: ScopeIn) -> dict[str, Any]:
        s = _session(h, session_id)
        if h.scorer is None:
            raise HTTPException(503, "no scorer configured; scope checks are unavailable")
        if not body.text.strip():
            raise HTTPException(422, "text is empty")
        change = await h.scope_guard(s).check(body.text.strip(), bot_id=s.bot_id, session_id=s.id)
        d = _scope_dict(change)
        s.scope_pending = d if change.changed else None
        return d

    @app.post("/sessions/{session_id}/scope/confirm")
    async def scope_confirm(session_id: str, body: ScopeConfirmIn) -> dict[str, Any]:
        s = _session(h, session_id)
        guard = h.scope_guards.get(session_id)
        if guard is None or guard.pending is None:
            raise HTTPException(409, "no scope change is pending")
        block = guard.confirm(task=body.task, constraints=body.constraints)
        s.protected = block.to_dict()
        s.scope_pending = None
        return {"protected": s.protected, "confirm": str(ConfirmState.confirmed)}

    @app.post("/sessions/{session_id}/scope/reject")
    async def scope_reject(session_id: str) -> dict[str, Any]:
        s = _session(h, session_id)
        guard = h.scope_guards.get(session_id)
        if guard is None or guard.pending is None:
            raise HTTPException(409, "no scope change is pending")
        guard.reject()
        s.scope_pending = None
        return {"protected": s.protected, "confirm": str(ConfirmState.denied)}

    @app.get("/memory/cache")
    async def cache_facts(session_id: str) -> list[dict[str, Any]]:
        _session(h, session_id)
        return [f.to_dict() for f in h.cache.facts(session_id)]

    @app.post("/memory/cache", status_code=201)
    async def cache_add(session_id: str, body: CacheFactIn) -> dict[str, Any]:
        s = _session(h, session_id)
        if not body.text.strip():
            raise HTTPException(422, "text is empty")
        return h.cache.add(s.id, s.bot_id, body.text.strip(), body.entities).to_dict()

    @app.get("/sessions/{session_id}/events")
    async def session_events(session_id: str) -> StreamingResponse:
        _session(h, session_id)

        async def gen():
            for e in h.ledger.tail(0, session_id=session_id):
                yield _sse(e.to_dict())
            q = h.ledger.subscribe()
            try:
                while True:
                    s = h.sessions[session_id]
                    # `end` tells the GUI to reload the conversation, so it waits for the run's task to finish too:
                    # the exchange (reply and attachments) is saved after the turn note, which is a model call
                    task = h.tasks.get(session_id)
                    over = task is None or task.done()
                    if s.state in (SessionState.done, SessionState.stopped, SessionState.failed) and over and q.empty():
                        yield _sse({"kind": "end", "state": str(s.state)})
                        return
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if e.session_id == session_id:
                        yield _sse(e.to_dict())
            finally:
                h.ledger.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # approvals

    @app.get("/approvals")
    async def list_approvals(state: str | None = None) -> list[dict[str, Any]]:
        return [r.to_dict() for r in h.approvals.list(state)]

    @app.post("/approvals/{approval_id}/decide")
    async def decide(approval_id: str, body: DecisionIn) -> dict[str, Any]:
        if h.approvals.get(approval_id) is None:
            raise HTTPException(404, "no such approval")
        try:
            req = h.approvals.decide(approval_id, body.approved)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        if req.tool == "learn_page" and h.learner is not None:  # a held discovery: write it or remember the denial
            await h.learner.decide(req, body.approved)
        return req.to_dict()

    # rooms: text only, tools stay in each bot's own session

    @app.get("/rooms")
    async def list_rooms() -> list[dict[str, Any]]:
        return [_room_status(r) for r in sorted(h.rooms.list(), key=lambda r: r.last_active, reverse=True)]

    @app.post("/orchestrate")
    async def orchestrate(body: OrchestrateIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(422, "text is empty")
        if not h.bound:
            raise HTTPException(503, UnboundProvider.MESSAGE)
        if h.orchestrator is None or ASSISTANT_ID not in h.bots:
            return {"outcome": "task"}
        try:
            out = await h.orchestrator.route(body.text, context=h.recent_exchange(ASSISTANT_ID), new_topic=body.new_topic)
        except (ProviderError, RoomError) as e:  # RoomError: a room refused the text; answer directly instead
            err = f"{type(e).__name__}: {e}"
            h.ledger.append("error", ASSISTANT_ID, "", where="orchestrate", error=err)
            return {"outcome": "task", "fallback": err}
        if out.outcome == "room":
            h._last_routed_at = datetime.now(timezone.utc)  # the direct chat before this is no longer "just now"
            room = h.rooms.get(out.room_id) if out.room_id else None
            try:
                h.conversations.record(ASSISTANT_ID, body.text, "routed", f"(Sent to the room {room.name if room else ''!r}.)")
            except Exception as e:  # noqa: BLE001 - the room is open; a failed record must not turn the route into a 500
                h.ledger.append("error", ASSISTANT_ID, "", where="orchestrate_record", error=f"{type(e).__name__}: {e}")
        if out.outcome == "studio" and out.bot_id:
            h._last_routed_at = datetime.now(timezone.utc)
            bot = h.bots[out.bot_id]
            try:
                # a design starts in a conversation of its own; the GUI opens it and starts the task there
                conv = h.conversations.current(bot.id)
                if conv is None or conv.exchanges:
                    conv = h.conversations.start_new(bot.id)
                h.conversations.record(ASSISTANT_ID, body.text, "routed", f"(Sent to {bot.name}.)")
            except Exception as e:  # noqa: BLE001 - without a conversation to hand over to, ZenBot answers itself
                err = f"{type(e).__name__}: {e}"
                h.ledger.append("error", ASSISTANT_ID, "", where="orchestrate_record", error=err)
                return {"outcome": "task", "fallback": err}
            return {**out.to_dict(), "conversation_id": conv.id}
        return out.to_dict()

    @app.put("/rooms/{room_id}/archived")
    async def archive_room(room_id: str, body: ArchivedIn) -> dict[str, Any]:
        return _room_status(_room_call(h.rooms.archive, room_id, body.archived))

    @app.delete("/rooms/{room_id}", status_code=204)
    async def delete_room(room_id: str) -> Response:
        try:
            h.rooms.delete(room_id)
        except RoomError as e:
            raise HTTPException(404 if str(e).startswith("no room") else 409, str(e)) from None
        return Response(status_code=204)

    def _room_attachment(room_id: str, attachment_id: str) -> Attachment:
        room = _room_call(h.rooms.get, room_id)
        att = next((a for p in room.posts for a in p.attachments if a.id == attachment_id), None)
        if att is None:
            raise HTTPException(404, "no such attachment")
        return att

    @app.post("/rooms/{room_id}/attachments/{attachment_id}/open")
    async def open_room_attachment(room_id: str, attachment_id: str) -> dict[str, str]:
        _room_attachment(room_id, attachment_id)  # see open_conversation_attachment: a closed page cannot ask
        return {"token": open_tokens.issue(("room", room_id, attachment_id))}

    @app.get("/rooms/{room_id}/attachments/{attachment_id}")
    async def room_attachment(room_id: str, attachment_id: str, preview: str = "",
                              open_: str = Query("", alias="open")) -> FileResponse:
        att = _room_attachment(room_id, attachment_id)
        opened = bool(open_) and open_tokens.redeem(open_, ("room", room_id, attachment_id))
        return serve_attachment(att.path, opened=opened, preview=preview == "1")

    @app.post("/rooms")
    async def create_room(body: RoomIn) -> dict[str, Any]:
        return _room_status(_room_call(h.rooms.create, body.name, body.members))

    @app.put("/rooms/{room_id}/members")
    async def set_room_members(room_id: str, body: MembersIn) -> dict[str, Any]:
        return _room_status(_room_call(h.rooms.set_members, room_id, body.members))

    @app.post("/rooms/{room_id}/posts")
    async def post_to_room(room_id: str, body: PostIn) -> dict[str, Any]:
        try:
            post = await h.rooms.post(room_id, body.author, body.text, reply_to=body.reply_to)
        except RoomError as e:
            raise HTTPException(404 if str(e).startswith("no room") else 422, str(e)) from None
        return post.to_dict()

    @app.post("/rooms/{room_id}/conversations")
    async def new_room_conversation(room_id: str) -> dict[str, Any]:
        _room_call(h.rooms.new_conversation, room_id)
        return _room_status(h.rooms.get(room_id))

    @app.post("/rooms/{room_id}/conversations/{conv_id}/current")
    async def resume_room_conversation(room_id: str, conv_id: str) -> dict[str, Any]:
        _room_call(h.rooms.select_conversation, room_id, conv_id)
        return _room_status(h.rooms.get(room_id))

    @app.get("/rooms/{room_id}/turn")
    async def room_turn(room_id: str) -> dict[str, Any]:
        room = _room_call(h.rooms.get, room_id)
        h.rooms.turns.expire_overdue(room)
        return _room_status(room)

    # recorder: a driven browser, actions by intent, secrets as handles

    def _rec() -> Recorder:
        if h.recorder is None:
            raise HTTPException(503, "no browser recorder: install playwright (uv pip install playwright && playwright install chromium)")
        return h.recorder

    @app.get("/recorder/status")
    async def recorder_status() -> dict[str, Any]:
        return h.recorder.status() if h.recorder else {"state": "unavailable", "driver": None}

    @app.post("/recorder/start")
    async def recorder_start(body: RecordStartIn) -> dict[str, Any]:
        rec = _rec()
        try:
            await rec.start(body.name)
        except RecorderError as e:
            raise HTTPException(409, str(e)) from None
        return rec.status()

    @app.post("/recorder/actions")
    async def recorder_action(body: RecordActionIn) -> dict[str, Any]:
        rec = _rec()
        try:
            action = rec.on_action(body.kind, body.target, body.value, body.intent, body.url, body.secret)
        except RecorderError as e:
            raise HTTPException(409, str(e)) from None
        return {"recorded": action is not None, "action": action.to_dict() if action else None, **rec.status()}

    @app.post("/recorder/stop")
    async def recorder_stop() -> dict[str, Any]:
        rec = _rec()
        try:
            recording = await rec.stop()
        except RecorderError as e:
            raise HTTPException(409, str(e)) from None
        d, _ = exclusion.redact_obj(recording.to_dict())
        return d

    @app.post("/recorder/draft")
    async def recorder_draft(body: DraftIn) -> dict[str, Any]:
        rec = _rec()
        if rec.last is None:
            raise HTTPException(409, "nothing recorded yet")
        taken: set[str] = set()
        if h.config.user_content:  # a draft never takes the slug of a shipped skill
            mine = h.config.skills_write_dir.resolve()
            taken = {s.slug for s in h.catalog.skills() if not (s.path and Path(s.path).resolve().is_relative_to(mine))}
        res = draft_skill(rec.last, name=body.name, use=body.use, skills_dir=h.config.skills_write_dir, catalog=h.catalog, config=h.skill_config, taken=taken)
        return {
            "state": str(res.state), "reason": str(res.reason) if res.reason else None, "detail": res.detail,
            "path": str(res.path) if res.path else None, "slug": res.skill.slug if res.skill else None,
            "skill": res.skill.to_dict() if res.skill else None, "recording": rec.last.id,
        }

    # ledger

    @app.get("/ledger")
    async def ledger(n: int = 50, kind: str | None = None, bot_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
        return [e.to_dict() for e in h.ledger.tail(n, kind=kind, bot_id=bot_id, session_id=session_id)]

    return app


def _persist(h: Harness, bot: Bot) -> None:
    """The bot's identity lives in bot-prompts/<id>.md; a new bot gets its id from the file name."""
    taken: set[str] = set()
    if h.config.user_content:  # the user's folder is empty of shipped ids: a new bot must not take one, or it would shadow it
        taken = set(h.bots) | {f.stem for f in h.config.bot_prompts_dir.glob("*.md")} if h.config.bot_prompts_dir.is_dir() else set(h.bots)
    p = save_bot_prompt(h.config.bots_write_dir, BotPrompt(id=bot.id, name=bot.name, role=bot.role, instructions=bot.instructions, skills=[s.slug for s in bot.skills], mcps=list(bot.mcps) or None, studio=bot.studio, places=list(bot.places), closed=bot.closed), taken)
    bot.id = p.id


def _attach(h: Harness, bot: Bot, slugs: list[str]) -> None:
    for slug in slugs:
        skill = h.catalog.skill(slug)
        if skill is None:
            raise HTTPException(422, f"unknown skill {slug!r}")
        bot.attach_skill(skill, h.config.max_skills_per_bot)


def _bot(h: Harness, bot_id: str) -> Bot:
    try:
        return h.bots[bot_id]
    except KeyError:
        raise HTTPException(404, "no such bot") from None


def _session(h: Harness, session_id: str) -> Session:
    try:
        return h.sessions[session_id]
    except KeyError:
        raise HTTPException(404, "no such session") from None


def _session_brief(s: Session) -> dict[str, Any]:
    d = s.to_dict(redact=exclusion.redact_obj)
    d.pop("messages", None)
    return d


def _scope_dict(change) -> dict[str, Any]:
    return {
        "changed": change.changed, "probability": change.probability, "new_goal": change.new_goal,
        "new_constraints": change.new_constraints, "confirm": str(change.confirm),
    }


def _room_call(fn, *args):
    try:
        return fn(*args)
    except RoomError as e:
        raise HTTPException(404 if str(e).startswith("no room") else 422, str(e)) from None


def _room_status(room) -> dict[str, Any]:
    d, _ = exclusion.redact_obj(room.to_dict())
    return d


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
