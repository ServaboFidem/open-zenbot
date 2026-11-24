"""A tiny listener that serves one WAV clip to one speaker for a short time.

A Sonos group's lead speaker fetches audio from a URL. This listens on one
local address (never 0.0.0.0), answers only the lead speaker's address, at an
unguessable path, and stops on exit or after ``life_s``. Nothing about the
address, URL or token is logged.
"""

from __future__ import annotations

import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_RANGE = re.compile(r"^bytes=(\d{1,15})-(\d{0,15})$")


def local_ip_toward(ip: str) -> str:
    """The address of this computer's interface that reaches ``ip``."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 1400))
        return s.getsockname()[0]
    finally:
        s.close()


class _QuietServer(ThreadingHTTPServer):
    """Never prints: the default handle_error writes the client's address."""

    errors = 0

    def handle_error(self, request, client_address):
        self.errors += 1


class ClipServer:
    def __init__(self, wav: bytes, allowed: str, bind: str, life_s: float):
        self._wav = wav
        self._allowed = allowed
        self._bind = bind
        self._life_s = life_s
        self._token = secrets.token_urlsafe(16)
        self._hits = 0
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._timer: threading.Timer | None = None
        self._port = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def url(self) -> str:
        return f"http://{self._bind}:{self._port}/{self._token}.wav"

    def _count(self) -> None:
        with self._lock:
            self._hits += 1

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):  # silenced
                pass

            def _serve(self, body: bool) -> None:
                if self.client_address[0] != outer._allowed:
                    self.send_error(403)
                    return
                if self.path != f"/{outer._token}.wav":
                    self.send_error(404)
                    return
                wav = outer._wav
                total = len(wav)
                status, data, extra = 200, wav, {}
                m = _RANGE.match(self.headers.get("Range", "").strip())
                if m:
                    a = int(m.group(1))
                    b = int(m.group(2)) if m.group(2) else total - 1
                    b = min(b, total - 1)
                    if a <= b:
                        status, data = 206, wav[a : b + 1]
                        extra["Content-Range"] = f"bytes {a}-{b}/{total}"
                outer._count()
                self.send_response(status)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        return Handler

    def __enter__(self) -> "ClipServer":
        self._httpd = _QuietServer((self._bind, 0), self._handler())
        self._httpd.daemon_threads = True
        self._port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self._timer = threading.Timer(self._life_s, self._stop)
        self._timer.daemon = True
        self._timer.start()
        return self

    def _stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    def __exit__(self, *exc) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._stop()
