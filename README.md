# Tessera

**Every contribution, connected.**

Tessera is an open research intelligence platform for academic research organizations. It assembles an organization's research footprint from public sources (PubMed, NIH RePORTER, ClinicalTrials.gov, iCite, and ORCID), connects people, publications, grants, and clinical studies, shows the evidence behind every connection, and helps people find who works on what and who should meet.

- **Starts from public data.** No HR feeds or institutional integrations needed to get going.
- **Evidence on every link.** Each connection records its source, method, likelihood, and review history.
- **Humans decide.** Automation suggests; curators confirm; decisions survive every refresh.
- **Fast to curate.** A keyboard-driven review queue, with the evidence for each suggestion on screen.
- **Open and portable.** Apache 2.0, runs on a laptop or a single server, data in open formats, re-brandable.

> **Status:** version 0.2.0. It builds the research graph, supports curation with accounts and roles, and adds discovery: expert finding, similar people, the collaboration graph, missed connections, and the unit matrix. Lab pages, reports, and metrics come next; see the [roadmap](spec/roadmap.md).

## What it does

- **Finds your organization in public records:** grants in NIH RePORTER, studies in ClinicalTrials.gov, and papers in PubMed whose authors list your organization, using a profile of how your organization's name is written.
- **Builds a people directory** from grant investigators and an optional roster, and proposes affiliated authors it discovers for you to promote or dismiss.
- **Suggests who wrote what,** scoring each candidate paper on explainable evidence: ORCID iDs and public ORCID records, email domains, affiliations, grant acknowledgments, coauthors, topics, journals, career span, and how common the name is. Weights can be learned from your own decisions, and auto-accept can be switched on where the measured precision allows.
- **Connects records:** papers to the grants they acknowledge, studies to their publications and grants, investigators to grants, and officials to studies.
- **Lets people decide,** in a review queue, on profiles, and in bulk, with every decision logged and reversible; with accounts, each person signs in and decides what their role allows. Duplicate records of one person are proposed and merged reversibly.
- **Finds expertise and connections:** "who works on X?" in plain words, each person's topics with what drives them, similar people, graphs of who works with whom and how research areas connect, pairs who share interests but have never worked together, and units compared by interest and collaboration.
- **Serves a fast web app and a JSON API:** search, browsable directories with facets, profiles, record pages with provenance, and data health, in light and dark versions of three color palettes.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), which provides Python 3.12 or newer.

```bash
git clone https://github.com/robert-mcdermott/tessera.git
cd tessera
uv sync
uv run tessera init      # writes tessera.toml and a data home in ./var
```

Edit the `[organization]` section of `tessera.toml` to describe how your organization appears in public records ([how](docs/organization-profile.md)), then:

```bash
uv run tessera sync      # fetch public data, build the directory, suggest authorships
uv run tessera serve     # open http://127.0.0.1:8000
```

Later syncs are incremental; run `sync` daily or weekly. Optional: import a roster (`uv run tessera people import roster.csv`) and set `NCBI_API_KEY` for faster PubMed requests.

## Documentation

- [Getting started](docs/getting-started.md), [Describing your organization](docs/organization-profile.md), [Curating](docs/curation.md), and [Finding experts](docs/discovery.md)
- [Configuration](docs/configuration.md), [Running Tessera](docs/operations.md), [The API](docs/api.md), and the [Command reference](docs/cli.md)
- [`spec/`](spec/README.md): vision, architecture, data model, evidence engine, roadmap, and design decisions

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The test suite runs offline: `uv run pytest`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
