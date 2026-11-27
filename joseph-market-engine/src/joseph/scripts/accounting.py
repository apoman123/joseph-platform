# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Host-side reconciliation of simulated fills against authoritative candles."""

from __future__ import annotations

import math
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import errors
from joseph.scripts import models


def reconcile(
    result: models.ScriptResult,
    bars: Sequence[Mapping[str, float]],
    request: models.ScriptRun,
) -> models.ScriptResult:
    """Recomputes performance instead of trusting user-mutable runner balances.

    Args:
        result: Structurally validated strategy output with source-bar fills.
        bars: Authoritative chronological OHLCV input dictionaries.
        request: Host-validated initial equity, commission and slippage
            settings.

    Returns:
        Result whose equity and financial metrics follow a verified fill ledger.

    Raises:
        errors.ValidationFailedError: For fabricated prices/fees, duplicate or
            first-bar fills, invalid effects, excessive positions or insolvency.
    """
    if result.kind != "strategy":
        return result
    by_time = {fill.time_ms: fill for fill in result.fills}
    starts = {int(bar["time_ms"]) for bar in bars[1:]}
    if len(by_time) != len(result.fills) or not set(by_time) <= starts:
        raise errors.ValidationFailedError(
            "simulated fills require distinct next-bar source timestamps"
        )
    cash = request.initial_equity
    position = 0.0
    peak = cash
    drawdown = 0.0
    fees = 0.0
    curve = []
    for bar in bars:
        fill = by_time.get(int(bar["time_ms"]))
        if fill is not None:
            change = fill.quantity if fill.side == "buy" else -fill.quantity
            price = bar["open"] * (
                1 + math.copysign(request.slippage_bps / 10000, change)
            )
            fee = abs(change) * price * request.commission_bps / 10000
            desired = position + change
            effect = "entry"
            if desired == 0 or (
                position * desired > 0 and abs(desired) < abs(position)
            ):
                effect = "exit"
            elif position * desired < 0:
                effect = "reversal"
            marked = cash + position * bar["open"]
            valid = (
                math.isclose(fill.price, price, rel_tol=1e-10)
                and math.isclose(fill.fee, fee, rel_tol=1e-10, abs_tol=1e-9)
                and fill.effect == effect
                and abs(desired) <= 1e9
                and (
                    desired == 0
                    or abs(desired) * price + fee <= max(0, marked) + 1e-7
                )
            )
            if not valid:
                raise errors.ValidationFailedError(
                    "simulated fill does not reconcile with source prices, "
                    "costs or account limits"
                )
            cash -= change * price + fee
            position = desired
            fees += fee
        equity = cash + position * bar["close"]
        if not math.isfinite(equity):
            raise errors.ValidationFailedError("simulated equity is not finite")
        peak = max(peak, equity)
        drawdown = max(
            drawdown, (peak - equity) / peak * 100 if peak > 0 else 0
        )
        curve.append(
            models.PlotPoint(time_ms=int(bar["time_ms"]), value=equity)
        )
    final = curve[-1].value if curve else request.initial_equity
    if final is None:
        final = request.initial_equity
    metrics = {
        "initial_equity": request.initial_equity,
        "final_equity": final,
        "net_profit": final - request.initial_equity,
        "return_pct": (final / request.initial_equity - 1) * 100,
        "max_drawdown_pct": drawdown,
        "fill_count": float(len(result.fills)),
        "fees": fees,
    }
    for name in ("unfilled_intents", "rejected_orders"):
        value = result.metrics.get(name, 0.0)
        if not value.is_integer() or not 0 <= value <= len(bars):
            raise errors.ValidationFailedError(
                "invalid simulated order diagnostic count"
            )
        metrics[name] = value
    return result.model_copy(update={"equity_curve": curve, "metrics": metrics})
