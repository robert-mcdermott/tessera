# Wave 8: Stable (v1.0.0)

**Goal:** make Tessera dependable to run and to build on: steady when many people use it at once, at the size it is built for; an API, a configuration, and a data home that later releases promise to keep; an upgrade from any earlier release that loses nothing; and hardened by our own testing, so it is ready to demonstrate and to pilot.

**Branch:** `wave-8` · **Target version:** `1.0.0` · **Status:** ◐ in progress

## Exit criteria

The wave is done when, on a fresh checkout:

1. With 20 simulated users on the 100,000-publication organization, run by `tessera bench`: at a realistic pace, the slowest 5% of requests take under 300 ms and none takes over 2 s; with every user requesting without pause, on the processes the service files run, the slowest 5% take under 1 s and none takes over 5 s; and each Insight figure is built once however many people ask for it at the same moment (PLT-16).
2. Every route under `/api/v1` is marked stable or experimental; a test fails on any change that would break a client of a stable route; and the guides say what 1.0 promises (the API, the command line, the configuration file, the data home, the export, and the public site's addresses) and how anything in them is retired (PLT-17).
3. A data home made by each earlier release, 0.1.0 to 0.7.0, upgrades to 1.0 with every record kept and the app serving it, on SQLite, and on PostgreSQL 16 from 0.7.0 (PLT-18).
4. Our own hardening is done and its findings fixed: the server and the nightly sync run for hours and days of simulated use without errors or growing memory; drills of the failures an installation will meet recover as *Running Tessera* says; and a review of the code that decides or protects things finds nothing left open.

## Decisions from the maintainer

- **Wave 8 is 1.0.0** (2026-11-09). Waves keep counting after it; each is a minor release (wave 9 becomes 1.1.0) unless it breaks a promise of 1.0, which would need 2.0.
- **The size Tessera is built for** (2026-11-09): a few thousand people and up to about 300,000 publications. The load-test fixes are in this wave; the work recorded at 1,000,000 publications waits under *Later, for much larger organizations* in the roadmap.
- **No outside pilot before 1.0** (2026-11-09). There is no pilot feedback to wait for beyond the work done so far; hardening comes from our own testing and review. Demonstrations and a pilot with real users follow the release.

### Defaults taken (either can change)

- **No new runtime dependencies.** Several server processes come from uvicorn's own `--workers`; nothing else is added to run them.
- **One process on a laptop, four on a server.** `tessera serve` runs `[server] workers` processes, one unless set; the container and the systemd unit run four, and `tessera doctor --production` warns at one.
- **Breaking fixes now, none after.** Anything inconsistent in `/api/v1` is fixed in this wave and listed under **Breaking** in the changelog; after 1.0, a stable route changes only by adding to it, and retiring one gives notice through at least one minor release.
- **Experimental is allowed.** Routes that are young or depend on outside services (the AI assistant, for one) may be marked experimental, which the guides explain; they may change in a minor release.
- **A screen-reader script, not a gate.** The pass with a real screen reader that wave 7 recommended becomes a short script of tasks for the maintainer to run with VoiceOver or NVDA during the demonstrations; whatever it finds is fixed in a later release.
- **The live Amazon Bedrock check** still waits for the maintainer's go-ahead and does not hold the release.

---

## Slice 1: Many people at once ☐

**Goal:** the server stays fast when many people use it at once, and an organization can measure that on its own server (PLT-16).

- [ ] `tessera bench --users N`: simulated users against the server with its configured processes, at a realistic pace and without pause, reporting requests per second, the median, slowest 5%, and slowest of each page, and the server's processor use
- [ ] Each Insight input and figure built once: requests for one that is being built wait for that result, and a failure is not kept
- [ ] Whatever else the load test shows piling up gets the same treatment
- [ ] The limit on failed sign-ins kept in the ledger, so it holds across processes and restarts
- [ ] `tessera serve --workers N` and `[server] workers`; the container and systemd unit run four; whatever must happen once per server, not once per process, is found and made so
- [ ] *Running Tessera*: how many processes to run, the memory each takes, and how to run the load test

**Acceptance:** exit criterion 1, measured on the laptop with the realistic pace and without pause; the sign-in limit holds across four processes.

## Slice 2: The 1.0 promise ☐

**Goal:** a client, an adopter's scripts, and an installation can rely on what 1.0 provides (PLT-17).

- [ ] A review of every `/api/v1` route for consistency (names, envelopes, errors, paging, dates, identifiers), with what is inconsistent fixed now and listed under **Breaking**
- [ ] Each route marked stable or experimental, in the OpenAPI document and the API guide
- [ ] A committed snapshot of the stable API and a test that fails when a stable route, parameter, or response field is removed or changes type, and passes when one is added
- [ ] A deprecation policy: `Deprecation` and `Sunset` headers on a retiring route, a changelog section, and the notice given
- [ ] *Compatibility*, a guide to what 1.0 promises: the API, the command line, the configuration file, the data home's layout, the export format, and the public site's addresses; `spec/process.md` and `SECURITY.md` updated for versions after 1.0

**Acceptance:** the snapshot test fails on a removed field, a renamed parameter, and a changed type, and passes on an added field; every route has its level in the OpenAPI document.

## Slice 3: Upgrades from every earlier release ☐

**Goal:** an installation of any 0.x release becomes a 1.0 installation and keeps everything (PLT-18).

- [ ] A script that installs each tagged release, 0.1.0 to 0.7.0, in its own environment, makes a data home with it (the demonstration organization where the release has one, otherwise a sync from the recorded responses its tests use), and upgrades it with the current code
- [ ] Every record kept: counts of every table and a sample of records compared before and after; the main pages served; `tessera doctor` clean
- [ ] 0.7.0 on PostgreSQL 16 as well, and a backup made by an earlier release restored into 1.0
- [ ] Whatever trips is fixed; *Running Tessera*'s upgrade section covers moving to 1.0 from any 0.x release

**Acceptance:** exit criterion 3, by one command.

## Slice 4: Hardened ☐

**Goal:** find what a pilot would have found, by our own testing.

- [ ] A soak: the server under the realistic load for hours, with memory steady and no errors; a week of nightly syncs on the simulated organization, each finishing and leaving nothing growing that should not
- [ ] Failure drills: a source down, slow, or sending errors; a sync and a resolve stopped midway and run again; the disk filling during a sync; a damaged search index or derived file; the database unreachable at start; a restore from backup. Each recovers as *Running Tessera* says, or is fixed so that it does
- [ ] A review of the code that decides or protects things (evidence and resolving, access control, sessions, the public site's projection, migrations), with each finding fixed and a regression test
- [ ] Every command-line failure says what to do next; logs carry the run and contain no secrets
- [ ] Dependencies at their latest patch releases within the locked ranges, with no known vulnerability and their licenses checked

**Acceptance:** exit criterion 4; each drill and finding is recorded in the log below with what it led to.

## Slice 5: Release prep ☐

- [ ] Guides, screenshots, the documentation site's home page for 1.0, `CHANGELOG.md` for 1.0.0 with upgrade notes from every 0.x release, and the version bump
- [ ] The screen-reader script for the maintainer
- [ ] Checks: the full suite on SQLite and PostgreSQL 16; migrations up and down on both; the upgrade test from every earlier release; the load test; the pilot's ORCID works-list check; dependency and license review; the private-terms check; a pilot-name scan of every change since 0.7.0
- [ ] PR, merge, tag `v1.0.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance:** everything above passes, and the release is published on the maintainer's go-ahead.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-09 | Wave 8 planned as 1.0.0. Its first slice carries the three fixes put in the roadmap after wave 7's load test (20 simulated users on one server process at 100,000 publications: at a realistic pace, a median of 33 ms and a slowest request of 2 s; without pause, Insight figures each built by every request at once, waiting up to 93 s, and one process topping out near 20 requests a second, where four served ten times as many). The pilot-feedback item is replaced by our own hardening (slice 4), since no outside pilot comes before 1.0. |
