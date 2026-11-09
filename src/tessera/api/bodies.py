"""Request bodies that are checked by their store rather than by a model, described for the
OpenAPI document from the same lists the stores check against, so the two cannot drift."""

from __future__ import annotations

from typing import Any

from tessera.groups.importer import GROUP_COLUMNS, MAX_ROWS, MEMBERSHIP_COLUMNS
from tessera.ledger.tables import OPPORTUNITY_KINDS
from tessera.opportunities.store import DATES, MONEY, TEXT_LIMITS

TEAM_TEXT = {"name": 200, "purpose": 5000, "opportunity": 80, "notes": 10000}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _body(schema: dict[str, Any], description: str) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "description": description,
            "content": {"application/json": {"schema": schema}},
        }
    }


def _opportunity(required: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        name: _nullable({"type": "string", "maxLength": limit})
        for name, limit in TEXT_LIMITS.items()
    }
    properties["kind"] = {"type": "string", "enum": list(OPPORTUNITY_KINDS)}
    properties |= {name: _nullable({"type": "string", "format": "date"}) for name in DATES}
    properties |= {name: _nullable({"type": "integer", "minimum": 0}) for name in MONEY}
    properties["limited_submission"] = {"type": "boolean"}
    properties["applicant_types"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Grants.gov applicant type codes",
    }
    return {
        "type": "object",
        "title": "OpportunityIn",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _team(*, creating: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        name: _nullable({"type": "string", "maxLength": limit}) for name, limit in TEAM_TEXT.items()
    }
    properties["opportunity"]["description"] = (
        "an opportunity's ID: a new team takes its name and topics from it unless given"
    )
    properties["concepts"] = {
        "type": "array",
        "description": "the topics the team needs",
        "items": {
            "type": "object",
            "properties": {
                "descriptor_ui": {"type": "string", "description": "a MeSH descriptor ID"},
                "descriptor": {"type": "string"},
                "weight": {"type": "number"},
            },
            "required": ["descriptor_ui"],
        },
    }
    if not creating:
        properties["shared_with"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "person IDs it is shared with (its owner, or an admin, only)",
        }
    return {"type": "object", "title": "TeamIn" if creating else "TeamChange",
            "properties": properties}  # fmt: skip


def _opportunity_change() -> dict[str, Any]:
    schema = _opportunity([])
    schema["title"] = "OpportunityChange"
    schema["properties"]["archived"] = {
        "type": "boolean",
        "description": "false restores an archived opportunity",
    }
    return schema


def _group_import() -> dict[str, Any]:
    def rows(columns: dict[str, tuple[str, ...]], what: str) -> dict[str, Any]:
        return {
            "type": "array",
            "maxItems": MAX_ROWS,
            "description": f"{what}, each with these columns (or the other names the Groups "
            "guide lists)",
            "items": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in columns},
            },
        }

    return {
        "title": "GroupImport",
        "type": "object",
        "properties": {
            "groups": rows(GROUP_COLUMNS, "the groups"),
            "memberships": rows(MEMBERSHIP_COLUMNS, "who belongs to which"),
            "source": {"type": "string"},
        },
        "additionalProperties": False,
    }


NEW_OPPORTUNITY = _body(_opportunity(["title", "source"]), "The opportunity's fields.")
OPPORTUNITY_CHANGE = _body(_opportunity_change(), "Only the fields to change.")
NEW_TEAM = _body(_team(creating=True), "A name, or an opportunity to start from.")
GROUP_IMPORT = _body(_group_import(), "Groups and memberships (a list alone is groups).")
TEAM_CHANGE = _body(_team(creating=False), "Only the fields to change.")

__all__ = ["GROUP_IMPORT", "NEW_OPPORTUNITY", "NEW_TEAM", "OPPORTUNITY_CHANGE", "TEAM_CHANGE"]
