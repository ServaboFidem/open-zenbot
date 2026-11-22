"""The three ways of finding speakers on the home network, each behind one `Finder` protocol, and the one zeroconf
listener AirPlay and Chromecast share. Only this module imports pyatv, pychromecast, async_upnp_client and zeroconf.

Everything a device announces is untrusted: names and models are cleaned and capped (`settings.clean`), and the one
request a finder makes on its own, the DLNA description, goes only to the address that answered the search, over
plain http, with no redirect followed, a size limit, and an XML parser that refuses entities (defusedxml). A failure
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


class SharedZeroconf:
    """One AsyncZeroconf for AirPlay and Chromecast, made on the first look that needs it (so nothing listens while
    both are off) and closed with the service."""

    def __init__(self) -> None:
        self._zc: Any = None

    async def get(self) -> Any:
        if self._zc is None:
            from zeroconf.asyncio import AsyncZeroconf
            self._zc = AsyncZeroconf()
        return self._zc

    async def close(self) -> None:
        zc, self._zc = self._zc, None
        if zc is not None:
            await zc.async_close()


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

    def __init__(self, zc: SharedZeroconf, scan: Callable[..., Any] | None = None) -> None:
        self.zc = zc
        self._scan = scan

    async def find(self, timeout: float) -> list[Speaker]:
        try:
            from pyatv.const import Protocol
            scan = self._scan
            if scan is None:
                import pyatv
                scan = pyatv.scan
            confs = await scan(asyncio.get_running_loop(), timeout=int(timeout),
                               protocol={Protocol.AirPlay, Protocol.RAOP}, aiozc=await self.zc.get())
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
    """PyChromecast's discovery, which blocks, in a thread, on the shared zeroconf. Every cast type is kept: a speaker
    group is a place to play too. The browser is always stopped."""

    def __init__(self, zc: SharedZeroconf, discover: Callable[..., Any] | None = None) -> None:
        self.zc = zc
        self._discover = discover

    async def find(self, timeout: float) -> list[Speaker]:
        try:
            discover = self._discover
            if discover is None:
                from pychromecast.discovery import discover_chromecasts
                discover = discover_chromecasts
            zc = (await self.zc.get()).zeroconf

            def run() -> list[Any]:
                infos, browser = discover(timeout=timeout, zeroconf_instance=zc)
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
            async with http.stream("GET", location, follow_redirects=False, timeout=DESCRIPTION_TIMEOUT_S) as r:
                if r.status_code != 200:
                    return None
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > DESCRIPTION_MAX:
                        return None
        except (httpx.HTTPError, OSError):
            return None
        parsed = _parse_description(bytes(body))
        if parsed is None:
            return None
        udn, name, model = parsed
        return Speaker(_key("dlna", udn), "dlna", clean(name), clean(model), host[:64])

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
            http = self.http or httpx.AsyncClient()
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


def real_finders(zc: SharedZeroconf) -> dict[str, Finder]:
    return {"airplay": AirPlayFinder(zc), "chromecast": CastFinder(zc), "dlna": DlnaFinder()}
