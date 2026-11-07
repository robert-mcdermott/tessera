"""Lake table definitions (Arrow schemas). See spec/data-model.md.

Tables are grouped into *datasets*. A dataset has one root table (one row per source
record, e.g. a PubMed article) and optional child tables (rows that belong to a root
record, e.g. its authors). A root record and its children are written together as a
*bundle* and share ``_run_id`` and ``_retrieved_at``, so the lake can tell which child
rows belong to the current version of each root record.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

STR = pa.string()
I16 = pa.int16()
I32 = pa.int32()
I64 = pa.int64()
F64 = pa.float64()
BOOL = pa.bool_()
DATE = pa.date32()
STR_LIST = pa.list_(STR)

BOOKKEEPING = (
    pa.field("_run_id", STR, nullable=False),
    pa.field("_retrieved_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("_content_hash", STR, nullable=False),
    pa.field("_source_version", STR),
)
BOOKKEEPING_NAMES = frozenset(f.name for f in BOOKKEEPING)


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[pa.Field, ...]
    key: tuple[str, ...]
    field: str | None = None  # for child tables: the list in the root record holding its rows
    version: int = 1

    @property
    def schema(self) -> pa.Schema:
        return pa.schema([*self.columns, *BOOKKEEPING])

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.columns)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    root: TableSpec
    children: tuple[TableSpec, ...] = ()

    @property
    def tables(self) -> tuple[TableSpec, ...]:
        return (self.root, *self.children)

    @property
    def group_key(self) -> tuple[str, ...]:
        return self.root.key

    def validate(self) -> None:
        for table in self.tables:
            names = set(table.column_names)
            missing = set(table.key) - names
            if missing:
                raise ValueError(f"{table.name}: key columns {sorted(missing)} not in schema")
            if names & BOOKKEEPING_NAMES:
                raise ValueError(f"{table.name}: column names may not start with '_'")
        for child in self.children:
            if child.field is None:
                raise ValueError(f"{child.name}: child tables need a record field name")
            if not set(self.group_key) <= set(child.column_names):
                raise ValueError(f"{child.name}: must contain root key {self.group_key}")


def f(name: str, kind: pa.DataType, nullable: bool = True) -> pa.Field:
    return pa.field(name, kind, nullable=nullable)


# --- PubMed ---------------------------------------------------------------------------

PUBMED = DatasetSpec(
    name="pubmed",
    root=TableSpec(
        name="pubmed_article",
        key=("pmid",),
        columns=(
            f("pmid", STR, False),
            f("doi", STR),
            f("pmcid", STR),
            f("title", STR),
            f("abstract", STR),
            f("journal", STR),
            f("journal_abbrev", STR),
            f("issn", STR),
            f("volume", STR),
            f("issue", STR),
            f("pages", STR),
            f("pub_date", DATE),
            f("pub_year", I16),
            f("date_precision", STR),  # year | month | day
            f("epub_date", DATE),
            f("publication_types", STR_LIST),
            f("publication_status", STR),
            f("languages", STR_LIST),
            f("keywords", STR_LIST),
            f("is_retracted", BOOL),
            f(
                "comments_corrections",
                pa.list_(pa.struct([f("ref_type", STR), f("pmid", STR), f("note", STR)])),
            ),
        ),
    ),
    children=(
        TableSpec(
            name="pubmed_author",
            key=("pmid", "position"),
            field="authors",
            columns=(
                f("pmid", STR, False),
                f("position", I16, False),
                f("family", STR),
                f("given", STR),
                f("initials", STR),
                f("suffix", STR),
                f("collective_name", STR),
                f("orcid", STR),
                f("affiliations", STR_LIST),
                f("is_first", BOOL),
                f("is_last", BOOL),
                f("author_count", I16),
            ),
        ),
        TableSpec(
            name="pubmed_mesh",
            key=("pmid", "descriptor_ui"),
            field="mesh",
            columns=(
                f("pmid", STR, False),
                f("descriptor_ui", STR, False),
                f("descriptor", STR),
                f("major_topic", BOOL),
                f("qualifiers", STR_LIST),
            ),
        ),
        TableSpec(
            name="pubmed_grant",
            key=("pmid", "seq"),
            field="grants",
            columns=(
                f("pmid", STR, False),
                f("seq", I16, False),
                f("grant_id_raw", STR),
                f("core_project_num", STR),
                f("acronym", STR),
                f("agency", STR),
                f("country", STR),
            ),
        ),
    ),
)

# --- iCite ----------------------------------------------------------------------------

ICITE = DatasetSpec(
    name="icite",
    root=TableSpec(
        name="icite_metrics",
        key=("pmid",),
        columns=(
            f("pmid", STR, False),
            f("year", I16),
            f("citation_count", I32),
            f("relative_citation_ratio", F64),
            f("nih_percentile", F64),
            f("field_citation_rate", F64),
            f("expected_citations_per_year", F64),
            f("citations_per_year", F64),
            f("apt", F64),
            f("is_clinical", BOOL),
            f("is_research_article", BOOL),
        ),
    ),
)

# --- NIH RePORTER ---------------------------------------------------------------------

REPORTER = DatasetSpec(
    name="reporter",
    root=TableSpec(
        name="reporter_project",
        key=("appl_id",),
        columns=(
            f("appl_id", I64, False),
            f("project_num", STR),
            f("core_project_num", STR),
            f("subproject_id", STR),
            f("fiscal_year", I16),
            f("title", STR),
            f("abstract", STR),
            f("phr", STR),
            f("activity_code", STR),
            f("agency_ic_admin", STR),
            f("funding_mechanism", STR),
            f("award_amount", I64),
            f("direct_cost", I64),
            f("indirect_cost", I64),
            f("award_notice_date", DATE),
            f("project_start", DATE),
            f("project_end", DATE),
            f("budget_start", DATE),
            f("budget_end", DATE),
            f("is_active", BOOL),
            f("is_new", BOOL),
            f("org_name", STR),
            f("org_city", STR),
            f("org_state", STR),
            f("org_country", STR),
            f("org_uei", STR),
            f("org_dept_type", STR),
            f("terms", STR_LIST),
            f("spending_categories", STR_LIST),
            f("date_added", DATE),
        ),
    ),
    children=(
        TableSpec(
            name="reporter_investigator",
            key=("appl_id", "profile_id"),
            field="investigators",
            columns=(
                f("appl_id", I64, False),
                f("profile_id", I64, False),
                f("full_name", STR),
                f("family", STR),
                f("given", STR),
                f("middle", STR),
                f("title", STR),
                f("is_contact_pi", BOOL),
            ),
        ),
    ),
)

REPORTER_LINKS = DatasetSpec(
    name="reporter_links",
    root=TableSpec(
        name="reporter_publication_link",
        key=("pmid", "core_project_num"),
        columns=(
            f("pmid", STR, False),
            f("core_project_num", STR, False),
            f("appl_id", I64),
        ),
    ),
)

# --- ClinicalTrials.gov ---------------------------------------------------------------

_MESH_TERM = pa.list_(pa.struct([f("id", STR), f("term", STR)]))

CTGOV = DatasetSpec(
    name="ctgov",
    root=TableSpec(
        name="ctgov_study",
        key=("nct_id",),
        columns=(
            f("nct_id", STR, False),
            f("aliases", STR_LIST),
            f("org_study_id", STR),
            f("brief_title", STR),
            f("official_title", STR),
            f("acronym", STR),
            f("overall_status", STR),
            f("last_known_status", STR),
            f("why_stopped", STR),
            f("study_type", STR),
            f("phases", STR_LIST),
            f("conditions", STR_LIST),
            f("condition_mesh", _MESH_TERM),
            f("intervention_mesh", _MESH_TERM),
            f("interventions", pa.list_(pa.struct([f("type", STR), f("name", STR)]))),
            f("keywords", STR_LIST),
            f("lead_sponsor", STR),
            f("lead_sponsor_class", STR),
            f("collaborators", STR_LIST),
            f("start_date", DATE),
            f("start_date_type", STR),
            f("primary_completion_date", DATE),
            f("primary_completion_date_type", STR),
            f("completion_date", DATE),
            f("completion_date_type", STR),
            f("enrollment", I32),
            f("enrollment_type", STR),
            f("has_results", BOOL),
            f("first_posted", DATE),
            f("last_update_posted", DATE),
            f("brief_summary", STR),
        ),
    ),
    children=(
        TableSpec(
            name="ctgov_official",
            key=("nct_id", "seq"),
            field="officials",
            columns=(
                f("nct_id", STR, False),
                f("seq", I16, False),
                f("name", STR),
                f("affiliation", STR),
                f("role", STR),
            ),
        ),
        TableSpec(
            name="ctgov_site",
            key=("nct_id", "seq"),
            field="sites",
            columns=(
                f("nct_id", STR, False),
                f("seq", I16, False),
                f("facility", STR),
                f("status", STR),
                f("city", STR),
                f("state", STR),
                f("country", STR),
                f("latitude", F64),
                f("longitude", F64),
            ),
        ),
        TableSpec(
            name="ctgov_reference",
            key=("nct_id", "pmid"),
            field="references",
            columns=(
                f("nct_id", STR, False),
                f("pmid", STR, False),
                f("type", STR),
                f("citation", STR),
            ),
        ),
        TableSpec(
            name="ctgov_secondary_id",
            key=("nct_id", "seq"),
            field="secondary_ids",
            columns=(
                f("nct_id", STR, False),
                f("seq", I16, False),
                f("id", STR),
                f("type", STR),
                f("core_project_num", STR),
            ),
        ),
    ),
)

DATASETS: dict[str, DatasetSpec] = {
    d.name: d for d in (PUBMED, ICITE, REPORTER, REPORTER_LINKS, CTGOV)
}
TABLES: dict[str, TableSpec] = {t.name: t for d in DATASETS.values() for t in d.tables}
DATASET_OF: dict[str, DatasetSpec] = {t.name: d for d in DATASETS.values() for t in d.tables}

for _dataset in DATASETS.values():
    _dataset.validate()
