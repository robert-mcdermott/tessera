"""``tessera bench``: how Tessera performs on this machine at a chosen size (PLT-14, PLT-16).

1. **Build** (once per folder): a simulated organization written to the lake, then everything a
   first sync does locally: the roster and groups imported, people, links, and authorship
   resolved, the strong suggestions accepted as a curator would, and the search index, concept
   profiles, group attribution, and opportunity fit built.
2. **Nightly:** one day's new papers (300) added, then what a nightly sync does after fetching:
   resolve, and every build again.
3. **Pages:** ``tessera serve`` started on a free port, and the app's main requests timed (p50
   and p95), one at a time, with the server's memory.
4. **Many people at once:** simulated users against the server with several processes, at a
   realistic pace and without pause (``tessera.bench.load``).

Nothing touches the network. The report (printed, and saved as JSON beside the organization)
names the machine, so results from different servers can be compared.
"""

from __future__ import annotations

import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update

from tessera import __version__
from tessera.bench.load import LoadRun, load
from tessera.bench.serving import memory_mb, serving
from tessera.config import Config, load_config
from tessera.home import DataHome

NEW_PAPERS = 300  # one day's new papers for a large institution
STATE = "bench-state.json"


@dataclass
class Step:
    name: str
    seconds: float
    detail: str = ""
    peak_mb: float | None = None  # the process's peak memory so far; the step that raised it


@dataclass
class Page:
    name: str
    path: str
    samples: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    first_ms: float = 0.0  # the first request, before anything is cached
    budget_ms: float | None = None
    cold: bool = False  # judged by the first request rather than the p95

    @property
    def within(self) -> bool | None:
        if self.budget_ms is None:
            return None
        return (self.first_ms if self.cold else self.p95_ms) <= self.budget_ms


@dataclass
class Report:
    people: int
    papers: int
    machine: dict[str, Any]
    build: list[Step] = field(default_factory=list)
    nightly: list[Step] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    server_memory_mb: float | None = None
    ledger: str = "SQLite"
    load: list[LoadRun] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["pages"] = [{**asdict(p), "within": p.within} for p in self.pages]
        out["load"] = [{**asdict(r), "within": r.within} for r in self.load]
        return out


def machine() -> dict[str, Any]:
    memory = None
    try:
        if sys.platform == "darwin":
            memory = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
        elif Path("/proc/meminfo").exists():
            line = Path("/proc/meminfo").read_text().splitlines()[0]
            memory = int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        memory = None
    chip = platform.processor() or platform.machine()
    try:
        if sys.platform == "darwin":
            chip = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "system": f"{platform.system()} {platform.release()}",
        "cpu": chip,
        "cores": os.cpu_count(),
        "memory_gb": round(memory / 2**30, 1) if memory else None,
        "python": platform.python_version(),
        "tessera": __version__,
        "date": date.today().isoformat(),
    }


class Timer:
    def __init__(self, steps: list[Step], say: Callable[[str], None]) -> None:
        self.steps, self.say = steps, say

    @contextmanager
    def __call__(self, name: str) -> Iterator[Step]:
        step = Step(name, 0.0)
        self.say(f"  {name} …")
        start = time.perf_counter()
        yield step
        step.seconds = round(time.perf_counter() - start, 2)
        step.peak_mb = peak_memory_mb()
        self.steps.append(step)
        notes = [n for n in (step.detail, _memory(step.peak_mb)) if n]
        self.say(f"    {_duration(step.seconds)}{f'  ({"; ".join(notes)})' if notes else ''}")


def peak_memory_mb() -> float | None:
    """This process's peak resident memory, where the system says (not on Windows)."""
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 2**20 if sys.platform == "darwin" else peak / 2**10, 1)


def _memory(mb: float | None) -> str:
    if mb is None:
        return ""
    return f"peak memory {mb / 1024:.1f} GB" if mb >= 1024 else f"peak memory {mb:.0f} MB"


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s"


def _record(engine: Any, home: DataHome, source: str, dataset: Any, records: list[dict],
            mode: str) -> None:  # fmt: skip
    from tessera.demo import _record as record

    record(engine, home, source, dataset, records, mode)


def _sizes(home: DataHome, config: Config, engine: Any = None) -> dict[str, int]:
    """The lake, derived files, and ledger on disk (a PostgreSQL ledger: its database's size)."""

    def size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0

    out = {"lake": size(home.lake), "derived": size(home.root / "derived")}
    if config.ledger_url.startswith("sqlite"):
        out["ledger"] = size(home.root / "ledger.db")
    elif engine is not None and engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with engine.connect() as conn:
            out["ledger"] = int(
                conn.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
            )
    return out


# --- 1. build ------------------------------------------------------------------------------------


def build(path: Path, people: int, papers: int, *, seed: int, say: Callable[[str], None],
          report: Report) -> None:  # fmt: skip
    from tessera.bench import simulate as sim
    from tessera.demo import demo_config_text, federal_opportunities
    from tessera.demo.groups import registry
    from tessera.demo.mesh import mesh_records
    from tessera.evidence.people import import_roster
    from tessera.evidence.resolve import resolve
    from tessera.groups.importer import import_groups
    from tessera.jobs.build import (
        build_attribution,
        build_discovery,
        build_opportunities,
        build_search,
    )
    from tessera.lake.schemas import (
        CTGOV,
        GRANTS_GOV,
        ICITE,
        MESH,
        ORCID,
        PUBMED,
        REPORTER,
        REPORTER_LINKS,
    )
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    today = date.today()
    var = path / "var"
    if var.exists():
        shutil.rmtree(var)
    path.mkdir(parents=True, exist_ok=True)
    config_path = path / "tessera.toml"
    if not config_path.exists():
        config_path.write_text(demo_config_text(), encoding="utf-8")
    config = load_config(config_path)
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    timer = Timer(report.build, say)
    try:
        migrate.upgrade(engine)
        with timer("simulate the people, groups, grants, and studies") as step:
            org = sim.simulate(people, papers, seed=seed, today=today)
            step.detail = f"{len(org.people):,} people in {len(org.lab_members):,} labs"
        with timer("write grants, studies, MeSH, and opportunities to the lake"):
            _record(engine, home, "reporter", REPORTER, org.reporter, "organization")
            _record(engine, home, "ctgov", CTGOV, org.studies, "organization")
            _record(engine, home, "mesh", MESH, mesh_records(today.year), "annual")
            _record(engine, home, "grants_gov", GRANTS_GOV, federal_opportunities(today), "open")
        works: dict[str, tuple[int, str]] = {}
        rng = random.Random(seed + 5)
        with timer(f"simulate and write {papers:,} papers and their citation metrics") as step:
            for batch in sim.papers(org):
                _record(engine, home, "pubmed", PUBMED, batch, "affiliation")
                _record(engine, home, "icite", ICITE, sim.icite(batch, today, rng), "refresh")
                works.update((r["pmid"], (r["pub_year"], r["journal"])) for r in batch)
                say(f"    {len(works):,} papers written")
            _record(engine, home, "reporter", REPORTER_LINKS, org.links, "links")
            step.detail = f"{len(org.links):,} grant acknowledgments"
        with timer("write ORCID records"):
            _record(engine, home, "orcid", ORCID, sim.orcid_records(org, works, rng), "refresh")
        del works
        with timer("import the roster"):
            roster = path / "roster.csv"
            lines = ["family,given,email,orcid,unit,kind,title,start"]
            for row in org.roster:
                values = [row[k] for k in ("family", "given", "email", "orcid", "unit")]
                lines.append(",".join(f'"{v}"' for v in [*values, "division", row["title"],
                                                           row["start"]]))  # fmt: skip
            roster.write_text("\n".join(lines) + "\n", encoding="utf-8")
            import_roster(engine, roster, today=today, vocabulary=config.vocabulary)
        with timer("import groups and memberships"):
            rows = registry(org, today)  # type: ignore[arg-type]
            import_groups(
                engine,
                rows,
                source="registry",
                vocabulary=config.vocabulary,
                actor="Registry import",
                today=today,
            )
        with timer("resolve people, links, and authorship (a first sync)") as step:
            counts = resolve(config, home, engine, today=today).counts
            step.detail = _authorship(counts)
        with timer("accept the strong suggestions, as a curator would") as step:
            step.detail = f"{_accept_strong(engine, config):,} accepted"
        with timer("resolve again, nothing new"):
            resolve(config, home, engine, today=today)
        with timer("build the search index"):
            build_search(config, home, engine, today=today)
        with timer("build concept profiles, similar people, and the graph"):
            build_discovery(config, home, engine)
        with timer("build group attribution"):
            build_attribution(config, home, engine, today=today)
        with timer("build opportunity fit"):
            build_opportunities(config, home, engine, today=today)
        (path / STATE).write_text(json.dumps({"people": people, "papers": papers, "seed": seed,
                                              "nightly": 0}))  # fmt: skip
    finally:
        engine.dispose()


def _authorship(counts: dict[str, Any]) -> str:
    a = counts.get("authorship") or {}
    made = a.get("candidates") or a.get("suggested") or a.get("created")
    return f"{made:,} authorship suggestions" if isinstance(made, int) else ""


def _accept_strong(engine: Any, config: Config) -> int:
    """Accept every strong suggestion, with a decision for each, in bulk."""
    from tessera.core.clock import utc_now
    from tessera.ledger.tables import assertion, curation_event

    now = utc_now()
    with engine.begin() as conn:
        ids = [
            r[0]
            for r in conn.execute(
                select(assertion.c.id).where(
                    assertion.c.predicate == "authored",
                    assertion.c.status == "suggested",
                    assertion.c.score >= config.evidence.strong_threshold,
                )
            )
        ]
        for start in range(0, len(ids), 5000):
            chunk = ids[start : start + 5000]
            conn.execute(
                update(assertion)
                .where(assertion.c.id.in_(chunk))
                .values(status="accepted", updated_at=now)
            )
            conn.execute(
                insert(curation_event),
                [{"assertion_id": i, "actor": "Benchmark curator", "action": "accept", "at": now}
                 for i in chunk],
            )  # fmt: skip
    return len(ids)


# --- 2. nightly ----------------------------------------------------------------------------------


def nightly(path: Path, *, say: Callable[[str], None], report: Report) -> None:
    from tessera.bench import simulate as sim
    from tessera.evidence.resolve import resolve
    from tessera.jobs.build import (
        build_attribution,
        build_discovery,
        build_opportunities,
        build_search,
    )
    from tessera.lake.schemas import ICITE, PUBMED
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    state = json.loads((path / STATE).read_text())
    config = load_config(path / "tessera.toml")
    home = DataHome(config.data.home)
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)  # an organization built by an earlier version
    timer = Timer(report.nightly, say)
    today = date.today()
    try:
        night = state["nightly"] + 1
        org = sim.simulate(state["people"], NEW_PAPERS, seed=state["seed"], today=today)
        with timer(f"write {NEW_PAPERS} new papers"):
            batch = next(sim.papers(org, first_pmid=990_000_000 + night * 10_000,
                                    seed_offset=night, year=today.year))  # fmt: skip
            _record(engine, home, "pubmed", PUBMED, batch, "affiliation")
            rng = random.Random(night)
            _record(engine, home, "icite", ICITE, sim.icite(batch, today, rng), "refresh")
        with timer("resolve (one day's new papers)") as step:
            step.detail = _authorship(resolve(config, home, engine, today=today).counts)
        with timer("rebuild the search index"):
            build_search(config, home, engine, today=today)
        with timer("rebuild concept profiles, similar people, and the graph"):
            build_discovery(config, home, engine)
        with timer("rebuild group attribution"):
            build_attribution(config, home, engine, today=today)
        with timer("rebuild opportunity fit"):
            build_opportunities(config, home, engine, today=today)
        state["nightly"] = night
        (path / STATE).write_text(json.dumps(state))
        report.sizes = _sizes(home, config, engine)
    finally:
        engine.dispose()


# --- 3. pages ------------------------------------------------------------------------------------


def pages(path: Path, *, samples: int, say: Callable[[str], None], report: Report) -> None:
    """Each request alone, one after another, against one server process."""
    import httpx

    with serving(path) as (base, server), httpx.Client(base_url=base, timeout=120) as client:
        say(f"  timing the app's requests at {base} ({samples} of each) …")
        _time_pages(client, samples, report, say)
        report.server_memory_mb = memory_mb(server.pid)


def _time_pages(client: Any, samples: int, report: Report,
                say: Callable[[str], None]) -> None:  # fmt: skip
    rng = random.Random(3)
    people = client.get("/api/v1/people", params={"limit": 200, "sort": "activity"}).json()["data"]
    ids = [p["id"] for p in people] or ["none"]
    surnames = sorted({p["name"].split()[-1] for p in people if p.get("name")}) or ["none"]
    terms = ["memory T cells", "tumor", "synapse", "vaccine", "single-cell", "cryo-electron",
             "screening", "antiviral", "DNA repair", "sleep"]  # fmt: skip
    groups = [g["slug"] for g in client.get("/api/v1/groups", params={"limit": 200}).json()["data"]]
    grants = [g["core_project_num"] for g in client.get("/api/v1/grants", params={"limit": 100})
              .json()["data"] if g.get("core_project_num")] or ["none"]  # fmt: skip
    metrics = [m["id"] for m in client.get("/api/v1/insight/metrics").json()["data"]]

    def measure(name: str, budget: float | None, make: Callable[[], str],
                cold: bool = False) -> None:  # fmt: skip
        times = []
        first = make()
        for i in range(samples):
            url = first if i == 0 else make()
            start = time.perf_counter()
            response = client.get(url)
            times.append((time.perf_counter() - start) * 1000)
            if response.status_code != 200:
                raise RuntimeError(f"{url}: {response.status_code} {response.text[:200]}")
        first_ms = times[0]
        times.sort()
        p95 = times[min(len(times) - 1, round(0.95 * (len(times) - 1)))]
        page = Page(
            name=name,
            path=first.split("?")[0],
            samples=len(times),
            p50_ms=round(statistics.median(times), 1),
            p95_ms=round(p95, 1),
            max_ms=round(times[-1], 1),
            first_ms=round(first_ms, 1),
            budget_ms=budget,
            cold=cold,
        )
        report.pages.append(page)
        mark = (
            "" if page.within is None else ("  within budget" if page.within else "  OVER BUDGET")
        )
        say(f"    {name:<40} first {page.first_ms:>7.1f}  p50 {page.p50_ms:>7.1f}  "
            f"p95 {page.p95_ms:>7.1f} ms{mark}")  # fmt: skip

    measure("Home page counts", 300, lambda: "/api/v1/stats")
    measure("A profile", 300, lambda: f"/api/v1/people/{rng.choice(ids)}")
    measure("A profile's publications", 300,
            lambda: f"/api/v1/people/{rng.choice(ids)}/publications?limit=50")  # fmt: skip
    # Topic words match a large share of the papers (the worst case); a surname, what people
    # search for most, matches few.
    measure("Search everything", 200, lambda: f"/api/v1/search?q={rng.choice(terms)}")
    measure("Search everything for a name", 200, lambda: f"/api/v1/search?q={rng.choice(surnames)}")
    measure("Search people", 200, lambda: f"/api/v1/search?type=person&q={rng.choice(terms)}")
    measure("Search publications", 200,
            lambda: f"/api/v1/search?type=publication&q={rng.choice(terms)}")  # fmt: skip
    measure("People directory, with facets", 300, lambda: "/api/v1/people?limit=50")
    measure("Publications list, with facets", 300,
            lambda: f"/api/v1/publications?limit=50&offset={rng.randint(0, 2000)}")  # fmt: skip
    measure("Grants list, with facets", 300, lambda: "/api/v1/grants?limit=50")
    measure("Review queue", 100, lambda: f"/api/v1/review?limit=20&offset={rng.randint(0, 200)}")
    measure("Find experts", 300, lambda: f"/api/v1/experts?q={rng.choice(terms)}")
    if groups:
        measure("A group", 300, lambda: f"/api/v1/groups/{rng.choice(groups)}")
        measure("A group's members", 300, lambda: f"/api/v1/groups/{rng.choice(groups)}/members")
    measure("A grant's thread", 300, lambda: f"/api/v1/grants/{rng.choice(grants)}/thread")
    for metric in metrics:
        params = "?kind=division" if metric == "funding.by_group" else ""
        # Judged by the first request: later ones are served from the figure cache.
        url = f"/api/v1/insight/figures/{metric}{params}"
        measure(f"Insight: {metric}", 1000, lambda u=url: u, cold=True)


# --- the command ---------------------------------------------------------------------------------


STEPS = ("nightly", "pages", "load")


def _upgrade(path: Path) -> None:
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    engine = make_engine(load_config(path / "tessera.toml").ledger_url)
    try:
        migrate.upgrade(engine)
    finally:
        engine.dispose()


def run(path: Path, *, people: int, papers: int, seed: int = 7, rebuild: bool = False,
        samples: int = 30, users: int = 20, seconds: float = 60.0, workers: int = 4,
        steps: tuple[str, ...] = STEPS, say: Callable[[str], None] = print) -> Report:  # fmt: skip
    path = path.resolve()
    state = json.loads((path / STATE).read_text()) if (path / STATE).exists() else None
    fresh = rebuild or state is None or (state["people"], state["papers"]) != (people, papers)
    report = Report(people, papers, machine())
    if fresh:
        say(f"Building a simulated organization of {people:,} people and {papers:,} papers in "
            f"{path} …")  # fmt: skip
        build(path, people, papers, seed=seed, say=say, report=report)
    else:
        say(f"Using the simulated organization already in {path}.")
    report.ledger = (
        "PostgreSQL" if "postgresql" in load_config(path / "tessera.toml").ledger_url else "SQLite"
    )
    _upgrade(path)  # an organization built by an earlier version
    if "nightly" in steps:
        say("A nightly sync (no fetching) …")
        nightly(path, say=say, report=report)
    if "pages" in steps:
        say("The app, one request at a time …")
        pages(path, samples=samples, say=say, report=report)
    if "load" in steps and users > 0:
        say("Many people at once …")
        load(path, users=users, seconds=seconds, workers=workers, say=say, runs=report.load)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = path / f"bench-{stamp}.json"
    out.write_text(json.dumps(report.to_json(), indent=1, default=str), encoding="utf-8")
    say(f"Report: {out}")
    return report


__all__ = ["NEW_PAPERS", "STEPS", "Page", "Report", "Step", "machine", "run"]
