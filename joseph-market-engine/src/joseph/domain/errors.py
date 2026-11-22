"""Domain error hierarchy with contract error codes.

Every error carries a machine-readable ``code`` from the ``ErrorCode`` enum in
``common.schema.json`` so API adapters can map it to an ``ErrorResponse``
without string matching.
"""

from __future__ import annotations


class JosephError(Exception):
    """Base class for all expected Joseph domain errors.

    Attributes:
        code: Contract ``ErrorCode`` value, e.g. ``"MISSING_DATA"``.
        retryable: Whether retrying the same request may succeed.
    """

    code: str = "INTERNAL"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        """Initializes the error.

        Args:
            message: Human readable explanation of what went wrong.
            code: Optional override of the class-level error code.
            retryable: Optional override of the class-level retry hint.
        """
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class ValidationFailedError(JosephError):
    """Input failed structural or semantic validation."""

    code = "VALIDATION_FAILED"


class NotFoundError(JosephError):
    """A requested entity does not exist or is not visible to the tenant."""

    code = "NOT_FOUND"


class UnsupportedError(JosephError):
    """The venue, adapter or feature does not support the request.

    Raised instead of silently degrading or fabricating data.
    """

    code = "UNSUPPORTED"


class MissingDataError(JosephError):
    """Required market data is absent, so no value may be inferred."""

    code = "MISSING_DATA"


class MarketStaleError(JosephError):
    """Market data is older than the allowed freshness budget."""

    code = "MARKET_STALE"
    retryable = True


class VenueUnavailableError(JosephError):
    """A venue endpoint is unreachable, erroring or rate limiting."""

    code = "VENUE_UNAVAILABLE"
    retryable = True


class RateLimitedError(VenueUnavailableError):
    """The venue rejected a request because of rate limits (HTTP 429)."""

    code = "RATE_LIMITED"


class ConflictError(JosephError):
    """The request conflicts with existing immutable state."""

    code = "CONFLICT"


class UnauthenticatedError(JosephError):
    """The caller did not present valid credentials."""

    code = "UNAUTHENTICATED"


class ForbiddenError(JosephError):
    """The caller is authenticated but lacks access to the resource."""

    code = "FORBIDDEN"
