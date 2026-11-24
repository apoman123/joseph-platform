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

# HTTP status per contract error code. A ``JosephError`` whose code is not
# listed here is answered with 400.
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
        Trace id the ``trace`` middleware stored, or ``"unknown"`` when the
        request did not pass through it.
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
        code: Contract error code, a key of ``STATUS_BY_CODE``.
        message: Plain-language, actionable message.
        trace_id: Request trace id.
        retryable: Whether retrying the same request may succeed.
        field_errors: ``(field, message)`` pairs for invalid inputs.
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
    """Renders a ``JosephError`` as its contract ``ErrorResponse``.

    The status comes from ``STATUS_BY_CODE`` (400 for an unlisted code). An
    error with an ``issues`` list (``StrategyValidationError``) fills
    ``field_errors`` from its ``path`` and ``message`` entries.

    Args:
        request: Request that raised the error.
        exc: The raised error; always a ``JosephError``.

    Returns:
        JSON error response.
    """
    if not isinstance(exc, errors.JosephError):
        raise AssertionError("registered for JosephError only")
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
    """Renders a FastAPI request validation error as ``VALIDATION_FAILED``.

    Args:
        request: Request whose parameters or body failed validation.
        exc: The raised error; always a ``RequestValidationError``.

    Returns:
        422 response with one field error per failed location, the field
        named by its dotted ``loc`` path (for example ``query.limit``).
    """
    if not isinstance(exc, fastapi_exceptions.RequestValidationError):
        raise AssertionError("registered for RequestValidationError only")
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
    """Renders a framework HTTP error (unknown route, wrong method).

    The original status is kept. 401, 403 and 404 map to their contract
    codes; any other status (for example 405) is reported as
    ``VALIDATION_FAILED``.

    Args:
        request: Request that failed.
        exc: The raised error; always a Starlette ``HTTPException``.

    Returns:
        Contract error response, never retryable.
    """
    if not isinstance(exc, starlette_exceptions.HTTPException):
        raise AssertionError("registered for HTTPException only")
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
    """Renders an unexpected exception without leaking details.

    Only the exception type and the trace id are logged; the message and
    the traceback are not.

    Args:
        request: Request that failed.
        exc: The unexpected exception.

    Returns:
        500 ``INTERNAL`` response, marked retryable.
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
        app: FastAPI application to configure.
    """
    app.add_exception_handler(errors.JosephError, _domain_handler)
    app.add_exception_handler(
        fastapi_exceptions.RequestValidationError, _validation_handler
    )
    app.add_exception_handler(starlette_exceptions.HTTPException, _http_handler)
    app.add_exception_handler(Exception, _internal_handler)
