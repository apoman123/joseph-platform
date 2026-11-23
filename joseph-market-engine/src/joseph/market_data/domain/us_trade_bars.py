"""Seconds bars from US consolidated-tape trades (pure).

US trades carry sale conditions that decide which parts of a bar a trade
may update. The table below is the one Alpaca documents for its own minute
bars (https://docs.alpaca.markets/docs/market-data-faq, "How are bars
aggregated?", rules for bar type ``M``), derived from the CTS (tapes A/B)
and UTP (tape C) specifications:

=====  ===============================  =====  ====  =====  ======
Cond.  Meaning                          Tape   O/C   H/L    Volume
=====  ===============================  =====  ====  =====  ======
" "    Regular sale                     A B    yes   yes    yes
``@``  Regular sale                     C O    yes   yes    yes
A      Acquisition                      C      yes   yes    yes
B      Average price trade              A B    no    no     yes
B      Bunched trade                    C      yes   yes    yes
C      Cash sale                        all    no    no     yes
D      Distribution                     C      yes   yes    yes
E      Automatic execution              A B    yes   yes    yes
F      Intermarket sweep                A B C  yes   yes    yes
G      Bunched sold trade               C      no    no     yes
H      Price variation trade            A B C  no    no     yes
I      Odd lot trade                    all    no    no     yes
K      Rule 127 / Rule 155              A B C  yes   yes    yes
L      Sold last                        A B C  yes   yes    yes
M      Market center official close     A B C  no    no     no
N      Next day                         all    no    no     yes
O      Market center opening trade      A B C  yes   yes    yes
P      Prior reference price            all    no    no     yes
Q      Market center official open      A B C  no    no     no
R      Seller                           all    no    no     yes
T      Extended hours trade             all    yes   yes    yes
U      Extended trading hours (sold OOS) all   no    no     yes
V      Contingent trade                 A B C  no    no     yes
W      Average price trade              C O    no    no     yes
X      Cross trade                      A B C  yes   yes    yes
Y      Yellow flag regular trade        C      yes   yes    yes
Z      Sold out of sequence             A B C  no    no     yes
4      Derivatively priced              A B C  no    no     yes
5      Market center reopening trade    A B C  yes   yes    yes
6      Market center closing trade      A B C  yes   yes    yes
7      Qualified contingent trade       A B C  no    no     yes
9      Corrected consolidated close     A B C  no    no     no
=====  ===============================  =====  ====  =====  ======

A trade with several conditions gets the strictest combination; unknown
conditions are treated as regular. Only trades inside a session of the
instrument's calendar are folded (extended-hours trades are outside), so a
bar exists only for an interval with at least one price-updating trade and
never spans a session boundary; quiet intervals have no bar (the Alpaca
minute-bar convention). Buy volume sums the volume of trades classified as
buys (Lee-Ready, ``classification``) and is reported only when every
volume-updating trade of the bar was classified.

The one exception is the official closing print: the closing cross is
stamped just after the close, so a trade with condition ``6`` (market
center closing trade) stamped in ``[close, close + CLOSING_WINDOW_MS)``
joins the session's last bar. Every other after-close trade (Form T ``T``,
late reports, ...) stays out. The XNYS calendar itself keeps no close
grace because Alpaca's 16:00 minute bar also holds post-market trades. The
session's last seconds bar therefore settles (becomes final) only once its
closing window has been delivered.

Minute bars come from Alpaca, whose minute holding the closing print (16:00,
or 13:00 on an early close) also holds post-market trades and lies outside
the session. ``with_closing_prints`` folds the condition-``6`` prints of
each session's closing window into the session's last minute bar with the
same update rules (``fold_closing_prints``), so that bar, and every
intraday aggregate of it, closes at the official close; a session whose
prints are not known yet keeps its last minute bar open and its slot out of
coverage (``closing_slot``).
"""

from __future__ import annotations

import bisect
import dataclasses
import decimal
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import session_bars


@dataclasses.dataclass(frozen=True, slots=True)
class UpdateRule:
    """Which bar fields a trade updates.

    Attributes:
        open_close: Open and close.
        high_low: High and low.
        volume: Volume (and trade count).
    """

    open_close: bool
    high_low: bool
    volume: bool

    def strictest(self, other: UpdateRule) -> UpdateRule:
        """Combines two rules, keeping only what both allow.

        Args:
            other: Rule of another condition.

        Returns:
            Combined rule.
        """
        return UpdateRule(
            self.open_close and other.open_close,
            self.high_low and other.high_low,
            self.volume and other.volume,
        )


_ALL = UpdateRule(True, True, True)
_VOLUME = UpdateRule(False, False, True)
_NONE = UpdateRule(False, False, False)
_AB = frozenset("AB")
_C = frozenset("C")
_RULES: dict[str, tuple[tuple[frozenset[str] | None, UpdateRule], ...]] = {
    " ": ((None, _ALL),),
    "@": ((None, _ALL),),
    "A": ((None, _ALL),),
    "B": ((_AB, _VOLUME), (None, _ALL)),
    "C": ((None, _VOLUME),),
    "D": ((None, _ALL),),
    "E": ((None, _ALL),),
    "F": ((None, _ALL),),
    "G": ((None, _VOLUME),),
    "H": ((None, _VOLUME),),
    "I": ((None, _VOLUME),),
    "K": ((None, _ALL),),
    "L": ((None, _ALL),),
    "M": ((None, _NONE),),
    "N": ((None, _VOLUME),),
    "O": ((None, _ALL),),
    "P": ((None, _VOLUME),),
    "Q": ((None, _NONE),),
    "R": ((None, _VOLUME),),
    "T": ((None, _ALL),),
    "U": ((None, _VOLUME),),
    "V": ((None, _VOLUME),),
    "W": ((None, _VOLUME),),
    "X": ((None, _ALL),),
    "Y": ((None, _ALL),),
    "Z": ((None, _VOLUME),),
    "4": ((None, _VOLUME),),
    "5": ((None, _ALL),),
    "6": ((None, _ALL),),
    "7": ((None, _VOLUME),),
    "9": ((None, _NONE),),
}


CLOSING_TRADE = "6"
"""Sale condition of the market center closing trade (table above)."""
CLOSING_WINDOW_MS = 60_000
"""A closing trade stamped this soon after the close joins the last bar."""


def _closing_session(
    calendar: sessions.BarCalendar, ts_ms: int
) -> sessions.Session | None:
    """Returns the session whose closing window contains an instant.

    Args:
        calendar: Instrument calendar.
        ts_ms: Instant.

    Returns:
        The session when ``close <= ts_ms < close + CLOSING_WINDOW_MS``,
        else ``None`` (always on a continuous calendar).
    """
    if not isinstance(calendar, sessions.ExchangeCalendar):
        return None
    try:
        found = calendar.session_at_or_before(ts_ms)
    except ValueError:
        return None
    if found.close_ms <= ts_ms < found.close_ms + CLOSING_WINDOW_MS:
        return found
    return None


def closing_window_end(
    instrument_id: instruments.InstrumentId, end_ms: int
) -> int:
    """Extends a trade window that ends inside a closing window.

    A window ending at a session close must also read the closing window,
    or the session's last bar would miss its closing print.

    Args:
        instrument_id: Instrument.
        end_ms: Exclusive end of a trade window.

    Returns:
        The end of the closing window containing ``end_ms``, else
        ``end_ms``.
    """
    found = _closing_session(sessions.for_instrument(instrument_id), end_ms)
    return end_ms if found is None else found.close_ms + CLOSING_WINDOW_MS


def settled_until(
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    delivered_ms: int,
) -> int:
    """Returns the time before which bars are settled by delivered trades.

    Trades are delivered completely before ``delivered_ms``. Inside a
    closing window the session's last bar can still receive the closing
    print, so the settled time stops at that bar's start.

    Args:
        instrument_id: Instrument.
        timeframe: Seconds resolution.
        delivered_ms: Exclusive end of the complete trades.

    Returns:
        Settled time (bars ending by it are final).
    """
    calendar = sessions.for_instrument(instrument_id)
    found = _closing_session(calendar, delivered_ms)
    if found is None:
        return delivered_ms
    return calendar.floor(timeframe, found.close_ms - 1)


def update_rule(conditions: Iterable[str], tape: str | None) -> UpdateRule:
    """Returns the combined update rule of a trade's conditions.

    Args:
        conditions: Sale condition codes.
        tape: Tape (``A``, ``B``, ``C``, ``O``).

    Returns:
        Strictest rule over the conditions (regular when none apply).
    """
    rule = _ALL
    for code in conditions:
        for tapes, candidate in _RULES.get(code, ((None, _ALL),)):
            if tapes is None or (tape is not None and tape in tapes):
                rule = rule.strictest(candidate)
                break
    return rule


@dataclasses.dataclass(frozen=True, slots=True)
class TapeTrade:
    """A consolidated-tape trade with its classification.

    Attributes:
        trade_id: Unique id (exchange code and trade id).
        ts_ms: Trade time.
        price: Price.
        size: Shares.
        conditions: Sale conditions.
        tape: Tape.
        side: Classified side.
    """

    trade_id: str
    ts_ms: int
    price: decimal.Decimal
    size: decimal.Decimal
    conditions: tuple[str, ...]
    tape: str | None
    side: market.AggressorSide


@dataclasses.dataclass(slots=True)
class _Bucket:
    """Running aggregate of one seconds bar.

    Attributes:
        open: Open.
        high: High.
        low: Low.
        close: Close.
        volume: Volume.
        buy: Classified buy volume.
        unknown: True when a volume trade was unclassified.
        count: Volume-updating trades.
        last_ts: Newest trade time.
        open_key: Ordering key of the open.
        close_key: Ordering key of the close.
        final: True once closed.
    """

    open: decimal.Decimal | None = None
    high: decimal.Decimal | None = None
    low: decimal.Decimal | None = None
    close: decimal.Decimal | None = None
    volume: decimal.Decimal = decimals.ZERO
    buy: decimal.Decimal = decimals.ZERO
    unknown: bool = False
    count: int = 0
    last_ts: int = 0
    open_key: tuple[int, int] = (0, 0)
    close_key: tuple[int, int] = (0, 0)
    final: bool = False


class SecondsBarBuilder:
    """Folds one instrument's tape trades into session seconds bars.

    Attributes:
        instrument_id: Instrument.
        timeframe: Seconds resolution.
        source: Provenance label.
        outside_session: Trades skipped as outside every session.
        duplicates: Trades ignored as already seen.
    """

    def __init__(
        self,
        instrument_id: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        *,
        source: str,
        retention_bars: int = 600,
        feed_state: quality.FeedState = quality.FeedState.LIVE,
    ) -> None:
        """Initializes an empty builder.

        Args:
            instrument_id: Instrument.
            timeframe: Seconds resolution.
            source: Provenance label.
            retention_bars: Buckets kept behind the newest one.
            feed_state: Provenance state of emitted bars.

        Raises:
            ValueError: For non-seconds timeframes.
        """
        if not timeframe.is_seconds:
            raise ValueError("seconds bars need a seconds timeframe")
        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self.source = source
        self.feed_state = feed_state
        self.outside_session = 0
        self.duplicates = 0
        self._calendar = sessions.for_instrument(instrument_id)
        self._retention = retention_bars
        self._buckets: dict[int, _Bucket] = {}
        self._starts: list[int] = []
        self._seen: set[str] = set()
        self._seq = 0
        self._complete_from: int | None = None
        self._partial: int | None = None

    def start_segment(self, complete_from_ms: int | None) -> None:
        """Starts a new coverage segment, forgetting open bars.

        Args:
            complete_from_ms: Time from which every trade will be delivered,
                or ``None`` when unknown (the first trade's bar is then
                partial and never emitted).
        """
        self._buckets = {s: b for s, b in self._buckets.items() if b.final}
        self._starts = sorted(self._buckets)
        self._complete_from = complete_from_ms
        self._partial = None
        if complete_from_ms is not None:
            aligned = self.timeframe.floor(complete_from_ms)
            if aligned != complete_from_ms:
                self._partial = aligned

    def _bucket_start(self, trade: TapeTrade) -> int | None:
        """Returns the bar of a trade, or ``None`` outside sessions.

        Args:
            trade: Trade.

        Returns:
            Bar start: the bar containing the trade, or the session's last
            bar for a closing trade stamped in the closing window.
        """
        calendar = self._calendar
        if calendar.is_open(trade.ts_ms):
            return calendar.floor(self.timeframe, trade.ts_ms)
        if CLOSING_TRADE not in trade.conditions:
            return None
        found = _closing_session(calendar, trade.ts_ms)
        if found is None:
            return None
        return calendar.floor(self.timeframe, found.close_ms - 1)

    def _settles_at(self, start: int) -> int:
        """Returns the time after which no trade can join a bar.

        Args:
            start: Bar start.

        Returns:
            The bar end, or for a session's last bar the end of its
            closing window.
        """
        end = self._calendar.end_of(self.timeframe, start)
        if self._calendar.is_open(end):
            return end
        return end + CLOSING_WINDOW_MS

    def add(
        self, trades: Iterable[TapeTrade], now_ms: int
    ) -> list[market.Candle]:
        """Folds trades in arrival order.

        Args:
            trades: Trades of the instrument.
            now_ms: Receipt time.

        Returns:
            Bars whose values or finality changed, ascending.
        """
        touched: set[int] = set()
        for trade in trades:
            if trade.trade_id in self._seen:
                self.duplicates += 1
                continue
            self._seen.add(trade.trade_id)
            start = self._bucket_start(trade)
            if start is None:
                self.outside_session += 1
                continue
            if self._complete_from is None:
                self._complete_from = trade.ts_ms
                self._partial = start
            if trade.ts_ms < self._complete_from:
                continue
            rule = update_rule(trade.conditions, trade.tape)
            if not (rule.volume or rule.open_close or rule.high_low):
                continue
            self._seq += 1
            bucket = self._buckets.get(start)
            if bucket is None:
                bucket = _Bucket()
                self._buckets[start] = bucket
                bisect.insort(self._starts, start)
            self._fold(bucket, trade, rule)
            touched.add(start)
        if touched:
            newest = self._starts[-1]
            for start in self._starts:
                if start < newest and not self._buckets[start].final:
                    self._buckets[start].final = True
                    touched.add(start)
        out = self._emit(touched, now_ms)
        self._evict()
        return out

    def _fold(
        self, bucket: _Bucket, trade: TapeTrade, rule: UpdateRule
    ) -> None:
        """Applies one trade to a bucket.

        Args:
            bucket: Bucket.
            trade: Trade.
            rule: Its update rule.
        """
        key = (trade.ts_ms, self._seq)
        if rule.open_close:
            if bucket.open is None or key < bucket.open_key:
                bucket.open, bucket.open_key = trade.price, key
            if bucket.close is None or key >= bucket.close_key:
                bucket.close, bucket.close_key = trade.price, key
        if rule.high_low:
            bucket.high = (
                trade.price
                if bucket.high is None
                else max(bucket.high, trade.price)
            )
            bucket.low = (
                trade.price
                if bucket.low is None
                else min(bucket.low, trade.price)
            )
        if rule.volume:
            bucket.volume += trade.size
            bucket.count += 1
            if trade.side is market.AggressorSide.BUY:
                bucket.buy += trade.size
            elif trade.side is market.AggressorSide.UNKNOWN:
                bucket.unknown = True
        bucket.last_ts = max(bucket.last_ts, trade.ts_ms)

    def coverage(self, delivered_ms: int) -> candle_rules.Span | None:
        """Returns the span whose every bar the current segment delivered.

        Every trade from the segment's start up to ``delivered_ms`` has
        been folded, so a slot there without a bar had no trades. The bar
        of the segment's first trade is partial and not covered, and a
        session's last bar only once its closing window was delivered.

        Args:
            delivered_ms: Trades before this time have all been delivered.

        Returns:
            ``(from, to)``, or ``None`` before the segment's first trade.
        """
        if self._complete_from is None:
            return None
        start = self._complete_from
        if self._partial is not None:
            start = max(
                start, self._calendar.end_of(self.timeframe, self._partial)
            )
        end = settled_until(self.instrument_id, self.timeframe, delivered_ms)
        return (start, end) if end > start else None

    def finalize_through(
        self, proven_ms: int, now_ms: int
    ) -> list[market.Candle]:
        """Finalizes bars that no undelivered trade can join any more.

        A bar settles at its end; a session's last bar at the end of its
        closing window (the closing print is stamped after the close).

        Args:
            proven_ms: Trades before this time have all been delivered.
            now_ms: Receipt time.

        Returns:
            Newly final bars.
        """
        touched = set()
        for start in self._starts:
            bucket = self._buckets[start]
            if not bucket.final and self._settles_at(start) <= proven_ms:
                bucket.final = True
                touched.add(start)
        return self._emit(touched, now_ms)

    def _emit(self, touched: set[int], now_ms: int) -> list[market.Candle]:
        """Builds the candles of touched buckets.

        Args:
            touched: Bucket starts.
            now_ms: Receipt time.

        Returns:
            Candles (buckets without a price or partial are skipped).
        """
        out = []
        for start in sorted(touched):
            bucket = self._buckets[start]
            if start == self._partial or bucket.open is None:
                continue
            assert bucket.close is not None
            high = bucket.high if bucket.high is not None else bucket.open
            low = bucket.low if bucket.low is not None else bucket.open
            out.append(
                session_bars.candle(
                    instrument_id=self.instrument_id,
                    timeframe=self.timeframe,
                    start_ms=start,
                    open_=bucket.open,
                    high=high,
                    low=low,
                    close=bucket.close,
                    base_volume=bucket.volume,
                    now_ms=now_ms,
                    source=self.source,
                    is_final=bucket.final,
                    feed_state=self.feed_state,
                    trade_count=bucket.count,
                    event_ts_ms=bucket.last_ts,
                    taker_buy_base_volume=None
                    if bucket.unknown
                    else bucket.buy,
                    taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
                )
            )
        return out

    def _evict(self) -> None:
        """Forgets buckets and ids behind the retention window."""
        if len(self._starts) <= self._retention:
            return
        cut = len(self._starts) - self._retention
        for start in self._starts[:cut]:
            del self._buckets[start]
        del self._starts[:cut]
        if len(self._seen) > 50 * self._retention:
            self._seen.clear()


def bars_from_trades(
    trades: Iterable[TapeTrade],
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    *,
    from_ms: int,
    to_ms: int,
    source: str,
    now_ms: int,
) -> list[market.Candle]:
    """Builds the bars of a complete trade interval ``[from, to)``.

    Bars ending by ``to_ms`` are final, except a session's last bar, which
    is final only once ``to_ms`` passed its closing window (read the window
    with ``closing_window_end``); a bar straddling ``to_ms`` is returned
    non-final, and a non-aligned ``from_ms`` makes its bar partial
    (omitted).

    Args:
        trades: Every trade of the interval, ascending.
        instrument_id: Instrument.
        timeframe: Seconds resolution.
        from_ms: Inclusive start of the complete interval.
        to_ms: Exclusive end of the complete interval.
        source: Provenance label.
        now_ms: Receipt time.

    Returns:
        Ascending bars.
    """
    builder = SecondsBarBuilder(
        instrument_id, timeframe, source=source, retention_bars=1_000_000
    )
    builder.start_segment(from_ms)
    out: dict[int, market.Candle] = {}
    inside = [t for t in trades if from_ms <= t.ts_ms < to_ms]
    for candle in builder.add(inside, now_ms):
        out[candle.start_ms] = candle
    for candle in builder.finalize_through(to_ms, now_ms):
        out[candle.start_ms] = candle
    return [out[s] for s in sorted(out)]


# Closing prints of minute bars ---------------------------------------------


def closing_prints(
    trades: Iterable[TapeTrade], close_ms: int
) -> tuple[TapeTrade, ...]:
    """Keeps the closing prints of one session's closing window.

    Args:
        trades: Trades read around the close.
        close_ms: Session close.

    Returns:
        Trades with condition ``6`` stamped in
        ``[close, close + CLOSING_WINDOW_MS)``, in time order (ties keep
        their input order).
    """
    return tuple(
        sorted(
            (
                trade
                for trade in trades
                if CLOSING_TRADE in trade.conditions
                and close_ms <= trade.ts_ms < close_ms + CLOSING_WINDOW_MS
            ),
            key=lambda trade: trade.ts_ms,
        )
    )


def closing_slot(
    instrument_id: instruments.InstrumentId, close_ms: int
) -> candle_rules.Span:
    """Returns the last minute of the session closing at ``close_ms``.

    Args:
        instrument_id: Instrument.
        close_ms: Session close.

    Returns:
        ``(start, close)`` of the session's last minute bar.
    """
    calendar = sessions.for_instrument(instrument_id)
    return (calendar.floor(timeframes.Timeframe.M1, close_ms - 1), close_ms)


def fold_closing_prints(
    bar: market.Candle | None,
    prints: Sequence[TapeTrade],
    *,
    instrument_id: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    start_ms: int,
    now_ms: int,
    source: str,
    is_final: bool,
) -> market.Candle | None:
    """Folds a session's closing prints into its last bar.

    The prints are stamped at or after the close, after every trade of the
    bar, so a price-updating print moves the close (and sets the open of a
    bar that had no trades); each print updates only what its conditions
    allow (``update_rule``). Quote turnover and buy volume are dropped when
    prints add volume (the prints carry neither).

    Args:
        bar: The session's last bar, or ``None`` when it had no trades.
        prints: Closing prints in time order (``closing_prints``).
        instrument_id: Instrument.
        timeframe: Bar resolution.
        start_ms: Bar start.
        now_ms: Receipt time.
        source: Provenance label of a bar created from prints alone.
        is_final: Finality of the result.

    Returns:
        The folded bar (``bar`` with the given finality when no print
        applies), or ``None`` when there is neither a bar nor a
        price-updating print.
    """
    open_ = bar.open if bar is not None else None
    high = bar.high if bar is not None else None
    low = bar.low if bar is not None else None
    close = bar.close if bar is not None else None
    count = bar.trade_count if bar is not None else 0
    event_ts = bar.event_ts_ms if bar is not None else None
    added = decimals.ZERO
    folded = False
    for trade in prints:
        rule = update_rule(trade.conditions, trade.tape)
        if rule.open_close:
            open_ = trade.price if open_ is None else open_
            close = trade.price
        if rule.high_low:
            high = trade.price if high is None else max(high, trade.price)
            low = trade.price if low is None else min(low, trade.price)
        if rule.volume:
            added += trade.size
            count = None if count is None else count + 1
        if rule.open_close or rule.high_low or rule.volume:
            folded = True
            event_ts = (
                trade.ts_ms if event_ts is None else max(event_ts, trade.ts_ms)
            )
    if open_ is None or close is None:
        return None
    if bar is not None and not folded:
        return dataclasses.replace(bar, is_final=is_final)
    kept = bar if bar is not None and not added else None
    return session_bars.candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start_ms=start_ms,
        open_=open_,
        high=open_ if high is None else high,
        low=open_ if low is None else low,
        close=close,
        base_volume=(decimals.ZERO if bar is None else bar.base_volume) + added,
        now_ms=now_ms,
        source=source if bar is None else bar.source,
        is_final=is_final,
        feed_state=quality.FeedState.LIVE if bar is None else bar.feed_state,
        quote_turnover=None if kept is None else kept.quote_turnover,
        trade_count=count,
        event_ts_ms=event_ts,
        taker_buy_base_volume=None
        if kept is None
        else kept.taker_buy_base_volume,
        taker_buy_method=None if kept is None else kept.taker_buy_method,
    )


def with_closing_prints(
    bars: Iterable[market.Candle],
    instrument_id: instruments.InstrumentId,
    *,
    prints: Mapping[int, Sequence[TapeTrade]],
    pending: Iterable[int],
    now_ms: int,
    settled_before_ms: int,
    source: str,
) -> list[market.Candle]:
    """Folds closing prints into the sessions' last minute bars.

    Args:
        bars: Minute bars of one instrument, without closing prints.
        instrument_id: Instrument.
        prints: Closing prints per session close (empty when the session
            printed none); each folds into that session's last minute.
        pending: Closes whose prints are not known yet: their sessions'
            last minute bars are not final.
        now_ms: Receipt time.
        settled_before_ms: A last minute bar created from prints alone is
            final when it ends by then.
        source: Provenance label of bars created from prints alone.

    Returns:
        Ascending minute bars.
    """
    by_start = {bar.start_ms: bar for bar in bars}
    for close_ms, trades in prints.items():
        start, _ = closing_slot(instrument_id, close_ms)
        bar = by_start.get(start)
        folded = fold_closing_prints(
            bar,
            trades,
            instrument_id=instrument_id,
            timeframe=timeframes.Timeframe.M1,
            start_ms=start,
            now_ms=now_ms,
            source=source,
            is_final=bar.is_final
            if bar is not None
            else close_ms <= settled_before_ms,
        )
        if folded is not None:
            by_start[start] = folded
    for close_ms in pending:
        start, _ = closing_slot(instrument_id, close_ms)
        bar = by_start.get(start)
        if bar is not None and bar.is_final:
            by_start[start] = dataclasses.replace(bar, is_final=False)
    return [by_start[start] for start in sorted(by_start)]
