"""Scenario tests for the premium_discount_ote plugin."""

from __future__ import annotations

import decimal

from joseph.domain import structures
from joseph.indicators import testing
from joseph.indicators.plugins.premium_discount_ote import plugin as pdo

D = decimal.Decimal
SETTINGS: dict[str, object] = {"swing_length": 2}
# Swing low 95 (bar 2, confirmed 4); swing high 105 (bar 6, confirmed 8).
# Bar 8 keeps its low high enough that no minor swing low forms before the
# retracement (a newer swing low would itself reset the range when broken).
BASE = [
    ("100", "101", "99.5", "100.5"),
    ("100.5", "101.5", "100", "101"),
    ("101", "101.2", "95", "95.5"),
    ("95.5", "98", "95.5", "97.5"),
    ("97.5", "100", "97", "99.5"),
    ("99.5", "104", "99", "103.5"),
    ("103.5", "105", "103", "104"),
    ("104", "104.5", "102", "102.5"),
    ("102.7", "103", "102.6", "102.8"),
]
BULL_BREAK = ("102", "106", "101.8", "105.8")
EXTEND = ("105.8", "108", "105", "107")
RETRACE = ("107", "107.5", "99", "99.2")
CONFIRM_108 = ("99.2", "101", "98.5", "100")
BEAR_BREAK = ("100", "100.5", "94", "94.5")


def _run(
    rows: list[tuple[str, str, str, str]],
    settings: dict[str, object] | None = None,
) -> list:  # type: ignore[type-arg]
    """Runs the plugin.

    Args:
        rows: OHLC rows.
        settings: Overrides (defaults to swing length 2).

    Returns:
        ``run_bars`` results.
    """
    return testing.run_bars(
        pdo.PremiumDiscountOtePlugin(),
        testing.candles_from_rows(rows),
        settings or SETTINGS,
    )


def test_bullish_break_resets_range_with_oriented_ote() -> None:
    """A close above the swing high anchors a bullish dealing range."""
    results = _run([*BASE, BULL_BREAK])
    assert testing.event_names(results) == [(9, "RANGE_RESET")]
    values = results[-1][0].values
    assert values["direction"] == "BULLISH"
    assert (values["range_low"], values["range_high"]) == (D(95), D(106))
    assert values["eq"] == D("100.5")
    assert values["ote_lower"] == D("97.31")
    assert values["ote_upper"] == D("99.18")
    assert values["in_premium"] is True
    assert values["in_discount"] is False
    assert values["in_ote"] is False
    kinds = {a.kind.value for a in results[-1][0].aois}
    assert kinds == {"PREMIUM", "DISCOUNT", "EQUILIBRIUM", "OTE"}
    ote = next(a for a in results[-1][0].aois if a.kind.value == "OTE")
    assert ote.side is structures.ZoneSide.BULLISH
    assert ote.range_id == values["range_id"]


def test_leg_extension_keeps_the_same_range_id() -> None:
    """New highs extend the range without changing its id."""
    first = _run([*BASE, BULL_BREAK])[-1][0].values["range_id"]
    results = _run([*BASE, BULL_BREAK, EXTEND, RETRACE])
    values = results[-1][0].values
    assert values["range_id"] == first
    assert values["range_high"] == D(108)
    assert values["in_discount"] is True
    assert values["in_ote"] is True


def test_bearish_break_flips_range_and_invalidates_old_zones() -> None:
    """Breaking the swing low resets to a bearish range."""
    rows = [*BASE, BULL_BREAK, EXTEND, RETRACE, CONFIRM_108, BEAR_BREAK]
    results = _run(rows)
    resets = [i for i, n in testing.event_names(results) if n == "RANGE_RESET"]
    assert resets == [9, 13]
    snapshot = results[-1][0]
    values = snapshot.values
    assert values["direction"] == "BEARISH"
    assert (values["range_low"], values["range_high"]) == (D(94), D(108))
    assert values["ote_lower"] == D("102.68")
    assert values["ote_upper"] == D("105.06")
    invalidated = [
        a for a in snapshot.aois if a.state is structures.AoiState.INVALIDATED
    ]
    assert len(invalidated) == 4
    event = results[-1][1].events[0]
    assert event.meta["previous_range_id"] != event.meta["range_id"]


def test_wick_mode_breaks_on_wicks_close_mode_does_not() -> None:
    """break_mode selects close or wick confirmation."""
    wick_only = ("102", "105.5", "101.8", "104.8")
    close_mode = _run([*BASE, wick_only])
    assert testing.event_names(close_mode) == []
    wick_mode = _run(
        [*BASE, wick_only], {"swing_length": 2, "break_mode": "WICK"}
    )
    assert testing.event_names(wick_mode) == [(9, "RANGE_RESET")]


def test_values_are_none_before_any_range() -> None:
    """No dealing range yet means unknown zone membership."""
    values = _run(BASE)[-1][0].values
    assert values["range_id"] is None
    assert values["in_ote"] is None
