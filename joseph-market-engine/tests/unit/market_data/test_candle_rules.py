"""Unit tests for candle merge semantics and gap detection."""

from __future__ import annotations

import dataclasses
import decimal
import itertools

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.domain import candles

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_TF = timeframes.Timeframe.M5
_T0 = 1_795_252_800_000
_D = decimal.Decimal


def bar(
    start: int = _T0,
    *,
    close: str = "100",
    volume: str = "10",
    final: bool = False,
    received: int = 0,
    revision: int = 0,
) -> market.Candle:
    """Builds a candle for tests.

    Args:
        start: Bar start.
        close: Close price.
        volume: Volume.
        final: Finality.
        received: Receipt time.
        revision: Revision.

    Returns:
        Candle.
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
    result = candles.merge_candle(None, bar())
    assert result.action is candles.MergeAction.INSERTED
    assert result.changed


def test_live_update_replaces_live_bar() -> None:
    """A newer live update with more volume replaces the stored bar."""
    result = candles.merge_candle(
        bar(volume="10", received=1),
        bar(close="100.5", volume="12", received=2),
    )
    assert result.action is candles.MergeAction.UPDATED
    assert result.candle.close == _D("100.5")


def test_out_of_order_live_update_is_ignored() -> None:
    """Older receipt time or shrinking volume is treated as stale."""
    stored = bar(volume="12", received=5)
    assert (
        candles.merge_candle(stored, bar(volume="13", received=4)).action
        is candles.MergeAction.IGNORED_STALE
    )
    assert (
        candles.merge_candle(stored, bar(volume="11", received=6)).action
        is candles.MergeAction.IGNORED_STALE
    )


def test_final_replaces_live() -> None:
    """A final observation finalizes a live bar."""
    result = candles.merge_candle(bar(), bar(final=True, volume="15"))
    assert result.action is candles.MergeAction.FINALIZED
    assert result.candle.is_final


def test_live_never_overwrites_final() -> None:
    """A late live update cannot reopen a closed bar."""
    stored = bar(final=True)
    result = candles.merge_candle(stored, bar(close="105", received=99))
    assert result.action is candles.MergeAction.IGNORED_STALE
    assert result.candle is stored


def test_identical_final_is_duplicate() -> None:
    """A repeated final observation is a duplicate."""
    assert (
        candles.merge_candle(
            bar(final=True), bar(final=True, received=7)
        ).action
        is candles.MergeAction.DUPLICATE
    )


def test_changed_final_is_revised_with_incremented_revision() -> None:
    """Final bars are never mutated silently."""
    result = candles.merge_candle(
        bar(final=True, revision=0), bar(final=True, close="100.1")
    )
    assert result.action is candles.MergeAction.REVISED
    assert result.candle.revision == 1


def test_merge_rejects_different_bars() -> None:
    """Merging two different bars is a programming error."""
    with pytest.raises(ValueError):
        candles.merge_candle(bar(), bar(start=_T0 + _TF.duration_ms))


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
    rows = [bar(final=True), bar(received=3), bar(start=_T0 - 300_000)]
    result = candles.sort_unique(rows)
    assert [c.start_ms for c in result] == [_T0 - 300_000, _T0]
    assert result[1].is_final


def test_finals_only_filters_live_bars() -> None:
    """Only closed bars survive."""
    assert candles.finals_only([bar(), bar(start=_T0 + 300_000, final=True)])[
        0
    ].is_final


@hypothesis.given(strategies.permutations(list(range(6))))
def test_final_wins_regardless_of_arrival_order(order: list[int]) -> None:
    """Any arrival order of live updates plus the final ends final."""
    updates = [bar(volume=str(10 + i), received=i) for i in range(5)]
    updates.append(bar(final=True, volume="20", received=10))
    stored = None
    for index in order:
        stored = candles.merge_candle(stored, updates[index]).candle
    assert stored is not None
    assert stored.is_final
    assert stored.base_volume == _D("20")


def test_merge_is_idempotent_for_replays() -> None:
    """Re-applying the same sequence produces no further changes."""
    sequence = [
        bar(volume="10", received=1),
        bar(volume="11", received=2),
        bar(final=True, volume="12", received=3),
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
        missing = {s for a, b in gaps for s in range(a, b, step)}
        expected = {_T0 + i * step for i, p in enumerate(mask) if not p}
        assert missing == expected


def test_revision_of_replaced_dataclass_keeps_identity() -> None:
    """Revised candles keep instrument, timeframe and start."""
    revised = candles.merge_candle(
        bar(final=True), dataclasses.replace(bar(final=True), close=_D("100.2"))
    ).candle
    assert (revised.instrument_id, revised.timeframe, revised.start_ms) == (
        _ID,
        _TF,
        _T0,
    )
