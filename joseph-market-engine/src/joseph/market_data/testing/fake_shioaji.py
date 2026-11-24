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
    """Shioaji: the daily data allowance is used up."""


class AuthError(Exception):
    """Shioaji: not authenticated."""


class TokenError(AuthError):
    """Shioaji: token invalid or expired."""


class ShioajiConnectionError(ConnectionError):
    """Shioaji: transport failure."""


@dataclasses.dataclass
class FakeStock:
    """``sj.Stock`` contract.

    Attributes:
        code: Security code.
        exchange: ``TSE`` or ``OTC``.
    """

    code: str
    exchange: str = "TSE"


class FakeGroup:
    """``ContractGroup`` of one exchange.

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
            code: Code.

        Returns:
            Contract.
        """
        return self.items[code]


class FakeCategory:
    """``contracts.stocks`` with the ``TSE`` and ``OTC`` groups.

    Attributes:
        TSE: TWSE contracts.
        OTC: TPEx contracts.
    """

    def __init__(self, stocks: dict[str, str]) -> None:
        """Initializes the category.

        Args:
            stocks: Exchange per code.
        """
        self.TSE = FakeGroup(
            {c: FakeStock(c, "TSE") for c, e in stocks.items() if e == "TSE"}
        )
        self.OTC = FakeGroup(
            {c: FakeStock(c, "OTC") for c, e in stocks.items() if e == "OTC"}
        )


@dataclasses.dataclass
class FakeContracts:
    """``api.contracts``.

    Attributes:
        stocks: Stock contracts.
    """

    stocks: FakeCategory


@dataclasses.dataclass
class FakeUsage:
    """``UsageOut``.

    Attributes:
        connections: Open connections.
        bytes: Bytes used today.
        limit_bytes: Daily allowance.
        remaining_bytes: Bytes left today.
    """

    connections: int
    bytes: int
    limit_bytes: int
    remaining_bytes: int


@dataclasses.dataclass
class FakeTicks:
    """``Ticks`` columns.

    Attributes:
        ts: Taipei wall-clock epoch ns.
        close: Prices.
        volume: Lots.
        bid_price: Bid after the trade.
        bid_volume: Bid size.
        ask_price: Ask after the trade.
        ask_volume: Ask size.
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
    """``KBars`` columns (labels are minute ends).

    Attributes:
        ts: Taipei wall-clock epoch ns of each minute's end.
        Open: Opens.
        High: Highs.
        Low: Lows.
        Close: Closes.
        Volume: Lots.
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
    """``TickSTKv1``.

    Attributes:
        code: Code.
        datetime: Naive Taipei time.
        close: Price (str).
        volume: Lots of this tick.
        total_volume: Day lots through this tick.
        tick_type: Inside/outside flag.
        open: Day open (str).
        high: Day high (str).
        low: Day low (str).
        price_chg: Change from the reference price (str).
        simtrade: Trial match (not a trade).
        intraday_odd: Odd-lot stream.
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
    """``BidAskSTKv1``.

    Attributes:
        code: Code.
        datetime: Naive Taipei time.
        bid_price: Five bid prices (str).
        bid_volume: Five bid sizes (lots).
        ask_price: Five ask prices (str).
        ask_volume: Five ask sizes (lots).
        simtrade: Trial match.
        intraday_odd: Odd-lot stream.
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
    """``sj.Shioaji`` market data with scripted history and streams.

    Attributes:
        kbars_rows: ``(code, date)`` -> ``(label, open, high, low, close,
            lots, amount)`` rows (``label`` a naive Taipei minute end).
        ticks_rows: ``(code, date)`` -> ``(time, price, lots, tick_type)``.
        failures: Method name -> exception raised on the next call(s).
        remaining_bytes: Data allowance left (``usage``).
        empty_when_spent: Queries return nothing once the allowance is
            spent (Shioaji's documented behaviour).
        subscriptions: ``(code, quote_type)`` subscribed.
        calls: Methods called, with their main argument.
        logged_in: Whether ``login`` succeeded.
    """

    def __init__(self, stocks: dict[str, str] | None = None) -> None:
        """Initializes the fake.

        Args:
            stocks: Exchange (``TSE``/``OTC``) per code.
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
        """Raises a scripted failure.

        Args:
            name: Method name.

        Raises:
            BaseException: The scripted exception.
        """
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    def login(
        self, api_key: str, secret_key: str, subscribe_trade: bool = True
    ) -> list[Any]:
        """Logs in.

        Args:
            api_key: API key.
            secret_key: Secret key.
            subscribe_trade: Ignored.

        Returns:
            One account.
        """
        del api_key, secret_key, subscribe_trade
        self.calls.append("login")
        self._maybe_fail("login")
        self.logged_in = True
        return [object()]

    def logout(self) -> object:
        """Logs out.

        Returns:
            True.
        """
        self.calls.append("logout")
        self.logged_in = False
        return True

    def usage(self) -> FakeUsage:
        """Returns the data usage of today.

        Returns:
            ``UsageOut``.
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
        """Returns the contract tree."""
        return self._contracts

    def ticks(self, contract: FakeStock, date: str) -> FakeTicks:
        """Returns one day's ticks.

        Args:
            contract: Contract.
            date: ``YYYY-MM-DD``.

        Returns:
            Columns (empty when the allowance is spent).
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
        """Returns one-minute kbars of a date range.

        Args:
            contract: Contract.
            start: First date.
            end: Last date.

        Returns:
            Columns (empty when the allowance is spent).
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
        """Subscribes a stream.

        Args:
            contract: Contract.
            quote_type: ``tick`` or ``bid_ask``.
            intraday_odd: Must be False here.
            version: ``v1``.
        """
        del version
        assert not intraday_odd
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
        """Sets the tick callback.

        Args:
            callback: Callback.
        """
        self._tick_cb = callback

    def set_on_bidask_stk_v1_callback(
        self, callback: Callable[..., None]
    ) -> None:
        """Sets the bid/ask callback.

        Args:
            callback: Callback.
        """
        self._bidask_cb = callback

    def set_event_callback(
        self, callback: Callable[[int, int, str, str], None]
    ) -> None:
        """Sets the session event callback.

        Args:
            callback: Callback.
        """
        self._event_cb = callback

    def push_tick(self, tick: FakeTick) -> None:
        """Delivers a tick as the docs show (``exchange, tick``).

        Args:
            tick: Tick.
        """
        assert self._tick_cb is not None
        self._tick_cb("TSE", tick)

    def push_bidask(self, bidask: FakeBidAsk) -> None:
        """Delivers a bid/ask update as the 1.7.11 stubs show (quote only).

        Args:
            bidask: Update.
        """
        assert self._bidask_cb is not None
        self._bidask_cb(bidask)

    def push_event(self, event_code: int, info: str = "") -> None:
        """Delivers a session event.

        Args:
            event_code: Code.
            info: Text.
        """
        assert self._event_cb is not None
        self._event_cb(0, event_code, info, "event")


class FakeShioajiFactory:
    """``SjDataFactory`` handing out one ``FakeShioaji``.

    Attributes:
        api: The fake client.
        created: Clients created.
    """

    def __init__(self, api: FakeShioaji) -> None:
        """Initializes the factory.

        Args:
            api: Fake client.
        """
        self.api = api
        self.created = 0

    def client(self) -> FakeShioaji:
        """Returns the fake client.

        Returns:
            Client.
        """
        self.created += 1
        return self.api

    def quote_version(self) -> str:
        """Returns ``v1``."""
        return "v1"
