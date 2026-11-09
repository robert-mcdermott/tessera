# Adopting Tessera

This guide takes a research organization from nothing to a running Tessera that people sign in to, with its links curated and a public site published. It puts the steps in order and says what to decide at each; the other guides have the details, and this one links to them.

Plan on an afternoon to install and describe the organization, a night for the first sync, and a few weeks of curation, shared out, before the profiles are trustworthy enough to publish.

## Rehearse on the demo organization

Before your own organization, go through the whole life of an installation on the demo: an invented research institute of about 100 people, built in seconds and needing no network. On any machine with [uv](https://docs.astral.sh/uv/) and git:

```bash
git clone https://github.com/robert-mcdermott/tessera.git
cd tessera
uv sync
uv run tessera demo                                    # builds ./demo
```

Turn on sign-in: in `demo/tessera.toml`, under `[server]`, set `accounts = true`. Then add an admin (you are asked for a password of at least 12 characters) and start the app:

```bash
uv run tessera --config demo/tessera.toml users add admin --role admin
uv run tessera --config demo/tessera.toml serve
```

Open <http://127.0.0.1:8000> and sign in as `admin` (if something else already uses port 8000, `serve` says so: add `--port 9000` and open that port instead). Then:

1. **Curate.** Open **Review queue**, and accept or reject a few suggestions (`A` and `R` on the keyboard). Each decision is recorded with your name; **Decision log** lists them.
2. **Make a profile public.** Open a person, choose **Make it public** on **About**, and look at **Preview**.
3. **Publish.** Stop the app (Ctrl-C) and run `uv run tessera --config demo/tessera.toml publish`. Open `demo/var/public/index.html`: the people and groups who chose to be public, and nothing else.

That is the life of an installation in small: data from public sources, decisions by people, and a site built from what they chose. Delete the `demo` folder when you are done.

## Decide first

| Decision | Choose | When |
|---|---|---|
| **Where it runs** | A virtual machine or server with [systemd](operations.md#on-a-server-with-systemd), or [containers](operations.md#in-a-container) | Whichever your team already runs and backs up |
| **The ledger** | SQLite (one file in the data home) or [PostgreSQL](operations.md#postgresql) | SQLite unless you already run PostgreSQL, or policy wants a database server |
| **The size of the server** | 8 GB of memory up to about 100,000 papers, 32 GB at a million | See [Sizing a server](operations.md#sizing-a-server) |
| **Sign-in** | Local accounts, or [single sign-on](operations.md#single-sign-on) with your identity provider, which brings its own multi-factor authentication | Single sign-on for more than a handful of people; keep one local admin as a way back in |
| **Who curates** | Curators for everyone, curators for some groups, researchers for themselves, managers for their groups' pages | See [Who decides](curation.md#who-decides) |
| **A public site** | None, or one served by your web server | Nothing is public unless a person or group chooses it |

Tessera needs no institutional system: its data come from PubMed, iCite, NIH RePORTER, ClinicalTrials.gov, ORCID, MeSH, and Grants.gov. A roster from human resources or a department list helps, but is optional.

## Install

Install on the server as [Running it as a service](operations.md#running-it-as-a-service) describes: the systemd units or the container in `deploy/`, a reverse proxy for HTTPS, and `[server] accounts = true`. Then:

```bash
tessera init           # writes tessera.toml and the data home
tessera users add NAME --role admin
tessera doctor         # every check should pass, except what the first sync will fix
```

(With systemd, run these as the `tessera` user, with `TESSERA_CONFIG` set; with containers, through `docker compose run --rm tessera`.)

## Describe your organization

Tessera finds your organization in public records by its names, so this is the step that matters most. In `tessera.toml`, fill in `[organization]` (its name, how it is written in affiliations, on NIH grants, and on ClinicalTrials.gov, and its email domains) and `[sources] contact_email`. [Describing your organization](organization-profile.md) explains each setting and how to check the result. An NCBI API key in the environment (`NCBI_API_KEY`) makes PubMed three times faster.

Then make it yours:

- **Its look**: under **Admin → Branding**, your name, colors, and logos, checked for contrast as you go ([Look and words](branding.md)).
- **Its words**: a vocabulary preset for your kind of organization, so groups and roles have the names you use ([The words](branding.md#the-words)).

## Your people and groups

The first sync builds the directory from NIH grant investigators. Most organizations add more:

- **A roster**: `tessera people import roster.csv` adds the people grants miss, with ORCID iDs, emails, units, titles, and dates ([Import a roster](getting-started.md#optional-import-a-roster)).
- **Groups**: divisions, programs, centers, cores, and labs, with members and their roles, by hand or from a file ([Groups](groups.md)).
- **Accounts**: one for each curator and admin, and for researchers who will keep their own profiles, linked to their person (`--person per_…`). With single sign-on, accounts are made at first sign-in and roles come from the provider ([Single sign-on](operations.md#single-sign-on)).

## The first sync

```bash
tessera sync
```

It fetches the organization's grants, studies, and papers, finds each person's papers, scores every candidate with its evidence, and builds the search index and the views behind **Find experts** and **Insight**. For a few thousand papers it takes minutes; for a large academic medical center, hours of downloading and up to an hour of work after it ([Sizing a server](operations.md#sizing-a-server)). Run it in the background or through `tessera-sync.service`, and follow it with `tessera runs` or the **Sources** page.

Then schedule the nightly sync and backup (the timers in `deploy/systemd`), and run `tessera doctor --production`.

## The first weeks of curation

A sync suggests; people decide. The suggestions come with their evidence and a score, and the review queue puts the quickest and most consequential first. A plan that works:

1. **Week one, the strong suggestions.** They are almost always right: work through them person by person, accepting a person's whole strong band at once with *Accept all strong* when it looks right (see [The review queue](curation.md#the-review-queue)). This alone makes most profiles usable.
2. **Discovered people and duplicates.** Promote affiliated authors who belong in the directory, and merge records that are one person (**Discovered people**, **Possible duplicates**). Run a sync afterwards, so new people's papers are found.
3. **Weeks two and three, the possible band.** Conflicts and ambiguous names come first. Share the work: curators for a group's people, and researchers for themselves (they know their own papers best).
4. **Then, the evidence model.** Once a few thousand decisions are recorded, evaluate the learned weights and consider auto-accepting what the model is sure of ([Learned weights and auto-accept](curation.md#learned-weights-and-auto-accept)).

Decisions survive every sync: a rejected paper is never suggested again for that person, and an accepted one stays ([What survives a sync](curation.md#what-survives-a-sync)).

## Groups, pages, and profiles

- **Managers** keep their groups' pages: what the group does, its themes, its openings, and which of its members' papers count as its own ([Groups](groups.md)). Each group's **Inbox** collects what needs their attention, and a weekly digest can email it.
- **Researchers** keep their profiles, or name someone to keep them, and build CVs and NIH biosketches from them ([Profiles](profiles.md)).

## The public site

When profiles and pages are ready, invite people and groups to make theirs public. Nothing is public until they do, and each chooses what their page shows. `tessera publish` builds the site as plain files in `<data home>/public`; serve them with your web server (the examples in `deploy/proxy` do), and publish after each sync ([The public site](public-site.md)).

## Keep it running

- **Every night**, the sync and the backup; copy backups off the machine ([Backups](operations.md#backups)).
- **Every week**, a glance at **Admin → Operations** for failed runs and what needs attention, and at the review queue.
- **At each release**, an upgrade: the new code, `tessera db upgrade`, and a restart ([Upgrading](operations.md#upgrading)).
- **When something is wrong**, `tessera doctor`, the **Sources** page, and the logs ([When something goes wrong](operations.md#when-something-goes-wrong)).

## Getting help

- The guides, here and at [robert-mcdermott.github.io/tessera](https://robert-mcdermott.github.io/tessera/).
- Questions and problems: the repository's [issues](https://github.com/robert-mcdermott/tessera/issues).
- A security problem: report it privately, as [SECURITY.md](../SECURITY.md) describes.
