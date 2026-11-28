"""The media route: remote images, video and PDFs fetched for the chat, trusted hosts at once and
others only on the user's click, never from a private address."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.harness.media import DEFAULT_TRUSTED, USER_AGENT, MediaProxy, host_trusted, trusted_hosts
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.skills.loader import load_skill

from .conftest import ScriptedProvider

PUBLIC = "93.184.216.34"


@pytest.fixture
def harness(vault: Path, tmp_path: Path, example_skill_dir: Path) -> Harness:
    catalog = Catalog()
    catalog.add_skill(load_skill(example_skill_dir).skill)
    cfg = HarnessConfig(vault_root=vault, skills_dir=example_skill_dir.parent, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=catalog)
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def proxy(handler, addresses=None, hosts=("images.pexels.com",)) -> MediaProxy:
    """A proxy whose upstream is `handler` and whose DNS answers from `addresses` (host -> ips)."""
    addresses = addresses or {}

    async def resolve(host: str) -> list[str]:
        return addresses.get(host, [PUBLIC])

    return MediaProxy(hosts, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolve=resolve)


def jpeg(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"\xff\xd8jpegbytes", headers={"Content-Type": "image/jpeg"})


def media(url: str, ok: str = "") -> str:
    return "/media?" + str(httpx.QueryParams({"url": url, **({"ok": ok} if ok else {})}))


async def consent(client, url: str) -> str:
    """The token the GUI gets for `url` on the user's click."""
    r = await client.post("/media/consent", json={"urls": [url]})
    assert r.status_code == 200
    return r.json()["tokens"][url]


def test_trusted_hosts_default_and_env():
    assert trusted_hosts({}) == DEFAULT_TRUSTED
    assert DEFAULT_TRUSTED[:2] == ("images.pexels.com", "videos.pexels.com")
    assert trusted_hosts({"ZEN_MEDIA_TRUSTED_HOSTS": " cdn.example.com, ,Img.Other.org, upload.wikimedia.org "}) == (
        *DEFAULT_TRUSTED, "cdn.example.com", "img.other.org")


def test_museum_pictures_load_without_a_click():
    for url in ("https://images.metmuseum.org/CRDImages/ad/web-large/DP-29006-001.jpg",
                "https://upload.wikimedia.org/wikipedia/commons/1/12/John_Singer_Sargent_001.jpg",
                "https://www.artic.edu/iiif/2/abc/full/843,/0/default.jpg",
                "https://iiif.micr.io/xyz/full/max/0/default.jpg"):
        assert host_trusted(url, trusted_hosts({}))
    assert not host_trusted("https://commons.wikimedia.org/wiki/File:X.jpg", trusted_hosts({}))  # pages are not pictures
    assert not host_trusted("https://upload.wikimedia.org.evil.net/x.jpg", trusted_hosts({}))


def test_the_gui_starts_with_the_same_trusted_list():
    jsx = (Path(__file__).resolve().parents[1] / "src" / "gui" / "MediaCards.jsx").read_text("utf-8")
    block = re.search(r"const DEFAULT_TRUSTED = \[(.*?)\];", jsx, re.S)
    assert block and tuple(re.findall(r"'([^']+)'", block.group(1))) == DEFAULT_TRUSTED


def test_host_trusted_matches_exact_and_subdomain_only():
    hosts = ("images.pexels.com", "example.com")
    assert host_trusted("https://images.pexels.com/photos/1/a.jpeg?w=940", hosts)
    assert host_trusted("https://a.b.example.com/x.png", hosts)
    assert not host_trusted("https://notexample.com/x.png", hosts)
    assert not host_trusted("https://images.pexels.com.evil.net/x.png", hosts)


async def test_untrusted_needs_the_click(client, harness):
    harness.media = proxy(jpeg)
    r = await client.get(media("https://cdn.example.net/a.jpg"))
    assert r.status_code == 403
    r = await client.get(media("https://cdn.example.net/a.jpg", ok=await consent(client, "https://cdn.example.net/a.jpg")))
    assert r.status_code == 200 and r.content == b"\xff\xd8jpegbytes"


async def test_the_click_cannot_be_claimed_with_a_flag_or_another_urls_token(client, harness):
    """A bot-made page can navigate its own frame to /media with any query: only a token for this URL counts."""
    harness.media = proxy(jpeg)
    other = await consent(client, "https://cdn.example.net/a.jpg")
    for ok in ("1", "true", other, "\u00e9"):
        assert (await client.get(media("https://collector.example.net/?d=secret", ok=ok))).status_code == 403
    token = await consent(client, "https://cdn.example.net/a.jpg")
    for _ in range(2):  # a video's range requests reuse the token
        assert (await client.get(media("https://cdn.example.net/a.jpg", ok=token))).status_code == 200


async def test_consent_is_json_only_and_bounded(client):
    body = '{"urls": ["https://cdn.example.net/a.jpg"]}'
    for kind in ("text/plain", "application/x-www-form-urlencoded"):  # what a page elsewhere can send without a preflight
        r = await client.post("/media/consent", content=body, headers={"Content-Type": kind})
        assert r.status_code in (415, 422) and "tokens" not in r.text
    assert (await client.post("/media/consent", json={"urls": ["https://a/x"] * 21})).status_code == 422
    assert (await client.post("/media/consent", json={"urls": ["https://a/" + "x" * 4096]})).status_code == 422


async def test_trusted_streams_with_headers(client, harness):
    harness.media = proxy(jpeg)
    r = await client.get(media("https://images.pexels.com/photos/1/p.jpeg?w=940"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.content == b"\xff\xd8jpegbytes"


async def test_range_passes_through_as_206(client, harness):
    seen = {}

    def video(request: httpx.Request) -> httpx.Response:
        seen["range"] = request.headers.get("range")
        return httpx.Response(206, content=b"0123456789", headers={
            "Content-Type": "video/mp4", "Content-Range": "bytes 0-9/100", "Accept-Ranges": "bytes"})

    harness.media = proxy(video, hosts=("videos.pexels.com",))
    r = await client.get(media("https://videos.pexels.com/v/1.mp4"), headers={"Range": "bytes=0-9"})
    assert seen["range"] == "bytes=0-9"
    assert r.status_code == 206 and r.headers["content-range"] == "bytes 0-9/100"
    assert r.headers["accept-ranges"] == "bytes" and r.content == b"0123456789"


async def test_html_at_an_image_url_is_415(client, harness):
    harness.media = proxy(lambda req: httpx.Response(200, text="<html>login</html>", headers={"Content-Type": "text/html"}))
    assert (await client.get(media("https://images.pexels.com/p/1.jpeg"))).status_code == 415


async def test_private_address_refused_even_when_clicked(client, harness):
    harness.media = proxy(jpeg, addresses={"intranet.local": ["127.0.0.1"]})
    assert (await client.get(media("http://intranet.local/a.png", ok=await consent(client, "http://intranet.local/a.png")))).status_code == 403


async def test_redirect_to_private_address_refused(client, harness):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.pexels.com":
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/x.png"})
        return jpeg(request)

    harness.media = proxy(handler, addresses={"10.0.0.5": ["10.0.0.5"]})
    assert (await client.get(media("https://images.pexels.com/p/1.jpeg"))).status_code == 403


async def test_redirect_to_an_untrusted_host_waits_for_the_click(client, harness):
    """A trusted host that redirects (an open redirect) must not load an untrusted host on its own."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.pexels.com":
            return httpx.Response(302, headers={"Location": "https://cdn2.example.org/x.jpeg"})
        return jpeg(request)

    harness.media = proxy(handler)
    assert (await client.get(media("https://images.pexels.com/p/1.jpeg"))).status_code == 403
    r = await client.get(media("https://images.pexels.com/p/1.jpeg", ok=await consent(client, "https://images.pexels.com/p/1.jpeg")))
    assert r.status_code == 200 and r.content == b"\xff\xd8jpegbytes"


async def test_redirect_between_trusted_hosts_is_followed(client, harness):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.pexels.com":
            return httpx.Response(302, headers={"Location": "https://cdn.images.pexels.com/x.jpeg"})
        return jpeg(request)

    harness.media = proxy(handler)
    r = await client.get(media("https://images.pexels.com/p/1.jpeg"))
    assert r.status_code == 200 and r.content == b"\xff\xd8jpegbytes"


@pytest.mark.parametrize("address", ["::ffff:127.0.0.1", "64:ff9b::7f00:1", "::127.0.0.1", "64:ff9b::a00:5"])
def test_ipv4_inside_ipv6_is_judged_by_the_ipv4(address):
    from src.harness.media import _public

    assert not _public(address)
    assert _public("64:ff9b::5db8:d822")  # 93.184.216.34, public


async def test_dns_rebinding_is_refused_at_connect():
    """The host answers a public address to the check and a private one to the connection: the
    connection is refused before any socket opens, because the connect step resolves and checks itself."""
    answers = iter([[PUBLIC], ["192.168.1.1"]])

    async def resolve(host: str) -> list[str]:
        return next(answers)

    from src.harness.media import MediaRefused

    # .invalid never resolves for real: without the connect-time check this is a 502, never a 403
    proxy_ = MediaProxy(("rebind.invalid",), resolve=resolve)
    try:
        with pytest.raises(MediaRefused) as e:
            await proxy_.open("http://rebind.invalid/x.jpeg", clicked=False, range_header=None)
        assert e.value.status == 403
    finally:
        await proxy_.client.aclose()


async def test_guarded_connect_dials_the_vetted_address():
    from src.harness.media import GuardedBackend

    dialed = []

    class Inner:
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            dialed.append((host, port))
            return "stream"

    async def resolve(host: str) -> list[str]:
        return [PUBLIC]

    backend = GuardedBackend(resolve, inner=Inner())
    assert await backend.connect_tcp("images.pexels.com", 443) == "stream"
    assert dialed == [(PUBLIC, 443)]


async def test_upstream_404_is_404(client, harness):
    harness.media = proxy(lambda req: httpx.Response(404))
    assert (await client.get(media("https://images.pexels.com/p/gone.jpeg"))).status_code == 404


async def test_non_http_scheme_is_400(client, harness):
    harness.media = proxy(jpeg)
    assert (await client.get(media("file:///C:/x.png", ok=await consent(client, "file:///C:/x.png")))).status_code == 400


async def test_settings_lists_trusted_hosts(client):
    assert "images.pexels.com" in (await client.get("/settings")).json()["media_trusted_hosts"]


async def test_every_fetch_names_the_app_not_the_http_library():
    # upload.wikimedia.org answers 403 to a client's default User-Agent ("python-httpx/...") or none, so the
    # museum server's Wikimedia Commons images failed even after the user's click.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"Location": "https://upload.wikimedia.org/a.jpg"})
        return jpeg(request)

    p = proxy(handler, hosts=("upload.wikimedia.org",))
    for url in ("https://upload.wikimedia.org/a.jpg", "https://upload.wikimedia.org/hop"):
        stream = await p.open(url, clicked=False, range_header=None)
        await stream.close()
    assert seen == [USER_AGENT] * 3  # a redirect's next hop too
    assert USER_AGENT.startswith("OpenZenBot/") and "python-httpx" not in USER_AGENT
