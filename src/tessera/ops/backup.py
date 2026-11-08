"""Backup and restore (PLT-08).

A backup is one ``.tar.gz`` archive:

* ``manifest.json``: the format, Tessera's version, the ledger's schema revision and row counts,
  and every other file with its size and SHA-256 digest;
* ``ledger/<table>.jsonl``: every ledger table as JSON lines, in an order that keeps references
  intact. Portable rows rather than a database file, so a backup taken on SQLite restores into
  PostgreSQL and the other way round;
* ``lake/``: the lake's Parquet files and manifests (what was fetched);
* ``snapshots/``: frozen report snapshots;
* ``config/tessera.toml``: the configuration (secrets stay in the environment);
* ``raw/``: the raw responses from each source, only when asked (``--raw``).

The search index, discovery files, and public site are not included: ``tessera build`` and
``tessera publish`` make them again from the ledger and the lake.

A backup is taken under the data home's job lock, so no sync writes while it reads, and the
ledger is read in one transaction. A restore goes into an empty data home and an empty ledger:
the ledger is brought to the backup's schema revision, the rows are loaded, and the schema is
upgraded to this version of Tessera.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, MetaData, func, insert, select, text, update
from sqlalchemy.engine import Engine

from tessera import __version__
from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.home import DataHome
from tessera.jobs.lock import job_lock
from tessera.ledger import migrate
from tessera.ledger.tables import metadata, setting

FORMAT = "tessera-backup/1"
BACKUPS = "backups"
LAST_BACKUP = "operations.last_backup"
CHUNK = 1 << 20


class BackupError(TesseraError):
    """A backup that cannot be made, trusted, or restored."""


@dataclass
class BackupResult:
    path: Path
    bytes: int
    tables: dict[str, int]
    files: int


@dataclass
class VerifyResult:
    path: Path
    manifest: dict[str, Any]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class RestoreResult:
    tables: dict[str, int]
    files: int
    revision: str
    upgraded_to: str | None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return isoformat(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot store {type(value).__name__} in a backup")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree(root: Path) -> Iterator[Path]:
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.startswith(".") and ".partial" not in path.name:
                yield path


def default_out(home: DataHome) -> Path:
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return home.root / BACKUPS / f"tessera-backup-{stamp}.tar.gz"


def create_backup(
    config: Config, home: DataHome, engine: Engine, *, out: Path | None = None, raw: bool = False
) -> BackupResult:
    out = out or default_out(home)
    if out.is_dir():
        out = out / default_out(home).name
    out.parent.mkdir(parents=True, exist_ok=True)
    revision = migrate.current_revision(engine)
    if revision != migrate.head_revision():
        raise BackupError("The ledger schema is not current; run `tessera db upgrade` first.")
    with job_lock(home.root), tempfile.TemporaryDirectory(dir=out.parent, prefix=".backup-") as tmp:
        work = Path(tmp)
        files: dict[str, dict[str, Any]] = {}
        tables: dict[str, int] = {}
        (work / "ledger").mkdir()
        with engine.connect() as conn, conn.begin():
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for table in metadata.sorted_tables:
                path = work / "ledger" / f"{table.name}.jsonl"
                count = 0
                with path.open("w", encoding="utf-8") as handle:
                    key = list(table.primary_key.columns)
                    for row in conn.execute(select(table).order_by(*key)).mappings():
                        handle.write(json.dumps(dict(row), default=_json_default,
                                                ensure_ascii=False) + "\n")  # fmt: skip
                        count += 1
                tables[table.name] = count
        members: list[tuple[str, Path]] = [
            (f"ledger/{p.name}", p) for p in sorted((work / "ledger").iterdir())
        ]
        for name, root in (("lake", home.lake), ("snapshots", home.root / "snapshots")) + (
            (("raw", home.raw),) if raw else ()
        ):
            members += [(f"{name}/{p.relative_to(root).as_posix()}", p) for p in _tree(root)]
        if config.path and config.path.is_file():
            members.append(("config/tessera.toml", config.path))
        for arcname, path in members:
            files[arcname] = {"bytes": path.stat().st_size, "sha256": _hash_file(path)}
        manifest = {
            "format": FORMAT,
            "tessera_version": __version__,
            "created_at": isoformat(utc_now()),
            "organization": config.organization.name,
            "ledger": {"revision": revision, "backend": engine.dialect.name, "tables": tables},
            "includes": sorted({a.split("/")[0] for a, _ in members}),
            "files": files,
        }
        partial = out.with_name(f".{out.name}.partial")
        with tarfile.open(partial, "w:gz") as archive:
            data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime = len(data), int(utc_now().timestamp())
            archive.addfile(info, io.BytesIO(data))
            for arcname, path in members:
                archive.add(path, arcname=arcname, recursive=False)
        partial.replace(out)
    size = out.stat().st_size
    with engine.begin() as conn:
        _remember(conn, {"at": manifest["created_at"], "path": str(out), "bytes": size,
                         "revision": revision})  # fmt: skip
    return BackupResult(out, size, tables, len(members))


def _remember(conn: Any, value: dict[str, Any]) -> None:
    now = utc_now()
    found = conn.execute(select(setting.c.key).where(setting.c.key == LAST_BACKUP)).first()
    values = {"value": value, "updated_by": "tessera backup", "updated_at": now}
    if found:
        conn.execute(update(setting).where(setting.c.key == LAST_BACKUP).values(**values))
    else:
        conn.execute(insert(setting).values(key=LAST_BACKUP, **values))


def last_backup(engine: Engine) -> dict[str, Any] | None:
    with engine.connect() as conn:
        value = conn.execute(select(setting.c.value).where(setting.c.key == LAST_BACKUP)).scalar()
    return value or None


def _safe(name: str) -> bool:
    parts = Path(name).parts
    return bool(parts) and not Path(name).is_absolute() and ".." not in parts


def verify_backup(path: Path) -> VerifyResult:
    """Check every file in an archive against its manifest."""
    if not path.is_file():
        raise BackupError(f"No backup at {path}.")
    try:
        archive = tarfile.open(path, "r:gz")  # noqa: SIM115 - closed by the with below
    except (tarfile.TarError, OSError) as exc:
        raise BackupError(f"{path} is not a Tessera backup: {exc}") from exc
    with archive:
        try:
            manifest = json.loads(archive.extractfile("manifest.json").read())  # type: ignore[union-attr]
        except (KeyError, AttributeError, json.JSONDecodeError) as exc:
            raise BackupError(f"{path} has no readable manifest.") from exc
        if manifest.get("format") != FORMAT:
            raise BackupError(f"{path} is not a {FORMAT} archive.")
        result = VerifyResult(path, manifest)
        expected = dict(manifest["files"])
        for member in archive.getmembers():
            if member.name == "manifest.json":
                continue
            if not member.isfile() or not _safe(member.name):
                result.problems.append(f"{member.name} is not a plain file inside the archive.")
                continue
            info = expected.pop(member.name, None)
            if info is None:
                result.problems.append(f"{member.name} is not in the manifest.")
                continue
            h = hashlib.sha256()
            handle = archive.extractfile(member)
            assert handle is not None
            for chunk in iter(lambda: handle.read(CHUNK), b""):  # noqa: B023 - read at once
                h.update(chunk)
            if h.hexdigest() != info["sha256"]:
                result.problems.append(f"{member.name} does not match its digest.")
        result.problems += [f"{name} is missing." for name in sorted(expected)]
    return result


# --- restore -------------------------------------------------------------------------------------


def _empty(engine: Engine, home: DataHome) -> list[str]:
    """Why the target is not empty (nothing when it is)."""
    problems = []
    if migrate.current_revision(engine) is not None:
        live = MetaData()
        live.reflect(bind=engine)
        with engine.connect() as conn:
            for table in live.sorted_tables:
                if table.name == "alembic_version":
                    continue
                if conn.execute(select(func.count()).select_from(table)).scalar():
                    problems.append(f"The ledger already has data (table {table.name}).")
                    break
    for name, root in (("lake", home.lake), ("snapshots", home.root / "snapshots")):
        if any(_tree(root)):
            problems.append(f"The data home already has a {name} ({root}).")
    return problems


def _convert(column: Any, value: Any, sqlite: bool) -> Any:
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, DateTime) and isinstance(value, str):
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        return moment.replace(tzinfo=None) if sqlite else moment
    if isinstance(kind, Date) and not isinstance(kind, DateTime) and isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(kind, Boolean):
        return bool(value)
    if isinstance(kind, JSON):
        return value
    return value


def restore_backup(
    config: Config, home: DataHome, engine: Engine, path: Path, *, checked: bool = False
) -> RestoreResult:
    """Load an archive into an empty data home and ledger, then upgrade the schema."""
    verified = verify_backup(path)
    if not verified.ok:
        raise BackupError("The backup does not match its manifest: " + "; ".join(
            verified.problems[:5]))  # fmt: skip
    manifest = verified.manifest
    revision = manifest["ledger"]["revision"]
    known = migrate.script_revisions()
    if revision not in known:
        raise BackupError(
            f"The backup's ledger is at schema {revision}, which this version of Tessera "
            "does not know: restore it with the version that made it, or a newer one."
        )
    home.create()
    problems = _empty(engine, home)
    if problems:
        raise BackupError("Restore into an empty data home and ledger. " + " ".join(problems))
    current = migrate.current_revision(engine)
    if current != revision:
        if current is not None:
            migrate.downgrade(engine, "base")  # an empty ledger, as `tessera init` makes one
        migrate.upgrade(engine, revision)
    live = MetaData()
    live.reflect(bind=engine)
    sqlite = engine.dialect.name == "sqlite"
    counts: dict[str, int] = {}
    with tarfile.open(path, "r:gz") as archive, engine.begin() as conn:
        names = {m.name for m in archive.getmembers()}
        for table in live.sorted_tables:
            member = f"ledger/{table.name}.jsonl"
            if table.name == "alembic_version" or member not in names:
                continue
            handle = archive.extractfile(member)
            assert handle is not None
            self_refs = [
                fk.parent.name for fk in table.foreign_keys if fk.column.table.name == table.name
            ]
            later: list[tuple[dict[str, Any], dict[str, Any]]] = []
            batch: list[dict[str, Any]] = []
            count = 0
            for line in io.TextIOWrapper(handle, encoding="utf-8"):
                raw = json.loads(line)
                row = {k: _convert(table.c[k], v, sqlite) for k, v in raw.items() if k in table.c}
                held = {k: row[k] for k in self_refs if row.get(k) is not None}
                if held:
                    later.append(({c.name: row[c.name] for c in table.primary_key.columns}, held))
                    row = {**row, **dict.fromkeys(held)}
                batch.append(row)
                count += 1
                if len(batch) >= 1000:
                    conn.execute(insert(table), batch)
                    batch = []
            if batch:
                conn.execute(insert(table), batch)
            for key, held in later:  # references within the table, once every row is there
                where = [table.c[k] == v for k, v in key.items()]
                conn.execute(update(table).where(*where).values(**held))
            counts[table.name] = count
            if not sqlite:
                _reset_sequence(conn, table)
        files = 0
        targets = {"lake": home.lake, "snapshots": home.root / "snapshots", "raw": home.raw,
                   "config": home.root / "restored"}  # fmt: skip
        for member in archive.getmembers():
            top = member.name.split("/")[0]
            if top not in targets or not member.isfile() or not _safe(member.name):
                continue
            target = targets[top] / Path(*Path(member.name).parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            assert source is not None
            with target.open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK)
            files += 1
    expected = manifest["ledger"]["tables"]
    wrong = [t for t, n in expected.items() if counts.get(t, 0) != n]
    if wrong:
        raise BackupError(f"Restored row counts differ from the backup's for {', '.join(wrong)}.")
    upgraded = None
    if revision != migrate.head_revision():
        migrate.upgrade(engine)
        upgraded = migrate.head_revision()
    return RestoreResult(counts, files, revision, upgraded)


def _reset_sequence(conn: Any, table: Any) -> None:
    """PostgreSQL: move an integer key's sequence past the restored rows."""
    for column in table.primary_key.columns:
        if column.autoincrement is True or (
            column.autoincrement == "auto" and str(column.type).upper().startswith("INT")
        ):
            name = table.name.replace('"', "")
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('\"{name}\"', '{column.name}'), "  # noqa: S608
                    f'COALESCE(MAX("{column.name}"), 1), MAX("{column.name}") IS NOT NULL) '
                    f'FROM "{name}"'
                )
            )


__all__ = [
    "BackupError",
    "BackupResult",
    "RestoreResult",
    "VerifyResult",
    "create_backup",
    "default_out",
    "last_backup",
    "restore_backup",
    "verify_backup",
]
