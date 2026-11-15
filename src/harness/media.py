"""Remote media for the chat: the GUI shows a bot's images, video and PDFs through this proxy, so the
page's CSP names only the harness origin. A trusted host is fetched at once; any other host only when
the request says the user clicked (`ok=1`). A bot that read a hostile page could be steered into putting
the user's data in an image URL, and loading it would send that data out, so nothing untrusted loads on
its own. No hop of a fetch may reach a private address: the proxy is not a way into the local network."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

DEFAULT_TRUSTED: tuple[str, ...] = ("images.pexels.com", "videos.pexels.com")
MAX_REDIRECTS = 3
ALLOWED_TYPES = ("image/", "video/", "application/pdf")
PASSED_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


def trusted_hosts(env: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """Pexels always, plus the comma-separated ZEN_MEDIA_TRUSTED_HOSTS, lower-cased."""
    extra = [h.strip().lower() for h in env.get("ZEN_MEDIA_TRUSTED_HOSTS", "").split(",")]
    return tuple(dict.fromkeys([*DEFAULT_TRUSTED, *(h for h in extra if h)]))


def host_trusted(url: str, hosts: Iterable[str]) -> bool:
    """The URL's host is a trusted host or a subdomain of one."""
    host = (urlsplit(url).hostname or "").lower()
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


class MediaRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class MediaStream:
    status: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    return [str(info[4][0]) for info in infos]


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPATIBLE = ipaddress.ip_network("::/96")


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):  # an IPv4 address carried inside IPv6 is judged as that IPv4
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in _NAT64 or ip in _V4_COMPATIBLE:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast


class _PrivateAddress(Exception):
    """Raised from inside the connection, so httpx passes it through unwrapped."""


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves the host when the connection opens and dials only a vetted public address, so a DNS
    answer cannot be public for the check and private for the connection (DNS rebinding). TLS still
    verifies and sends SNI for the URL's host: httpcore takes those from the origin, not from the dial."""

    def __init__(self, resolve: Callable[[str], Awaitable[list[str]]], inner: Any = None) -> None:
        self.resolve = resolve
        self.inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: Any = None) -> Any:
        try:
            addresses = await self.resolve(host)
        except OSError:
            raise httpcore.ConnectError(f"{host} could not be found") from None
        if not addresses or not all(_public(a) for a in addresses):
            raise _PrivateAddress(host)
        return await self.inner.connect_tcp(addresses[0], port, timeout=timeout, local_address=local_address,
                                            socket_options=socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> Any:
        raise _PrivateAddress(path)

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def _guarded_client(resolve: Callable[[str], Awaitable[list[str]]]) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport()
    # httpx has no public hook for the network backend; this is the pool it would build, dialing through the guard
    transport._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(),
                                                   network_backend=GuardedBackend(resolve))
    # trust_env off: a proxy from the environment would make the guard check the proxy, not the host
    return httpx.AsyncClient(transport=transport, trust_env=False, timeout=httpx.Timeout(30.0, connect=10.0))


class MediaProxy:
    def __init__(self, hosts: Iterable[str], client: httpx.AsyncClient | None = None,
                 resolve: Callable[[str], Awaitable[list[str]]] | None = None) -> None:
        self.hosts = tuple(hosts)
        self.resolve = resolve or _resolve
        self.client = client or _guarded_client(self.resolve)

    async def _check_hop(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise MediaRefused(400, "only http and https media can be shown")
        try:
            addresses = await self.resolve(parts.hostname)
        except OSError:
            raise MediaRefused(502, f"{parts.hostname} could not be found") from None
        if not addresses or not all(_public(a) for a in addresses):
            raise MediaRefused(403, "refused: that host is not a public address")

    async def open(self, url: str, clicked: bool, range_header: str | None) -> MediaStream:
        """Fetch `url` (following up to three redirects, each checked) and stream it back. Raises
        MediaRefused with the status the route answers."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise MediaRefused(400, "only http and https media can be shown")
        if not clicked and not host_trusted(url, self.hosts):
            raise MediaRefused(403, "click to load: that host is not on the trusted list")
        await self._check_hop(url)
        # identity: the bytes pass through as sent, so Content-Length and Content-Range stay true
        headers = {"Accept-Encoding": "identity", **({"Range": range_header} if range_header else {})}
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = await self.client.send(self.client.build_request("GET", url, headers=headers), stream=True)
            except _PrivateAddress:
                raise MediaRefused(403, "refused: that host is not a public address") from None
            except httpx.HTTPError as e:
                raise MediaRefused(502, f"the host did not answer ({type(e).__name__})") from None
            if response.is_redirect:
                await response.aclose()
                url = urljoin(url, response.headers.get("location", ""))
                if not clicked and not host_trusted(url, self.hosts):  # a trusted host's redirect is not a click
                    raise MediaRefused(403, "click to load: that host is not on the trusted list")
                await self._check_hop(url)
                continue
            break
        else:
            raise MediaRefused(502, "too many redirects")
        if not response.is_success:
            await response.aclose()
            raise MediaRefused(404 if response.status_code == 404 else 502, f"the host answered {response.status_code}")
        kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not kind.startswith(ALLOWED_TYPES):
            await response.aclose()
            raise MediaRefused(415, f"not an image, video or PDF ({kind or 'no type'})")
        out = {k: response.headers[k] for k in PASSED_HEADERS if k in response.headers}
        out.update({"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                    "Cache-Control": "private, max-age=3600"})
        return MediaStream(response.status_code, out, response.aiter_bytes(), response.aclose)
