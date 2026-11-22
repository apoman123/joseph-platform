"""Unit tests for order-book reconciliation, slippage and feed health."""

from __future__ import annotations

import decimal

import hypothesis
from hypothesis import strategies

from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.market_data.domain import books
from joseph.market_data.domain import health

_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_D = decimal.Decimal


def _levels(
    *pairs: tuple[str, str],
) -> list[tuple[decimal.Decimal, decimal.Decimal]]:
    """Builds decimal levels.

    Args:
        *pairs: ``(price, qty)`` strings.

    Returns:
        Levels.
    """
    return [(_D(p), _D(q)) for p, q in pairs]


def _builder() -> books.OrderBookBuilder:
    """Returns a builder with a snapshot at update id 100.

    Returns:
        Builder.
    """
    builder = books.OrderBookBuilder(_ID, "test", max_age_ms=1_000)
    builder.apply_snapshot(
        _levels(("99", "1"), ("98", "2")),
        _levels(("101", "1"), ("102", "2")),
        100,
        0,
        0,
    )
    return builder


def _diff(
    builder: books.OrderBookBuilder, first: int, final: int, prev: int
) -> books.ApplyResult:
    """Applies a Binance diff with fixed levels.

    Args:
        builder: Builder.
        first: ``U``.
        final: ``u``.
        prev: ``pu``.

    Returns:
        Apply result.
    """
    return builder.apply_binance_diff(
        first_update_id=first,
        final_update_id=final,
        previous_final_update_id=prev,
        bids=_levels(("99", "3")),
        asks=_levels(("101", "0")),
        ts_ms=1,
        received_ms=1,
    )


def test_new_builder_requires_snapshot() -> None:
    """Deltas before a snapshot demand a resync and the book is stale."""
    builder = books.OrderBookBuilder(_ID, "test")
    assert builder.apply_delta(1, [], [], 0, 0) is (
        books.ApplyResult.RESYNC_REQUIRED
    )
    assert builder.book(0).stale


def test_binance_diff_older_than_snapshot_is_dropped() -> None:
    """Events with ``u`` below the snapshot id are ignored."""
    assert _diff(_builder(), 80, 90, 79) is books.ApplyResult.IGNORED_OLD


def test_binance_first_diff_must_straddle_snapshot() -> None:
    """The first event must contain the snapshot id."""
    assert _diff(_builder(), 105, 110, 104) is (
        books.ApplyResult.RESYNC_REQUIRED
    )


def test_binance_diff_chain_applies_and_detects_gap() -> None:
    """Chained ``pu`` applies; a broken chain requires resync."""
    builder = _builder()
    assert _diff(builder, 95, 105, 94) is books.ApplyResult.APPLIED
    book = builder.book(1)
    assert book.bids[0] == market.BookLevel(_D("99"), _D("3"))
    assert book.asks[0].price == _D("102")
    assert _diff(builder, 106, 110, 105) is books.ApplyResult.APPLIED
    assert _diff(builder, 120, 130, 115) is books.ApplyResult.RESYNC_REQUIRED
    assert builder.needs_resync
    assert builder.book(1).stale
    assert builder.resyncs == 1


def test_generic_delta_requires_contiguous_sequence() -> None:
    """Generic deltas must be previous + 1."""
    builder = _builder()
    assert builder.apply_delta(101, [], [], 1, 1) is books.ApplyResult.APPLIED
    assert builder.apply_delta(101, [], [], 1, 1) is (
        books.ApplyResult.IGNORED_OLD
    )
    assert builder.apply_delta(103, [], [], 1, 1) is (
        books.ApplyResult.RESYNC_REQUIRED
    )


def test_book_goes_stale_with_age() -> None:
    """Books older than their budget are stale."""
    builder = _builder()
    assert not builder.book(500).stale
    assert builder.book(2_000).stale


def test_crossed_book_is_stale() -> None:
    """A crossed book is never trusted."""
    builder = books.OrderBookBuilder(_ID, "test")
    builder.apply_snapshot(
        _levels(("101", "1")), _levels(("100", "1")), 1, 0, 0
    )
    assert builder.book(0).stale


def _book(
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    stale: bool = False,
) -> market.OrderBook:
    """Builds a book.

    Args:
        bids: Bid levels.
        asks: Ask levels.
        stale: Staleness flag.

    Returns:
        Order book.
    """
    book = books.snapshot_book(
        _ID,
        _levels(*bids),
        _levels(*asks),
        ts_ms=0,
        received_ms=0,
        source="test",
    )
    if stale:
        return market.OrderBook(
            instrument_id=_ID,
            bids=book.bids,
            asks=book.asks,
            snapshot_ts_ms=0,
            received_at_ms=0,
            source="test",
            stale=True,
        )
    return book


def test_slippage_walks_both_sides_and_takes_worst() -> None:
    """Worst side slippage is measured from mid."""
    book = _book([("99", "1000")], [("101", "100"), ("111", "1000")])
    slippage = books.worst_side_slippage_pct(book, _D(20_000))
    assert slippage is not None
    assert slippage > _D(1)


def test_slippage_is_none_for_stale_or_thin_books() -> None:
    """Stale, one-sided or too-shallow books yield no slippage."""
    assert (
        books.worst_side_slippage_pct(
            _book([("99", "1")], [("101", "1")], stale=True)
        )
        is None
    )
    assert books.worst_side_slippage_pct(_book([], [("101", "1")])) is None
    assert (
        books.worst_side_slippage_pct(_book([("99", "1")], [("101", "1")]))
        is None
    )


@hypothesis.given(
    strategies.lists(
        strategies.integers(min_value=1, max_value=50),
        min_size=3,
        max_size=20,
    ),
    strategies.integers(min_value=1_000, max_value=40_000),
    strategies.integers(min_value=1_000, max_value=40_000),
)
def test_slippage_is_monotonic_in_notional(
    sizes: list[int], first: int, second: int
) -> None:
    """Walking a larger notional never reduces slippage."""
    bids = [(str(1000 - i), str(s)) for i, s in enumerate(sizes)]
    asks = [(str(1001 + i), str(s)) for i, s in enumerate(sizes)]
    book = _book(bids, asks)
    small, large = sorted((first, second))
    s_small = books.worst_side_slippage_pct(book, _D(small))
    s_large = books.worst_side_slippage_pct(book, _D(large))
    if s_large is not None:
        assert s_small is not None
        assert s_large >= s_small
        assert s_small > 0


def test_health_tracker_states() -> None:
    """Health derives HEALTHY, DEGRADED, STALE and UNAVAILABLE."""
    tracker = health.FeedHealthTracker(
        instruments.Venue.BINANCE, stale_after_ms=1_000
    )
    tracker.set_transport(quality.Transport.NATIVE_WS, quality.FeedState.LIVE)
    assert tracker.snapshot(0).state is quality.HealthState.DEGRADED
    tracker.record_message(100_000)
    assert tracker.snapshot(100_500).state is quality.HealthState.HEALTHY
    stale = tracker.snapshot(102_000)
    assert stale.state is quality.HealthState.STALE
    assert stale.feed_state is quality.FeedState.STALE
    tracker.record_reconnect(102_000, "ConnectionClosed")
    tracker.record_message(102_100)
    assert tracker.snapshot(102_200).state is quality.HealthState.DEGRADED
    tracker.set_unavailable(103_000, "boom")
    snapshot = tracker.snapshot(103_000)
    assert snapshot.state is quality.HealthState.UNAVAILABLE
    assert snapshot.reconnects == 1


def test_fallback_transport_is_degraded() -> None:
    """Running on a fallback is always visible as DEGRADED."""
    tracker = health.FeedHealthTracker(instruments.Venue.LIGHTER)
    tracker.set_transport(
        quality.Transport.REST_POLL, quality.FeedState.POLL, fallback=True
    )
    tracker.record_message(1_000_000)
    snapshot = tracker.snapshot(1_000_100)
    assert snapshot.state is quality.HealthState.DEGRADED
    assert snapshot.transport is quality.Transport.REST_POLL
