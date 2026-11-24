# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Seconds candles folded from public trades (pure).

No perpetual venue publishes sub-minute klines, so seconds bars are built
from the venue's public trades:

* OHLC come from the first, last and extreme trade prices of each bucket
  (venue time, then arrival order); base volume and quote turnover are sums
  (quote is ``price * quantity`` unless the venue publishes the quote
  amount); ``trade_count`` counts trade records and is ``None`` when the
  venue aggregates fills; ``taker_buy_base_volume`` sums buyer-aggressor
  quantity and is ``None`` when any trade of the bar has an unknown
  aggressor.
* Coverage: bars exist only where the trades are known to be complete. A
  coverage segment starts at a known instant (an aligned REST or backfill
  start) or, for a stream without backfill, at the first received trade, in
  which case that trade's bucket is partial and never emitted.
* Empty buckets between two observed trades of one segment become flat,
  zero-volume final bars at the previous close (the convention venues use
  for minute klines). Nothing is filled across a segment break, before the
  first trade or after the last one.
* Finalization: a bar becomes final when a trade of a later bucket arrives
  or when delivery is proven past its end (``finalize_through``).
* Duplicate trade ids are ignored. A late trade for an already final bar
  re-emits it (stores record a ``REVISED`` bar) together with any flat bars
  whose previous close it changes. Trades older than the retention window,
  or older than the segment's coverage, are dropped and counted; flat runs
  are emitted only within the retention window, so the empty seconds of a
  very quiet market stay gaps instead of flooding memory.
* Sessions: buckets follow the instrument's session calendar
  (``sessions.for_instrument``). Exchange-listed instruments get bars only
  inside sessions: flat runs never cross the time between sessions, a print
  up to ``close_grace_ms`` after the close (a closing auction stamped at the
  close) belongs to the session's last bar, and any other trade outside a
  session is dropped and counted in ``out_of_session``.
"""

from __future__ import annotations

import bisect
import dataclasses
import decimal
from collections.abc import Iterable

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import trades as trades_lib

_Key = tuple[int, int]
_EmitKey = tuple[object, ...]


@dataclasses.dataclass(slots=True)
class _Bucket:
    """Running aggregate of the trades of one bar.

    Attributes:
        start_ms: Bar start.
        open_key: Ordering key of the opening trade.
        open: Opening price.
        close_key: Ordering key of the closing trade.
        close: Closing price.
        high: Highest price.
        low: Lowest price.
        volume: Base volume.
        quote: Quote turnover.
        taker: Buyer-aggressor base volume.
        unknown_side: True when a trade had no published aggressor.
        count: Trade records.
        aggregated: True when any record aggregates several fills.
        last_ts: Newest venue timestamp.
        final: True once the bar is closed.
    """

    start_ms: int
    open_key: _Key
    open: decimal.Decimal
    close_key: _Key
    close: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    volume: decimal.Decimal
    quote: decimal.Decimal
    taker: decimal.Decimal
    unknown_side: bool
    count: int
    aggregated: bool
    last_ts: int
    final: bool = False

    @classmethod
    def first(cls, start_ms: int, trade: market.Trade, seq: int) -> _Bucket:
        """Creates a bucket from its first trade.

        Args:
            start_ms: Bar start.
            trade: First trade.
            seq: Arrival sequence number.

        Returns:
            Bucket.
        """
        key = (trade.venue_event_ts_ms, seq)
        bucket = cls(
            start_ms=start_ms,
            open_key=key,
            open=trade.price,
            close_key=key,
            close=trade.price,
            high=trade.price,
            low=trade.price,
            volume=decimals.ZERO,
            quote=decimals.ZERO,
            taker=decimals.ZERO,
            unknown_side=False,
            count=0,
            aggregated=False,
            last_ts=trade.venue_event_ts_ms,
        )
        bucket.add(trade, seq)
        return bucket

    def add(self, trade: market.Trade, seq: int) -> None:
        """Folds one trade into the aggregate.

        Args:
            trade: Trade of this bucket.
            seq: Arrival sequence number (breaks timestamp ties).
        """
        key = (trade.venue_event_ts_ms, seq)
        if key < self.open_key:
            self.open_key, self.open = key, trade.price
        if key > self.close_key:
            self.close_key, self.close = key, trade.price
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.volume += trade.base_quantity
        self.quote += (
            trade.quote_quantity
            if trade.quote_quantity is not None
            else trade.price * trade.base_quantity
        )
        if trade.aggressor_side is market.AggressorSide.BUY:
            self.taker += trade.base_quantity
        elif trade.aggressor_side is market.AggressorSide.UNKNOWN:
            self.unknown_side = True
        self.count += 1
        self.aggregated = self.aggregated or trade.is_aggregated
        self.last_ts = max(self.last_ts, trade.venue_event_ts_ms)


def _emit_key(candle: market.Candle) -> _EmitKey:
    """Returns the values whose change makes a bar worth re-emitting.

    Args:
        candle: Candle.

    Returns:
        Comparable tuple.
    """
    return (
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.base_volume,
        candle.quote_turnover,
        candle.trade_count,
        candle.taker_buy_base_volume,
        candle.is_final,
    )


class TradeBarBuilder:
    """Incrementally folds one instrument's trades into seconds candles.

    Attributes:
        instrument_id: Instrument of every trade.
        timeframe: Seconds resolution.
        source: Provenance label of emitted bars (e.g. ``binance.trades``).
        feed_state: Provenance state of emitted bars.
        buy_volume_method: How the trades' aggressor side was obtained
            (``classified`` on stock exchanges), stamped on emitted bars.
        calendar: Session calendar of the instrument.
        late_dropped: Trades dropped as older than coverage or retention.
        duplicates: Trades ignored as already seen.
        out_of_session: Trades dropped because no session bar holds them.
    """

    def __init__(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        *,
        source: str,
        feed_state: quality.FeedState = quality.FeedState.LIVE,
        retention_bars: int = 300,
        dedupe_capacity: int = 50_000,
        buy_volume_method: market.BuyVolumeMethod | None = None,
    ) -> None:
        """Initializes an empty builder (no coverage yet).

        Args:
            instrument_id: Instrument of every trade.
            timeframe: Seconds resolution.
            source: Provenance label of emitted bars.
            feed_state: Provenance state of emitted bars.
            retention_bars: Bars kept for late trades behind the newest one.
            dedupe_capacity: Trade ids remembered for de-duplication.
            buy_volume_method: Origin of the aggressor side (``None`` means
                the venue's taker flag).

        Raises:
            ValueError: For non-seconds timeframes or a bad retention.
        """
        if not timeframe.is_seconds:
            raise ValueError("trade bars are built for seconds timeframes")
        if retention_bars < 1:
            raise ValueError("retention_bars must be positive")
        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self.source = source
        self.feed_state = feed_state
        self.buy_volume_method = buy_volume_method
        self.calendar = sessions.for_instrument(instrument_id)
        self.late_dropped = 0
        self.duplicates = 0
        self.out_of_session = 0
        self._retention = retention_bars
        self._seen = trades_lib.RecentIds(dedupe_capacity)
        self._buckets: dict[int, _Bucket] = {}
        self._starts: list[int] = []
        self._emitted: dict[int, _EmitKey] = {}
        self._seq = 0
        self._complete_from: int | None = None
        self._partial: int | None = None

    def start_segment(self, complete_from_ms: int | None) -> int:
        """Starts a new coverage segment, forgetting the previous one.

        Call it at startup and after a disconnect that could not be
        backfilled. Nothing is filled across segments and open bars of the
        previous segment are discarded (they may miss trades).

        Args:
            complete_from_ms: Venue time from which every trade will be
                delivered, or ``None`` when unknown (the first trade's
                bucket is then partial).

        Returns:
            Number of open (non-final) bars discarded.
        """
        discarded = sum(
            1
            for start, bucket in self._buckets.items()
            if not bucket.final and start != self._partial
        )
        self._buckets.clear()
        self._starts.clear()
        self._emitted.clear()
        self._complete_from = complete_from_ms
        self._partial = None
        if complete_from_ms is not None:
            aligned = self._bar_of(complete_from_ms)
            if aligned is not None and aligned != complete_from_ms:
                self._partial = aligned
        return discarded

    def _bar_of(self, ts_ms: int) -> int | None:
        """Returns the start of the bar that holds a trade time.

        Args:
            ts_ms: Venue trade time.

        Returns:
            Bar start, or ``None`` between sessions (outside the closing
            grace).
        """
        if self.calendar.continuous:
            return self.timeframe.floor(ts_ms)
        try:
            start = self.calendar.floor(self.timeframe, ts_ms)
        except ValueError:
            return None
        end = self.calendar.end_of(self.timeframe, start)
        if ts_ms < end:
            return start
        grace = self.calendar.close_grace_ms
        if ts_ms < end + grace and not self.calendar.is_open(end):
            return start
        return None

    def _settled_ms(self, start: int) -> int:
        """Returns when no further trade can belong to a bar.

        Args:
            start: Bar start.

        Returns:
            The bar end, plus the closing grace for a session's last bar.
        """
        end = self.calendar.end_of(self.timeframe, start)
        grace = self.calendar.close_grace_ms
        if grace and not self.calendar.is_open(end):
            return end + grace
        return end

    def add(
        self, trades: Iterable[market.Trade], now_ms: int
    ) -> list[market.Candle]:
        """Folds trades in arrival order.

        Args:
            trades: Trades of this instrument.
            now_ms: Receipt time stamped on emitted bars.

        Returns:
            Bars whose values or finality changed, ascending.

        Raises:
            ValueError: If a trade belongs to another instrument.
        """
        touched: set[int] = set()
        for trade in trades:
            if trade.instrument_id != self.instrument_id:
                raise ValueError("trade of another instrument")
            if not self._seen.add(trade.trade_id):
                self.duplicates += 1
                continue
            ts = trade.venue_event_ts_ms
            bar_start = self._bar_of(ts)
            if self._complete_from is None:
                self._complete_from = ts
                self._partial = bar_start
            if bar_start is None:
                self.out_of_session += 1
                continue
            start = bar_start
            if ts < self._complete_from or (
                self._starts
                and start
                < self.calendar.shift(
                    self.timeframe, self._starts[-1], -self._retention
                )
            ):
                self.late_dropped += 1
                continue
            self._seq += 1
            bucket = self._buckets.get(start)
            if bucket is None:
                self._buckets[start] = _Bucket.first(start, trade, self._seq)
                bisect.insort(self._starts, start)
            else:
                bucket.add(trade, self._seq)
            touched.add(start)
        if not touched:
            return []
        newest = self._starts[-1]
        for start in self._starts[:-1]:
            bucket = self._buckets[start]
            if not bucket.final:
                bucket.final = True
                touched.add(start)
        del newest
        emitted = self._emit(touched, now_ms)
        self._evict()
        return emitted

    def finalize_through(
        self, proven_ms: int, now_ms: int
    ) -> list[market.Candle]:
        """Finalizes bars ending at or before a proven delivery time.

        Args:
            proven_ms: Venue time up to which every trade has been
                delivered (for streams: latest frame receipt minus grace).
            now_ms: Receipt time stamped on emitted bars.

        Returns:
            Newly final bars, ascending.
        """
        touched: set[int] = set()
        for start in self._starts:
            bucket = self._buckets[start]
            if not bucket.final and self._settled_ms(start) <= proven_ms:
                bucket.final = True
                touched.add(start)
        return self._emit(touched, now_ms) if touched else []

    def _neighbors(self, start: int) -> tuple[int | None, int | None]:
        """Returns the real buckets strictly before and after ``start``.

        Args:
            start: Bar start.

        Returns:
            ``(previous, next)`` bucket starts (``None`` when absent).
        """
        lo = bisect.bisect_left(self._starts, start)
        hi = bisect.bisect_right(self._starts, start)
        prev = self._starts[lo - 1] if lo > 0 else None
        nxt = self._starts[hi] if hi < len(self._starts) else None
        return prev, nxt

    def _emit(self, touched: set[int], now_ms: int) -> list[market.Candle]:
        """Builds changed bars for touched buckets and their flat runs.

        Args:
            touched: Real bucket starts whose state changed.
            now_ms: Receipt time.

        Returns:
            Changed bars, ascending.
        """
        horizon = (
            self.calendar.shift(
                self.timeframe, self._starts[-1], -self._retention
            )
            if self._starts
            else None
        )
        candidates = set(touched)
        for start in touched:
            prev, _ = self._neighbors(start)
            for anchor in (prev, start):
                if anchor is None:
                    continue
                _, nxt = self._neighbors(anchor)
                if nxt is None:
                    continue
                cursor = self.calendar.shift(self.timeframe, anchor, 1)
                if horizon is not None and cursor < horizon:
                    cursor = horizon
                while cursor < nxt:
                    candidates.add(cursor)
                    cursor = self.calendar.shift(self.timeframe, cursor, 1)
        out: list[market.Candle] = []
        for start in sorted(candidates):
            candle = self._candle_at(start, now_ms)
            if candle is None:
                continue
            key = _emit_key(candle)
            if self._emitted.get(start) == key:
                continue
            self._emitted[start] = key
            out.append(candle)
        return out

    def _candle_at(self, start: int, now_ms: int) -> market.Candle | None:
        """Builds the bar at ``start`` from its bucket or as a flat bar.

        Args:
            start: Bar start.
            now_ms: Receipt time.

        Returns:
            Candle, or ``None`` when the bar is partial or not fillable.
        """
        end = self.calendar.end_of(self.timeframe, start)
        bucket = self._buckets.get(start)
        if bucket is not None:
            if start == self._partial:
                return None
            return market.Candle(
                instrument_id=self.instrument_id,
                timeframe=self.timeframe,
                start_ms=start,
                end_ms=end,
                open=bucket.open,
                high=bucket.high,
                low=bucket.low,
                close=bucket.close,
                base_volume=bucket.volume,
                is_final=bucket.final,
                source=self.source,
                feed_state=self.feed_state,
                received_at_ms=now_ms,
                quote_turnover=bucket.quote,
                trade_count=None if bucket.aggregated else bucket.count,
                event_ts_ms=bucket.last_ts,
                taker_buy_base_volume=(
                    None if bucket.unknown_side else bucket.taker
                ),
                taker_buy_method=(
                    None if bucket.unknown_side else self.buy_volume_method
                ),
            )
        prev, nxt = self._neighbors(start)
        if prev is None or nxt is None:
            return None
        price = self._buckets[prev].close
        return market.Candle(
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            start_ms=start,
            end_ms=end,
            open=price,
            high=price,
            low=price,
            close=price,
            base_volume=decimals.ZERO,
            is_final=True,
            source=self.source,
            feed_state=self.feed_state,
            received_at_ms=now_ms,
            quote_turnover=decimals.ZERO,
            trade_count=0,
            taker_buy_base_volume=decimals.ZERO,
            taker_buy_method=self.buy_volume_method,
        )

    def _evict(self) -> None:
        """Forgets buckets and emitted keys behind the retention window."""
        if not self._starts:
            return
        horizon = self.calendar.shift(
            self.timeframe, self._starts[-1], -self._retention
        )
        cut = bisect.bisect_left(self._starts, horizon)
        if cut > 0:
            for start in self._starts[:cut]:
                del self._buckets[start]
            del self._starts[:cut]
        stale = [s for s in self._emitted if s < horizon]
        for start in stale:
            del self._emitted[start]


def bars_from_coverage(
    coverage: trades_lib.TradeCoverage,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    *,
    source: str,
    now_ms: int,
    feed_state: quality.FeedState = quality.FeedState.LIVE,
    buy_volume_method: market.BuyVolumeMethod | None = None,
) -> list[market.Candle]:
    """Builds the bars of a complete trade history interval.

    Bars ending at or before ``coverage.to_ms`` are final; the bar that
    straddles ``coverage.to_ms`` (if it has trades) is returned non-final.
    A non-aligned ``coverage.from_ms`` makes its bucket partial (omitted).

    Args:
        coverage: Trades complete over an interval.
        instrument_id: Instrument.
        timeframe: Seconds resolution.
        source: Provenance label.
        now_ms: Receipt time.
        feed_state: Provenance state.
        buy_volume_method: Origin of the trades' aggressor side.

    Returns:
        Ascending bars inside the coverage.
    """
    calendar = sessions.for_instrument(instrument_id)
    try:
        first = calendar.floor(timeframe, coverage.from_ms)
    except ValueError:
        first = coverage.from_ms
    span = calendar.bars_between(timeframe, first, coverage.to_ms)
    builder = TradeBarBuilder(
        instrument_id,
        timeframe,
        source=source,
        feed_state=feed_state,
        retention_bars=max(1, span + 1),
        dedupe_capacity=max(1, len(coverage.trades)),
        buy_volume_method=buy_volume_method,
    )
    builder.start_segment(coverage.from_ms)
    out: dict[int, market.Candle] = {}
    for candle in builder.add(trades_lib.sort_trades(coverage.trades), now_ms):
        out[candle.start_ms] = candle
    for candle in builder.finalize_through(coverage.to_ms, now_ms):
        out[candle.start_ms] = candle
    return [out[start] for start in sorted(out)]
