# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""In-memory stand-in for ``shioaji.Shioaji`` market data (no network).

Mirrors the Shioaji 1.7.11 shapes the market-data source depends on
(``shioaji/_core.pyi``): columnar ``Ticks``/``KBars`` whose ``ts`` is Taipei
wall-clock time as epoch nanoseconds, kbars labeled by the end of their
minute with volumes in board lots, ``TickSTKv1``/``BidAskSTKv1`` stream
objects (prices as strings, volumes in lots), ``usage()`` and the
``contracts.stocks.TSE``/``OTC`` groups. Exceptions carry Shioaji's class
names, which the source classifies by name.

Where it differs from the real SDK: there are no rate limits, query budgets,
30-day ``kbars`` cap or connection limits; queries never use up the
allowance (tests set ``remaining_bytes``); ``ticks`` echoes each trade price
as its bid and ask with a size of one lot; streams deliver only what a test
pushes, with the exchange always ``"TSE"``; and only the methods and fields
the market-data source reads exist.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Callable
from typing import Any

ALLOWANCE = 500 * 1024 * 1024
"""Daily data allowance of an account without API trading (500 MB)."""


def wall_ns(moment: datetime.datetime) -> int:
    """Encodes a naive Taipei time the way Shioaji's ``ts`` does.

    Args:
        moment: Naive Taipei wall-clock time.

    Returns:
        Epoch nanoseconds of that wall-clock time read as UTC.
    """
    utc = moment.replace(tzinfo=datetime.UTC)
    return int(utc.timestamp() * 1_000_000) * 1_000


class MarketDataQueryUsageLimitError(Exception):
    """Shioaji's error for a used-up daily data allowance."""


class AuthError(Exception):
    """Shioaji's error for a session that is not authenticated."""


class TokenError(AuthError):
    """Shioaji's error for an invalid or expired token."""


class ShioajiConnectionError(ConnectionError):
    """A Shioaji transport failure."""


@dataclasses.dataclass
class FakeStock:
    """An ``sj.Stock`` contract.

    Attributes:
        code: Security code, for example ``2330``.
        exchange: ``TSE`` (TWSE) or ``OTC`` (TPEx).
    """

    code: str
    exchange: str = "TSE"


class FakeGroup:
    """The ``ContractGroup`` of one exchange, indexed by security code.

    Attributes:
        items: Contracts by code.
    """

    def __init__(self, items: dict[str, FakeStock]) -> None:
        """Initializes the group.

        Args:
            items: Contracts by code.
        """
        self.items = items

    def __getitem__(self, code: str) -> FakeStock:
        """Looks up a contract.

        Args:
            code: Security code.

        Returns:
            The contract.

        Raises:
            KeyError: The group has no contract with that code.
        """
        return self.items[code]


class FakeCategory:
    """The ``contracts.stocks`` tree with the ``TSE`` and ``OTC`` groups.

    Attributes:
        TSE: TWSE contracts.
        OTC: TPEx contracts.
    """

    def __init__(self, stocks: dict[str, str]) -> None:
        """Initializes the category.

        Args:
            stocks: Exchange (``TSE`` or ``OTC``) per security code; codes
                of any other exchange are left out.
        """
        self.TSE = FakeGroup(
            {c: FakeStock(c, "TSE") for c, e in stocks.items() if e == "TSE"}
        )
        self.OTC = FakeGroup(
            {c: FakeStock(c, "OTC") for c, e in stocks.items() if e == "OTC"}
        )


@dataclasses.dataclass
class FakeContracts:
    """The ``api.contracts`` root.

    Attributes:
        stocks: Stock contracts.
    """

    stocks: FakeCategory


@dataclasses.dataclass
class FakeUsage:
    """The ``UsageOut`` result of ``usage()``.

    Attributes:
        connections: Open connections.
        bytes: Bytes used today.
        limit_bytes: Daily allowance in bytes.
        remaining_bytes: Bytes left today.
    """

    connections: int
    bytes: int
    limit_bytes: int
    remaining_bytes: int


@dataclasses.dataclass
class FakeTicks:
    """The ``Ticks`` columns of ``ticks()``, one entry per trade.

    Attributes:
        ts: Taipei wall-clock time as epoch nanoseconds (see ``wall_ns``).
        close: Trade prices.
        volume: Trade sizes in board lots.
        bid_price: Best bid after the trade.
        bid_volume: Best bid size in lots.
        ask_price: Best ask after the trade.
        ask_volume: Best ask size in lots.
        tick_type: ``1`` 外盤, ``2`` 內盤, ``0`` undetermined.
    """

    ts: list[int] = dataclasses.field(default_factory=list)
    close: list[float] = dataclasses.field(default_factory=list)
    volume: list[int] = dataclasses.field(default_factory=list)
    bid_price: list[float] = dataclasses.field(default_factory=list)
    bid_volume: list[int] = dataclasses.field(default_factory=list)
    ask_price: list[float] = dataclasses.field(default_factory=list)
    ask_volume: list[int] = dataclasses.field(default_factory=list)
    tick_type: list[int] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class FakeKBars:
    """The ``KBars`` columns of ``kbars()``, one entry per minute.

    Attributes:
        ts: Taipei wall-clock epoch nanoseconds of each minute's end (a
            kbar is labeled by the end of its minute).
        Open: Opening prices.
        High: High prices.
        Low: Low prices.
        Close: Closing prices.
        Volume: Volumes in board lots.
        Amount: Turnover in TWD.
    """

    ts: list[int] = dataclasses.field(default_factory=list)
    Open: list[float] = dataclasses.field(default_factory=list)
    High: list[float] = dataclasses.field(default_factory=list)
    Low: list[float] = dataclasses.field(default_factory=list)
    Close: list[float] = dataclasses.field(default_factory=list)
    Volume: list[int] = dataclasses.field(default_factory=list)
    Amount: list[float] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class FakeTick:
    """A ``TickSTKv1`` stream trade.

    Attributes:
        code: Security code.
        datetime: Naive Taipei time.
        close: Trade price as a string, as Shioaji sends it.
        volume: Lots of this tick.
        total_volume: Day lots through this tick.
        tick_type: ``1`` 外盤, ``2`` 內盤, ``0`` undetermined (Shioaji's
            classification, see the ``Ticks`` columns).
        open: Day open (str).
        high: Day high (str).
        low: Day low (str).
        price_chg: Change from the reference price (str).
        simtrade: Trial match (試撮), not a trade.
        intraday_odd: Whether it comes from the odd-lot stream.
    """

    code: str
    datetime: datetime.datetime
    close: str
    volume: int
    total_volume: int
    tick_type: int = 1
    open: str = ""
    high: str = ""
    low: str = ""
    price_chg: str | None = None
    simtrade: bool = False
    intraday_odd: bool = False


@dataclasses.dataclass
class FakeBidAsk:
    """A ``BidAskSTKv1`` stream book update.

    Attributes:
        code: Security code.
        datetime: Naive Taipei time.
        bid_price: Up to five bid prices as strings, best first.
        bid_volume: Bid sizes in lots, matching ``bid_price``.
        ask_price: Up to five ask prices as strings, best first.
        ask_volume: Ask sizes in lots, matching ``ask_price``.
        simtrade: Trial match (試撮), not a live book.
        intraday_odd: Whether it comes from the odd-lot stream.
    """

    code: str
    datetime: datetime.datetime
    bid_price: list[str]
    bid_volume: list[int]
    ask_price: list[str]
    ask_volume: list[int]
    simtrade: bool = False
    intraday_odd: bool = False


class FakeShioaji:
    """An ``sj.Shioaji`` client with scripted history and streams.

    Tests script it through the public attributes and drive the streams with
    the ``push_*`` methods.

    Attributes:
        kbars_rows: ``(code, date)`` -> ``(label, open, high, low, close,
            lots, amount)`` rows (``label`` a naive Taipei minute end).
        ticks_rows: ``(code, date)`` -> ``(time, price, lots, tick_type)``
            rows (``time`` a naive Taipei time).
        failures: Method name -> exception that every call of that method
            raises until the entry is removed (``login``, ``usage``,
            ``ticks``, ``kbars`` and ``subscribe`` check it).
        remaining_bytes: Data allowance left in bytes, reported by
            ``usage``; queries do not use it up.
        empty_when_spent: Whether ``ticks`` and ``kbars`` return empty
            columns once ``remaining_bytes`` is zero or less (Shioaji's
            documented behaviour).
        subscriptions: ``(code, quote_type)`` pairs subscribed.
        calls: Methods called, each with its main arguments, for example
            ``"kbars 2330 2026-11-24 2026-11-24"``.
        logged_in: Whether ``login`` succeeded and no ``logout`` followed.
    """

    def __init__(self, stocks: dict[str, str] | None = None) -> None:
        """Initializes the fake.

        Args:
            stocks: Exchange (``TSE``/``OTC``) per security code; ``None``
                or empty gives 2330 on TWSE and 6488 on TPEx.
        """
        self._contracts = FakeContracts(
            FakeCategory(stocks or {"2330": "TSE", "6488": "OTC"})
        )
        self.kbars_rows: dict[tuple[str, datetime.date], list[tuple[Any, ...]]]
        self.kbars_rows = {}
        self.ticks_rows: dict[tuple[str, datetime.date], list[tuple[Any, ...]]]
        self.ticks_rows = {}
        self.failures: dict[str, BaseException] = {}
        self.remaining_bytes = ALLOWANCE
        self.empty_when_spent = True
        self.subscriptions: set[tuple[str, str]] = set()
        self.calls: list[str] = []
        self.logged_in = False
        self._tick_cb: Callable[..., None] | None = None
        self._bidask_cb: Callable[..., None] | None = None
        self._event_cb: Callable[[int, int, str, str], None] | None = None

    def _maybe_fail(self, name: str) -> None:
        """Raises the failure scripted for a method, if any.

        Args:
            name: Method name, a key of ``failures``.

        Raises:
            BaseException: The scripted exception.
        """
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    def login(
        self, api_key: str, secret_key: str, subscribe_trade: bool = True
    ) -> list[Any]:
        """Logs in, or raises the failure scripted for ``login``.

        Args:
            api_key: API key (ignored).
            secret_key: Secret key (ignored).
            subscribe_trade: Ignored.

        Returns:
            One placeholder account.
        """
        del api_key, secret_key, subscribe_trade
        self.calls.append("login")
        self._maybe_fail("login")
        self.logged_in = True
        return [object()]

    def logout(self) -> object:
        """Logs out.

        Returns:
            ``True``, as Shioaji does on success.
        """
        self.calls.append("logout")
        self.logged_in = False
        return True

    def usage(self) -> FakeUsage:
        """Returns today's data usage, or raises the scripted failure.

        Returns:
            One connection and the allowance left in ``remaining_bytes``.
        """
        self.calls.append("usage")
        self._maybe_fail("usage")
        return FakeUsage(
            1,
            ALLOWANCE - self.remaining_bytes,
            ALLOWANCE,
            self.remaining_bytes,
        )

    @property
    def contracts(self) -> FakeContracts:
        """The contract tree (``contracts.stocks.TSE[code]``)."""
        return self._contracts

    def ticks(self, contract: FakeStock, date: str) -> FakeTicks:
        """Returns one day's ticks from ``ticks_rows``.

        Args:
            contract: Contract.
            date: Taipei trading date as ``YYYY-MM-DD``.

        Returns:
            The day's columns; empty when no rows are scripted, or when the
            allowance is spent and ``empty_when_spent`` is set.
        """
        self.calls.append(f"ticks {contract.code} {date}")
        self._maybe_fail("ticks")
        out = FakeTicks()
        if self.remaining_bytes <= 0 and self.empty_when_spent:
            return out
        day = datetime.date.fromisoformat(date)
        for moment, price, lots, kind in self.ticks_rows.get(
            (contract.code, day), []
        ):
            out.ts.append(wall_ns(moment))
            out.close.append(price)
            out.volume.append(lots)
            out.bid_price.append(price)
            out.bid_volume.append(1)
            out.ask_price.append(price)
            out.ask_volume.append(1)
            out.tick_type.append(kind)
        return out

    def kbars(self, contract: FakeStock, start: str, end: str) -> FakeKBars:
        """Returns one-minute kbars of a date range from ``kbars_rows``.

        Args:
            contract: Contract.
            start: First Taipei date as ``YYYY-MM-DD``, inclusive.
            end: Last Taipei date as ``YYYY-MM-DD``, inclusive.

        Returns:
            The range's columns in date order; empty when no rows are
            scripted, or when the allowance is spent and
            ``empty_when_spent`` is set.
        """
        self.calls.append(f"kbars {contract.code} {start} {end}")
        self._maybe_fail("kbars")
        out = FakeKBars()
        if self.remaining_bytes <= 0 and self.empty_when_spent:
            return out
        first = datetime.date.fromisoformat(start)
        last = datetime.date.fromisoformat(end)
        for (code, day), rows in sorted(self.kbars_rows.items()):
            if code != contract.code or not first <= day <= last:
                continue
            for label, open_, high, low, close, lots, amount in rows:
                out.ts.append(wall_ns(label))
                out.Open.append(open_)
                out.High.append(high)
                out.Low.append(low)
                out.Close.append(close)
                out.Volume.append(lots)
                out.Amount.append(amount)
        return out

    def subscribe(
        self,
        contract: FakeStock,
        quote_type: str,
        intraday_odd: bool = False,
        version: Any = None,
    ) -> None:
        """Subscribes a stream, or raises the failure scripted for it.

        Args:
            contract: Contract.
            quote_type: ``tick`` or ``bid_ask``.
            intraday_odd: Must be False: the fake streams board lots only.
            version: Quote version (ignored; the source asks for ``v1``).
        """
        del version
        if intraday_odd:
            raise AssertionError("the fake streams board lots only")
        self.calls.append(f"subscribe {contract.code} {quote_type}")
        self._maybe_fail("subscribe")
        self.subscriptions.add((contract.code, quote_type))

    def unsubscribe(
        self,
        contract: FakeStock,
        quote_type: str,
        intraday_odd: bool = False,
        version: Any = None,
    ) -> None:
        """Unsubscribes a stream.

        Args:
            contract: Contract.
            quote_type: ``tick`` or ``bid_ask``.
            intraday_odd: Ignored.
            version: Ignored.
        """
        del intraday_odd, version
        self.calls.append(f"unsubscribe {contract.code} {quote_type}")
        self.subscriptions.discard((contract.code, quote_type))

    def set_on_tick_stk_v1_callback(
        self, callback: Callable[..., None]
    ) -> None:
        """Sets the tick callback that ``push_tick`` calls.

        Args:
            callback: Called with ``(exchange, tick)``.
        """
        self._tick_cb = callback

    def set_on_bidask_stk_v1_callback(
        self, callback: Callable[..., None]
    ) -> None:
        """Sets the bid/ask callback that ``push_bidask`` calls.

        Args:
            callback: Called with the ``BidAskSTKv1`` update alone.
        """
        self._bidask_cb = callback

    def set_event_callback(
        self, callback: Callable[[int, int, str, str], None]
    ) -> None:
        """Sets the session event callback that ``push_event`` calls.

        Args:
            callback: Called with ``(resp_code, event_code, info, event)``.
        """
        self._event_cb = callback

    def push_tick(self, tick: FakeTick) -> None:
        """Delivers a tick as the docs show (``exchange, tick``).

        The exchange is always ``"TSE"``; the source ignores it.

        Args:
            tick: Tick.
        """
        if self._tick_cb is None:
            raise AssertionError("set_on_tick_stk_v1_callback() first")
        self._tick_cb("TSE", tick)

    def push_bidask(self, bidask: FakeBidAsk) -> None:
        """Delivers a bid/ask update as the 1.7.11 stubs show (quote only).

        Args:
            bidask: Update.
        """
        if self._bidask_cb is None:
            raise AssertionError("set_on_bidask_stk_v1_callback() first")
        self._bidask_cb(bidask)

    def push_event(self, event_code: int, info: str = "") -> None:
        """Delivers a session event with response code 0.

        Args:
            event_code: Shioaji event code (0 up, 1 down, 2 connect
                failed, 12 reconnecting, 13 reconnected).
            info: Event text.
        """
        if self._event_cb is None:
            raise AssertionError("set_event_callback() first")
        self._event_cb(0, event_code, info, "event")


class FakeShioajiFactory:
    """An ``SjDataFactory`` that hands out one shared ``FakeShioaji``.

    Attributes:
        api: The fake client.
        created: Number of ``client()`` calls.
    """

    def __init__(self, api: FakeShioaji) -> None:
        """Initializes the factory.

        Args:
            api: Fake client.
        """
        self.api = api
        self.created = 0

    def client(self) -> FakeShioaji:
        """Returns the shared fake client and counts the call.

        Returns:
            ``api``.
        """
        self.created += 1
        return self.api

    def quote_version(self) -> str:
        """Returns ``"v1"``, the quote version the source subscribes."""
        return "v1"
