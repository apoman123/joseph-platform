"""Risk Gateway: the only component that can authorize an order.

Every check in ``checks.PIPELINE`` runs for every intent. ALLOW requires
that no check FAILs or is UNKNOWN; a check that raises is treated as
UNKNOWN (fail closed).
"""

from __future__ import annotations

import logging

from joseph_execution.domain import enums
from joseph_execution.domain import risk
from joseph_execution.risk import checks
from joseph_execution.risk import context
from joseph_execution.risk import limits

_LOG = logging.getLogger(__name__)


class RiskGateway:
    """Deny-by-default policy pipeline."""

    def __init__(
        self,
        pipeline: tuple[checks.CheckSpec, ...] = checks.PIPELINE,
        policy_version: str = limits.POLICY_VERSION,
    ) -> None:
        """Initializes the gateway.

        Args:
            pipeline: Ordered checks.
            policy_version: Policy identifier recorded on decisions.
        """
        self._pipeline = pipeline
        self._policy_version = policy_version

    def evaluate(self, ctx: context.RiskContext) -> risk.RiskDecision:
        """Runs every check and aggregates a decision.

        Args:
            ctx: Risk context.

        Returns:
            Decision with one result per check.
        """
        results: list[risk.RiskCheckResult] = []
        verdict = enums.Decision.ALLOW
        rank = {
            enums.Decision.ALLOW: 0,
            enums.Decision.REJECT: 1,
            enums.Decision.PAUSE: 2,
            enums.Decision.BLOCK: 3,
        }
        for spec in self._pipeline:
            try:
                result = spec.fn(ctx)
            except Exception as err:
                _LOG.exception("risk check %s raised", spec.fn.__name__)
                result = risk.RiskCheckResult(
                    check=spec.fn.__name__,
                    domain=enums.RiskDomain.MODE,
                    outcome=enums.CheckOutcome.UNKNOWN,
                    message=f"Check failed to evaluate: {type(err).__name__}",
                    code="INTERNAL",
                )
            results.append(result)
            outcome = result.outcome
            if outcome is enums.CheckOutcome.FAIL:
                if spec.reject_on_fail:
                    candidate = enums.Decision.REJECT
                elif spec.pause_on_fail:
                    candidate = enums.Decision.PAUSE
                else:
                    candidate = enums.Decision.BLOCK
            elif outcome is enums.CheckOutcome.UNKNOWN:
                candidate = enums.Decision.BLOCK
            else:
                continue
            if rank[candidate] > rank[verdict]:
                verdict = candidate
        return risk.RiskDecision(
            decision=verdict,
            checks=tuple(results),
            policy_version=self._policy_version,
            evaluated_at_ms=ctx.now_ms,
        )
