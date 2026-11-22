"""Scenario tests for the daily_bias plugin."""

from __future__ import annotations

import datetime
import decimal

from joseph.domain import timeframes
from joseph.indicators import testing
from joseph.indicators.plugins.daily_bias import plugin as daily_bias

D = decimal.Decimal
H1 = timeframes.Timeframe.H1

# Pivot high 105 (bar 3, confirmed bar 6); close above at bar 11; pivot low
# 96.5 (bar 9, confirmed 12) broken at bar 14; pivot low 94 (bar 15,
# confirmed 18) broken at bar 19.
STRUCTURE_ROWS = [
    ("100", "101", "99", "100.5"),
    ("100.5", "102", "100", "101.5"),
    ("101.5", "103", "101", "102.5"),
    ("102.5", "105", "102", "103"),
    ("103", "104", "101", "101.5"),
    ("101.5", "103", "100", "100.5"),
    ("100.5", "102", "99", "99.5"),
    ("99.5", "101", "98", "98.5"),
    ("98.5", "100", "97", "97.5"),
    ("97.5", "99", "96.5", "98"),
    ("98", "104", "97.5", "103.5"),
    ("103.5", "106", "103", "105.5"),
    ("105.5", "106.5", "104", "106"),
    ("106", "106.2", "100", "100.5"),
    ("100.5", "101", "95", "96"),
    ("96", "97", "94", "94.5"),
    ("94.5", "96", "94.5", "95.5"),
    ("95.5", "97", "95", "96.5"),
    ("96.5", "97.5", "96", "97"),
    ("97", "97.2", "93", "93.5"),
]


def _run(rows: list[tuple[str, str, str, str]]) -> list:  # type: ignore[type-arg]
    """Runs the plugin over rows on the 5m timeframe.

    Args:
        rows: OHLC rows.

    Returns:
        ``run_bars`` results.
    """
    return testing.run_bars(
        daily_bias.DailyBiasPlugin(), testing.candles_from_rows(rows)
    )


def test_initial_break_then_choch_then_bos() -> None:
    """Breaks are classified against the prevailing trend."""
    results = _run(STRUCTURE_ROWS)
    assert testing.event_names(results) == [
        (11, "INITIAL_BREAK_BULL"),
        (14, "CHOCH_BEAR"),
        (19, "BOS_BEAR"),
    ]
    final = results[-1][0].values
    assert final["trend"] == "BEARISH"
    assert final["last_event"] == "BOS"
    assert final["last_event_direction"] == "BEARISH"


def test_pivot_is_known_only_after_confirmation_delay() -> None:
    """The swing high appears at the confirming bar, not before."""
    results = _run(STRUCTURE_ROWS)
    assert results[5][0].values["last_pivot_high"] is None
    assert results[6][0].values["last_pivot_high"] == D(105)


def test_same_pivot_never_triggers_twice() -> None:
    """A second close above an already-broken pivot is silent."""
    results = _run(STRUCTURE_ROWS[:13])
    names = [name for _, name in testing.event_names(results)]
    assert names.count("INITIAL_BREAK_BULL") == 1
    assert len(names) == 1


def test_break_event_carries_level_and_timing() -> None:
    """Events reference the broken level and confirm at the bar close."""
    results = _run(STRUCTURE_ROWS)
    event = results[11][1].events[0]
    candles = testing.candles_from_rows(STRUCTURE_ROWS)
    assert event.price == D(105)
    assert event.bar_start_ms == candles[11].start_ms
    assert event.confirmed_at_ms == candles[11].end_ms
    assert event.meta["pivot_start_ms"] == candles[3].start_ms


def test_prior_day_high_low_published_on_day_change() -> None:
    """PDH/PDL equal the previous UTC day's extremes."""
    candles = testing.random_walk(30, seed=5, timeframe=H1)
    results = testing.run_bars(daily_bias.DailyBiasPlugin(), candles)
    day_one = candles[:24]
    assert results[23][0].values["pdh"] is None
    snapshot = results[24][0]
    assert snapshot.values["pdh"] == max(c.high for c in day_one)
    assert snapshot.values["pdl"] == min(c.low for c in day_one)
    kinds = {aoi.kind.value for aoi in snapshot.aois}
    assert kinds == {"PDH", "PDL"}


def test_missing_day_leaves_prior_day_levels_unknown() -> None:
    """A skipped calendar day yields no PDH/PDL instead of stale ones."""
    candles = testing.random_walk(24, seed=6, timeframe=H1)
    later = testing.random_walk(
        3,
        seed=7,
        timeframe=H1,
        start_ms=testing.DEFAULT_START_MS + 2 * 86_400_000,
    )
    results = testing.run_bars(daily_bias.DailyBiasPlugin(), candles + later)
    assert results[-1][0].values["pdh"] is None
    assert results[-1][0].values["pdl"] is None


def _utc_ms(*args: int) -> int:
    """Builds a UTC timestamp.

    Args:
        *args: ``datetime`` components.

    Returns:
        Epoch milliseconds.
    """
    return int(datetime.datetime(*args, tzinfo=datetime.UTC).timestamp() * 1000)


def test_sessions_follow_new_york_daylight_saving() -> None:
    """Session windows move with US DST (2026-03-08 transition)."""
    # 13:00 UTC is 08:00 EST (before NY open) but 09:00 EDT (in NY open).
    assert daily_bias.session_for(_utc_ms(2026, 3, 6, 13, 0)) == "NONE"
    assert daily_bias.session_for(_utc_ms(2026, 3, 9, 13, 0)) == "NEW_YORK"
    # 00:30 UTC is 19:30 EST (before Asia) but 20:30 EDT (Asia).
    assert daily_bias.session_for(_utc_ms(2026, 3, 6, 0, 30)) == "NONE"
    assert daily_bias.session_for(_utc_ms(2026, 3, 10, 0, 30)) == "ASIA"
    # London 02:00-05:00 NY time; 07:00 UTC in winter is 02:00 EST.
    assert daily_bias.session_for(_utc_ms(2026, 1, 12, 7, 0)) == "LONDON"
    assert daily_bias.session_for(_utc_ms(2026, 1, 12, 10, 0)) == "NONE"


def test_session_value_is_published_per_bar() -> None:
    """The snapshot exposes the session of the last bar."""
    start = _utc_ms(2026, 1, 12, 13, 30)  # 08:30 EST
    candles = testing.random_walk(
        2, seed=1, timeframe=timeframes.Timeframe.M30, start_ms=start
    )
    results = testing.run_bars(daily_bias.DailyBiasPlugin(), candles)
    assert results[0][0].values["session"] == "NEW_YORK"
    assert results[1][0].values["session"] == "NEW_YORK"
