"""RFC 9457 problem details for every API error."""

from __future__ import annotations

import logging
import sqlite3
from http import HTTPStatus
from typing import Any

import duckdb
import sqlalchemy.exc
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tessera.core.errors import LedgerError, TesseraError
from tessera.jobs.lock import JobLockedError
from tessera.search.query import SearchUnavailable

PROBLEM_JSON = "application/problem+json"
log = logging.getLogger("tessera.api")


def problem(status: int, detail: str | None = None, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        response = problem(exc.status_code, detail, instance=request.url.path)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": list(err.get("loc", ())), "message": err.get("msg", "")}
            for err in exc.errors()
        ]
        return problem(422, "The request is not valid.", errors=errors, instance=request.url.path)

    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, exc: LedgerError) -> JSONResponse:
        log.error("ledger unavailable: %s", exc)
        return problem(503, "The ledger database is unavailable.", instance=request.url.path)

    @app.exception_handler(SearchUnavailable)
    async def search_unavailable(request: Request, exc: SearchUnavailable) -> JSONResponse:
        return problem(503, str(exc), instance=request.url.path)

    @app.exception_handler(JobLockedError)
    async def job_locked(request: Request, exc: JobLockedError) -> JSONResponse:
        return problem(409, str(exc), instance=request.url.path)

    @app.exception_handler(TesseraError)
    async def tessera_error(request: Request, exc: TesseraError) -> JSONResponse:
        return problem(400, str(exc), instance=request.url.path)

    @app.exception_handler(sqlalchemy.exc.OperationalError)
    @app.exception_handler(sqlalchemy.exc.InterfaceError)
    async def ledger_unreachable(request: Request, exc: Exception) -> JSONResponse:
        # The ledger's server went away (or cannot be reached): say so, not an unexplained 500.
        log.error("the ledger is unavailable on %s: %s", request.url.path, exc)
        return problem(503, "The ledger database is unavailable. An admin can check it with "
                       "tessera doctor.", instance=request.url.path)  # fmt: skip

    @app.exception_handler(sqlite3.DatabaseError)
    async def index_unreadable(request: Request, exc: sqlite3.DatabaseError) -> JSONResponse:
        # The search index is the one SQLite file read directly (the ledger's errors arrive
        # wrapped by SQLAlchemy, as LedgerError or its own).
        if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
            return problem(503, "The search index is busy; try again in a moment.",
                           instance=request.url.path)  # fmt: skip
        log.error("the search index cannot be read: %s", exc)
        return problem(
            503,
            "The search index cannot be read. An admin can rebuild it: tessera build search.",
            instance=request.url.path,
        )

    @app.exception_handler(duckdb.Error)
    async def data_unreadable(request: Request, exc: duckdb.Error) -> JSONResponse:
        if isinstance(exc, duckdb.ProgrammingError):  # a mistake in a query, not damage
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            return problem(500, "An unexpected error occurred.", instance=request.url.path)
        log.error("a data file cannot be read on %s: %s", request.url.path, exc)
        return problem(
            503,
            "A data file cannot be read. An admin can find which, and how to fix it, with "
            "tessera doctor.",
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(500, "An unexpected error occurred.", instance=request.url.path)
