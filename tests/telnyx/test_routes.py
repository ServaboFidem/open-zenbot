"""The /telnyx routes through the real app, Telnyx being the stand-in. No network."""

import json

import httpx
import pytest

from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.telnyx.routes import FORBIDDEN, telnyx_router
from src.telnyx.service import TelnyxService
from src.telnyx.settings import TEXTS_FILE
from src.vault_settings import settings_dir
from tests.conftest import ScriptedProvider
from tests.telnyx.conftest import KEY


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def calendar(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    yield s
    await s.close()


@pytest.fixture
async def harness(cfg, calendar, fake_telnyx):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(), calendar=calendar)
    built = h.telnyx
    h.telnyx = TelnyxService(cfg.vault_root, h.ledger, lambda: calendar.store, http=fake_telnyx.client)
    yield h
    await built.close()
    await h.discover.close()
    await h.mail.close()


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


def phone(vault):
    path = settings_dir(vault) / TEXTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"phone": "+15551234567", "before": True}), encoding="utf-8")


async def setup(client):
    assert (await client.put("/telnyx/key", json={"key": KEY})).status_code == 200
    assert (await client.put("/telnyx/number", json={"id": "n1"})).status_code == 200


def test_every_post_needs_a_body():
    posts = [r.path for r in telnyx_router(None).routes if "POST" in (getattr(r, "methods", None) or ())]
    assert posts == ["/telnyx/test"]


async def test_bodiless_post_is_refused(client):
    assert (await client.post("/telnyx/test")).status_code == 422
    assert (await client.post("/telnyx/test", content="x", headers={"content-type": "text/plain"})).status_code == 422


async def test_status_starts_empty(client):
    got = (await client.get("/telnyx/status")).json()
    assert (got["key_set"], got["number"], got["texts"]) == (False, None, None)


async def test_no_key_in_any_answer(client, vault):
    await setup(client)
    phone(vault)
    answers = [await client.get("/telnyx/status"), await client.get("/telnyx/numbers"),
               await client.put("/telnyx/number", json={"id": "n1"}), await client.post("/telnyx/test", json={})]
    assert [a.status_code for a in answers] == [200, 200, 200, 200]
    for answer in answers:
        assert KEY not in answer.text and "+15551234567" not in answer.text


async def test_key_refusals(client, fake_telnyx):
    assert (await client.put("/telnyx/key", json={"key": "a b"})).status_code == 422
    got = await client.put("/telnyx/key", json={"key": "WRONGKEY0000"})
    assert (got.status_code, got.json()["detail"]) == (422, "Telnyx did not accept this key.")
    fake_telnyx.fail["GET /messaging_profiles"] = httpx.ConnectError("down")
    assert (await client.put("/telnyx/key", json={"key": KEY})).status_code == 502


async def test_a_misshapen_key_body_never_echoes_the_key(client):
    for body in ({"key": [KEY]}, {"key": {"k": KEY}}, [KEY], KEY):
        got = await client.put("/telnyx/key", json=body)
        assert got.status_code == 422 and KEY not in got.text
    assert (await client.put("/telnyx/key", content=KEY, headers={"content-type": "text/plain"})).status_code == 422


async def test_numbers_and_choice(client):
    assert (await client.get("/telnyx/numbers")).status_code == 409
    await client.put("/telnyx/key", json={"key": KEY})
    assert (await client.get("/telnyx/numbers")).json() == \
        {"numbers": [{"id": "n1", "phone": "+15550001111", "profile_id": "p1"}]}
    assert (await client.put("/telnyx/number", json={"id": "nope"})).status_code == 404


async def test_test_route_409_and_429(client, vault, fake_telnyx):
    assert (await client.post("/telnyx/test", json={})).status_code == 409
    await setup(client)
    assert (await client.post("/telnyx/test", json={})).status_code == 409  # no phone saved in the desktop app
    phone(vault)
    assert (await client.post("/telnyx/test", json={})).status_code == 200
    assert (await client.post("/telnyx/test", json={})).status_code == 429
    assert len(fake_telnyx.sent) == 1


async def test_test_route_send_failure(client, vault, fake_telnyx):
    await setup(client)
    phone(vault)
    fake_telnyx.fail["POST /messages"] = 422
    got = await client.post("/telnyx/test", json={})
    assert got.status_code == 422 and "refused" in got.json()["detail"]


async def test_test_route_account_limit_does_not_blame_the_key(client, vault, fake_telnyx):
    # Telnyx answers 403 (code 10039) when the account may only text verified numbers; the key itself is fine
    await setup(client)
    phone(vault)
    fake_telnyx.fail["POST /messages"] = 403
    got = await client.post("/telnyx/test", json={})
    assert (got.status_code, got.json()["detail"]) == (403, FORBIDDEN)
    assert "key" not in got.json()["detail"]


async def test_numbers_403_is_not_a_bad_key(client, fake_telnyx):
    await client.put("/telnyx/key", json={"key": KEY})
    fake_telnyx.fail["GET /messaging_profiles"] = 403
    got = await client.get("/telnyx/numbers")
    assert (got.status_code, got.json()["detail"]) == (403, FORBIDDEN)
    fake_telnyx.fail["GET /messaging_profiles"] = 401
    got = await client.get("/telnyx/numbers")
    assert (got.status_code, got.json()["detail"]) == (401, "Telnyx no longer accepts the saved key. Save a new one.")


async def test_key_save_refused_by_permission(client, fake_telnyx):
    fake_telnyx.fail["GET /messaging_profiles"] = 403
    got = await client.put("/telnyx/key", json={"key": KEY})
    assert (got.status_code, got.json()["detail"]) == \
        (422, "This key may not list your Telnyx numbers. Check its permissions.")


async def test_forget_key(client):
    await setup(client)
    got = await client.delete("/telnyx/key")
    assert (got.status_code, got.json()["key_set"], got.json()["number"]) == (200, False, None)


async def test_harness_builds_its_own_service(cfg, calendar):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(), calendar=calendar)
    assert isinstance(h.telnyx, TelnyxService)
    assert h.telnyx.calendar_store() is calendar.store
    await h.telnyx.close()
    await h.discover.close()
    await h.mail.close()
