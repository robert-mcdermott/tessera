"""A polite HTTP client shared by every connector.

* One rate limiter per source, shared by all clients of that source in the process.
* Retries with exponential backoff and jitter on network errors, 429, and 5xx, honoring
  ``Retry-After``.
* Other 4xx responses fail immediately with the API's own explanation.
* Identifies Tessera and the adopter's contact address in ``User-Agent``.
"""

from __future__ import annotations

import email.utils
import html
import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from tessera import __version__
from tessera.core.errors import TesseraError

log = logging.getLogger("tessera.connectors.http")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_BACKOFF = 60.0
MAX_RETRY_AFTER = 120.0


class SourceError(TesseraError):
    """A public source could not be reached or rejected a request."""

    def __init__(self, source: str, message: str, status: int | None = None) -> None:
        self.source = source
        self.status = status
        super().__init__(f"{source}: {message}")


class RateLimiter:
    """Enforce a minimum interval between requests (thread-safe)."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self, sleep: Callable[[float], None]) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if delay > 0:
            sleep(delay)


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(source: str, min_interval: float) -> RateLimiter:
    """The process-wide limiter for a source (created on first use)."""
    with _limiters_lock:
        limiter = _limiters.get(source)
        if limiter is None or limiter.min_interval != min_interval:
            limiter = _limiters[source] = RateLimiter(min_interval)
        return limiter


def user_agent(contact_email: str) -> str:
    contact = f"; mailto:{contact_email}" if contact_email else ""
    return f"Tessera/{__version__} (research information system{contact})"


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a ``Retry-After`` header: delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def error_message(response: httpx.Response) -> str:
    """The API's explanation of a rejected request, as plain text."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "title"):
            if data.get(key):
                return html.unescape(str(data[key]))[:500]
    if isinstance(data, list) and data:
        return html.unescape("; ".join(map(str, data)))[:500]
    text = response.text.strip()
    return html.unescape(text)[:500] if text else f"HTTP {response.status_code}"


class HttpClient:
    def __init__(
        self,
        source: str,
        base_url: str,
        *,
        min_interval: float,
        contact_email: str = "",
        timeout: float = 60.0,
        retries: int = 4,
        backoff: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.limiter = (
            limiter_for(source, min_interval) if transport is None else RateLimiter(min_interval)
        )
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
            headers={"User-Agent": user_agent(contact_email), "Accept-Encoding": "gzip"},
            transport=transport,
            follow_redirects=True,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: Any = None,
        retries: int | None = None,
    ) -> httpx.Response:
        """Send a request, retrying transient failures. Returns a 2xx response or raises."""
        attempts = (self.retries if retries is None else retries) + 1
        last_error: str = "no attempt made"
        for attempt in range(1, attempts + 1):
            self.limiter.wait(self.sleep)
            try:
                response = self.client.request(method, path, params=params, data=data, json=json)
            except httpx.TransportError as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
                delay = self._backoff(attempt)
            else:
                if response.is_success:
                    return response
                if response.status_code not in RETRY_STATUSES:
                    raise SourceError(self.source, error_message(response), response.status_code)
                last_error = f"HTTP {response.status_code}: {error_message(response)}"
                hinted = retry_after_seconds(response.headers.get("Retry-After"))
                delay = (
                    min(hinted, MAX_RETRY_AFTER) if hinted is not None else self._backoff(attempt)
                )
            if attempt < attempts:
                log.info(
                    "%s request failed (%s); retrying in %.1fs (attempt %d of %d)",
                    self.source,
                    last_error,
                    delay,
                    attempt + 1,
                    attempts,
                )
                self.sleep(delay)
        raise SourceError(self.source, f"giving up after {attempts} attempts: {last_error}")

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def _backoff(self, attempt: int) -> float:
        base = min(MAX_BACKOFF, self.backoff * (2 ** (attempt - 1)))
        return base + random.uniform(0, self.backoff)  # noqa: S311 - jitter, not security

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
