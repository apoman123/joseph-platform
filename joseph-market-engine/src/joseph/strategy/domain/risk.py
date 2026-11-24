# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Projected entry, stop, target and net reward/risk for a setup.

Entry is the close of the evaluated signal bar. When any input is missing
the assessment is *not computable* and carries reasons; it never guesses.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Collection
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import market
from joseph.domain import structures
from joseph.strategy.domain import ast

ATR_PERIOD = 14
"""Wilder ATR period used for stop buffers."""

SWING_LOOKBACK_BARS = 10
"""Bars scanned for the SWING stop model."""

_RR_QUANTUM = decimal.Decimal("0.0001")
"""Reward/risk precision; values are rounded down so that rounding never
lifts a setup over a ``min_rr`` threshold."""

_BPS = decimal.Decimal(10_000)
"""Basis points per unit."""


@dataclasses.dataclass(frozen=True, slots=True)
class TouchedAoi:
    """An AOI touched by a recent closed bar.

    Attributes:
        aoi: Latest visible record of the AOI.
        bar_close_ms: Close time of the touching bar.
    """

    aoi: structures.Aoi
    bar_close_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Projected trade levels.

    Attributes:
        entry: Projected entry (signal close).
        stop: Protective stop, if computable.
        target: Target, if computable.
        rr: Net reward/risk after estimated round-trip costs, rounded down
            to 0.0001, if computable.
        gross_rr: Reward/risk before costs, rounded down to 0.0001, if
            computable.
        atr: ATR(14) used for buffers, if available.
        anchor_aoi_id: AOI used for the invalidation stop.
        reasons: Why values could not be computed.
    """

    entry: decimal.Decimal | None
    stop: decimal.Decimal | None = None
    target: decimal.Decimal | None = None
    rr: decimal.Decimal | None = None
    gross_rr: decimal.Decimal | None = None
    atr: decimal.Decimal | None = None
    anchor_aoi_id: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def computable(self) -> bool:
        """Whether a net reward/risk was computed."""
        return self.rr is not None


def wilder_atr(
    candles: Sequence[market.Candle], period: int = ATR_PERIOD
) -> decimal.Decimal | None:
    """Computes Wilder's ATR (RMA of true range) over the given candles.

    The seed is the simple mean of the first ``period`` true ranges; each
    later value is ``(prev * (period - 1) + tr) / period``, matching Pine's
    ``ta.atr``.

    Args:
        candles: Chronological candles.
        period: Smoothing period.

    Returns:
        ATR of the last candle, or ``None`` with fewer than ``period`` bars.
    """
    if len(candles) < period:
        return None
    ranges: list[decimal.Decimal] = []
    previous_close: decimal.Decimal | None = None
    for candle in candles:
        high_low = candle.high - candle.low
        if previous_close is None:
            ranges.append(high_low)
        else:
            ranges.append(
                max(
                    high_low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
        previous_close = candle.close
    atr = sum(ranges[:period], decimals.ZERO) / period
    for true_range in ranges[period:]:
        atr = (atr * (period - 1) + true_range) / period
    return atr


def choose_anchor(touched: Sequence[TouchedAoi]) -> TouchedAoi | None:
    """Picks the AOI that anchors a setup.

    Most recent touch wins, then higher quality, then the smaller id, so
    the choice is deterministic.

    Args:
        touched: Positive AOI touches on the evaluated bar's lookback.

    Returns:
        The anchor, or ``None`` when nothing was touched.
    """
    if not touched:
        return None
    return sorted(
        touched,
        key=lambda t: (
            -t.bar_close_ms,
            -(t.aoi.quality_score if t.aoi.quality_score is not None else -1),
            t.aoi.aoi_id,
        ),
    )[0]


def _nearest_aoi_target(
    frame: structures.BarFrame,
    entry: decimal.Decimal,
    long: bool,
    kinds: Collection[str],
    plugins: Collection[str],
) -> tuple[decimal.Decimal | None, str | None]:
    """Finds the nearest live AOI of the target kinds beyond the entry.

    For a long, the nearest AOI whose lower bound is above the entry; for a
    short, the nearest whose upper bound is below it. Every timeframe's
    snapshot of the publishing plugins in the current frame is searched.

    Args:
        frame: Current frame.
        entry: Projected entry.
        long: True for long setups.
        kinds: Target AOI kinds.
        plugins: Plugins publishing those kinds.

    Returns:
        ``(level, reason)`` where reason explains a missing level.
    """
    snapshots = [
        snap
        for (plugin, _), snap in frame.snapshots.items()
        if plugin in plugins
    ]
    if not snapshots:
        return None, "target zone snapshot unavailable"
    levels: list[decimal.Decimal] = []
    for snap in snapshots:
        for aoi in snap.aois:
            if aoi.kind not in kinds or not aoi.state.is_live:
                continue
            level = aoi.lower if long else aoi.upper
            if (long and level > entry) or (not long and level < entry):
                levels.append(level)
    if not levels:
        return None, f"no {'/'.join(sorted(kinds))} zone beyond the entry"
    return (min(levels) if long else max(levels)), None


def _stop(
    model: ast.RiskModel,
    entry: decimal.Decimal,
    candles: Sequence[market.Candle],
    *,
    atr: decimal.Decimal | None,
    anchor: TouchedAoi | None,
    long: bool,
    reasons: list[str],
) -> decimal.Decimal | None:
    """Places the protective stop for the configured stop model.

    Args:
        model: Risk model.
        entry: Projected entry.
        candles: Window candles, latest last.
        atr: ATR(14) or ``None``.
        anchor: Anchoring AOI touch.
        long: True for long setups.
        reasons: Receives explanations when no stop can be placed.

    Returns:
        Stop price or ``None``.
    """
    buffer = model.stop_buffer_atr or decimals.ZERO
    offset = buffer * (atr or decimals.ZERO)
    stop: decimal.Decimal | None = None
    if buffer > 0 and atr is None:
        reasons.append("ATR(14) unavailable for the stop buffer")
    elif model.stop_model == "AOI_INVALIDATION":
        if anchor is None:
            reasons.append("no touched AOI to anchor the invalidation stop")
        else:
            stop = (
                anchor.aoi.lower - offset if long else anchor.aoi.upper + offset
            )
    elif model.stop_model == "SWING":
        recent = candles[-SWING_LOOKBACK_BARS:]
        stop = (
            min(c.low for c in recent) - offset
            if long
            else max(c.high for c in recent) + offset
        )
    elif atr is None:
        reasons.append("ATR(14) unavailable for the ATR stop")
    else:
        stop = entry - offset if long else entry + offset
    return stop


def assess(
    definition: ast.StrategyDefinition,
    frames: Sequence[structures.BarFrame],
    touched: Sequence[TouchedAoi],
    target_plugins: Collection[str] = (),
) -> RiskAssessment:
    """Projects entry, stop, target and net reward/risk.

    Costs are ``fee_bps_estimate`` charged on entry and exit at the entry
    price, ``cost = entry * fee_bps * 2 / 10000``; the net reward/risk is
    ``(reward - cost) / (risk + cost)``. A stop on the wrong side of the
    entry counts as no stop.

    Args:
        definition: Strategy definition (direction and risk model).
        frames: Evaluation window, current frame last.
        touched: Positive AOI touches on the current bar's lookback.
        target_plugins: Plugins publishing the ``NEAREST_AOI`` target
            kinds.

    Returns:
        The assessment; ``rr`` is ``None`` with reasons when incomplete.
    """
    if not frames:
        return RiskAssessment(entry=None, reasons=("no frames",))
    model = definition.risk
    long = definition.direction == "LONG"
    current = frames[-1]
    entry = current.candle.close
    candles = [frame.candle for frame in frames]
    atr = wilder_atr(candles)
    anchor = choose_anchor(touched)
    reasons: list[str] = []

    stop = _stop(
        model,
        entry,
        candles,
        atr=atr,
        anchor=anchor,
        long=long,
        reasons=reasons,
    )

    if stop is not None and (
        (long and stop >= entry) or (not long and stop <= entry)
    ):
        reasons.append("stop is not beyond the entry")
        stop = None

    target: decimal.Decimal | None = None
    if stop is not None:
        risk_distance = abs(entry - stop)
        if model.target_model == "FIXED_R":
            multiple = model.fixed_r or decimals.ZERO
            target = (
                (entry + multiple * risk_distance)
                if long
                else (entry - multiple * risk_distance)
            )
        else:
            target, reason = _nearest_aoi_target(
                current,
                entry,
                long,
                frozenset(model.target_kinds or ()),
                frozenset(target_plugins),
            )
            if reason:
                reasons.append(reason)

    rr: decimal.Decimal | None = None
    gross: decimal.Decimal | None = None
    if stop is not None and target is not None:
        risk_distance = abs(entry - stop)
        reward = abs(target - entry)
        fee = model.fee_bps_estimate or decimals.ZERO
        cost = entry * fee * 2 / _BPS
        gross = (reward / risk_distance).quantize(
            _RR_QUANTUM, rounding=decimal.ROUND_FLOOR
        )
        rr = ((reward - cost) / (risk_distance + cost)).quantize(
            _RR_QUANTUM, rounding=decimal.ROUND_FLOOR
        )
    return RiskAssessment(
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        gross_rr=gross,
        atr=atr,
        anchor_aoi_id=None if anchor is None else anchor.aoi.aoi_id,
        reasons=tuple(reasons),
    )
