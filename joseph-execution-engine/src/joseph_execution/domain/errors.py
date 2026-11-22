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
