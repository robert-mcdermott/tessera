"""The stable API's contract (PLT-17): what each stable route accepts and what it answers with,
recorded in ``tests/api_contract.json``, and the comparison that finds a breaking change.

A route's parameters and request body come from the OpenAPI document. What it answers with is
recorded from the demonstration organization: every stable ``GET`` is called, and each field of
its answer is kept by its path (``data.names[].family``) with the JSON types seen there. Keys
that are data rather than names (years, identifiers, references) are kept as ``*``.

Breaking, for a client: a route, a parameter, a field of a request body, or a field of an answer
taken away; a type changed; a parameter or body field becoming required, or a new required one.
Adding is never breaking.
"""

from __future__ import annotations

import re
from typing import Any

METHODS = ("get", "post", "put", "patch", "delete")
DATA_KEY = re.compile(
    r"^(\d[\d\-]*|[a-z]{2,5}_[0-9A-Z]{20,}|[a-z]{2,5}:.+|[A-Z]\d{2}[A-Z]{2}\d+.*|NCT\d+|\d{4}-.*)$"
)


def stable_operations(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in METHODS and operation.get("x-stability") == "stable":
                out[f"{method.upper()} {path}"] = operation
    return out


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        schema = spec["components"]["schemas"][name]
    return schema


def type_of(spec: dict[str, Any], schema: dict[str, Any]) -> str:
    """A simple name for a schema's type: ``string``, ``integer``, ``array[string]``, …;
    a value that may be null is named for what it is otherwise."""
    schema = _resolve(spec, schema)
    if "anyOf" in schema or "oneOf" in schema:
        kinds = sorted(
            {type_of(spec, s) for s in schema.get("anyOf", schema.get("oneOf", []))} - {"null"}
        )
        return "|".join(kinds) or "null"
    kind = schema.get("type")
    if kind == "array":
        return f"array[{type_of(spec, schema.get('items', {}))}]"
    if kind is None:
        return "object" if "properties" in schema else "any"
    if kind == "number":
        return "number"
    return str(kind)


def parameters(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for p in operation.get("parameters", []):
        if p["in"] == "query":
            out[p["name"]] = {
                "type": type_of(spec, p.get("schema", {})),
                "required": bool(p.get("required")),
            }
    return out


def body(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    content = operation.get("requestBody", {}).get("content", {})
    if not content:
        return None
    media = next(iter(content))
    schema = _resolve(spec, content[media].get("schema", {}))
    for choice in schema.get("anyOf", []):  # an optional body: what it is when given
        if choice.get("type") != "null":
            schema = _resolve(spec, choice)
    required = set(schema.get("required", []))
    fields = {
        name: {"type": type_of(spec, prop), "required": name in required}
        for name, prop in schema.get("properties", {}).items()
    }
    return {"media": media, "fields": fields}


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def shape(
    value: Any, path: str = "", out: dict[str, set[str]] | None = None
) -> dict[str, set[str]]:
    """Every field of a JSON value, by path, with the JSON types seen there."""
    out = {} if out is None else out
    if path:
        out.setdefault(path, set()).add(_kind(value))
    if isinstance(value, dict):
        for key, item in value.items():
            name = "*" if DATA_KEY.match(str(key)) else key
            shape(item, f"{path}.{name}" if path else name, out)
    elif isinstance(value, list):
        for item in value:
            shape(item, f"{path}[]", out)
    return out


def compare(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What in ``new`` would break a client written against ``old``."""
    problems = []
    for key, was in sorted(old.items()):
        now = new.get(key)
        if now is None:
            problems.append(f"{key}: the route is gone")
            continue
        for name, p in was["query"].items():
            q = now["query"].get(name)
            if q is None:
                problems.append(f"{key}: parameter {name!r} is gone")
            elif q["type"] != p["type"]:
                problems.append(f"{key}: parameter {name!r} was {p['type']}, now {q['type']}")
            elif q["required"] and not p["required"]:
                problems.append(f"{key}: parameter {name!r} is now required")
        for name, q in now["query"].items():
            if name not in was["query"] and q["required"]:
                problems.append(f"{key}: a new required parameter {name!r}")
        problems += _compare_body(key, was.get("body"), now.get("body"))
        problems += _compare_response(key, was.get("response"), now.get("response"))
    return problems


def _compare_body(key: str, was: dict[str, Any] | None, now: dict[str, Any] | None) -> list[str]:
    if was is None:
        if now is not None and any(f["required"] for f in now["fields"].values()):
            return [f"{key}: a request body is now required"]
        return []
    if now is None:
        return [f"{key}: the request body is no longer read"]
    if now["media"] != was["media"]:
        return [f"{key}: the request body was {was['media']}, now {now['media']}"]
    problems = []
    for name, f in was["fields"].items():
        g = now["fields"].get(name)
        if g is None:
            problems.append(f"{key}: body field {name!r} is gone")
        elif g["type"] != f["type"]:
            problems.append(f"{key}: body field {name!r} was {f['type']}, now {g['type']}")
        elif g["required"] and not f["required"]:
            problems.append(f"{key}: body field {name!r} is now required")
    for name, g in now["fields"].items():
        if name not in was["fields"] and g["required"]:
            problems.append(f"{key}: a new required body field {name!r}")
    return problems


def _compare_response(
    key: str, was: dict[str, Any] | None, now: dict[str, Any] | None
) -> list[str]:
    if was is None:
        return []
    if now is None:
        return [f"{key}: its answer was not recorded this time"]
    if now["media"] != was["media"]:
        return [f"{key}: it answered with {was['media']}, now {now['media']}"]
    problems = []
    for path, kinds in was.get("shape", {}).items():
        seen = now.get("shape", {}).get(path)
        if seen is None:
            problems.append(f"{key}: field {path!r} is gone from the answer")
            continue
        before, after = set(kinds) - {"null"}, set(seen) - {"null"}
        if before and after and not after <= before:
            problems.append(
                f"{key}: field {path!r} was {'|'.join(sorted(before))}, "
                f"now {'|'.join(sorted(after))}"
            )
    return problems


def merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The contract with what ``new`` adds. Nothing in ``old`` is dropped or changed (except a
    field whose type was unknown, seen only as null), so that recording again never hides a
    breaking change: taking something out, or changing it, is an edit by hand."""
    out = {k: dict(v) for k, v in old.items()}
    for key, now in new.items():
        if key not in out:
            out[key] = now
            continue
        was = out[key]
        was["query"] = {**now["query"], **was["query"]}
        if was.get("body") is None:
            was["body"] = now.get("body")
        elif now.get("body") is not None:
            fields = {**now["body"]["fields"], **was["body"]["fields"]}
            was["body"] = {**was["body"], "fields": fields}
        if was.get("response") is None:
            was["response"] = now.get("response")
        elif now.get("response") is not None and "shape" in was["response"]:
            kept = dict(was["response"]["shape"])
            for path, kinds in now["response"].get("shape", {}).items():
                if path not in kept or kept[path] == ["null"]:
                    kept[path] = kinds
            was["response"] = {**was["response"], "shape": dict(sorted(kept.items()))}
    return dict(sorted(out.items()))


__all__ = ["body", "compare", "merge", "parameters", "shape", "stable_operations", "type_of"]
