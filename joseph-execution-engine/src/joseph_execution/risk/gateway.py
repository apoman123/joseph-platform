# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Risk Gateway: the only component that can authorize an order.

Every check of the pipeline (``checks.PIPELINE`` by default) runs for every
intent, in order and without short-circuiting, so the decision records all
reasons at once. The decision is ALLOW only when every check returns PASS,
WARN or SKIP. A check that raises counts as UNKNOWN, and UNKNOWN blocks:
missing knowledge never authorizes an order (fail closed).
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
    """Deny-by-default pre-trade policy pipeline.

    Each check result maps to a candidate decision: FAIL gives REJECT when
    the check's spec has ``reject_on_fail``, else PAUSE when it has
    ``pause_on_fail``, else BLOCK; UNKNOWN gives BLOCK; PASS, WARN and SKIP
    give none. The final decision is the most severe candidate, ranked
    ALLOW < REJECT < PAUSE < BLOCK, and ALLOW only when there is none.
    """

    def __init__(
        self,
        pipeline: tuple[checks.CheckSpec, ...] = checks.PIPELINE,
        policy_version: str = limits.POLICY_VERSION,
    ) -> None:
        """Initializes the gateway.

        Args:
            pipeline: Checks to run, in the order their results are recorded.
            policy_version: Policy identifier recorded on every decision.
        """
        self._pipeline = pipeline
        self._policy_version = policy_version

    def evaluate(self, ctx: context.RiskContext) -> risk.RiskDecision:
        """Runs every check and aggregates a decision.

        Never raises for a failing check: the exception is logged and the
        check is recorded as UNKNOWN with code ``INTERNAL``, which blocks.

        Args:
            ctx: Inputs of the evaluation; ``ctx.now_ms`` becomes the
                decision time.

        Returns:
            The decision, with one result per check in pipeline order.
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
            except Exception as err:  # noqa: BLE001 - isolation point: a raising check is recorded as UNKNOWN (blocks) and the other checks still run.
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
