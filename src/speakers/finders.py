"""The three ways of finding speakers on the home network, each behind one `Finder` protocol. Only this module imports
pyatv, pychromecast, async_upnp_client and zeroconf. No zeroconf is shared or kept between looks: pyatv's own scanner
sends its queries and listens for `timeout` seconds (given a zeroconf, it would only read that one's cache), and each
Chromecast look gets the zeroconf pychromecast makes for it, which `stop_discovery` closes.

Everything a device announces is untrusted: names and models are cleaned and capped (`settings.clean`), and the one
request a finder makes on its own, the DLNA description, goes only to the address that answered the search (never
through a proxy), over plain http, uncompressed, with no redirect followed, a size and time limit, and an XML parser
that refuses entities (defusedxml). One responder that fails any of these is skipped, never the whole look. A failure
is a `FindError` whose kind is `network` or `other`; nothing here logs a name or an address."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Callable, Protocol as TypingProtocol
from urllib.parse import urlsplit

import httpx

from src.speakers.settings import KEY_CAP, Speaker, clean

DLNA_TARGET = "urn:schemas-upnp-org:device:MediaRenderer:1"
DESCRIPTION_MAX = 262_144
DESCRIPTION_TIMEOUT_S = 4.0


class FindError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _kind(exc: BaseException) -> str:
    return "network" if isinstance(exc, (OSError, TimeoutError, asyncio.TimeoutError, httpx.TransportError)) else "other"


class Finder(TypingProtocol):
    async def find(self, timeout: float) -> list[Speaker]: ...


def _key(protocol: str, ident: object) -> str:
    text = clean(str(ident) if ident is not None else "", KEY_CAP - len(protocol) - 1)
    return f"{protocol}:{text}" if text else ""


def _unique(found: list[Speaker]) -> list[Speaker]:
    seen: dict[str, Speaker] = {}
    for s in found:
        if s.key and s.key not in seen:
            seen[s.key] = s
    return list(seen.values())


class AirPlayFinder:
    """pyatv's scan for AirPlay and RAOP. A device is kept only when it offers RAOP, the AirPlay audio service: an
    Apple TV, a HomePod, an AirPlay speaker or a Mac does; a remote-control-only or video-only one does not."""

    def __init__(self, scan: Callable[..., Any] | None = None) -> None:
        self._scan = scan

    async def find(self, timeout: float) -> list[Speaker]:
        try:
            from pyatv.const import Protocol
            scan = self._scan
            if scan is None:
                import pyatv
                scan = pyatv.scan
            confs = await scan(asyncio.get_running_loop(), timeout=int(timeout),
                               protocol={Protocol.AirPlay, Protocol.RAOP})
            found = []
            for c in confs:
                if not any(getattr(s, "protocol", None) == Protocol.RAOP for s in (getattr(c, "services", None) or [])):
                    continue
                info = getattr(c, "device_info", None)
                found.append(Speaker(_key("airplay", getattr(c, "identifier", None)), "airplay", clean(getattr(c, "name", "")),
                                     clean(getattr(info, "model_str", "") if info is not None else ""), str(getattr(c, "address", ""))[:64]))
            return _unique(found)
        except FindError:
            raise
        except Exception as e:  # noqa: BLE001 - any library failure is this protocol's error, never the harness's
            raise FindError(_kind(e)) from None


class CastFinder:
    """PyChromecast's discovery, which blocks, in a thread, on a zeroconf of its own for this look. Every cast type is
    kept: a speaker group is a place to play too. The browser is always stopped, which also closes that zeroconf, so a
    look cut off by the service still cleans up after itself when its thread ends."""

    def __init__(self, discover: Callable[..., Any] | None = None) -> None:
        self._discover = discover

    async def find(self, timeout: float) -> list[Speaker]:
        try:
            discover = self._discover
            if discover is None:
                from pychromecast.discovery import discover_chromecasts
                discover = discover_chromecasts
            def run() -> list[Any]:
                infos, browser = discover(timeout=timeout)
                try:
                    return list(infos)
                finally:
                    browser.stop_discovery()
            infos = await asyncio.to_thread(run)
            return _unique([Speaker(_key("chromecast", getattr(i, "uuid", None)), "chromecast", clean(getattr(i, "friendly_name", "")),
                                    clean(getattr(i, "model_name", "")), str(getattr(i, "host", ""))[:64]) for i in infos])
        except FindError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FindError(_kind(e)) from None


def _same_host(location: str, host: str) -> bool:
    try:
        url = urlsplit(location)
        if url.scheme != "http" or not url.hostname or url.username or url.password:
            return False
        return ipaddress.ip_address(url.hostname) == ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_description(body: bytes) -> tuple[str, str, str] | None:
    """(udn, friendlyName, modelName) of the root device, or None."""
    from defusedxml import ElementTree
    try:
        root = ElementTree.fromstring(body)
    except Exception:  # noqa: BLE001 - malformed, or entities defusedxml refuses
        return None
    device = next((child for child in root if _local(child.tag) == "device"), None)
    if device is None:
        return None
    fields = {_local(child.tag): (child.text or "") for child in device}
    udn = fields.get("UDN", "").strip()
    return (udn, fields.get("friendlyName", ""), fields.get("modelName", "")) if udn else None


class DlnaFinder:
    """An SSDP search for media renderers, then each responder's description, read once, for its name and UDN."""

    def __init__(self, search: Callable[..., Any] | None = None, http: httpx.AsyncClient | None = None) -> None:
        self._search = search
        self.http = http

    async def _description(self, http: httpx.AsyncClient, location: str, host: str) -> Speaker | None:
        if not _same_host(location, host):
            return None
        try:
            body = await asyncio.wait_for(self._read(http, location), DESCRIPTION_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a bad URL, a slow or broken responder: skip it, never the whole look
            return None
        if body is None:
            return None
        parsed = _parse_description(bytes(body))
        if parsed is None:
            return None
        udn, name, model = parsed
        return Speaker(_key("dlna", udn), "dlna", clean(name), clean(model), host[:64])

    @staticmethod
    async def _read(http: httpx.AsyncClient, location: str) -> bytes | None:
        """The body as sent, or None. Only an uncompressed answer is read, so the limit counts what arrives."""
        headers = {"Accept-Encoding": "identity"}
        async with http.stream("GET", location, headers=headers, follow_redirects=False, timeout=DESCRIPTION_TIMEOUT_S) as r:
            if r.status_code != 200 or r.headers.get("content-encoding", "identity").lower() != "identity":
                return None
            body = bytearray()
            async for chunk in r.aiter_bytes():  # identity only (checked above), so nothing is decoded
                body += chunk
                if len(body) > DESCRIPTION_MAX:
                    return None
            return bytes(body)

    async def find(self, timeout: float) -> list[Speaker]:
        try:
            search = self._search
            if search is None:
                from async_upnp_client.search import async_search
                search = async_search
            responders: dict[str, str] = {}

            async def heard(headers: Any) -> None:
                location, host = headers.get("location"), headers.get("_host")
                if isinstance(location, str) and isinstance(host, str) and len(responders) < 256:
                    responders.setdefault(location, host)
            await search(async_callback=heard, timeout=max(1, int(timeout)), search_target=DLNA_TARGET)
            http = self.http or _client()
            try:
                found = await asyncio.gather(*(self._description(http, loc, host) for loc, host in responders.items()))
            finally:
                if self.http is None:
                    await http.aclose()
            return _unique([s for s in found if s is not None])
        except FindError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FindError(_kind(e)) from None


def _client() -> httpx.AsyncClient:
    """The DLNA finder's own client: no proxy from the environment, so a description is asked of the responder itself."""
    return httpx.AsyncClient(trust_env=False)


def real_finders() -> dict[str, Finder]:
    return {"airplay": AirPlayFinder(), "chromecast": CastFinder(), "dlna": DlnaFinder()}
