"""Many people at once (PLT-16): simulated users against ``tessera serve`` with several
processes, at a realistic pace and without pause.

Each user is a thread with its own connection, asking for a mix of the app's main requests
(profiles, searches, lists, the review queue, groups, grants, Insight figures). At a realistic
pace each waits 2 to 8 seconds between requests, faster than people read; without pause each
asks again as soon as it has an answer, several times the load real people make. Each run
starts a new server, so its caches are empty, as they are after the nightly sync.
"""

from __future__ import annotations

import random
import statistics
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tessera.bench.serving import cpu_seconds, memory_mb, serving

PACES: dict[str, tuple[float, float] | None] = {
    "realistic": (2.0, 8.0),  # seconds between one user's requests
    "without pause": None,
}
BUDGETS = {"realistic": (300.0, 2000.0), "without pause": (1000.0, 5000.0)}  # slowest 5%, slowest
WARM_SECONDS = 30.0
TERMS = ["memory T cells", "tumor", "synapse", "vaccine", "single-cell", "cryo-electron",
         "screening", "antiviral", "DNA repair", "sleep"]  # fmt: skip


@dataclass
class LoadPage:
    name: str
    requests: int
    p50_ms: float
    p95_ms: float
    max_ms: float


@dataclass
class LoadRun:
    pace: str
    users: int
    workers: int
    seconds: float
    requests: int
    per_second: float
    errors: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    cpu_cores: float | None = None  # processor time the server used, per second of the run
    memory_mb: float | None = None  # the server's processes together, at the end
    # The requests begun after the first WARM_SECONDS, once the caches the first ones fill are
    # full: what people meet for the rest of the day, unless the data changes again.
    warm_p95_ms: float | None = None
    warm_max_ms: float | None = None
    pages: list[LoadPage] = field(default_factory=list)

    @property
    def budget(self) -> tuple[float, float]:
        return BUDGETS[self.pace]

    @property
    def within(self) -> bool | None:
        if not self.requests:
            return None
        p95, slowest = self.budget
        return self.errors == 0 and self.p95_ms <= p95 and self.max_ms <= slowest


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _targets(client: Any) -> list[tuple[str, int, Callable[[random.Random], str]]]:
    people = client.get("/api/v1/people", params={"limit": 200, "sort": "activity"}).json()["data"]
    ids = [p["id"] for p in people] or ["none"]
    surnames = sorted({p["name"].split()[-1] for p in people if p.get("name")}) or ["none"]
    groups = [g["slug"] for g in client.get("/api/v1/groups", params={"limit": 200}).json()["data"]]
    grants = [g["core_project_num"] for g in client.get("/api/v1/grants", params={"limit": 100})
              .json()["data"] if g.get("core_project_num")] or ["none"]  # fmt: skip
    metrics = [m["id"] for m in client.get("/api/v1/insight/metrics").json()["data"]]

    def figure(r: random.Random) -> str:
        metric = r.choice(metrics)
        division = "?kind=division" if metric == "funding.by_group" else ""
        return f"/api/v1/insight/figures/{metric}{division}"

    mix: list[tuple[str, int, Callable[[random.Random], str]]] = [
        ("A profile", 20, lambda r: f"/api/v1/people/{r.choice(ids)}"),
        ("A profile's publications", 10,
         lambda r: f"/api/v1/people/{r.choice(ids)}/publications?limit=50"),
        ("Search everything", 12, lambda r: f"/api/v1/search?q={r.choice(TERMS)}"),
        ("Search for a name", 6, lambda r: f"/api/v1/search?q={r.choice(surnames)}"),
        ("People directory", 8, lambda r: "/api/v1/people?limit=50"),
        ("Publications list", 6,
         lambda r: f"/api/v1/publications?limit=50&offset={r.randint(0, 2000)}"),
        ("Grants list", 3, lambda r: "/api/v1/grants?limit=50"),
        ("Review queue", 8, lambda r: f"/api/v1/review?limit=20&offset={r.randint(0, 200)}"),
        ("Find experts", 5, lambda r: f"/api/v1/experts?q={r.choice(TERMS)}"),
        ("Home page counts", 5, lambda r: "/api/v1/stats"),
        ("A grant's thread", 4, lambda r: f"/api/v1/grants/{r.choice(grants)}/thread"),
    ]  # fmt: skip
    if groups:
        mix += [
            ("A group", 5, lambda r: f"/api/v1/groups/{r.choice(groups)}"),
            ("A group's members", 3, lambda r: f"/api/v1/groups/{r.choice(groups)}/members"),
        ]
    if metrics:
        mix.append(("An Insight figure", 5, figure))
    return mix


def run_once(path: Path, *, pace: str, users: int, seconds: float, workers: int,
             seed: int = 11) -> LoadRun:  # fmt: skip
    """One run: a new server, ``users`` simulated users for ``seconds``, then their numbers."""
    import httpx

    wait = PACES[pace]
    with serving(path, workers=workers) as (base, server):
        with httpx.Client(base_url=base, timeout=60) as client:
            mix = _targets(client)
        names = [m[0] for m in mix]
        weights = [m[1] for m in mix]
        makers = {m[0]: m[2] for m in mix}
        results: list[tuple[str, float, bool, float]] = []
        lock = threading.Lock()
        started = time.perf_counter()
        end = started + seconds

        def user(i: int) -> None:
            r = random.Random(seed * 1000 + i)
            with httpx.Client(base_url=base, timeout=120) as own:
                if wait:
                    time.sleep(r.uniform(0, wait[1] - wait[0]))  # people do not start together
                while time.perf_counter() < end:
                    name = r.choices(names, weights)[0]
                    url = makers[name](r)
                    begun = time.perf_counter()
                    try:
                        ok = own.get(url).status_code == 200
                    except httpx.HTTPError:
                        ok = False
                    ms = (time.perf_counter() - begun) * 1000
                    with lock:
                        results.append((name, ms, ok, begun - started))
                    if wait:
                        time.sleep(r.uniform(*wait))

        cpu_before = cpu_seconds(server.pid)
        threads = [threading.Thread(target=user, args=(i,), daemon=True) for i in range(users)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        took = time.perf_counter() - started
        cpu_after = cpu_seconds(server.pid)
        memory = memory_mb(server.pid)
    times = [ms for _, ms, _, _ in results]
    warm = [ms for _, ms, _, at in results if at >= WARM_SECONDS]
    by_page: dict[str, list[float]] = defaultdict(list)
    for name, ms, _, _ in results:
        by_page[name].append(ms)
    cores = None
    if cpu_before is not None and cpu_after is not None and took > 0:
        cores = round((cpu_after - cpu_before) / took, 2)
    return LoadRun(
        pace=pace,
        users=users,
        workers=workers,
        seconds=round(took, 1),
        requests=len(results),
        per_second=round(len(results) / took, 1) if took else 0.0,
        errors=sum(1 for _, _, ok, _ in results if not ok),
        p50_ms=round(statistics.median(times), 1) if times else 0.0,
        p95_ms=round(_percentile(times, 0.95), 1) if times else 0.0,
        max_ms=round(max(times), 1) if times else 0.0,
        cpu_cores=cores,
        memory_mb=memory,
        warm_p95_ms=round(_percentile(warm, 0.95), 1) if warm else None,
        warm_max_ms=round(max(warm), 1) if warm else None,
        pages=[
            LoadPage(
                name=name,
                requests=len(by_page[name]),
                p50_ms=round(statistics.median(by_page[name]), 1),
                p95_ms=round(_percentile(by_page[name], 0.95), 1),
                max_ms=round(max(by_page[name]), 1),
            )
            for name in names
            if by_page.get(name)
        ],
    )


def describe(run: LoadRun) -> list[str]:
    processes = "1 process" if run.workers == 1 else f"{run.workers} processes"
    errors = "no errors" if not run.errors else f"{run.errors:,} errors"
    use = []
    if run.cpu_cores is not None:
        use.append(f"{run.cpu_cores:.2f} cores")
    if run.memory_mb is not None:
        use.append(f"{run.memory_mb / 1024:.1f} GB")
    lines = [
        f"    {run.users} users, {run.pace}, {processes}, {run.seconds:.0f} s: "
        f"{run.requests:,} requests ({run.per_second:.1f} a second), {errors}"
        + (f"; the server used {' and '.join(use)}" if use else "")
    ]
    p95, slowest = run.budget
    mark = {None: "", True: "  within budget", False: "  OVER BUDGET"}[run.within]
    lines.append(
        f"      {'every request':<28} median {run.p50_ms:>7.1f}  slowest 5% {run.p95_ms:>7.1f}  "
        f"slowest {run.max_ms:>8.1f} ms  (budget {p95:.0f} and {slowest:.0f}){mark}"
    )
    if run.warm_p95_ms is not None and run.warm_max_ms is not None:
        lines.append(
            f"      {f'after the first {WARM_SECONDS:.0f} s':<28} {'':>14}  slowest 5% "
            f"{run.warm_p95_ms:>7.1f}  slowest {run.warm_max_ms:>8.1f} ms"
        )
    for page in run.pages:
        lines.append(
            f"      {page.name:<28} median {page.p50_ms:>7.1f}  slowest 5% {page.p95_ms:>7.1f}  "
            f"slowest {page.max_ms:>8.1f} ms  ({page.requests:,})"
        )
    return lines


def load(path: Path, *, users: int, seconds: float, workers: int, say: Callable[[str], None],
         runs: list[LoadRun], paces: tuple[str, ...] = tuple(PACES)) -> None:  # fmt: skip
    for pace in paces:
        say(f"  {users} simulated users, {pace}, for {seconds:.0f} s, on {workers} "
            f"server process{'es' if workers > 1 else ''} …")  # fmt: skip
        run = run_once(path, pace=pace, users=users, seconds=seconds, workers=workers)
        runs.append(run)
        for line in describe(run):
            say(line)


__all__ = [
    "BUDGETS",
    "PACES",
    "WARM_SECONDS",
    "LoadPage",
    "LoadRun",
    "describe",
    "load",
    "run_once",
]
