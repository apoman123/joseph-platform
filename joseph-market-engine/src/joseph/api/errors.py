# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Mapping of domain errors to the contract ``ErrorResponse``.

Unexpected exceptions return ``INTERNAL`` with a trace id and no internal
details, so stack traces and venue payloads never reach clients.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import fastapi
from fastapi import exceptions as fastapi_exceptions
from fastapi import responses
from starlette import exceptions as starlette_exceptions

from joseph.domain import errors

_LOG = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "VALIDATION_FAILED": 422,
    "NOT_FOUND": 404,
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "CONFLICT": 409,
    "UNSUPPORTED": 422,
    "MISSING_DATA": 422,
    "MARKET_STALE": 503,
    "VENUE_UNAVAILABLE": 503,
    "RATE_LIMITED": 429,
    "TRADE_MODE_DISABLED": 409,
    "INTERNAL": 500,
}


def trace_id_of(request: fastapi.Request) -> str:
    """Returns the request's trace id.

    Args:
        request: Current request.

    Returns:
        Trace id (``"unknown"`` outside the middleware).
    """
    return str(getattr(request.state, "trace_id", "unknown"))


def error_body(
    code: str,
    message: str,
    trace_id: str,
    *,
    retryable: bool,
    field_errors: Sequence[tuple[str, str]] = (),
    source_status: str | None = None,
) -> dict[str, object]:
    """Builds a contract ``ErrorResponse`` body.

    Args:
        code: Contract error code.
        message: Plain-language, actionable message.
        trace_id: Request trace id.
        retryable: Whether retrying may succeed.
        field_errors: ``(field, message)`` pairs.
        source_status: Optional upstream status text.

    Returns:
        JSON-compatible body.
    """
    return {
        "code": code,
        "message": message,
        "trace_id": trace_id,
        "retryable": retryable,
        "field_errors": [
            {"field": field, "message": text} for field, text in field_errors
        ],
        "source_status": source_status,
    }


async def _domain_handler(
    request: fastapi.Request, exc: Exception
) -> responses.JSONResponse:
    """Handles ``JosephError``.

    Args:
        request: Request.
        exc: Raised error.

    Returns:
        JSON error response.
    """
    assert isinstance(exc, errors.JosephError)
    status = STATUS_BY_CODE.get(exc.code, 400)
    body = error_body(
        exc.code, exc.message, trace_id_of(request), retryable=exc.retryable
    )
    issues = getattr(exc, "issues", None)
    if isinstance(issues, list):
        body["field_errors"] = [
            {"field": str(i.get("path", "")), "message": str(i.get("message"))}
            for i in issues
            if isinstance(i, dict)
        ]
    return responses.JSONResponse(status_code=status, content=body)


async def _validation_handler(
    request: fastapi.Request, exc: Exception
) -> responses.JSONResponse:
    """Handles request validation errors.

    Args:
        request: Request.
        exc: Validation error.

    Returns:
        422 response with field errors.
    """
    assert isinstance(exc, fastapi_exceptions.RequestValidationError)
    fields = [
        (".".join(str(p) for p in err.get("loc", ())), str(err.get("msg")))
        for err in exc.errors()
    ]
    return responses.JSONResponse(
        status_code=422,
        content=error_body(
            "VALIDATION_FAILED",
            "The request is invalid.",
            trace_id_of(request),
            retryable=False,
            field_errors=fields,
        ),
    )


async def _http_handler(
    request: fastapi.Request, exc: Exception
) -> responses.JSONResponse:
    """Handles framework HTTP errors (404 routes, 405 methods).

    Args:
        request: Request.
        exc: HTTP exception.

    Returns:
        Contract error response.
    """
    assert isinstance(exc, starlette_exceptions.HTTPException)
    code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(
        exc.status_code, "VALIDATION_FAILED"
    )
    return responses.JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            code, str(exc.detail), trace_id_of(request), retryable=False
        ),
    )


async def _internal_handler(
    request: fastapi.Request, exc: Exception
) -> responses.JSONResponse:
    """Handles unexpected exceptions without leaking details.

    Args:
        request: Request.
        exc: Exception.

    Returns:
        500 response.
    """
    _LOG.error(
        "unhandled %s (trace %s)", type(exc).__name__, trace_id_of(request)
    )
    return responses.JSONResponse(
        status_code=500,
        content=error_body(
            "INTERNAL",
            "Something went wrong on our side.",
            trace_id_of(request),
            retryable=True,
        ),
    )


def install(app: fastapi.FastAPI) -> None:
    """Registers every error handler on an app.

    Args:
        app: FastAPI application.
    """
    app.add_exception_handler(errors.JosephError, _domain_handler)
    app.add_exception_handler(
        fastapi_exceptions.RequestValidationError, _validation_handler
    )
    app.add_exception_handler(starlette_exceptions.HTTPException, _http_handler)
    app.add_exception_handler(Exception, _internal_handler)
