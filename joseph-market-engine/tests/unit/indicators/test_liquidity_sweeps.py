"""Scenario tests for the liquidity_sweeps plugin."""

from __future__ import annotations

import decimal

from joseph.domain import structures
from joseph.indicators import testing
from joseph.indicators.plugins.liquidity_sweeps import plugin as liquidity

D = decimal.Decimal
FLAT = ("100", "100.5", "99.5", "100")
# 14 flat bars seed ATR; swing high 110 at bar 17 confirmed at bar 20.
SWING_110 = [FLAT] * 14 + [
    ("100", "101", "99.8", "100.8"),
    ("100.8", "102", "100.5", "101.5"),
    ("101.5", "103", "101", "102.5"),
    ("102.5", "110", "102", "103"),
    ("103", "104", "101", "101.5"),
    ("101.5", "103", "100.5", "101"),
    ("101", "102", "100", "100.5"),
]
SWEEP = ("100.5", "110.5", "100.3", "109")
BREAK = ("100.5", "110.5", "100.3", "110.4")
TINY = ("100.5", "110.05", "100.3", "109")


def _mirror(
    rows: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, str, str]]:
    """Reflects rows around 200 (highs become lows).

    Args:
        rows: OHLC rows.

    Returns:
        Mirrored rows.
    """
    return [
        (str(200 - D(o)), str(200 - D(lo)), str(200 - D(h)), str(200 - D(c)))
        for o, h, lo, c in rows
    ]


def _run(rows: list[tuple[str, str, str, str]]) -> list:  # type: ignore[type-arg]
    """Runs the plugin.

    Args:
        rows: OHLC rows.

    Returns:
        ``run_bars`` results.
    """
    return testing.run_bars(
        liquidity.LiquiditySweepsPlugin(), testing.candles_from_rows(rows)
    )


def _live(results: list) -> list[structures.Aoi]:  # type: ignore[type-arg]
    """Returns live pools after the last bar.

    Args:
        results: ``run_bars`` results.

    Returns:
        Live AOIs.
    """
    return [a for a in results[-1][0].aois if a.state.is_live]


def test_pool_appears_only_after_swing_confirmation() -> None:
    """BSL exists from the confirming bar onward (no early pools)."""
    results = _run(SWING_110)
    assert results[19][0].aois == ()
    (pool,) = _live(results)
    candles = testing.candles_from_rows(SWING_110)
    assert pool.kind is structures.AoiKind.BSL
    assert pool.lower == pool.upper == D(110)
    assert pool.created_at_ms == candles[17].start_ms
    assert pool.confirmed_at_ms == candles[20].end_ms


def test_wick_beyond_with_close_back_inside_is_a_sweep() -> None:
    """Penetration plus reclaim emits BSL_SWEEP with a 0-100 score."""
    results = _run([*SWING_110, SWEEP])
    assert testing.event_names(results) == [(21, "BSL_SWEEP")]
    event = results[-1][1].events[0]
    assert event.price == D(110)
    assert event.score is not None and 0 < event.score <= 100
    assert event.meta["pool_kind"] == "BSL"
    assert _live(results) == []
    swept = results[-1][0].aois[0]
    assert swept.state is structures.AoiState.SWEPT


def test_close_beyond_level_is_a_break_not_a_sweep() -> None:
    """Closing outside the pool is a BREAK."""
    results = _run([*SWING_110, BREAK])
    assert testing.event_names(results) == [(21, "BSL_BREAK")]


def test_tiny_penetration_keeps_the_pool() -> None:
    """Penetration below max(tick, tolerance) is ignored."""
    results = _run([*SWING_110, TINY])
    assert testing.event_names(results) == []
    assert len(_live(results)) == 1


def test_one_bar_can_sweep_several_pools() -> None:
    """Each pool swept by a single wick gets its own event."""
    rows = [
        *SWING_110,
        ("100.5", "105", "100.2", "104.5"),
        ("104.5", "106", "104", "105.5"),
        ("105.5", "108", "105", "106"),
        ("106", "107", "104", "104.5"),
        ("104.5", "106", "103", "103.5"),
        ("103.5", "105", "102", "102.5"),
        ("102.5", "111", "102", "107"),
    ]
    results = _run(rows)
    sweeps = [e for _, e in testing.event_names(results)]
    assert sweeps == ["BSL_SWEEP", "BSL_SWEEP"]
    levels = sorted(e.price for e in results[-1][1].events)
    assert levels == [D(108), D(110)]


def test_equal_highs_merge_into_eqh() -> None:
    """A second swing within tolerance merges into one EQH pool."""
    rows = [
        *SWING_110,
        ("100.5", "105", "100.2", "104.5"),
        ("104.5", "106", "104", "105.5"),
        ("105.5", "110.05", "105", "106"),
        ("106", "107", "104", "104.5"),
        ("104.5", "106", "103", "103.5"),
        ("103.5", "105", "102", "102.5"),
    ]
    results = _run(rows)
    assert testing.event_names(results) == []
    (pool,) = [a for a in _live(results) if a.lower > D(105)]
    assert pool.kind is structures.AoiKind.EQH
    assert pool.lower == D("110.05")
    assert pool.meta["pivot_count"] == 2


def test_sell_side_sweep_is_the_mirror() -> None:
    """A wick below a swing low that closes back above is an SSL sweep."""
    results = _run(_mirror([*SWING_110, SWEEP]))
    assert testing.event_names(results) == [(21, "SSL_SWEEP")]
    assert results[20][0].aois[0].kind is structures.AoiKind.SSL


def test_pivot_bar_cannot_sweep_its_own_pool() -> None:
    """No sweep or break is emitted up to the pool's confirmation."""
    results = _run(SWING_110)
    assert testing.event_names(results) == []
