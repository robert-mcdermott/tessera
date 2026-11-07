"""RFC 9457 problem details for every API error."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

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

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(500, "An unexpected error occurred.", instance=request.url.path)
