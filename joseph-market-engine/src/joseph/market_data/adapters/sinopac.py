# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SinoPac Shioaji market data for TWSE and TPEx (optional, credential-gated).

``JOSEPH_TW_DATA_SOURCE=sinopac`` with a SinoPac Shioaji API key
(``JOSEPH_SINOPAC_API_KEY[_FILE]``, ``JOSEPH_SINOPAC_SECRET_KEY[_FILE]``;
market data needs no CA certificate) lets the TWSE and TPEx adapters
(``twse.TaiwanExchangeAdapter``) take bars, trades and books from Shioaji.
The SDK is the optional extra ``sinopac`` (``shioaji``, SinoPac's
proprietary binary wheels). Verified against the Shioaji 1.7.11 type stubs
(``shioaji/_core.pyi``) and https://sinotrade.github.io/ (2026-11-23):

* ``sj.Shioaji(simulation=False)``; ``api.login(api_key, secret_key,
  subscribe_trade=...)``; ``api.usage()`` -> ``UsageOut(connections,
  bytes, limit_bytes, remaining_bytes)``; ``api.contracts.stocks.TSE[code]``
  and ``.OTC[code]`` (TWSE and TPEx contract groups).
* ``api.kbars(contract, start="YYYY-MM-DD", end=...)`` -> ``KBars`` columns
  ``ts, Open, High, Low, Close, Volume, Amount``; at most 30 days per query;
  stock history from 2020-03-02 (tutor/market_data/historical). The
  documented 2330 example has 270 bars per day labeled 09:01 to 13:30, so a
  kbar is labeled by the *end* of its minute; its ``Amount`` is about
  ``Close * Volume * 1000``, so ``Volume`` is in board lots.
* ``api.ticks(contract, date="YYYY-MM-DD")`` (``query_type`` defaults to
  ``AllDay``) -> ``Ticks`` columns ``ts, close, volume, bid_price,
  bid_volume, ask_price, ask_volume, tick_type``.
* ``ts`` values are epoch nanoseconds of *Taipei wall-clock* time: the
  documented ``1779094808306075000`` is printed as 2026-05-18 09:00:08.306
  (inferred from the examples; the page states no time zone).
* ``api.subscribe(contract, quote_type="tick"|"bid_ask", intraday_odd=False,
  version=sj.QuoteVersion.v1)`` (``api.quote.subscribe`` is a deprecated
  proxy in 1.7.11); callbacks ``set_on_tick_stk_v1_callback`` and
  ``set_on_bidask_stk_v1_callback`` (the stubs pass the quote alone, the
  docs ``(exchange, quote)``: the last argument is used) and
  ``set_event_callback(cb(resp_code, event_code, info, event))``.
  ``TickSTKv1``: ``datetime``, ``close`` (str), ``volume`` and
  ``total_volume`` (board lots for common stock), ``tick_type``,
  ``price_chg``, ``open``/``high``/``low``, ``simtrade`` (試撮, trial
  matching, not a trade), ``intraday_odd``; ``BidAskSTKv1``: five-level
  ``bid_price``/``ask_price`` (str) and ``bid_volume``/``ask_volume``
  (lots).
* ``tick_type``: Python docs "內外盤別{1: 外盤, 2: 內盤, 0: 無法判定}"
  (English "{1: ask, 2: bid, 0: unknown}"); the C# docs print 1 and 2 the
  other way round. Neither says the exchange reports it, and code 0
  ("cannot be determined") marks it as a classification, so buy volume is
  labeled ``classified``, never ``taker``. The documented samples fit the
  Python mapping when their bid/ask is the book after the trade.
* Limits (tutor/limit): 50 market-data queries (``ticks``, ``kbars``,
  ``snapshots``, ...) per 10 s; during trading hours at most 10 ``ticks``
  and 270 ``kbars`` queries; 200 subscriptions; 5 connections per person
  and 1,000 logins a day; a daily data allowance of 500 MB (no API trading
  in 30 days), 2 GB or 10 GB by the 30-day API trading value, reset at
  08:00 Taipei. Over the allowance ``ticks``/``kbars``/``snapshots`` return
  empty values (1.7.11 also has ``MarketDataQueryUsageLimitError``); over a
  rate limit the service is suspended for a minute.

``SinopacSource`` owns the one Shioaji login shared by the TWSE and TPEx
adapters: pacing, the trading-hours query budgets, failure states (quota
until the 08:00 reset, login retries, outages) and the live tick and
bid/ask streams (``Feed`` per code). Shioaji calls block, so they run in a
worker thread one at a time; SDK callbacks hop onto the event loop.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import dataclasses
import datetime
import decimal
import functools
import importlib
import logging
import zoneinfo
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import http
from joseph.market_data.domain import books
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import session_bars

_LOG = logging.getLogger(__name__)

SOURCE = "sinopac"
"""Provider prefix of every Shioaji-sourced value."""
KBAR_SOURCE = "sinopac.kbars"
TICK_SOURCE = "sinopac.ticks"
BOOK_SOURCE = "sinopac.ws"
DOCS_URL = "https://sinotrade.github.io/"
LOT = decimal.Decimal(1000)
"""Shares per board lot (Shioaji volumes of common stock are lots)."""
HISTORY_FLOOR = datetime.date(2020, 3, 2)
"""First day of Shioaji stock history (tutor/market_data/historical)."""
KBARS_MAX_DAYS = 30
"""Longest ``kbars`` query range."""
INTRADAY_TICKS_LIMIT = 10
INTRADAY_KBARS_LIMIT = 270
MAX_SUBSCRIPTIONS = 200
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_TPE_OFFSET_MS = 8 * 3_600_000
_M1 = timeframes.Timeframe.M1
KBAR_SETTLE_MS = 5_000
"""A kbar ending this long before its query is complete."""
_BEFORE_OPEN_MS = 30 * 60_000
DAY_SETTLE_MS = 15 * 60_000
"""History of a session is read as final this long after its close; the
trading-hours query budget applies from 30 minutes before the open until
then."""
_BOOK_GRACE_MS = 2_000
"""A bid/ask update proves tick delivery up to this long before it."""
_RATE_PER_S = 4.0
_BURST = 5
"""Pacing below the 50 market-data queries per 10 s."""
_LOGIN_RETRY_MS = 10 * 60_000
_OUTAGE_RETRY_MS = 60_000
_QUOTA_RESET = datetime.time(8, 0)
WATCH_TTL_MS = 30 * 60_000
"""A streamed code without listeners is dropped after this idle time."""
_DOWN_EVENTS = frozenset({1, 2})
_UP_EVENTS = frozenset({0, 13})
_RECONNECTING = 12

Runner = Callable[[Callable[[], Any]], Awaitable[Any]]
"""Runs one blocking SDK call (default: a worker thread)."""


async def _thread_runner(fn: Callable[[], Any]) -> Any:
    """Runs a blocking SDK call in a worker thread.

    Args:
        fn: Call.

    Returns:
        Its result.
    """
    return await asyncio.to_thread(fn)


@dataclasses.dataclass(frozen=True)
class SinopacConfig:
    """Shioaji market-data settings (credentials are redacted).

    Attributes:
        api_key: Shioaji API key, ``None`` when not configured.
        secret_key: Shioaji secret key, ``None`` when not configured.
        max_symbols: Codes streamed at once (two subscriptions each).
        intraday_ticks_calls: ``ticks`` queries spent per day in trading
            hours (Shioaji allows 10).
        intraday_kbars_calls: ``kbars`` queries spent per day in trading
            hours (Shioaji allows 270).
        seconds_history_days: Sessions of tick history (newest first) used
            for seconds bars and trades.
    """

    api_key: str | None = None
    secret_key: str | None = None
    max_symbols: int = 60
    intraday_ticks_calls: int = 8
    intraday_kbars_calls: int = 200
    seconds_history_days: int = 5

    def __repr__(self) -> str:
        """Returns a representation without the credentials."""
        return (
            f"SinopacConfig(api_key={'***' if self.api_key else None}, "
            f"secret_key={'***' if self.secret_key else None}, "
            f"max_symbols={self.max_symbols}, "
            f"intraday_ticks_calls={self.intraday_ticks_calls}, "
            f"intraday_kbars_calls={self.intraday_kbars_calls}, "
            f"seconds_history_days={self.seconds_history_days})"
        )

    @property
    def configured(self) -> bool:
        """Returns True when both credentials are set."""
        return bool(self.api_key and self.secret_key)


class SjDataClient(Protocol):
    """The ``sj.Shioaji`` surface used for market data."""

    def login(
        self, api_key: str, secret_key: str, subscribe_trade: bool = True
    ) -> list[Any]:
        """Logs in.

        Args:
            api_key: API key.
            secret_key: Secret key.
            subscribe_trade: Subscribe order/deal callbacks.

        Returns:
            Accounts.
        """
        ...

    def logout(self) -> object:
        """Logs out.

        Returns:
            SDK result.
        """
        ...

    def usage(self) -> Any:
        """Returns ``UsageOut`` (bytes used and left today)."""
        ...

    @property
    def contracts(self) -> Any:
        """Returns the contract tree (``contracts.stocks.TSE[code]``)."""
        ...

    def ticks(self, contract: Any, date: str) -> Any:
        """Reads a day's ticks (``AllDay``).

        Args:
            contract: Contract.
            date: ``YYYY-MM-DD``.

        Returns:
            ``Ticks`` columns.
        """
        ...

    def kbars(self, contract: Any, start: str, end: str) -> Any:
        """Reads one-minute kbars of a date range.

        Args:
            contract: Contract.
            start: First date ``YYYY-MM-DD``.
            end: Last date ``YYYY-MM-DD``.

        Returns:
            ``KBars`` columns.
        """
        ...

    def subscribe(
        self,
        contract: Any,
        quote_type: str,
        intraday_odd: bool = False,
        version: Any = None,
    ) -> None:
        """Subscribes a quote stream.

        Args:
            contract: Contract.
            quote_type: ``tick`` or ``bid_ask``.
            intraday_odd: Odd-lot stream instead of board lots.
            version: ``sj.QuoteVersion.v1``.
        """
        ...

    def unsubscribe(
        self,
        contract: Any,
        quote_type: str,
        intraday_odd: bool = False,
        version: Any = None,
    ) -> None:
        """Unsubscribes a quote stream.

        Args:
            contract: Contract.
            quote_type: ``tick`` or ``bid_ask``.
            intraday_odd: Odd-lot stream instead of board lots.
            version: ``sj.QuoteVersion.v1``.
        """
        ...

    def set_on_tick_stk_v1_callback(
        self, callback: Callable[..., None]
    ) -> None:
        """Sets the stock tick callback.

        Args:
            callback: Receives the tick as its last argument.
        """
        ...

    def set_on_bidask_stk_v1_callback(
        self, callback: Callable[..., None]
    ) -> None:
        """Sets the stock bid/ask callback.

        Args:
            callback: Receives the bid/ask as its last argument.
        """
        ...

    def set_event_callback(
        self, callback: Callable[[int, int, str, str], None]
    ) -> None:
        """Sets the session event callback.

        Args:
            callback: ``cb(resp_code, event_code, info, event)``.
        """
        ...


class SjDataFactory(Protocol):
    """Creates Shioaji clients (fakes implement it in tests)."""

    def client(self) -> SjDataClient:
        """Creates a production ``sj.Shioaji`` client.

        Returns:
            Client.
        """
        ...

    def quote_version(self) -> Any:
        """Returns ``sj.QuoteVersion.v1``."""
        ...


class ShioajiDataFactory:
    """``SjDataFactory`` backed by the real ``shioaji`` package."""

    def __init__(self) -> None:
        """Imports ``shioaji``.

        Raises:
            errors.VenueUnavailableError: When the ``sinopac`` extra is not
                installed.
        """
        try:
            self._sj: Any = importlib.import_module("shioaji")
        except ImportError as err:
            raise errors.VenueUnavailableError(
                "shioaji is not installed (build with the 'sinopac' extra)"
            ) from err

    def client(self) -> SjDataClient:
        """Creates ``sj.Shioaji(simulation=False)``.

        Returns:
            Client.
        """
        client: SjDataClient = self._sj.Shioaji(simulation=False)
        return client

    def quote_version(self) -> Any:
        """Returns ``sj.QuoteVersion.v1``."""
        return self._sj.QuoteVersion.v1


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def wall_ms(ts_ns: int) -> int:
    """Converts a Shioaji ``ts`` (Taipei wall clock as epoch ns) to UTC.

    Args:
        ts_ns: Epoch nanoseconds of Taipei local time.

    Returns:
        UTC epoch milliseconds.
    """
    return int(ts_ns) // 1_000_000 - _TPE_OFFSET_MS


def local_ms(moment: datetime.datetime) -> int:
    """Converts a stream ``datetime`` (naive Taipei time) to UTC.

    Args:
        moment: Naive local time, or an aware time.

    Returns:
        UTC epoch milliseconds.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_TPE)
    return int(moment.timestamp() * 1000)


def taipei_day(ts_ms: int) -> datetime.date:
    """Returns the Taipei date of an instant.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _TPE).date()


def tick_side(tick_type: object) -> market.AggressorSide:
    """Maps Shioaji ``tick_type`` to a classified side.

    Args:
        tick_type: ``1`` 外盤 (at the ask: buy), ``2`` 內盤 (at the bid:
            sell), ``0`` undetermined.

    Returns:
        Side (``UNKNOWN`` for 0 and anything else).
    """
    try:
        code = int(str(tick_type))
    except ValueError:
        return market.AggressorSide.UNKNOWN
    if code == 1:
        return market.AggressorSide.BUY
    if code == 2:
        return market.AggressorSide.SELL
    return market.AggressorSide.UNKNOWN


def error_kind(err: BaseException) -> str:
    """Classifies a Shioaji failure by its exception class names.

    Args:
        err: Exception raised by the SDK (or a fake).

    Returns:
        ``quota`` (daily data allowance used up), ``auth`` (login or
        permission) or ``outage`` (anything else).
    """
    names = {cls.__name__ for cls in type(err).__mro__}
    if "MarketDataQueryUsageLimitError" in names:
        return "quota"
    if names & {
        "AuthError",
        "TokenError",
        "AccountNotSignError",
        "ShioajiPermissionError",
    }:
        return "auth"
    return "outage"


def quota_reset_ms(now_ms: int) -> int:
    """Returns the next 08:00 Taipei, when the data allowance resets.

    Args:
        now_ms: Current time.

    Returns:
        UTC epoch milliseconds.
    """
    local = datetime.datetime.fromtimestamp(now_ms / 1000, _TPE)
    reset = datetime.datetime.combine(local.date(), _QUOTA_RESET, _TPE)
    if reset <= local:
        reset += datetime.timedelta(days=1)
    return int(reset.timestamp() * 1000)


def in_trading_hours(now_ms: int) -> bool:
    """Checks whether Shioaji's trading-hours query limits apply.

    Args:
        now_ms: Current time.

    Returns:
        True from 30 minutes before an XTAI open until 15 minutes after
        its close (a conservative reading of "盤中").
    """
    session = sessions.XTAI.session(taipei_day(now_ms))
    return session is not None and (
        session.open_ms - _BEFORE_OPEN_MS
        <= now_ms
        < session.close_ms + DAY_SETTLE_MS
    )


def trade_id(code: str, day: datetime.date, cumulative_lots: int) -> str:
    """Builds a trade id from the day volume after the trade.

    The cumulative day volume rises with every trade, so it identifies a
    trade within a day in both the tick history (summed) and the stream
    (``total_volume``).

    Args:
        code: Security code.
        day: Trading date.
        cumulative_lots: Day volume through this trade, in lots.

    Returns:
        Id such as ``2330-20261123-1532``.
    """
    return f"{code}-{day:%Y%m%d}-{cumulative_lots}"


def _column(rows: Any, name: str) -> list[Any]:
    """Reads one column of a columnar SDK result.

    Args:
        rows: ``Ticks`` or ``KBars`` (attributes or mapping).
        name: Column.

    Returns:
        Values (empty when absent).
    """
    value = (
        rows.get(name)
        if isinstance(rows, Mapping)
        else getattr(rows, name, None)
    )
    return list(value or [])


def _session_of(ts_ms: int) -> sessions.Session | None:
    """Returns the XTAI session holding an instant (with closing grace).

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Session, or ``None`` outside ``[open, close + grace)``.
    """
    session = sessions.XTAI.session(taipei_day(ts_ms))
    if session is None:
        return None
    if (
        session.open_ms
        <= ts_ms
        < session.close_ms + (sessions.XTAI.close_grace_ms)
    ):
        return session
    return None


def settled_ms(start_ms: int, timeframe: timeframes.Timeframe) -> int:
    """Returns when no further trade can belong to an XTAI bar.

    Args:
        start_ms: Bar start.
        timeframe: Intraday resolution.

    Returns:
        The bar end, plus the closing grace for a session's last bar.
    """
    calendar = sessions.XTAI
    end = calendar.end_of(timeframe, start_ms)
    if not calendar.is_open(end):
        return end + calendar.close_grace_ms
    return end


def parse_kbars(
    rows: Any,
    instrument_id: instruments.InstrumentId,
    *,
    fetched_ms: int,
) -> list[market.Candle]:
    """Converts ``KBars`` to final one-minute session bars.

    A kbar labeled ``t`` holds the minute ``[t - 1m, t)``; the 13:30 label
    holds the closing auction. Bars outside XTAI sessions and bars that had
    not ended by the query (minus a settle margin) are dropped.

    Args:
        rows: ``KBars`` columns.
        instrument_id: Instrument.
        fetched_ms: When the query returned.

    Returns:
        Ascending final candles (volume in shares, turnover in TWD).
    """
    calendar = sessions.XTAI
    labels = _column(rows, "ts")
    columns = [
        _column(rows, name)
        for name in ("Open", "High", "Low", "Close", "Volume", "Amount")
    ]
    out: dict[int, market.Candle] = {}
    for index, label in enumerate(labels):
        try:
            end = wall_ms(int(label))
            values = [column[index] for column in columns]
            open_, high, low, close = (
                decimals.to_decimal(value) for value in values[:4]
            )
            volume = decimals.to_decimal(values[4]) * LOT
            amount = decimals.to_decimal(values[5])
        except (ValueError, IndexError, TypeError, decimal.InvalidOperation):
            continue
        start = end - _M1.duration_ms
        session = _session_of(start)
        if (
            session is None
            or start >= session.close_ms
            or not calendar.is_aligned(_M1, start)
            or settled_ms(start, _M1) > fetched_ms - KBAR_SETTLE_MS
        ):
            continue
        out[start] = session_bars.candle(
            instrument_id=instrument_id,
            timeframe=_M1,
            start_ms=start,
            open_=open_,
            high=high,
            low=low,
            close=close,
            base_volume=volume,
            now_ms=fetched_ms,
            source=KBAR_SOURCE,
            is_final=True,
            quote_turnover=amount,
            event_ts_ms=end,
        )
    return [out[start] for start in sorted(out)]


@dataclasses.dataclass(frozen=True, slots=True)
class DayTicks:
    """One session's regular board-lot trades from the tick history.

    Attributes:
        trades: Trades inside ``[open, close + grace)``, ascending.
        cumulative: Day volume in lots through each trade.
        fetched_ms: When the query returned.
    """

    trades: tuple[market.Trade, ...]
    cumulative: tuple[int, ...]
    fetched_ms: int


def parse_ticks(
    rows: Any,
    instrument_id: instruments.InstrumentId,
    day: datetime.date,
    *,
    fetched_ms: int,
) -> DayTicks:
    """Converts a day's ``Ticks`` to trades.

    Volumes are lots (converted to shares); ``tick_type`` gives the
    classified side. Ticks outside the session (the 14:30 after-hours
    fixed-price match) count towards the cumulative volume but are not
    returned.

    Args:
        rows: ``Ticks`` columns (an ``AllDay`` query).
        instrument_id: Instrument.
        day: Trading date.
        fetched_ms: Receipt time.

    Returns:
        Trades and cumulative volumes.
    """
    code = instrument_id.venue_market_id
    stamps = _column(rows, "ts")
    closes = _column(rows, "close")
    volumes = _column(rows, "volume")
    types = _column(rows, "tick_type")
    trades: list[market.Trade] = []
    cumulative: list[int] = []
    total = 0
    for index, stamp in enumerate(stamps):
        try:
            ts = wall_ms(int(stamp))
            lots = int(volumes[index])
            price = decimals.to_decimal(closes[index])
        except (ValueError, IndexError, TypeError, decimal.InvalidOperation):
            continue
        if lots <= 0:
            continue
        total += lots
        if _session_of(ts) is None or taipei_day(ts) != day:
            continue
        trades.append(
            _trade(
                instrument_id,
                trade_id=trade_id(code, day, total),
                ts_ms=ts,
                received_ms=fetched_ms,
                price=price,
                lots=lots,
                side=tick_side(types[index] if index < len(types) else 0),
            )
        )
        cumulative.append(total)
    return DayTicks(tuple(trades), tuple(cumulative), fetched_ms)


def _trade(
    instrument_id: instruments.InstrumentId,
    *,
    trade_id: str,
    ts_ms: int,
    received_ms: int,
    price: decimal.Decimal,
    lots: int,
    side: market.AggressorSide,
) -> market.Trade:
    """Builds a board-lot trade.

    Args:
        instrument_id: Instrument.
        trade_id: Trade id (``trade_id``).
        ts_ms: Trade time.
        received_ms: Receipt time.
        price: Price.
        lots: Board lots.
        side: Classified side from ``tick_type``.

    Returns:
        Trade (``is_aggregated``: one tick may hold several fills).
    """
    quantity = decimal.Decimal(lots) * LOT
    return market.Trade(
        instrument_id=instrument_id,
        trade_id=trade_id,
        venue_event_ts_ms=ts_ms,
        received_at_ms=received_ms,
        price=price,
        base_quantity=quantity,
        aggressor_side=side,
        is_aggregated=True,
        quote_quantity=price * quantity,
    )


def parse_book(
    bidask: Any, instrument_id: instruments.InstrumentId, received_ms: int
) -> market.OrderBook:
    """Converts a ``BidAskSTKv1`` to a five-level book in shares.

    Args:
        bidask: Bid/ask update.
        instrument_id: Instrument.
        received_ms: Receipt time.

    Returns:
        Order book (empty and market-order levels skipped).
    """

    def levels(
        prices: Iterable[Any], sizes: Iterable[Any]
    ) -> list[tuple[decimal.Decimal, decimal.Decimal]]:
        """Parses one side.

        Args:
            prices: Level prices (str).
            sizes: Level sizes (lots).

        Returns:
            ``(price, shares)`` levels with a positive price and size.
        """
        out = []
        for price_text, size in zip(prices, sizes, strict=False):
            try:
                price = decimals.to_decimal(str(price_text))
                lots = decimals.to_decimal(size)
            except (ValueError, decimal.InvalidOperation):
                continue
            if price > 0 and lots > 0:
                out.append((price, lots * LOT))
        return out

    return books.snapshot_book(
        instrument_id,
        levels(bidask.bid_price or [], bidask.bid_volume or []),
        levels(bidask.ask_price or [], bidask.ask_volume or []),
        ts_ms=local_ms(bidask.datetime),
        received_ms=received_ms,
        source=BOOK_SOURCE,
    )


def minute_bars(
    trades: Sequence[market.Trade],
    instrument_id: instruments.InstrumentId,
    *,
    from_ms: int,
    proven_ms: int,
    now_ms: int,
    final_only: bool = False,
) -> list[market.Candle]:
    """Folds trades of one complete interval into one-minute bars.

    ``trades`` must hold every trade from ``from_ms`` until ``proven_ms``
    (and may hold later ones). The minute holding ``from_ms`` is partial
    (omitted) unless ``from_ms`` is a minute start; a bar is final once
    ``proven_ms`` passed its settle time (the closing grace for a
    session's last minute). Minutes without trades have no bar. Buy
    volume is the classified ``tick_type`` side and is ``None`` when any
    trade of the bar is undetermined.

    Args:
        trades: Ascending trades (earlier ones and trades outside sessions
            are ignored).
        instrument_id: Instrument.
        from_ms: Start of the complete interval.
        proven_ms: Every trade before this time is present.
        now_ms: Receipt time stamped on the bars.
        final_only: Drop bars that are not final.

    Returns:
        Ascending candles.
    """
    calendar = sessions.XTAI
    groups: dict[int, list[market.Trade]] = {}
    for trade in trades:
        ts = trade.venue_event_ts_ms
        if ts < from_ms or _session_of(ts) is None:
            continue
        groups.setdefault(calendar.floor(_M1, ts), []).append(trade)
    out = []
    for start, group in sorted(groups.items()):
        if start < from_ms:
            continue
        final = settled_ms(start, _M1) <= proven_ms
        if final_only and not final:
            continue
        unknown = any(
            t.aggressor_side is market.AggressorSide.UNKNOWN for t in group
        )
        buy = sum(
            (
                t.base_quantity
                for t in group
                if t.aggressor_side is market.AggressorSide.BUY
            ),
            decimals.ZERO,
        )
        prices = [t.price for t in group]
        out.append(
            session_bars.candle(
                instrument_id=instrument_id,
                timeframe=_M1,
                start_ms=start,
                open_=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                base_volume=sum(
                    (t.base_quantity for t in group), decimals.ZERO
                ),
                now_ms=now_ms,
                source=TICK_SOURCE,
                is_final=final,
                quote_turnover=sum(
                    (t.price * t.base_quantity for t in group), decimals.ZERO
                ),
                event_ts_ms=group[-1].venue_event_ts_ms,
                taker_buy_base_volume=None if unknown else buy,
                taker_buy_method=market.BuyVolumeMethod.CLASSIFIED,
            )
        )
    return out


def whole_minutes(
    span: candle_rules.Span, *, grace: bool = True
) -> candle_rules.Span | None:
    """Shrinks a span to the XTAI minutes it settles completely.

    Args:
        span: ``[from, to)`` data-time span.
        grace: Whether a session's last minute also needs the closing
            grace (trade-built bars) or only its end (snapshot bars).

    Returns:
        ``[first minute start, end of the last settled minute)``, or
        ``None`` when no minute lies wholly inside.
    """
    calendar = sessions.XTAI
    low, high = span
    try:
        first = calendar.ceil(_M1, low)
        last = calendar.floor(_M1, high)
    except ValueError:
        return None
    done = settled_ms(last, _M1) if grace else calendar.end_of(_M1, last)
    if done > high:
        try:
            last = calendar.shift(_M1, last, -1)
        except ValueError:
            return None
    end = calendar.end_of(_M1, last)
    if last < first or end <= first:
        return None
    return first, end


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Event:
    """A change of a ``Feed`` delivered to its listeners.

    Attributes:
        kind: ``trade``, ``proof`` (delivery proven up to ``proven_ms``),
            ``segment`` (a trade was missed: a new segment starts),
            ``quote``, ``book`` or ``down`` (the source failed).
        trade: The trade of a ``trade`` event.
        proven_ms: The proof time of a ``proof`` event.
        reason: Why the source failed (``down``).
    """

    kind: str
    trade: market.Trade | None = None
    proven_ms: int | None = None
    reason: str | None = None


@dataclasses.dataclass(slots=True)
class Segment:
    """A stretch of the tick stream in which no trade was missed.

    Attributes:
        from_ms: Every trade from this time on is held (the session open
            when the day volume before the first trade was zero, else just
            after the first trade, whose minute is then partial: trades
            sharing its millisecond may have been missed).
        proven_ms: Every trade before this time is held.
        base_lots: Day volume before the segment's first trade.
        first: Index of the segment's first trade in ``Feed.trades``.
    """

    from_ms: int
    proven_ms: int
    base_lots: int
    first: int


@dataclasses.dataclass(frozen=True, slots=True)
class LiveQuote:
    """Latest day statistics of a code from the tick stream.

    Attributes:
        ts_ms: Tick time.
        open: Day open.
        high: Day high.
        low: Day low.
        close: Last trade price.
        total_volume: Day volume in shares (board lots).
        change: Change from the reference price, when published.
    """

    ts_ms: int
    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    total_volume: decimal.Decimal
    change: decimal.Decimal | None


def _optional_decimal(value: object) -> decimal.Decimal | None:
    """Parses an optional SDK number.

    Args:
        value: String, number or ``None``.

    Returns:
        Decimal, or ``None`` for missing or malformed input.
    """
    if value is None:
        return None
    try:
        return decimals.to_decimal(str(value))
    except (ValueError, decimal.InvalidOperation):
        return None


def _decimal_or(value: object, default: decimal.Decimal) -> decimal.Decimal:
    """Parses an SDK number with a fallback.

    Args:
        value: String, number or ``None``.
        default: Value for missing or malformed input.

    Returns:
        Decimal.
    """
    parsed = _optional_decimal(value)
    return default if parsed is None else parsed


class Feed:
    """Live Shioaji state of one code for the current trading day.

    The stream's cumulative day volume (``total_volume``) proves continuity:
    a tick whose volume before it equals the previous tick's day volume
    follows it without a missed trade, even across a reconnect. A jump
    starts a new ``Segment``. Bid/ask updates prove delivery up to
    ``_BOOK_GRACE_MS`` before their time, except after a disconnect until a
    tick proves continuity again.

    Attributes:
        code: Security code.
        exchange: Shioaji contract group (``TSE`` or ``OTC``).
        instrument_id: Instrument.
        day: Trading date of the state.
        trades: Today's trades, ascending.
        times: Their times (for bisection).
        cumulative: Day volume in lots through each trade.
        segments: Continuity segments of today, ascending.
        quote: Latest day statistics.
        book: Latest five-level book.
        listeners: Queues of the streams reading this feed.
        used_ms: Last time a request used the feed.
        suspect: A disconnect happened since the last tick.
        seeded: The tick history of today was merged (or attempted).
    """

    def __init__(
        self, code: str, exchange: str, instrument_id: instruments.InstrumentId
    ) -> None:
        """Initializes an empty feed.

        Args:
            code: Security code.
            exchange: ``TSE`` or ``OTC``.
            instrument_id: Instrument.
        """
        self.code = code
        self.exchange = exchange
        self.instrument_id = instrument_id
        self.day: datetime.date | None = None
        self.trades: list[market.Trade] = []
        self.times: list[int] = []
        self.cumulative: list[int] = []
        self.segments: list[Segment] = []
        self.quote: LiveQuote | None = None
        self.book: market.OrderBook | None = None
        self.listeners: list[asyncio.Queue[Event]] = []
        self.used_ms = 0
        self.suspect = False
        self.seeded = False

    def _new_day(self, day: datetime.date) -> None:
        """Forgets the previous day.

        Args:
            day: New trading date.
        """
        self.day = day
        self.trades.clear()
        self.times.clear()
        self.cumulative.clear()
        self.segments.clear()
        self.quote = None
        self.book = None
        self.suspect = False
        self.seeded = False

    def apply_tick(self, tick: Any, received_ms: int) -> list[Event]:
        """Applies one ``TickSTKv1``.

        Args:
            tick: Tick (trial matches and odd lots are ignored).
            received_ms: Receipt time.

        Returns:
            Events for listeners.
        """
        if bool(getattr(tick, "simtrade", False)) or bool(
            getattr(tick, "intraday_odd", False)
        ):
            return []
        try:
            ts = local_ms(tick.datetime)
            total = int(tick.total_volume)
            lots = int(tick.volume)
            price = decimals.to_decimal(str(tick.close))
        except (
            ValueError,
            TypeError,
            AttributeError,
            decimal.InvalidOperation,
        ):
            return []
        day = taipei_day(ts)
        if self.day != day:
            self._new_day(day)
        self.quote = LiveQuote(
            ts_ms=ts,
            open=_decimal_or(getattr(tick, "open", None), price),
            high=_decimal_or(getattr(tick, "high", None), price),
            low=_decimal_or(getattr(tick, "low", None), price),
            close=price,
            total_volume=decimal.Decimal(total) * LOT,
            change=_optional_decimal(getattr(tick, "price_chg", None)),
        )
        events = [Event("quote")]
        session = _session_of(ts)
        last = self.cumulative[-1] if self.cumulative else None
        if session is None or lots <= 0 or (last is not None and total <= last):
            return events
        base = total - lots
        if not self.segments or base != last:
            self.segments.append(
                Segment(
                    from_ms=session.open_ms if base == 0 else ts + 1,
                    proven_ms=ts,
                    base_lots=base,
                    first=len(self.trades),
                )
            )
            events.append(Event("segment"))
        self.suspect = False
        trade = _trade(
            self.instrument_id,
            trade_id=trade_id(self.code, day, total),
            ts_ms=ts,
            received_ms=received_ms,
            price=price,
            lots=lots,
            side=tick_side(getattr(tick, "tick_type", 0)),
        )
        self.trades.append(trade)
        self.times.append(ts)
        self.cumulative.append(total)
        segment = self.segments[-1]
        segment.proven_ms = max(segment.proven_ms, ts)
        events.append(Event("trade", trade=trade))
        events.append(Event("proof", proven_ms=segment.proven_ms))
        return events

    def apply_book(self, bidask: Any, received_ms: int) -> list[Event]:
        """Applies one ``BidAskSTKv1``.

        Args:
            bidask: Bid/ask update (trial matches and odd lots ignored).
            received_ms: Receipt time.

        Returns:
            Events for listeners.
        """
        if bool(getattr(bidask, "simtrade", False)) or bool(
            getattr(bidask, "intraday_odd", False)
        ):
            return []
        try:
            book = parse_book(bidask, self.instrument_id, received_ms)
        except (ValueError, TypeError, AttributeError):
            return []
        day = taipei_day(book.snapshot_ts_ms)
        if self.day != day:
            self._new_day(day)
        self.book = book
        events = [Event("book")]
        if self.segments and not self.suspect:
            segment = self.segments[-1]
            proof = book.snapshot_ts_ms - _BOOK_GRACE_MS
            if proof > segment.proven_ms:
                segment.proven_ms = proof
                events.append(Event("proof", proven_ms=proof))
        return events

    def segment_trades(self, index: int) -> list[market.Trade]:
        """Returns the trades of one segment.

        Args:
            index: Segment index.

        Returns:
            Ascending trades.
        """
        first = self.segments[index].first
        stop = (
            self.segments[index + 1].first
            if index + 1 < len(self.segments)
            else len(self.trades)
        )
        return self.trades[first:stop]

    def coverage(self) -> list[candle_rules.Span]:
        """Returns today's spans in which every trade is held.

        Returns:
            ``(from, proven)`` per segment.
        """
        return [
            (segment.from_ms, segment.proven_ms)
            for segment in self.segments
            if segment.proven_ms > segment.from_ms
        ]

    def minute_bars(self, now_ms: int) -> list[market.Candle]:
        """Builds today's one-minute bars from the held trades.

        Older segments contribute their final bars only (their open bar
        lost trades to the break); the newest also its live bar.

        Args:
            now_ms: Receipt time.

        Returns:
            Ascending candles.
        """
        out: list[market.Candle] = []
        for index, segment in enumerate(self.segments):
            out.extend(
                minute_bars(
                    self.segment_trades(index),
                    self.instrument_id,
                    from_ms=segment.from_ms,
                    proven_ms=segment.proven_ms,
                    now_ms=now_ms,
                    final_only=index < len(self.segments) - 1,
                )
            )
        return out

    def minute_bar(self, start_ms: int, now_ms: int) -> market.Candle | None:
        """Builds one minute of the newest segment.

        Args:
            start_ms: Minute start.
            now_ms: Receipt time.

        Returns:
            Candle, or ``None`` without trades or for a partial minute.
        """
        if not self.segments:
            return None
        segment = self.segments[-1]
        lo = bisect.bisect_left(self.times, start_ms, lo=segment.first)
        hi = bisect.bisect_left(self.times, settled_ms(start_ms, _M1), lo=lo)
        bars = minute_bars(
            self.trades[lo:hi],
            self.instrument_id,
            from_ms=segment.from_ms,
            proven_ms=segment.proven_ms,
            now_ms=now_ms,
        )
        return bars[0] if bars else None

    def seed(self, history: DayTicks) -> bool:
        """Extends the first segment back to the open with the tick history.

        The history joins only when it holds the trade that brought the day
        volume to the segment's base, so the union provably misses nothing.

        Args:
            history: Today's tick history.

        Returns:
            True when today is now held from the session open.
        """
        self.seeded = True
        if not self.segments or self.day is None:
            return False
        first = self.segments[0]
        session = sessions.XTAI.session(self.day)
        if first.base_lots == 0:
            return True
        if session is None or first.base_lots not in history.cumulative:
            return False
        cut = history.cumulative.index(first.base_lots) + 1
        prefix = list(history.trades[:cut])
        self.trades[:0] = prefix
        self.times[:0] = [t.venue_event_ts_ms for t in prefix]
        self.cumulative[:0] = list(history.cumulative[:cut])
        for segment in self.segments[1:]:
            segment.first += len(prefix)
        first.from_ms = session.open_ms
        first.base_lots = 0
        return True


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class SinopacSource:
    """One Shioaji login shared by the TWSE and TPEx adapters.

    Attributes:
        config: Settings.
        calls: SDK operations made (for tests and diagnostics).
    """

    def __init__(
        self,
        config: SinopacConfig,
        clock: clock_lib.Clock,
        *,
        factory: Callable[[], SjDataFactory] = ShioajiDataFactory,
        runner: Runner = _thread_runner,
        limiter: http.RateLimiter | None = None,
    ) -> None:
        """Initializes a source that logs in on first use.

        Args:
            config: Settings (credentials, budgets).
            clock: Time source.
            factory: Creates the SDK factory (imports ``shioaji``).
            runner: Runs blocking SDK calls.
            limiter: Query pacing (defaults below 50 per 10 s).
        """
        self.config = config
        self._clock = clock
        self._factory_fn = factory
        self._factory: SjDataFactory | None = None
        self._runner = runner
        self._limiter = limiter or http.RateLimiter(_RATE_PER_S, _BURST)
        self._api: SjDataClient | None = None
        self._login_lock = asyncio.Lock()
        self._sdk_lock = asyncio.Lock()
        self._permanent: str | None = (
            None
            if config.configured
            else "JOSEPH_SINOPAC_API_KEY and JOSEPH_SINOPAC_SECRET_KEY are "
            "not set"
        )
        self._down_until_ms: int | None = None
        self._down_reason: str | None = None
        self._quota_until_ms: int | None = None
        self._quota_reason: str | None = None
        self._budget_day: datetime.date | None = None
        self._spent = {"ticks": 0, "kbars": 0}
        self._feeds: dict[str, Feed] = {}
        self._contracts: dict[tuple[str, str], Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._users = 0
        self.calls: list[str] = []

    # Lifecycle -----------------------------------------------------------

    def acquire(self) -> None:
        """Registers one adapter using the source."""
        self._users += 1

    async def close(self) -> None:
        """Releases one user; logs out after the last."""
        self._users -= 1
        if self._users > 0:
            return
        for feed in list(self._feeds.values()):
            for queue in feed.listeners:
                queue.put_nowait(Event("down", reason="source closed"))
        self._feeds.clear()
        api, self._api = self._api, None
        if api is not None:
            with contextlib.suppress(Exception):
                await self._run(api.logout)

    @property
    def enabled(self) -> bool:
        """Returns False when Shioaji can never be used (no key or SDK)."""
        return self._permanent is None

    def check_sdk(self) -> bool:
        """Imports the SDK now, so capabilities are right from the start.

        Returns:
            True when the source is usable (credentials and SDK present).
        """
        if self._permanent is None and self._factory is None:
            try:
                self._factory = self._factory_fn()
            except errors.VenueUnavailableError as err:
                self._permanent = err.message
                _LOG.warning("sinopac market data disabled: %s", err.message)
        return self._permanent is None

    def unavailable(self) -> str | None:
        """Returns why Shioaji cannot be used now.

        Returns:
            Reason, or ``None`` when calls may be made.
        """
        if self._permanent is not None:
            return self._permanent
        now = self._clock.now_ms()
        if self._down_until_ms is not None and now < self._down_until_ms:
            return self._down_reason
        return None

    def history_unavailable(self) -> str | None:
        """Returns why Shioaji history queries cannot be made now.

        A used-up daily data allowance pauses ``ticks`` and ``kbars``
        (they come back empty) but not the subscribed streams.

        Returns:
            Reason, or ``None`` when queries may be made.
        """
        reason = self.unavailable()
        if reason is not None:
            return reason
        now = self._clock.now_ms()
        if self._quota_until_ms is not None and now < self._quota_until_ms:
            return self._quota_reason
        return None

    @staticmethod
    def _until(until_ms: int | None) -> str:
        """Formats when a pause ends.

        Args:
            until_ms: End of the pause.

        Returns:
            `` until YYYY-MM-DD HH:MM Taipei`` or nothing.
        """
        if until_ms is None:
            return ""
        moment = datetime.datetime.fromtimestamp(until_ms / 1000, _TPE)
        return " until " + moment.strftime("%Y-%m-%d %H:%M Taipei")

    def status_note(self) -> str:
        """Describes the current state for capability notes.

        Returns:
            One sentence.
        """
        reason = self.unavailable()
        if reason is not None:
            when = (
                ""
                if self._permanent is not None
                else self._until(self._down_until_ms)
            )
            return (
                f"Shioaji status: unavailable ({reason}){when}; TWSE/TPEx "
                "official data is served instead."
            )
        history = self.history_unavailable()
        if history is not None:
            return (
                f"Shioaji status: streams available, history paused "
                f"({history}){self._until(self._quota_until_ms)}; official "
                "data serves what the streams do not."
            )
        return (
            "Shioaji status: available (logged in)."
            if self._api is not None
            else "Shioaji status: available (logs in on first use)."
        )

    def _mark_down(self, kind: str, what: str) -> errors.VenueUnavailableError:
        """Records a failure and returns the error to raise.

        Args:
            kind: ``quota``, ``auth`` or ``outage``.
            what: Short description (never secrets).

        Returns:
            Error naming the fallback.
        """
        now = self._clock.now_ms()
        if kind == "quota":
            reason = f"daily data allowance used up ({what})"
            if self._quota_reason != reason or self._quota_until_ms is None:
                _LOG.warning(
                    "sinopac history paused: %s; serving official TWSE/TPEx "
                    "data",
                    reason,
                )
            self._quota_until_ms = quota_reset_ms(now)
            self._quota_reason = reason
            return errors.VenueUnavailableError(
                f"sinopac history unavailable: {reason}"
            )
        if kind == "auth":
            until = now + _LOGIN_RETRY_MS
            reason = f"login failed ({what})"
        else:
            until = now + _OUTAGE_RETRY_MS
            reason = f"outage ({what})"
        if self._down_reason != reason or self._down_until_ms is None:
            _LOG.warning(
                "sinopac market data unavailable: %s; serving official "
                "TWSE/TPEx data",
                reason,
            )
        self._down_until_ms = until
        self._down_reason = reason
        for feed in self._feeds.values():
            for queue in feed.listeners:
                queue.put_nowait(Event("down", reason=reason))
        return errors.VenueUnavailableError(f"sinopac unavailable: {reason}")

    def _require(self) -> None:
        """Refuses calls while Shioaji is unavailable.

        Raises:
            errors.VenueUnavailableError: When unavailable.
        """
        reason = self.unavailable()
        if reason is not None:
            raise errors.VenueUnavailableError(f"sinopac unavailable: {reason}")

    async def _run(self, fn: Callable[[], Any]) -> Any:
        """Runs one blocking SDK call at a time.

        Args:
            fn: Call.

        Returns:
            Its result.
        """
        async with self._sdk_lock:
            return await self._runner(fn)

    async def _client(self) -> SjDataClient:
        """Logs in once.

        Returns:
            Logged-in client.

        Raises:
            errors.VenueUnavailableError: When the SDK is missing or the
                login fails.
        """
        self._require()
        async with self._login_lock:
            if self._api is not None:
                return self._api
            self._require()
            if self._factory is None:
                try:
                    self._factory = self._factory_fn()
                except errors.VenueUnavailableError as err:
                    self._permanent = err.message
                    _LOG.warning(
                        "sinopac market data disabled: %s", err.message
                    )
                    raise
            api = self._factory.client()
            self._loop = asyncio.get_running_loop()
            api.set_on_tick_stk_v1_callback(self._on_tick)
            api.set_on_bidask_stk_v1_callback(self._on_bidask)
            api.set_event_callback(self._on_event)
            config = self.config
            assert config.api_key is not None and config.secret_key is not None
            key, secret = config.api_key, config.secret_key
            self.calls.append("login")
            try:
                await self._run(
                    lambda: api.login(
                        api_key=key, secret_key=secret, subscribe_trade=False
                    )
                )
            except Exception as err:
                kind = error_kind(err)
                raise self._mark_down(
                    "auth" if kind == "outage" else kind, type(err).__name__
                ) from err
            self._api = api
            self._down_until_ms = self._down_reason = None
            return api

    def _budget(self, kind: str) -> None:
        """Spends one trading-hours query of a kind.

        Args:
            kind: ``ticks`` or ``kbars``.

        Raises:
            errors.RateLimitedError: When today's budget is spent.
        """
        now = self._clock.now_ms()
        if not in_trading_hours(now):
            return
        day = taipei_day(now)
        if self._budget_day != day:
            self._budget_day = day
            self._spent = {"ticks": 0, "kbars": 0}
        limit = (
            self.config.intraday_ticks_calls
            if kind == "ticks"
            else self.config.intraday_kbars_calls
        )
        if self._spent[kind] >= limit:
            raise errors.RateLimitedError(
                f"sinopac {kind} budget for trading hours is spent "
                f"({limit} queries today)"
            )
        self._spent[kind] += 1

    def budget_left(self, kind: str) -> int:
        """Returns the trading-hours queries of a kind left today.

        Args:
            kind: ``ticks`` or ``kbars``.

        Returns:
            Queries left (unbounded outside trading hours: the limit).
        """
        now = self._clock.now_ms()
        limit = (
            self.config.intraday_ticks_calls
            if kind == "ticks"
            else self.config.intraday_kbars_calls
        )
        if not in_trading_hours(now):
            return limit
        if self._budget_day != taipei_day(now):
            return limit
        return max(0, limit - self._spent[kind])

    async def _contract(
        self, api: SjDataClient, code: str, exchange: str
    ) -> Any:
        """Looks up a stock contract.

        Args:
            api: Client.
            code: Security code.
            exchange: ``TSE`` or ``OTC``.

        Returns:
            Contract.

        Raises:
            errors.MissingDataError: When Shioaji has no such contract.
        """
        key = (exchange, code)
        found = self._contracts.get(key)
        if found is not None:
            return found
        try:
            found = await self._run(
                lambda: getattr(api.contracts.stocks, exchange)[code]
            )
        except (KeyError, AttributeError, IndexError) as err:
            raise errors.MissingDataError(
                f"Shioaji has no {exchange} contract {code}"
            ) from err
        if found is None:
            raise errors.MissingDataError(
                f"Shioaji has no {exchange} contract {code}"
            )
        self._contracts[key] = found
        return found

    def _require_history(self) -> None:
        """Refuses history queries while they are paused.

        Raises:
            errors.VenueUnavailableError: When paused or unavailable.
        """
        reason = self.history_unavailable()
        if reason is not None:
            raise errors.VenueUnavailableError(
                f"sinopac history unavailable: {reason}"
            )

    async def _query(
        self, kind: str, code: str, exchange: str, call: Callable[[Any], Any]
    ) -> Any:
        """Makes one budgeted, paced market-data query.

        Args:
            kind: ``ticks`` or ``kbars``.
            code: Security code.
            exchange: ``TSE`` or ``OTC``.
            call: ``call(contract)`` on the client.

        Returns:
            SDK result.

        Raises:
            errors.VenueUnavailableError: On SDK failures (the source is
                marked down) or a spent budget.
        """
        self._require_history()
        api = await self._client()
        contract = await self._contract(api, code, exchange)
        self._budget(kind)
        await self._limiter.acquire()
        self.calls.append(f"{kind} {exchange} {code}")
        try:
            return await self._run(lambda: call(contract))
        except Exception as err:
            raise self._mark_down(
                error_kind(err), f"{kind}: {type(err).__name__}"
            ) from err

    async def _check_allowance(self, what: str) -> None:
        """Distinguishes an empty answer from a used-up data allowance.

        Args:
            what: Query that came back empty.

        Raises:
            errors.VenueUnavailableError: When no bytes are left today.
        """
        api = await self._client()
        try:
            usage = await self._run(api.usage)
        except Exception as err:
            raise self._mark_down(
                error_kind(err), f"usage: {type(err).__name__}"
            ) from err
        remaining = getattr(usage, "remaining_bytes", None)
        if remaining is not None and int(remaining) <= 0:
            raise self._mark_down("quota", f"{what} came back empty")

    async def kbars(
        self,
        code: str,
        exchange: str,
        first: datetime.date,
        last: datetime.date,
    ) -> Any:
        """Reads one-minute kbars of up to 30 days.

        Args:
            code: Security code.
            exchange: ``TSE`` or ``OTC``.
            first: First date.
            last: Last date (at most 29 days after ``first``).

        Returns:
            ``KBars`` columns.

        Raises:
            ValueError: For a range longer than 30 days.
        """
        if (last - first).days >= KBARS_MAX_DAYS:
            raise ValueError("Shioaji kbars span at most 30 days")
        start, end = first.isoformat(), last.isoformat()
        rows = await self._query(
            "kbars",
            code,
            exchange,
            lambda contract: self._kbars_call(contract, start, end),
        )
        if not _column(rows, "ts"):
            await self._check_allowance("kbars")
        return rows

    def _kbars_call(self, contract: Any, start: str, end: str) -> Any:
        """Calls ``api.kbars`` (worker thread).

        Args:
            contract: Contract.
            start: First date.
            end: Last date.

        Returns:
            ``KBars``.
        """
        assert self._api is not None
        return self._api.kbars(contract=contract, start=start, end=end)

    async def ticks(self, code: str, exchange: str, day: datetime.date) -> Any:
        """Reads one day's ticks (``AllDay``).

        Args:
            code: Security code.
            exchange: ``TSE`` or ``OTC``.
            day: Trading date.

        Returns:
            ``Ticks`` columns.
        """
        text = day.isoformat()
        rows = await self._query(
            "ticks",
            code,
            exchange,
            lambda contract: self._ticks_call(contract, text),
        )
        if not _column(rows, "ts"):
            await self._check_allowance("ticks")
        return rows

    def _ticks_call(self, contract: Any, day: str) -> Any:
        """Calls ``api.ticks`` (worker thread).

        Args:
            contract: Contract.
            day: Date.

        Returns:
            ``Ticks``.
        """
        assert self._api is not None
        return self._api.ticks(contract=contract, date=day)

    # Streams -------------------------------------------------------------

    def feed(self, code: str) -> Feed | None:
        """Returns the live feed of a code, if streamed.

        Args:
            code: Security code.

        Returns:
            Feed or ``None``.
        """
        return self._feeds.get(code)

    async def watch(
        self, code: str, exchange: str, instrument_id: instruments.InstrumentId
    ) -> Feed:
        """Streams a code's ticks and bid/ask (subscribing on first use).

        Idle feeds (no listener, unused for ``WATCH_TTL_MS``) are dropped,
        the least recently used first when the symbol budget is full.

        Args:
            code: Security code.
            exchange: ``TSE`` or ``OTC``.
            instrument_id: Instrument.

        Returns:
            Feed.

        Raises:
            errors.VenueUnavailableError: When Shioaji is unavailable, the
                subscription fails or every streamed code is in use.
        """
        now = self._clock.now_ms()
        found = self._feeds.get(code)
        if found is not None:
            self._require()
            found.used_ms = now
            return found
        api = await self._client()
        await self._expire(api, now)
        if len(self._feeds) >= self.config.max_symbols:
            raise errors.VenueUnavailableError(
                f"sinopac streams {self.config.max_symbols} codes already "
                "(JOSEPH_SINOPAC_MAX_SYMBOLS)"
            )
        contract = await self._contract(api, code, exchange)
        self.calls.append(f"subscribe {exchange} {code}")
        try:
            await self._run(
                lambda: self._quote_streams(api, contract, subscribe=True)
            )
        except Exception as err:
            raise self._mark_down(
                error_kind(err), f"subscribe: {type(err).__name__}"
            ) from err
        feed = Feed(code, exchange, instrument_id)
        feed.used_ms = now
        self._feeds[code] = feed
        return feed

    async def _expire(self, api: SjDataClient, now_ms: int) -> None:
        """Unsubscribes idle feeds (and the LRU one when full).

        Args:
            api: Client.
            now_ms: Current time.
        """
        idle = sorted(
            (feed for feed in self._feeds.values() if not feed.listeners),
            key=lambda feed: feed.used_ms,
        )
        drop = [feed for feed in idle if now_ms - feed.used_ms > WATCH_TTL_MS]
        if len(self._feeds) - len(drop) >= self.config.max_symbols:
            rest = [feed for feed in idle if feed not in drop]
            drop.extend(rest[:1])
        for feed in drop:
            del self._feeds[feed.code]
            contract = self._contracts.get((feed.exchange, feed.code))
            self.calls.append(f"unsubscribe {feed.exchange} {feed.code}")
            with contextlib.suppress(Exception):
                await self._run(
                    functools.partial(
                        self._quote_streams, api, contract, subscribe=False
                    )
                )

    def _quote_streams(
        self, api: SjDataClient, contract: Any, *, subscribe: bool
    ) -> None:
        """Subscribes or drops a contract's tick and bid/ask (worker thread).

        Args:
            api: Client.
            contract: Contract.
            subscribe: True to subscribe, False to unsubscribe.
        """
        assert self._factory is not None
        version = self._factory.quote_version()
        method = api.subscribe if subscribe else api.unsubscribe
        for quote_type in ("tick", "bid_ask"):
            method(
                contract,
                quote_type=quote_type,
                intraday_odd=False,
                version=version,
            )

    def listen(self, feed: Feed) -> asyncio.Queue[Event]:
        """Registers a stream reading a feed.

        Args:
            feed: Feed.

        Returns:
            Queue receiving the feed's events.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        feed.listeners.append(queue)
        return queue

    def unlisten(self, feed: Feed, queue: asyncio.Queue[Event]) -> None:
        """Unregisters a stream.

        Args:
            feed: Feed.
            queue: Its queue.
        """
        with contextlib.suppress(ValueError):
            feed.listeners.remove(queue)
        feed.used_ms = self._clock.now_ms()

    def _hop(self, fn: Callable[..., None], *args: Any) -> None:
        """Runs a callback's work on the event loop.

        Args:
            fn: Loop-side handler.
            *args: Its arguments.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(fn, *args)

    def _on_tick(self, *args: Any) -> None:
        """Receives ``TickSTKv1`` on an SDK thread.

        Args:
            *args: ``(tick)`` or ``(exchange, tick)``.
        """
        if args:
            self._hop(self.deliver_tick, args[-1])

    def _on_bidask(self, *args: Any) -> None:
        """Receives ``BidAskSTKv1`` on an SDK thread.

        Args:
            *args: ``(bidask)`` or ``(exchange, bidask)``.
        """
        if args:
            self._hop(self.deliver_bidask, args[-1])

    def _on_event(
        self, resp_code: int, event_code: int, info: str, event: str
    ) -> None:
        """Receives session events on an SDK thread.

        Args:
            resp_code: Response code.
            event_code: Event code (0 up, 1 down, 2 connect failed, 12
                reconnecting, 13 reconnected).
            info: Text.
            event: Event name.
        """
        del resp_code, event
        self._hop(self.deliver_event, int(event_code), str(info))

    @staticmethod
    def _broadcast(feed: Feed, events: Sequence[Event]) -> None:
        """Hands events to a feed's listeners.

        Args:
            feed: Feed.
            events: Events.
        """
        for queue in feed.listeners:
            for event in events:
                queue.put_nowait(event)

    def deliver_tick(self, tick: Any) -> None:
        """Applies a tick on the loop thread.

        Args:
            tick: ``TickSTKv1``.
        """
        feed = self._feeds.get(str(getattr(tick, "code", "")))
        if feed is not None:
            self._broadcast(feed, feed.apply_tick(tick, self._clock.now_ms()))

    def deliver_bidask(self, bidask: Any) -> None:
        """Applies a bid/ask update on the loop thread.

        Args:
            bidask: ``BidAskSTKv1``.
        """
        feed = self._feeds.get(str(getattr(bidask, "code", "")))
        if feed is not None:
            self._broadcast(feed, feed.apply_book(bidask, self._clock.now_ms()))

    def deliver_event(self, event_code: int, info: str) -> None:
        """Applies a session event on the loop thread.

        Args:
            event_code: Event code.
            info: Text.
        """
        if event_code in _DOWN_EVENTS:
            for feed in self._feeds.values():
                feed.suspect = True
            self._mark_down("outage", f"session event {event_code}")
        elif event_code == _RECONNECTING:
            for feed in self._feeds.values():
                feed.suspect = True
        elif (
            event_code in _UP_EVENTS
            and self._down_reason is not None
            and self._down_reason.startswith("outage (session event")
        ):
            self._down_until_ms = self._down_reason = None
        _LOG.info("shioaji session event %s: %s", event_code, info)

    def live_snapshot(self, feed: Feed) -> market.MarketSnapshot | None:
        """Builds a snapshot from a feed's latest tick and book.

        Args:
            feed: Feed.

        Returns:
            Snapshot, or ``None`` before today's first tick.
        """
        quote = feed.quote
        if quote is None:
            return None
        book = feed.book
        previous = None if quote.change is None else quote.close - quote.change
        change = (
            None
            if previous is None or previous <= 0 or quote.change is None
            else quote.change / previous * decimals.HUNDRED
        )
        return market.MarketSnapshot(
            instrument_id=feed.instrument_id,
            feed_state=quality.FeedState.LIVE,
            source=BOOK_SOURCE,
            as_of_ms=max(
                quote.ts_ms, book.snapshot_ts_ms if book is not None else 0
            ),
            last=quote.close,
            best_bid=None if book is None else book.best_bid,
            best_ask=None if book is None else book.best_ask,
            volume_24h_base=quote.total_volume,
            change_24h_pct=change,
        )
