# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Deterministic candle builders for indicator and strategy tests.

Shipped with the package (no runtime cost) so every bounded context can
build consistent multi-timeframe fixtures: lower-timeframe series are
generated first and higher timeframes are aggregated from them, exactly as
a venue would.

Every builder follows the instrument's session calendar: pass an exchange
instrument (``STOCK_INSTRUMENT`` is ``mock:stock:ACME`` on XNYS) and bars
start at the session open, the last bar of a session is cut at the close,
weekends and holidays are skipped, and ``aggregate`` builds session daily
and weekly bars.
"""

from __future__ import annotations

import decimal
import random
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.indicators.sdk import protocol

Number = str | int | decimal.Decimal
"""Accepted numeric input for builders."""

DEFAULT_INSTRUMENT = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
# 2026-11-16 00:00:00 UTC (a Monday), aligned for every timeframe.
DEFAULT_START_MS = 1_794_787_200_000
STOCK_INSTRUMENT = instruments.InstrumentId.parse("mock:stock:ACME")
"""DEMO stock on the XNYS calendar (09:30-16:00 America/New_York)."""
# 2026-11-16 09:30 America/New_York (14:30 UTC), a Monday session open.
STOCK_START_MS = 1_794_839_400_000


def bar_starts(
    timeframe: timeframes.Timeframe,
    start_ms: int,
    count: int,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[int]:
    """Returns consecutive bar starts on the instrument's calendar.

    Args:
        timeframe: Bar resolution.
        start_ms: First bar start (a later time counts from the next bar).
        count: Number of bars.
        instrument_id: Instrument.

    Returns:
        Ascending bar starts.
    """
    calendar = sessions.for_instrument(instrument_id)
    starts: list[int] = []
    current = calendar.shift(timeframe, start_ms, 0)
    for _ in range(count):
        starts.append(current)
        current = calendar.shift(timeframe, current, 1)
    return starts


def make_candle(  # noqa: PLR0917 - OHLCV reads naturally positionally.
    timeframe: timeframes.Timeframe,
    start_ms: int,
    open_: Number,
    high: Number,
    low: Number,
    close: Number,
    volume: Number = "10",
    *,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
    feed_state: quality.FeedState = quality.FeedState.LIVE,
    is_final: bool = True,
    revision: int = 0,
) -> market.Candle:
    """Builds one candle.

    Args:
        timeframe: Bar resolution.
        start_ms: Aligned bar start.
        open_: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Base volume.
        instrument_id: Instrument.
        feed_state: Provenance.
        is_final: Whether the bar is closed.
        revision: Correction counter.

    Returns:
        The candle.
    """
    end_ms = sessions.for_instrument(instrument_id).end_of(timeframe, start_ms)
    return market.Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        open=decimals.to_decimal(open_),
        high=decimals.to_decimal(high),
        low=decimals.to_decimal(low),
        close=decimals.to_decimal(close),
        base_volume=decimals.to_decimal(volume),
        is_final=is_final,
        source="test",
        feed_state=feed_state,
        received_at_ms=end_ms,
        revision=revision,
    )


def candles_from_rows(
    rows: Iterable[Sequence[Number]],
    timeframe: timeframes.Timeframe = timeframes.Timeframe.M5,
    start_ms: int = DEFAULT_START_MS,
    *,
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[market.Candle]:
    """Builds consecutive candles from ``(open, high, low, close[, vol])``.

    Args:
        rows: OHLC(V) rows, oldest first.
        timeframe: Bar resolution.
        start_ms: Start of the first bar.
        instrument_id: Instrument.

    Returns:
        Candles.
    """
    rows = list(rows)
    starts = bar_starts(timeframe, start_ms, len(rows), instrument_id)
    candles = []
    for index, row in enumerate(rows):
        open_, high, low, close = row[:4]
        volume = row[4] if len(row) > 4 else "10"
        candles.append(
            make_candle(
                timeframe,
                starts[index],
                open_,
                high,
                low,
                close,
                volume,
                instrument_id=instrument_id,
            )
        )
    return candles


def random_walk(
    count: int,
    *,
    seed: int,
    timeframe: timeframes.Timeframe = timeframes.Timeframe.M1,
    start_ms: int = DEFAULT_START_MS,
    base: Number = "100",
    instrument_id: instruments.InstrumentId = DEFAULT_INSTRUMENT,
) -> list[market.Candle]:
    """Generates a deterministic random-walk series.

    Prices move in steps of 0.01 with occasional large moves, so pivots,
    gaps and sweeps occur naturally.

    Args:
        count: Number of bars.
        seed: Random seed.
        timeframe: Bar resolution.
        start_ms: Start of the first bar.
        base: Starting price.
        instrument_id: Instrument.

    Returns:
        Candles, oldest first.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic test data.
    tick = decimal.Decimal("0.01")
    price = decimals.to_decimal(base)
    starts = bar_starts(timeframe, start_ms, count, instrument_id)
    candles = []
    for index in range(count):
        open_ = price
        move = rng.choice((1, 2, 3, 5, 8, 13)) * rng.choice((-1, 1))
        if rng.random() < 0.05:
            move *= 6
        close = max(tick, open_ + tick * move)
        high = max(open_, close) + tick * rng.randint(0, 6)
        low = max(tick / 2, min(open_, close) - tick * rng.randint(0, 6))
        volume = decimal.Decimal(rng.randint(1, 50))
        if rng.random() < 0.1:
            volume *= 4
        candles.append(
            make_candle(
                timeframe,
                starts[index],
                open_,
                high,
                low,
                close,
                volume,
                instrument_id=instrument_id,
            )
        )
        price = close
    return candles


def aggregate(
    candles: Sequence[market.Candle], timeframe: timeframes.Timeframe
) -> list[market.Candle]:
    """Aggregates lower-timeframe candles into complete higher bars.

    Only buckets fully covered by the input are emitted, mirroring how a
    venue publishes closed bars.

    Args:
        candles: Contiguous lower-timeframe candles, oldest first.
        timeframe: Target timeframe (must contain the source timeframe).

    Returns:
        Aggregated final candles.

    Raises:
        ValueError: If the timeframes do not nest.
    """
    if not candles:
        return []
    source_tf = candles[0].timeframe
    calendar = sessions.for_instrument(candles[0].instrument_id)
    if not calendar.contains(timeframe, source_tf) and (
        timeframe is not source_tf
    ):
        raise ValueError(f"{source_tf} does not nest in {timeframe}")
    buckets: dict[int, list[market.Candle]] = {}
    for candle in candles:
        buckets.setdefault(
            calendar.floor(timeframe, candle.start_ms), []
        ).append(candle)
    result = []
    for start, members in sorted(buckets.items()):
        end = calendar.end_of(timeframe, start)
        per_bucket = calendar.bars_between(source_tf, start, end)
        if len(members) != per_bucket:
            continue
        result.append(
            market.Candle(
                instrument_id=members[0].instrument_id,
                timeframe=timeframe,
                start_ms=start,
                end_ms=end,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                base_volume=sum(
                    (c.base_volume for c in members), decimals.ZERO
                ),
                is_final=True,
                source="test.aggregate",
                feed_state=members[-1].feed_state,
                received_at_ms=end,
                taker_buy_base_volume=_sum_taker(members),
                taker_buy_method=members[-1].taker_buy_method,
            )
        )
    return result


def _sum_taker(members: Sequence[market.Candle]) -> decimal.Decimal | None:
    """Sums the members' buy volume when every member carries one.

    Args:
        members: Candles of one bucket.

    Returns:
        Total buy volume, or ``None`` when any member has none.
    """
    if any(c.taker_buy_base_volume is None for c in members):
        return None
    return sum(
        (c.taker_buy_base_volume or decimals.ZERO for c in members),
        decimals.ZERO,
    )


def run_bars(
    plugin: protocol.IndicatorPlugin,
    candles: Sequence[market.Candle],
    settings: Mapping[str, object] | None = None,
    *,
    intrabars: Mapping[
        int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]
    ]
    | None = None,
    aux: Mapping[int, Mapping[timeframes.Timeframe, tuple[market.Candle, ...]]]
    | None = None,
) -> list[tuple[structures.IndicatorSnapshot, protocol.PluginOutput]]:
    """Runs one plugin bar by bar and records snapshots and outputs.

    Args:
        plugin: Plugin under test.
        candles: Closed series bars, oldest first.
        settings: Setting overrides.
        intrabars: Optional intrabars per bar index.
        aux: Optional aux bars per bar index.

    Returns:
        ``(snapshot, output)`` after each bar.
    """
    if not candles:
        return []
    state = plugin.initialize(
        candles[0].instrument_id, candles[0].timeframe, settings
    )
    results = []
    for index, candle in enumerate(candles):
        bar = protocol.BarInput(
            candle=candle,
            intrabars=dict((intrabars or {}).get(index, {})),
            aux=dict((aux or {}).get(index, {})),
        )
        state, output = plugin.on_bar_closed(state, bar)
        results.append((plugin.snapshot(state), output))
    return results


def event_names(
    results: Sequence[
        tuple[structures.IndicatorSnapshot, protocol.PluginOutput]
    ],
) -> list[tuple[int, str]]:
    """Flattens ``run_bars`` results into ``(bar_index, event_name)``.

    Args:
        results: Output of ``run_bars``.

    Returns:
        Event occurrences in order.
    """
    return [
        (index, event.name.value)
        for index, (_, output) in enumerate(results)
        for event in output.events
    ]
