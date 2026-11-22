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

    Attributes:
        version: Version number.
        instrument_id: Canonical instrument id.
        from_ms: Inclusive start.
        to_ms: Exclusive end.
        fee_bps: Fee per side.
        slippage_bps: Slippage on market fills.
        max_hold_bars: Timeout in bars.
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

    Args:
        backtests: Backtest service.
        get_principal: Authentication dependency.

    Returns:
        Router.
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
            body: Parameters.
            principal: Caller.

        Returns:
            Contract ``BacktestReport``.
        """
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
