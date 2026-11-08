# The assistant

Tessera can include an assistant that answers questions from Tessera's own records, citing them. It is **optional and off by default**: until the configuration turns it on, nothing in the app, the API, or the public site mentions it, and no model is ever called. Everything else in Tessera works the same with it off.

## What it does, and what it never does

- It answers from the records the person asking may read, through the same access policy as every page. It cannot change anything.
- It cites the records it used, and declines what the records do not support.
- Nothing sent to a model includes contact details (email addresses, phone numbers, postal addresses) or links nobody has accepted.
- Every exchange is logged: who asked, when, the provider and model, the prompt's version, the messages and tool calls with the records they returned, the answer, its citations, tokens, and time. Admins read the log in **Admin → Assistant**.

## Turning it on

Add an `[ai]` section to the configuration and restart `tessera serve`. Keys are never written in the file: the configuration names the environment variable that holds one, and a key written in the file stops Tessera from starting.

`provider` is the protocol the endpoint speaks:

| `provider` | Speaks | Works with |
|---|---|---|
| `openai` | OpenAI's Chat Completions API | OpenAI, and any endpoint that speaks it: Ollama, vLLM, LM Studio, gateways |
| `anthropic` | Anthropic's Messages API | Anthropic, and any endpoint that speaks it: Ollama, gateways |
| `bedrock` | Amazon Bedrock's Converse API | Amazon Bedrock in your AWS account |

A model served by Ollama on the same machine, through its OpenAI-compatible endpoint (no key needed):

```toml
[ai]
enabled = true
provider = "openai"
base_url = "http://127.0.0.1:11434/v1"
model = "gemma4:31b-cloud"
```

The same model through Ollama's Anthropic-compatible endpoint:

```toml
[ai]
enabled = true
provider = "anthropic"
base_url = "http://127.0.0.1:11434"
model = "gemma4:31b-cloud"
```

OpenAI's or Anthropic's own service: leave `base_url` empty and put the key in `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` (or name another variable with `api_key_env`):

```toml
[ai]
enabled = true
provider = "anthropic"
model = "the model's name"
```

Amazon Bedrock: name the region and the model (or inference profile) ID. Tessera reads an Amazon Bedrock API key from `AWS_BEARER_TOKEN_BEDROCK`, or else access keys from `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`, and signs its requests with them. With an AWS profile or single sign-on, `aws configure export-credentials --format env` puts them in the environment.

```toml
[ai]
enabled = true
provider = "bedrock"
region = "us-west-2"
model = "the model or inference profile ID"
```

Then check it:

```bash
tessera ai check
```

It sends one short question and one that needs a tool, and says how the model answered. `tessera doctor` also checks the section once it is on. Every setting is in [Configuration](configuration.md#ai).

## Choosing a model

The assistant relies on the model to call tools and to cite what they return, so a model needs tool calling. Larger models follow the instructions more reliably. Small local models are fine for trying it out; for everyday use, choose a model your organization trusts with the records the assistant reads, since everything it is sent leaves Tessera.
