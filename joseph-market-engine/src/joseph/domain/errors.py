# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Domain error hierarchy with contract error codes.

Every error carries a machine-readable ``code`` from the ``ErrorCode`` enum in
``common.schema.json`` so API adapters can map it to an ``ErrorResponse``
without string matching.
"""

from __future__ import annotations


class JosephError(Exception):
    """Expected domain error; the base of every Joseph domain error.

    Attributes:
        message: Human-readable explanation passed to the constructor.
        code: Contract ``ErrorCode`` value, e.g. ``"MISSING_DATA"``: the
            class default unless the constructor overrides it.
        retryable: Whether retrying the same request may succeed: the class
            default unless the constructor overrides it.
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
    """Input failed structural or semantic validation.

    Attributes:
        code: Default contract error code, ``"VALIDATION_FAILED"``.
    """

    code = "VALIDATION_FAILED"


class NotFoundError(JosephError):
    """A requested entity does not exist or is not visible to the tenant.

    Attributes:
        code: Default contract error code, ``"NOT_FOUND"``.
    """

    code = "NOT_FOUND"


class UnsupportedError(JosephError):
    """The venue, adapter or feature does not support the request.

    Raised instead of silently degrading or fabricating data.

    Attributes:
        code: Default contract error code, ``"UNSUPPORTED"``.
    """

    code = "UNSUPPORTED"


class MissingDataError(JosephError):
    """Required market data is absent, so no value may be inferred.

    Attributes:
        code: Default contract error code, ``"MISSING_DATA"``.
    """

    code = "MISSING_DATA"


class MarketStaleError(JosephError):
    """Market data is older than the allowed freshness budget.

    Attributes:
        code: Default contract error code, ``"MARKET_STALE"``.
        retryable: True by default: fresher data may arrive.
    """

    code = "MARKET_STALE"
    retryable = True


class VenueUnavailableError(JosephError):
    """A venue endpoint is unreachable, erroring or rate limiting.

    Attributes:
        code: Default contract error code, ``"VENUE_UNAVAILABLE"``.
        retryable: True by default: the venue may recover.
    """

    code = "VENUE_UNAVAILABLE"
    retryable = True


class RateLimitedError(VenueUnavailableError):
    """The venue rejected a request because of rate limits (HTTP 429).

    Attributes:
        code: Default contract error code, ``"RATE_LIMITED"``.
    """

    code = "RATE_LIMITED"


class ConflictError(JosephError):
    """The request conflicts with existing immutable state.

    Attributes:
        code: Default contract error code, ``"CONFLICT"``.
    """

    code = "CONFLICT"


class UnauthenticatedError(JosephError):
    """The caller did not present valid credentials.

    Attributes:
        code: Default contract error code, ``"UNAUTHENTICATED"``.
    """

    code = "UNAUTHENTICATED"


class ForbiddenError(JosephError):
    """The caller is authenticated but lacks access to the resource.

    Attributes:
        code: Default contract error code, ``"FORBIDDEN"``.
    """

    code = "FORBIDDEN"
