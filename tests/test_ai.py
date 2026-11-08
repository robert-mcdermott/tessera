"""The optional assistant's foundation (wave 5): invisible when off (AI-01, AI-07); three
protocols over plain HTTP with credentials from the environment (AI-02); nothing contact-like or
unreviewed sent (AI-05); every exchange logged."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tessera.ai import log
from tessera.ai.context import scrub
from tessera.ai.eventstream import EventStreamError, encode, frames
from tessera.ai.providers import ProviderError, make
from tessera.ai.sigv4 import sign
from tessera.api.access import POLICIES
from tessera.api.app import create_app
from tessera.config import AIConfig, example_config_text, load_config
from tessera.core.errors import ConfigError
from tessera.ledger.db import make_engine

from .conftest import Instance

LOCAL = "http://localhost"
WEB = Path(__file__).parents[1] / "src" / "tessera" / "web" / "static"
PUBLISH = Path(__file__).parents[1] / "src" / "tessera" / "publish"
MARKERS = ("assistant", "/api/v1/ai", "/js/ai/")
AI_SECTION = ('\n[ai]\nenabled = true\nprovider = "openai"\nmodel = "test-model"\n'
              'base_url = "http://127.0.0.1:9/v1"\n')  # fmt: skip


def _config(instance: Instance, extra: str = ""):  # type: ignore[no-untyped-def]
    instance.path.write_text(example_config_text() + extra, encoding="utf-8")
    return load_config(instance.path, env={})


# --- invisible when off ------------------------------------------------------------------------


def test_nothing_says_there_is_an_assistant_when_it_is_off(instance: Instance) -> None:
    config = _config(instance)
    assert not config.ai.enabled
    with TestClient(create_app(config), base_url=LOCAL) as client:
        document = client.get("/api/openapi.json").text.lower()
        assert not any(m in document for m in MARKERS)
        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["extensions"] == []
        assert client.get("/js/ai/assistant.js").status_code == 404
        assert client.get("/api/v1/ai/status").status_code == 404
        page = client.get("/").text.lower()
        assert not any(m in page for m in MARKERS)
    for path in [*WEB.rglob("*.js"), *WEB.rglob("*.html"), *WEB.rglob("*.css"),
                 *PUBLISH.rglob("*.py")]:  # fmt: skip
        text = path.read_text("utf-8").lower()
        assert not any(m in text for m in MARKERS), path


def test_turned_on_it_adds_its_routes_policies_and_module(instance: Instance) -> None:
    config = _config(instance, AI_SECTION)
    app = create_app(config)
    routes = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    added = {r for r in routes if r[1].startswith("/api/v1/ai/")}
    assert added and added <= set(app.state.extra_policies)
    assert not added & set(POLICIES)  # the static table never names them
    with TestClient(app, base_url=LOCAL) as client:
        assert client.get("/api/v1/meta").json()["data"]["extensions"] == ["/js/ai/assistant.js"]
        module = client.get("/js/ai/assistant.js")
        assert module.status_code == 200 and "export function install" in module.text
        assert client.get("/js/ai/notes.txt").status_code == 404
        assert client.get("/js/ai/missing.js").status_code == 404
        status = client.get("/api/v1/ai/status").json()["data"]
        assert status["model"] == "test-model" and status["left_today"] == 100


def test_keys_are_never_in_the_configuration(instance: Instance) -> None:
    for line in ('api_key = "sk-test"', 'headers = ["Authorization: Bearer sk-test"]'):
        with pytest.raises(ConfigError, match="never written in this file"):
            _config(instance, AI_SECTION + line + "\n")
    with pytest.raises(ConfigError, match=r"ai\.provider"):
        _config(instance, AI_SECTION.replace('"openai"', '"palm"'))
    with pytest.raises(ConfigError, match="region"):
        _config(instance, AI_SECTION.replace('"openai"', '"bedrock"').replace(
            'base_url = "http://127.0.0.1:9/v1"\n', ""))  # fmt: skip


# --- the three protocols -----------------------------------------------------------------------

TOPIC = {"type": "object", "properties": {"topic": {"type": "string"}}}
TOOLS = [{"name": "search_people", "description": "Find people.", "parameters": TOPIC}]


def _sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def test_openai_streams_text_and_tool_calls_split_across_chunks() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _sse([
            {"choices": [{"delta": {"content": "Look"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "search_people", "arguments": '{"top'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                "arguments": 'ic": "lymphoma"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
        ]) + b"data: [DONE]\n\n"  # fmt: skip
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    config = AIConfig(enabled=True, provider="openai", model="m", base_url="http://x/v1",
                      api_key_env="TEST_KEY")  # fmt: skip
    provider = make(config, env={"TEST_KEY": "sk-1"}, transport=httpx.MockTransport(answer))
    reply = provider.complete("System.", [{"role": "user", "text": "Who?"}], TOOLS)
    assert reply.text == "Look" and reply.stop == "tool_calls"
    assert reply.tool_calls == [{"id": "c1", "name": "search_people",
                                 "arguments": {"topic": "lymphoma"}}]  # fmt: skip
    assert reply.usage == {"input": 12, "output": 5}
    sent = json.loads(seen[0].content)
    assert seen[0].url == "http://x/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-1"
    assert sent["messages"][0] == {"role": "system", "content": "System."}
    assert sent["tools"][0]["function"]["name"] == "search_people"
    # A conversation with a tool's answer is sent back in the protocol's shape.
    messages = [{"role": "user", "text": "Who?"},
                {"role": "assistant", "text": "", "tool_calls": reply.tool_calls},
                {"role": "tool", "id": "c1", "name": "search_people", "content": "{}"}]  # fmt: skip
    provider.complete("System.", messages, TOOLS)
    again = json.loads(seen[1].content)["messages"]
    assert again[2]["tool_calls"][0]["function"]["arguments"] == '{"topic": "lymphoma"}'
    assert again[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


def test_anthropic_streams_text_and_tool_use() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _sse([
            {"type": "message_start", "message": {"usage": {"input_tokens": 20}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},  # noqa: E501
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},  # noqa: E501
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {
                "type": "tool_use", "id": "t1", "name": "search_people", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": '{"topic":'}},
            {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": ' "HIV"}'}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},  # noqa: E501
            {"type": "message_stop"},
        ])  # fmt: skip
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    config = AIConfig(enabled=True, provider="anthropic", model="m", base_url="http://x")
    provider = make(config, env={"ANTHROPIC_API_KEY": "k"}, transport=httpx.MockTransport(answer))
    reply = provider.complete("System.", [{"role": "user", "text": "Who?"}], TOOLS)
    assert reply.text == "Hi" and reply.stop == "tool_use"
    assert reply.tool_calls == [
        {"id": "t1", "name": "search_people", "arguments": {"topic": "HIV"}}
    ]
    assert reply.usage == {"input": 20, "output": 9}
    assert seen[0].url == "http://x/v1/messages" and seen[0].headers["x-api-key"] == "k"
    sent = json.loads(seen[0].content)
    assert sent["system"] == "System." and sent["tools"][0]["input_schema"]["type"] == "object"


def _bedrock_stream() -> bytes:
    events = [
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Hel"}}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "lo"}}),
        ("contentBlockStart", {"contentBlockIndex": 1, "start": {"toolUse": {
            "toolUseId": "u1", "name": "search_people"}}}),
        ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"topic"'}}}),  # noqa: E501
        ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": ': "AML"}'}}}),  # noqa: E501
        ("contentBlockStop", {"contentBlockIndex": 1}),
        ("messageStop", {"stopReason": "tool_use"}),
        ("metadata", {"usage": {"inputTokens": 30, "outputTokens": 7}}),
    ]  # fmt: skip
    return b"".join(encode({":event-type": kind, ":message-type": "event"}, json.dumps(data).encode())  # noqa: E501
                    for kind, data in events)  # fmt: skip


def test_bedrock_decodes_its_event_stream_with_an_api_key_or_signed_keys() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_bedrock_stream())

    config = AIConfig(enabled=True, provider="bedrock", region="us-west-2",
                      model="us.anthropic.example-v1:0")  # fmt: skip
    provider = make(config, env={"AWS_BEARER_TOKEN_BEDROCK": "abc"},
                    transport=httpx.MockTransport(answer))  # fmt: skip
    reply = provider.complete("System.", [{"role": "user", "text": "Who?"}], TOOLS)
    assert reply.text == "Hello" and reply.stop == "tool_use"
    assert reply.tool_calls == [
        {"id": "u1", "name": "search_people", "arguments": {"topic": "AML"}}
    ]
    assert reply.usage == {"input": 30, "output": 7}
    assert str(seen[0].url) == ("https://bedrock-runtime.us-west-2.amazonaws.com/model/"
                                "us.anthropic.example-v1%3A0/converse-stream")  # fmt: skip
    assert seen[0].headers["authorization"] == "Bearer abc"
    sent = json.loads(seen[0].content)
    assert sent["system"] == [{"text": "System."}]
    assert sent["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["type"] == "object"
    keys = {"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "session"}  # fmt: skip
    signed = make(config, env=keys, transport=httpx.MockTransport(answer))
    signed.complete("System.", [{"role": "user", "text": "Who?"}])
    auth = seen[1].headers["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-west-2/bedrock/aws4_request" in auth and "x-amz-security-token" in auth
    with pytest.raises(ProviderError, match="AWS_BEARER_TOKEN_BEDROCK"):
        make(config, env={}, transport=httpx.MockTransport(answer)).complete("S", [])


def test_errors_say_what_happened() -> None:
    config = AIConfig(enabled=True, provider="openai", model="m", base_url="http://x/v1")
    refused = make(config, env={}, transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": "bad key"})))  # fmt: skip
    with pytest.raises(ProviderError, match="401"):
        refused.complete("S", [{"role": "user", "text": "Hi"}])
    with pytest.raises(ProviderError, match="not enabled"):
        make(AIConfig())


@pytest.mark.parametrize(("method", "url", "signature"), [
    ("GET", "https://example.amazonaws.com/",
     "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"),
    ("POST", "https://example.amazonaws.com/",
     "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"),
    ("GET", "https://example.amazonaws.com/?Param2=value2&Param1=value1",
     "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"),
])  # fmt: skip
def test_signature_version_4_matches_the_published_vectors(method: str, url: str,
                                                           signature: str) -> None:  # fmt: skip
    """get-vanilla, post-vanilla, and get-vanilla-query-order-key-case from AWS's test suite."""
    headers = sign(method, url, {"Host": "example.amazonaws.com"}, b"", access_key="AKIDEXAMPLE",
                   secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", region="us-east-1",
                   service="service", now=datetime(2015, 8, 30, 12, 36, tzinfo=UTC))  # fmt: skip
    assert headers["Authorization"].endswith(f"Signature={signature}")


def test_event_streams_are_checked() -> None:
    message = encode({":event-type": "x"}, b"{}")
    assert list(frames([message[:5], message[5:]])) == [({":event-type": "x"}, b"{}")]
    broken = message[:-1] + bytes([message[-1] ^ 1])
    with pytest.raises(EventStreamError, match="checksum"):
        list(frames([broken]))
    with pytest.raises(EventStreamError, match="ended inside"):
        list(frames([message[:-3]]))


# --- what a model may see, and the log ---------------------------------------------------------


def test_contact_details_and_unreviewed_links_are_never_sent() -> None:
    record = {"name": "Ada Example", "email": "ada@example.edu", "phone_number": "555",
              "bio": "Write to ada@example.edu or call +1 (206) 555-0100.",
              "papers": [{"pmid": "1", "status": "accepted"}, {"pmid": "2", "status": "suggested"}],
              "contact": {"office": "B2"}}  # fmt: skip
    out = scrub(record)
    assert "email" not in out and "phone_number" not in out and "contact" not in out
    assert "ada@example.edu" not in out["bio"] and "555-0100" not in out["bio"]
    assert [p["pmid"] for p in out["papers"]] == ["1"]


def test_every_exchange_is_logged_and_old_ones_go(instance: Instance) -> None:
    config = AIConfig(enabled=True, provider="openai", model="m", log_days=30)
    engine = make_engine(instance.config.ledger_url)
    try:
        now = datetime.now(UTC)
        for started in (now - timedelta(days=40), now):
            log.record(engine, config, owner="operator", asked_by="Operator", kind="question",
                       prompt_version="assistant/v1", question="Who?", request={"messages": []},
                       answer="Nobody.", citations=[], usage={"input": 3, "output": 2},
                       status="answered", started_at=started)  # fmt: skip
        rows, total = log.recent(engine)
        assert total == 1 and rows[0]["model"] == "m"  # the 40-day-old one was dropped
        assert log.used_today(engine, "operator") == 1
        assert log.usage_by_day(engine)[0]["input_tokens"] == 3
    finally:
        engine.dispose()
