# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit and reference tests of the anchored VWAP and regression channel.

The reference tests recompute every bar from scratch in ``decimal.Decimal``
(high precision, no running sums) and require the published values to stay
within one tick of it.
"""

from __future__ import annotations

import decimal
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

import pytest

from joseph.domain import errors
from joseph.domain import market
from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.builtin import anchored_vwap
from joseph.indicators.builtin import regression_channel

TF = timeframes.Timeframe
D = decimal.Decimal
Validate: TypeAlias = Callable[[str, str, object], None]
_START = testing.DEFAULT_START_MS
_CONTEXT = decimal.Context(prec=50)
_TICK = D("0.01")
_RATIO_TOLERANCE = D("0.0001")


def _source(candle: market.Candle, name: str) -> D:
    """Evaluates a price source exactly.

    Args:
        candle: Bar.
        name: ``close``, ``hl2``, ``hlc3`` or ``ohlc4``.

    Returns:
        The source price as an exact quotient.
    """
    with decimal.localcontext(_CONTEXT):
        if name == "hl2":
            return (candle.high + candle.low) / 2
        if name == "hlc3":
            return (candle.high + candle.low + candle.close) / 3
        if name == "ohlc4":
            return (candle.open + candle.high + candle.low + candle.close) / 4
        return candle.close


def _close_to(got: object, want: D | None, tolerance: D) -> bool:
    """Compares a published value with a reference value.

    Args:
        got: Published value (``Decimal`` or ``None``).
        want: Reference value or ``None``.
        tolerance: Allowed absolute difference.

    Returns:
        True when both are ``None`` or within ``tolerance``.
    """
    if want is None or got is None:
        return want is None and got is None
    assert isinstance(got, D)
    return abs(got - want) <= tolerance


def _reference_vwap(
    window: Sequence[market.Candle], source: str, bands: Sequence[D]
) -> dict[str, D | None]:
    """Recomputes the VWAP and bands of one anchor period.

    Args:
        window: Bars from the anchor bar through the current one.
        source: Price source.
        bands: Band multipliers.

    Returns:
        ``vwap``, ``stdev``, ``upper_k`` and ``lower_k``.
    """
    with decimal.localcontext(_CONTEXT):
        traded = [c for c in window if c.base_volume > 0]
        volume = sum((c.base_volume for c in traded), D(0))
        values: dict[str, D | None] = dict.fromkeys(
            ("vwap", "stdev", "upper_1", "lower_1", "upper_2", "lower_2")
        )
        if volume == 0:
            return values
        vwap = sum((_source(c, source) * c.base_volume for c in traded), D(0))
        vwap /= volume
        variance = sum(
            ((_source(c, source) - vwap) ** 2 * c.base_volume for c in traded),
            D(0),
        )
        stdev = (variance / volume).sqrt()
        values["vwap"] = vwap
        values["stdev"] = stdev
        for index, multiplier in enumerate(bands, start=1):
            if multiplier > 0:
                values[f"upper_{index}"] = vwap + multiplier * stdev
                values[f"lower_{index}"] = vwap - multiplier * stdev
        return values


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"anchor": "week", "source": "close", "band_2": "2.5"},
        {"source": "ohlc4", "band_1": "0", "band_2": "1"},
    ],
)
@pytest.mark.parametrize("base", ["100", "60000"])
def test_vwap_matches_a_decimal_recompute(
    settings: Mapping[str, object], base: str
) -> None:
    """Every bar's VWAP and bands are within a tick of the exact value."""
    candles = testing.random_walk(600, seed=7, timeframe=TF.H1, base=base)
    plugin = anchored_vwap.AnchoredVwapPlugin()
    resolved = plugin.resolve_settings(settings)
    source = str(resolved["source"])
    bands = [D(str(resolved["band_1"])), D(str(resolved["band_2"]))]
    anchor = str(resolved["anchor"])
    results = testing.run_bars(plugin, candles, settings)
    period_start = 0
    for index, (snapshot, _) in enumerate(results):
        period = anchored_vwap.utc_period_start(candles[index].start_ms, anchor)
        if index == 0 or period != anchored_vwap.utc_period_start(
            candles[index - 1].start_ms, anchor
        ):
            period_start = index
        want = _reference_vwap(candles[period_start : index + 1], source, bands)
        for key, value in want.items():
            got = snapshot.values[key]
            assert _close_to(got, value, _TICK), (index, key, got, value)
        assert snapshot.values["anchor_ms"] == candles[period_start].start_ms
        assert snapshot.values["bars"] == index - period_start + 1


def test_bars_without_volume_add_nothing() -> None:
    """A zero-volume bar counts in ``bars`` but leaves the VWAP unchanged."""
    candles = testing.candles_from_rows(
        [("10", "10", "10", "10", "0"), ("20", "20", "20", "20", "2")],
        TF.M5,
    )
    first, second = (
        s
        for s, _ in testing.run_bars(
            anchored_vwap.AnchoredVwapPlugin(), candles, {"source": "close"}
        )
    )
    assert first.values["vwap"] is None
    assert first.values["bars"] == 1
    assert second.values["vwap"] == D(20)
    assert second.values["stdev"] == D(0)
    assert second.values["bars"] == 2


def test_time_anchor_requires_anchor_ms() -> None:
    """An anchored VWAP without an anchor time is a validation error."""
    plugin = anchored_vwap.AnchoredVwapPlugin()
    with pytest.raises(errors.ValidationFailedError, match="anchor_ms"):
        plugin.initialize(testing.DEFAULT_INSTRUMENT, TF.M5, {"anchor": "time"})


def test_session_vwap_resets_each_utc_day() -> None:
    """The default session anchor restarts at 00:00 UTC."""
    plugin = anchored_vwap.AnchoredVwapPlugin()
    day = 86_400_000
    first = _START - _START % day
    candles = [
        testing.make_candle(TF.H12, first, "10", "10", "10", "10", "1"),
        testing.make_candle(TF.H12, first + day // 2, "20", "20", "20", "20"),
        testing.make_candle(TF.H12, first + day, "30", "30", "30", "30", "1"),
    ]
    results = testing.run_bars(plugin, candles, {"source": "close"})
    assert results[1][0].values["anchor_ms"] == first
    assert results[2][0].values["vwap"] == D(30)
    assert results[2][0].values["anchor_ms"] == first + day
    assert results[2][0].values["bars"] == 1


def test_utc_periods() -> None:
    """Days, ISO weeks from Monday and calendar months, in UTC."""
    day = 86_400_000
    monday = _START  # 2026-11-16 00:00 UTC, a Monday.
    assert anchored_vwap.utc_period_start(monday + 5, "session") == monday
    assert anchored_vwap.utc_period_start(monday + 6 * day, "week") == monday
    assert anchored_vwap.utc_period_start(monday + 7 * day, "week") == (
        monday + 7 * day
    )
    november = monday - 15 * day
    assert anchored_vwap.utc_period_start(monday, "month") == november
    with pytest.raises(ValueError, match="calendar anchor"):
        anchored_vwap.utc_period_start(monday, "time")


def _reference_fit(
    window: Sequence[market.Candle], source: str
) -> dict[str, D]:
    """Recomputes a least-squares channel over a window.

    Args:
        window: Bars of the fit, oldest first (at least two).
        source: Price source.

    Returns:
        Line ends, slope, residual stdev, excursions and Pearson's R. R is
        left out for a flat window: prices equal in decimal may differ by an
        ulp in doubles, which makes R of such a window any value in [-1, 1].
    """
    with decimal.localcontext(_CONTEXT):
        count = len(window)
        ys = [_source(c, source) for c in window]
        mean_x = D(count - 1) / 2
        mean_y = sum(ys, D(0)) / count
        sxy = sum(((x - mean_x) * (y - mean_y) for x, y in enumerate(ys)), D(0))
        sxx = sum(((x - mean_x) ** 2 for x in range(count)), D(0))
        syy = sum(((y - mean_y) ** 2 for y in ys), D(0))
        slope = sxy / sxx
        start = mean_y - slope * mean_x
        line = [start + slope * x for x in range(count)]
        residuals = sum(
            ((y - f) ** 2 for y, f in zip(ys, line, strict=True)), D(0)
        )
        stdev = (residuals / (count - 1)).sqrt()
        fit = {
            "start": start,
            "end": line[-1],
            "slope": slope,
            "stdev": stdev,
            "max_up": max(
                c.high - f for c, f in zip(window, line, strict=True)
            ),
            "max_down": max(
                f - c.low for c, f in zip(window, line, strict=True)
            ),
        }
        if syy > 0:
            fit["pearson_r"] = sxy / (sxx * syy).sqrt()
        return fit


@pytest.mark.parametrize(
    ("settings", "anchor_index"),
    [
        ({"length": 20}, None),
        ({"length": 2, "source": "hl2"}, None),
        ({"length": 50, "source": "ohlc4", "deviations": "1"}, None),
        ({"source": "hlc3"}, 40),
    ],
)
@pytest.mark.parametrize("base", ["100", "60000"])
def test_regression_matches_a_decimal_recompute(
    settings: dict[str, object], anchor_index: int | None, base: str
) -> None:
    """Every bar's channel is within a tick of the exact least squares."""
    candles = testing.random_walk(240, seed=11, timeframe=TF.M5, base=base)
    if anchor_index is not None:
        settings = {**settings, "anchor_ms": candles[anchor_index].start_ms + 7}
    plugin = regression_channel.RegressionChannelPlugin()
    resolved = plugin.resolve_settings(settings)
    source = str(resolved["source"])
    deviations = D(str(resolved["deviations"]))
    length = int(str(resolved["length"]))
    results = testing.run_bars(plugin, candles, settings)
    for index, (snapshot, _) in enumerate(results):
        first = index - length + 1 if anchor_index is None else anchor_index
        if index - first + 1 < 2 or first < 0:
            assert snapshot.values["end"] is None
            assert snapshot.values["bars"] == 0
            continue
        want = _reference_fit(candles[first : index + 1], source)
        values = snapshot.values
        assert values["bars"] == index - first + 1
        assert values["start_ms"] == candles[first].start_ms
        if "pearson_r" in want:
            assert _close_to(
                values["pearson_r"], want["pearson_r"], _RATIO_TOLERANCE
            )
        with decimal.localcontext(_CONTEXT):
            width = deviations * want["stdev"]
            expected = {
                **want,
                "upper_start": want["start"] + width,
                "lower_start": want["start"] - width,
                "upper_end": want["end"] + width,
                "lower_end": want["end"] - width,
            }
        for key, value in expected.items():
            if key == "pearson_r":
                continue
            assert _close_to(values[key], value, _TICK), (index, key)


def test_rolling_regression_warms_up_after_length_bars() -> None:
    """The rolling channel publishes once ``length`` bars exist."""
    plugin = regression_channel.RegressionChannelPlugin()
    candles = testing.random_walk(12, timeframe=TF.M5, seed=5)
    results = testing.run_bars(plugin, candles, {"length": 10})
    assert results[8][0].values["end"] is None
    assert not results[8][0].warmup_complete
    snapshot = results[-1][0]
    assert snapshot.warmup_complete
    assert snapshot.values["bars"] == 10
    assert snapshot.values["start_ms"] == candles[2].start_ms
    assert snapshot.values["anchor_in_window"] is True


def test_upper_hull_answers_the_largest_offset() -> None:
    """The hull's maximum offset equals a brute-force maximum."""
    points = [(0.0, 1.0), (1.0, 3.0), (2.0, 2.0), (3.0, 5.0), (4.0, 1.0)]
    hull = regression_channel.UpperHull()
    for x, y in points:
        hull = hull.add(x, y)
    for slope in (-2.0, -0.5, 0.0, 0.7, 3.0):
        assert hull.max_offset(slope) == max(y - slope * x for x, y in points)


def test_infos_validate(contract_validate: Validate) -> None:
    """Both studies publish valid descriptors with their unchanged ids."""
    vwap = anchored_vwap.AnchoredVwapPlugin()
    regression = regression_channel.RegressionChannelPlugin()
    for plugin in (vwap, regression):
        contract_validate(
            "indicators.schema.json", "IndicatorPluginInfo", plugin.info()
        )
    assert vwap.info()["display_name"] == "VWAP"
    assert regression.display.layer is False
    assert vwap.indicator_version == regression.indicator_version == "2.0.0"
