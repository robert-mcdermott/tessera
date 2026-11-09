# Compatibility

From 1.0, Tessera promises that what you build on it keeps working when you upgrade: scripts that call its API, the configuration you wrote, the data home it keeps, the exports you take, and the addresses of your public site. This guide says what is promised, what is not, and how anything is retired.

## Version numbers

Versions are *major.minor.patch*:

- **Patch** releases (1.0.1) fix problems and change nothing you rely on.
- **Minor** releases (1.1.0) add things. Nothing promised below changes or goes away in one, except a deprecated item past its sunset (see [Retiring something](#retiring-something)).
- **Major** releases (2.0.0) may change or remove what is promised. Each says what, and how to move.

Every change is in the [changelog](../CHANGELOG.md), with **Breaking** changes (only ever in a major release, or for an experimental route) and **Deprecated** items called out.

## The API

Every route under `/api/v1` is either **stable** or **experimental**, marked as `x-stability` in the OpenAPI document (served at `/api/openapi.json`; a copy for this version is in [openapi.json](openapi.json)).

A **stable** route changes only by adding to it:

- new routes;
- new optional parameters, and new optional fields in a request body;
- new fields in an answer, and new values where a field lists choices (treat a value you do not know as you would an unknown one);
- more items in a list, and more facets.

So a client should ignore fields it does not know, and should not depend on the order of an object's fields. Everything else stays: a stable route's address, its parameters, the fields of its request body and of its answer, their types, and the [conventions](api.md#conventions) (the envelope, paging, errors, and identifiers).

**Experimental** routes serve views that are still settling, or depend on outside services: the graph views' data, the branding editor's, job metrics, how an opportunity fits, the evidence model's evaluation, and the optional assistant. They may change in a minor release, and the changelog says how.

A test keeps the promise: [`tests/api_contract.json`](../tests/api_contract.json) records what every stable route accepts and what it answers with (each answer recorded from the demonstration organization, field by field, with its type), and the test fails on any change that would break a client. Recording it again adds what is new and never removes anything.

An Insight figure's rows are described by its metric's definition, which has a version (`publications.count/v1`): a metric whose counting changes gets a new version, and every figure says which it used (see [Insight](insight.md#definitions)).

## The command line

Commands, their options, and their exit codes (0 for success, anything else for a failure) keep working; new commands and options may be added. What a command prints for people to read may change; for anything a script needs, use the API or the exports.

## The configuration file

Every setting in `tessera.toml` keeps its meaning, and every new setting has a default, so a configuration written for 1.0 loads in every 1.x release. If a setting is ever renamed, the old name keeps working, with a warning, until the next major release. The same holds for the environment variables that override settings (`TESSERA_SERVER_WORKERS`, and the like).

## The data home and the ledger

A data home from any earlier release upgrades with `tessera db upgrade`, keeping every record; the search index and other derived files are rebuilt by `tessera build`. Upgrades go forward only: to go back, restore the backup taken before upgrading (see [Upgrading](operations.md#upgrading)).

The data home's layout, the ledger's tables, and the lake's files are Tessera's own, and may change in any release, with migrations that carry your data forward. Read them through the API or an export rather than directly.

## Exports

The exit export (`tessera export`) keeps its tables and their columns; new ones may be added, and its data dictionary describes every one. Backups (`tessera backup`) restore into the same release or any later one.

## The public site

The addresses of the public site's pages (each group's and person's page, the pieces a group's own website embeds, the indexes, and the sitemap) stay the same, so links to them keep working.

## Retiring something

Anything stable that is to go away is first **deprecated**: it keeps working until its **sunset**, at least six months after it was deprecated and never before the next minor release. The changelog lists it under **Deprecated**, with what to use instead. A deprecated API route also says so in every answer:

- `Deprecation`: when it was deprecated ([RFC 9745](https://www.rfc-editor.org/rfc/rfc9745)), such as `@1799020800`;
- `Sunset`: the date it stops working ([RFC 8594](https://www.rfc-editor.org/rfc/rfc8594));
- `Link`: what replaces it;

and is marked `deprecated` in the OpenAPI document, with `x-sunset` and `x-replacement`. Nothing is deprecated in 1.0.

## Security fixes

Fixes go into the latest minor release of the current major version. See [Security](../SECURITY.md).
