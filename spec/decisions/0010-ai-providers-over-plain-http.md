# ADR-0010: AI providers spoken over plain HTTP, without SDKs

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The optional assistant (ADR-0008) must work with whatever model an organization may use: any endpoint that speaks OpenAI's Chat Completions API or Anthropic's Messages API, official or self-hosted (including local servers such as Ollama and vLLM, and gateways), and Amazon Bedrock. Each vendor offers an SDK, and Bedrock's usually comes with the whole AWS SDK. Tessera keeps a small dependency budget (ADR-0002), and the assistant must leave no trace when it is off: no module loaded, no route registered, nothing in the API document or the web app (AI-07).

## Decision

- **Three protocols, spoken directly over the HTTP library Tessera already uses (httpx):** Chat Completions and Messages, both streamed as server-sent events, and Bedrock's Converse API, streamed as AWS's binary event stream. `[ai] provider` names the protocol, not the vendor, so any endpoint that speaks it works.
- **One shape inside Tessera** for messages, tool definitions, tool calls, streamed text, usage, and errors; the rest of Tessera never knows which provider answers.
- **Bedrock without the AWS SDK:** an Amazon Bedrock API key (`AWS_BEARER_TOKEN_BEDROCK`) is sent as a bearer token; access keys from the environment (with a session token when there is one) sign requests with Signature Version 4, written with the standard library and checked against AWS's published test vectors. The event stream is decoded with the standard library, checking both CRC-32 checksums of every message.
- **Credentials come only from the environment.** The configuration names the variable (`api_key_env`); a key, or an `Authorization` header, written in the configuration file is refused.
- **Invisible when off.** The assistant's routes, their access policies, its front-end module, and its admin page are installed only when `[ai] enabled`; the web app loads the front-end modules that `/api/v1/meta` names, so the app's own code never names the assistant. The `tessera ai` command is not listed in the help, and `tessera doctor` says nothing of it unless it is on.

## Consequences

- No new dependency. A new provider that speaks one of the three protocols needs only configuration.
- Tessera owns a small amount of protocol code (a few hundred lines) and must follow changes in the three APIs; recorded tests of each stream format guard the parsers.
- Bedrock credentials from a shared AWS profile or single sign-on are not read directly: `aws configure export-credentials --format env` puts them in the environment, or a Bedrock API key is used.
- Vendor-specific features beyond these protocols (built-in web search, file uploads) are not available, and are not needed: the assistant answers from Tessera's own records.

## Alternatives

- **Vendor SDKs:** each would add dependencies (the AWS SDK alone is large), and three of them would still need one shape in common.
- **A routing library that speaks to many providers:** a large, fast-moving dependency for a feature most installations leave off.
- **OpenAI-compatible only:** most local servers and gateways speak it, but the official Anthropic API and Bedrock would then need a proxy in front.
