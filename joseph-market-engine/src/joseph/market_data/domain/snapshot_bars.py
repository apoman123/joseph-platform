# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""One-minute bars built from periodic exchange snapshots (pure).

Some exchanges (TWSE's MIS, which also serves TPEx) publish no trade feed,
only snapshots of the day's cumulative volume, last price, day high/low and
the best quotes. Bars are reconstructed from consecutive snapshots of one
trading day:

* **Derived trades.** The increase of cumulative volume between two
  snapshots is one derived trade. It is *priced* when the newer snapshot
  shows a trade at its own data time (``last_price``) or a last trade
  stamped inside the interval (``trade_price``/``trade_ms``); otherwise its
  price is unknown.
* **Attribution.** A derived trade belongs to the bar containing the
  instant just before the newer snapshot's data time (the interval's
  trades happened before it), clamped to the session open.
* **Prices.** Bar open/close/high/low come from priced derived trades in
  data-time order. When the day high (low) rises (falls) between two
  snapshots of the same bar, that bar's high (low) is the new day extreme,
  which is exact; extremes printed between snapshots of *different* bars
  cannot be placed and may be missed.
* **Volume.** Every derived trade's volume counts, priced or not; a bar
  without any priced trade has no prices and is not emitted.
* **Buy volume** is classified (quote rule against the previous snapshot's
  best bid/ask, tick test fallback) and reported only when every derived
  trade of the bar was priced and classified.
* **Quiet bars.** When the cumulative volume did not change between two
  snapshots, the bars lying entirely between them provably had no trades
  and become flat zero-volume bars at the last known price.
* **Coverage.** Bars exist only inside coverage segments. A segment starts
  at a baseline snapshot (the first of a day, or the first after a gap
  longer than ``max_gap_ms``), whose volume so far is not attributed; the
  bar containing a baseline is partial and omitted.
* **Finality.** A bar is final once a snapshot of its segment with a data
  time at or after its end has been applied (later volume belongs to later
  bars).
* **Recording coverage.** Inside a segment, every bar ending by its newest
  snapshot has its final attribution, so a minute there without a bar had
  no (attributed) trades. ``coverage`` returns those spans, minus minutes
  whose volume has no known price (they traded but are not emitted) and
  minus bars dropped by the retention.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import classification
from joseph.market_data.domain import session_bars

_M1 = timeframes.Timeframe.M1
# Open end for clipping coverage spans, later than any data time.
_FOREVER_MS = 2**63


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshot:
    """One exchange snapshot of an instrument's trading day.

    Attributes:
        day: Exchange-local trading date.
        data_ms: Time of the data, epoch milliseconds (exchange clock).
        cumulative_volume: Day volume so far, in base units (shares).
        last_price: Price of a trade printed exactly at ``data_ms``.
        trade_price: Price of the latest trade the source reports.
        trade_ms: Time of that trade, epoch milliseconds.
        day_open: Official day open.
        day_high: Official day high.
        day_low: Official day low.
        best_bid: Best bid.
        best_ask: Best ask.
    """

    day: datetime.date
    data_ms: int
    cumulative_volume: decimal.Decimal
    last_price: decimal.Decimal | None = None
    trade_price: decimal.Decimal | None = None
    trade_ms: int | None = None
    day_open: decimal.Decimal | None = None
    day_high: decimal.Decimal | None = None
    day_low: decimal.Decimal | None = None
    best_bid: decimal.Decimal | None = None
    best_ask: decimal.Decimal | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DerivedTrade:
    """Volume traded between two snapshots.

    Attributes:
        data_ms: Data time of the newer snapshot, epoch milliseconds.
        quantity: Base volume.
        price: Price when known.
        side: Classified side (``UNKNOWN`` when unpriced or unclassified).
    """

    data_ms: int
    quantity: decimal.Decimal
    price: decimal.Decimal | None
    side: market.AggressorSide


@dataclasses.dataclass(slots=True)
class _Bar:
    """Running state of one bar.

    Attributes:
        start_ms: Bar start, epoch milliseconds.
        open: Open (first priced trade).
        high: High.
        low: Low.
        close: Close (last priced trade).
        volume: Base volume of every derived trade.
        buy: Classified buy volume.
        unknown: True when a derived trade was unpriced or unclassified.
        last_data_ms: Newest data time applied.
    """

    start_ms: int
    open: decimal.Decimal | None = None
    high: decimal.Decimal | None = None
    low: decimal.Decimal | None = None
    close: decimal.Decimal | None = None
    volume: decimal.Decimal = decimals.ZERO
    buy: decimal.Decimal = decimals.ZERO
    unknown: bool = False
    last_data_ms: int = 0

    def price(self, value: decimal.Decimal) -> None:
        """Folds a trade price.

        Args:
            value: Price.
        """
        if self.open is None:
            self.open = value
        self.close = value
        self.high = value if self.high is None else max(self.high, value)
        self.low = value if self.low is None else min(self.low, value)


class SnapshotBarBuilder:
    """Builds one-minute bars of one instrument from its snapshots.

    Attributes:
        instrument_id: Instrument.
        source: Provenance label of emitted bars.
        applied: Snapshots applied.
        ignored: Snapshots ignored (stale, repeated or out of session).
    """

    def __init__(
        self,
        instrument_id: instruments.InstrumentId,
        *,
        source: str,
        retention_bars: int = 2_000,
        max_gap_ms: int = 300_000,
    ) -> None:
        """Initializes an empty builder.

        Args:
            instrument_id: Instrument (its calendar sets the grid).
            source: Provenance label.
            retention_bars: One-minute bars kept (oldest dropped).
            max_gap_ms: Silences between snapshots longer than this, in
                milliseconds, start a new coverage segment instead of
                attributing their volume.
        """
        self.instrument_id = instrument_id
        self.source = source
        self.applied = 0
        self.ignored = 0
        self._calendar = sessions.for_instrument(instrument_id)
        self._retention = retention_bars
        self._max_gap_ms = max_gap_ms
        self._segments: list[list[int]] = []
        self._bars: dict[int, _Bar] = {}
        self._previous: Snapshot | None = None
        self._last_price: decimal.Decimal | None = None
        self._classifier = classification.LeeReadyClassifier()
        self._retained_from: int | None = None

    @property
    def latest(self) -> Snapshot | None:
        """The newest applied snapshot (``None`` before the first)."""
        return self._previous

    def segment_at(self, ts_ms: int) -> tuple[int, int] | None:
        """Returns the coverage segment containing an instant.

        Args:
            ts_ms: Instant, epoch milliseconds (typically a bar start).

        Returns:
            ``(first, last)`` data times of the segment, both inclusive, or
            ``None`` when no segment contains ``ts_ms``.
        """
        for first, last in reversed(self._segments):
            if first <= ts_ms <= last:
                return first, last
        return None

    def coverage(self) -> list[candle_rules.Span]:
        """Returns the spans in which every one-minute bar is known.

        Returns:
            Disjoint ascending spans: the coverage segments, without the
            minutes holding volume of unknown price and without the time
            before the oldest retained bar (after an eviction).
        """
        spans = [(first, last) for first, last in self._segments]
        unpriced = [
            (start, self._calendar.end_of(_M1, start))
            for start, bar in self._bars.items()
            if bar.open is None
        ]
        spans = candle_rules.subtract_spans(spans, unpriced)
        if self._retained_from is not None:
            spans = candle_rules.clip_spans(
                spans, self._retained_from, _FOREVER_MS
            )
        return spans

    def _bar_start(self, data_ms: int) -> int | None:
        """Returns the bar of the trades just before ``data_ms``.

        Args:
            data_ms: Snapshot data time.

        Returns:
            One-minute bar start, or ``None`` outside the session.
        """
        try:
            session = self._session_of(data_ms)
        except ValueError:
            return None
        if session is None:
            return None
        moment = max(data_ms - 1, session.open_ms)
        return self._calendar.floor(_M1, moment)

    def _session_of(self, data_ms: int) -> sessions.Session | None:
        """Returns the session a data time belongs to (with close grace).

        Args:
            data_ms: Snapshot data time.

        Returns:
            Session, or ``None`` when the time is outside every session.
        """
        calendar = self._calendar
        if not isinstance(calendar, sessions.ExchangeCalendar):
            return None
        session = calendar.session_at_or_before(data_ms)
        if data_ms > session.close_ms + calendar.close_grace_ms:
            return None
        return session

    def apply(self, snapshot: Snapshot) -> list[DerivedTrade]:
        """Applies the next snapshot of the instrument.

        Snapshots older than or equal to the newest one (by data time), or
        with a falling cumulative volume on the same day, are ignored. A
        new trading day starts afresh (its first snapshot is a baseline).

        Args:
            snapshot: Snapshot.

        Returns:
            A one-element list with the derived trade when the volume
            increased inside a session; empty for an ignored or baseline
            snapshot, or when no volume was added.
        """
        previous = self._previous
        if (
            previous is not None
            and previous.day == snapshot.day
            and (
                snapshot.data_ms <= previous.data_ms
                or snapshot.cumulative_volume < previous.cumulative_volume
            )
        ):
            self.ignored += 1
            return []
        if (
            previous is None
            or previous.day != snapshot.day
            or snapshot.data_ms - previous.data_ms > self._max_gap_ms
        ):
            self._baseline(
                snapshot,
                new_day=previous is None or (previous.day != snapshot.day),
            )
            return []
        start = self._bar_start(snapshot.data_ms)
        self._previous = snapshot
        self._segments[-1][1] = snapshot.data_ms
        self.applied += 1
        if start is None:
            return []
        traded = snapshot.cumulative_volume - previous.cumulative_volume
        out: list[DerivedTrade] = []
        if traded > 0:
            out.append(self._trade(previous, snapshot, start, traded))
        else:
            self._quiet(previous, snapshot)
        self._extremes(previous, snapshot, start)
        self._evict()
        return out

    def _baseline(self, snapshot: Snapshot, *, new_day: bool) -> None:
        """Starts a coverage segment at a snapshot.

        Args:
            snapshot: Baseline snapshot (its volume so far is not
                attributed to any bar).
            new_day: True at the first snapshot of a trading day.
        """
        if new_day:
            self._classifier.reset()
        if snapshot.last_price is not None and snapshot.cumulative_volume > 0:
            self._last_price = snapshot.last_price
        else:
            self._last_price = None
        self._previous = snapshot
        self._segments.append([snapshot.data_ms, snapshot.data_ms])
        self.applied += 1

    def _price(
        self, previous: Snapshot, snapshot: Snapshot
    ) -> decimal.Decimal | None:
        """Returns the known price of the trades between two snapshots.

        Args:
            previous: Older snapshot.
            snapshot: Newer snapshot.

        Returns:
            Price, or ``None`` when no trade inside the interval is known.
        """
        if snapshot.last_price is not None:
            return snapshot.last_price
        if (
            snapshot.trade_price is not None
            and snapshot.trade_ms is not None
            and previous.data_ms < snapshot.trade_ms <= snapshot.data_ms
        ):
            return snapshot.trade_price
        return None

    def _trade(
        self,
        previous: Snapshot,
        snapshot: Snapshot,
        start: int,
        quantity: decimal.Decimal,
    ) -> DerivedTrade:
        """Folds a derived trade into its bar.

        Args:
            previous: Older snapshot (its quote prevailed).
            snapshot: Newer snapshot.
            start: Bar start.
            quantity: Traded volume.

        Returns:
            The derived trade.
        """
        bar = self._bars.setdefault(start, _Bar(start))
        bar.volume += quantity
        bar.last_data_ms = snapshot.data_ms
        price = self._price(previous, snapshot)
        side = market.AggressorSide.UNKNOWN
        if price is None:
            bar.unknown = True
            self._last_price = None
        else:
            side = self._classifier.classify(
                price, previous.best_bid, previous.best_ask
            )
            bar.price(price)
            self._last_price = price
            if side is market.AggressorSide.BUY:
                bar.buy += quantity
            elif side is market.AggressorSide.UNKNOWN:
                bar.unknown = True
        return DerivedTrade(snapshot.data_ms, quantity, price, side)

    def _quiet(self, previous: Snapshot, snapshot: Snapshot) -> None:
        """Creates flat bars proven to have had no trades.

        Args:
            previous: Older snapshot.
            snapshot: Newer snapshot with the same cumulative volume.
        """
        price = self._last_price
        if price is None:
            return
        calendar = self._calendar
        try:
            cursor = calendar.shift(_M1, previous.data_ms, 0)
        except ValueError:
            return
        while cursor < snapshot.data_ms:
            end = calendar.end_of(_M1, cursor)
            if end > snapshot.data_ms:
                break
            if cursor not in self._bars:
                bar = _Bar(cursor, last_data_ms=snapshot.data_ms)
                bar.price(price)
                self._bars[cursor] = bar
            cursor = calendar.shift(_M1, cursor, 1)

    def _extremes(
        self, previous: Snapshot, snapshot: Snapshot, start: int
    ) -> None:
        """Places new day extremes reached inside one bar.

        Args:
            previous: Older snapshot.
            snapshot: Newer snapshot.
            start: Bar of the newer snapshot.
        """
        bar = self._bars.get(start)
        if bar is None or bar.open is None:
            return
        if self._bar_start(previous.data_ms + 1) != start:
            return
        high, low = snapshot.day_high, snapshot.day_low
        if high is not None and (
            previous.day_high is None or high > previous.day_high
        ):
            bar.high = high if bar.high is None else max(bar.high, high)
        if low is not None and (
            previous.day_low is None or low < previous.day_low
        ):
            bar.low = low if bar.low is None else min(bar.low, low)

    def _evict(self) -> None:
        """Drops the oldest bars beyond the retention (and old segments)."""
        while len(self._bars) > self._retention:
            dropped = min(self._bars)
            del self._bars[dropped]
            self._retained_from = self._calendar.end_of(_M1, dropped)
        oldest = min(self._bars, default=None)
        while (
            len(self._segments) > 1
            and oldest is not None
            and self._segments[0][1] < oldest
        ):
            del self._segments[0]

    def bars(
        self, start_ms: int, end_ms: int, now_ms: int
    ) -> list[market.Candle]:
        """Returns the one-minute bars starting in ``[start_ms, end_ms)``.

        Args:
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            now_ms: Receipt time stamped on the candles, epoch milliseconds.

        Returns:
            Ascending candles (bars without a priced trade, or starting
            before their coverage segment, are omitted); a bar is final once
            a snapshot of its segment at or after its end was applied.
        """
        out = []
        for key in sorted(self._bars):
            bar = self._bars[key]
            if not start_ms <= key < end_ms or bar.open is None:
                continue
            segment = self.segment_at(key)
            if segment is None:
                continue
            if bar.high is None or bar.low is None or bar.close is None:
                raise AssertionError("a priced bar has a high, low and close")
            out.append(
                session_bars.candle(
                    instrument_id=self.instrument_id,
                    timeframe=_M1,
                    start_ms=key,
                    open_=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    base_volume=bar.volume,
                    now_ms=now_ms,
                    source=self.source,
                    is_final=self._calendar.end_of(_M1, key) <= segment[1],
                    feed_state=quality.FeedState.LIVE,
                    event_ts_ms=bar.last_data_ms or None,
                    taker_buy_base_volume=None if bar.unknown else bar.buy,
                    taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
                )
            )
        return out
