"""Each Facebook POST refuses simple cross-site sends.

Covers only the Facebook router: every POST needs a JSON body, so an empty,
bare, or cross-site send fails with 422 and keeps nothing.
"""

from __future__ import annotations

import json
import sys
import types

import httpx
import pytest

try:
    import src.events.facebook_pool  # noqa: F401 -- real mapper when the fb-pool slice has landed
except ImportError:  # the walk proves the body rule, which runs before any mapping
    stub = types.ModuleType("src.events.facebook_pool")
    stub.SOURCES = ("facebook", "facebook-birthday")
    stub.SOURCE = "facebook"
    stub.BIRTHDAY_SOURCE = "facebook-birthday"

    def _unused(*args, **kwargs):  # pragma: no cover -- never reached on a 422 walk
        raise AssertionError("facebook_pool stub must not be reached")

    stub.map_batch = _unused
    stub.row_key = _unused
    sys.modules["src.events.facebook_pool"] = stub

from src.events.service import CalendarService
from src.harness.api import Harness
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def service(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    yield s
    await s.close()


@pytest.fixture
def harness(cfg, service):
    return Harness(config=cfg, provider=ScriptedProvider(), ledger=service.ledger,
                   catalog=Catalog(), calendar=service)


@pytest.fixture
async def client(harness):
    from fastapi import FastAPI

    from src.events.facebook_routes import facebook_router

    app = FastAPI()
    app.include_router(facebook_router(harness.discover, harness))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://localhost") as c:
        yield c


def facebook_posts(harness) -> list[str]:
    from src.events.facebook_routes import facebook_router

    router = facebook_router(harness.discover, harness)
    return sorted(r.path for r in router.routes if "POST" in (getattr(r, "methods", None) or ()))


async def test_no_facebook_post_can_be_sent_as_a_simple_cross_site_request(client, harness):
    paths = facebook_posts(harness)
    assert paths == ["/intake/facebook", "/intake/facebook/events/{event_id}/dismiss",
                     "/intake/facebook/refresh"]
    foreign = {"Origin": "https://evil.example"}
    full = json.dumps({"helper": "fb-events-v1"}).encode()
    for path in paths:
        for body in (b"", b"{}", full):
            for headers in (foreign, {**foreign, "Content-Type": "text/plain"},
                            {**foreign, "Content-Type": "application/x-www-form-urlencoded"}):
                r = await client.post(path, content=body, headers=headers)
                assert r.status_code == 422, (path, body, headers, r.status_code)
    store = harness.discover.store
    assert store.pool_size("facebook") == 0
    assert store.pool_size("facebook-birthday") == 0
