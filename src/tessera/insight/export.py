"""Exports that carry their meaning with them (INT-04).

Every export holds the figure's numbers, the records it counted with the links behind each,
and an account of what it is: the metric's definition, version, and counting rule, the cohort
and how it was chosen, the period, the options and filters, the sources with their data
timestamps, the notes, and when it was computed or frozen.

* **JSON**: one file, ``{"export": …, "about": …, "columns", "rows", "records", …}``.
* **CSV**: a zip with ``<name>.csv`` (the figure), ``<name>-records.csv``, ``about.json``,
  and ``README.txt`` (the same account in words).
* **Parquet**: a zip with ``<name>.parquet`` and ``<name>-records.parquet``, each carrying the
  account in its schema metadata under ``tessera``, with ``about.json`` and ``README.txt``.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tessera import __version__
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError

FORMATS = {
    "json": ("application/json", "json"),
    "csv": ("application/zip", "zip"),
    "parquet": ("application/zip", "zip"),
}
ABOUT_KEYS = (
    "metric", "cohort", "period", "options", "small_number", "notes", "sources", "generated_at",
)  # fmt: skip


class ExportError(TesseraError):
    """An export in a format Tessera does not write."""


def option_values(payload: dict[str, Any]) -> dict[str, Any]:
    """A figure's options as they are written in an address (``amount=direct``)."""
    return {k: v["value"] if isinstance(v, dict) else v
            for k, v in (payload.get("options") or {}).items()}  # fmt: skip


def about(payload: dict[str, Any], *, filters: dict[str, Any], snapshot: str | None = None,
          exported_at: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    out = {k: payload[k] for k in ABOUT_KEYS if k in payload}
    out["export"] = {
        "tessera_version": __version__,
        "exported_at": isoformat(exported_at or utc_now()),
        "filters": filters,
        "snapshot": snapshot,
        "totals": payload.get("totals"),
        "record_count": payload.get("record_count", len(payload.get("records") or [])),
    }
    return out


def readme(info: dict[str, Any]) -> str:
    m = info["metric"]
    cohort, period = info["cohort"], info["period"]
    lines = [
        f"{m['name']} ({m['ref']})",
        "",
        m["definition"],
        "",
        "What is counted",
        m["counting"],
        "",
        f"Cohort: {cohort['label']} ({cohort.get('size', '?')} people). "
        f"{cohort.get('note', '')}".rstrip(),
        f"Cohort as written: {cohort.get('spec', '')}",
        f"Period: {period['label']}",
    ]  # fmt: skip
    for key, option in (info.get("options") or {}).items():
        lines.append(f"{key}: {option['label']}")
    lines.append(f"Small numbers: counts from 1 to {info.get('small_number', 5) - 1} are marked.")
    lines += ["", "Sources"]
    for s in info.get("sources") or []:
        when = s.get("last_success_at") or "not fetched"
        complete = f", complete from {s['complete_from']}" if s.get("complete_from") else ""
        lines.append(f"- {s['label']}: last fetched {when}{complete}")
    if info.get("notes"):
        lines += ["", "Notes", *[f"- {n}" for n in info["notes"]]]
    if m.get("caveats"):
        lines += ["", "Keep in mind", *[f"- {c}" for c in m["caveats"]]]
    export = info.get("export") or {}
    lines += [
        "",
        f"Computed {info.get('generated_at')}; exported {export.get('exported_at')} by Tessera "
        f"{export.get('tessera_version')}.",
    ]
    if export.get("snapshot"):
        lines.append(f"From snapshot {export['snapshot']}, which `tessera report verify` checks.")
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return value


def _csv(columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([c["key"] for c in columns])
    for row in rows:
        writer.writerow(["" if row.get(c["key"]) is None else _cell(row.get(c["key"]))
                         for c in columns])  # fmt: skip
    return out.getvalue().encode("utf-8")


def _parquet(columns: list[dict[str, Any]], rows: list[dict[str, Any]], info: dict) -> bytes:
    data = {c["key"]: [_cell(r.get(c["key"])) for r in rows] for c in columns}
    table = pa.table(data) if columns else pa.table({})
    meta = {b"tessera": json.dumps(info, ensure_ascii=False, default=str).encode("utf-8"),
            b"tessera_columns": json.dumps(columns).encode("utf-8")}  # fmt: skip
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **meta})
    out = io.BytesIO()
    pq.write_table(table, out)
    return out.getvalue()


def add_figure(archive: zipfile.ZipFile, payload: dict, info: dict, fmt: str, prefix: str) -> None:
    name = payload["metric"]["id"].replace(".", "-")
    columns, records = payload["columns"], payload.get("records") or []
    record_columns = payload.get("record_columns") or []
    if fmt == "csv":
        archive.writestr(f"{prefix}{name}.csv", _csv(columns, payload["rows"]))
        archive.writestr(f"{prefix}{name}-records.csv", _csv(record_columns, records))
    else:
        archive.writestr(f"{prefix}{name}.parquet", _parquet(columns, payload["rows"], info))
        archive.writestr(f"{prefix}{name}-records.parquet",
                         _parquet(record_columns, records, info))  # fmt: skip
    archive.writestr(f"{prefix}about.json", json.dumps(info, indent=2, ensure_ascii=False,
                                                       default=str))  # fmt: skip
    archive.writestr(f"{prefix}README.txt", readme(info))


def figure_export(
    payload: dict[str, Any], fmt: str, *, filters: dict[str, Any], snapshot: str | None = None
) -> tuple[bytes, str, str]:
    """(content, media type, file name) for one figure computed with its records."""
    if fmt not in FORMATS:
        raise ExportError(f"Export as {', '.join(FORMATS)}.")
    info = about(payload, filters=filters, snapshot=snapshot)
    media, extension = FORMATS[fmt]
    stem = payload["metric"]["id"].replace(".", "-")
    if fmt == "json":
        body = {"export": info["export"], "about": {k: v for k, v in info.items() if k != "export"},
                **{k: payload[k] for k in ("columns", "rows", "totals", "small", "record_columns",
                                            "records") if k in payload}}  # fmt: skip
        return (
            json.dumps(body, indent=2, ensure_ascii=False, default=str).encode(),
            media,
            (f"{stem}.json"),
        )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        add_figure(archive, payload, info, fmt, "")
    return out.getvalue(), media, f"{stem}-{fmt}.{extension}"


def snapshot_export(
    manifest: dict[str, Any], payloads: list[dict[str, Any]], fmt: str
) -> tuple[bytes, str, str]:
    """Every figure of a snapshot in one zip (JSON too), with the manifest."""
    if fmt not in FORMATS:
        raise ExportError(f"Export as {', '.join(FORMATS)}.")
    report = manifest["report"]
    filters = {"cohort": report["cohort"], "start": report["start"], "end": report["end"]}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        lines = [f"{report['name']}: snapshot {manifest['snapshot']}",
                 f"Taken {manifest['taken_at']} by {manifest['taken_by']}.", ""]  # fmt: skip
        for n, payload in enumerate(payloads, start=1):
            prefix = f"{n:02d}-{payload['metric']['id'].replace('.', '-')}/"
            info = about(payload, filters={**filters, **option_values(payload)},
                         snapshot=manifest["snapshot"])  # fmt: skip
            lines.append(f"{prefix} {payload['metric']['name']} ({payload['metric']['ref']})")
            if fmt == "json":
                archive.writestr(f"{prefix}figure.json", json.dumps(
                    {"export": info["export"], **payload}, indent=2, ensure_ascii=False,
                    default=str))  # fmt: skip
                archive.writestr(f"{prefix}README.txt", readme(info))
            else:
                add_figure(archive, payload, info, fmt, prefix)
        archive.writestr("README.txt", "\n".join(lines) + "\n")
    return out.getvalue(), "application/zip", f"snapshot-{manifest['snapshot']}-{fmt}.zip"


__all__ = [
    "FORMATS",
    "ExportError",
    "about",
    "figure_export",
    "option_values",
    "readme",
    "snapshot_export",
]
