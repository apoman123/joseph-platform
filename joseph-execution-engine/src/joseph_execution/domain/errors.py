"""Execution error hierarchy carrying contract ``ErrorCode`` values."""

from __future__ import annotations


class ExecutionError(Exception):
    """Base class for expected execution-engine errors.

    Attributes:
        code: Contract ``ErrorCode`` value such as ``"RISK_LIMIT"``.
        message: Plain-language explanation.
        retryable: Whether retrying the same request may succeed.
        details: Optional JSON-compatible details for the response body.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        """Initializes the error.

        Args:
            code: Contract error code.
            message: Human readable explanation.
            retryable: Retry hint for clients.
            details: Optional structured details.
        """
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class InvalidTransitionError(ExecutionError):
    """An order state transition is not permitted by the state machine."""

    def __init__(self, current: str, target: str) -> None:
        """Initializes the error.

        Args:
            current: Current order state.
            target: Requested order state.
        """
        super().__init__(
            "CONFLICT", f"illegal order transition {current} -> {target}"
        )
        self.current = current
        self.target = target


class AdapterTimeoutError(ExecutionError):
    """A venue call timed out; the outcome of the request is unknown."""

    def __init__(self, message: str = "venue request timed out") -> None:
        """Initializes the error.

        Args:
            message: Explanation.
        """
        super().__init__("ORDER_UNKNOWN", message, retryable=True)


class AdapterUnavailableError(ExecutionError):
    """A venue adapter cannot serve the request (refused before sending)."""

    def __init__(self, message: str, *, code: str = "VENUE_UNAVAILABLE"):
        """Initializes the error.

        Args:
            message: Explanation.
            code: Contract error code.
        """
        super().__init__(code, message)


class MarketDataUnavailableError(ExecutionError):
    """Market data required for a decision could not be obtained."""

    def __init__(self, message: str) -> None:
        """Initializes the error.

        Args:
            message: Explanation.
        """
        super().__init__("MARKET_STALE", message, retryable=True)


# ----------------------------------------------------------------------
# Venue rejections (live venue adapters).
# ----------------------------------------------------------------------


class VenueRejectedError(ExecutionError):
    """The venue definitively refused a request; it is never retried.

    The request reached the venue and was not executed, so no order (or
    amendment or cancel) exists because of it. Subclasses distinguish the
    reasons a user can act on.

    Attributes:
        venue: Venue code.
        venue_code: The venue's own error code, if any.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        venue: str,
        venue_code: str | None = None,
    ) -> None:
        """Initializes the error.

        Args:
            code: Contract error code.
            message: Explanation (never contains credentials).
            venue: Venue code.
            venue_code: Venue error code.
        """
        super().__init__(
            code,
            message,
            retryable=False,
            details={"venue": venue, "venue_code": venue_code},
        )
        self.venue = venue
        self.venue_code = venue_code


class InsufficientMarginError(VenueRejectedError):
    """The venue refused an order for lack of margin or balance."""

    def __init__(
        self, message: str, *, venue: str, venue_code: str | None = None
    ) -> None:
        """Initializes the error.

        Args:
            message: Explanation.
            venue: Venue code.
            venue_code: Venue error code.
        """
        super().__init__(
            "INSUFFICIENT_MARGIN", message, venue=venue, venue_code=venue_code
        )


class OrderRejectedError(VenueRejectedError):
    """The venue refused the request's parameters (price, size, flags).

    ``code`` is ``VALIDATION_FAILED`` unless a more specific contract code
    applies (``INVALID_TICK_SIZE``, ``INVALID_LOT_SIZE``, ``MIN_NOTIONAL``,
    ``UNSUPPORTED_ORDER_TYPE``, ``NOT_FOUND``, ``CONFLICT``).
    """

    def __init__(
        self,
        message: str,
        *,
        venue: str,
        venue_code: str | None = None,
        code: str = "VALIDATION_FAILED",
    ) -> None:
        """Initializes the error.

        Args:
            message: Explanation.
            venue: Venue code.
            venue_code: Venue error code.
            code: Contract error code.
        """
        super().__init__(code, message, venue=venue, venue_code=venue_code)


class VenuePermissionError(VenueRejectedError):
    """The credentials lack a permission (trading scope, KYC, IP, key)."""

    def __init__(
        self, message: str, *, venue: str, venue_code: str | None = None
    ) -> None:
        """Initializes the error.

        Args:
            message: Explanation, starting with "permission missing".
            venue: Venue code.
            venue_code: Venue error code.
        """
        super().__init__(
            "AUTH_SCOPE_MISSING", message, venue=venue, venue_code=venue_code
        )


class RateLimitedError(AdapterUnavailableError):
    """A request was refused for rate limiting; it was not executed.

    Raised before sending when the local limiter has no budget, or when
    the venue answers 429/418. Retrying later may succeed.

    Attributes:
        retry_after_s: Seconds the venue asked to wait, if known.
    """

    def __init__(
        self, message: str, retry_after_s: float | None = None
    ) -> None:
        """Initializes the error.

        Args:
            message: Explanation.
            retry_after_s: Venue ``Retry-After`` hint.
        """
        super().__init__(message, code="RATE_LIMITED")
        self.retryable = True
        self.retry_after_s = retry_after_s
