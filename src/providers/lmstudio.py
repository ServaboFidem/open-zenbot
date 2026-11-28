"""Reads LM Studio's model list for the context a model is loaded with. GET only, never changes the server."""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class LmModel:
    id: str
    type: str
    max_context: int | None
    loaded_context: int | None
    loaded: bool


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5.0)


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str(v: object) -> str:
    return v if isinstance(v, str) else ""


def _v1(items: list) -> list[LmModel]:
    out = []
    for m in items:
        if not isinstance(m, dict) or not _str(m.get("key")):
            continue
        inst = m.get("loaded_instances")
        first = inst[0] if isinstance(inst, list) and inst else None
        cfg = first.get("config") if isinstance(first, dict) else None
        ctx = _int(cfg.get("context_length")) if isinstance(cfg, dict) else None
        out.append(LmModel(m["key"], _str(m.get("type")), _int(m.get("max_context_length")), ctx, bool(first)))
    return out


def _v0(items: list) -> list[LmModel]:
    out = []
    for m in items:
        if not isinstance(m, dict) or not _str(m.get("id")):
            continue
        out.append(LmModel(m["id"], _str(m.get("type")), _int(m.get("max_context_length")),
                           _int(m.get("loaded_context_length")), m.get("state") == "loaded"))
    return out


async def read_models(base_url: str, key: str | None, client: httpx.AsyncClient) -> list[LmModel] | None:
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    for path, field, parse in (("/api/v1/models", "models", _v1), ("/api/v0/models", "data", _v0)):
        try:
            r = await client.get(root + path, headers=headers)
            body = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            continue
        items = body.get(field) if isinstance(body, dict) else None
        if isinstance(items, list):
            return parse(items)
    return None


def find(models: list[LmModel], model_id: str) -> LmModel | None:
    for m in models:
        if m.id == model_id:
            return m
    tail = model_id.rsplit("/", 1)[-1]
    hits = [m for m in models if m.id.rsplit("/", 1)[-1] == tail]
    return hits[0] if len(hits) == 1 else None
