"""Scenario tests for the order_flow_pressure plugin."""

from __future__ import annotations

import decimal

import pytest

from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.plugins.order_flow_pressure import plugin as flow

D = decimal.Decimal
M1 = timeframes.Timeframe.M1
M5 = timeframes.Timeframe.M5
BASE = [("100", "101", "99", "100", "10")] * 20
UP_INTRABARS = [
    ("100", "100.5", "99.9", "100.4", "4"),
    ("100.4", "101", "100.3", "100.9", "4"),
    ("100.9", "104", "100.8", "103", "4"),
    ("103", "103.2", "102.8", "103", "4"),
    ("103", "103.1", "100.4", "100.5", "4"),
]


def _run(
    rows: list[tuple[str, ...]],
    intrabar_rows: list[tuple[str, ...]] | None,
    settings: dict[str, object] | None = None,
) -> list:  # type: ignore[type-arg]
    """Runs the plugin with intrabars attached to the last bar.

    Args:
        rows: Series OHLCV rows.
        intrabar_rows: 1m OHLCV rows inside the last bar (or ``None``).
        settings: Overrides.

    Returns:
        ``run_bars`` results.
    """
    candles = testing.candles_from_rows(rows, M5)
    intrabars = {}
    if intrabar_rows is not None:
        last = candles[-1]
        intrabars = {
            len(candles) - 1: {
                M1: tuple(
                    testing.candles_from_rows(intrabar_rows, M1, last.start_ms)
                )
            }
        }
    return testing.run_bars(
        flow.OrderFlowPressurePlugin(), candles, settings, intrabars=intrabars
    )


def test_doji_carries_previous_direction() -> None:
    """Doji intrabars inherit the last direction; leading doji is unknown."""
    candles = testing.candles_from_rows(
        [
            ("1", "2", "0.5", "1.5", "3"),
            ("1.5", "2", "1", "1.5", "2"),
            ("1.5", "2", "1", "1", "5"),
        ],
        M1,
    )
    up, down, total, direction = flow.classify(candles, 0)
    assert (up, down, total, direction) == (D(5), D(5), D(10), -1)
    leading = testing.candles_from_rows([("1", "2", "0.5", "1", "4")], M1)
    assert flow.classify(leading, 0)[:2] == (D(0), D(0))


def test_buyer_failure_on_heavy_buying_with_upper_rejection() -> None:
    """RelVol >= 1.5, delta >= 20 and a 35% upper wick."""
    results = _run([*BASE, ("100", "104", "99.8", "100.5", "20")], UP_INTRABARS)
    assert testing.event_names(results) == [(20, "BUYER_FAILURE")]
    values = results[-1][0].values
    assert values["delta_pct"] == D(60)
    assert values["rel_volume"] == D(2)
    assert values["coverage_pct"] == D(100)
    assert values["intrabar_count"] == 5
    assert values["method"] == "candle_direction"


def test_seller_failure_is_the_mirror() -> None:
    """Heavy selling with a long lower wick is a seller failure."""
    down = [
        (str(200 - D(o)), str(200 - D(lo)), str(200 - D(h)), str(200 - D(c)), v)
        for o, h, lo, c, v in UP_INTRABARS
    ]
    bar = ("100", "100.2", "96", "99.5", "20")
    results = _run([*BASE, bar], down)
    assert testing.event_names(results) == [(20, "SELLER_FAILURE")]


def test_buyer_breakout_needs_body_close_near_high_and_new_high() -> None:
    """A strong close above the prior 20-bar high is a buyer breakout."""
    bar = ("100", "103", "99.9", "102.9", "20")
    results = _run([*BASE, bar], UP_INTRABARS)
    assert testing.event_names(results) == [(20, "BUYER_BREAKOUT")]


def test_low_coverage_suppresses_values_and_events() -> None:
    """Intrabar coverage below 80% yields no delta."""
    thin = [(o, h, lo, c, "3") for o, h, lo, c, _ in UP_INTRABARS]
    results = _run([*BASE, ("100", "104", "99.8", "100.5", "20")], thin)
    values = results[-1][0].values
    assert values["coverage_pct"] == D(75)
    assert values["delta_pct"] is None
    assert testing.event_names(results) == []
    assert results[-1][0].data_quality.coverage_pct == "75"


def test_too_few_intrabars_suppresses_delta() -> None:
    """Fewer than five intrabars is insufficient data."""
    four = [(o, h, lo, c, "5") for o, h, lo, c, _ in UP_INTRABARS[:4]]
    results = _run([*BASE, ("100", "104", "99.8", "100.5", "20")], four)
    assert results[-1][0].values["delta_pct"] is None


def test_relative_volume_threshold_is_inclusive() -> None:
    """RelVol exactly 1.5 qualifies; just below does not."""
    exact = [(o, h, lo, c, "3") for o, h, lo, c, _ in UP_INTRABARS]
    hit = _run([*BASE, ("100", "104", "99.8", "100.5", "15")], exact)
    assert testing.event_names(hit) == [(20, "BUYER_FAILURE")]
    below = [(o, h, lo, c, "2.98") for o, h, lo, c, _ in UP_INTRABARS]
    miss = _run([*BASE, ("100", "104", "99.8", "100.5", "14.9")], below)
    assert testing.event_names(miss) == []


def test_baseline_uses_previous_bars_only() -> None:
    """Without 20 previous bars there is no relative volume."""
    results = _run(
        [*BASE[:10], ("100", "104", "99.8", "100.5", "20")], UP_INTRABARS
    )
    assert results[-1][0].values["rel_volume"] is None
    assert testing.event_names(results) == []


def test_intrabar_timeframe_must_nest() -> None:
    """A 1m series cannot use 1m intrabars."""
    with pytest.raises(ValueError):
        flow.OrderFlowPressurePlugin().initialize(
            testing.DEFAULT_INSTRUMENT,
            timeframes.Timeframe.M3,
            {"intrabar_tf": "5m"},
        )


def test_required_streams_follow_settings() -> None:
    """The intrabar stream reflects the configured timeframe."""
    plugin = flow.OrderFlowPressurePlugin()
    assert plugin.required_streams({"intrabar_tf": "3m"}) == frozenset(
        {"candles:chart", "intrabars:3m"}
    )
