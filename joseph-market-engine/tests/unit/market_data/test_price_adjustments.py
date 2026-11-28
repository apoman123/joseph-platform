# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Provider-evidenced price continuity, real jumps and cache lifecycle tests."""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
from collections.abc import Mapping

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import price_adjustments
from joseph.market_data.domain import session_bars

_D = decimal.Decimal
_TF = timeframes.Timeframe
_ID = instruments.InstrumentId.parse("twse:etf:0050")
_BEFORE = 1749517200000  # June 10, 2025, 09:00 Taipei.
_AFTER = 1750208400000  # June 18, 2025, 09:00 Taipei.
_NOW = 1795568400000  # November 25, 2026, 09:00 Taipei.


def _bar(start: int, price: str = "100", *, tf: _TF = _TF.M1) -> market.Candle:
    """Build a native Taiwan bar retaining tape quantities and provenance.

    Args:
        start: UTC epoch milliseconds aligned to the requested timeframe.
        price: Finite decimal OHLC price as a string.
        tf: Optional native minute or daily source interval.

    Returns:
        Candle with known volume, turnover, counts and classified buy volume.
    """
    return session_bars.candle(
        instrument_id=_ID,
        timeframe=tf,
        start_ms=start,
        open_=_D(price),
        high=_D(price),
        low=_D(price),
        close=_D(price),
        base_volume=_D(10),
        now_ms=_NOW,
        source="fixture.raw",
        quote_turnover=_D(1000),
        trade_count=3,
        taker_buy_base_volume=_D(4),
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
    )


def test_daily_evidence_does_not_infer_actions_from_price_jumps() -> None:
    """Use paired provider prices; unity preserves a genuine 75% market fall.

    Returns:
        None after verifying split, reverse-split and non-action ratios.
    """
    raw = [_bar(_BEFORE, tf=_TF.D1), _bar(_AFTER, "25", tf=_TF.D1)]
    restored = [_bar(_BEFORE, "25", tf=_TF.D1), raw[1]]
    assert price_adjustments.daily_factors(raw, restored) == {
        _BEFORE: _D("0.25"),
        _AFTER: _D(1),
    }
    assert price_adjustments.daily_factors(raw, raw) == {
        _BEFORE: _D(1),
        _AFTER: _D(1),
    }
    assert price_adjustments.daily_factors(restored, raw)[_BEFORE] == 4


@pytest.mark.parametrize("bad", ["missing", "identity", "interval", "zero"])
def test_daily_evidence_rejects_unrelated_or_missing_observations(
    bad: str,
) -> None:
    """Reject unsafe daily pairings rather than silently assuming unity.

    Args:
        bad: Daily evidence error injected into one provider observation.

    Returns:
        None after checking the actionable provider error.
    """
    raw = [_bar(_BEFORE, tf=_TF.D1)]
    other = list(raw)
    if bad == "missing":
        other = []
    elif bad == "identity":
        other = [
            dataclasses.replace(
                raw[0],
                instrument_id=instruments.InstrumentId.parse("tpex:stock:6488"),
            )
        ]
    elif bad == "interval":
        other = [_bar(_BEFORE)]
    else:
        other = [_bar(_BEFORE, "0", tf=_TF.D1)]
    with pytest.raises(errors.VenueUnavailableError):
        price_adjustments.daily_factors(raw, other)


async def test_normalization_preserves_tape_and_shares_factor_reads() -> None:
    """Normalize before aggregation and share one factor read across charts.

    Returns:
        None after verifying prices, quantities, source and concurrent reuse.
    """
    clock = clock_lib.ManualClock(_NOW)
    normalizer = price_adjustments.DailyPriceNormalizer(clock)
    calls = []

    async def load(start: int, end: int) -> Mapping[int, decimal.Decimal]:
        """Yield once to expose duplicate concurrent loads.

        Args:
            start: Inclusive UTC daily session start in milliseconds.
            end: Exclusive UTC daily session end in milliseconds.

        Returns:
            Confirmed split factors for the requested fixture dates.
        """
        calls.append((start, end))
        await asyncio.sleep(0)
        return {_BEFORE: _D("0.25"), _AFTER: _D(1)}

    native = [_bar(_BEFORE), _bar(_AFTER, "25.5")]
    one, two = await asyncio.gather(
        normalizer.adjust(native, load), normalizer.adjust(native, load)
    )
    assert len(calls) == 1 and one == two
    assert [bar.close for bar in one] == [_D(25), _D("25.5")]
    for original, restored in zip(native, one, strict=True):
        assert restored.base_volume == original.base_volume
        assert restored.quote_turnover == original.quote_turnover
        assert restored.trade_count == original.trade_count
        assert restored.taker_buy_base_volume == original.taker_buy_base_volume
        assert restored.buy_volume_method == original.buy_volume_method
        assert restored.source == "fixture.raw.adjusted"
    month = session_bars.aggregate(
        one, _TF.MN1, now_ms=_NOW, covered_to_ms=_NOW
    )
    assert len(month) == 1 and month[0].high == _D("25.5")


async def _unexpected_daily_lookup(
    start: int, end: int
) -> Mapping[int, decimal.Decimal]:
    """Fail if a current-session operation attempts historical I/O.

    Args:
        start: Unused inclusive UTC milliseconds.
        end: Unused exclusive UTC milliseconds.

    Returns:
        No mapping; reaching this callback is a test failure.

    Raises:
        AssertionError: Always; this path must never be needed.
    """
    del start, end
    raise AssertionError("current-session quotes must not wait for daily bars")


async def test_current_session_and_empty_bars_require_no_daily_lookup() -> None:
    """Keep native live prices and avoid adding latency to current quotes.

    Returns:
        None after verifying no provider callback is executed.
    """
    normalizer = price_adjustments.DailyPriceNormalizer(
        clock_lib.ManualClock(_NOW)
    )

    current = _bar(_NOW)
    assert await normalizer.adjust([current], _unexpected_daily_lookup) == [
        current
    ]
    assert await normalizer.adjust([], _unexpected_daily_lookup) == []


async def test_cache_refreshes_actions_and_rejects_missing_evidence() -> None:
    """Reload new factors after the epoch and never cache an unverifiable date.

    Returns:
        None after checking changed prices, raw tape preservation and retry.
    """
    clock = clock_lib.ManualClock(_NOW)
    normalizer = price_adjustments.DailyPriceNormalizer(clock)
    factor = _D("0.25")

    async def load(start: int, end: int) -> Mapping[int, decimal.Decimal]:
        """Return the provider's current factor only for the evidenced date.

        Args:
            start: Unused inclusive UTC milliseconds.
            end: Unused exclusive UTC milliseconds.

        Returns:
            One provider-confirmed daily factor.
        """
        del start, end
        return {_BEFORE: factor}

    assert (await normalizer.adjust([_bar(_BEFORE)], load))[0].close == 25
    factor = _D("0.125")
    clock.current_ms += price_adjustments.REFRESH_MS
    assert (await normalizer.adjust([_bar(_BEFORE)], load))[0].close == _D(
        "12.5"
    )
    with pytest.raises(errors.VenueUnavailableError, match="Missing confirmed"):
        await normalizer.adjust([_bar(_AFTER)], load)


async def test_epoch_change_during_evidence_load_never_fails_or_mixes() -> None:
    """Crossing a cache epoch reloads what the new epoch lacks, never fails.

    Returns:
        None after checking that one call uses only fresh evidence.
    """
    clock = clock_lib.ManualClock(_NOW)
    normalizer = price_adjustments.DailyPriceNormalizer(clock)
    loads: list[tuple[int, int]] = []
    ratios = {_BEFORE: _D("0.25"), _AFTER: _D("0.5")}

    async def load(start: int, end: int) -> Mapping[int, decimal.Decimal]:
        """Advance the clock past an epoch during the first read only.

        Args:
            start: Inclusive UTC milliseconds.
            end: Exclusive UTC milliseconds.

        Returns:
            The ratios of the sessions in the window.
        """
        loads.append((start, end))
        if len(loads) == 2:
            clock.current_ms += price_adjustments.REFRESH_MS
        return {s: r for s, r in ratios.items() if start <= s < end}

    first = await normalizer.adjust([_bar(_BEFORE)], load)
    assert first[0].close == _D("25.00")
    ratios[_BEFORE] = _D("0.125")
    both = await normalizer.adjust([_bar(_BEFORE), _bar(_AFTER)], load)
    # The second read crossed the epoch: the session cached in the old epoch
    # was read again in the new one instead of mixing the two.
    assert len(loads) == 3
    assert [bar.close for bar in both] == [_D("12.5"), _D("50")]


def test_fixture_timestamps_are_actual_xtai_session_opens() -> None:
    """Keep regression timestamps on actual supported exchange sessions.

    Returns:
        None after validating all fixture anchors.
    """
    assert all(
        sessions.XTAI.is_aligned(_TF.D1, t) for t in [_BEFORE, _AFTER, _NOW]
    )


@pytest.mark.parametrize(
    "now,day",
    [
        (_NOW - 3_600_000, _NOW - 86_400_000),
        (_NOW + 3 * 86_400_000, _NOW + 2 * 86_400_000),
    ],
)
async def test_previous_session_adjusts_before_open_and_on_weekends(
    now: int,
    day: int,
) -> None:
    """Do not treat yesterday as today's live session before the next open.

    Args:
        now: Wednesday pre-open or Saturday morning, UTC milliseconds.
        day: Previous trading session open requiring an updated price ratio.

    Returns:
        None after verifying an overnight action also rebases the latest bar.
    """
    normalizer = price_adjustments.DailyPriceNormalizer(
        clock_lib.ManualClock(now)
    )

    async def load(start: int, end: int) -> Mapping[int, decimal.Decimal]:
        """Provide confirmed action evidence for the previous trading session.

        Args:
            start: Unused inclusive UTC start in milliseconds.
            end: Unused exclusive UTC end in milliseconds.

        Returns:
            A confirmed quarter-price factor for the previous session.
        """
        del start, end
        return {day: _D("0.25")}

    assert (await normalizer.adjust([_bar(day)], load))[0].close == 25
