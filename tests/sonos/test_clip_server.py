import socket
import time

import httpx
import pytest

from src.sonos import clip_server
from src.sonos.clip_server import ClipServer, local_ip_toward

WAV = bytes(range(256)) * 4


def test_serves_the_clip_to_the_allowed_address():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        r = httpx.get(s.url)
        assert r.status_code == 200
        assert r.content == WAV
        assert r.headers["content-type"] == "audio/wav"
        assert r.headers["content-length"] == str(len(WAV))
        assert r.headers["cache-control"] == "no-store"
        assert s.hits == 1


def test_head_has_no_body():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        r = httpx.head(s.url)
        assert r.status_code == 200
        assert r.headers["content-length"] == str(len(WAV))
        assert r.content == b""
        assert s.hits == 0  # a HEAD is not a fetch of the audio


def test_refuses_other_addresses_before_reading_them():
    # closed in the accept loop, so no handler thread waits on a request line that never comes
    with ClipServer(WAV, allowed="127.0.0.2", bind="127.0.0.1", life_s=30) as s:
        with pytest.raises(httpx.TransportError):
            httpx.get(s.url)
        assert s.hits == 0


def test_a_stalled_connection_is_closed_after_the_timeout(monkeypatch):
    monkeypatch.setattr(clip_server, "REQUEST_TIMEOUT_S", 0.3)
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        host, port = s.url.split("/")[2].split(":")
        with socket.create_connection((host, int(port)), timeout=5) as c:
            c.sendall(b"GET /")  # never finishes the request line
            assert c.recv(1024) == b""  # the server gave up and closed it
        assert httpx.get(s.url).status_code == 200


def test_connections_at_once_are_bounded(monkeypatch):
    monkeypatch.setattr(clip_server, "MAX_CONNECTIONS", 2)
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        host, port = s.url.split("/")[2].split(":")
        held = [socket.create_connection((host, int(port)), timeout=5) for _ in range(2)]
        try:
            time.sleep(0.2)
            with pytest.raises(httpx.TransportError):
                httpx.get(s.url)
        finally:
            for c in held:
                c.close()
        time.sleep(0.2)
        assert httpx.get(s.url).status_code == 200


def test_unknown_path_is_404():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        base = s.url.rsplit("/", 1)[0]
        assert httpx.get(base + "/nope.wav").status_code == 404


def test_range_request_gets_206():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        r = httpx.get(s.url, headers={"Range": "bytes=4-9"})
        assert r.status_code == 206
        assert r.content == WAV[4:10]
        assert r.headers["content-range"] == f"bytes 4-9/{len(WAV)}"
        r = httpx.get(s.url, headers={"Range": "bytes=1000-"})
        assert r.status_code == 206
        assert r.content == WAV[1000:]


@pytest.mark.parametrize("rng", ["bytes=abc", "bytes=0-1,3-4", "bytes=9-4", "items=1-2"])
def test_bad_range_gets_the_whole_file(rng):
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        r = httpx.get(s.url, headers={"Range": rng})
        assert r.status_code == 200
        assert r.content == WAV


def test_post_is_refused():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        assert httpx.post(s.url, content=b"x").status_code == 501


def test_stops_after_its_life():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=0.2) as s:
        time.sleep(0.5)
        with pytest.raises(httpx.TransportError):
            httpx.get(s.url)


def test_local_ip_toward_loopback():
    assert local_ip_toward("127.0.0.1") == "127.0.0.1"


def test_handler_error_writes_nothing_to_stderr(capfd, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(ClipServer, "_count", boom)
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        try:
            httpx.get(s.url)
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    out, err = capfd.readouterr()
    assert err == "" and out == ""


def test_huge_range_number_gets_the_whole_file():
    with ClipServer(WAV, allowed="127.0.0.1", bind="127.0.0.1", life_s=30) as s:
        r = httpx.get(s.url, headers={"Range": "bytes=" + "9" * 5000 + "-"})
        assert r.status_code == 200
        assert r.content == WAV
