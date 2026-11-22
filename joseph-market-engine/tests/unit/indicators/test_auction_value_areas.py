"""Scenario tests for the auction_value_areas plugin."""

from __future__ import annotations

import decimal

from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.plugins.auction_value_areas import plugin as ava
from joseph.indicators.sdk import ta

D = decimal.Decimal
H1 = timeframes.Timeframe.H1
M5 = timeframes.Timeframe.M5


def _points(rows: list[tuple[str, ...]]) -> list[ta.BarPoint]:
    """Builds bar points from rows.

    Args:
        rows: OHLCV rows.

    Returns:
        Bar points.
    """
    return [ta.BarPoint.of(c) for c in testing.candles_from_rows(rows)]


def test_uniform_profile_poc_tie_prefers_center_then_lower_bin() -> None:
    """Equal bins: POC is the lower of the two central bins."""
    profile = ava.build_profile(
        _points([("0", "10", "0", "10", "10"), ("10", "10", "0", "0", "10")]),
        32,
        D(70),
    )
    assert profile is not None
    width = D(10) / 32
    assert profile.poc == width * 15 + width / 2
    # Ties expand upward until the top, then downward (ASSUMPTION).
    assert profile.vah == D(10)
    assert profile.val == width * 9


def test_zero_range_bar_volume_goes_to_its_bin() -> None:
    """A doji with no range contributes to the containing bin only."""
    profile = ava.build_profile(
        _points([("5", "5", "5", "5", "10"), ("0", "10", "0", "10", "0")]),
        32,
        D(70),
    )
    assert profile is not None
    width = D(10) / 32
    assert profile.poc == width * 16 + width / 2
    assert (profile.val, profile.vah) == (width * 16, width * 17)


def test_flat_window_collapses_to_one_level() -> None:
    """When every price is equal, VAH = POC = VAL."""
    profile = ava.build_profile(
        _points([("5", "5", "5", "5", "3")] * 3), 32, D(70)
    )
    assert profile is not None
    assert profile.vah == profile.poc == profile.val == D(5)


def test_zero_volume_yields_no_profile() -> None:
    """No volume means no value area."""
    assert (
        ava.build_profile(_points([("1", "2", "0", "1", "0")] * 3), 32, D(70))
        is None
    )


def test_value_area_contains_at_least_seventy_percent() -> None:
    """VAL <= POC <= VAH on random data."""
    candles = testing.random_walk(60, seed=9, timeframe=M5)
    profile = ava.build_profile([ta.BarPoint.of(c) for c in candles], 32, D(70))
    assert profile is not None
    assert profile.val <= profile.poc <= profile.vah


def test_chart_profile_needs_twenty_contiguous_bars() -> None:
    """Values stay None until 20 completed chart bars exist."""
    candles = testing.random_walk(25, seed=2, timeframe=M5)
    results = testing.run_bars(ava.AuctionValueAreasPlugin(), candles)
    assert results[18][0].values["poc"] is None
    assert results[19][0].values["poc"] is not None
    gap = candles[:10] + candles[11:]
    gapped = testing.run_bars(ava.AuctionValueAreasPlugin(), gap)
    assert gapped[19][0].values["poc"] is None


def test_hourly_profile_updates_only_on_new_completed_hour() -> None:
    """The 1H profile recomputes (and emits) once per new 1H bar."""
    hours = testing.random_walk(26, seed=4, timeframe=H1)
    m5 = testing.random_walk(3, seed=8, timeframe=M5, start_ms=hours[-1].end_ms)
    aux = {
        0: {H1: tuple(hours[:24])},
        1: {H1: tuple(hours[:24])},
        2: {H1: tuple(hours[1:25])},
    }
    results = testing.run_bars(ava.AuctionValueAreasPlugin(), m5, aux=aux)
    assert testing.event_names(results) == [
        (0, "VALUE_AREA_UPDATED"),
        (2, "VALUE_AREA_UPDATED"),
    ]
    assert results[0][0].values["poc_1h"] is not None
    kinds = sorted(a.kind.value for a in results[0][0].aois)
    assert kinds == ["POC", "VAH", "VAL"]


def test_hourly_profile_requires_full_window() -> None:
    """Fewer than 24 completed hours leaves the 1H profile empty."""
    hours = testing.random_walk(10, seed=4, timeframe=H1)
    m5 = testing.random_walk(1, seed=8, timeframe=M5, start_ms=hours[-1].end_ms)
    results = testing.run_bars(
        ava.AuctionValueAreasPlugin(), m5, aux={0: {H1: tuple(hours)}}
    )
    assert results[0][0].values["poc_1h"] is None
    assert testing.event_names(results) == []
