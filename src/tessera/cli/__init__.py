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
    LOOPBACK_HOSTS,
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

    demo = commands.add_parser(
        "demo", help="build a fictional organization to explore (and to take screenshots of)"
    )
    demo.add_argument("path", nargs="?", default="demo", help="where to build it (default: ./demo)")
    demo.add_argument("--force", action="store_true", help="replace a demo already there")
    demo.add_argument("--seed", type=int, default=7, help="another seed, other people")
    demo.set_defaults(handler=cmd_demo)

    bench = commands.add_parser(
        "bench",
        help="measure Tessera on this machine with a simulated organization of any size",
    )
    bench.add_argument(
        "path", nargs="?", default="bench", help="where to build it, or reuse (default: ./bench)"
    )
    bench.add_argument("--people", type=int, default=1000, help="people (default 1,000)")
    bench.add_argument("--papers", type=int, default=50_000, help="papers (default 50,000)")
    bench.add_argument("--seed", type=int, default=7, help="another seed, another organization")
    bench.add_argument("--rebuild", action="store_true", help="build it again even if it is there")
    bench.add_argument(
        "--samples", type=int, default=30, help="requests timed per page (default 30)"
    )
    bench.set_defaults(handler=cmd_bench)

    doctor = commands.add_parser("doctor", help="check that this installation is ready")
    doctor.add_argument("--online", action="store_true", help="also test public API reachability")
    doctor.add_argument(
        "--production", action="store_true", help="also check what a server others use needs"
    )
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
    gc.add_argument(
        "--retention-days",
        type=float,
        default=7.0,
        metavar="DAYS",
        help="keep superseded files younger than this (default: 7)",
    )
    gc.set_defaults(handler=cmd_lake_gc)

    ingest = commands.add_parser(
        "ingest",
        help="fetch public data into the lake",
        description="Fetch from a public source. SOURCE is pubmed, icite, reporter, ctgov, "
        "orcid, or 'all' (every enabled source in a sensible order). Without --mode, an "
        "organization sweep is run (or an ID fetch if --ids is given); iCite and ORCID "
        "read what the lake and the directory need.",
    )
    ingest.add_argument("source", help="pubmed | icite | reporter | ctgov | orcid | all")
    ingest.add_argument(
        "--mode",
        default="auto",
        help="org | query | ids | linked, people, or citing (pubmed) | pi (reporter) | "
        "refresh (icite, orcid)",
    )
    ingest.add_argument("--ids", nargs="+", default=[], metavar="ID", help="IDs to fetch")
    ingest.add_argument("--ids-file", metavar="PATH", help="file with one ID per line")
    ingest.add_argument("--query", help="source query (PubMed syntax, Essie, or RePORTER JSON)")
    ingest.add_argument(
        "--since", type=_date_arg, metavar="YYYY-MM-DD", help="start of the window to fetch"
    )
    ingest.add_argument(
        "--until", type=_date_arg, metavar="YYYY-MM-DD", help="end of the window to fetch"
    )
    ingest.add_argument("--full", action="store_true", help="ignore the saved checkpoint")
    ingest.add_argument(
        "--max-results", type=int, metavar="N", help="stop after N records (for quick checks)"
    )
    ingest.set_defaults(handler=cmd_ingest)

    replay_cmd = commands.add_parser(
        "replay", help="rebuild lake records from a run's raw payloads"
    )
    replay_cmd.add_argument("source", help="pubmed | icite | reporter | ctgov | orcid")
    replay_cmd.add_argument("run_id", help="the run to replay (see `tessera runs`)")
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
    build_actions.add_parser(
        "discovery", help="concept profiles, similar people, and the collaboration graph"
    ).set_defaults(handler=cmd_build_discovery)
    build_actions.add_parser(
        "opportunities", help="who fits each open funding opportunity (after discovery)"
    ).set_defaults(handler=cmd_build_opportunities)

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
    importer.add_argument("path", type=Path, help="CSV file with a header row")
    importer.set_defaults(handler=cmd_people_import)
    people_public = people_actions.add_parser(
        "public", help="give people a profile on the public site (or take it away)"
    )
    people_public.add_argument("groups", nargs="+", metavar="person", help="IDs (per_…)")
    people_public.add_argument("--off", action="store_true", help="take the profiles away")
    people_public.set_defaults(handler=cmd_public, what="person")
    lister = people_actions.add_parser("list", help="list people")
    lister.add_argument("--status", default="active", help="active | former | candidate | all")
    lister.add_argument("--limit", type=int, default=50, help="at most this many (default: 50)")
    lister.set_defaults(handler=cmd_people_list)

    groups_cmd = commands.add_parser(
        "groups", help="groups: divisions, programs, labs, and other kinds"
    )
    groups_actions = groups_cmd.add_subparsers(
        dest="groups_command", metavar="ACTION", required=True
    )
    group_import = groups_actions.add_parser(
        "import",
        help="import groups or memberships from a CSV or JSON file",
        description="Import groups (a CSV file with a 'name' column) or memberships (a CSV "
        "file with a 'group' column), or both (a JSON file with 'groups' and 'memberships'). "
        "Rows carry the source's own identifiers, so importing the same source again updates "
        "what it made earlier. See docs/groups.md for the columns.",
    )
    group_import.add_argument("path", type=Path, help="CSV or JSON file")
    group_import.add_argument(
        "--source", required=True, help="a short name for where the file comes from, like hr"
    )
    group_import.add_argument(
        "--complete",
        action="store_true",
        help="the file is the source's whole list: archive its missing groups and end its "
        "missing current memberships today",
    )
    group_import.add_argument(
        "--dry-run", action="store_true", help="report what would change; change nothing"
    )
    group_import.set_defaults(handler=cmd_groups_import)
    groups_actions.add_parser(
        "attribute",
        help="suggest which papers are each group's own (also run by sync)",
    ).set_defaults(handler=cmd_groups_attribute)
    group_public = groups_actions.add_parser(
        "public", help="give groups a page on the public site (or take it away)"
    )
    group_public.add_argument("groups", nargs="+", help="addresses, codes, or IDs")
    group_public.add_argument("--off", action="store_true", help="take the pages away")
    group_public.set_defaults(handler=cmd_public, what="group")
    # The optional assistant: not listed in the help (it says nothing unless [ai] is enabled).
    ai_cmd = commands.add_parser("ai", description="The optional assistant ([ai] in the "
                                 "configuration).")  # fmt: skip
    commands._choices_actions = [a for a in commands._choices_actions if a.dest != "ai"]
    ai_actions = ai_cmd.add_subparsers(dest="ai_command", metavar="ACTION", required=True)
    ai_actions.add_parser("check", help="one round trip to the configured provider").set_defaults(
        handler=cmd_ai_check
    )
    opportunities_cmd = commands.add_parser(
        "opportunities", help="funding opportunities the organization tracks itself"
    )
    opportunity_actions = opportunities_cmd.add_subparsers(
        dest="opportunities_command", metavar="ACTION", required=True
    )
    opportunity_import = opportunity_actions.add_parser(
        "import",
        help="add or update opportunities from a CSV or JSON file",
        description="Import opportunities the organization tracks itself (foundation calls, "
        "internal pilot funds, limited submissions) from a CSV file with a header, or a JSON "
        "list. Columns: source, external_id, kind, number, title, sponsor, description, url, "
        "posted_on, closes_on, internal_deadline, award_floor, award_ceiling, "
        "limited_submission, eligibility_notes, applicant_types. Importing the same rows again "
        "updates them. Nothing is written if any row has a problem. Federal opportunities come "
        "from Grants.gov by themselves.",
    )
    opportunity_import.add_argument("path", type=Path, help="CSV or JSON file")
    opportunity_import.add_argument(
        "--source", help="who offers or lists them, for every row (instead of a source column)"
    )
    opportunity_import.add_argument(
        "--complete",
        action="store_true",
        help="the file is the source's whole list: archive its opportunities the file omits",
    )
    opportunity_import.add_argument(
        "--dry-run", action="store_true", help="report what would change; change nothing"
    )
    opportunity_import.set_defaults(handler=cmd_opportunities_import)
    group_list = groups_actions.add_parser("list", help="list groups")
    group_list.add_argument("--kind", help="only groups of this kind")
    group_list.add_argument("--all", action="store_true", help="include archived groups")
    group_list.set_defaults(handler=cmd_groups_list)

    digest_cmd = commands.add_parser(
        "digest", help="pending work for each account: groups' inboxes and review queues"
    )
    digest_actions = digest_cmd.add_subparsers(
        dest="digest_command", metavar="ACTION", required=True
    )
    digest_show = digest_actions.add_parser("show", help="print the digests")
    digest_show.add_argument("--account", help="only this account (username)")
    digest_show.set_defaults(handler=cmd_digest, send=False, dry_run=True)
    digest_send = digest_actions.add_parser(
        "send",
        help="email each account with pending work its digest ([mail]); run it weekly",
    )
    digest_send.add_argument("--account", help="only this account (username)")
    digest_send.add_argument(
        "--dry-run", action="store_true", help="print the messages instead of sending them"
    )
    digest_send.set_defaults(handler=cmd_digest, send=True)

    publish_cmd = commands.add_parser(
        "publish",
        help="write the public site: pages for the people and groups that opted in",
        description="Write a static public site from an allowlisted projection: a page for "
        "every person and group that opted in, with citations and links rather than "
        "abstracts. The site is built and checked (structure, accessibility, links, and "
        "colors) before it replaces the published one. Run it daily, after sync.",
    )
    publish_cmd.add_argument("--out", type=Path, help="where to write it ([publish] out)")
    publish_cmd.add_argument(
        "--check", action="store_true", help="build and check, but write nothing"
    )
    publish_cmd.set_defaults(handler=cmd_publish)

    backup_cmd = commands.add_parser(
        "backup",
        help="write one archive of the ledger, the lake, snapshots, and the configuration",
        description="Write a .tar.gz archive that `tessera restore` can load into an empty data "
        "home, on SQLite or PostgreSQL: the ledger as portable rows, the lake, report snapshots, "
        "and the configuration, each file with its SHA-256 digest. The search index, discovery "
        "files, and public site are rebuilt from these. Holds the job lock, so no sync runs "
        "meanwhile.",
    )
    backup_cmd.add_argument(
        "--out", type=Path, help="file or folder to write (default: <data home>/backups/)"
    )
    backup_cmd.add_argument(
        "--raw", action="store_true", help="include the raw responses from each source too"
    )
    backup_cmd.add_argument(
        "--verify", type=Path, metavar="ARCHIVE", help="check an archive instead of writing one"
    )
    backup_cmd.set_defaults(handler=cmd_backup)

    restore_cmd = commands.add_parser(
        "restore",
        help="load a backup into an empty data home and ledger",
        description="Check an archive, then load it into the configured data home and ledger, "
        "which must be empty (a new SQLite file or PostgreSQL database). The ledger is brought to "
        "the backup's schema, loaded, and upgraded to this version. Then run `tessera build`.",
    )
    restore_cmd.add_argument("archive", type=Path, help="the backup to restore")
    restore_cmd.set_defaults(handler=cmd_restore)

    export_cmd = commands.add_parser(
        "export",
        help="write a full exit export: every table as Parquet and CSV, with a data dictionary",
        description="Write everything Tessera knows, in open formats readable without it: every "
        "ledger table and every lake table as Parquet and CSV, report snapshots, a data "
        "dictionary, and a manifest with digests. Password hashes and sessions are left out.",
    )
    export_cmd.add_argument("out", type=Path, help="a new, empty folder to write")
    export_cmd.set_defaults(handler=cmd_export)

    alerts_cmd = commands.add_parser(
        "alerts",
        help="check for failed runs, stale sources, and old backups; email [alerts] if any",
        description="List what needs attention: runs that failed or partly failed in the last "
        "two days, sources that have gone stale, and backups older than [alerts] backup_days. "
        "With --send, email them to [alerts] email through [mail], once per new set of issues "
        "(and again after a week). `tessera sync` does this by itself when alerts are set up.",
    )
    alerts_cmd.add_argument("--send", action="store_true", help="email the issues, if any")
    alerts_cmd.add_argument(
        "--force", action="store_true", help="send even if the same issues were sent this week"
    )
    alerts_cmd.set_defaults(handler=cmd_alerts)

    report_cmd = commands.add_parser(
        "report",
        help="Insight reports: list them, freeze snapshots, verify and export them",
        description="Reports are named sets of Insight figures over a cohort and a period. A "
        "snapshot freezes one with the exact inputs its figures were computed from, so it can "
        "be verified and exported long after the data has moved on.",
    )
    report_actions = report_cmd.add_subparsers(
        dest="report_command", metavar="ACTION", required=True
    )
    report_list = report_actions.add_parser("list", help="reports and their snapshots")
    report_list.set_defaults(handler=cmd_report_list)
    report_snap = report_actions.add_parser("snapshot", help="freeze a report now")
    report_snap.add_argument("report", help="the report's name or ID")
    report_snap.add_argument("--note", help="why it was taken")
    report_snap.set_defaults(handler=cmd_report_snapshot)
    report_verify = report_actions.add_parser(
        "verify", help="compute a snapshot again from its frozen inputs and compare"
    )
    report_verify.add_argument("snapshot", help="the snapshot's ID")
    report_verify.set_defaults(handler=cmd_report_verify)
    report_export = report_actions.add_parser(
        "export", help="write a snapshot's figures, records, and definitions to a file"
    )
    report_export.add_argument("snapshot", help="the snapshot's ID")
    report_export.add_argument(
        "--format", choices=("csv", "parquet", "json"), default="csv", help="(default: csv)"
    )
    report_export.add_argument("--out", type=Path, help="the file to write (default: its name)")
    report_export.set_defaults(handler=cmd_report_export)

    users_cmd = commands.add_parser("users", help="local accounts and their roles")
    users_actions = users_cmd.add_subparsers(dest="users_command", metavar="ACTION", required=True)
    role_help = (
        "admin, curator, manager, researcher, or viewer; a curator for some people only is "
        "curator=group:<name, address, or code> or curator=person:<ID>; a manager is "
        "manager=group:<…>, or manager=group:<…>+below to manage the groups below too "
        "(repeatable)"
    )
    adder = users_actions.add_parser("add", help="create an account (prompts for a password)")
    adder.add_argument("username", help="lowercase letters, digits, dots, dashes, underscores")
    adder.add_argument("--name", help="the name shown and recorded on decisions")
    adder.add_argument("--role", action="append", required=True, metavar="ROLE", help=role_help)
    adder.add_argument("--email", help="contact address")
    adder.add_argument("--person", metavar="ID", help="the person a researcher account belongs to")
    adder.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    adder.set_defaults(handler=cmd_users_add)
    users_actions.add_parser("list", help="list accounts and roles").set_defaults(
        handler=cmd_users_list
    )
    for action, text in (("disable", "sign an account out and block it"), ("enable", "unblock")):
        sub = users_actions.add_parser(action, help=text)
        sub.add_argument("username")
        sub.set_defaults(
            handler=cmd_users_status, status="disabled" if action == "disable" else "active"
        )
    resetter = users_actions.add_parser(
        "reset-password", help="set a new password (signs the account out everywhere)"
    )
    resetter.add_argument("username")
    resetter.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    resetter.set_defaults(handler=cmd_users_password)
    roles = users_actions.add_parser("roles", help="replace an account's roles")
    roles.add_argument("username")
    roles.add_argument("--role", action="append", required=True, metavar="ROLE", help=role_help)
    roles.set_defaults(handler=cmd_users_roles)

    evidence_cmd = commands.add_parser(
        "evidence", help="the authorship model: learned weights and auto-accept"
    )
    evidence_actions = evidence_cmd.add_subparsers(
        dest="evidence_command", metavar="ACTION", required=True
    )
    evidence_actions.add_parser(
        "status", help="the active model, the latest evaluation, and auto-accept"
    ).set_defaults(handler=cmd_evidence_status)
    evidence_actions.add_parser(
        "evaluate", help="fit learned weights to decisions and compare on held-out people"
    ).set_defaults(handler=cmd_evidence_evaluate)
    evidence_actions.add_parser(
        "release", help="make the evaluated learned model active (if it was better)"
    ).set_defaults(handler=cmd_evidence_release)
    evidence_actions.add_parser("revert", help="go back to the hand-set weights").set_defaults(
        handler=cmd_evidence_revert
    )
    auto = evidence_actions.add_parser(
        "auto-accept", help="accept strong suggestions automatically, where precision allows"
    )
    auto_mode = auto.add_mutually_exclusive_group(required=True)
    auto_mode.add_argument(
        "--threshold", type=float, help="switch on at this score (one the evaluation measured)"
    )
    auto_mode.add_argument("--off", action="store_true", help="switch off")
    auto_mode.add_argument("--undo", metavar="BATCH", help="undo one automatic batch")
    auto.set_defaults(handler=cmd_evidence_auto_accept)

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
    runs_cmd.add_argument("--source", help="only this source")
    runs_cmd.add_argument("--limit", type=int, default=20, help="at most this many (default: 20)")
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


def cmd_bench(args: argparse.Namespace) -> int:
    from tessera.bench.run import run

    if args.people < 10 or args.papers < 100:
        print("Use at least 10 people and 100 papers.", file=sys.stderr)
        return EXIT_ERROR
    run(Path(args.path), people=args.people, papers=args.papers, seed=args.seed,
        rebuild=args.rebuild, samples=args.samples,
        say=lambda line: print(line, flush=True))  # fmt: skip
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    from tessera.demo import DemoError, build_demo

    target = Path(args.path)
    print(f"Building the demo organization in {target} …")
    try:
        result = build_demo(target, seed=args.seed, force=args.force)
    except DemoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(
        f"{result['people']:,} people, {result['papers']:,} publications, "
        f"{result['grants']:,} grants, and {result['studies']:,} studies; "
        f"{result['accepted']:,} links confirmed and {result['rejected']:,} rejected by the "
        f"demo curator, {result['merged']:,} duplicate records merged."
    )
    print("Everyone and everything in it is invented.")
    if result.get("public_site"):
        print(f"Its public site: {result['public_site']}")
    print(f"Next: tessera --config {result['config']} serve")
    return EXIT_OK


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

    checks = run_checks(args.config, online=args.online, production=args.production)
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
        ids = a.get("identifiers") or {}
        if ids.get("proposed") or ids.get("withdrawn"):
            print(
                f"  ORCID iDs suggested from people's papers: {ids.get('proposed', 0):,} new, "
                f"{ids.get('withdrawn', 0):,} withdrawn (confirm them on profiles)"
            )
    auto = counts.get("auto_accept") or {}
    if auto.get("accepted"):
        print(f"  auto-accepted: {auto['accepted']:,} strong suggestions (batch {auto['batch']})")
    dup = counts.get("duplicates") or {}
    if dup.get("pairs"):
        print(
            f"  possible duplicates: {dup['pairs']:,} pairs ({dup.get('strong', 0):,} strong); "
            "review them under Curate"
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


def cmd_build_discovery(args: argparse.Namespace) -> int:
    from tessera.jobs.build import build_discovery

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = build_discovery(config, home, engine)
    finally:
        engine.dispose()
    print(
        f"concept profiles: {result['people']:,} people, {result['concepts']:,} concepts, "
        f"{result['pairs']:,} similar pairs (from {result['papers']:,} papers, "
        f"{result['confirmed_papers']:,} confirmed)"
    )
    print(
        f"collaboration graph: {result['collaborations']:,} collaborating pairs in "
        f"{result['groups']:,} groups, {result['bridges']:,} people bridging them; "
        f"concept landscape: {result['concept_pairs']:,} linked concepts"
    )
    return EXIT_OK


def cmd_build_opportunities(args: argparse.Namespace) -> int:
    from tessera.jobs.build import build_opportunities

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = build_opportunities(config, home, engine)
    finally:
        engine.dispose()
    if not result.get("ready"):
        print("nothing to read yet: build search and discovery first")
        return EXIT_PARTIAL
    print(f"opportunity fit: {result['opportunities']:,} open opportunities read in "
          f"{result['seconds']:.1f}s; {result['indexed']:,} indexed")  # fmt: skip
    return EXIT_OK


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
    _alert_after_sync(config, home)
    return {"succeeded": EXIT_OK, "partial": EXIT_PARTIAL}.get(result.status, EXIT_ERROR)


def _alert_after_sync(config, home) -> None:  # type: ignore[no-untyped-def]
    """Email [alerts] about new issues, when alerts are set up; never fail the sync for it."""
    if not config.alerts.email or not config.mail.host:
        return
    from tessera.ops.status import alert

    engine = _engine(config)
    try:
        found, _, sent = alert(config, engine, home)
        if sent:
            print(f"alerts: sent {len(found)} issues to {', '.join(config.alerts.email)}")
    except Exception as exc:  # the sync's own result matters more than the alert
        log.warning("Could not send alerts: %s", exc)
    finally:
        engine.dispose()


def cmd_people_import(args: argparse.Namespace) -> int:
    from tessera.evidence.people import import_roster

    config, _ = _load(args)
    engine = _engine(config)
    try:
        stats = import_roster(engine, args.path, vocabulary=config.vocabulary)
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


def cmd_groups_import(args: argparse.Namespace) -> int:
    from tessera.groups.importer import import_groups, read_file
    from tessera.search import update

    config, home = _load(args)
    engine = _engine(config)
    try:
        stats = import_groups(
            engine,
            read_file(args.path),
            source=args.source,
            vocabulary=config.vocabulary,
            complete=args.complete,
            dry_run=args.dry_run,
        )
        if not args.dry_run and stats.people:
            update.apply(config, home, engine, people=stats.people)
    finally:
        engine.dispose()

    def counts(c: dict[str, int]) -> str:
        return ", ".join(f"{n} {k}" for k, n in sorted(c.items())) or "none"

    prefix = "would import" if args.dry_run else "imported"
    print(f"{stats.source}: {prefix} groups ({counts(stats.groups)}); "
          f"memberships ({counts(stats.memberships)})")  # fmt: skip
    for problem in stats.skipped[:20]:
        print(f"  skipped {problem}")
    if len(stats.skipped) > 20:
        print(f"  … and {len(stats.skipped) - 20} more skipped")
    return EXIT_OK if not stats.skipped else EXIT_PARTIAL


def cmd_ai_check(args: argparse.Namespace) -> int:
    import time

    from tessera.ai.providers import ProviderError, make

    config, _ = _load(args)
    if not config.ai.enabled:
        print("The assistant is not enabled ([ai] enabled = false).")
        return EXIT_ERROR
    started = time.monotonic()
    tools = [
        {
            "name": "echo",
            "description": "Repeat a word.",
            "parameters": {
                "type": "object",
                "properties": {"word": {"type": "string"}},
                "required": ["word"],
            },
        }
    ]
    try:
        provider = make(config.ai)
        try:
            reply = provider.complete("Reply in one word.", [{"role": "user", "text": "Ready?"}])
            tool = provider.complete("Use the echo tool with the word ready.",
                                     [{"role": "user", "text": "Go."}], tools)  # fmt: skip
        finally:
            provider.close()
    except ProviderError as exc:
        print(f"{config.ai.provider} · {config.ai.model}: {exc}")
        return EXIT_ERROR
    seconds = time.monotonic() - started
    print(f"{config.ai.provider} · {config.ai.model} at {config.ai.url}: answered "
          f"“{reply.text.strip()[:60]}” and {'called' if tool.tool_calls else 'did not call'} "
          f"a tool, in {seconds:.1f}s")  # fmt: skip
    return EXIT_OK if tool.tool_calls else EXIT_PARTIAL


def cmd_opportunities_import(args: argparse.Namespace) -> int:
    from tessera.opportunities import catalog
    from tessera.opportunities.store import import_rows, read_rows
    from tessera.search.update import refresh_opportunities

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = import_rows(engine, read_rows(args.path), f"{config.server.operator} (import)",
                             source=args.source, complete=args.complete,
                             dry_run=args.dry_run)  # fmt: skip
        if not args.dry_run and not result["problems"]:
            from tessera.lake import Lake

            with Lake(home.lake) as lake:
                refs = [v["id"] for v in catalog.everything(engine, lake)
                        if v["source"] == catalog.LOCAL]  # fmt: skip
            refresh_opportunities(config, home, engine, refs)
    finally:
        engine.dispose()
    if result["problems"]:
        print("nothing imported:")
        for problem in result["problems"][:20]:
            print(f"  {problem}")
        return EXIT_PARTIAL
    verb = "would import" if args.dry_run else "imported"
    print(f"{verb}: {result['created']} new, {result['updated']} updated, "
          f"{result['unchanged']} unchanged, {result['archived']} archived")  # fmt: skip
    return EXIT_OK


def cmd_groups_attribute(args: argparse.Namespace) -> int:
    from tessera.jobs.build import build_attribution

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = build_attribution(config, home, engine)
    finally:
        engine.dispose()
    print(
        f"attribution: {result['groups']} groups, {result['suggestions']} papers suggested "
        f"({result['inserted']} new, {result['unreported']} no longer supported)"
    )
    return EXIT_OK


def cmd_digest(args: argparse.Namespace) -> int:
    from tessera.auth.accounts import grants_of, list_accounts
    from tessera.auth.principal import Principal
    from tessera.groups.inbox import digest, digest_text
    from tessera.mail import message, send

    config, home = _load(args)
    engine = _engine(config)
    outgoing = []
    try:
        if config.server.accounts:
            accounts = [
                a
                for a in list_accounts(engine)
                if a["status"] == "active" and (not args.account or a["username"] == args.account)
            ]
            if args.account and not accounts:
                raise TesseraError(f"No active account {args.account!r}.")
            principals = []
            with engine.connect() as conn:
                for a in accounts:
                    principals.append(
                        (
                            a,
                            Principal(
                                name=a["display_name"],
                                account_id=a["id"],
                                username=a["username"],
                                person_id=a["person_id"],
                                grants=tuple(grants_of(conn, a["id"])),
                            ),
                        )
                    )
        else:
            operator = config.server.operator
            principals = [({"username": operator, "email": None, "display_name": operator},
                           Principal(name=operator, single_operator=True))]  # fmt: skip
        for account, principal in principals:
            result = digest(engine, principal, config, home=home)
            if not result["total"]:
                continue
            subject, text = digest_text(result, config, account["display_name"])
            if args.send and not args.dry_run:
                if account.get("email"):
                    outgoing.append(message(config.mail, account["email"], subject, text))
                continue
            to = account.get("email") or "(no email address)"
            print(f"To: {account['username']} <{to}>\nSubject: {subject}\n\n{text}")
    finally:
        engine.dispose()
    if outgoing:
        sent = send(config.mail, outgoing)
        print(f"digest: sent {sent} {'message' if sent == 1 else 'messages'}")
    elif args.send and not args.dry_run:
        print("digest: nothing to send")
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    from tessera.publish import publish

    config, home = _load(args)
    engine = _engine(config)
    try:
        result = publish(config, home, engine, out=args.out, check_only=args.check)
    finally:
        engine.dispose()
    for problem in result.problems[:30]:
        print(f"  problem: {problem}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.problems:
        print(f"publish: {len(result.problems)} problems; the published site was not changed")
        return EXIT_ERROR
    verb = "would write" if args.check else "wrote"
    print(
        f"publish: {verb} {result.pages} pages ({result.groups} groups, {result.people} people)"
        + ("" if args.check else f" to {result.out}")
    )
    return EXIT_OK


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return str(n)  # pragma: no cover


def cmd_backup(args: argparse.Namespace) -> int:
    from tessera.ops import backup

    if args.verify:
        result = backup.verify_backup(args.verify)
        for problem in result.problems[:30]:
            print(f"  problem: {problem}")
        manifest = result.manifest
        if result.ok:
            rows = sum(manifest["ledger"]["tables"].values())
            print(f"backup: {args.verify} is intact: {len(manifest['files'])} files, {rows:,} "
                  f"ledger rows at schema {manifest['ledger']['revision']}, taken "
                  f"{manifest['created_at']} by Tessera {manifest['tessera_version']}")  # fmt: skip
            return EXIT_OK
        print(f"backup: {args.verify} does not match its manifest")
        return EXIT_ERROR
    config, home = _load(args)
    home.require()
    engine = _engine(config)
    try:
        result = backup.create_backup(config, home, engine, out=args.out, raw=args.raw)
    finally:
        engine.dispose()
    rows = sum(result.tables.values())
    print(f"backup: wrote {result.path} ({_size(result.bytes)}; {rows:,} ledger rows in "
          f"{len(result.tables)} tables, {result.files} files)")  # fmt: skip
    return EXIT_OK


def cmd_restore(args: argparse.Namespace) -> int:
    from tessera.ledger.db import make_engine
    from tessera.ops import backup

    config, home = _load(args)
    home.create()
    engine = make_engine(config.ledger_url)
    try:
        result = backup.restore_backup(config, home, engine, args.archive)
    finally:
        engine.dispose()
    rows = sum(result.tables.values())
    upgraded = f", upgraded to schema {result.upgraded_to}" if result.upgraded_to else ""
    print(f"restore: loaded {rows:,} ledger rows at schema {result.revision}{upgraded} and "
          f"{result.files} files into {home.root}")  # fmt: skip
    print("Next: `tessera build search` and `tessera build discovery` to rebuild what is "
          "computed, and `tessera publish` if you publish a public site.")  # fmt: skip
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    from tessera.ops.exit import write_exit

    config, home = _load(args)
    home.require()
    engine = _engine(config)
    try:
        result = write_exit(config, home, engine, args.out)
    finally:
        engine.dispose()
    print(f"export: wrote {result.files} files ({_size(result.bytes)}) to {result.out}: "
          f"{len(result.ledger)} ledger tables ({sum(result.ledger.values()):,} rows) and "
          f"{len(result.lake)} lake tables ({sum(result.lake.values()):,} rows)")  # fmt: skip
    return EXIT_OK


def cmd_alerts(args: argparse.Namespace) -> int:
    from tessera.ops.status import alert, issues

    config, home = _load(args)
    engine = _engine(config)
    try:
        if not args.send:
            found = issues(config, home, engine)
            sent = False
        else:
            if not config.alerts.email or not config.mail.host:
                print("alerts: set [alerts] email and [mail] host to send alerts")
                return EXIT_ERROR
            found, _, sent = alert(config, engine, home, force=args.force)
    finally:
        engine.dispose()
    for issue in found:
        print(f"  {issue['kind']}: {issue['text']}")
    if not found:
        print("alerts: nothing needs attention")
        return EXIT_OK
    if args.send:
        print(f"alerts: {len(found)} issues; " + ("sent to " + ", ".join(config.alerts.email)
              if sent else "already sent this week (--force to send again)"))  # fmt: skip
    else:
        print(f"alerts: {len(found)} issues")
    return EXIT_PARTIAL


def cmd_report_list(args: argparse.Namespace) -> int:
    from tessera.insight import reports

    config, _ = _load(args)
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            rows = reports.list_reports(conn)
            snapshots = reports.list_snapshots(conn)
    finally:
        engine.dispose()
    if not rows and not snapshots:
        print("No reports yet. Save one from any Insight page.")
        return EXIT_OK
    table(
        [(r["id"], r["name"], r["cohort"], len(r["figures"]), r["snapshots"]) for r in rows],
        headers=("ID", "Report", "Cohort", "Figures", "Snapshots"),
    )
    if snapshots:
        print()
        table(
            [(s["id"], s["name"], s["taken_at"].strftime("%Y-%m-%d %H:%M"), s["taken_by"],
              s["note"] or "") for s in snapshots[:30]],
            headers=("Snapshot", "Report", "Taken (UTC)", "By", "Note"),
        )  # fmt: skip
    return EXIT_OK


def cmd_report_snapshot(args: argparse.Namespace) -> int:
    from tessera.insight import reports
    from tessera.lake import Lake
    from tessera.search.index import index_path
    from tessera.search.query import SearchIndex

    config, home = _load(args)
    engine = _engine(config)
    path = index_path(home)
    try:
        with engine.connect() as conn:
            row = reports.find_report(conn, args.report)
        with Lake(home.lake) as lake:
            snapshot_id = reports.take_snapshot(
                engine, lake, config, home, row["id"], actor=config.server.operator,
                note=args.note, search=SearchIndex(path) if path.is_file() else None,
            )  # fmt: skip
    finally:
        engine.dispose()
    print(f"report: froze {row['name']!r} as {snapshot_id}")
    return EXIT_OK


def cmd_report_verify(args: argparse.Namespace) -> int:
    from tessera.insight import reports

    config, home = _load(args)
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            row = reports.get_snapshot(conn, args.snapshot)
    finally:
        engine.dispose()
    result = reports.verify_snapshot(home, row)
    for problem in result["problems"]:
        print(f"  problem: {problem}")
    for figure in result["figures"]:
        mark = "same" if figure["same"] else "DIFFERENT"
        print(f"  {mark:9} {figure['metric']}  {figure.get('problem', '')}".rstrip())
    if result["same"]:
        print(f"report: {row['id']} computes again exactly ({len(result['figures'])} figures)")
        return EXIT_OK
    print(f"report: {row['id']} does not compute again as it was")
    return EXIT_ERROR


def cmd_report_export(args: argparse.Namespace) -> int:
    from tessera.insight import export, reports

    config, home = _load(args)
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            row = reports.get_snapshot(conn, args.snapshot)
    finally:
        engine.dispose()
    manifest, figures = reports.load_snapshot(home, row)
    content, _, name = export.snapshot_export(manifest, figures, args.format)
    out = args.out or Path(name)
    out.write_bytes(content)
    print(f"report: wrote {out} ({len(content):,} bytes, {len(figures)} figures)")
    return EXIT_OK


def cmd_public(args: argparse.Namespace) -> int:
    from sqlalchemy import update

    from tessera.groups import store as groups
    from tessera.ledger.tables import person, unit

    config, _ = _load(args)
    engine = _engine(config)
    changed = 0
    try:
        with engine.begin() as conn:
            for ref in args.groups:
                if args.what == "group":
                    gid = groups.resolve(conn, ref)["id"]
                    query = update(unit).where(unit.c.id == gid)
                else:
                    query = update(person).where(person.c.id == ref)
                changed += conn.execute(query.values(public=not args.off)).rowcount or 0
    finally:
        engine.dispose()
    if changed < len(args.groups):
        print(f"{args.what}: {len(args.groups) - changed} not found")
        return EXIT_PARTIAL
    state = "off" if args.off else "on"
    print(f"{args.what}: public pages {state} for {changed}")
    return EXIT_OK


def cmd_groups_list(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from tessera.groups import store as groups
    from tessera.ledger.tables import unit

    config, _ = _load(args)
    engine = _engine(config)
    statuses = ("active", "archived") if args.all else ("active",)
    query = select(unit).where(unit.c.status.in_(statuses)).order_by(unit.c.kind, unit.c.name)
    if args.kind:
        query = query.where(unit.c.kind == args.kind)
    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query).mappings()]
            counts = groups.member_counts(conn)
            parents = groups.parents_of(conn, [r["id"] for r in rows])
    finally:
        engine.dispose()
    table(
        [
            (
                r["slug"],
                r["name"],
                config.vocabulary.kind_label(r["kind"]),
                "; ".join(p["name"] for p in parents.get(r["id"], [])),
                counts.get(r["id"], {}).get("current", 0),
                r["source"] + (f":{r['external_id']}" if r["external_id"] else ""),
                r["status"],
            )
            for r in rows
        ],
        headers=("address", "name", "kind", "within", "members", "source", "status"),
    )
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
    if not config.server.accounts and host not in LOOPBACK_HOSTS:
        raise TesseraError(
            f"Without accounts, Tessera listens on this machine only, not on {host}. Set "
            "server.accounts = true and create an admin with `tessera users add`."
        )
    if config.server.accounts:
        from tessera.auth.accounts import active_admins

        engine = _engine(config)
        try:
            admins = active_admins(engine)
        finally:
            engine.dispose()
        if not admins:
            raise TesseraError(
                "Accounts are on, but no admin can sign in. Create one first: "
                "tessera users add <username> --role admin"
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


# --- users ---------------------------------------------------------------------------------------


def _read_password(from_stdin: bool) -> str:
    import getpass

    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password (at least 12 characters): ")
    if getpass.getpass("Again: ") != first:
        raise TesseraError("The two passwords differ.")
    return first


def _parse_roles(conn, specs: list[str]):  # type: ignore[no-untyped-def]
    from tessera.auth.accounts import make_grant

    grants = []
    for spec in specs:
        role, _, scope = spec.partition("=")
        grants.append(make_grant(conn, role, scope.strip() or None))
    return grants


def _describe_roles(grants, units: dict[str, str]) -> str:  # type: ignore[no-untyped-def]
    out = []
    for g in grants:
        if g.scope_type == "unit":
            below = " and below" if g.include_below else ""
            out.append(f"{g.role} ({units.get(g.scope_id, g.scope_id)}{below})")
        elif g.scope_type == "person":
            out.append(f"{g.role} ({g.scope_id})")
        else:
            out.append(g.role)
    return ", ".join(out)


def cmd_users_add(args: argparse.Namespace) -> int:
    from tessera.auth import accounts

    config, _ = _load(args)
    engine = _engine(config)
    try:
        with engine.connect() as conn:
            grants = _parse_roles(conn, args.role)
        password = _read_password(args.password_stdin)
        account_id = accounts.create_account(
            engine,
            args.username,
            args.name or args.username,
            password,
            grants,
            actor="command line",
            email=args.email,
            person_id=args.person,
        )
    finally:
        engine.dispose()
    print(f"Created {args.username.lower()} ({account_id}).")
    if not config.server.accounts:
        print(
            "Accounts are off: set server.accounts = true in the configuration to require sign-in."
        )
    return EXIT_OK


def cmd_users_list(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from tessera.auth import accounts
    from tessera.ledger.tables import unit

    config, _ = _load(args)
    engine = _engine(config)
    try:
        rows = accounts.list_accounts(engine)
        with engine.connect() as conn:
            units = dict(conn.execute(select(unit.c.id, unit.c.name)).all())
    finally:
        engine.dispose()
    if not rows:
        print("No accounts yet. Create an admin: tessera users add <username> --role admin")
        return EXIT_OK
    for r in rows:
        seen = r["last_sign_in_at"].date().isoformat() if r["last_sign_in_at"] else "never"
        print(
            f"{r['username']:<20} {r['status']:<9} {_describe_roles(r['grants'], units):<40} "
            f"{r['display_name']} · last sign-in {seen}"
        )
    return EXIT_OK


def _account_or_fail(engine, username: str):  # type: ignore[no-untyped-def]
    from tessera.auth import accounts

    row = accounts.find_account(engine, username)
    if row is None:
        raise TesseraError(f"No account {username!r}.")
    return row


def cmd_users_status(args: argparse.Namespace) -> int:
    from tessera.auth import accounts

    config, _ = _load(args)
    engine = _engine(config)
    try:
        row = _account_or_fail(engine, args.username)
        accounts.set_status(engine, row["id"], args.status)
    finally:
        engine.dispose()
    print(f"{row['username']} is now {args.status}.")
    return EXIT_OK


def cmd_users_password(args: argparse.Namespace) -> int:
    from tessera.auth import accounts

    config, _ = _load(args)
    engine = _engine(config)
    try:
        row = _account_or_fail(engine, args.username)
        accounts.set_password(engine, row["id"], _read_password(args.password_stdin))
    finally:
        engine.dispose()
    print(f"Set a new password for {row['username']}; their sessions were signed out.")
    return EXIT_OK


def cmd_users_roles(args: argparse.Namespace) -> int:
    from tessera.auth import accounts

    config, _ = _load(args)
    engine = _engine(config)
    try:
        row = _account_or_fail(engine, args.username)
        with engine.connect() as conn:
            grants = _parse_roles(conn, args.role)
        accounts.update_account(engine, row["id"], actor="command line", grants=grants)
    finally:
        engine.dispose()
    print(f"{row['username']}: {', '.join(g.role for g in grants)}.")
    return EXIT_OK


# --- evidence ----------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _print_measures(label: str, m: dict | None) -> None:  # type: ignore[type-arg]
    if not m:
        return
    print(
        f"  {label:<10} log loss {m['log_loss']:.4f} · strong precision "
        f"{_pct(m['strong']['precision'])}, recall {_pct(m['strong']['recall'])} · review "
        f"precision {_pct(m['review']['precision'])}, recall {_pct(m['review']['recall'])}"
    )


def cmd_evidence_status(args: argparse.Namespace) -> int:
    from tessera.evidence import auto_accept
    from tessera.evidence.model import EVALUATION_SETTING, active_model, read_setting

    config, _ = _load(args)
    engine = _engine(config)
    try:
        model = active_model(engine)
        report = read_setting(engine, EVALUATION_SETTING)
        state = auto_accept.status(engine)
        recent = auto_accept.batches(engine, limit=5)
    finally:
        engine.dispose()
    print(f"Active model: {model.method}" + (" (hand-set)" if model.is_base else ""))
    for feature, factor in sorted(model.multipliers.items()):
        if abs(factor - 1) >= 0.05:
            print(f"  {feature}: {factor:.2f} times the hand-set weight")
    if report:
        d = report["decisions"]
        print(
            f"Latest evaluation {report['generated_at']}: {d['examples']} decisions "
            f"({d['negatives']} rejections) about {d['people']} people"
        )
        _print_measures("hand-set", report.get("base"))
        _print_measures("learned", report.get("fitted"))
        print(
            "  "
            + (
                "The learned model is better: run `tessera evidence release`."
                if report["better"]
                else "Not released: " + " ".join(report["reasons"])
            )
        )
    else:
        print("No evaluation yet: run `tessera evidence evaluate`.")
    if state["enabled"]:
        print(
            f"Auto-accept: on at {state['threshold']} (evaluated precision "
            f"{_pct(state['precision'])} over {state['support']} decisions)"
        )
    else:
        why = f" ({state['disabled_because']})" if state.get("disabled_because") else ""
        print(f"Auto-accept: off{why}")
    for b in recent:
        undone = f", undone by {b['undone_by']}" if b["undone_at"] else ""
        print(
            f"  batch {b['id']}: {b['accepted']} accepted {b['created_at']:%Y-%m-%d %H:%M}{undone}"
        )
    return EXIT_OK


def cmd_evidence_evaluate(args: argparse.Namespace) -> int:
    from tessera.evidence.model import evaluate

    config, _ = _load(args)
    engine = _engine(config)
    try:
        report = evaluate(engine, config)
    finally:
        engine.dispose()
    d = report["decisions"]
    print(f"{d['examples']} decisions ({d['negatives']} rejections) about {d['people']} people")
    _print_measures("hand-set", report.get("base"))
    _print_measures("learned", report.get("fitted"))
    if report["better"]:
        print(
            "On held-out people the learned model is better. "
            "Release it with `tessera evidence release`."
        )
    else:
        print("Not offered: " + " ".join(report["reasons"]))
    for point in (report.get("fitted") or report.get("base") or {}).get("curve", []):
        print(
            f"  at {point['threshold']:<6} precision {_pct(point['precision'])} over "
            f"{point['support']} decisions"
        )
    return EXIT_OK


def cmd_evidence_release(args: argparse.Namespace) -> int:
    from tessera.evidence.model import release

    config, _ = _load(args)
    engine = _engine(config)
    try:
        model = release(engine, "command line")
    finally:
        engine.dispose()
    print(
        f"Released {model.method}; scores change at the next resolve or sync. Auto-accept is off."
    )
    return EXIT_OK


def cmd_evidence_revert(args: argparse.Namespace) -> int:
    from tessera.evidence.model import revert

    config, _ = _load(args)
    engine = _engine(config)
    try:
        revert(engine, "command line")
    finally:
        engine.dispose()
    print("Back to the hand-set weights from the next resolve or sync. Auto-accept is off.")
    return EXIT_OK


def cmd_evidence_auto_accept(args: argparse.Namespace) -> int:
    from tessera.evidence import auto_accept

    config, _ = _load(args)
    engine = _engine(config)
    try:
        if args.off:
            auto_accept.disable(engine, "command line")
            print("Auto-accept is off.")
        elif args.undo:
            count = auto_accept.undo_batch(engine, args.undo, "command line")
            print(f"Returned {count} links to suggestions.")
        else:
            state = auto_accept.enable(engine, args.threshold, "command line")
            print(
                f"Auto-accept is on at {state['threshold']} (evaluated precision "
                f"{_pct(state['precision'])}); it runs at the next resolve or sync."
            )
    finally:
        engine.dispose()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
