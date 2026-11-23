"""Maps errors to the contract ``ErrorResponse``."""

from __future__ import annotations

import logging

import fastapi
from fastapi import exceptions as fastapi_exceptions
from fastapi import responses
from starlette import exceptions as starlette_exceptions

from joseph_execution.domain import errors
from joseph_execution.domain import tracing

_LOG = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "VALIDATION_FAILED": 422,
    "INVALID_TICK_SIZE": 422,
    "INVALID_LOT_SIZE": 422,
    "MIN_NOTIONAL": 422,
    "UNSUPPORTED_ORDER_TYPE": 422,
    "UNSUPPORTED": 422,
    "NOT_FOUND": 404,
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "AUTH_SCOPE_MISSING": 403,
    "STEP_UP_REQUIRED": 403,
    "LIVE_TRADING_DISABLED": 403,
    "LIVE_NOT_ARMED": 403,
    "TRADE_MODE_DISABLED": 403,
    "CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "DUPLICATE_INTENT": 409,
    "ORDER_UNKNOWN": 409,
    "POSITION_UNKNOWN": 409,
    "KILL_SWITCH_ENGAGED": 409,
    "RISK_LIMIT": 409,
    "INSUFFICIENT_MARGIN": 409,
    "PREVIEW_REQUIRED": 400,
    "PREVIEW_EXPIRED": 400,
    "MARKET_STALE": 503,
    "MISSING_DATA": 503,
    "VENUE_UNAVAILABLE": 503,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
}


def body(
    code: str,
    message: str,
    retryable: bool = False,
    field_errors: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Builds an ``ErrorResponse``.

    Args:
        code: Contract error code.
        message: Message.
        retryable: Retry hint.
        field_errors: Field errors.

    Returns:
        Contract error body.
    """
    return {
        "code": code,
        "message": message,
        "trace_id": tracing.current_trace_id(),
        "retryable": retryable,
        "field_errors": field_errors or [],
        "source_status": None,
    }


def install(app: fastapi.FastAPI) -> None:
    """Registers exception handlers.

    Args:
        app: Application.
    """

    @app.exception_handler(errors.ExecutionError)
    async def _execution_error(
        request: fastapi.Request, exc: errors.ExecutionError
    ) -> responses.JSONResponse:
        """Maps domain errors.

        Args:
            request: Request.
            exc: Error.

        Returns:
            JSON response.
        """
        del request
        return responses.JSONResponse(
            body(exc.code, exc.message, exc.retryable),
            status_code=STATUS_BY_CODE.get(exc.code, 400),
        )

    @app.exception_handler(fastapi_exceptions.RequestValidationError)
    async def _validation_error(
        request: fastapi.Request,
        exc: fastapi_exceptions.RequestValidationError,
    ) -> responses.JSONResponse:
        """Maps request validation errors.

        Args:
            request: Request.
            exc: Error.

        Returns:
            JSON response.
        """
        del request
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "message": str(err.get("msg", "invalid")),
            }
            for err in exc.errors()
        ]
        return responses.JSONResponse(
            body(
                "VALIDATION_FAILED",
                "request validation failed",
                field_errors=fields,
            ),
            status_code=422,
        )

    @app.exception_handler(starlette_exceptions.HTTPException)
    async def _http_error(
        request: fastapi.Request, exc: starlette_exceptions.HTTPException
    ) -> responses.JSONResponse:
        """Maps framework HTTP errors.

        Args:
            request: Request.
            exc: Error.

        Returns:
            JSON response.
        """
        del request
        code = {
            404: "NOT_FOUND",
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            405: "VALIDATION_FAILED",
        }.get(exc.status_code, "INTERNAL")
        return responses.JSONResponse(
            body(code, str(exc.detail)), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def _unexpected(
        request: fastapi.Request, exc: Exception
    ) -> responses.JSONResponse:
        """Hides internals of unexpected errors.

        Args:
            request: Request.
            exc: Error.

        Returns:
            JSON response.
        """
        del request
        _LOG.error("unhandled %s", type(exc).__name__, exc_info=exc)
        return responses.JSONResponse(
            body("INTERNAL", "internal error"), status_code=500
        )
