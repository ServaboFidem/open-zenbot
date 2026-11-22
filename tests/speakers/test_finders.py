"""The three finders against stand-ins: no multicast, no device. Each library's discovery call is passed in."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import httpx
import pytest
from pyatv.const import Protocol

from src.speakers.finders import AirPlayFinder, CastFinder, DlnaFinder, FindError
from src.speakers.settings import Speaker


def _conf(identifier, name, address, model, *protocols):
    return NS(identifier=identifier, name=name, address=address, device_info=NS(model_str=model),
              services=[NS(protocol=p) for p in protocols])


async def test_airplay_keeps_audio_devices_and_cleans_names():
    seen = {}

    async def scan(loop, timeout=5, protocol=None, **kw):
        seen.update(timeout=timeout, protocol=protocol)
        return [_conf("ID1", "Kit\x07chen", "10.0.0.5", "HomePod", Protocol.RAOP, Protocol.AirPlay),
                _conf("ID2", "Remote only", "10.0.0.6", "Apple TV", Protocol.MRP),
                _conf("ID3", "Video only", "10.0.0.7", "TV", Protocol.AirPlay)]
    found = await AirPlayFinder(scan=scan).find(5)
    assert found == [Speaker("airplay:ID1", "airplay", "Kitchen", "HomePod", "10.0.0.5")]
    assert seen["protocol"] == {Protocol.AirPlay, Protocol.RAOP}


async def test_cast_lists_every_cast_type_and_stops_discovery():
    stops = []
    browser = NS(stop_discovery=lambda: stops.append(1))

    def discover(timeout=5, **kw):
        return [NS(uuid="u-1", friendly_name="Bedroom", model_name="Nest Mini", host="10.0.0.8", cast_type="audio"),
                NS(uuid="u-2", friendly_name="Downstairs", model_name="Google Cast Group", host="10.0.0.9", cast_type="group")], browser
    found = await CastFinder(discover=discover).find(5)
    assert [s.key for s in found] == ["chromecast:u-1", "chromecast:u-2"] and found[0].name == "Bedroom"
    assert stops == [1]

    def broken(**kw):
        raise OSError("no network")
    with pytest.raises(FindError) as e:
        await CastFinder(discover=broken).find(5)
    assert e.value.kind == "network"


DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:ZonePlayer:1</deviceType>
<friendlyName>Living Room</friendlyName><modelName>SYMFONISK</modelName><UDN>uuid:RINCON_1</UDN>
</device></root>"""


def _search(*responses):
    async def search(async_callback, timeout=4, search_target="ssdp:all", **kw):
        assert search_target == "urn:schemas-upnp-org:device:MediaRenderer:1"
        for r in responses:
            await async_callback(r)
    return search


async def test_dlna_reads_the_description_on_the_responder_only():
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, text=DESCRIPTION)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await DlnaFinder(search=_search({"location": "http://10.0.0.9:1400/xml/device_description.xml", "_host": "10.0.0.9"}), http=http).find(4)
    assert found == [Speaker("dlna:uuid:RINCON_1", "dlna", "Living Room", "SYMFONISK", "10.0.0.9")]
    assert asked == ["http://10.0.0.9:1400/xml/device_description.xml"]


BOMB = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>&lol2;</friendlyName><UDN>uuid:X</UDN></device></root>"""


@pytest.mark.parametrize("location,response", [
    ("http://10.0.0.99:1400/d.xml", httpx.Response(200, text=DESCRIPTION)),
    ("https://10.0.0.9:1400/d.xml", httpx.Response(200, text=DESCRIPTION)),
    ("http://10.0.0.9:1400/d.xml", httpx.Response(302, headers={"location": "http://10.0.0.99/d.xml"})),
    ("http://10.0.0.9:1400/d.xml", httpx.Response(200, content=b"<root>" + b"x" * 300_000 + b"</root>")),
    ("http://10.0.0.9:1400/d.xml", httpx.Response(200, text="<root><device>")),
    ("http://10.0.0.9:1400/d.xml", httpx.Response(200, text=BOMB)),
    ("http://10.0.0.9:1400/d.xml", httpx.Response(200, text="<root/>")),
])
async def test_dlna_skips_other_hosts_https_redirects_big_and_bad_xml(location, response):
    asked = []

    def handler(request):
        asked.append(request.url.host)
        return response
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await DlnaFinder(search=_search({"location": location, "_host": "10.0.0.9"}), http=http).find(4)
    assert found == [] and set(asked) <= {"10.0.0.9"}


@pytest.mark.parametrize("exc,kind", [(OSError("down"), "network"), (TimeoutError(), "network"), (RuntimeError("x"), "other")])
async def test_a_library_failure_is_a_find_error(exc, kind):
    async def scan(*a, **kw):
        raise exc
    with pytest.raises(FindError) as e:
        await AirPlayFinder(scan=scan).find(5)
    assert e.value.kind == kind


# --- final review fixes ------------------------------------------------------------------------------------------


async def test_airplay_scan_sends_its_own_queries():
    """pyatv given an aiozc only reads that zeroconf's cache, which nothing fills: it must use its own scanner."""
    seen = {}

    async def scan(loop, timeout=5, protocol=None, **kw):
        seen.update(kw)
        return []
    await AirPlayFinder(scan=scan).find(5)
    assert seen.get("aiozc") is None


async def test_cast_gives_each_look_its_own_zeroconf():
    """pychromecast's stop_discovery closes the zeroconf it was given: so it is never handed one to share."""
    given = []

    def discover(timeout=5, **kw):
        given.append(kw.get("zeroconf_instance"))
        return [], NS(stop_discovery=lambda: None)
    finder = CastFinder(discover=discover)
    await finder.find(5)
    await finder.find(5)
    assert given == [None, None]


class Drip(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"<root>"
        await asyncio.sleep(3600)
        yield b"</root>"


async def test_dlna_one_bad_or_slow_responder_never_hides_the_others(monkeypatch):
    from src.speakers import finders
    monkeypatch.setattr(finders, "DESCRIPTION_TIMEOUT_S", 0.05)

    def handler(request):
        if request.url.path == "/slow.xml":
            return httpx.Response(200, stream=Drip())
        return httpx.Response(200, text=DESCRIPTION)
    search = _search({"location": "http://10.0.0.9:abc/d.xml", "_host": "10.0.0.9"},
                     {"location": "http://10.0.0.9/\x00.xml", "_host": "10.0.0.9"},
                     {"location": "http://10.0.0.9:1400/slow.xml", "_host": "10.0.0.9"},
                     {"location": "http://10.0.0.9:1400/good.xml", "_host": "10.0.0.9"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await asyncio.wait_for(DlnaFinder(search=search, http=http).find(4), 5)
    assert [s.key for s in found] == ["dlna:uuid:RINCON_1"]


async def test_dlna_asks_for_no_compression_and_refuses_a_compressed_body():
    import gzip
    asked = []

    def handler(request):
        asked.append(request.headers.get("accept-encoding"))
        return httpx.Response(200, content=gzip.compress(DESCRIPTION.encode()), headers={"content-encoding": "gzip"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await DlnaFinder(search=_search({"location": "http://10.0.0.9:1400/d.xml", "_host": "10.0.0.9"}), http=http).find(4)
    assert found == [] and asked == ["identity"]


async def test_dlna_own_client_ignores_proxy_settings():
    from src.speakers import finders
    client = finders._client()
    try:
        assert client.trust_env is False
    finally:
        await client.aclose()
