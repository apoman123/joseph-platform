# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Strategy-specific errors carrying contract error codes."""

from __future__ import annotations

from joseph.domain import errors


class TradeModeDisabledError(errors.JosephError):
    """The requested execution mode cannot be enabled from this service.

    AUTO_LIVE requires an execution-engine deployment with explicit scoped
    consent; the market engine never enables live trading.
    """

    code = "TRADE_MODE_DISABLED"


class IllegalTransitionError(errors.JosephError):
    """A setup state machine transition is not in the transition table."""

    code = "CONFLICT"


class StrategyValidationError(errors.ValidationFailedError):
    """A strategy definition failed semantic validation.

    Attributes:
        issues: Contract ``ValidationIssue`` dicts describing each problem.
    """

    def __init__(self, message: str, issues: list[dict[str, str]]) -> None:
        """Initializes the error.

        Args:
            message: Summary message.
            issues: Contract validation issues.
        """
        super().__init__(message)
        self.issues = issues
