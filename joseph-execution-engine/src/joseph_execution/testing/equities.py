"""Securities fixtures for tests and demos: instruments and sessions.

Sessions are built with fixed UTC offsets (Taipei UTC+8; New York in
winter, UTC-5) so tests need no time-zone database.
"""

from __future__ import annotations

import datetime
import decimal

from joseph_execution.adapters import market_static
from joseph_execution.domain import enums
from joseph_execution.domain import market

_D = decimal.Decimal
TSMC = "twse:stock:2330"
YUANTA_50 = "twse:etf:0050"
GLOBALWAFERS = "tpex:stock:6488"
YUANTA_US20 = "tpex:etf:00679B"
AAPL = "nasdaq:stock:AAPL"
SPY = "nyse:etf:SPY"
UST10Y = "treasury:rate:UST10Y"
MOCK_STOCK = "mock:stock:DEMO"

TAIWAN_STOCK_LADDER = (
    market.TickStep(_D(0), _D("0.01")),
    market.TickStep(_D(10), _D("0.05")),
    market.TickStep(_D(50), _D("0.1")),
    market.TickStep(_D(100), _D("0.5")),
    market.TickStep(_D(500), _D(1)),
    market.TickStep(_D(1000), _D(5)),
)
"""TWSE and TPEx stock tick ladder."""

TAIWAN_ETF_LADDER = (
    market.TickStep(_D(0), _D("0.01")),
    market.TickStep(_D(50), _D("0.05")),
)
"""TWSE and TPEx ETF tick ladder."""

_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
_NEW_YORK_WINTER = datetime.timezone(datetime.timedelta(hours=-5))


def twse(symbol: str = "2330", etf: bool = False) -> market.InstrumentMeta:
    """Builds TWSE instrument metadata (as the market engine publishes it).

    Args:
        symbol: TWSE code.
        etf: ETF instead of stock.

    Returns:
        Metadata with the tick ladder, 10% limit and 1000-share board lot.
    """
    kind = "etf" if etf else "stock"
    return market.InstrumentMeta(
        instrument_id=f"twse:{kind}:{symbol}",
        venue="twse",
        tick_size=_D("0.01"),
        lot_size=_D(1),
        min_qty=_D(1),
        status=enums.InstrumentStatus.TRADING,
        quote="TWD",
        calendar="XTAI",
        listing_exchange="TWSE",
        tick_ladder=TAIWAN_ETF_LADDER if etf else TAIWAN_STOCK_LADDER,
        price_limit_pct=_D(10),
        board_lot=_D(1000),
    )


def tpex(
    symbol: str = "6488", etf: bool = False, limited: bool = True
) -> market.InstrumentMeta:
    """Builds TPEx instrument metadata (as the market engine publishes it).

    Args:
        symbol: TPEx code.
        etf: ETF instead of stock.
        limited: False for listings without a daily limit (foreign-
            constituent ETFs such as 00679B, new listings' first days).

    Returns:
        Metadata with the TWSE tick ladders, the 10% limit (or none),
        1000-share board lots and the XTAI calendar.
    """
    kind = "etf" if etf else "stock"
    return market.InstrumentMeta(
        instrument_id=f"tpex:{kind}:{symbol}",
        venue="tpex",
        tick_size=_D("0.01"),
        lot_size=_D(1),
        min_qty=_D(1),
        status=enums.InstrumentStatus.TRADING,
        quote="TWD",
        calendar="XTAI",
        listing_exchange="TPEx",
        tick_ladder=TAIWAN_ETF_LADDER if etf else TAIWAN_STOCK_LADDER,
        price_limit_pct=_D(10) if limited else None,
        board_lot=_D(1000),
    )


def us(
    instrument_id: str = AAPL, listing_exchange: str = "Nasdaq"
) -> market.InstrumentMeta:
    """Builds US-listed instrument metadata.

    Args:
        instrument_id: Instrument id (``nasdaq:...`` or ``nyse:...``).
        listing_exchange: Listing exchange, e.g. ``NYSE Arca``.

    Returns:
        Metadata (cent ticks, single shares, no daily limit).
    """
    return market.InstrumentMeta(
        instrument_id=instrument_id,
        venue=instrument_id.split(":", 1)[0],
        tick_size=_D("0.01"),
        lot_size=_D(1),
        min_qty=_D(1),
        status=enums.InstrumentStatus.TRADING,
        quote="USD",
        calendar="XNYS",
        listing_exchange=listing_exchange,
    )


def treasury() -> market.InstrumentMeta:
    """Builds a Treasury yield series (never tradable).

    Returns:
        Metadata.
    """
    return market.InstrumentMeta(
        instrument_id=UST10Y,
        venue="treasury",
        tick_size=_D("0.01"),
        lot_size=_D(1),
        min_qty=_D(1),
        status=enums.InstrumentStatus.TRADING,
        calendar="USBOND",
    )


def at(day: datetime.date, hour: int, minute: int, tz: datetime.tzinfo) -> int:
    """Returns epoch milliseconds of a local wall-clock time.

    Args:
        day: Local date.
        hour: Hour.
        minute: Minute.
        tz: Time zone.

    Returns:
        UTC epoch milliseconds.
    """
    moment = datetime.datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=tz
    )
    return int(moment.timestamp() * 1000)


def taipei(day: datetime.date, hour: int, minute: int = 0) -> int:
    """Returns epoch milliseconds of a Taipei wall-clock time.

    Args:
        day: Date.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    return at(day, hour, minute, _TAIPEI)


def new_york(day: datetime.date, hour: int, minute: int = 0) -> int:
    """Returns epoch milliseconds of a New York (winter) wall-clock time.

    Args:
        day: Date.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    return at(day, hour, minute, _NEW_YORK_WINTER)


def _weekdays(start: datetime.date, days: int) -> list[datetime.date]:
    """Lists weekdays from a date.

    Args:
        start: First date.
        days: Calendar days to cover.

    Returns:
        Weekdays.
    """
    dates = (start + datetime.timedelta(days=i) for i in range(days))
    return [d for d in dates if d.weekday() < 5]


def xtai_sessions(
    start: datetime.date, days: int = 21
) -> list[market.TradingSession]:
    """Builds TWSE sessions (09:00-13:30 Taipei) on weekdays.

    Args:
        start: First date.
        days: Calendar days to cover.

    Returns:
        Sessions.
    """
    return [
        market.TradingSession(d.isoformat(), taipei(d, 9), taipei(d, 13, 30))
        for d in _weekdays(start, days)
    ]


def xnys_sessions(
    start: datetime.date, days: int = 21
) -> list[market.TradingSession]:
    """Builds NYSE sessions (09:30-16:00 New York, winter) on weekdays.

    Args:
        start: First date.
        days: Calendar days to cover.

    Returns:
        Sessions.
    """
    return [
        market.TradingSession(
            d.isoformat(), new_york(d, 9, 30), new_york(d, 16)
        )
        for d in _weekdays(start, days)
    ]


def install(
    gateway: market_static.StaticMarketDataGateway,
    start: datetime.date,
    *,
    tsmc_mark: str = "600",
    tsmc_close: str = "600",
    aapl_mark: str = "190",
) -> None:
    """Registers TWSE/TPEx/US instruments, quotes, closes and sessions.

    Args:
        gateway: Static gateway.
        start: First session date.
        tsmc_mark: 2330 mark.
        tsmc_close: 2330 previous close.
        aapl_mark: AAPL mark.
    """
    gateway.add_instrument(twse(), _D(tsmc_mark))
    gateway.add_instrument(twse("0050", etf=True), _D("150"))
    gateway.add_instrument(tpex(), _D("460"))
    gateway.add_instrument(tpex("00679B", etf=True, limited=False), _D("26"))
    gateway.add_instrument(us(), _D(aapl_mark))
    gateway.add_instrument(us(SPY, "NYSE Arca"), _D("580"))
    gateway.add_instrument(treasury(), _D("4.25"))
    gateway.set_previous_close(TSMC, _D(tsmc_close))
    gateway.set_previous_close(YUANTA_50, _D("150"))
    gateway.set_previous_close(GLOBALWAFERS, _D("441.5"))
    gateway.set_previous_close(YUANTA_US20, _D("26.15"))
    gateway.set_sessions("XTAI", xtai_sessions(start))
    gateway.set_sessions("XNYS", xnys_sessions(start))
