# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Minimal error mapping for router tests.

Production mapping to the contract ``ErrorResponse`` is owned by the API
layer; tests use this equivalent so routers can be exercised alone.
"""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph.domain import errors

STATUS_BY_CODE = {
    "VALIDATION_FAILED": 422,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "FORBIDDEN": 403,
    "UNAUTHENTICATED": 401,
    "TRADE_MODE_DISABLED": 409,
    "UNSUPPORTED": 400,
}
"""HTTP status per error code."""


async def _handle_joseph_error(
    request: fastapi.Request, exc: Exception
) -> responses.JSONResponse:
    """Renders a domain error as a contract-shaped JSON error.

    Args:
        request: Request (unused).
        exc: Raised error; the handler is registered for ``JosephError``.

    Returns:
        JSON error response; codes missing from ``STATUS_BY_CODE`` map to
        HTTP 500.
    """
    del request  # Unused.
    if not isinstance(exc, errors.JosephError):
        raise AssertionError("the handler is registered for JosephError")
    body: dict[str, object] = {
        "code": exc.code,
        "message": exc.message,
        "trace_id": "test",
        "retryable": exc.retryable,
        "field_errors": [],
    }
    issues = getattr(exc, "issues", None)
    if issues:
        body["field_errors"] = [
            {"field": issue["path"], "message": issue["message"]}
            for issue in issues
        ]
    return responses.JSONResponse(
        body, status_code=STATUS_BY_CODE.get(exc.code, 500)
    )


def install_error_handler(app: fastapi.FastAPI) -> None:
    """Maps ``JosephError`` to a contract-shaped JSON error.

    Args:
        app: Application under test.
    """
    app.add_exception_handler(errors.JosephError, _handle_joseph_error)
