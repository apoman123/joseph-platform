# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Risk check results and gateway decisions."""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import enums

DEFAULT_TWD_LIMIT_FACTOR = decimal.Decimal(30)
"""Default multiplier of USD-sized money limits for TWD accounts.

Notional and loss limits are in the account's own currency; TWD accounts
(TWSE, TPEx, the TWD paper account) multiply the USD-sized defaults and
platform hard caps by this factor, so one board lot of a large-cap is not
blocked by a USD-sized cap. It is a sizing convention, not an exchange
rate. Deployments override it with ``JOSEPH_TWD_LIMIT_FACTOR``.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """The outcome of one risk check.

    Attributes:
        check: Dotted check name, e.g. ``size.tick``.
        domain: Risk domain.
        outcome: PASS, FAIL, WARN, SKIP or UNKNOWN.
        message: Plain-language explanation.
        code: Contract error code of a failure; ``None`` when there is none.
        observed: Observed value as text, if the check reports one.
        limit: Limit value as text, if the check reports one.
    """

    check: str
    domain: enums.RiskDomain
    outcome: enums.CheckOutcome
    message: str
    code: str | None = None
    observed: str | None = None
    limit: str | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``RiskCheckResult``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "check": self.check,
            "domain": self.domain.value,
            "outcome": self.outcome.value,
            "code": self.code,
            "message": self.message,
            "observed": self.observed,
            "limit": self.limit,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RiskDecision:
    """A verdict of the Risk Gateway over one intent.

    Attributes:
        decision: ALLOW, REJECT, BLOCK or PAUSE; only ALLOW authorizes.
        checks: Every evaluated check, in pipeline order.
        policy_version: Identifier of the risk policy that was applied.
        evaluated_at_ms: Evaluation time, UTC epoch milliseconds.
        reservation_id: Margin reservation created for an allowed intent;
            ``None`` until one is attached.
    """

    decision: enums.Decision
    checks: tuple[RiskCheckResult, ...]
    policy_version: str
    evaluated_at_ms: int
    reservation_id: str | None = None

    @property
    def allowed(self) -> bool:
        """Whether the decision is ALLOW; every other verdict denies."""
        return self.decision is enums.Decision.ALLOW

    def failures(self) -> list[RiskCheckResult]:
        """Returns the checks that failed or were unknown.

        Returns:
            Checks with outcome FAIL or UNKNOWN, in evaluation order (WARN
            and SKIP are not failures).
        """
        return [
            check
            for check in self.checks
            if check.outcome
            in (enums.CheckOutcome.FAIL, enums.CheckOutcome.UNKNOWN)
        ]

    def primary_code(self) -> str:
        """Returns the most relevant error code of a non-allowed decision.

        Returns:
            Code of the first failing check that has one, else
            ``RISK_LIMIT``.
        """
        for check in self.failures():
            if check.code:
                return check.code
        return "RISK_LIMIT"

    def with_reservation(self, reservation_id: str) -> RiskDecision:
        """Returns a copy carrying a reservation id.

        Args:
            reservation_id: Reservation identifier.

        Returns:
            New decision.
        """
        return dataclasses.replace(self, reservation_id=reservation_id)

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``RiskDecision``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "decision": self.decision.value,
            "checks": [check.to_contract() for check in self.checks],
            "policy_version": self.policy_version,
            "evaluated_at_ms": self.evaluated_at_ms,
            "reservation_id": self.reservation_id,
        }

    @classmethod
    def from_contract(cls, body: dict[str, object]) -> RiskDecision:
        """Rebuilds a decision from its stored contract form.

        Args:
            body: Contract ``RiskDecision`` as stored with an intent.

        Returns:
            Decision; a missing or non-list ``checks`` gives no checks.

        Raises:
            KeyError: If a required field is missing.
            ValueError: If an enum value or the time is invalid.
        """
        raw = body.get("checks")
        raw_checks: list[dict[str, object]] = (
            raw if isinstance(raw, list) else []
        )
        checks = tuple(
            RiskCheckResult(
                check=str(c["check"]),
                domain=enums.RiskDomain(str(c["domain"])),
                outcome=enums.CheckOutcome(str(c["outcome"])),
                message=str(c["message"]),
                code=None if c.get("code") is None else str(c["code"]),
                observed=None
                if c.get("observed") is None
                else str(c["observed"]),
                limit=None if c.get("limit") is None else str(c["limit"]),
            )
            for c in raw_checks
        )
        reservation = body.get("reservation_id")
        return cls(
            decision=enums.Decision(str(body["decision"])),
            checks=checks,
            policy_version=str(body["policy_version"]),
            evaluated_at_ms=int(str(body["evaluated_at_ms"])),
            reservation_id=None if reservation is None else str(reservation),
        )
