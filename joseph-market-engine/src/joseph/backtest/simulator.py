"""Conservative fill simulation and performance metrics for backtests.

Assumptions (also reported in every ``BacktestReport``):

* Entries fill at the open of the bar after the signal bar, plus slippage.
* A trade whose entry gaps beyond its stop is skipped.
* Stops fill at the stop (or the open when a bar gaps through it) minus
  slippage; targets fill at the target without slippage.
* When one bar touches both stop and target, the stop is assumed first.
* Fees are charged on entry and exit notional at ``fee_bps`` per side.
* One position at a time; signals during an open trade are skipped.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Sequence

from joseph.domain import market
from joseph.domain import structures
from joseph.signals.domain import signal as signal_domain

_BPS = decimal.Decimal(10_000)
_R_QUANTUM = decimal.Decimal("0.0001")
_PRICE_QUANTUM = decimal.Decimal("0.00000001")
ZERO = decimal.Decimal(0)

ASSUMPTIONS: tuple[str, ...] = (
    "Entry fills at the next bar open plus slippage_bps.",
    "Trades whose entry gaps beyond the stop are skipped.",
    "Stops fill at the stop or a worse gap open, minus slippage; targets "
    "fill at the target.",
    "If a bar touches both stop and target the stop is assumed first.",
    "Fees are charged on entry and exit notional at fee_bps per side.",
    "One position at a time; signals during an open trade are skipped.",
    "Universe qualification is assumed true for every bar (point-in-time "
    "qualification history is not replayed).",
    "Funding payments are not included.",
)
"""Human-readable simulation assumptions."""


@dataclasses.dataclass(frozen=True, slots=True)
class SimulationParams:
    """Simulation parameters.

    Attributes:
        fee_bps: Fee per side in basis points.
        slippage_bps: Slippage on market fills in basis points.
        max_hold_bars: Bars after which a trade exits at the close.
    """

    fee_bps: decimal.Decimal
    slippage_bps: decimal.Decimal
    max_hold_bars: int


@dataclasses.dataclass(frozen=True, slots=True)
class Trade:
    """One simulated trade (contract ``BacktestTrade``).

    Attributes:
        signal_id: Entry signal.
        direction: ``LONG`` or ``SHORT``.
        entry_ms: Entry bar start.
        entry_price: Fill price.
        stop_price: Stop.
        target_price: Target.
        exit_ms: Exit bar close.
        exit_price: Exit price.
        exit_reason: ``STOP``, ``TARGET``, ``TIMEOUT`` or ``END_OF_DATA``.
        r_multiple: Net R after fees.
        fees_r: Fees in R.
        hold_bars: Bars held including entry and exit bars.
    """

    signal_id: str
    direction: str
    entry_ms: int
    entry_price: decimal.Decimal
    stop_price: decimal.Decimal
    target_price: decimal.Decimal
    exit_ms: int
    exit_price: decimal.Decimal
    exit_reason: str
    r_multiple: decimal.Decimal
    fees_r: decimal.Decimal
    hold_bars: int

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        fmt = _fmt
        return {
            "signal_id": self.signal_id,
            "direction": self.direction,
            "entry_ms": self.entry_ms,
            "entry_price": fmt(self.entry_price),
            "stop_price": fmt(self.stop_price),
            "target_price": fmt(self.target_price),
            "exit_ms": self.exit_ms,
            "exit_price": fmt(self.exit_price),
            "exit_reason": self.exit_reason,
            "r_multiple": fmt(self.r_multiple),
            "fees_r": fmt(self.fees_r),
            "hold_bars": self.hold_bars,
        }


def _fmt(value: decimal.Decimal) -> str:
    """Formats a decimal without exponent notation.

    Args:
        value: Decimal.

    Returns:
        Plain decimal text.
    """
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclasses.dataclass(frozen=True, slots=True)
class Metrics:
    """Aggregate performance (contract ``BacktestMetrics``).

    Attributes:
        trades: Number of trades.
        wins: Trades with positive net R.
        losses: Trades with non-positive net R.
        win_rate: Wins / trades.
        expectancy_r: Mean net R.
        profit_factor: Gross positive R / gross negative R.
        max_drawdown_r: Largest peak-to-trough of cumulative R.
        avg_r: Mean net R.
        avg_hold_bars: Mean bars held.
    """

    trades: int
    wins: int
    losses: int
    win_rate: decimal.Decimal | None
    expectancy_r: decimal.Decimal | None
    profit_factor: decimal.Decimal | None
    max_drawdown_r: decimal.Decimal
    avg_r: decimal.Decimal | None
    avg_hold_bars: decimal.Decimal | None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """

        def opt(value: decimal.Decimal | None) -> str | None:
            """Formats an optional decimal.

            Args:
                value: Decimal or ``None``.

            Returns:
                Text or ``None``.
            """
            return None if value is None else _fmt(value)

        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": opt(self.win_rate),
            "expectancy_r": opt(self.expectancy_r),
            "profit_factor": opt(self.profit_factor),
            "max_drawdown_r": _fmt(self.max_drawdown_r),
            "avg_r": opt(self.avg_r),
            "avg_hold_bars": opt(self.avg_hold_bars),
        }


def _q(value: decimal.Decimal) -> decimal.Decimal:
    """Quantizes an R value to 4 decimals (toward negative infinity).

    Args:
        value: Value.

    Returns:
        Quantized value.
    """
    return value.quantize(_R_QUANTUM, rounding=decimal.ROUND_FLOOR)


def compute_metrics(trades: Sequence[Trade]) -> Metrics:
    """Aggregates trade results.

    Args:
        trades: Simulated trades in chronological order.

    Returns:
        Metrics (ratios are ``None`` when undefined).
    """
    count = len(trades)
    if count == 0:
        return Metrics(0, 0, 0, None, None, None, ZERO, None, None)
    rs = [t.r_multiple for t in trades]
    wins = sum(1 for r in rs if r > 0)
    losses = count - wins
    total = sum(rs, ZERO)
    mean = _q(total / count)
    positive = sum((r for r in rs if r > 0), ZERO)
    negative = -sum((r for r in rs if r < 0), ZERO)
    profit_factor = None if negative == 0 else _q(positive / negative)
    peak = cumulative = drawdown = ZERO
    for r in rs:
        cumulative += r
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    holds = sum(t.hold_bars for t in trades)
    return Metrics(
        trades=count,
        wins=wins,
        losses=losses,
        win_rate=_q(decimal.Decimal(wins) / count),
        expectancy_r=mean,
        profit_factor=profit_factor,
        max_drawdown_r=_q(drawdown),
        avg_r=mean,
        avg_hold_bars=_q(decimal.Decimal(holds) / count),
    )


def _exit_for_bar(
    candle: market.Candle,
    *,
    long: bool,
    stop: decimal.Decimal,
    target: decimal.Decimal,
    is_entry_bar: bool,
) -> tuple[str, decimal.Decimal] | None:
    """Determines whether a bar exits the trade.

    Args:
        candle: Bar.
        long: Trade direction.
        stop: Stop price.
        target: Target price.
        is_entry_bar: Whether this is the entry bar (entered at the open).

    Returns:
        ``(reason, raw_price)`` or ``None`` when the trade stays open.
    """
    if long:
        gap = not is_entry_bar and candle.open <= stop
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
    else:
        gap = not is_entry_bar and candle.open >= stop
        stop_hit = candle.high >= stop
        target_hit = candle.low <= target
    if gap:
        return "STOP", candle.open
    if stop_hit:
        return "STOP", stop
    if target_hit:
        return "TARGET", target
    return None


def simulate(
    signals: Sequence[signal_domain.Signal],
    frames: Sequence[structures.BarFrame],
    params: SimulationParams,
) -> list[Trade]:
    """Simulates trades for ENTRY_CONFIRMED signals.

    Args:
        signals: Signals from the backtest run.
        frames: Frames used for the run, oldest first.
        params: Simulation parameters.

    Returns:
        Trades in chronological order.
    """
    by_start = {
        frame.candle.start_ms: index for index, frame in enumerate(frames)
    }
    entries = sorted(
        (
            s
            for s in signals
            if s.kind == "ENTRY_CONFIRMED"
            and s.stop_price is not None
            and s.target_price is not None
        ),
        key=lambda s: s.bar_close_ms,
    )
    trades: list[Trade] = []
    busy_until = -1
    slip_rate = params.slippage_bps / _BPS
    for sig in entries:
        if sig.bar_close_ms < busy_until:
            continue
        index = by_start.get(sig.bar_close_ms)
        if index is None:
            continue
        trade = _simulate_one(sig, frames, index, params, slip_rate)
        if trade is None:
            continue
        trades.append(trade)
        busy_until = trade.exit_ms
    return trades


def _simulate_one(
    sig: signal_domain.Signal,
    frames: Sequence[structures.BarFrame],
    index: int,
    params: SimulationParams,
    slip_rate: decimal.Decimal,
) -> Trade | None:
    """Simulates one trade starting at ``frames[index]``.

    Args:
        sig: Entry signal (with stop and target).
        frames: Frames.
        index: Entry bar index.
        params: Simulation parameters.
        slip_rate: Slippage as a fraction.

    Returns:
        The trade, or ``None`` when the entry gapped beyond the stop.
    """
    long = sig.direction == "LONG"
    stop = sig.stop_price or ZERO
    target = sig.target_price or ZERO
    entry_open = frames[index].candle.open
    slip = entry_open * slip_rate
    entry = entry_open + slip if long else entry_open - slip
    if (long and entry <= stop) or (not long and entry >= stop):
        return None
    risk_unit = abs(entry - stop)
    exit_reason, exit_price, exit_index = "END_OF_DATA", ZERO, len(frames) - 1
    for j in range(index, len(frames)):
        candle = frames[j].candle
        hit = _exit_for_bar(
            candle, long=long, stop=stop, target=target, is_entry_bar=j == index
        )
        if hit is not None:
            exit_reason, exit_price = hit
            exit_index = j
            break
        if j - index + 1 >= params.max_hold_bars:
            exit_reason, exit_price, exit_index = "TIMEOUT", candle.close, j
            break
    else:
        exit_price = frames[-1].candle.close
    if exit_reason != "TARGET":
        adjust = exit_price * slip_rate
        exit_price = exit_price - adjust if long else exit_price + adjust
    gross = (exit_price - entry) if long else (entry - exit_price)
    fees = (entry + exit_price) * params.fee_bps / _BPS
    fees_r = fees / risk_unit
    return Trade(
        signal_id=sig.signal_id,
        direction=sig.direction,
        entry_ms=frames[index].candle.start_ms,
        entry_price=entry.quantize(_PRICE_QUANTUM),
        stop_price=stop,
        target_price=target,
        exit_ms=frames[exit_index].close_ms,
        exit_price=exit_price.quantize(_PRICE_QUANTUM),
        exit_reason=exit_reason,
        r_multiple=_q(gross / risk_unit - fees_r),
        fees_r=_q(fees_r),
        hold_bars=exit_index - index + 1,
    )
