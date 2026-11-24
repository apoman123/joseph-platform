# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Session-aligned candles for exchange and rate venues (pure).

Bars of exchange-listed instruments follow the instrument's session calendar
(``sessions.for_instrument``): intraday bars start at the session open and
the last one is cut at the close, a daily bar is one session, and weekly and
monthly bars span the sessions of an ISO week or calendar month. This module
builds such candles and aggregates shorter bars onto a calendar's grid:

* OHLC: first open, highest high, lowest low, last close; volume, quote
  turnover, trade count and buy volume are sums (an optional sum is ``None``
  when any constituent lacks it, and the buy-volume method is kept only
  when every constituent agrees).
* Finality: an aggregate is final only when it has ended, every constituent
  is final and the constituent data is known complete through its end
  (``covered_to_ms``), so a bar whose sources are still arriving is never
  final.
"""

from __future__ import annotations

import decimal
from collections.abc import Iterable
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes


def candle(
    *,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    open_: decimal.Decimal,
    high: decimal.Decimal,
    low: decimal.Decimal,
    close: decimal.Decimal,
    base_volume: decimal.Decimal,
    now_ms: int,
    source: str,
    is_final: bool | None = None,
    feed_state: quality.FeedState = quality.FeedState.LIVE,
    quote_turnover: decimal.Decimal | None = None,
    trade_count: int | None = None,
    event_ts_ms: int | None = None,
    taker_buy_base_volume: decimal.Decimal | None = None,
    taker_buy_method: market.BuyVolumeMethod | None = None,
) -> market.Candle:
    """Builds a candle on the instrument's session calendar.

    High/low are widened to contain open and close, volume is clamped at
    zero and buy volume into ``[0, volume]``. Finality defaults to "the bar
    has ended by ``now_ms``".

    Args:
        instrument_id: Instrument.
        timeframe: Resolution.
        start_ms: Session-aligned bar start, epoch milliseconds.
        open_: Open.
        high: High.
        low: Low.
        close: Close.
        base_volume: Base volume.
        now_ms: Receipt time, epoch milliseconds.
        source: Provenance label.
        is_final: Explicit finality (``None`` derives it from the end).
        feed_state: Provenance state.
        quote_turnover: Optional quote turnover.
        trade_count: Optional trade count.
        event_ts_ms: Optional source event time, epoch milliseconds.
        taker_buy_base_volume: Optional buy volume.
        taker_buy_method: How the buy volume was obtained (dropped when
            there is no buy volume).

    Returns:
        Candle.

    Raises:
        ValueError: If ``start_ms`` is not a bar start of the calendar.
    """
    calendar = sessions.for_instrument(instrument_id)
    end_ms = calendar.end_of(timeframe, start_ms)
    volume = max(base_volume, decimals.ZERO)
    taker = (
        None
        if taker_buy_base_volume is None
        else min(max(taker_buy_base_volume, decimals.ZERO), volume)
    )
    return market.Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=open_,
        high=max(high, open_, close),
        low=min(low, open_, close),
        close=close,
        base_volume=volume,
        is_final=end_ms <= now_ms if is_final is None else is_final,
        source=source,
        feed_state=feed_state,
        received_at_ms=now_ms,
        quote_turnover=quote_turnover,
        trade_count=trade_count,
        event_ts_ms=event_ts_ms,
        taker_buy_base_volume=taker,
        taker_buy_method=None if taker is None else taker_buy_method,
    )


def _optional_sum(
    values: Iterable[decimal.Decimal | None],
) -> decimal.Decimal | None:
    """Sums values, or returns ``None`` when any is missing.

    Args:
        values: Values.

    Returns:
        Sum or ``None``.
    """
    total = decimals.ZERO
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _merge(
    group: Sequence[market.Candle],
    timeframe: timeframes.Timeframe,
    start_ms: int,
    *,
    now_ms: int,
    covered_to_ms: int,
    source: str | None,
) -> market.Candle:
    """Merges the constituents of one aggregate bar.

    Args:
        group: Ascending constituents of the bar.
        timeframe: Aggregate resolution.
        start_ms: Aggregate bar start, epoch milliseconds.
        now_ms: Receipt time, epoch milliseconds.
        covered_to_ms: Constituent data is complete before this time, epoch
            milliseconds.
        source: Provenance label (defaults to the first constituent's).

    Returns:
        Aggregate candle.
    """
    first = group[0]
    calendar = sessions.for_instrument(first.instrument_id)
    end_ms = calendar.end_of(timeframe, start_ms)
    counts = [c.trade_count for c in group if c.trade_count is not None]
    methods = {c.buy_volume_method for c in group}
    taker = _optional_sum(c.taker_buy_base_volume for c in group)
    return candle(
        instrument_id=first.instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        open_=first.open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        base_volume=sum((c.base_volume for c in group), decimals.ZERO),
        now_ms=now_ms,
        source=source or first.source,
        is_final=(
            end_ms <= now_ms
            and end_ms <= covered_to_ms
            and all(c.is_final for c in group)
        ),
        feed_state=first.feed_state,
        quote_turnover=_optional_sum(c.quote_turnover for c in group),
        trade_count=sum(counts) if len(counts) == len(group) else None,
        event_ts_ms=max(
            (c.event_ts_ms for c in group if c.event_ts_ms is not None),
            default=None,
        ),
        taker_buy_base_volume=taker if len(methods) == 1 else None,
        taker_buy_method=next(iter(methods)) if len(methods) == 1 else None,
    )


def aggregate(
    bars: Iterable[market.Candle],
    timeframe: timeframes.Timeframe,
    *,
    now_ms: int,
    covered_to_ms: int,
    source: str | None = None,
) -> list[market.Candle]:
    """Aggregates shorter bars onto the calendar grid of ``timeframe``.

    Args:
        bars: Constituent bars of one instrument (any order; duplicates by
            start keep the last).
        timeframe: Target resolution (must tile the constituents).
        now_ms: Receipt time, epoch milliseconds.
        covered_to_ms: Time before which the constituent data is complete,
            epoch milliseconds (e.g. the close of the latest published
            session).
        source: Optional provenance label of the aggregates.

    Returns:
        Ascending aggregate bars (one per grid bar with constituents).

    Raises:
        ValueError: If the constituents' timeframe does not nest in
            ``timeframe`` or they mix instruments.
    """
    by_start = {bar.start_ms: bar for bar in bars}
    if not by_start:
        return []
    ordered = [by_start[s] for s in sorted(by_start)]
    instrument_id = ordered[0].instrument_id
    small = ordered[0].timeframe
    calendar = sessions.for_instrument(instrument_id)
    if small is not timeframe and not calendar.contains(timeframe, small):
        raise ValueError(f"{small.value} bars do not tile {timeframe.value}")
    groups: dict[int, list[market.Candle]] = {}
    for bar in ordered:
        if bar.instrument_id != instrument_id or bar.timeframe is not small:
            raise ValueError("aggregate needs bars of one series")
        groups.setdefault(calendar.floor(timeframe, bar.start_ms), []).append(
            bar
        )
    return [
        _merge(
            groups[start],
            timeframe,
            start,
            now_ms=now_ms,
            covered_to_ms=covered_to_ms,
            source=source,
        )
        for start in sorted(groups)
    ]
