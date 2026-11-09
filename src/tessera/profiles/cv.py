"""CVs built from a profile and a person's confirmed records (PRF-04).

A CV is a named ``spec``: which sections it includes, in what order, which entries of each (all
of them, including ones added later, or a chosen list in a chosen order), and its options. It is
built from the profile and the records each time it is shown, so it never goes stale.
Publications, grants, and studies come from confirmed links only; publications are cited in a
style close to the National Library of Medicine's, with the person's own name in bold.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.config import Config
from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.groups import store as groups
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.lake.reader import quote_literal
from tessera.ledger.tables import assertion, cv_document, person, person_identifier, unit
from tessera.profiles import format as fmt
from tessera.profiles import store
from tessera.profiles.sections import ProfileError

SECTIONS = {
    "overview": "Summary",
    "position": "Positions and appointments",
    "education": "Education and training",
    "honor": "Honors and awards",
    "membership": "Memberships and service",
    "teaching": "Teaching and mentoring",
    "talk": "Talks and presentations",
    "publications": "Publications",
    "grants": "Grants",
    "studies": "Clinical studies",
}
DEFAULT = (
    "position",
    "education",
    "honor",
    "membership",
    "teaching",
    "talk",
    "publications",
    "grants",
    "studies",
)
PUBLICATION_MODES = ("all", "since", "chosen")
MOST_PICKED = 1000


# --- the spec ------------------------------------------------------------------------------------


def default_spec() -> dict[str, Any]:
    return {
        "sections": [
            {"id": s, "pick": None}
            | ({"mode": "all", "numbered": True} if s == "publications" else {})
            for s in DEFAULT
        ]
    }


def clean_spec(spec: Any) -> dict[str, Any]:
    """The spec, checked: known sections once each, picks as lists of IDs, valid options."""
    if not isinstance(spec, dict) or not isinstance(spec.get("sections"), list):
        raise ProfileError("A CV's spec has a list of sections.")
    out, seen = [], set()
    for raw in spec["sections"]:
        if not isinstance(raw, dict) or raw.get("id") not in SECTIONS:
            raise ProfileError(
                f"There is no CV section {raw!r}."
                if not isinstance(raw, dict)
                else f"There is no CV section {raw.get('id')!r}."
            )
        sid = raw["id"]
        if sid in seen:
            raise ProfileError(f"{SECTIONS[sid]} appears twice.")
        seen.add(sid)
        item: dict[str, Any] = {"id": sid, "pick": None}
        pick = raw.get("pick")
        if pick is not None:
            if not isinstance(pick, list) or len(pick) > MOST_PICKED:
                raise ProfileError(f"{SECTIONS[sid]}: choose up to {MOST_PICKED} entries.")
            item["pick"] = list(dict.fromkeys(str(p) for p in pick))
        title = raw.get("title")
        if title:
            item["title"] = str(title).strip()[:120]
        if sid == "publications":
            mode = raw.get("mode", "all")
            if mode not in PUBLICATION_MODES:
                raise ProfileError("Publications: all, since a year, or chosen.")
            item["mode"] = mode
            item["numbered"] = bool(raw.get("numbered", True))
            if mode == "since":
                try:
                    since = int(raw.get("since"))
                except (TypeError, ValueError) as exc:
                    raise ProfileError("Publications since which year?") from exc
                if not 1900 <= since <= 2100:
                    raise ProfileError("Publications since which year?")
                item["since"] = since
        out.append(item)
    return {"sections": out}


# --- what a CV can include -------------------------------------------------------------


def _directory_positions(
    conn: Connection, config: Config, person_id: str, today: date
) -> list[dict[str, Any]]:
    links = [
        dict(r)
        for r in conn.execute(
            select(assertion).where(
                assertion.c.subject_id == person_id,
                assertion.c.predicate == "member_of",
                assertion.c.status == "accepted",
            )
        ).mappings()
    ]
    names = (
        dict(
            conn.execute(
                select(unit.c.id, unit.c.name).where(unit.c.id.in_([a["object_id"] for a in links]))
            ).all()
        )
        if links
        else {}
    )
    out = []
    for a in links:
        view = groups.membership_view(a, config.vocabulary, today)
        fields = {
            "title": view["title"] or view["role_label"],
            "organization": ", ".join(
                x for x in (names.get(a["object_id"]), config.organization.name) if x
            ),
            "start": view["start"],
            "end": view["end"],
        }
        out.append(
            {
                "id": f"dir:{a['id']}",
                "fields": {k: v for k, v in fields.items() if v},
                "directory": True,
                "current": view["current"],
            }
        )
    out.sort(key=lambda x: (not x["current"], -(int((x["fields"].get("start") or "0")[:4]))))
    return out


def _authors(
    lake: Lake, pmids: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    if not pmids:
        return {}, {}
    listed = ", ".join(quote_literal(p) for p in pmids)  # quoted literals, never raw text
    article_sql = f"SELECT pmid, title, journal_abbrev, journal, pub_year, volume, issue, pages, doi FROM pubmed_article WHERE pmid IN ({listed})"  # noqa: S608, E501
    author_sql = f"SELECT pmid, position, family, initials, collective_name FROM pubmed_author WHERE pmid IN ({listed})"  # noqa: S608, E501
    articles = {str(r["pmid"]): r for r in lake.rows(article_sql)}
    authors: dict[str, dict[int, str]] = {}
    for r in lake.rows(author_sql):
        name = r["collective_name"] or " ".join(x for x in (r["family"], r["initials"]) if x)
        authors.setdefault(str(r["pmid"]), {})[int(r["position"])] = name
    return articles, {p: [a[k] for k in sorted(a)] for p, a in authors.items()}


def _publications(conn: Connection, home: DataHome, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(assertion.c.object_id, assertion.c.qualifiers).where(
            assertion.c.subject_id == person_id,
            assertion.c.predicate == "authored",
            assertion.c.status == "accepted",
        )
    ).all()
    mine = {
        r.object_id.removeprefix("pub:"): (r.qualifiers or {}).get("author_position") for r in rows
    }
    with Lake(home.lake) as lake:
        articles, authors = _authors(lake, sorted(mine))
    out = []
    for pmid, position in mine.items():
        a = articles.get(pmid)
        if a is None:
            continue
        out.append(
            {
                "id": pmid,
                "pmid": pmid,
                "title": a["title"],
                "journal": a["journal_abbrev"] or a["journal"],
                "year": a["pub_year"],
                "volume": a["volume"],
                "issue": a["issue"],
                "pages": a["pages"],
                "doi": a["doi"],
                "authors": authors.get(pmid, []),
                "self": int(position) - 1 if position else None,
            }
        )
    out.sort(key=lambda p: (-(p["year"] or 0), p["pmid"]))
    return out


def _linked(conn: Connection, index: Any, person_id: str, predicate: str) -> list[dict[str, Any]]:
    refs = [
        r
        for (r,) in conn.execute(
            select(assertion.c.object_id).where(
                assertion.c.subject_id == person_id,
                assertion.c.predicate == predicate,
                assertion.c.status == "accepted",
            )
        )
    ]
    found = index.summaries(sorted(refs)) if index is not None and refs else {}
    out = []
    for ref in refs:
        s = found.get(ref) or {}
        if predicate == "investigator_on":
            people = s.get("investigators") or []
            me = next(
                (p for p in people if p.get("id") == person_id or p.get("person_id") == person_id),
                {},
            )
            role = "PI" if len(people) <= 1 else ("Contact PI" if me.get("contact_pi") else "MPI")
            years = s.get("fiscal_years") or []
            out.append(
                {
                    "id": ref.removeprefix("grt:"),
                    "main": s.get("title") or ref[4:],
                    "meta": fmt._join(ref[4:], s.get("institute"), role),
                    "dates": fmt.span(
                        {
                            "start": (s.get("project_start") or "")[:7]
                            or (str(years[0]) if years else None),
                            "end": (s.get("project_end") or "")[:7] or None,
                        },
                        "grant",
                    ),
                    "sort": s.get("project_start") or "",
                }
            )
        else:
            officials = s.get("officials") or []
            me = next((o for o in officials if o.get("id") == person_id), {})
            phases = "/".join(
                p.replace("PHASE", "Phase ") for p in s.get("phases") or [] if p != "NA"
            )
            role = (me.get("role") or "").replace("_", " ").capitalize()
            out.append(
                {
                    "id": ref.removeprefix("stu:"),
                    "main": s.get("title") or ref[4:],
                    "meta": fmt._join(
                        ref[4:],
                        phases,
                        (s.get("registry_status") or "").replace("_", " ").capitalize(),
                        role,
                    ),
                    "dates": fmt.when((s.get("start_date") or "")[:7] or None),
                    "sort": s.get("start_date") or "",
                }
            )
    out.sort(key=lambda x: x["sort"], reverse=True)
    return out


def catalogue(
    conn: Connection,
    config: Config,
    home: DataHome,
    index: Any,
    person_id: str,
    today: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Everything a CV of this person can include, by section, in its natural order."""
    today = today or date.today()
    found = store.entries(conn, person_id)
    out: dict[str, list[dict[str, Any]]] = {}
    for sid in SECTIONS:
        if sid in ("publications", "grants", "studies"):
            continue
        rows = [{"id": r["id"], "fields": r["fields"]} for r in found.get(sid, [])]
        if sid == "position":
            rows = _directory_positions(conn, config, person_id, today) + rows
        out[sid] = rows
    out["publications"] = _publications(conn, home, person_id)
    out["grants"] = _linked(conn, index, person_id, "investigator_on")
    out["studies"] = _linked(conn, index, person_id, "official_of")
    return out


# --- building it ----------------------------------------------------------------------------------


def citation(p: dict[str, Any], bold: bool = True) -> tuple[list[tuple[str, bool]], str]:
    """(authors as (name, is the person), the rest), in a style close to NLM's."""
    names = [(n, bold and i == p.get("self")) for i, n in enumerate(p["authors"])]
    where = f"{p['year'] or ''}"
    if p.get("volume"):
        where += f";{p['volume']}" + (f"({p['issue']})" if p.get("issue") else "")
    if p.get("pages"):
        where += f":{p['pages']}"
    tail = ". ".join(x for x in (p["title"].rstrip("."), p.get("journal"), where) if x) + "."
    if p.get("doi"):
        tail += f" doi:{p['doi']}."
    return names, tail + f" PMID: {p['pmid']}."


def _item(section: str, row: dict[str, Any]) -> dict[str, Any]:
    if section in ("grants", "studies"):
        return {k: row[k] for k in ("id", "main", "meta", "dates")}
    if section == "publications":
        names, tail = citation(row)
        return {
            "id": row["id"],
            "authors": [{"name": n, "self": s} for n, s in names],
            "rest": tail,
            "year": row["year"],
        }
    if section == "overview":
        return {"id": row["id"], "text": row["fields"]["text"]}
    main, meta, dates = fmt.lines(section, row["fields"])
    return {
        "id": row["id"],
        "main": main,
        "meta": meta,
        "dates": dates,
        "note": row["fields"].get("description"),
        "directory": row.get("directory", False),
    }


def build(spec: dict[str, Any], available: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The CV's sections in order, each with its items; sections with nothing are left out."""
    out = []
    for s in spec["sections"]:
        sid = s["id"]
        rows = available.get(sid, [])
        if sid == "publications" and s.get("mode") == "since":
            rows = [r for r in rows if (r["year"] or 0) >= s["since"]]
        if s.get("pick") is not None and not (sid == "publications" and s.get("mode") != "chosen"):
            by_id = {str(r["id"]): r for r in rows}
            rows = [by_id[i] for i in s["pick"] if i in by_id]
        if rows:
            out.append(
                {
                    "id": sid,
                    "title": s.get("title") or SECTIONS[sid],
                    "numbered": bool(s.get("numbered")) if sid == "publications" else False,
                    "items": [_item(sid, r) for r in rows],
                }
            )
    return out


def header(conn: Connection, config: Config, person_id: str) -> dict[str, Any]:
    row = conn.execute(select(person.c.display_name).where(person.c.id == person_id)).first()
    orcid = conn.execute(
        select(person_identifier.c.value).where(
            person_identifier.c.person_id == person_id,
            person_identifier.c.scheme == "orcid",
            person_identifier.c.status == "accepted",
        )
    ).scalar()
    return {
        "name": row.display_name if row else person_id,
        "organization": config.organization.name,
        "orcid": orcid,
    }


def markdown(name: str, head: dict[str, Any], sections: list[dict[str, Any]], today: date) -> str:
    lines = [
        f"# {head['name']}",
        "",
        f"{name} · {head['organization']} · {today:%B %Y}",
    ]
    if head.get("orcid"):
        lines.append(f"ORCID iD: https://orcid.org/{head['orcid']}")
    for s in sections:
        lines += ["", f"## {s['title']}", ""]
        for n, item in enumerate(s["items"], start=1):
            if s["id"] == "overview":
                lines.append(item["text"])
            elif s["id"] == "publications":
                authors = ", ".join(
                    f"**{a['name']}**" if a["self"] else a["name"] for a in item["authors"]
                )
                bullet = f"{n}." if s["numbered"] else "-"
                lines.append(
                    f"{bullet} {authors}. {item['rest']}" if authors else f"{bullet} {item['rest']}"
                )
            else:
                text = f"- **{item['main']}**"
                detail = " · ".join(x for x in (item.get("meta"), item.get("dates")) if x)
                if detail:
                    text += f"  \n  {detail}"
                if item.get("note"):
                    text += f"  \n  {item['note']}"
                lines.append(text)
    return "\n".join(lines).rstrip() + "\n"


# --- the store --------------------------------------------------------------------------


def documents(conn: Connection, person_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            select(
                cv_document.c.id,
                cv_document.c.kind,
                cv_document.c.name,
                cv_document.c.form,
                cv_document.c.updated_at,
                cv_document.c.updated_by,
            )
            .where(cv_document.c.person_id == person_id)
            .order_by(cv_document.c.kind, cv_document.c.name)
        ).mappings()
    ]


def get(conn: Connection, person_id: str, doc_id: str) -> dict[str, Any]:
    row = (
        conn.execute(
            select(cv_document).where(
                cv_document.c.id == doc_id, cv_document.c.person_id == person_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ProfileError("No such CV or biosketch for this person.")
    return dict(row)


def _name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ProfileError("Give it a name, such as “Promotion dossier”.")
    if len(text) > 200:
        raise ProfileError("A name is at most 200 characters.")
    return text


def create(
    engine: Engine,
    person_id: str,
    *,
    name: Any,
    kind: str = "cv",
    spec: Any = None,
    form: str | None = None,
    actor: str,
    capacity: str,
) -> dict[str, Any]:
    from tessera.profiles.store import _event, _member

    if kind not in ("cv", "nih_biosketch"):
        raise ProfileError("A document is a CV or an NIH biosketch.")
    spec = spec if kind != "cv" else clean_spec(spec if spec is not None else default_spec())
    now = utc_now()
    doc_id = new_id("cvd")
    with engine.begin() as conn:
        _member(conn, person_id)
        conn.execute(
            insert(cv_document).values(
                id=doc_id,
                person_id=person_id,
                kind=kind,
                name=_name(name),
                spec=spec,
                form=form,
                created_at=now,
                created_by=actor,
                updated_at=now,
                updated_by=actor,
            )
        )
        _event(
            conn,
            person_id,
            doc_id,
            "documents",
            "created",
            actor,
            capacity,
            after={"name": _name(name), "kind": kind},
        )
        return get(conn, person_id, doc_id)


def change(
    engine: Engine,
    person_id: str,
    doc_id: str,
    *,
    actor: str,
    capacity: str,
    name: Any = None,
    spec: Any = None,
) -> dict[str, Any]:
    from tessera.profiles.store import _event

    with engine.begin() as conn:
        before = get(conn, person_id, doc_id)
        values: dict[str, Any] = {}
        if name is not None and _name(name) != before["name"]:
            values["name"] = _name(name)
        if spec is not None:
            spec = clean_spec(spec) if before["kind"] == "cv" else spec
            if spec != before["spec"]:
                values["spec"] = spec
        if values:
            conn.execute(
                update(cv_document)
                .where(cv_document.c.id == doc_id)
                .values(**values, updated_at=utc_now(), updated_by=actor)
            )
            _event(
                conn,
                person_id,
                doc_id,
                "documents",
                "renamed" if "name" in values and len(values) == 1 else "changed",
                actor,
                capacity,
                before={"name": before["name"]},
                after={"name": values.get("name", before["name"])},
            )
        return get(conn, person_id, doc_id)


def remove(engine: Engine, person_id: str, doc_id: str, *, actor: str, capacity: str) -> None:
    from tessera.profiles.store import _event

    with engine.begin() as conn:
        before = get(conn, person_id, doc_id)
        conn.execute(delete(cv_document).where(cv_document.c.id == doc_id))
        _event(
            conn,
            person_id,
            doc_id,
            "documents",
            "deleted",
            actor,
            capacity,
            before={"name": before["name"], "kind": before["kind"]},
        )


__all__ = [
    "DEFAULT",
    "SECTIONS",
    "build",
    "catalogue",
    "change",
    "citation",
    "clean_spec",
    "create",
    "default_spec",
    "documents",
    "get",
    "header",
    "markdown",
    "remove",
]
