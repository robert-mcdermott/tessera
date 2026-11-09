"""What the API promises (PLT-17): each route is stable or experimental, and a retiring route
says so before it goes.

**Stable** routes change only by adding to them (a new optional parameter, a new field in a
response, a new route) until a major version. **Experimental** routes serve views that are
still settling, or depend on outside services; they may change in a minor release, which the
changelog says. Both are marked in the OpenAPI document as ``x-stability``.

**Deprecated** routes keep working until their sunset, at least one minor release after the one
that announced it, and answer with ``Deprecation`` (RFC 9745) and ``Sunset`` (RFC 8594) headers
and a ``Link`` to what replaces them.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import format_datetime
from typing import Any

from fastapi import FastAPI, Request, Response

STABLE = "stable"
METHODS = {"get", "post", "put", "patch", "delete"}
EXPERIMENTAL = "experimental"

# Experimental routes, by path (a pattern matched from the start). Everything else under
# /api/v1 is stable.
EXPERIMENTAL_PATHS: tuple[tuple[str, str], ...] = (
    (r"/api/v1/graph/", "the graph views' data, shaped for them and still settling"),
    (r"/api/v1/admin/branding", "the branding editor's data, new in 0.7"),
    (r"/api/v1/operations$", "job metrics for the admin pages"),
    (r"/api/v1/opportunities/[^/]+/fit$", "how an opportunity fits, while its scoring settles"),
    (r"/api/v1/evidence/evaluate$", "the evidence model's evaluation"),
    (r"/api/v1/me/public-page\.css$", "a style sheet for the public page's preview"),
    (r"/api/v1/ai/", "the optional assistant, which depends on an outside model"),
)


@dataclass(frozen=True)
class Deprecation:
    """A route on its way out: when it was announced, when it stops, and what replaces it."""

    announced: date
    sunset: date
    replacement: str  # a route, or a link to what to use instead
    note: str = ""


# Deprecated routes, by method and path as declared. None at 1.0.
DEPRECATED: dict[tuple[str, str], Deprecation] = {}


def level_of(path: str) -> str:
    for pattern, _why in EXPERIMENTAL_PATHS:
        if re.match(pattern, path):
            return EXPERIMENTAL
    return STABLE


def mark(app: FastAPI) -> None:
    """Put each route's level, and any deprecation, in the OpenAPI document."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        for path, item in schema.get("paths", {}).items():
            if not path.startswith("/api/v1/"):
                continue
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                operation["x-stability"] = level_of(path)
                gone = DEPRECATED.get((method.upper(), path))
                if gone is not None:
                    operation["deprecated"] = True
                    operation["x-sunset"] = gone.sunset.isoformat()
                    operation["x-replacement"] = gone.replacement
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _pattern(path: str) -> re.Pattern[str]:
    """A declared path, such as ``/api/v1/people/{person_id}``, as a pattern for request paths."""
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")


def headers_for(gone: Deprecation) -> dict[str, str]:
    announced = datetime(gone.announced.year, gone.announced.month, gone.announced.day, tzinfo=UTC)
    sunset = datetime(gone.sunset.year, gone.sunset.month, gone.sunset.day, tzinfo=UTC)
    return {
        "Deprecation": f"@{int(announced.timestamp())}",
        "Sunset": format_datetime(sunset, usegmt=True),
        "Link": f'<{gone.replacement}>; rel="successor-version"'
        if gone.replacement.startswith("/")
        else f'<{gone.replacement}>; rel="deprecation"',
    }


def install_deprecations(app: FastAPI) -> None:
    """Answer requests to deprecated routes with the headers that say so (only when there are
    any, so that a release without them pays nothing)."""
    if not DEPRECATED:
        return
    matchers = [
        (method, _pattern(path), headers_for(gone)) for (method, path), gone in DEPRECATED.items()
    ]

    @app.middleware("http")
    async def deprecations(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for method, regex, headers in matchers:
            if request.method == method and regex.match(request.url.path):
                response.headers.update(headers)
                break
        return response


__all__ = [
    "DEPRECATED",
    "EXPERIMENTAL",
    "EXPERIMENTAL_PATHS",
    "STABLE",
    "Deprecation",
    "headers_for",
    "install_deprecations",
    "level_of",
    "mark",
]
