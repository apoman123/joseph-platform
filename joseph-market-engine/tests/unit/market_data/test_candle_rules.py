# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for candle merge semantics and gap detection."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import itertools

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_TF = timeframes.Timeframe.M5
_T0 = 1_795_252_800_000
_D = decimal.Decimal


def _bar(
    start: int = _T0,
    *,
    close: str = "100",
    volume: str = "10",
    final: bool = False,
    received: int = 0,
    revision: int = 0,
) -> market.Candle:
    """Builds a 5m BTCUSDT candle for tests.

    Args:
        start: Bar start, epoch milliseconds.
        close: Close price; the bar opens at 100 and spans at least 99-101.
        volume: Base volume.
        final: Whether the bar is final.
        received: Receipt time, epoch milliseconds.
        revision: Revision number of a final bar.

    Returns:
        The candle.
    """
    price = _D(close)
    return market.Candle(
        instrument_id=_ID,
        timeframe=_TF,
        start_ms=start,
        end_ms=_TF.end_of(start),
        open=_D("100"),
        high=max(price, _D("101")),
        low=min(price, _D("99")),
        close=price,
        base_volume=_D(volume),
        is_final=final,
        source="test",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=received,
        revision=revision,
    )


def test_first_observation_is_inserted() -> None:
    """A bar with no prior observation is inserted."""
    result = candles.merge_candle(None, _bar())
    assert result.action is candles.MergeAction.INSERTED
    assert result.changed


def test_live_update_replaces_live_bar() -> None:
    """A newer live update with more volume replaces the stored bar."""
    result = candles.merge_candle(
        _bar(volume="10", received=1),
        _bar(close="100.5", volume="12", received=2),
    )
    assert result.action is candles.MergeAction.UPDATED
    assert result.candle.close == _D("100.5")


def test_out_of_order_live_update_is_ignored() -> None:
    """Older receipt time or shrinking volume is treated as stale."""
    stored = _bar(volume="12", received=5)
    assert (
        candles.merge_candle(stored, _bar(volume="13", received=4)).action
        is candles.MergeAction.IGNORED_STALE
    )
    assert (
        candles.merge_candle(stored, _bar(volume="11", received=6)).action
        is candles.MergeAction.IGNORED_STALE
    )


def test_final_replaces_live() -> None:
    """A final observation finalizes a live bar."""
    result = candles.merge_candle(_bar(), _bar(final=True, volume="15"))
    assert result.action is candles.MergeAction.FINALIZED
    assert result.candle.is_final


def test_live_never_overwrites_final() -> None:
    """A late live update cannot reopen a closed bar."""
    stored = _bar(final=True)
    result = candles.merge_candle(stored, _bar(close="105", received=99))
    assert result.action is candles.MergeAction.IGNORED_STALE
    assert result.candle is stored


def test_identical_final_is_duplicate() -> None:
    """A repeated final observation is a duplicate."""
    assert (
        candles.merge_candle(
            _bar(final=True), _bar(final=True, received=7)
        ).action
        is candles.MergeAction.DUPLICATE
    )


def test_changed_final_is_revised_with_incremented_revision() -> None:
    """Final bars are never mutated silently."""
    result = candles.merge_candle(
        _bar(final=True, revision=0), _bar(final=True, close="100.1")
    )
    assert result.action is candles.MergeAction.REVISED
    assert result.candle.revision == 1


def test_merge_rejects_different_bars() -> None:
    """Merging two different bars is a programming error."""
    with pytest.raises(ValueError):
        candles.merge_candle(_bar(), _bar(start=_T0 + _TF.duration_ms))


def test_find_gaps_reports_missing_runs() -> None:
    """Gaps are reported as half-open ranges."""
    step = _TF.duration_ms
    starts = [_T0, _T0 + step, _T0 + 4 * step]
    gaps = candles.find_gaps(starts, _TF, _T0, _T0 + 6 * step)
    assert gaps == [
        (_T0 + 2 * step, _T0 + 4 * step),
        (_T0 + 5 * step, _T0 + 6 * step),
    ]


def test_find_gaps_rounds_start_up_to_boundary() -> None:
    """A misaligned start does not invent a gap before the first bar."""
    step = _TF.duration_ms
    assert candles.find_gaps([_T0 + step], _TF, _T0 + 1, _T0 + 2 * step) == []


def test_sort_unique_merges_duplicates() -> None:
    """Duplicate observations collapse to the best one per bar."""
    rows = [_bar(final=True), _bar(received=3), _bar(start=_T0 - 300_000)]
    result = candles.sort_unique(rows)
    assert [c.start_ms for c in result] == [_T0 - 300_000, _T0]
    assert result[1].is_final


def test_finals_only_filters_live_bars() -> None:
    """Only closed bars survive."""
    assert candles.finals_only([_bar(), _bar(start=_T0 + 300_000, final=True)])[
        0
    ].is_final


@hypothesis.given(strategies.permutations(list(range(6))))
def test_final_wins_regardless_of_arrival_order(order: list[int]) -> None:
    """Any arrival order of live updates plus the final ends final."""
    updates = [_bar(volume=str(10 + i), received=i) for i in range(5)]
    updates.append(_bar(final=True, volume="20", received=10))
    stored = None
    for index in order:
        stored = candles.merge_candle(stored, updates[index]).candle
    assert stored is not None
    assert stored.is_final
    assert stored.base_volume == _D("20")


def test_merge_is_idempotent_for_replays() -> None:
    """Re-applying the same sequence produces no further changes."""
    sequence = [
        _bar(volume="10", received=1),
        _bar(volume="11", received=2),
        _bar(final=True, volume="12", received=3),
    ]
    stored = None
    for update in sequence:
        stored = candles.merge_candle(stored, update).candle
    for update in sequence:
        assert not candles.merge_candle(stored, update).changed


def test_gap_detection_matches_brute_force() -> None:
    """Gap ranges cover exactly the missing slots."""
    step = _TF.duration_ms
    for mask in itertools.product([0, 1], repeat=7):
        starts = [_T0 + i * step for i, present in enumerate(mask) if present]
        gaps = candles.find_gaps(starts, _TF, _T0, _T0 + 7 * step)
        missing = set(
            itertools.chain.from_iterable(range(a, b, step) for a, b in gaps)
        )
        expected = {_T0 + i * step for i, p in enumerate(mask) if not p}
        assert missing == expected


def test_revision_of_replaced_dataclass_keeps_identity() -> None:
    """Revised candles keep instrument, timeframe and start."""
    revised = candles.merge_candle(
        _bar(final=True),
        dataclasses.replace(_bar(final=True), close=_D("100.2")),
    ).candle
    assert (revised.instrument_id, revised.timeframe, revised.start_ms) == (
        _ID,
        _TF,
        _T0,
    )


# Coverage (sparse-bar venues) -------------------------------------------

_XNYS = sessions.XNYS
_M30 = timeframes.Timeframe.M30
_FRIDAY = _XNYS.session(datetime.date(2026, 11, 20))
_MONDAY = _XNYS.session(datetime.date(2026, 11, 23))
assert _FRIDAY is not None and _MONDAY is not None


def _xnys_starts(first: int, end: int) -> list[int]:
    """Lists XNYS 30m bar starts in ``[first, end)``.

    Args:
        first: Inclusive start.
        end: Exclusive end.

    Returns:
        Ascending bar starts.
    """
    out = []
    cursor = _XNYS.ceil(_M30, first)
    while cursor < end:
        out.append(cursor)
        cursor = _XNYS.shift(_M30, cursor, 1)
    return out


def test_span_helpers_merge_clip_and_subtract() -> None:
    """Spans unite when touching, clip to a window and lose holes."""
    assert candles.merge_spans([(5, 9), (0, 3), (3, 4), (7, 12), (2, 2)]) == [
        (0, 4),
        (5, 12),
    ]
    assert candles.clip_spans([(0, 4), (5, 12)], 2, 6) == [(2, 4), (5, 6)]
    assert candles.clip_spans([(0, 4)], 4, 9) == []
    assert candles.subtract_spans([(0, 10), (20, 30)], [(2, 4), (8, 22)]) == [
        (0, 2),
        (4, 8),
        (22, 30),
    ]
    assert candles.subtract_spans([(0, 10)], []) == [(0, 10)]


def test_find_gaps_skips_quiet_bars_inside_coverage() -> None:
    """A missing bar wholly inside coverage is quiet; outside it is a gap."""
    step = _TF.duration_ms
    starts = [_T0, _T0 + 4 * step]
    end = _T0 + 8 * step
    assert candles.find_gaps(starts, _TF, _T0, end) == [
        (_T0 + step, _T0 + 4 * step),
        (_T0 + 5 * step, end),
    ]
    covered = [(_T0, _T0 + 6 * step + 1)]
    assert candles.find_gaps(starts, _TF, _T0, end, covered=covered) == [
        (_T0 + 6 * step, end)
    ]
    # A slot only partly covered (from mid-bar) is still missing.
    partly = [(_T0 + step + 1, _T0 + 3 * step)]
    assert candles.find_gaps(starts, _TF, _T0, end, covered=partly) == [
        (_T0 + step, _T0 + 2 * step),
        (_T0 + 3 * step, _T0 + 4 * step),
        (_T0 + 5 * step, end),
    ]


def test_find_gaps_coverage_across_sessions() -> None:
    """Coverage of two sessions leaves no gap; the night belongs to none."""
    starts = [_FRIDAY.open_ms, _MONDAY.close_ms - 1_800_000]
    covered = [
        (_FRIDAY.open_ms, _FRIDAY.close_ms),
        (_MONDAY.open_ms, _MONDAY.close_ms),
    ]
    window = (_FRIDAY.open_ms, _MONDAY.close_ms)
    assert (
        candles.find_gaps(
            starts, _M30, *window, calendar=_XNYS, covered=covered
        )
        == []
    )
    # Monday morning was not covered: its bars are the only gap, and it
    # starts at Monday's open, not in the weekend.
    late = [covered[0], (_MONDAY.open_ms + 3_600_000, _MONDAY.close_ms)]
    assert candles.find_gaps(
        starts, _M30, *window, calendar=_XNYS, covered=late
    ) == [(_MONDAY.open_ms, _MONDAY.open_ms + 3_600_000)]


@hypothesis.settings(max_examples=200, deadline=None)
@hypothesis.given(
    strategies.lists(strategies.booleans(), min_size=26, max_size=26),
    strategies.lists(
        strategies.tuples(
            strategies.integers(0, 26 * 1_800_000),
            strategies.integers(0, 6 * 1_800_000),
        ),
        max_size=4,
    ),
)
def test_coverage_gaps_match_brute_force(
    mask: list[bool], raw_spans: list[tuple[int, int]]
) -> None:
    """Reported gaps hold exactly the missing, not wholly covered bars."""
    window = (_FRIDAY.open_ms, _MONDAY.close_ms)
    slots = _xnys_starts(*window)
    assert len(slots) == 26
    present = [s for s, keep in zip(slots, mask, strict=True) if keep]
    base = _FRIDAY.open_ms + 3 * 3_600_000
    covered = [(base + a, base + a + b) for a, b in raw_spans]
    merged = candles.merge_spans(covered)
    gaps = candles.find_gaps(
        present, _M30, *window, calendar=_XNYS, covered=covered
    )
    reported = set(
        itertools.chain.from_iterable(
            _xnys_starts(low, high) for low, high in gaps
        )
    )
    expected = {
        s
        for s in slots
        if s not in present
        and not any(
            low <= s and _XNYS.end_of(_M30, s) <= high for low, high in merged
        )
    }
    assert reported == expected
    for low, high in gaps:
        assert _XNYS.is_aligned(_M30, low) and low < high


def test_stream_coverage_chains_consecutive_bars() -> None:
    """Bars of one connection prove the slots between them; restart forgets."""
    step = _TF.duration_ms
    chain = candles.StreamCoverage()
    assert chain.observe(_T0, _T0 + step) is None
    assert chain.observe(_T0 + 3 * step, _T0 + 4 * step) == (
        _T0 + step,
        _T0 + 4 * step,
    )
    assert chain.observe(_T0 + step, _T0 + 2 * step) is None  # A revision.
    chain.restart()
    assert chain.observe(_T0 + 9 * step, _T0 + 10 * step) is None
    assert chain.observe(_T0 + 10 * step, _T0 + 11 * step) == (
        _T0 + 10 * step,
        _T0 + 11 * step,
    )
