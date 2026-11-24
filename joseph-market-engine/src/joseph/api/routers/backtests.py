# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Backtest route (``POST /v1/strategies/{id}/backtests``).

No postponed annotations: FastAPI resolves the injected dependency.
"""

from collections.abc import Callable
from typing import Annotated

import fastapi
import pydantic

from joseph.backtest import runner
from joseph.backtest import service as backtest_service
from joseph.domain import identity
from joseph.strategy.domain import ast


class BacktestBody(pydantic.BaseModel):
    """Body mirroring the contract ``BacktestRequest``.

    Unknown fields are rejected (422).

    Attributes:
        version: Strategy version number to evaluate.
        instrument_id: Canonical instrument id.
        from_ms: Inclusive start, UTC epoch milliseconds.
        to_ms: Exclusive end, UTC epoch milliseconds.
        fee_bps: Fee per side in basis points; when omitted, the
            strategy's fee estimate or 0.
        slippage_bps: Slippage on market fills in basis points; the
            runner's default when omitted.
        max_hold_bars: Bars before a timeout exit (1..10000); the runner's
            default when omitted.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    version: Annotated[int, pydantic.Strict(), pydantic.Field(ge=1)]
    instrument_id: ast.InstrumentIdStr
    from_ms: Annotated[int, pydantic.Strict(), pydantic.Field(ge=0)]
    to_ms: Annotated[int, pydantic.Strict(), pydantic.Field(ge=0)]
    fee_bps: ast.NonNegativeDecimalStr | None = None
    slippage_bps: ast.NonNegativeDecimalStr | None = None
    max_hold_bars: (
        Annotated[int, pydantic.Strict(), pydantic.Field(ge=1, le=10000)] | None
    ) = None


def build_router(
    *,
    backtests: backtest_service.BacktestService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the backtest router.

    The route requires a bearer token; ``get_principal`` answers 401
    without a valid one.

    Args:
        backtests: Backtest service.
        get_principal: Authentication dependency.

    Returns:
        Router serving ``POST /v1/strategies/{strategy_id}/backtests``.
    """
    router = fastapi.APIRouter(tags=["backtests"])
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]

    @router.post("/v1/strategies/{strategy_id}/backtests")
    async def run_backtest(
        strategy_id: Annotated[
            str, fastapi.Path(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
        ],
        body: BacktestBody,
        principal: principal_dep,
    ) -> dict[str, object]:
        """Runs a backtest with the production evaluator.

        Args:
            strategy_id: Strategy slug.
            body: Parameters; omitted options take the runner's defaults.
            principal: Caller; only their own versions can be run.

        Returns:
            Contract ``BacktestReport``.

        Raises:
            errors.NotFoundError: If the version is not the caller's (404).
            errors.ValidationFailedError: If the range or the strategy is
                invalid for a backtest (422).
        """
        # Built from the required fields only, so it carries the runner's
        # defaults for the options the body leaves out.
        defaults = runner.BacktestRequest(
            version=body.version,
            instrument_id=body.instrument_id,
            from_ms=body.from_ms,
            to_ms=body.to_ms,
        )
        request = runner.BacktestRequest(
            version=body.version,
            instrument_id=body.instrument_id,
            from_ms=body.from_ms,
            to_ms=body.to_ms,
            fee_bps=body.fee_bps,
            slippage_bps=(
                defaults.slippage_bps
                if body.slippage_bps is None
                else body.slippage_bps
            ),
            max_hold_bars=(
                defaults.max_hold_bars
                if body.max_hold_bars is None
                else body.max_hold_bars
            ),
        )
        report = await backtests.run(principal, strategy_id, request)
        return report.to_contract()

    return router
