"""The ``tessera`` command-line interface.

Exit codes: 0 success, 1 error, 2 partial success.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import timedelta
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
