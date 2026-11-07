# Tessera

**Every contribution, connected.**

Tessera is an open research intelligence platform for academic research organizations. It assembles an organization's research footprint from public sources (PubMed, NIH RePORTER, ClinicalTrials.gov, and iCite), connects people, publications, grants, and clinical studies into one graph, and shows the evidence behind every connection.

- **Starts from public data.** No HR feeds or institutional integrations needed to get going.
- **Evidence on every link.** Each connection records its source, method, confidence, and review history.
- **Humans decide.** Automation suggests; curators and researchers confirm; decisions survive every refresh.
- **Explainable discovery.** Find experts and collaborators, with the reasons shown.
- **Open and portable.** Apache 2.0, runs on a single machine, data in open formats, re-brandable.

> **Status:** early development. Wave 1 (v0.1.0) is in progress. See the [roadmap](spec/roadmap.md).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). Tessera runs on a single machine with Python 3.12+.

```bash
git clone <this repository> && cd tessera
uv sync
uv run tessera init      # writes tessera.toml and a data home in ./var
uv run tessera doctor    # checks the installation
```

Edit the `[organization]` section of `tessera.toml` to describe how your organization appears in public records, then fetch its public footprint and start the app:

```bash
uv run tessera sync          # fetch public data, find people, suggest authorships with evidence
uv run tessera people list   # who was found, with accepted and suggested papers
uv run tessera runs          # what was fetched, and whether each run completed
uv run tessera serve         # http://127.0.0.1:8000 (API reference at /api/docs)
```

Optional: import a roster (`uv run tessera people import roster.csv`) and set `NCBI_API_KEY` in your environment for faster PubMed requests. Later syncs are incremental.

## Documentation

The [`spec/`](spec/) folder holds the product vision, architecture, data model, roadmap, and design decisions. Start with [spec/README.md](spec/README.md).

## License

Apache License 2.0.
