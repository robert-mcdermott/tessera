"""The three protocols an assistant can speak, over plain HTTP (ADR-0010).

* ``openai``: any endpoint that speaks the Chat Completions API (the official service, gateways,
  and local servers such as Ollama or vLLM). ``base_url`` is the API root, such as
  ``https://api.openai.com/v1`` or ``http://127.0.0.1:11434/v1``.
* ``anthropic``: any endpoint that speaks the Messages API (the official service, gateways, and
  local servers such as Ollama). ``base_url`` is the root, such as ``https://api.anthropic.com``.
* ``bedrock``: Amazon Bedrock's Converse API, with an Amazon Bedrock API key
  (AWS_BEARER_TOKEN_BEDROCK) or access keys from the environment (signed with SigV4).

Every provider takes the same conversation and tools and answers with the same events, so the
rest of Tessera never knows which one is in use. Messages in that common shape are
``{"role": "user", "text"}``, ``{"role": "assistant", "text", "tool_calls"}``, and
``{"role": "tool", "id", "name", "content"}``; tools are ``{"name", "description",
"parameters"}`` (a JSON schema). Events are ``{"type": "text", "text"}``, ``{"type":
"tool_call", "id", "name", "arguments"}``, and finally ``{"type": "done", "stop", "usage"}``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from tessera.ai.eventstream import frames
from tessera.ai.sigv4 import sign
from tessera.config import AIConfig
from tessera.core.errors import TesseraError

Event = dict[str, Any]


class ProviderError(TesseraError):
    """The provider could not be reached, refused, or answered something unreadable."""


@dataclass
class Reply:
    """A whole answer, collected from the events."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def collect(events: Iterator[Event]) -> Reply:
    reply = Reply()
    for event in events:
        if event["type"] == "text":
            reply.text += event["text"]
        elif event["type"] == "tool_call":
            reply.tool_calls.append({k: event[k] for k in ("id", "name", "arguments")})
        elif event["type"] == "done":
            reply.stop, reply.usage = event.get("stop"), event.get("usage") or {}
    return reply


def _arguments(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        message = f"The model sent tool arguments that are not JSON: {text[:200]}"
        raise ProviderError(message) from exc
    return value if isinstance(value, dict) else {}


class Provider:
    name = ""

    def __init__(self, config: AIConfig, env: Mapping[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:  # fmt: skip
        self.config = config
        self.env = os.environ if env is None else env
        extra = {}
        for header in config.headers:
            name, _, value = header.partition(":")
            extra[name.strip()] = value.strip()
        self.client = httpx.Client(timeout=config.timeout_seconds, transport=transport,
                                   headers=extra)  # fmt: skip

    def close(self) -> None:
        self.client.close()

    def stream(self, system: str, messages: list[dict[str, Any]],
               tools: list[dict[str, Any]] | None = None) -> Iterator[Event]:  # fmt: skip
        raise NotImplementedError

    def complete(self, system: str, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None) -> Reply:  # fmt: skip
        return collect(self.stream(system, messages, tools))

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        try:
            request = self.client.build_request("POST", url, json=body, headers=headers)
            response = self.client.send(self._sign(request), stream=True)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: could not reach {url}: {exc}") from exc
        if response.status_code >= 400:
            detail = response.read().decode("utf-8", "replace")[:500]
            response.close()
            raise ProviderError(f"{self.name}: {response.status_code} from {url}: {detail}")
        return response

    def _sign(self, request: httpx.Request) -> httpx.Request:
        return request


class OpenAIProvider(Provider):
    """The Chat Completions API, streamed as server-sent events."""

    name = "openai"

    def _messages(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("text") or None,
                            "tool_calls": [{"id": c["id"], "type": "function",
                                            "function": {"name": c["name"],
                                                         "arguments": json.dumps(c["arguments"])}}
                                           for c in m["tool_calls"]]})  # fmt: skip
            else:
                out.append({"role": m["role"], "content": m.get("text") or ""})
        return out

    def stream(self, system: str, messages: list[dict[str, Any]],
               tools: list[dict[str, Any]] | None = None) -> Iterator[Event]:  # fmt: skip
        body: dict[str, Any] = {
            "model": self.config.model, "messages": self._messages(system, messages),
            "max_tokens": self.config.max_tokens, "temperature": self.config.temperature,
            "stream": True, "stream_options": {"include_usage": True},
        }  # fmt: skip
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {"Accept": "text/event-stream"}
        key = self.env.get(self.config.key_env) if self.config.key_env else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        response = self._post(f"{self.config.url}/chat/completions", body, headers)
        calls: dict[int, dict[str, Any]] = {}
        stop, usage = None, {}
        try:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage = {"input": u.get("prompt_tokens", 0),
                             "output": u.get("completion_tokens", 0)}  # fmt: skip
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield {"type": "text", "text": delta["content"]}
                    for call in delta.get("tool_calls") or []:
                        empty = {"id": "", "name": "", "arguments": ""}
                        slot = calls.setdefault(call.get("index", len(calls)), empty)
                        fn = call.get("function") or {}
                        slot["id"] = call.get("id") or slot["id"]
                        slot["name"] = fn.get("name") or slot["name"]
                        slot["arguments"] += fn.get("arguments") or ""
                    stop = choice.get("finish_reason") or stop
        except json.JSONDecodeError as exc:
            raise ProviderError(f"openai: an unreadable event: {exc}") from exc
        finally:
            response.close()
        for i in sorted(calls):
            c = calls[i]
            yield {"type": "tool_call", "id": c["id"] or f"call_{i}", "name": c["name"],
                   "arguments": _arguments(c["arguments"])}  # fmt: skip
        yield {"type": "done", "stop": stop, "usage": usage}


class AnthropicProvider(Provider):
    """The Messages API, streamed as server-sent events."""

    name = "anthropic"

    def _messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)  # results of one turn go together
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                content: list[dict[str, Any]] = []
                if m.get("text"):
                    content.append({"type": "text", "text": m["text"]})
                for c in m.get("tool_calls") or []:
                    content.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                                    "input": c["arguments"]})  # fmt: skip
                out.append({"role": "assistant", "content": content or ""})
            else:
                out.append({"role": "user", "content": m.get("text") or ""})
        return out

    def stream(self, system: str, messages: list[dict[str, Any]],
               tools: list[dict[str, Any]] | None = None) -> Iterator[Event]:  # fmt: skip
        body: dict[str, Any] = {
            "model": self.config.model, "messages": self._messages(messages),
            "max_tokens": self.config.max_tokens, "temperature": self.config.temperature,
            "stream": True,
        }  # fmt: skip
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"],
                              "input_schema": t["parameters"]} for t in tools]  # fmt: skip
        headers = {"anthropic-version": "2023-06-01", "Accept": "text/event-stream"}
        key = self.env.get(self.config.key_env) if self.config.key_env else None
        if key:
            headers["x-api-key"] = key
        response = self._post(f"{self.config.url}/v1/messages", body, headers)
        blocks: dict[int, dict[str, Any]] = {}
        stop, usage = None, {"input": 0, "output": 0}
        try:
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip() or "{}")
                kind = event.get("type")
                if kind == "message_start":
                    u = (event.get("message") or {}).get("usage") or {}
                    usage["input"] = u.get("input_tokens", 0)
                elif kind == "content_block_start":
                    block = event.get("content_block") or {}
                    blocks[event.get("index", 0)] = {**block, "json": ""}
                    if block.get("type") == "text" and block.get("text"):
                        yield {"type": "text", "text": block["text"]}
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        yield {"type": "text", "text": delta.get("text", "")}
                    elif delta.get("type") == "input_json_delta":
                        blocks.setdefault(event.get("index", 0), {"json": ""})
                        blocks[event.get("index", 0)]["json"] += delta.get("partial_json", "")
                elif kind == "content_block_stop":
                    block = blocks.get(event.get("index", 0)) or {}
                    if block.get("type") == "tool_use":
                        given = block.get("input") or {}
                        args = _arguments(block["json"]) if block["json"] else given
                        yield {"type": "tool_call", "id": block.get("id"),
                               "name": block.get("name"), "arguments": args}  # fmt: skip
                elif kind == "message_delta":
                    stop = (event.get("delta") or {}).get("stop_reason") or stop
                    out = (event.get("usage") or {}).get("output_tokens")
                    usage["output"] = out if out is not None else usage["output"]
                elif kind == "error":
                    raise ProviderError(f"anthropic: {event.get('error')}")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"anthropic: an unreadable event: {exc}") from exc
        finally:
            response.close()
        yield {"type": "done", "stop": stop, "usage": usage}


class BedrockProvider(Provider):
    """Amazon Bedrock's Converse API, streamed as an AWS event stream (ConverseStream)."""

    name = "bedrock"

    def _messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "tool":
                block = {"toolResult": {"toolUseId": m["id"], "content": [{"text": m["content"]}]}}
                if out and out[-1]["role"] == "user" and "toolResult" in out[-1]["content"][0]:
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                content: list[dict[str, Any]] = [{"text": m["text"]}] if m.get("text") else []
                for c in m.get("tool_calls") or []:
                    content.append({"toolUse": {"toolUseId": c["id"], "name": c["name"],
                                                "input": c["arguments"]}})  # fmt: skip
                out.append({"role": "assistant", "content": content or [{"text": ""}]})
            else:
                out.append({"role": "user", "content": [{"text": m.get("text") or ""}]})
        return out

    def _sign(self, request: httpx.Request) -> httpx.Request:
        token = self.env.get(self.config.api_key_env or "AWS_BEARER_TOKEN_BEDROCK")
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
            return request
        access, secret = self.env.get("AWS_ACCESS_KEY_ID"), self.env.get("AWS_SECRET_ACCESS_KEY")
        if not (access and secret):
            raise ProviderError("bedrock: set AWS_BEARER_TOKEN_BEDROCK (an Amazon Bedrock API "
                                "key), or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")  # fmt: skip
        body = request.read()
        headers = {"content-type": request.headers.get("content-type", "application/json"),
                   "host": request.url.netloc.decode()}  # fmt: skip
        signed = sign("POST", str(request.url), headers, body, access_key=access, secret_key=secret,
                      session_token=self.env.get("AWS_SESSION_TOKEN"), region=self.config.region,
                      service="bedrock")  # fmt: skip
        for k, v in signed.items():
            request.headers[k] = v
        return request

    def stream(self, system: str, messages: list[dict[str, Any]],
               tools: list[dict[str, Any]] | None = None) -> Iterator[Event]:  # fmt: skip
        body: dict[str, Any] = {
            "messages": self._messages(messages),
            "inferenceConfig": {"maxTokens": self.config.max_tokens,
                                "temperature": self.config.temperature},
        }  # fmt: skip
        if system:
            body["system"] = [{"text": system}]
        if tools:
            body["toolConfig"] = {"tools": [{"toolSpec": {
                "name": t["name"], "description": t["description"],
                "inputSchema": {"json": t["parameters"]}}} for t in tools]}  # fmt: skip
        url = f"{self.config.url}/model/{quote(self.config.model, safe='')}/converse-stream"
        response = self._post(url, body, {"Accept": "application/vnd.amazon.eventstream"})
        tool: dict[str, Any] | None = None
        stop, usage = None, {}
        try:
            for headers, payload in frames(response.iter_bytes()):
                kind = headers.get(":event-type")
                if headers.get(":message-type") == "exception":
                    said = payload.decode("utf-8", "replace")[:300]
                    raise ProviderError(f"bedrock: {kind}: {said}")
                data = json.loads(payload or b"{}")
                if kind == "contentBlockStart":
                    start = (data.get("start") or {}).get("toolUse")
                    if start:
                        tool = {"id": start.get("toolUseId"), "name": start.get("name"), "json": ""}
                elif kind == "contentBlockDelta":
                    delta = data.get("delta") or {}
                    if "text" in delta:
                        yield {"type": "text", "text": delta["text"]}
                    elif "toolUse" in delta and tool is not None:
                        tool["json"] += delta["toolUse"].get("input", "")
                elif kind == "contentBlockStop" and tool is not None:
                    yield {"type": "tool_call", "id": tool["id"], "name": tool["name"],
                           "arguments": _arguments(tool["json"])}  # fmt: skip
                    tool = None
                elif kind == "messageStop":
                    stop = data.get("stopReason")
                elif kind == "metadata":
                    u = data.get("usage") or {}
                    usage = {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)}
        except json.JSONDecodeError as exc:
            raise ProviderError(f"bedrock: an unreadable event: {exc}") from exc
        finally:
            response.close()
        yield {"type": "done", "stop": stop, "usage": usage}


PROVIDERS: dict[str, type[Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "bedrock": BedrockProvider,
}


def make(config: AIConfig, env: Mapping[str, str] | None = None,
         transport: httpx.BaseTransport | None = None) -> Provider:  # fmt: skip
    if not config.enabled:
        raise ProviderError("The assistant is not enabled.")
    kind = PROVIDERS.get(config.provider)
    if kind is None:
        raise ProviderError(f"Unknown provider {config.provider!r}.")
    return kind(config, env, transport)


__all__ = ["PROVIDERS", "Provider", "ProviderError", "Reply", "collect", "make"]
