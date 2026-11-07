"""The ``tessera`` command-line interface.

Exit codes: 0 success, 1 error, 2 partial success.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from tessera import PRODUCT_NAME, TAGLINE, __version__
from tessera.config import (
    DEFAULT_CONFIG_NAME,
    ENV_CONFIG_PATH,
    config_warnings,
    example_config_text,
    find_config_path,
    load_config,
)
from tessera.core.errors import TesseraError
from tessera.core.log import configure_logging
from tessera.home import DataHome

from .output import table

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2

log = logging.getLogger("tessera.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tessera",
        description=f"{PRODUCT_NAME}: {TAGLINE}",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help=f"configuration file (default: ${ENV_CONFIG_PATH} or ./{DEFAULT_CONFIG_NAME})",
    )
    noise = parser.add_mutually_exclusive_group()
    noise.add_argument("-v", "--verbose", action="count", default=0, help="more output")
    noise.add_argument("-q", "--quiet", action="store_true", help="only warnings and errors")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = commands.add_parser("init", help="create a configuration file and data home")
    init.add_argument("--force", action="store_true", help="overwrite an existing config file")
    init.set_defaults(handler=cmd_init)

    doctor = commands.add_parser("doctor", help="check that this installation is ready")
    doctor.add_argument("--online", action="store_true", help="also test public API reachability")
    doctor.set_defaults(handler=cmd_doctor)

    db = commands.add_parser("db", help="ledger database maintenance")
    db_commands = db.add_subparsers(dest="db_command", metavar="ACTION", required=True)
    db_commands.add_parser("upgrade", help="apply pending migrations").set_defaults(
        handler=cmd_db_upgrade
    )
    db_commands.add_parser("current", help="show the schema revision").set_defaults(
        handler=cmd_db_current
    )

    lake = commands.add_parser("lake", help="lake (Parquet) maintenance")
    lake_commands = lake.add_subparsers(dest="lake_command", metavar="ACTION", required=True)
    lake_commands.add_parser("status", help="files and rows per table").set_defaults(
        handler=cmd_lake_status
    )
    compact = lake_commands.add_parser("compact", help="rewrite tables to current rows only")
    compact.add_argument("datasets", nargs="*", help="datasets to compact (default: all)")
    compact.set_defaults(handler=cmd_lake_compact)
    gc = lake_commands.add_parser("gc", help="delete superseded files past retention")
    gc.add_argument("--retention-days", type=float, default=7.0, metavar="DAYS")
    gc.set_defaults(handler=cmd_lake_gc)

    ingest = commands.add_parser(
        "ingest",
        help="fetch public data into the lake",
        description="Fetch from a public source. SOURCE is pubmed, icite, reporter, ctgov, "
        "or 'all' (every enabled source in a sensible order). Without --mode, an "
        "organization sweep is run (or an ID fetch if --ids is given).",
    )
    ingest.add_argument("source", help="pubmed | icite | reporter | ctgov | all")
    ingest.add_argument(
        "--mode",
        default="auto",
        help="org | query | ids | linked (pubmed) | pi (reporter) | refresh (icite)",
    )
    ingest.add_argument("--ids", nargs="+", default=[], metavar="ID", help="IDs to fetch")
    ingest.add_argument("--ids-file", metavar="PATH", help="file with one ID per line")
    ingest.add_argument("--query", help="source query (PubMed syntax, Essie, or RePORTER JSON)")
    ingest.add_argument("--since", type=_date_arg, metavar="YYYY-MM-DD")
    ingest.add_argument("--until", type=_date_arg, metavar="YYYY-MM-DD")
    ingest.add_argument("--full", action="store_true", help="ignore the saved checkpoint")
    ingest.add_argument(
        "--max-results", type=int, metavar="N", help="stop after N records (for quick checks)"
    )
    ingest.set_defaults(handler=cmd_ingest)

    replay_cmd = commands.add_parser(
        "replay", help="rebuild lake records from a run's raw payloads"
    )
    replay_cmd.add_argument("source")
    replay_cmd.add_argument("run_id")
    replay_cmd.set_defaults(handler=cmd_replay)

    resolve_cmd = commands.add_parser(
        "resolve", help="update people, links, and authorship suggestions from the lake"
    )
    resolve_cmd.add_argument("--people-only", action="store_true", help="stop after people")
    resolve_cmd.set_defaults(handler=cmd_resolve)

    build_cmd = commands.add_parser("build", help="rebuild derived artifacts")
    build_actions = build_cmd.add_subparsers(dest="build_command", metavar="WHAT", required=True)
    build_actions.add_parser("search", help="the search and serving index").set_defaults(
        handler=cmd_build_search
    )

    sync_cmd = commands.add_parser(
        "sync", help="fetch every source and resolve, in the right order (the usual command)"
    )
    sync_cmd.add_argument("--no-fetch", action="store_true", help="only resolve the lake")
    sync_cmd.add_argument("--full", action="store_true", help="ignore saved checkpoints")
    sync_cmd.set_defaults(handler=cmd_sync)

    people_cmd = commands.add_parser("people", help="the people directory")
    people_actions = people_cmd.add_subparsers(
        dest="people_command", metavar="ACTION", required=True
    )
    importer = people_actions.add_parser("import", help="import a roster CSV file")
    importer.add_argument("path", type=Path)
    importer.set_defaults(handler=cmd_people_import)
    lister = people_actions.add_parser("list", help="list people")
    lister.add_argument("--status", default="active", help="active | former | candidate | all")
    lister.add_argument("--limit", type=int, default=50)
    lister.set_defaults(handler=cmd_people_list)

    eval_cmd = commands.add_parser("eval", help="evaluate matching quality")
    eval_actions = eval_cmd.add_subparsers(dest="eval_command", metavar="WHAT", required=True)
    eval_authorship = eval_actions.add_parser(
        "authorship", help="precision, recall, and calibration of authorship suggestions"
    )
    eval_authorship.add_argument(
        "--gold", type=Path, metavar="CSV", help="adjudicated pairs: person,pmid,label"
    )
    eval_authorship.set_defaults(handler=cmd_eval_authorship)

    runs_cmd = commands.add_parser("runs", help="recent ingest runs")
    runs_cmd.add_argument("--source")
    runs_cmd.add_argument("--limit", type=int, default=20)
    runs_cmd.set_defaults(handler=cmd_runs)

    serve = commands.add_parser("serve", help="run the web app and API")
    serve.add_argument("--host", help="override server.host")
    serve.add_argument("--port", type=int, help="override server.port")
    serve.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    serve.set_defaults(handler=cmd_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    verbosity = -1 if args.quiet else args.verbose
    configure_logging(verbosity)
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_OK
    try:
        return int(args.handler(args) or EXIT_OK)
    except TesseraError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.warning("interrupted")
        return EXIT_ERROR


def _load(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    config = load_config(args.config)
    home = DataHome(config.data.home)
    if home.logs.is_dir():
        configure_logging(-1 if args.quiet else args.verbose, home.logs)
    return config, home


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None


def _engine(config):  # type: ignore[no-untyped-def]
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    engine = make_engine(config.ledger_url)
    if not migrate.is_current(engine):
        engine.dispose()
        raise TesseraError("The ledger schema is not current; run `tessera db upgrade`.")
    return engine


def _print_result(result) -> None:  # type: ignore[no-untyped-def]
    report = result.report
    found = "" if report.total_found is None else f", {report.total_found:,} found"
    written = sum(
        sum(stats.get("written", {}).values()) for stats in result.counts.get("lake", {}).values()
    )
    unchanged = sum(stats.get("unchanged", 0) for stats in result.counts.get("lake", {}).values())
    label = result.source if not result.plan else f"{result.source} ({result.plan.mode})"
    print(
        f"{label}: {result.status}{found}, {report.payloads} payloads, "
        f"{written:,} rows written, {unchanged:,} records unchanged  [{result.run_id or '-'}]"
    )
    for failure in report.failures[:5]:
        print(f"  failed batch: {failure}")
    if len(report.failures) > 5:
        print(f"  ... and {len(report.failures) - 5} more failed batches")
    for note in report.notes[:5]:
        print(f"  note: {note}")
    if result.error:
        print(f"  error: {result.error}")


# --- commands -------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    path = find_config_path(args.config)
    if path.exists() and not args.force:
        print(f"Using existing configuration {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example_config_text(), encoding="utf-8")
        print(f"Wrote {path} (edit the [organization] section before your first sync)")
    config = load_config(path)
    home = DataHome(config.data.home)
    home.create()
    print(f"Data home ready at {home.root}")
    engine = make_engine(config.ledger_url)
    try:
        migrate.upgrade(engine)
        print(f"Ledger ready (revision {migrate.current_revision(engine)})")
    finally:
        engine.dispose()
    for warning in config_warnings(config):
        print(f"Note: {warning}")
    print("Next: run `tessera doctor`, then `tessera serve`.")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import exit_code, print_checks, run_checks

    checks = run_checks(args.config, online=args.online)
    print_checks(checks)
    return exit_code(checks)


def cmd_db_upgrade(args: argparse.Namespace) -> int:
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    config, home = _load(args)
    home.require() if not config.ledger.url else None
    engine = make_engine(config.ledger_url)
    try:
        before = migrate.current_revision(engine)
        migrate.upgrade(engine)
        after = migrate.current_revision(engine)
    finally:
        engine.dispose()
    print(f"Ledger at revision {after}" + ("" if before == after else f" (was {before})"))
    return EXIT_OK


def cmd_db_current(args: argparse.Namespace) -> int:
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    config, _ = _load(args)
    engine = make_engine(config.ledger_url)
    try:
        current = migrate.current_revision(engine)
    finally:
        engine.dispose()
    head = migrate.head_revision()
    status = "up to date" if current == head else f"behind (latest is {head})"
    print(f"Ledger revision {current or 'none'}: {status}")
    return EXIT_OK if current == head else EXIT_ERROR


def cmd_lake_status(args: argparse.Namespace) -> int:
    from tessera.lake import Lake

    _, home = _load(args)
    home.require()
    with Lake(home.lake) as lake:
        stats = lake.stats()
    table(
        [
            (
                s["table"],
                s["files"],
                f"{s['rows_current']:,}",
                f"{s['rows_written']:,}",
                s["updated_at"] or "never",
            )
            for s in stats
        ],
        headers=("table", "files", "current rows", "rows written", "updated"),
    )
    return EXIT_OK


def cmd_lake_compact(args: argparse.Namespace) -> int:
    from tessera.lake.compact import compact_all
    from tessera.lake.schemas import DATASETS

    _, home = _load(args)
    home.require()
    unknown = sorted(set(args.datasets) - set(DATASETS))
    if unknown:
        raise TesseraError(f"Unknown dataset(s) {unknown}; choose from {sorted(DATASETS)}")
    for report in compact_all(home.lake, args.datasets or None):
        if report.get("skipped"):
            print(f"{report['dataset']}: {report['skipped']}")
            continue
        for name, t in report["tables"].items():
            print(
                f"{name}: {t['files_before']} files / {t['rows_before']:,} rows -> "
                f"{t['files_after']} files / {t['rows_after']:,} rows"
            )
    return EXIT_OK


def cmd_lake_gc(args: argparse.Namespace) -> int:
    from tessera.lake.compact import gc

    _, home = _load(args)
    home.require()
    result = gc(home.lake, timedelta(days=args.retention_days))
    print(
        f"Removed {result['files']} superseded files and {result['staging_folders']} "
        "abandoned staging folders"
    )
    return EXIT_OK


def cmd_ingest(args: argparse.Namespace) -> int:
    from tessera.connectors.base import IngestOptions
    from tessera.jobs.ingest import ingest, ingest_many

    config, home = _load(args)
    home.require()
    ids = list(args.ids)
    if args.ids_file:
        ids += [
            line.strip() for line in Path(args.ids_file).read_text().splitlines() if line.strip()
        ]
    options = IngestOptions(
        mode=args.mode,
        ids=ids,
        query=args.query,
        since=args.since,
        until=args.until,
        full=args.full,
        max_results=args.max_results,
    )
    engine = _engine(config)
    try:
        if args.source == "all":
            if ids or args.query or args.mode != "auto":
                raise TesseraError("`ingest all` takes no --ids, --query, or --mode.")
            results = ingest_many(config, home, engine, options=options)
        else:
            results = [ingest(config, home, engine, args.source, options)]
    finally:
        engine.dispose()
    for result in results:
        _print_result(result)
    statuses = {r.status for r in results}
    if statuses <= {"succeeded"}:
        return EXIT_OK
    return EXIT_ERROR if statuses == {"failed"} else EXIT_PARTIAL


def cmd_replay(args: argparse.Namespace) -> int:
    from tessera.jobs.ingest import replay

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = replay(config, home, engine, args.source, args.run_id)
    finally:
        engine.dispose()
    _print_result(result)
    return EXIT_OK


def cmd_runs(args: argparse.Namespace) -> int:
    from tessera.core.clock import isoformat
    from tessera.jobs.runs import recent_runs

    config, _ = _load(args)
    engine = _engine(config)
    try:
        rows = recent_runs(engine, args.source, args.limit)
    finally:
        engine.dispose()
    table(
        [
            (
                r["id"],
                r["source"],
                r["kind"],
                (r.get("params") or {}).get("mode", "-"),
                r["status"],
                isoformat(r["started_at"])[:19].replace("T", " "),
                (r.get("error") or "")[:60],
            )
            for r in rows
        ],
        headers=("run", "source", "kind", "mode", "status", "started (UTC)", "error"),
    )
    return EXIT_OK


def _print_resolve(result) -> None:  # type: ignore[no-untyped-def]
    counts = result.counts
    people = counts.get("people", {})
    discovery = counts.get("discovery", {})
    print(f"resolve: {result.status}  [{result.run_id}]")
    directory = counts.get("directory", {})
    print(
        f"  people: {directory.get('active', 0):,} active, {directory.get('former', 0):,} "
        f"former, {directory.get('candidate', 0):,} candidates awaiting review "
        f"({people.get('created', 0)} new from grants, {people.get('promoted', 0)} promoted by "
        f"affiliated papers, {discovery.get('candidates_created', 0)} newly discovered)"
    )
    if "authorship" in counts:
        a = counts["authorship"]
        stored = a.get("store", {})
        print(
            f"  authorship: {a['candidates']:,} candidates for {a['people']:,} people: "
            f"{a['strong']:,} strong, {a['possible']:,} possible, {a['unlikely']:,} unlikely "
            f"({stored.get('inserted', 0):,} new, {stored.get('updated', 0):,} updated)"
        )
    for predicate, stats in counts.get("links", {}).items():
        if stats:
            total = stats.get("inserted", 0) + stats.get("updated", 0) + stats.get("unchanged", 0)
            print(f"  {predicate}: {total:,} links ({stats.get('inserted', 0):,} new)")


def cmd_resolve(args: argparse.Namespace) -> int:
    from tessera.evidence.resolve import resolve

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = resolve(config, home, engine, people_only=args.people_only)
    finally:
        engine.dispose()
    _print_resolve(result)
    return EXIT_OK


def _print_build(result: dict) -> None:  # type: ignore[type-arg]
    docs = result.get("documents", {})
    print(
        f"search index: {docs.get('person', 0):,} people, {docs.get('publication', 0):,} "
        f"publications, {docs.get('grant', 0):,} grants, {docs.get('study', 0):,} studies, "
        f"{docs.get('candidate', 0):,} discovered people in {result.get('seconds', 0)}s"
    )


def cmd_build_search(args: argparse.Namespace) -> int:
    from tessera.jobs.build import build_search

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = build_search(config, home, engine)
    finally:
        engine.dispose()
    _print_build(result)
    return EXIT_OK


def cmd_sync(args: argparse.Namespace) -> int:
    from tessera.connectors.base import IngestOptions
    from tessera.jobs.sync import sync

    config, home = _load(args)
    home.require()
    engine = _engine(config)
    try:
        result = sync(
            config, home, engine, fetch=not args.no_fetch, options=IngestOptions(full=args.full)
        )
    finally:
        engine.dispose()
    for run in result.ingests:
        _print_result(run)
    _print_resolve(result.resolves[-1])
    _print_build(result.build)
    return {"succeeded": EXIT_OK, "partial": EXIT_PARTIAL}.get(result.status, EXIT_ERROR)


def cmd_people_import(args: argparse.Namespace) -> int:
    from tessera.evidence.people import import_roster

    config, _ = _load(args)
    engine = _engine(config)
    try:
        stats = import_roster(engine, args.path)
    finally:
        engine.dispose()
    print(f"roster: {stats.rows} rows, {stats.created} people created, {stats.matched} matched")
    for problem in stats.skipped[:10]:
        print(f"  skipped {problem}")
    return EXIT_OK if not stats.skipped else EXIT_PARTIAL


def cmd_people_list(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from tessera.ledger.tables import assertion, person

    config, _ = _load(args)
    engine = _engine(config)
    statuses = ("active", "former", "candidate") if args.status == "all" else (args.status,)
    papers = (
        select(assertion.c.subject_id, func.count().label("n"))
        .where(assertion.c.predicate == "authored", assertion.c.status == "accepted")
        .group_by(assertion.c.subject_id)
        .subquery()
    )
    suggested = (
        select(assertion.c.subject_id, func.count().label("n"))
        .where(
            assertion.c.predicate == "authored",
            assertion.c.status == "suggested",
            assertion.c.reported.is_(True),
        )
        .group_by(assertion.c.subject_id)
        .subquery()
    )
    query = (
        select(
            person.c.id,
            person.c.display_name,
            person.c.status,
            func.coalesce(papers.c.n, 0),
            func.coalesce(suggested.c.n, 0),
        )
        .outerjoin(papers, papers.c.subject_id == person.c.id)
        .outerjoin(suggested, suggested.c.subject_id == person.c.id)
        .where(person.c.status.in_(statuses))
        .order_by(person.c.display_name)
        .limit(args.limit)
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).all()
    finally:
        engine.dispose()
    table(rows, headers=("id", "name", "status", "accepted papers", "suggested"))
    return EXIT_OK


def cmd_eval_authorship(args: argparse.Namespace) -> int:
    from tessera.evidence.evaluate import evaluate, render_markdown, write_report

    config, home = _load(args)
    engine = _engine(config)
    try:
        report = evaluate(engine, config, args.gold)
    finally:
        engine.dispose()
    json_path, md_path = write_report(report, home.derived / "eval")
    print(render_markdown(report))
    print(f"Report written to {md_path} and {json_path}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    config, home = _load(args)
    home.require()
    host = args.host or config.server.host
    port = args.port or config.server.port
    for warning in config_warnings(config):
        log.warning("%s", warning)
    if args.host and args.host not in {"127.0.0.1", "localhost", "::1"}:
        log.warning(
            "Listening on %s: Tessera has no sign-in yet; anyone who can reach it can curate.", host
        )
    assert config.path is not None
    os.environ[ENV_CONFIG_PATH] = str(Path(config.path).resolve())
    print(f"{config.branding.product_name} running at http://{host}:{port}/  (Ctrl-C to stop)")
    uvicorn.run(
        "tessera.api.app:create_app_from_env",
        factory=True,
        host=host,
        port=port,
        reload=args.reload,
        reload_dirs=[str(Path(__file__).resolve().parents[1])] if args.reload else None,
        log_level="warning" if args.quiet else "info",
        server_header=False,
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
