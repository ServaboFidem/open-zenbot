"""The summary model's context window: read from LM Studio, learned from a refusal, and reported.

Only a local provider is read; a hosted one keeps full-size pieces and never warns.
Nothing here holds message or summary text: numbers and ids only.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from src.context.fit import MAX_PIECE, RECOMMENDED_CONTEXT, ContextTooSmall, Overrun, piece_chars
from src.generation.generator import SUMMARY_SYSTEM
from src.providers.lmstudio import LmModel, find, read_models
from src.providers.store import ProviderEntry

ALTERNATIVES_MAX = 8


class SummaryContext:
    def __init__(self, *, entry: ProviderEntry | None, model: str, key: Callable[[], str | None],
                 client: httpx.AsyncClient, clock: Callable[[], float] = time.monotonic,
                 ttl: float = 300.0) -> None:
        self._entry = entry
        self._model = model
        self._key = key
        self._client = client
        self._clock = clock
        self._ttl = ttl
        self._reset()

    @property
    def entry(self) -> ProviderEntry | None:
        return self._entry

    async def aclose(self) -> None:
        await self._client.aclose()

    def _reset(self) -> None:
        self._models: list[LmModel] | None = None
        self._read_at: float | None = None
        self._learned: int | None = None
        self._overrun: dict[str, Any] | None = None
        self._recovered: str | None = None

    def _hosted(self) -> bool:
        return self._entry is None or not self._entry.local()

    async def _read(self, fresh: bool) -> list[LmModel] | None:
        if self._hosted():
            return None
        if not fresh and self._read_at is not None and self._clock() - self._read_at < self._ttl:
            return self._models
        assert self._entry is not None
        self._models = await read_models(self._entry.base_url, self._key(), self._client)
        self._read_at = self._clock()
        return self._models

    def _bound(self) -> LmModel | None:
        return find(self._models, self._model) if self._models is not None else None

    async def limit(self) -> int | None:
        if self._hosted():
            return None
        await self._read(False)
        bound = self._bound()
        if bound is not None and bound.loaded_context is not None:
            return bound.loaded_context
        return self._learned

    def learn(self, o: Overrun) -> None:
        if self._hosted() or o.n_ctx is None:
            return
        self._learned = o.n_ctx

    def record_overrun(self, where: str, o: Overrun | None, conversation_id: str = "") -> None:
        if self._hosted():
            return
        self._overrun = {
            "at": datetime.now(timezone.utc).isoformat(),
            "where": where,
            "n_ctx": o.n_ctx if o else None,
            "n_prompt_tokens": o.n_prompt_tokens if o else None,
        }
        self._recovered = None

    def record_success(self) -> None:
        if self._overrun is not None and self._recovered is None:
            self._recovered = datetime.now(timezone.utc).isoformat()

    def rebind(self, entry: ProviderEntry | None, model: str) -> None:
        """A new model or provider drops what was read and learned; the same one keeps it."""
        same = model == self._model and (entry.id if entry else None) == (self._entry.id if self._entry else None)
        self._entry = entry
        if same:
            return
        self._model = model
        self._reset()

    def _warning(self, bound: LmModel | None) -> str:
        if self._hosted():
            return ""
        if self._overrun is not None and self._recovered is None:
            return "overrun"
        if bound is not None and bound.loaded_context is not None:
            try:
                small = piece_chars(bound.loaded_context, len(SUMMARY_SYSTEM)) < MAX_PIECE
            except ContextTooSmall:
                small = True
            if small:
                at_max = bound.max_context is not None and bound.loaded_context >= bound.max_context
                return "at_max" if at_max else "small"
        return ""

    async def status(self, *, fresh: bool, enabled: set[str]) -> dict[str, Any]:
        await self._read(fresh)
        hosted = self._hosted()
        bound = None if hosted else self._bound()
        server = "hosted" if hosted else ("lmstudio" if self._models is not None else "other")
        max_ctx = bound.max_context if bound else None
        alternatives = []
        if not hosted and self._models is not None:
            alts = [m for m in self._models if m.type == "llm" and m is not bound
                    and m.max_context is not None and m.max_context >= RECOMMENDED_CONTEXT]
            alts.sort(key=lambda m: m.max_context or 0, reverse=True)
            alternatives = [{"id": m.id, "max_context": m.max_context, "loaded": m.loaded,
                             "enabled": m.id in enabled} for m in alts[:ALTERNATIVES_MAX]]
        return {
            "provider": {"id": self._entry.id, "name": self._entry.name} if self._entry else None,
            "model": self._model,
            "server": server,
            "loaded_context": bound.loaded_context if bound else None,
            "max_context": max_ctx,
            "loaded": bound.loaded if bound else False,
            "learned_context": self._learned,
            "last_overrun": dict(self._overrun) if self._overrun else None,
            "recovered_since": self._recovered,
            "warning": self._warning(bound),
            "recommended_context": min(max_ctx, RECOMMENDED_CONTEXT) if max_ctx else RECOMMENDED_CONTEXT,
            "alternatives": alternatives,
        }
