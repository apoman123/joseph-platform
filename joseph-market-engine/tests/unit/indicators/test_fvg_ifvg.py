"""Scenario tests for the fvg_ifvg plugin."""

from __future__ import annotations

import decimal

from joseph.domain import structures
from joseph.indicators import testing
from joseph.indicators.plugins.fvg_ifvg import plugin as fvg
from joseph.indicators.sdk import ta

D = decimal.Decimal
FLAT = ("100", "100.5", "99.5", "100")
# 14 flat bars seed ATR = 1; bar 14 is c2, 15 the displacement, 16 confirms
# a bullish gap [100.5, 101].
BULL = [FLAT] * 15 + [
    ("100", "102", "99.9", "101.9"),
    ("102", "103", "101", "102.5"),
]
MID_RETEST = ("102.5", "102.6", "100.7", "101")
WICK_THROUGH = ("101", "101.2", "100.2", "100.8")
CLOSE_THROUGH = ("100.8", "100.9", "99.8", "100")
IFVG_FAIL = ("100", "101.5", "99.9", "101.2")


def _mirror(
    rows: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, str, str]]:
    """Reflects rows around 200 to build the bearish twin of a scenario.

    Args:
        rows: OHLC rows.

    Returns:
        Mirrored rows (high and low swap).
    """
    return [
        (str(200 - D(o)), str(200 - D(lo)), str(200 - D(h)), str(200 - D(c)))
        for o, h, lo, c in rows
    ]


def _run(
    rows: list[tuple[str, str, str, str]],
    settings: dict[str, object] | None = None,
) -> list:  # type: ignore[type-arg]
    """Runs the plugin.

    Args:
        rows: OHLC rows.
        settings: Overrides.

    Returns:
        ``run_bars`` results.
    """
    return testing.run_bars(
        fvg.FvgIfvgPlugin(), testing.candles_from_rows(rows), settings
    )


def _zones(results: list, kind: str) -> list[structures.Aoi]:  # type: ignore[type-arg]
    """Returns live AOIs of one kind after the last bar.

    Args:
        results: ``run_bars`` results.
        kind: AOI kind.

    Returns:
        Matching AOIs.
    """
    return [
        aoi
        for aoi in results[-1][0].aois
        if aoi.kind.value == kind and aoi.state.is_live
    ]


def test_bullish_gap_confirms_on_third_bar() -> None:
    """low[0] > high[2] forms an FVG only at the third bar's close."""
    results = _run(BULL)
    assert testing.event_names(results) == [(16, "FVG_FORMED")]
    candles = testing.candles_from_rows(BULL)
    (zone,) = _zones(results, "FVG")
    assert (zone.lower, zone.upper) == (D("100.5"), D(101))
    assert zone.side is structures.ZoneSide.BULLISH
    assert zone.created_at_ms == candles[14].start_ms
    assert zone.confirmed_at_ms == candles[16].end_ms
    assert zone.meta["mid"] == D("100.75")
    assert zone.quality_score is not None
    assert 0 <= zone.quality_score <= 100


def test_formation_score_follows_weighted_formula() -> None:
    """55 gap/ATR + 30 body/ATR + 15 close location (saturated)."""
    plugin = fvg.FvgIfvgPlugin()
    results = _run(BULL)
    atr = ta.Atr(length=14)
    for candle in testing.candles_from_rows(BULL):
        atr = atr.update(candle)
    assert atr.value is not None
    gap = ta.saturate(D("0.5") / atr.value, D(1))
    body = ta.saturate(D("1.9") / atr.value, D("1.5"))
    location = (D("101.9") - D("99.9")) / D("2.1")
    expected = ta.score((D(55), gap), (D(30), body), (D(15), location))
    (zone,) = _zones(results, "FVG")
    assert zone.quality_score == expected
    del plugin


def test_equal_wick_is_not_a_gap() -> None:
    """low[0] == high[2] does not form an FVG (strict inequality)."""
    rows = [*BULL[:-1], ("102", "103", "100.5", "102.5")]
    assert testing.event_names(_run(rows)) == []


def test_minimum_gap_gates_apply() -> None:
    """Gaps below the ATR multiple or one tick are ignored."""
    assert testing.event_names(_run(BULL, {"min_gap_atr": "5"})) == []
    assert testing.event_names(_run(BULL, {"tick_size": "1"})) == []
    assert testing.event_names(_run(BULL, {"tick_size": "0.5"})) == [
        (16, "FVG_FORMED")
    ]


def test_no_gap_before_atr_is_seeded() -> None:
    """With ATR unavailable (warmup) no gap can qualify."""
    rows = [
        FLAT,
        ("100", "102", "99.9", "101.9"),
        ("102", "103", "101", "102.5"),
    ]
    assert testing.event_names(_run(rows)) == []


def test_first_midline_retest_decays_score_once() -> None:
    """The first wick into the midline multiplies the score by 0.70."""
    base = _zones(_run(BULL), "FVG")[0].quality_score
    assert base is not None
    once = _zones(_run([*BULL, MID_RETEST]), "FVG")[0]
    assert once.state is structures.AoiState.TESTED
    assert once.quality_score == ta.decay(base, D("0.70"))
    twice = _zones(_run([*BULL, MID_RETEST, MID_RETEST]), "FVG")[0]
    assert twice.quality_score == once.quality_score


def test_intrabar_wick_through_far_edge_is_not_an_inversion() -> None:
    """A wick below the far edge with a close inside keeps the FVG."""
    results = _run([*BULL, MID_RETEST, WICK_THROUGH])
    assert [n for _, n in testing.event_names(results)] == ["FVG_FORMED"]
    assert len(_zones(results, "FVG")) == 1


def test_close_beyond_far_edge_inverts_into_bearish_ifvg() -> None:
    """A close below the bullish FVG's lower edge creates a bearish IFVG."""
    rows = [*BULL, MID_RETEST, WICK_THROUGH, CLOSE_THROUGH]
    results = _run(rows)
    assert testing.event_names(results)[-2:] == [
        (19, "AOI_INVALIDATED"),
        (19, "IFVG_FORMED"),
    ]
    assert _zones(results, "FVG") == []
    (ifvg,) = _zones(results, "IFVG")
    assert ifvg.side is structures.ZoneSide.BEARISH
    ended = [a for a in results[-1][0].aois if a.kind.value == "FVG"]
    assert ended[0].state is structures.AoiState.INVERTED
    assert ended[0].invalidated_at_ms == ifvg.confirmed_at_ms


def test_ifvg_invalidates_on_reverse_close_through() -> None:
    """An IFVG is removed when price closes back through it."""
    rows = [*BULL, MID_RETEST, WICK_THROUGH, CLOSE_THROUGH, IFVG_FAIL]
    results = _run(rows)
    assert testing.event_names(results)[-1] == (20, "AOI_INVALIDATED")
    assert _zones(results, "IFVG") == []
    assert results[-1][0].values["active_count"] == 0


def test_bearish_gap_is_the_mirror_image() -> None:
    """high[0] < low[2] forms a bearish FVG."""
    results = _run(_mirror(BULL))
    assert testing.event_names(results) == [(16, "FVG_FORMED")]
    (zone,) = _zones(results, "FVG")
    assert zone.side is structures.ZoneSide.BEARISH
    assert (zone.lower, zone.upper) == (D(99), D("99.5"))


def test_retention_cap_expires_oldest_zone() -> None:
    """Beyond max_zones the oldest zone expires with an event."""
    second_gap = [
        ("102.5", "103", "102", "102.5"),
        ("102.5", "105", "102.4", "104.9"),
        ("105", "106", "104", "105.5"),
    ]
    results = _run(BULL + second_gap, {"max_zones": 1})
    assert testing.event_names(results) == [
        (16, "FVG_FORMED"),
        (19, "FVG_FORMED"),
        (19, "AOI_EXPIRED"),
    ]
    assert results[-1][0].values["active_count"] == 1


def test_age_expiry() -> None:
    """expire_bars ends a zone after the configured age."""
    calm = [("102.5", "102.8", "102.2", "102.5")] * 3
    results = _run(BULL + calm, {"expire_bars": 2})
    assert (18, "AOI_EXPIRED") in testing.event_names(results)
