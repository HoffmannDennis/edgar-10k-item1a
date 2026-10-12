"""SEC Archives client against a scripted loopback server; no real network."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import math
import random
import socket
import threading
import time
import zlib

import pytest

from edgar_10k_item1a.sec import client as client_module
from edgar_10k_item1a.sec.client import (
    ALLOWED_PATH_PREFIX, DEFAULT_MAX_BYTES, DEFAULT_RATE, MAX_REDIRECTS, SEC_BASE, SEC_HOST,
    USER_AGENT_ENV, FetchResult, HostNotAllowed, ResponseTooLarge, SecAccessDenied, SecClient,
    SecFetchError, SecHTTPError, SecRateLimited, SecRetriesExhausted, SecRunHalted, TokenBucket,
    check_sec_url, resolve_user_agent,
)


CONTACT = "research-bot@example.org"
UA = f"Example Research Bot {CONTACT}"
SEC = "https://www.sec.gov"
FOLDER = "/Archives/edgar/data/1/000000000124000001"
DOC = f"{FOLDER}/doc.htm"


# -- loopback fixture server ---------------------------------------------------

@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    encoding: str | None = None  # gzip | gzip-members | deflate | raw-deflate
    framing: str = "length"  # length | chunked | chunked-cut | close | drop

    def encoded(self) -> tuple[bytes, dict[str, str]]:
        body, headers = self.body, dict(self.headers)
        if self.encoding == "gzip":
            body, headers["Content-Encoding"] = gzip.compress(body, mtime=0), "gzip"
        elif self.encoding == "gzip-members":
            half = len(body) // 2
            body = gzip.compress(body[:half], mtime=0) + gzip.compress(body[half:], mtime=0)
            headers["Content-Encoding"] = "gzip"
        elif self.encoding == "deflate":
            body, headers["Content-Encoding"] = zlib.compress(body), "deflate"
        elif self.encoding == "raw-deflate":
            packer = zlib.compressobj(wbits=-zlib.MAX_WBITS)
            body, headers["Content-Encoding"] = packer.compress(body) + packer.flush(), "deflate"
        headers.setdefault("Content-Type", "text/html; charset=utf-8")
        if self.framing == "length":
            headers.setdefault("Content-Length", str(len(body)))
        elif self.framing in {"chunked", "chunked-cut"}:
            headers["Transfer-Encoding"] = "chunked"
        return body, headers


@dataclass(frozen=True)
class Hit:
    path: str
    t: float
    user_agents: tuple[str, ...]
    accept_encoding: str | None


class LoopbackServer:
    """Routes map a path to scripted replies; each request pops one, the last repeats."""

    def __init__(self) -> None:
        self.routes: dict[str, list[Reply]] = {}
        self.log: list[Hit] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                arrived = time.monotonic()
                with outer._lock:
                    outer.log.append(Hit(self.path, arrived, tuple(self.headers.get_all("User-Agent") or ()),
                                         self.headers.get("Accept-Encoding")))
                    queue = outer.routes.get(self.path)
                    reply = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else Reply(404, b"not found")
                self.close_connection = True
                if reply.framing == "drop":
                    return
                body, headers = reply.encoded()
                self.send_response(reply.status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    if reply.framing == "chunked":
                        for start in range(0, len(body), 1024):
                            piece = body[start:start + 1024]
                            self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                        self.wfile.write(b"0\r\n\r\n")
                    elif reply.framing == "chunked-cut":  # one chunk announced in full, half sent, close
                        self.wfile.write(b"%x\r\n%s" % (len(body), body[:len(body) // 2]))
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, format, *args) -> None:  # noqa: A002, ANN001
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)

    def reset(self) -> None:
        with self._lock:
            self.routes.clear()
            self.log.clear()

    def route(self, path: str, *replies: Reply) -> None:
        with self._lock:
            self.routes[path] = list(replies)

    def hits(self, path: str | None = None) -> list[Hit]:
        with self._lock:
            return [hit for hit in self.log if path is None or hit.path == path]

    def __enter__(self) -> LoopbackServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture(autouse=True)
def _loopback_only_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):  # noqa: ANN001
        host = address[0] if isinstance(address, tuple) else address
        if host != "127.0.0.1":
            raise AssertionError(f"non-loopback network access attempted: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="module")
def _shared_server():
    with LoopbackServer() as srv:
        yield srv


@pytest.fixture()
def server(_shared_server: LoopbackServer) -> LoopbackServer:
    _shared_server.reset()
    return _shared_server


def make_client(server: LoopbackServer, **kwargs) -> SecClient:
    kwargs.setdefault("rate", 1000.0)
    kwargs.setdefault("sleep", SleepRecorder())
    return SecClient(user_agent=UA, test_loopback_base=server.base, **kwargs)


def body_of(n_hex_bytes: int) -> bytes:
    return b"<html>" + random.Random(0).randbytes(n_hex_bytes).hex().encode() + b"</html>"


# -- successful fetches and decoding ------------------------------------------

def test_get_returns_body_hash_and_content_type_and_sends_user_agent(server):
    body = b"<html><body>Item 1A. Risk Factors</body></html>"
    server.route(DOC, Reply(200, body, {"Content-Type": "application/xhtml+xml"}))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert isinstance(result, FetchResult)
    assert (result.url, result.final_url, result.http_status, result.attempts) == (SEC + DOC, SEC + DOC, 200, 1)
    assert result.content == body and result.n_bytes == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert result.content_type == "application/xhtml+xml"
    [hit] = server.hits(DOC)
    assert hit.user_agents == (UA,) and hit.accept_encoding == "gzip, deflate"
    assert client.n_http_requests == 1 and client.halted is None and client._sleep.calls == []


@pytest.mark.parametrize("encoding", ["gzip", "gzip-members", "deflate", "raw-deflate"])
def test_gzip_and_deflate_bodies_are_decoded(server, encoding):
    body = body_of(60_000)  # decoded body spans several read chunks
    server.route(DOC, Reply(200, body, encoding=encoding))
    result = make_client(server).get(SEC + DOC)
    assert result.content == body and result.n_bytes == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()


def test_unsupported_content_encoding_is_an_http_error(server):
    server.route(DOC, Reply(200, b"<html/>", {"Content-Encoding": "br"}))
    client = make_client(server)
    with pytest.raises(SecHTTPError, match="Content-Encoding"):
        client.get(SEC + DOC)
    assert client._sleep.calls == []


def test_truncated_gzip_is_retried_then_exhausted(server):
    broken = gzip.compress(b"<html>" + b"a" * 5000 + b"</html>")[:-12]
    server.route(DOC, Reply(200, broken, {"Content-Encoding": "gzip"}))
    client = make_client(server, max_attempts=2)
    with pytest.raises(SecRetriesExhausted, match="truncated gzip"):
        client.get(SEC + DOC)
    assert len(server.hits(DOC)) == 2 and client.halted is None


# -- retry policy --------------------------------------------------------------

def test_404_is_an_http_error_without_retry(server):
    client = make_client(server)
    with pytest.raises(SecHTTPError) as info:
        client.get(SEC + f"{FOLDER}/missing.htm")
    assert info.value.http_status == 404 and info.value.attempts == 1
    assert client._sleep.calls == [] and client.n_http_requests == 1 and client.halted is None


def test_500_then_200_retries_once_with_backoff(server):
    server.route(DOC, Reply(500), Reply(200, b"<html>ok</html>"))
    client = make_client(server, backoff_base=2.0)
    result = client.get(SEC + DOC)
    assert result.content == b"<html>ok</html>" and result.attempts == 2
    assert len(client._sleep.calls) == 1 and 2.0 <= client._sleep.calls[0] <= 2.5
    assert len(server.hits(DOC)) == 2 == client.n_http_requests


def test_backoff_is_exponential_with_seeded_jitter(server):
    def run() -> list[float]:
        server.route(DOC, Reply(503), Reply(500), Reply(502), Reply(200, b"<html>x</html>"))
        client = make_client(server, seed=7, backoff_base=2.0)
        assert client.get(SEC + DOC).attempts == 4
        return client._sleep.calls

    first, second = run(), run()
    assert first == second and len(first) == 3
    for attempt, delay in enumerate(first, start=1):
        base = 2.0 * 2 ** (attempt - 1)
        assert base <= delay <= base * 1.25


def test_dropped_connection_is_retried(server):
    server.route(DOC, Reply(framing="drop"), Reply(200, b"<html>back</html>"))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == b"<html>back</html>" and result.attempts == 2
    assert len(client._sleep.calls) == 1


def test_persistent_5xx_exhausts_retries_without_halting(server):
    server.route(DOC, Reply(500))
    client = make_client(server, max_attempts=3)
    with pytest.raises(SecRetriesExhausted) as info:
        client.get(SEC + DOC)
    assert (info.value.http_status, info.value.attempts) == (500, 3)
    assert len(server.hits(DOC)) == 3 and len(client._sleep.calls) == 2 and client.halted is None


def test_429_honours_retry_after_seconds(server):
    server.route(DOC, Reply(429, b"slow down", {"Retry-After": "3"}), Reply(200, b"<html>fine</html>"))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == b"<html>fine</html>" and result.attempts == 2
    assert client._sleep.calls[0] >= 3.0 and client.halted is None


def test_429_honours_retry_after_http_date(server):
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    server.route(DOC, Reply(429, b"", {"Retry-After": when}), Reply(200, b"<html>fine</html>"))
    client = make_client(server)
    assert client.get(SEC + DOC).attempts == 2
    assert 25.0 <= client._sleep.calls[0] <= 31.0


def test_persistent_429_halts_the_client_for_good(server):
    server.route(DOC, Reply(429, b"", {"Retry-After": "1"}))
    server.route(f"{FOLDER}/next.htm", Reply(200, b"<html>next</html>"))
    client = make_client(server, max_attempts=2)
    with pytest.raises(SecRateLimited) as info:
        client.get(SEC + DOC)
    assert (info.value.http_status, info.value.attempts) == (429, 2)
    assert client.halted and client._sleep.calls[0] >= 1.0
    with pytest.raises(SecRunHalted):
        client.get(SEC + f"{FOLDER}/next.htm")
    assert len(server.hits()) == 2 == client.n_http_requests


def test_retry_after_above_cap_halts_on_429_and_exhausts_on_5xx(server):
    server.route(DOC, Reply(429, b"", {"Retry-After": "601"}))
    client = make_client(server)
    with pytest.raises(SecRateLimited):
        client.get(SEC + DOC)
    assert client.halted and client._sleep.calls == [] and client.n_http_requests == 1

    other = f"{FOLDER}/other.htm"
    server.route(other, Reply(503, b"", {"Retry-After": "601"}))
    client = make_client(server)
    with pytest.raises(SecRetriesExhausted):
        client.get(SEC + other)
    assert client.halted is None and client._sleep.calls == [] and client.n_http_requests == 1


# -- access denial ------------------------------------------------------------

def test_403_is_access_denied_and_halts_without_retry(server):
    server.route(DOC, Reply(403, b"forbidden"))
    client = make_client(server)
    with pytest.raises(SecAccessDenied) as info:
        client.get(SEC + DOC)
    assert (info.value.http_status, info.value.attempts) == (403, 1)
    assert client.halted and client._sleep.calls == []
    with pytest.raises(SecRunHalted):
        client.get(SEC + f"{FOLDER}/next.htm")
    assert len(server.hits()) == 1


@pytest.mark.parametrize("marker", [b"Request Rate Threshold Exceeded", b"Undeclared Automated Tool"])
def test_block_page_with_status_200_is_access_denied(server, marker):
    server.route(DOC, Reply(200, b"<html><body><h1>Your " + marker + b"</h1></body></html>"))
    client = make_client(server)
    with pytest.raises(SecAccessDenied):
        client.get(SEC + DOC)
    assert client.halted
    with pytest.raises(SecRunHalted):
        client.get(SEC + DOC)
    assert len(server.hits()) == 1


# -- redirects -----------------------------------------------------------------

def test_redirects_within_allowlist_are_followed_and_each_hop_counted(server):
    first, second = f"{FOLDER}/moved.htm", f"{FOLDER}/final.htm"
    server.route(DOC, Reply(301, b"", {"Location": first}))
    server.route(first, Reply(302, b"", {"Location": server.base + second}))
    server.route(second, Reply(200, b"<html>final</html>"))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == b"<html>final</html>"
    assert (result.url, result.final_url, result.attempts) == (SEC + DOC, SEC + second, 1)
    assert client.n_http_requests == 3
    assert [hit.path for hit in server.hits()] == [DOC, first, second]


@pytest.mark.parametrize("location", [
    "http://evil.example/Archives/edgar/data/1/x.htm",
    "http://127.0.0.1:{other_port}/Archives/edgar/data/1/x.htm",
    "https://www.sec.gov/Archives/edgar/data/1/x.htm",  # test mode never escapes to the real host
    "/cgi-bin/browse-edgar",
    "/files/dera/data/financial-statement-data-sets/2024q1.zip",
    "/Archives/edgar/data/1/x.htm?action=getcompany",
])
def test_redirect_outside_allowlist_is_rejected(server, location):
    other_port = server.port + 1 if server.port < 65535 else server.port - 1
    server.route(DOC, Reply(302, b"", {"Location": location.format(other_port=other_port)}))
    client = make_client(server)
    with pytest.raises(HostNotAllowed):
        client.get(SEC + DOC)
    assert len(server.hits()) == 1 == client.n_http_requests


def test_more_than_max_redirects_is_an_http_error(server):
    server.route(DOC, Reply(301, b"", {"Location": DOC}))
    client = make_client(server)
    with pytest.raises(SecHTTPError, match="redirects"):
        client.get(SEC + DOC)
    assert client.n_http_requests == MAX_REDIRECTS + 1 == len(server.hits(DOC))


def test_rate_limiter_spaces_retries_and_redirect_hops_in_real_time(server):
    rate, target = 20.0, f"{FOLDER}/moved.htm"
    server.route(DOC, Reply(503), Reply(301, b"", {"Location": target}))
    server.route(target, Reply(200, b"<html>ok</html>"))
    client = make_client(server, rate=rate)
    client.get(SEC + DOC)
    arrivals = [hit.t for hit in server.hits()]
    assert len(arrivals) == 3 == client.n_http_requests
    assert arrivals[-1] - arrivals[0] >= 2 / rate * 0.9
    assert min(b - a for a, b in zip(arrivals, arrivals[1:])) >= 0.5 / rate


# -- byte guards ---------------------------------------------------------------

def test_declared_content_length_above_max_bytes(server):
    server.route(DOC, Reply(200, b"x" * 5000))
    client = make_client(server)
    with pytest.raises(ResponseTooLarge, match="Content-Length"):
        client.get(SEC + DOC, max_bytes=1000)
    assert client._sleep.calls == [] and client.halted is None


@pytest.mark.parametrize("framing", ["chunked", "close"])
def test_undeclared_body_above_max_bytes(server, framing):
    server.route(DOC, Reply(200, b"x" * 5000, framing=framing))
    with pytest.raises(ResponseTooLarge, match="raw body"):
        make_client(server).get(SEC + DOC, max_bytes=1000)


def test_undeclared_body_within_max_bytes_is_read(server):
    body = body_of(3000)
    server.route(DOC, Reply(200, body, framing="chunked"))
    assert make_client(server).get(SEC + DOC).content == body


def test_decoded_body_above_max_bytes(server):
    server.route(DOC, Reply(200, b"\0" * 2_000_000, encoding="gzip"))
    with pytest.raises(ResponseTooLarge, match="decoded body"):
        make_client(server).get(SEC + DOC, max_bytes=100_000)


def test_client_max_bytes_default_and_override_validation(server):
    server.route(DOC, Reply(200, b"x" * 5000))
    with pytest.raises(ResponseTooLarge):
        make_client(server, max_bytes=1000).get(SEC + DOC)
    with pytest.raises(ValueError):
        make_client(server).get(SEC + DOC, max_bytes=0)
    assert make_client(server).max_bytes == DEFAULT_MAX_BYTES == 100 * 1024 * 1024


# -- Content-Length framing integrity ------------------------------------------

def test_body_shorter_than_content_length_is_retried_then_exhausted(server):
    server.route(DOC, Reply(200, b"<html>partial", {"Content-Length": "1000"}))  # 13 bytes, then close
    client = make_client(server, max_attempts=3)
    with pytest.raises(SecRetriesExhausted, match=r"truncated body: 13 of 1000") as info:
        client.get(SEC + DOC)
    assert info.value.attempts == 3 == client.max_attempts == client.n_http_requests == len(server.hits(DOC))
    assert len(client._sleep.calls) == 2 and client.halted is None


def test_short_body_then_full_body_on_retry_succeeds(server):
    body = body_of(3000)
    server.route(DOC, Reply(200, body[:13], {"Content-Length": str(len(body))}), Reply(200, body))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == body and result.n_bytes == len(body) and result.attempts == 2
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert len(client._sleep.calls) == 1 and client.n_http_requests == 2


def test_gzip_content_length_counts_encoded_bytes(server):
    body = body_of(60_000)
    packed = gzip.compress(body, mtime=0)
    server.route(DOC, Reply(200, body, {"Content-Length": str(len(packed))}, encoding="gzip"))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == body and result.n_bytes == len(body) != len(packed)
    assert result.sha256 == hashlib.sha256(body).hexdigest() and client._sleep.calls == []


@pytest.mark.parametrize("framing", ["close", "chunked"])
def test_body_without_content_length_is_read(server, framing):
    body = body_of(3000)
    server.route(DOC, Reply(200, body, framing=framing))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == body and result.sha256 == hashlib.sha256(body).hexdigest()
    assert result.attempts == 1 and client._sleep.calls == []


def test_incomplete_chunked_read_is_retried_as_truncation(server):
    body = body_of(3000)
    server.route(DOC, Reply(200, body, framing="chunked-cut"), Reply(200, body, framing="chunked"))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert result.content == body and result.attempts == 2 and len(client._sleep.calls) == 1

    server.route(DOC, Reply(200, body, framing="chunked-cut"))
    client = make_client(server, max_attempts=2)
    with pytest.raises(SecRetriesExhausted, match=r"truncated body \(IncompleteRead\)"):
        client.get(SEC + DOC)
    assert client.n_http_requests == 2 and len(client._sleep.calls) == 1 and client.halted is None


def test_body_longer_than_content_length_is_malformed_and_not_retried(server):
    # Only conflicting framing (Transfer-Encoding plus Content-Length) can deliver excess bytes.
    server.route(DOC, Reply(200, body_of(3000), {"Content-Length": "20"}, framing="chunked"))
    client = make_client(server)
    with pytest.raises(SecHTTPError, match="exceeds declared Content-Length 20"):
        client.get(SEC + DOC)
    assert client.n_http_requests == 1 and client._sleep.calls == [] and client.halted is None


@pytest.mark.parametrize("value", ["abc", "-5", "+13", "13, 13", "1_3", "²"])
def test_malformed_content_length_is_an_http_error(server, value):
    server.route(DOC, Reply(200, b"<html>partial", {"Content-Length": value}, framing="close"))
    client = make_client(server)
    with pytest.raises(SecHTTPError, match="malformed Content-Length"):
        client.get(SEC + DOC)
    assert client.n_http_requests == 1 and client._sleep.calls == [] and client.halted is None


def test_truncated_block_page_halts_instead_of_retrying(server):
    server.route(DOC, Reply(200, b"<html>Request Rate Threshold Exceeded", {"Content-Length": "1000"}))
    client = make_client(server)
    with pytest.raises(SecAccessDenied):
        client.get(SEC + DOC)
    assert client.halted and client._sleep.calls == [] and len(server.hits()) == 1


# -- allowlist and identity validation ----------------------------------------

@pytest.mark.parametrize("url", [
    "",
    "http://www.sec.gov/Archives/edgar/data/1/a.htm",
    "https://sec.gov/Archives/edgar/data/1/a.htm",
    "https://www.sec.gov.evil.example/Archives/edgar/data/1/a.htm",
    "https://evil.example/Archives/edgar/data/1/a.htm",
    "https://user@www.sec.gov/Archives/edgar/data/1/a.htm",
    "https://user:pw@www.sec.gov/Archives/edgar/data/1/a.htm",
    "https://@www.sec.gov/Archives/edgar/data/1/a.htm",
    "https://www.sec.gov:8443/Archives/edgar/data/1/a.htm",
    "https://www.sec.gov:99999/Archives/edgar/data/1/a.htm",
    "https://www.sec.gov/Archives/edgar/data/1/a.htm?x=1",
    "https://www.sec.gov/Archives/edgar/data/1/a.htm?",
    "https://www.sec.gov/Archives/edgar/data/1/a.htm#part",
    "https://www.sec.gov/Archives/edgar/../../etc/passwd",
    "https://www.sec.gov/Archives/edgar/data/%2e%2e/%2E%2E/x",
    "https://www.sec.gov/Archives/edgar/data/..%2F..%2Fx",
    "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip",
    "https://www.sec.gov/cgi-bin/browse-edgar",
    "https://www.sec.gov/Archives/edgar",
    "https://www.sec.gov/archives/edgar/data/1/a.htm",
    "https://www.sec.gov/Archives/edgar/data/1/a b.htm",
    "https://www.sec.gov/Archives/edgar/data/1/a.htm\r\nX-Injected: 1",
    "https://www.sec.gov/Archives/edgar/data/1/ä.htm",
    "http://127.0.0.1:8000/Archives/edgar/data/1/a.htm",
])
def test_check_sec_url_rejects_everything_outside_archives(url):
    with pytest.raises(HostNotAllowed):
        check_sec_url(url)
    with pytest.raises(HostNotAllowed):  # rejected before any request (socket guard is active)
        SecClient(user_agent=UA, sleep=SleepRecorder()).get(url)


@pytest.mark.parametrize("url", [
    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
    "https://www.sec.gov:443/Archives/edgar/data/1/000000000124000001/0000000001-24-000001-index.htm",
])
def test_check_sec_url_accepts_archives_urls(url):
    assert check_sec_url(url) == url


@pytest.mark.parametrize("value", [
    "", "   ", "no contact address", f"Bot {CONTACT}\r\nX-Injected: 1", f"Bot\n{CONTACT}",
    f"Bot\x00 {CONTACT}", f"Bøt {CONTACT}",
])
def test_resolve_user_agent_rejects_invalid_values_without_echoing_them(value):
    with pytest.raises(ValueError) as info:
        resolve_user_agent(value)
    assert value.strip() == "" or value not in str(info.value)
    with pytest.raises(ValueError):
        SecClient(user_agent=value)


def test_resolve_user_agent_reads_environment(monkeypatch):
    monkeypatch.delenv(USER_AGENT_ENV, raising=False)
    with pytest.raises(ValueError):
        resolve_user_agent()
    with pytest.raises(ValueError):
        SecClient()
    monkeypatch.setenv(USER_AGENT_ENV, "no contact address")
    with pytest.raises(ValueError):
        SecClient()
    monkeypatch.setenv(USER_AGENT_ENV, f"  {UA}  ")
    assert resolve_user_agent() == UA
    assert resolve_user_agent("Other Bot other@example.org") == "Other Bot other@example.org"
    assert SecClient().base == SEC_BASE


def test_user_agent_is_sent_but_never_exposed(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.route(DOC, Reply(200, b"<html>ok</html>"))
    server.route(f"{FOLDER}/big.htm", Reply(200, b"x" * 5000))
    server.route(f"{FOLDER}/loop.htm", Reply(301, b"", {"Location": f"{FOLDER}/loop.htm"}))
    server.route(f"{FOLDER}/denied.htm", Reply(403, b"forbidden"))
    server.route(f"{FOLDER}/short.htm", Reply(200, b"<html>partial", {"Content-Length": "1000"}))
    client = make_client(server)
    result = client.get(SEC + DOC)
    assert server.hits(DOC)[0].user_agents == (UA,)

    errors: list[BaseException] = []
    for call in (
        lambda: client.get("https://www.sec.gov/cgi-bin/browse-edgar"),
        lambda: client.get(SEC + f"{FOLDER}/missing.htm"),
        lambda: client.get(SEC + f"{FOLDER}/big.htm", max_bytes=10),
        lambda: client.get(SEC + f"{FOLDER}/loop.htm"),
        lambda: client.get(SEC + f"{FOLDER}/short.htm"),
        lambda: client.get(SEC + f"{FOLDER}/denied.htm"),
        lambda: client.get(SEC + DOC),
    ):
        try:
            call()
        except (SecFetchError, HostNotAllowed) as exc:
            errors.append(exc)
        else:
            pytest.fail("expected a client error")
    assert [type(e) for e in errors] == [
        HostNotAllowed, SecHTTPError, ResponseTooLarge, SecHTTPError, SecRetriesExhausted, SecAccessDenied,
        SecRunHalted]

    texts = [repr(client), str(client), repr(vars(client)), repr(result), repr(asdict(result)), caplog.text]
    for error in errors:
        chained: BaseException | None = error
        while chained is not None:
            texts += [str(chained), repr(chained), repr(vars(chained))]
            chained = chained.__cause__ or chained.__context__
    for text in texts:
        assert UA not in text and CONTACT not in text
    assert not hasattr(client, "user_agent_sha256") and not hasattr(client_module, "user_agent_sha256")
    assert [f.name for f in fields(FetchResult)] == [
        "url", "final_url", "http_status", "n_bytes", "sha256", "attempts", "content_type", "content"]


# -- construction and limiter --------------------------------------------------

def test_public_constants_and_exception_hierarchy():
    assert (SEC_BASE, SEC_HOST, ALLOWED_PATH_PREFIX, USER_AGENT_ENV) == (
        "https://www.sec.gov", "www.sec.gov", "/Archives/edgar/", "SEC_USER_AGENT")
    assert (DEFAULT_RATE, MAX_REDIRECTS) == (5.0, 3)
    assert issubclass(SecFetchError, RuntimeError) and issubclass(HostNotAllowed, ValueError)
    for error in (SecHTTPError, SecRetriesExhausted, ResponseTooLarge, SecRunHalted):
        assert issubclass(error, SecFetchError)
    assert issubclass(SecAccessDenied, SecRunHalted) and issubclass(SecRateLimited, SecRunHalted)
    error = SecHTTPError("HTTP 404", url=SEC + DOC, http_status=404, attempts=1)
    assert (error.url, error.http_status, error.attempts) == (SEC + DOC, 404, 1)


def test_non_loopback_rate_above_five_is_rejected(server):
    client = SecClient(user_agent=UA)
    assert (client.base, client.is_test_loopback, client.rate) == (SEC_BASE, False, DEFAULT_RATE)
    assert SecClient(user_agent=UA, rate=5.0).rate == 5.0
    with pytest.raises(ValueError):
        SecClient(user_agent=UA, rate=6)
    assert make_client(server, rate=6).rate == 6.0
    for bad in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            make_client(server, rate=bad)
    with pytest.raises(ValueError):
        make_client(server, max_attempts=0)


@pytest.mark.parametrize("base", [
    "http://10.0.0.1:8080", "https://127.0.0.1:8443", "http://127.0.0.1", "http://localhost:8080",
    "http://127.0.0.1:8080/Archives", "http://user@127.0.0.1:8080", "http://127.0.0.1:8080?x=1",
    "http://www.sec.gov:80",
])
def test_test_loopback_base_must_be_explicit_ipv4_loopback(base):
    with pytest.raises(ValueError):
        SecClient(user_agent=UA, test_loopback_base=base)


def test_loopback_base_is_normalized(server):
    client = SecClient(user_agent=UA, test_loopback_base=server.base + "/")
    assert client.base == server.base and client.is_test_loopback


def test_token_bucket_spaces_acquisitions_with_injected_clock():
    class FakeClock:
        def __init__(self) -> None:
            self.now, self.sleeps = 0.0, []

        def __call__(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    clock = FakeClock()
    bucket = TokenBucket(4.0, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        bucket.acquire()
    assert clock.sleeps == [0.25, 0.25]
    clock.now += 10.0
    bucket.acquire()  # capacity 1: idle time never builds a burst
    bucket.acquire()
    assert clock.sleeps == [0.25, 0.25, 0.25]
    for kwargs in ({"rate": 0}, {"rate": math.nan}, {"rate": 1.0, "capacity": 0.5}):
        with pytest.raises(ValueError):
            TokenBucket(**kwargs)
