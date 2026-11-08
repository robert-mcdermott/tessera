# Tessera

**Every contribution, connected.** Tessera is an open research intelligence platform for academic research organizations. It assembles an organization's research footprint from public sources (PubMed, NIH RePORTER, ClinicalTrials.gov, iCite, and ORCID), connects people, publications, grants, and clinical studies, shows the evidence behind every connection, and helps people find who works on what and who should meet.

- **Starts from public data.** No HR feeds or institutional integrations needed to get going.
- **Evidence on every link.** Each connection records its source, method, likelihood, and review history.
- **Humans decide.** Automation suggests; curators confirm; decisions survive every refresh.
- **Finds expertise and connections.** Who works on a topic, in plain words; similar people; graphs of who works with whom; and pairs who share interests but have never worked together.
- **Open and portable.** Apache 2.0, runs on a laptop or a single server, data in open formats, re-brandable.

## Try it

Tessera needs [uv](https://docs.astral.sh/uv/), which provides Python 3.12 or newer. The demo builds an invented research institute, with about 100 people and their publications, grants, studies, and a curator's decisions, in a few seconds:

```bash
git clone https://github.com/robert-mcdermott/tessera.git
cd tessera
uv sync
uv run tessera demo
uv run tessera --config demo/tessera.toml serve
```

Then open <http://127.0.0.1:8000>. To set Tessera up for your own organization, start with [Getting started](getting-started.md).

## The guides

| Guide | Read it to |
|---|---|
| [Getting started](getting-started.md) | Install Tessera, describe your organization, run the first sync, and open the app |
| [Describing your organization](organization-profile.md) | Choose the names Tessera uses to find your people and records, and check the result |
| [Curating](curation.md) | Work through suggestions, promote discovered people, and understand what decisions do |
| [Finding experts](discovery.md) | Find who works on a topic, see similar people and connections, and understand what drives a profile |
| [Configuration](configuration.md) | Look up any setting, including palettes, your logo, and evidence thresholds |
| [Running Tessera](operations.md) | Schedule syncs, back up, upgrade, use PostgreSQL, and diagnose problems |
| [The API](api.md) | Build on Tessera's JSON API; every endpoint is in the [API reference](api-reference.md) |
| [Command reference](cli.md) | Look up any command and its options |

What changed in each release is in the [changelog](../CHANGELOG.md).
