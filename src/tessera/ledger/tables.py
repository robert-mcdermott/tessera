"""Ledger schema (SQLAlchemy Core). The source of truth for migrations.

See spec/data-model.md. The ledger holds what humans decide or own; bulk source
content lives in the lake and is referenced by key.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    true,
)

from tessera.ledger.types import JsonType, UtcDateTime

# Deterministic constraint names keep migrations identical across SQLite and PostgreSQL.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

ID = String(40)  # typed IDs such as per_0J8ZQ3W5T4K2M9B7XHRC1DGFNE
ENTITY_REF = String(120)  # typed IDs or natural keys such as pub:12345678

# "dismissed": a discovered candidate a curator decided is not a member. Kept (not deleted)
# so that discovery does not propose the same person again. "merged": a duplicate record whose
# links, names, and identifiers moved to the record in ``merged_into`` (see person_merge).
PERSON_STATUSES = ("active", "candidate", "former", "dismissed", "merged")
PERSON_ACTIONS = ("promote", "dismiss", "undo", "identifier", "merge", "unmerge")
ASSERTION_STATUSES = ("suggested", "accepted", "rejected", "deferred")
ASSERTION_ORIGINS = ("source", "inferred", "human")
RUN_STATUSES = ("running", "succeeded", "partial", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


person = Table(
    "person",
    metadata,
    Column("id", ID, primary_key=True),
    Column("display_name", String(300), nullable=False),
    Column("family_name", String(200)),
    Column("given_name", String(200)),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("merged_into", ID, ForeignKey("person.id", ondelete="SET NULL")),
    # Opted in to a page on the public site (tessera publish).
    Column("public", Boolean, nullable=False, server_default=false()),
    CheckConstraint(_in("status", PERSON_STATUSES), name="status"),
)
Index(None, person.c.family_name)
Index(None, person.c.status)

person_name = Table(
    "person_name",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("family", String(200), nullable=False),
    Column("given", String(200), nullable=False, server_default=""),
    Column("initials", String(20), nullable=False, server_default=""),
    Column("kind", String(16), nullable=False, server_default="variant"),
    Column("source", String(40), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    UniqueConstraint("person_id", "family", "given", "kind"),
    CheckConstraint(_in("kind", ("primary", "variant", "former")), name="kind"),
)
Index(None, person_name.c.family)

person_identifier = Table(
    "person_identifier",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("scheme", String(20), nullable=False),
    Column("value", String(320), nullable=False),
    Column("status", String(16), nullable=False, server_default="accepted"),
    Column("source", String(40), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("person_id", "scheme", "value"),
    CheckConstraint(_in("scheme", ("orcid", "nih_profile", "email", "local")), name="scheme"),
    CheckConstraint(_in("status", ASSERTION_STATUSES), name="status"),
)
Index(None, person_identifier.c.scheme, person_identifier.c.value)

# Groups: divisions, departments, programs, centers, labs, and any other kind the organization
# configures ([vocabulary.kinds]). The table keeps its first name, ``unit``. Each group records
# where it came from (``manual``, ``roster``, or a named feed) and its identifier there, so a
# later import from the same source updates it instead of adding a duplicate.
GROUP_STATUSES = ("active", "archived", "merged")

unit = Table(
    "unit",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String(300), nullable=False),
    Column("kind", String(40), nullable=False, server_default="department"),
    Column("code", String(80), unique=True),
    Column("slug", String(120), unique=True),  # the group's address, /groups/<slug>
    Column("short_name", String(80)),
    Column("description", Text),
    Column("email", String(320)),  # contact, shown on the group's page
    Column("website", String(500)),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("merged_into", ID, ForeignKey("unit.id", ondelete="SET NULL")),
    Column("source", String(40), nullable=False, server_default="manual"),
    Column("external_id", String(200)),
    Column("public", Boolean, nullable=False, server_default=false()),  # on the public site
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("source", "external_id"),
    CheckConstraint(_in("status", GROUP_STATUSES), name="status"),
)

# A group may sit under several others (a program that spans divisions). ``source`` says who
# made the link, so an import replaces only the links it supplied.
unit_parent = Table(
    "unit_parent",
    metadata,
    Column("child_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
    Column("parent_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
    Column("source", String(40), nullable=False, server_default="manual"),
    Column("created_at", UtcDateTime, nullable=False),
    CheckConstraint("child_id <> parent_id", name="not_self"),
)
Index(None, unit_parent.c.parent_id)

# Every change to a group and its memberships, append-only: who, what, and the values before.
unit_event = Table(
    "unit_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("detail", JsonType, nullable=False),
    Column("at", UtcDateTime, nullable=False),
)
Index(None, unit_event.c.unit_id)
Index(None, unit_event.c.at)

assertion = Table(
    "assertion",
    metadata,
    Column("id", ID, primary_key=True),
    Column("subject_type", String(20), nullable=False),
    Column("subject_id", ENTITY_REF, nullable=False),
    Column("predicate", String(40), nullable=False),
    Column("object_type", String(20), nullable=False),
    Column("object_id", ENTITY_REF, nullable=False),
    # Distinguishes otherwise-identical links, e.g. the author position for `authored`.
    Column("qualifier_key", String(80), nullable=False, server_default=""),
    Column("qualifiers", JsonType, nullable=False),
    Column("origin", String(12), nullable=False),
    Column("method", String(60), nullable=False),
    Column("score", Float),
    Column("evidence", JsonType, nullable=False),
    Column("status", String(16), nullable=False),
    Column("locked", Boolean, nullable=False, server_default=false()),
    # False once the source that reported this link stops reporting it.
    Column("reported", Boolean, nullable=False, server_default=true()),
    Column("decided_by", String(200)),
    Column("decided_at", UtcDateTime),
    Column("decision_note", Text),
    Column("first_seen_at", UtcDateTime, nullable=False),
    Column("last_evaluated_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("subject_id", "predicate", "object_id", "qualifier_key"),
    CheckConstraint(_in("status", ASSERTION_STATUSES), name="status"),
    CheckConstraint(_in("origin", ASSERTION_ORIGINS), name="origin"),
    CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
)
Index(None, assertion.c.subject_id, assertion.c.predicate)
Index(None, assertion.c.object_id, assertion.c.predicate)
Index(None, assertion.c.predicate, assertion.c.status)

curation_event = Table(
    "curation_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("assertion_id", ID, ForeignKey("assertion.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16)),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
)
Index(None, curation_event.c.assertion_id)
Index(None, curation_event.c.at)

# Decisions about people (promote a discovered candidate, dismiss one) and about their
# identifiers (add, confirm, reject, or remove an ORCID iD; ``detail`` holds the scheme and
# value, the statuses the identifier's), append-only like curation_event so that every change
# can be traced and undone.
person_event = Table(
    "person_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16)),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
    Column("detail", JsonType),
    CheckConstraint(_in("action", PERSON_ACTIONS), name="action"),
)
Index(None, person_event.c.person_id)
Index(None, person_event.c.at)

# One row per merge of two person records. ``journal`` lists every change the merge made, in
# order, with the values before it, so that undoing the merge restores the ledger exactly.
person_merge = Table(
    "person_merge",
    metadata,
    Column("id", ID, primary_key=True),
    Column("merged_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("survivor_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
    Column("journal", JsonType, nullable=False),
    Column("summary", JsonType, nullable=False),
    Column("undone_at", UtcDateTime),
    Column("undone_by", String(200)),
)
Index(None, person_merge.c.merged_id)
Index(None, person_merge.c.survivor_id)

# --- Accounts (PLT-06) ---------------------------------------------------------------------

ROLES = ("admin", "curator", "manager", "researcher", "viewer")
ACCOUNT_STATUSES = ("active", "disabled")
GRANT_SOURCES = ("local", "provider")

account = Table(
    "account",
    metadata,
    Column("id", ID, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("display_name", String(200), nullable=False),
    Column("email", String(320)),
    Column("password_hash", String(255), nullable=False),  # scrypt; see tessera.auth.passwords
    Column("status", String(16), nullable=False, server_default="active"),
    # The researcher this account belongs to, so that they can review their own suggestions.
    Column("person_id", ID, ForeignKey("person.id", ondelete="SET NULL")),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("password_changed_at", UtcDateTime, nullable=False),
    Column("last_sign_in_at", UtcDateTime),
    CheckConstraint(_in("status", ACCOUNT_STATUSES), name="status"),
)

# A role, optionally limited to the people of one unit or to one person (CUR-06).
role_grant = Table(
    "role_grant",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", ID, ForeignKey("account.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(16), nullable=False),
    Column("scope_type", String(16)),
    Column("scope_id", ID),
    # A manager's group scope covers the groups below it only when this is set; a curator's
    # group scope always does.
    Column("include_below", Boolean, nullable=False, server_default=false()),
    Column("granted_by", String(200), nullable=False),
    Column("granted_at", UtcDateTime, nullable=False),
    # "local": given in Tessera; "provider": from the single sign-on provider's mapping, and
    # replaced at each sign-in (wave 7).
    Column("source", String(16), nullable=False, server_default="local"),
    CheckConstraint(_in("role", ROLES), name="role"),
    CheckConstraint(_in("source", GRANT_SOURCES), name="source"),
    CheckConstraint(
        "(scope_type IS NULL AND scope_id IS NULL) OR "
        "(scope_type IN ('unit', 'person') AND scope_id IS NOT NULL)",
        name="scope",
    ),
)
Index(None, role_grant.c.account_id)

# A group's page: blocks from an approved set (spec: wave-3 slice 4), as a draft that its
# managers edit and the published version everyone sees. ``revision`` counts publications.
group_page = Table(
    "group_page",
    metadata,
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
    Column("draft", JsonType),
    Column("draft_updated_at", UtcDateTime),
    Column("draft_updated_by", String(200)),
    Column("published", JsonType),
    Column("revision", Integer, nullable=False, server_default="0"),
    Column("published_at", UtcDateTime),
    Column("published_by", String(200)),
)

# Every published version of every group page, so any can be looked at and restored.
group_page_revision = Table(
    "group_page_revision",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("blocks", JsonType, nullable=False),
    Column("action", String(20), nullable=False),  # publish | rollback
    Column("actor", String(200), nullable=False),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
    UniqueConstraint("unit_id", "number"),
)

# A job or training opportunity a group posts (LAB-08). Open until ``closes_on`` has passed,
# or until an editor closes it early (``closed_at``).
OPENING_KINDS = ("job", "training", "other")
opening = Table(
    "opening",
    metadata,
    Column("id", ID, primary_key=True),
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("description", Text),
    Column("url", String(500)),
    Column("contact", String(320)),
    Column("posted_on", Date, nullable=False),
    Column("closes_on", Date, nullable=False),
    Column("closed_at", UtcDateTime),
    Column("created_by", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint(_in("kind", OPENING_KINDS), name="kind"),
)
Index(None, opening.c.unit_id)
Index(None, opening.c.closes_on)

# A group's research themes and projects (LAB-07), each linking people (``per_``), grants
# (``grt:``), publications (``pub:``), and studies (``stu:``).
theme = Table(
    "theme",
    metadata,
    Column("id", ID, primary_key=True),
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("summary", Text),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("created_by", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint(_in("kind", ("theme", "project")), name="kind"),
    CheckConstraint(_in("status", ("active", "archived")), name="status"),
)
Index(None, theme.c.unit_id)

theme_link = Table(
    "theme_link",
    metadata,
    Column("theme_id", ID, ForeignKey("theme.id", ondelete="CASCADE"), primary_key=True),
    Column("ref", ENTITY_REF, primary_key=True),
    Column("added_by", String(200), nullable=False),
    Column("added_at", UtcDateTime, nullable=False),
)

# When a group's editors last went through its weekly inbox (LAB-06).
group_inbox = Table(
    "group_inbox",
    metadata,
    Column("unit_id", ID, ForeignKey("unit.id", ondelete="CASCADE"), primary_key=True),
    Column("reviewed_at", UtcDateTime, nullable=False),
    Column("reviewed_by", String(200), nullable=False),
)

# Saved cohorts for Insight (INT-01): whose work a figure counts. ``definition`` is a cohort
# specification (see tessera.insight.cohorts): the organization, a group, picked people, or a
# people search. Shared with everyone who can read Insight.
cohort = Table(
    "cohort",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("description", Text),
    Column("definition", JsonType, nullable=False),
    Column("created_by", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# A person's saved searches (DIS-10): what they searched for, and the matches they have already
# seen, so the list can show what is new. ``owner`` is the account's ID, or "operator" in
# single-operator mode.
saved_search = Table(
    "saved_search",
    metadata,
    Column("id", ID, primary_key=True),
    Column("owner", String(200), nullable=False),
    Column("name", String(200), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("q", Text, nullable=False, server_default=""),
    Column("filters", JsonType, nullable=False),
    Column("seen", JsonType, nullable=False),
    Column("seen_at", UtcDateTime, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)
Index(None, saved_search.c.owner)

# Reports (INT-03): named sets of figures over a cohort and a period, and their snapshots, each
# frozen in ``snapshots/<id>/`` in the data home with the inputs it was computed from.
report = Table(
    "report",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String(200), nullable=False, unique=True),
    Column("description", Text),
    Column("cohort", String(4000), nullable=False),
    Column("start_year", Integer),
    Column("end_year", Integer),
    Column("figures", JsonType, nullable=False),
    Column("created_by", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

report_snapshot = Table(
    "report_snapshot",
    metadata,
    Column("id", ID, primary_key=True),
    Column("report_id", ID, ForeignKey("report.id", ondelete="SET NULL")),
    Column("name", String(200), nullable=False),
    Column("note", Text),
    Column("taken_by", String(200), nullable=False),
    Column("taken_at", UtcDateTime, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("path", String(500), nullable=False),
    Column("summary", JsonType, nullable=False),
)
Index(None, report_snapshot.c.report_id)

# Accounts that sign in through an OpenID Connect provider: the provider's subject identifier
# for each, unique per issuer (wave 7).
account_identity = Table(
    "account_identity",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", ID, ForeignKey("account.id", ondelete="CASCADE"), nullable=False),
    Column("issuer", String(500), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("email", String(320)),
    Column("created_at", UtcDateTime, nullable=False),
    Column("last_sign_in_at", UtcDateTime),
    UniqueConstraint("issuer", "subject"),
)
Index(None, account_identity.c.account_id)

# A single sign-on under way: what its callback must find. Only a hash of the state is kept,
# and each is used once and expires in minutes.
sign_in_attempt = Table(
    "sign_in_attempt",
    metadata,
    Column("state_hash", String(64), primary_key=True),
    Column("nonce", String(64), nullable=False),
    Column("verifier", String(128), nullable=False),
    Column("next_path", String(500), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
)

# Signed-in browsers. Only a hash of the session token is stored.
auth_session = Table(
    "auth_session",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("account_id", ID, ForeignKey("account.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("revoked_at", UtcDateTime),
    Column("user_agent", String(300)),
)
Index(None, auth_session.c.account_id)

# Links one automatic run accepted (CUR-05), so that the whole run can be undone together.
auto_accept_batch = Table(
    "auto_accept_batch",
    metadata,
    Column("id", ID, primary_key=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("threshold", Float, nullable=False),
    Column("precision", Float),
    Column("method", String(60), nullable=False),
    Column("assertion_ids", JsonType, nullable=False),
    Column("undone_at", UtcDateTime),
    Column("undone_by", String(200)),
)

ingest_run = Table(
    "ingest_run",
    metadata,
    Column("id", ID, primary_key=True),
    Column("source", String(40), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("finished_at", UtcDateTime),
    Column("params", JsonType, nullable=False),
    Column("counts", JsonType, nullable=False),
    Column("error", Text),
    CheckConstraint(_in("status", RUN_STATUSES), name="status"),
)
Index(None, ingest_run.c.source, ingest_run.c.started_at)

source_checkpoint = Table(
    "source_checkpoint",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("scope", String(200), primary_key=True),
    Column("watermark", String(64), nullable=False),
    Column("run_id", ID),
    Column("updated_at", UtcDateTime, nullable=False),
)

setting = Table(
    "setting",
    metadata,
    Column("key", String(120), primary_key=True),
    Column("value", JsonType, nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# Opportunities an organization tracks itself (INT-12): foundation calls, internal pilot funds,
# limited submissions, and anything else not on Grants.gov, whose opportunities are in the lake.
# ``source`` and ``external_id`` let an import update what it brought in before.
OPPORTUNITY_KINDS = ("foundation", "internal", "federal", "state", "industry", "other")
opportunity = Table(
    "opportunity",
    metadata,
    Column("id", ID, primary_key=True),
    Column("source", String(200), nullable=False),
    Column("external_id", String(200)),
    Column("kind", String(20), nullable=False, server_default="foundation"),
    Column("number", String(100)),
    Column("title", String(500), nullable=False),
    Column("sponsor", String(300)),
    Column("description", Text),
    Column("url", String(1000)),
    Column("posted_on", Date),
    Column("closes_on", Date),
    Column("internal_deadline", Date),
    Column("award_floor", BigInteger),
    Column("award_ceiling", BigInteger),
    Column("limited_submission", Boolean, nullable=False, server_default=false()),
    Column("eligibility_notes", Text),
    Column("applicant_types", JsonType, nullable=False),
    Column("created_by", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("archived_at", UtcDateTime),
    UniqueConstraint("source", "external_id"),
    CheckConstraint(_in("kind", OPPORTUNITY_KINDS), name="kind"),
)
Index(None, opportunity.c.closes_on)

# What someone does with an opportunity for themselves (INT-09): watch it, or dismiss it from
# their own lists. ``owner`` is the account's ID, or "operator" without accounts.
INTEREST_STATES = ("watching", "dismissed")
opportunity_interest = Table(
    "opportunity_interest",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner", String(200), nullable=False),
    Column("opportunity", String(80), nullable=False),
    Column("state", String(12), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("owner", "opportunity"),
    CheckConstraint(_in("state", INTEREST_STATES), name="state"),
)
Index(None, opportunity_interest.c.owner)

# Teams (INT-10): people assembled for an opportunity, or for topics someone chooses. A team is
# its owner's (an account's ID, or "operator"), and visible to its members, the people it is
# shared with (person IDs), and admins. ``concepts`` are the topics it needs.
TEAM_ROLES = ("lead", "co_investigator", "collaborator", "core", "other")
team = Table(
    "team",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("purpose", Text),
    Column("opportunity", String(80)),
    Column("concepts", JsonType, nullable=False),
    Column("notes", Text),
    Column("shared_with", JsonType, nullable=False),
    Column("owner", String(200), nullable=False),
    Column("owner_name", String(200), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)
Index(None, team.c.owner)
team_member = Table(
    "team_member",
    metadata,
    Column("team_id", ID, ForeignKey("team.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(20), nullable=False, server_default="collaborator"),
    Column("note", String(500)),
    Column("added_by", String(200), nullable=False),
    Column("added_at", UtcDateTime, nullable=False),
    CheckConstraint(_in("role", TEAM_ROLES), name="role"),
)
Index(None, team_member.c.person_id)

# Every exchange with the optional assistant (wave 5): who asked, the provider and model, the
# prompt's version, the messages and tool calls (with the records they returned), the answer,
# its citations, tokens, time, and outcome. Kept ``[ai] log_days``.
AI_STATUSES = ("answered", "declined", "failed", "limited", "stopped")
ai_exchange = Table(
    "ai_exchange",
    metadata,
    Column("id", ID, primary_key=True),
    Column("owner", String(200), nullable=False),
    Column("asked_by", String(200), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("provider", String(20), nullable=False),
    Column("model", String(200), nullable=False),
    Column("prompt_version", String(40), nullable=False),
    Column("question", Text),
    Column("request", JsonType, nullable=False),
    Column("answer", Text),
    Column("citations", JsonType, nullable=False),
    Column("usage", JsonType, nullable=False),
    Column("status", String(16), nullable=False),
    Column("error", Text),
    Column("started_at", UtcDateTime, nullable=False),
    Column("seconds", Float),
    CheckConstraint(_in("status", AI_STATUSES), name="status"),
)
Index(None, ai_exchange.c.owner, ai_exchange.c.started_at)
Index(None, ai_exchange.c.started_at)

# Plain-language summaries of a person's or a group's work (wave 5): drafted by the optional
# assistant from confirmed records, shown only once a reviewer approves them, and never while the
# assistant is off. ``draft`` is what the model wrote; ``text`` is what was approved (the draft,
# or the reviewer's edit of it). ``inputs`` holds the counts and references it was drafted from,
# and ``inputs_digest`` their SHA-256, so a later draft can say whether anything changed.
SUMMARY_SUBJECTS = ("person", "group")
SUMMARY_STATUSES = ("draft", "approved", "rejected", "replaced")
ai_summary = Table(
    "ai_summary",
    metadata,
    Column("id", ID, primary_key=True),
    Column("subject_type", String(10), nullable=False),
    Column("subject_id", ID, nullable=False),
    Column("status", String(12), nullable=False),
    Column("draft", Text, nullable=False),
    Column("text", Text),
    Column("provider", String(20), nullable=False),
    Column("model", String(200), nullable=False),
    Column("prompt_version", String(40), nullable=False),
    Column("inputs", JsonType, nullable=False),
    Column("inputs_digest", String(64), nullable=False),
    Column("exchange_id", ID),
    Column("requested_by", String(200), nullable=False),
    Column("requested_at", UtcDateTime, nullable=False),
    Column("decided_by", String(200)),
    Column("decided_at", UtcDateTime),
    CheckConstraint(_in("subject_type", SUMMARY_SUBJECTS), name="subject_type"),
    CheckConstraint(_in("status", SUMMARY_STATUSES), name="status"),
)
Index(None, ai_summary.c.subject_type, ai_summary.c.subject_id, ai_summary.c.status)

# Every change to a summary, append-only: drafted, edited, approved, rejected, replaced.
summary_event = Table(
    "summary_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("summary_id", ID, ForeignKey("ai_summary.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
)
Index(None, summary_event.c.summary_id)

# The sections of a profile that a researcher keeps (wave 6): one row per entry, its fields as
# the section defines them (see tessera.profiles.sections). ``source`` is "manual" for entries
# made by hand; an import would set its own name and ``source_id``, its key for the entry, so it
# finds the entry again instead of adding a second one.
PROFILE_SECTIONS = (
    "overview", "interests", "position", "education", "honor", "membership", "teaching", "talk",
    "link", "featured",
)  # fmt: skip
profile_entry = Table(
    "profile_entry",
    metadata,
    Column("id", ID, primary_key=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("section", String(20), nullable=False),
    Column("fields", JsonType, nullable=False),
    Column("position", Integer, nullable=False),
    Column("source", String(40), nullable=False, server_default="manual"),
    Column("source_id", String(300)),
    # Left off the public page although its section is shown there (the person's choice).
    Column("hidden", Boolean, nullable=False, server_default=false()),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
    CheckConstraint(_in("section", PROFILE_SECTIONS), name="section"),
    UniqueConstraint("person_id", "source", "source_id"),
)
Index(None, profile_entry.c.person_id, profile_entry.c.section)

# Every change to a profile, append-only: the entry before and after, who made the change, and
# in what capacity (the person themselves, a curator, an admin, or someone acting for them).
profile_event = Table(
    "profile_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("entry_id", ID),
    Column("section", String(20), nullable=False),
    Column("action", String(20), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("capacity", String(20), nullable=False),
    Column("before", JsonType),
    Column("after", JsonType),
    Column("at", UtcDateTime, nullable=False),
)
Index(None, profile_event.c.person_id, profile_event.c.at)

# Accounts a person names to keep their profile, CVs, and biosketches for them (wave 6), such as
# an assistant. Acting for someone covers only those; never their authorship or identifiers.
profile_delegate = Table(
    "profile_delegate",
    metadata,
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
    Column("account_id", ID, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True),
    Column("granted_by", String(200), nullable=False),
    Column("granted_at", UtcDateTime, nullable=False),
)
Index(None, profile_delegate.c.account_id)

# What a person's public page shows: the sections they left off it (every other one is shown, so
# a section added in a later version appears unless they hide it). Entries can be hidden one by
# one with profile_entry.hidden.
profile_public = Table(
    "profile_public",
    metadata,
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
    Column("hidden_sections", JsonType, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
)

# A person's CVs and NIH biosketches (wave 6): named versions, each a ``spec`` of which sections
# and entries it includes, in what order, with its options; built from the profile and confirmed
# records whenever it is shown, so it stays current. ``form`` names the NIH form a biosketch
# was built for.
CV_KINDS = ("cv", "nih_biosketch")
cv_document = Table(
    "cv_document",
    metadata,
    Column("id", ID, primary_key=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("name", String(200), nullable=False),
    Column("spec", JsonType, nullable=False),
    Column("form", String(60)),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("updated_by", String(200), nullable=False),
    CheckConstraint(_in("kind", CV_KINDS), name="kind"),
)
Index(None, cv_document.c.person_id)
