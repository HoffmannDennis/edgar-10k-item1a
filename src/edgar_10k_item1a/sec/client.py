"""Rate-limited, fail-closed HTTPS client for SEC EDGAR Archives documents.

Only ``https://www.sec.gov/Archives/edgar/`` URLs are requested. One serial token
bucket (default 5 requests/s, burst 1) spaces every request, including retries
and redirect hops; redirects are followed manually and re-validated. 429/5xx and
transient network failures back off exponentially with seeded jitter and honour
``Retry-After``. HTTP 403, an SEC block page or a persistent 429 halts the client.
Bodies are decoded (gzip/deflate) under byte guards and returned in memory;
persistence is the caller's job. Nothing is requested on import.

A declared ``Content-Length`` must be one ASCII-digit value and must equal the raw
(still content-encoded) bytes received: fewer bytes is a truncated transfer and is
retried; more bytes (only possible with conflicting framing) or a malformed value
is a non-retryable :class:`SecHTTPError`. A block page in a truncated body halts.

The User-Agent is sent only as a request header; it is never part of a repr,
exception message or :class:`FetchResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import math
import os
import random
import threading
import time
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zlib


SEC_BASE = "https://www.sec.gov"
SEC_HOST = "www.sec.gov"
ALLOWED_PATH_PREFIX = "/Archives/edgar/"
USER_AGENT_ENV = "SEC_USER_AGENT"
DEFAULT_RATE = 5.0  # also the maximum outside test loopback mode
MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 100 * 1024 * 1024

_LOOPBACK_HOST = "127.0.0.1"
_JITTER_FRACTION = 0.25
_CHUNK_SIZE = 1 << 16
_BLOCK_PAGE_MARKERS = (b"request rate threshold exceeded", b"undeclared automated tool")
_BLOCK_SNIFF_BYTES = 8192
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_GZIP = frozenset({"gzip", "x-gzip"})


class SecFetchError(RuntimeError):
    """One request failed; other URLs may still be fetched unless the client halted."""

    def __init__(self, message: str, *, url: str, http_status: int | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.url = url
        self.http_status = http_status
        self.attempts = attempts


class SecHTTPError(SecFetchError):
    """Non-retryable HTTP status (e.g. 404), redirect overflow or malformed response."""


class SecRetriesExhausted(SecFetchError):
    """5xx, transient network, truncated-body or body-decoding failures persisted."""


class ResponseTooLarge(SecFetchError):
    """Declared, raw or decoded size exceeded the byte guard."""


class SecRunHalted(SecFetchError):
    """The client is halted; no further requests are made."""


class SecAccessDenied(SecRunHalted):
    """HTTP 403 or an SEC block page; never retried."""


class SecRateLimited(SecRunHalted):
    """HTTP 429 persisted, or its Retry-After exceeded the cap."""


class HostNotAllowed(ValueError):
    """URL outside ``https://www.sec.gov/Archives/edgar/`` (or the explicit test loopback)."""


def resolve_user_agent(value: str | None = None) -> str:
    """Return a validated User-Agent from ``value`` or ``$SEC_USER_AGENT``; errors never echo it."""

    agent = (os.environ.get(USER_AGENT_ENV, "") if value is None else value).strip()
    if not agent or "@" not in agent or not (agent.isascii() and agent.isprintable()):
        raise ValueError(f"{USER_AGENT_ENV} must be a printable ASCII User-Agent with a contact e-mail ('@')")
    return agent


def check_sec_url(url: str) -> str:
    """Return ``url`` if it is an https www.sec.gov URL under /Archives/edgar/, else raise."""

    if not isinstance(url, str) or not url.isascii() or any(c <= " " or c == "\x7f" for c in url) \
            or "?" in url or "#" in url:
        raise HostNotAllowed(f"URL must be printable ASCII without whitespace, query or fragment: {url!r}")
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if parsed.scheme != "https" or parsed.hostname != SEC_HOST or port not in {None, 443} or "@" in parsed.netloc:
        raise HostNotAllowed(f"URL outside https://{SEC_HOST}: {url}")
    decoded = unquote(parsed.path)
    if not (parsed.path.startswith(ALLOWED_PATH_PREFIX) and decoded.startswith(ALLOWED_PATH_PREFIX)) \
            or "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        raise HostNotAllowed(f"URL path outside {ALLOWED_PATH_PREFIX}: {url}")
    return url


def _loopback_base(base: str) -> tuple[str, int]:
    parsed = urlsplit(base.rstrip("/"))
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.scheme != "http" or not port or parsed.netloc != f"{_LOOPBACK_HOST}:{port}" \
            or parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"test_loopback_base must be exactly http://{_LOOPBACK_HOST}:<port>")
    return f"http://{_LOOPBACK_HOST}:{port}", port


class TokenBucket:
    """Thread-safe token bucket; ``capacity=1`` makes it a strict interval limiter."""

    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        *,
        capacity: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (math.isfinite(rate) and rate > 0):
            raise ValueError("rate must be a positive finite number")
        if not capacity >= 1:
            raise ValueError("capacity must be at least 1")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._last: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                if self._last is not None:
                    self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                self._sleep((1.0 - self._tokens) / self.rate)


@dataclass(frozen=True)
class FetchResult:
    """One decoded HTTP 200 body; ``n_bytes`` and ``sha256`` describe ``content``."""

    url: str
    final_url: str
    http_status: int
    n_bytes: int
    sha256: str
    attempts: int
    content_type: str | None
    content: bytes = field(repr=False)


class _Redacted:
    """Holds the User-Agent so reprs and attribute dumps never show it."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


class _TransientRead(Exception):
    """Retryable failure while reading or decoding a response body."""


class _Decoder:
    """Incremental gzip/deflate decoder; each output piece is at most ``_CHUNK_SIZE`` bytes."""

    def __init__(self, encoding: str) -> None:
        if encoding not in {"", "identity", "deflate"} | _GZIP:
            raise ValueError(encoding)
        self.encoding = encoding
        self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding in _GZIP else None
        self._pending_deflate = encoding == "deflate"

    def feed(self, data: bytes) -> Iterator[bytes]:
        if self._pending_deflate:  # zlib-wrapped or raw deflate, decided by the first bytes
            wrapped = len(data) >= 2 and (data[0] & 0x0F) == 8 and ((data[0] << 8) | data[1]) % 31 == 0
            self._obj = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
            self._pending_deflate = False
        if self._obj is None:
            yield data
            return
        pending = data
        try:
            while pending:
                if self._obj.eof:
                    if self.encoding not in _GZIP:
                        raise _TransientRead("data after the end of the deflate stream")
                    self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)  # next gzip member
                out = self._obj.decompress(pending, _CHUNK_SIZE)
                if out:
                    yield out
                pending = self._obj.unused_data if self._obj.eof else self._obj.unconsumed_tail
        except zlib.error as exc:
            raise _TransientRead(f"corrupt {self.encoding} stream") from exc

    def finish(self) -> Iterator[bytes]:
        if self._pending_deflate:
            raise _TransientRead("empty deflate stream")
        if self._obj is None:
            return
        try:
            while not self._obj.eof:
                out = self._obj.decompress(b"", _CHUNK_SIZE)
                if not out:
                    break
                yield out
        except zlib.error as exc:
            raise _TransientRead(f"corrupt {self.encoding} stream") from exc
        if not self._obj.eof:
            raise _TransientRead(f"truncated {self.encoding} stream")


class _NoRedirect(HTTPRedirectHandler):
    """Surface 3xx as HTTPError so each hop is validated and rate-limited."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _parse_retry_after(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _declared_length(headers: http.client.HTTPMessage) -> int | None:
    """Return the declared Content-Length, ``None`` if absent; ``ValueError`` unless one 1*DIGIT value."""

    values = headers.get_all("Content-Length") or []
    if not values:
        return None
    text = str(values[0]).strip()
    if len(values) != 1 or not (text.isascii() and text.isdigit()):
        raise ValueError("malformed Content-Length")
    return int(text)


def _drain(exc: HTTPError) -> None:
    try:
        exc.read(_CHUNK_SIZE)
    except Exception:  # noqa: BLE001 - best effort on an error body
        pass
    finally:
        try:
            exc.close()
        except Exception:  # noqa: BLE001
            pass


class SecClient:
    """Serial SEC Archives client; :meth:`get` is its only network entry point.

    ``sleep`` drives retry backoff only; the rate limiter always uses real time.
    ``test_loopback_base`` (``http://127.0.0.1:<port>``) maps logical SEC URLs onto a
    local test server and is the only way to exceed the default rate.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        rate: float = DEFAULT_RATE,
        max_attempts: int = 5,
        backoff_base: float = 2.0,
        backoff_cap: float = 120.0,
        max_retry_after: float = 600.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = 60.0,
        seed: int = 0,
        sleep: Callable[[float], None] = time.sleep,
        test_loopback_base: str | None = None,
    ) -> None:
        self._user_agent = _Redacted(resolve_user_agent(user_agent))
        self.is_test_loopback = test_loopback_base is not None
        self.base, self._port = (_loopback_base(test_loopback_base) if test_loopback_base is not None
                                 else (SEC_BASE, None))
        if not (math.isfinite(rate) and rate > 0):
            raise ValueError("rate must be a positive finite number")
        if rate > DEFAULT_RATE and not self.is_test_loopback:
            raise ValueError(f"rate must not exceed {DEFAULT_RATE} requests/s for SEC hosts")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_bytes <= 0 or timeout <= 0:
            raise ValueError("max_bytes and timeout must be positive")
        self.rate = float(rate)
        self.max_attempts = int(max_attempts)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.max_retry_after = float(max_retry_after)
        self.max_bytes = int(max_bytes)
        self.timeout = float(timeout)
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self._sleep = sleep
        self._limiter = TokenBucket(self.rate)
        handlers: list[object] = [_NoRedirect()]
        if self.is_test_loopback:
            handlers.append(ProxyHandler({}))
        self._opener = build_opener(*handlers)
        self.n_http_requests = 0
        self.halted: str | None = None

    def __repr__(self) -> str:
        return (f"SecClient(base={self.base!r}, rate={self.rate}, "
                f"n_http_requests={self.n_http_requests}, halted={self.halted is not None})")

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        """Fetch one allowlisted document into memory, decoded."""

        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        original = check_sec_url(url)
        if self.halted is not None:
            raise SecRunHalted(f"client halted earlier: {self.halted}", url=original)
        return self._fetch(original, self.max_bytes if max_bytes is None else int(max_bytes))

    # -- internals -------------------------------------------------------
    def _transport_url(self, logical: str) -> str:
        return self.base + urlsplit(logical).path if self.is_test_loopback else logical

    def _logical_from_transport(self, url: str) -> str:
        if not self.is_test_loopback:
            return check_sec_url(url)
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.netloc != f"{_LOOPBACK_HOST}:{self._port}":
            raise HostNotAllowed(f"redirect outside the test loopback base: {url}")
        return check_sec_url(SEC_BASE + url[len(self.base):])

    def _halt(self, reason: str, exc_type: type[SecRunHalted], url: str, status: int | None,
              attempts: int) -> SecRunHalted:
        self.halted = reason
        return exc_type(reason, url=url, http_status=status, attempts=attempts)

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        exponential = min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))
        delay = exponential * (1.0 + _JITTER_FRACTION * self._rng.random())
        return delay if retry_after is None else max(delay, retry_after)

    def _request(self, url: str) -> Request:
        return Request(url, headers={"User-Agent": self._user_agent.value, "Accept-Encoding": "gzip, deflate"})

    def _fetch(self, original: str, max_bytes: int) -> FetchResult:
        current = self._transport_url(original)
        attempts = redirects = 0
        while True:
            attempts += 1
            self._limiter.acquire()
            self.n_http_requests += 1
            status: int | None = None
            retry_after: float | None = None
            try:
                response = self._opener.open(self._request(current), timeout=self.timeout)
            except HTTPError as exc:
                status, headers = exc.code, exc.headers
                location = headers.get("Location") if headers else None
                retry_after = _parse_retry_after(headers.get("Retry-After") if headers else None)
                _drain(exc)
                if status == 403:
                    raise self._halt(f"HTTP 403 for {original}", SecAccessDenied, original, 403, attempts)
                if status in _REDIRECT_CODES and location:
                    redirects += 1
                    if redirects > MAX_REDIRECTS:
                        raise SecHTTPError(f"more than {MAX_REDIRECTS} redirects", url=original,
                                           http_status=status, attempts=attempts)
                    current = self._transport_url(self._logical_from_transport(urljoin(current, location)))
                    attempts -= 1  # a redirect hop is a rate-limited request, not a retry attempt
                    continue
                if status != 429 and not 500 <= status <= 599:
                    raise SecHTTPError(f"HTTP {status}", url=original, http_status=status, attempts=attempts)
                reason = f"HTTP {status}"
            except (URLError, OSError, http.client.HTTPException) as exc:
                reason = f"network error: {type(exc).__name__}"
            else:
                try:
                    with response:
                        return self._consume(original, response, attempts, max_bytes)
                except _TransientRead as exc:
                    reason = f"body read error: {exc}"
            if attempts >= self.max_attempts:
                if status == 429:
                    raise self._halt(f"HTTP 429 persisted after {attempts} attempts", SecRateLimited,
                                     original, 429, attempts)
                raise SecRetriesExhausted(f"{reason} after {attempts} attempts", url=original,
                                          http_status=status, attempts=attempts)
            if retry_after is not None and retry_after > self.max_retry_after:
                message = f"Retry-After {retry_after:.0f}s exceeds {self.max_retry_after:.0f}s"
                if status == 429:
                    raise self._halt(message, SecRateLimited, original, 429, attempts)
                raise SecRetriesExhausted(message, url=original, http_status=status, attempts=attempts)
            self._sleep(self._backoff_delay(attempts, retry_after))

    def _consume(self, original: str, response: http.client.HTTPResponse, attempts: int,
                 max_bytes: int) -> FetchResult:
        status = response.status
        final_url = self._logical_from_transport(response.geturl())

        def failure(exc_type: type[SecFetchError], message: str) -> SecFetchError:
            return exc_type(message, url=original, http_status=status, attempts=attempts)

        if status != 200:
            raise failure(SecHTTPError, f"unexpected HTTP {status}")
        headers = response.headers
        try:
            declared = _declared_length(headers)
        except ValueError:
            raise failure(SecHTTPError, "malformed Content-Length header") from None
        if declared is not None and declared > max_bytes:
            raise failure(ResponseTooLarge, f"Content-Length {declared} exceeds {max_bytes} bytes")
        encoding = (headers.get("Content-Encoding") or "identity").strip().lower()
        try:
            decoder = _Decoder(encoding)
        except ValueError:
            raise failure(SecHTTPError, f"unsupported Content-Encoding {encoding!r}") from None
        digest = hashlib.sha256()
        parts: list[bytes] = []
        sniff = bytearray()
        raw_bytes = n_bytes = 0

        def emit(piece: bytes) -> None:
            nonlocal n_bytes
            n_bytes += len(piece)
            if n_bytes > max_bytes:
                raise failure(ResponseTooLarge, f"decoded body exceeds {max_bytes} bytes")
            if len(sniff) < _BLOCK_SNIFF_BYTES:
                sniff.extend(piece[:_BLOCK_SNIFF_BYTES - len(sniff)])
            digest.update(piece)
            parts.append(piece)

        def check_block_page() -> None:
            lowered = bytes(sniff).lower()
            if any(marker in lowered for marker in _BLOCK_PAGE_MARKERS):
                raise self._halt("SEC block page (request rate threshold / undeclared automated tool)",
                                 SecAccessDenied, original, status, attempts)

        # Byte guards fire per chunk. http.client stops identity reads at Content-Length and
        # returns b"" on an early close, so a short body is only visible in raw_bytes; excess
        # bytes imply conflicting framing (Transfer-Encoding plus Content-Length): malformed,
        # never retried. Transient body failures first check the received prefix for a block page.
        try:
            while True:
                try:
                    chunk = response.read(_CHUNK_SIZE)
                except http.client.IncompleteRead as exc:
                    raise _TransientRead(f"truncated body ({type(exc).__name__})") from exc
                except (OSError, http.client.HTTPException) as exc:
                    raise _TransientRead(type(exc).__name__) from exc
                if not chunk:
                    break
                raw_bytes += len(chunk)
                if raw_bytes > max_bytes:
                    raise failure(ResponseTooLarge, f"raw body exceeds {max_bytes} bytes")
                if declared is not None and raw_bytes > declared:
                    raise failure(SecHTTPError, f"body exceeds declared Content-Length {declared}")
                for piece in decoder.feed(chunk):
                    emit(piece)
            if declared is not None and raw_bytes < declared:
                raise _TransientRead(f"truncated body: {raw_bytes} of {declared} declared bytes")
            for piece in decoder.finish():
                emit(piece)
        except _TransientRead:
            check_block_page()
            raise
        check_block_page()
        return FetchResult(
            url=original, final_url=final_url, http_status=status, n_bytes=n_bytes,
            sha256=digest.hexdigest(), attempts=attempts, content_type=headers.get("Content-Type"),
            content=b"".join(parts),
        )
