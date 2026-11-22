"""Risk check results and gateway decisions."""

from __future__ import annotations

import dataclasses

from joseph_execution.domain import enums


@dataclasses.dataclass(frozen=True, slots=True)
class RiskCheckResult:
    """Outcome of one risk check.

    Attributes:
        check: Dotted check name, e.g. ``size.tick``.
        domain: Risk domain.
        outcome: PASS, FAIL, WARN, SKIP or UNKNOWN.
        message: Plain-language explanation.
        code: Contract error code for failures.
        observed: Observed value as text.
        limit: Limit value as text.
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
    """Verdict of the Risk Gateway.

    Attributes:
        decision: ALLOW, REJECT, BLOCK or PAUSE.
        checks: Every evaluated check.
        policy_version: Policy identifier.
        evaluated_at_ms: Evaluation time.
        reservation_id: Reservation created for an allowed intent.
    """

    decision: enums.Decision
    checks: tuple[RiskCheckResult, ...]
    policy_version: str
    evaluated_at_ms: int
    reservation_id: str | None = None

    @property
    def allowed(self) -> bool:
        """Returns True only for ALLOW."""
        return self.decision is enums.Decision.ALLOW

    def failures(self) -> list[RiskCheckResult]:
        """Returns the checks that failed or were unknown.

        Returns:
            Failing checks in evaluation order.
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
            Contract error code, ``RISK_LIMIT`` by default.
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
            body: Contract ``RiskDecision``.

        Returns:
            Decision.
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
