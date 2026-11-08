from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tessera.connectors.http import HttpClient, RateLimiter, SourceError, retry_after_seconds


def client(handler, sleeps: list[float], **kwargs) -> HttpClient:  # type: ignore[no-untyped-def]
    return HttpClient(
        "test",
        "https://api.example.org/",
        min_interval=0.0,
        contact_email="research-it@example.edu",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kwargs,
    )


def test_success_and_user_agent() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with client(handler, []) as http:
        assert http.get("thing", params={"a": "1"}).json() == {"ok": True}
    ua = seen[0].headers["user-agent"]
    assert ua.startswith("Tessera/") and "mailto:research-it@example.edu" in ua
    assert seen[0].url == httpx.URL("https://api.example.org/thing?a=1")


def test_retries_transient_errors_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok")])
    sleeps: list[float] = []
    with client(lambda r: next(responses), sleeps, backoff=1.0) as http:
        assert http.get("x").text == "ok"
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 2.0 and 2.0 <= sleeps[1] <= 3.0  # exponential with jitter


def test_retry_after_is_honored() -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    sleeps: list[float] = []
    with client(lambda r: next(responses), sleeps) as http:
        http.get("x")
    assert sleeps == [7.0]


def test_gives_up_after_retries() -> None:
    sleeps: list[float] = []
    failing = client(lambda r: httpx.Response(500, json={"message": "boom"}), sleeps, retries=2)
    with failing as http, pytest.raises(SourceError, match="after 3 attempts: HTTP 500: boom"):
        http.get("x")
    assert len(sleeps) == 2


def test_network_errors_are_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200)

    with client(handler, []) as http:
        http.get("x")
    assert calls["n"] == 2


def test_client_errors_fail_fast_with_api_message() -> None:
    sleeps: list[float] = []
    rejecting = client(lambda r: httpx.Response(400, json={"message": "Bad &amp; wrong"}), sleeps)
    with rejecting as http, pytest.raises(SourceError) as caught:
        http.get("x")
    assert caught.value.status == 400
    assert "Bad & wrong" in str(caught.value)
    assert sleeps == []


def test_retry_after_parsing() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    later = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert retry_after_seconds("12") == 12.0
    assert retry_after_seconds(later, now=now) == 30.0
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds(None) is None


def test_rate_limiter_spaces_requests() -> None:
    clock = {"t": 100.0}
    limiter = RateLimiter(1.0, clock=lambda: clock["t"])
    sleeps: list[float] = []
    limiter.wait(sleeps.append)
    limiter.wait(sleeps.append)
    limiter.wait(sleeps.append)
    assert sleeps == [1.0, 2.0]
