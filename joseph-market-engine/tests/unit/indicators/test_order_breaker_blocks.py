"""Scenario tests for the order_breaker_blocks plugin."""

from __future__ import annotations

import decimal

from joseph.domain import structures
from joseph.indicators import testing
from joseph.indicators.plugins.order_breaker_blocks import plugin as blocks
from joseph.indicators.sdk import ta

D = decimal.Decimal
FLAT = ("100", "100.5", "99.5", "100")
# Pivot high 105 at bar 17 (confirmed 20); bearish bars 18-20; breakout 22.
SETUP = [FLAT] * 14 + [
    ("100", "101", "99.8", "100.8"),
    ("100.8", "102", "100.5", "101.5"),
    ("101.5", "103", "101", "102.5"),
    ("102.5", "105", "102", "104"),
    ("104", "104.5", "102.5", "103"),
    ("103", "103.5", "101.5", "102"),
    ("102", "102.5", "100.5", "101"),
    ("101", "104", "100.8", "103.8"),
]
BREAKOUT = ("103.8", "107", "103.6", "106.8")
WEAK_BREAK = ("104.9", "105.5", "104.8", "105.2")
STRONG_LATER = ("105.2", "108", "105", "107.8")
TOUCH = ("106.8", "107", "102.4", "103")
FAIL = ("103", "103.5", "100", "100.2")
BREAKER_FAIL = ("100.2", "103", "100", "102.8")


def _run(rows: list[tuple[str, str, str, str]]) -> list:  # type: ignore[type-arg]
    """Runs the plugin.

    Args:
        rows: OHLC rows.

    Returns:
        ``run_bars`` results.
    """
    return testing.run_bars(
        blocks.OrderBreakerBlocksPlugin(), testing.candles_from_rows(rows)
    )


def _live(results: list, kind: str) -> list[structures.Aoi]:  # type: ignore[type-arg]
    """Returns live blocks of one kind.

    Args:
        results: ``run_bars`` results.
        kind: AOI kind.

    Returns:
        Matching live AOIs.
    """
    return [
        a
        for a in results[-1][0].aois
        if a.kind.value == kind and a.state.is_live
    ]


def test_displacement_break_forms_order_block_from_last_opposing_candle() -> (
    None
):
    """The last bearish candle after the pivot becomes the bullish OB."""
    results = _run([*SETUP, BREAKOUT])
    assert testing.event_names(results) == [(22, "OB_FORMED")]
    (ob,) = _live(results, "OB")
    candles = testing.candles_from_rows([*SETUP, BREAKOUT])
    assert ob.side is structures.ZoneSide.BULLISH
    assert (ob.lower, ob.upper) == (D("100.5"), D("102.5"))
    assert ob.created_at_ms == candles[20].start_ms
    assert ob.confirmed_at_ms == candles[22].end_ms


def test_order_block_score_formula() -> None:
    """60 breakout body/ATR (cap 2) + 40 distance beyond pivot (cap 1)."""
    rows = [*SETUP, BREAKOUT]
    atr = ta.Atr(length=14)
    for candle in testing.candles_from_rows(rows):
        atr = atr.update(candle)
    assert atr.value is not None
    expected = ta.score(
        (D(60), ta.saturate(D(3) / atr.value, D(2))),
        (D(40), ta.saturate(D("1.8") / atr.value, D(1))),
    )
    assert _live(_run(rows), "OB")[0].quality_score == expected


def test_weak_break_consumes_pivot_so_one_block_per_pivot() -> None:
    """A small-body break forms nothing and later breaks reuse nothing."""
    results = _run([*SETUP, WEAK_BREAK, STRONG_LATER])
    assert testing.event_names(results) == []


def test_first_touch_decays_score() -> None:
    """The first touch multiplies the score by 0.70."""
    base = _live(_run([*SETUP, BREAKOUT]), "OB")[0].quality_score
    assert base is not None
    touched = _live(_run([*SETUP, BREAKOUT, TOUCH]), "OB")[0]
    assert touched.state is structures.AoiState.TESTED
    assert touched.quality_score == ta.decay(base, D("0.70"))


def test_close_through_far_edge_converts_to_breaker_then_removes() -> None:
    """OB failure creates an opposite breaker; reverse break removes it."""
    results = _run([*SETUP, BREAKOUT, TOUCH, FAIL])
    events = testing.event_names(results)
    assert (24, "AOI_INVALIDATED") in events
    assert (24, "BREAKER_FORMED") in events
    # The same bar also breaks the swing low at 100.5 with displacement,
    # legitimately forming a bearish OB from the last bullish candle.
    assert (24, "OB_FORMED") in events
    (breaker,) = _live(results, "BREAKER")
    assert breaker.side is structures.ZoneSide.BEARISH
    assert breaker.quality_score is not None
    removed = _run([*SETUP, BREAKOUT, TOUCH, FAIL, BREAKER_FAIL])
    assert (25, "AOI_INVALIDATED") in testing.event_names(removed)
    assert _live(removed, "BREAKER") == []


def test_no_opposing_candle_means_no_block() -> None:
    """Without a bearish candle after the pivot no OB forms."""
    rows = [FLAT] * 14 + [
        ("100", "101", "99.8", "100.8"),
        ("100.8", "102", "100.5", "101.5"),
        ("101.5", "103", "101", "102.5"),
        ("102.5", "105", "102", "104"),
        ("104", "104.5", "102.5", "104.2"),
        ("104.2", "104.4", "103", "104.3"),
        ("104.3", "104.6", "103.5", "104.5"),
        ("104.5", "107.5", "104.4", "107.4"),
    ]
    assert testing.event_names(_run(rows)) == []
