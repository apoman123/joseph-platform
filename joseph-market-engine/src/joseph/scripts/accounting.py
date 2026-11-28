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
    trades = round_trips(result.fills)
    closed = [trade for trade in trades if trade.pnl is not None]
    metrics["trade_count"] = float(len(closed))
    metrics["win_rate_pct"] = (
        sum(1 for trade in closed if (trade.pnl or 0) > 0) / len(closed) * 100
        if closed
        else 0.0
    )
    return result.model_copy(
        update={
            "equity_curve": curve,
            "metrics": metrics,
            "trades": trades,
            "position_size": position,
        }
    )


class _OpenTrade:
    """Accumulates the fills of one round trip.

    Attributes:
        sign: 1 for long, -1 for short.
        entry_time_ms: Opening fill bar start.
        size: Current absolute quantity.
        peak: Largest absolute quantity held.
        opened: Absolute quantity bought (long) or sold (short) to open.
        open_cost: Quote notional of the opening fills.
        closed: Absolute quantity closed so far.
        close_value: Quote notional of the closing fills.
        fees: Commissions so far.
    """

    def __init__(self, sign: int, time_ms: int) -> None:
        """Starts a flat trade in one direction.

        Args:
            sign: 1 for long, -1 for short.
            time_ms: Opening fill bar start.
        """
        self.sign = sign
        self.entry_time_ms = time_ms
        self.size = 0.0
        self.peak = 0.0
        self.opened = 0.0
        self.open_cost = 0.0
        self.closed = 0.0
        self.close_value = 0.0
        self.fees = 0.0

    def add(self, quantity: float, price: float) -> None:
        """Adds an opening quantity.

        Args:
            quantity: Positive base quantity.
            price: Fill price.
        """
        self.size += quantity
        self.peak = max(self.peak, self.size)
        self.opened += quantity
        self.open_cost += quantity * price

    def reduce(self, quantity: float, price: float) -> None:
        """Closes part of the position.

        Args:
            quantity: Positive base quantity, at most the current size.
            price: Fill price.
        """
        self.size -= quantity
        self.closed += quantity
        self.close_value += quantity * price

    def finish(self, time_ms: int | None) -> models.StrategyTrade:
        """Describes the trade.

        Args:
            time_ms: Closing fill bar start, or None while still open.

        Returns:
            The trade; profit fields are None while it is open.
        """
        entry_price = self.open_cost / self.opened
        exit_price = None if not self.closed else self.close_value / self.closed
        pnl = None
        return_pct = None
        if time_ms is not None and exit_price is not None:
            pnl = (
                self.sign * (exit_price - entry_price) * self.closed - self.fees
            )
            return_pct = pnl / (entry_price * self.closed) * 100
        return models.StrategyTrade(
            direction="LONG" if self.sign > 0 else "SHORT",
            entry_time_ms=self.entry_time_ms,
            exit_time_ms=time_ms,
            quantity=self.peak,
            entry_price=entry_price,
            exit_price=exit_price if time_ms is not None else None,
            fees=self.fees,
            pnl=pnl,
            return_pct=return_pct,
        )


def round_trips(
    fills: Sequence[models.SimulatedFill],
) -> list[models.StrategyTrade]:
    """Pairs verified fills into round trips.

    A trade opens when the position leaves zero and closes when it returns
    to zero; a reversal closes one trade and opens the opposite one with the
    remaining quantity. Fees of a reversal fill are split pro rata.

    Args:
        fills: Chronological reconciled fills.

    Returns:
        Trades in opening order; the last one is open when the position is.
    """
    trades: list[models.StrategyTrade] = []
    position = 0.0
    current: _OpenTrade | None = None
    for fill in fills:
        change = fill.quantity if fill.side == "buy" else -fill.quantity
        remaining = abs(change)
        if current is not None and position * change < 0:
            closing = min(abs(position), remaining)
            current.reduce(closing, fill.price)
            current.fees += fill.fee * closing / abs(change)
            remaining -= closing
            position += math.copysign(closing, change)
            if math.isclose(position, 0.0, abs_tol=1e-12):
                position = 0.0
                trades.append(current.finish(fill.time_ms))
                current = None
        if remaining > 1e-12:
            if current is None:
                current = _OpenTrade(1 if change > 0 else -1, fill.time_ms)
            current.add(remaining, fill.price)
            current.fees += fill.fee * remaining / abs(change)
            position += math.copysign(remaining, change)
    if current is not None:
        trades.append(current.finish(None))
    return trades
