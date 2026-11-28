# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Taiwan Stock Exchange (TWSE) market-data adapter (official, free, no key).

Sources (all public, verified live 2026-11-23):

* Instruments: OpenAPI ``openapi.twse.com.tw/v1`` ``exchangeReport/
  STOCK_DAY_ALL`` (every listed security with the last session's values),
  ``opendata/t187ap03_L`` (listed companies), ``opendata/t187ap47_L``
  (listed funds, with the fund type and whether constituents are foreign),
  ``exchangeReport/TWT88U`` (first five trading days, no price limit) and
  ``exchangeReport/TWTAWU`` (trading suspensions). Common stocks and ETFs
  are listed; preferred shares, REITs, ETNs and the foreign-currency ETF
  counters (sixth code character K, M, S or C) are not.
* Daily history: ``www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY`` gives one
  month per request with ROC dates (``115/11/02``), comma-grouped numbers,
  volume in shares and raw (unadjusted) prices. Its own notes state that a
  day's statistics include regular, odd-lot, after-hours fixed-price and
  block trades (not auctions or tenders).
* Live data: MIS ``mis.twse.com.tw/stock/api/getStockInfo.jsp`` snapshots,
  batched (``ex_ch=tse_2330.tw|tse_0050.tw``). Cumulative volume ``v``, the
  latest tick volume ``tv`` and book sizes ``f``/``g`` are in board lots
  (1,000 shares) of the regular session; odd lots trade in a separate book
  (``getOddInfo.jsp``, sizes in shares) that is not used. ``z`` is a price
  only when a trade printed at the data time (``tlong``), otherwise ``-``;
  the ``trade`` object holds a recent trade (the MIS web page falls back to
  it) but was observed to lag behind the cumulative volume.
* MIS serves identical queries from a server cache: responses then carry
  ``exKey`` and ``cachedAlive`` (ms in cache). Polling one query every 5 s,
  109 of 120 responses were cached and per-symbol data times advanced every
  4-120 s. TWSE blocks clients above roughly 3 requests per 5 s (not
  published by TWSE; the figure reported by the twstock project), so each
  host (MIS, ``www`` and ``openapi``, which publishes no limit) is paced at
  one request per 2 s without bursts, at most 3 in any 5 s (token buckets
  set up in ``adapters/factory.py``). MIS also serves TPEx listings
  (``otc_6488.tw``, ``adapters/tpex.py``); both venues share the MIS
  bucket.
* Holidays: ``openapi.../holidaySchedule/holidaySchedule`` (the latest
  published year) and ``www.../rwd/zh/holidaySchedule/holidaySchedule``
  (per year). Entries named ...開始交易 or ...最後交易 are trading days;
  every other weekday entry is a closure added to the XTAI calendar.

``TaiwanExchangeAdapter`` holds what TWSE and TPEx share (MIS snapshots,
recording, monthly daily-report caching, XTAI alignment, the optional
SinoPac Shioaji source); ``TwseAdapter`` adds the TWSE listings, reports
and holiday schedule.

Intraday bars are reconstructed from MIS snapshots recorded while the
engine runs (``snapshot_bars``); there is no intraday history beyond what
was recorded. Recorded final minutes and their coverage are kept in a
``BarArchive`` (the trade recorder's database) so they survive restarts.
Bars are sparse (a minute or day without trades has no bar), so fetches
state their coverage: the recording's segments for intraday bars, the
STOCK_DAY months read for daily bars. Missing bars there are not gaps;
minutes the recorder did not see (before recording started, across a
polling gap) still are.

With ``JOSEPH_TW_DATA_SOURCE=sinopac`` (``adapters/sinopac.py``) intraday
bars come from Shioaji kbars and ticks, seconds bars and trades from its
tick history and stream, books and snapshots from its bid/ask and tick
stream. Listings, tick ladders, limits and daily, weekly and monthly bars
stay official. Whenever Shioaji cannot serve a request (no login, used-up
data allowance, outage, spent query budget) the official sources serve it;
each one-minute bar comes from one source, and an aggregate whose minutes
come from different sources is not emitted (its slot is reported missing).
"""

from __future__ import annotations

import asyncio
import bisect
import collections
import contextlib
import dataclasses
import datetime
import decimal
import html
import logging
import zoneinfo
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import TypeAlias

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import fugle as fugle_lib
from joseph.market_data.adapters import http
from joseph.market_data.adapters import sinopac as sinopac_lib
from joseph.market_data.app import bar_archive as bar_archive_lib
from joseph.market_data.domain import books
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import session_bars
from joseph.market_data.domain import snapshot_bars
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_LOG = logging.getLogger(__name__)

OPENAPI_URL = "https://openapi.twse.com.tw"
WWW_URL = "https://www.twse.com.tw"
MIS_URL = "https://mis.twse.com.tw"
DOCS_URL = "https://openapi.twse.com.tw/"
MIS_SOURCE = "twse.mis"
REST_SOURCE = "twse.rest"
MIS_MARKET = "tse"
"""MIS channel prefix of TWSE listings (``tse_2330.tw``)."""
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_TF = timeframes.MINUTE_AND_ABOVE
_M1 = timeframes.Timeframe.M1
_D1 = timeframes.Timeframe.D1
_PERIODIC = (timeframes.Timeframe.W1, timeframes.Timeframe.MN1)
_INTRADAY = tuple(tf for tf in _TF if tf.duration_ms < _D1.duration_ms)
BOARD_LOT = decimal.Decimal(1000)
_PRICE_LIMIT = decimal.Decimal(10)
_LEVERAGED_LIMIT = decimal.Decimal(20)
_ZERO_TICK = decimal.Decimal("0.01")
STOCK_TICKS: tuple[instruments.TickStep, ...] = tuple(
    instruments.TickStep(decimal.Decimal(p), decimal.Decimal(t))
    for p, t in (
        ("0", "0.01"),
        ("10", "0.05"),
        ("50", "0.1"),
        ("100", "0.5"),
        ("500", "1"),
        ("1000", "5"),
    )
)
"""TWSE stock ticks (營業細則 §62), confirmed by MIS quote ladders."""
ETF_TICKS: tuple[instruments.TickStep, ...] = (
    instruments.TickStep(decimal.Decimal("0"), decimal.Decimal("0.01")),
    instruments.TickStep(decimal.Decimal("50"), decimal.Decimal("0.05")),
)
"""TWSE ETF ticks (below 50 TWD 0.01, from 50 TWD 0.05)."""
_FOREIGN_CURRENCY = frozenset("KMSC")
_TRADING_DAY_MARKERS = ("開始交易", "最後交易")
_HISTORY_FLOOR = datetime.date(1999, 1, 1)
MAX_MONTHS_PER_CALL = 24
"""STOCK_DAY months read per call (one request each, newest kept)."""
COLD_MONTHS_PER_DAILY_CALL = 4
"""Uncached STOCK_DAY months one daily-bar call reads (about 6 s paced).

The www pace allows one request per 2 s, so a cold 500-bar daily chart
(24 months) took about 48 s, longer than the terminal's 30 s deadline.
A call now returns the newest months it read within this budget (the
months already cached cost nothing); the chart's "load older" requests
read the next months, so history fills in progressively.
"""
COLD_MONTHS_PER_PERIODIC_CALL = 10
"""Uncached months one weekly or monthly call reads (about 18 s paced)."""
_MONTH_TTL_MS = 30 * 60_000
"""An open month's cached report is re-read after this long."""
_DUE_MONTH_TTL_MS = 10 * 60_000
"""Re-read interval once today's closed session is missing from a month."""
_MIS_CACHE_MS = 4_000
"""Per-symbol reuse window (MIS data times advanced every 4 s at most)."""
_MIS_BATCH = 40
_WATCH_TTL_MS = 30 * 60_000
"""A code is recorded until this long after the last request for it."""
_PRE_OPEN_MS = 5 * 60_000
"""Recording starts this long before a session opens."""
_POST_CLOSE_MS = 2 * 60_000
"""Recording continues this long after a session closes."""
POLL_INTERVAL_S = 5.0
"""Default pause between recorder passes, in seconds."""
Sleep: TypeAlias = Callable[[float], Awaitable[None]]
_PERSIST_EVERY_MS = 60_000
"""MIS recordings are written to the bar archive at most this often."""
ARCHIVE_RETENTION_MS = 400 * 86_400_000
"""Archived one-minute bars older than this are pruned (daily)."""
MAX_INTRADAY_SESSIONS = 60
"""Sessions of Shioaji minute history read per call (newest kept)."""
_KBAR_CACHE_DAYS = 300
"""``(code, day)`` kbar sessions kept in memory (least recent out)."""
_TICK_CACHE_DAYS = 20
"""``(code, day)`` tick histories kept in memory (least recent out)."""
_TODAY_KBARS_EVERY_MS = 60_000
"""Today's kbars are re-read at most this often while the tick stream
proves minutes, five times less often before it does."""
_BOOK_WAIT_S = 2.0
"""Seconds a book request waits for Shioaji's first bid/ask of the day."""
_STREAM_IDLE_S = 1.0
"""Seconds without a feed event after which a stream sees ``idle``."""
_SEED_MS = 10_000


def roc_date(text: str) -> datetime.date:
    """Parses a Republic of China calendar date.

    Args:
        text: ``1151120`` or ``115/11/20`` (ROC year 115 = 2026).

    Returns:
        Gregorian date.

    Raises:
        ValueError: For malformed text.
    """
    digits = text.strip().replace("/", "")
    if len(digits) < 7 or not digits.isdigit():
        raise ValueError(f"not a ROC date: {text!r}")
    year = int(digits[:-4]) + 1911
    return datetime.date(year, int(digits[-4:-2]), int(digits[-2:]))


def listing_date(text: str) -> datetime.date:
    """Parses a listing date in either form TWSE publishes.

    ``t187ap03_L`` uses Gregorian ``19940905``; ``t187ap47_L`` uses ROC
    ``0920630`` (verified 2026-11-23).

    Args:
        text: Eight-digit Gregorian or seven-digit ROC date.

    Returns:
        Date.

    Raises:
        ValueError: For malformed text.
    """
    digits = text.strip()
    if len(digits) == 8 and digits.isdigit():
        return datetime.date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    return roc_date(digits)


def number(text: object) -> decimal.Decimal | None:
    """Parses a TWSE number (comma groups; dashes or empty text are missing).

    Args:
        text: Raw value.

    Returns:
        Decimal, or ``None`` when the value is missing (empty, one to four
        dashes, ``X0.00``) or not a number.
    """
    raw = str(text).replace(",", "").strip()
    if raw in ("", "-", "--", "---", "----", "X0.00"):
        return None
    try:
        return decimal.Decimal(raw)
    except decimal.InvalidOperation:
        return None


def taipei_ms(day: datetime.date) -> int:
    """Returns Taipei midnight of a date.

    Args:
        day: Taipei date.

    Returns:
        UTC epoch milliseconds.
    """
    moment = datetime.datetime.combine(day, datetime.time(0), tzinfo=_TPE)
    return int(moment.timestamp() * 1000)


def taipei_date(ts_ms: int) -> datetime.date:
    """Returns the Taipei date of an instant.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Local date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _TPE).date()


@dataclasses.dataclass(frozen=True, slots=True)
class ListingMeta:
    """Per-security listing facts used by the adapter.

    Attributes:
        name: Short Chinese name.
        listed: Listing date, when published.
        etf: True for exchange-traded funds.
    """

    name: str
    listed: datetime.date | None
    etf: bool


def _fund_is_foreign(fund: Mapping[str, Any]) -> bool:
    """Checks whether a fund holds foreign constituents (no price limit).

    Args:
        fund: ``t187ap47_L`` row.

    Returns:
        True when the fund reports foreign constituents.
    """
    return str(fund.get("是否包含國外成分股", "")).strip() == "是"


def etf_asset_class(
    code: str, fund: Mapping[str, Any]
) -> instruments.AssetClass:
    """Classifies an ETF as BOND or EQUITY.

    Args:
        code: Security code (``B`` as sixth character marks bond ETFs).
        fund: ``t187ap47_L`` row.

    Returns:
        ``BOND`` for a ``B`` code, a bond fund type (債券) or a bond index
        (債券/公債 without 股), else ``EQUITY``.
    """
    kind = str(fund.get("基金類型", ""))
    index = str(fund.get("標的指數/追蹤指數名稱", ""))
    if len(code) == 6 and code[5] == "B":
        return instruments.AssetClass.BOND
    if "債券" in kind:
        return instruments.AssetClass.BOND
    if ("債券" in index or "公債" in index) and "股" not in index:
        return instruments.AssetClass.BOND
    return instruments.AssetClass.EQUITY


def etf_price_limit(
    code: str, fund: Mapping[str, Any]
) -> decimal.Decimal | None:
    """Returns an ETF's daily price limit in percent.

    Verified with MIS limit prices 2026-11-23: funds with foreign
    constituents (e.g. 00646, 00865B, 00680L, 00635U) are unlimited (MIS
    shows 9999.95), domestic 2x leveraged funds (00631L) move 20 %, other
    domestic funds (0050, 00632R, 00400A) 10 %.

    Args:
        code: Security code.
        fund: ``t187ap47_L`` row.

    Returns:
        Limit, or ``None`` when unlimited.
    """
    if _fund_is_foreign(fund):
        return None
    if code.endswith("L"):
        return _LEVERAGED_LIMIT
    return _PRICE_LIMIT


def _unlimited_codes(
    rows: Iterable[Mapping[str, Any]], today: datetime.date
) -> set[str]:
    """Returns codes in their first five trading days (no price limit).

    Args:
        rows: ``TWT88U`` rows.
        today: Taipei date.

    Returns:
        Codes.
    """
    codes = set()
    for row in rows:
        try:
            fifth = roc_date(str(row.get("5thTradingDate", "")))
        except ValueError:
            continue
        if today <= fifth:
            codes.add(str(row.get("SecurCode", "")).strip())
    return codes


def _halted_codes(
    rows: Iterable[Mapping[str, Any]], today: datetime.date
) -> set[str]:
    """Returns codes whose trading is suspended today.

    Args:
        rows: ``TWTAWU`` rows.
        today: Taipei date.

    Returns:
        Codes.
    """
    codes = set()
    for row in rows:
        try:
            halted = roc_date(str(row.get("TradingHaltDate", "")))
        except ValueError:
            continue
        try:
            resumes = roc_date(str(row.get("TradingResumptionDate", "")))
        except ValueError:
            resumes = None
        if halted <= today and (resumes is None or today < resumes):
            codes.add(str(row.get("Code", "")).strip())
    return codes


def parse_instruments(
    day_all: Iterable[Mapping[str, Any]],
    companies: Iterable[Mapping[str, Any]],
    funds: Iterable[Mapping[str, Any]],
    first_five: Iterable[Mapping[str, Any]],
    halts: Iterable[Mapping[str, Any]],
    *,
    now_ms: int,
) -> tuple[list[instruments.Instrument], dict[str, ListingMeta]]:
    """Builds TWSE stock and ETF instruments.

    Args:
        day_all: ``STOCK_DAY_ALL`` rows (the listed universe).
        companies: ``t187ap03_L`` rows.
        funds: ``t187ap47_L`` rows.
        first_five: ``TWT88U`` rows.
        halts: ``TWTAWU`` rows.
        now_ms: Fetch time.

    Returns:
        Instruments and listing metadata per code.
    """
    today = taipei_date(now_ms)
    by_company = {str(r.get("公司代號", "")).strip(): r for r in companies}
    by_fund = {str(r.get("基金代號", "")).strip(): r for r in funds}
    unlimited = _unlimited_codes(first_five, today)
    halted = _halted_codes(halts, today)
    rows: list[instruments.Instrument] = []
    meta: dict[str, ListingMeta] = {}
    for record in day_all:
        code = str(record.get("Code", "")).strip()
        name = str(record.get("Name", "")).strip()
        company = by_company.get(code)
        fund = by_fund.get(code)
        english = ""
        if company is not None:
            english = str(company.get("英文簡稱") or "")
            market_type = instruments.MarketType.STOCK
            ladder = STOCK_TICKS
            asset = instruments.AssetClass.EQUITY
            limit: decimal.Decimal | None = _PRICE_LIMIT
            listed_text = str(company.get("上市日期", ""))
        elif fund is not None and not (
            len(code) == 6 and code[5] in _FOREIGN_CURRENCY
        ):
            english = str(fund.get("基金英文名稱") or "")
            market_type = instruments.MarketType.ETF
            ladder = ETF_TICKS
            asset = etf_asset_class(code, fund)
            limit = etf_price_limit(code, fund)
            listed_text = str(fund.get("上市日期", ""))
        else:
            continue
        try:
            listed: datetime.date | None = listing_date(listed_text)
        except ValueError:
            listed = None
        if code in unlimited:
            limit = None
        instrument_id = instruments.InstrumentId(
            instruments.Venue.TWSE, market_type, code
        )
        rows.append(
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=instrument_id,
                    canonical_base=code,
                    quote="TWD",
                    settlement="TWD",
                ),
                display_symbol=code,
                tick_size=_ZERO_TICK,
                lot_size=decimals.ONE,
                min_qty=decimals.ONE,
                contract_multiplier=decimals.ONE,
                linear=True,
                status=(
                    instruments.InstrumentStatus.HALTED
                    if code in halted
                    else instruments.InstrumentStatus.TRADING
                ),
                asset_class=asset,
                source=REST_SOURCE,
                as_of_ms=now_ms,
                listing_exchange="TWSE",
                tick_ladder=ladder,
                price_limit_pct=limit,
                board_lot=BOARD_LOT,
                name=html.unescape(english).strip() or None,
                local_name=html.unescape(name) or None,
            )
        )
        meta[code] = ListingMeta(
            name, listed, market_type is instruments.MarketType.ETF
        )
    return rows, meta


def schedule_closures(
    entries: Iterable[tuple[datetime.date, str]], today: datetime.date
) -> set[datetime.date]:
    """Selects future weekday closures from TWSE holiday-schedule entries.

    Args:
        entries: ``(date, name)`` pairs.
        today: Taipei date (earlier dates are covered by the table).

    Returns:
        Weekday dates without a session.
    """
    return {
        day
        for day, name in entries
        if day >= today
        and day.weekday() < 5
        and not any(marker in name for marker in _TRADING_DAY_MARKERS)
    }


def parse_holidays_openapi(
    payload: Iterable[Mapping[str, Any]],
) -> list[tuple[datetime.date, str]]:
    """Parses ``openapi.../holidaySchedule/holidaySchedule``.

    Args:
        payload: Rows ``{Name, Date: "1160101", Weekday, Description}``.

    Returns:
        ``(date, name)`` pairs.
    """
    out = []
    for row in payload:
        with contextlib.suppress(ValueError):
            out.append(
                (roc_date(str(row.get("Date", ""))), str(row.get("Name", "")))
            )
    return out


def parse_holidays_rwd(
    payload: Mapping[str, Any],
) -> list[tuple[datetime.date, str]]:
    """Parses ``www.../rwd/zh/holidaySchedule/holidaySchedule``.

    Args:
        payload: ``{stat, data: [["2026-01-01", name, description], ...]}``.

    Returns:
        ``(date, name)`` pairs (empty when the year is not published).
    """
    out = []
    for row in payload.get("data") or []:
        with contextlib.suppress(ValueError, IndexError, TypeError):
            out.append((datetime.date.fromisoformat(str(row[0])), str(row[1])))
    return out


def parse_stock_day(
    payload: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    now_ms: int,
) -> list[market.Candle]:
    """Converts one ``STOCK_DAY`` month to daily candles.

    Args:
        payload: ``{stat: "OK", fields, data: [[date, shares, value, open,
            high, low, close, change, transactions, note], ...]}``.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Ascending candles, final once their session has closed (days
        without a trade or outside XTAI sessions are skipped; empty unless
        ``stat`` is ``OK``).
    """
    if str(payload.get("stat", "")).upper() != "OK":
        return []
    calendar = sessions.XTAI
    out = []
    for row in payload.get("data") or []:
        try:
            day = roc_date(str(row[0]))
        except (ValueError, IndexError):
            continue
        prices = [number(row[i]) for i in (3, 4, 5, 6)]
        volume = number(row[1])
        if any(p is None for p in prices) or volume is None:
            continue
        session = calendar.session(day)
        if session is None:
            _LOG.debug("twse %s: %s is not an XTAI session", instrument_id, day)
            continue
        open_, high, low, close = (p for p in prices if p is not None)
        count = number(row[8])
        out.append(
            session_bars.candle(
                instrument_id=instrument_id,
                timeframe=_D1,
                start_ms=session.open_ms,
                open_=open_,
                high=high,
                low=low,
                close=close,
                base_volume=volume,
                now_ms=now_ms,
                source=REST_SOURCE,
                is_final=session.close_ms <= now_ms,
                quote_turnover=number(row[2]),
                trade_count=None if count is None else int(count),
            )
        )
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class MisQuote:
    """Parsed MIS record of one security.

    Attributes:
        code: Security code.
        snapshot: Snapshot for bar building.
        previous_close: Reference price ``y``.
        bids: Bid ``(price, shares)`` levels, best first.
        asks: Ask ``(price, shares)`` levels, best first.
        received_ms: Receipt time (epoch ms).
    """

    code: str
    snapshot: snapshot_bars.Snapshot
    previous_close: decimal.Decimal | None
    bids: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    asks: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    received_ms: int

    @property
    def last(self) -> decimal.Decimal | None:
        """The newest known trade price (``z``, else ``trade.z``)."""
        snap = self.snapshot
        return (
            snap.last_price if snap.last_price is not None else snap.trade_price
        )


def _levels(
    prices: object, sizes: object
) -> tuple[tuple[decimal.Decimal, decimal.Decimal], ...]:
    """Parses ``a``/``f`` (or ``b``/``g``) underscore lists.

    Market-order queues (price 0) and missing values are skipped; sizes are
    converted from board lots to shares.

    Args:
        prices: ``"2035.0000_2040.0000_..._"``.
        sizes: ``"141_235_..._"`` in board lots.

    Returns:
        Levels in the published order.
    """
    out = []
    for price_text, size_text in zip(
        str(prices).split("_"), str(sizes).split("_"), strict=False
    ):
        price, size = number(price_text), number(size_text)
        if price is None or size is None or price <= 0:
            continue
        out.append((price, size * BOARD_LOT))
    return tuple(out)


def _clock_ms(day: datetime.date, text: object) -> int | None:
    """Converts an ``HH:MM:SS`` Taipei time on ``day`` to epoch ms.

    Args:
        day: Trading date.
        text: Time text.

    Returns:
        Epoch milliseconds, or ``None`` when malformed.
    """
    try:
        moment = datetime.time.fromisoformat(str(text))
    except ValueError:
        return None
    local = datetime.datetime.combine(day, moment, tzinfo=_TPE)
    return int(local.timestamp() * 1000)


def parse_mis_record(
    record: Mapping[str, Any], received_ms: int
) -> MisQuote | None:
    """Parses one ``msgArray`` record of ``getStockInfo.jsp``.

    Args:
        record: Record (unknown symbols come back as ``{"c": "", ...}``).
        received_ms: Receipt time.

    Returns:
        Quote, or ``None`` for an empty or malformed record.
    """
    code = str(record.get("c", "")).strip()
    tlong = str(record.get("tlong", "")).strip()
    day_text = str(record.get("d", "")).strip()
    if not code or not tlong.isdigit() or len(day_text) != 8:
        return None
    try:
        day = datetime.date(
            int(day_text[:4]), int(day_text[4:6]), int(day_text[6:])
        )
    except ValueError:
        return None
    trade = record.get("trade")
    trade_price = trade_ms = None
    if isinstance(trade, Mapping):
        trade_price = number(trade.get("z"))
        trade_ms = _clock_ms(day, trade.get("t"))
    lots = number(record.get("v")) or decimals.ZERO
    bids = _levels(record.get("b", ""), record.get("g", ""))
    asks = _levels(record.get("a", ""), record.get("f", ""))
    snapshot = snapshot_bars.Snapshot(
        day=day,
        data_ms=int(tlong),
        cumulative_volume=lots * BOARD_LOT,
        last_price=number(record.get("z")),
        trade_price=trade_price,
        trade_ms=trade_ms,
        day_open=number(record.get("o")),
        day_high=number(record.get("h")),
        day_low=number(record.get("l")),
        best_bid=bids[0][0] if bids else None,
        best_ask=asks[0][0] if asks else None,
    )
    return MisQuote(
        code=code,
        snapshot=snapshot,
        previous_close=number(record.get("y")),
        bids=bids,
        asks=asks,
        received_ms=received_ms,
    )


def mis_key(code: str, market: str = MIS_MARKET) -> str:
    """Returns the MIS channel of a security.

    Args:
        code: Security code.
        market: MIS market prefix (``tse`` for TWSE, ``otc`` for TPEx).

    Returns:
        ``{market}_{code}.tw`` (e.g. ``tse_2330.tw``).
    """
    return f"{market}_{code}.tw"


class MisClient:
    """Batched, cached and coalesced MIS snapshot requests.

    Callers arriving within ``coalesce_s`` of each other share one round of
    requests (``batch`` symbols each), and a symbol fetched within
    ``cache_ms`` is served from memory, so screening and the recorder stay
    within TWSE's limits. MIS serves TWSE (``tse_``) and TPEx (``otc_``)
    listings from the same host; one client serves one market.

    Attributes:
        requests: MIS requests sent so far.
    """

    def __init__(
        self,
        http_client: http.VenueHttp,
        clock: clock_lib.Clock,
        *,
        cache_ms: int = _MIS_CACHE_MS,
        batch: int = _MIS_BATCH,
        coalesce_s: float = 0.02,
        market: str = MIS_MARKET,
    ) -> None:
        """Initializes the client.

        Args:
            http_client: HTTP client for ``MIS_URL``.
            clock: Time source.
            cache_ms: Reuse window per symbol, in milliseconds.
            batch: Symbols per request.
            coalesce_s: Seconds to wait before a request so concurrent
                callers join it.
            market: MIS market prefix of the channels (``tse``/``otc``).
        """
        self._http = http_client
        self._clock = clock
        self._cache_ms = cache_ms
        self._batch = batch
        self._coalesce_s = coalesce_s
        self._market = market
        self._cache: dict[str, MisQuote | None] = {}
        self._cached_at: dict[str, int] = {}
        self._pending: set[str] = set()
        self._waiters: list[asyncio.Future[None]] = []
        self._flusher: asyncio.Task[None] | None = None
        self.requests = 0

    async def quotes(self, codes: Sequence[str]) -> dict[str, MisQuote]:
        """Returns the latest MIS quote of each code.

        Args:
            codes: Security codes.

        Returns:
            Quotes of the codes MIS knows.

        Raises:
            errors.JosephError: When the round that fetched the codes
                failed (propagated from the request).
        """
        now = self._clock.now_ms()
        missing = [
            code
            for code in dict.fromkeys(codes)
            if now - self._cached_at.get(code, -self._cache_ms)
            >= self._cache_ms
        ]
        if missing:
            future: asyncio.Future[None] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending.update(missing)
            self._waiters.append(future)
            if self._flusher is None or self._flusher.done():
                self._flusher = asyncio.create_task(self._flush())
            await future
        return {
            code: quote
            for code in codes
            if (quote := self._cache.get(code)) is not None
        }

    async def _flush(self) -> None:
        """Sends pending symbols in batches until none are left.

        Callers that arrive while a batch is in flight are served by the
        next round.
        """
        while self._pending:
            await asyncio.sleep(self._coalesce_s)
            codes = sorted(self._pending)
            waiters = self._waiters
            self._pending = set()
            self._waiters = []
            failure: errors.JosephError | None = None
            try:
                for i in range(0, len(codes), self._batch):
                    await self._fetch(codes[i : i + self._batch])
            except errors.JosephError as err:
                failure = err
            for waiter in waiters:
                if waiter.done():
                    continue
                if failure is None:
                    waiter.set_result(None)
                else:
                    waiter.set_exception(failure)

    async def _fetch(self, codes: Sequence[str]) -> None:
        """Requests one batch.

        Args:
            codes: Security codes (at most ``batch``).

        Raises:
            errors.VenueUnavailableError: When MIS reports an error.
        """
        self.requests += 1
        payload = await self._http.get(
            "/stock/api/getStockInfo.jsp",
            params={
                "ex_ch": "|".join(
                    mis_key(code, self._market) for code in codes
                ),
                "json": "1",
                "delay": "0",
                "_": str(self._clock.now_ms()),
            },
        )
        if not isinstance(payload, Mapping) or payload.get("rtcode") != "0000":
            raise errors.VenueUnavailableError(
                f"{self._market} MIS returned an error"
            )
        now = self._clock.now_ms()
        for code in codes:
            self._cache[code] = None
            self._cached_at[code] = now
        for record in payload.get("msgArray") or []:
            quote = parse_mis_record(record, now)
            if quote is not None:
                self._cache[quote.code] = quote
                self._cached_at[quote.code] = now


MIS_NOTES: tuple[str, ...] = (
    "Intraday bars (1m and above) are rebuilt from MIS "
    "snapshots recorded while the engine runs; there is no "
    "intraday history before recording started. They count "
    "regular board-lot trading only (no odd lots).",
    "MIS data refreshes irregularly: per-symbol data times "
    "advanced every 4-120 s in testing and identical queries are "
    "served from a server cache. Volume between two snapshots is "
    "assigned to the bar just before the newer one; highs and "
    "lows printed between snapshots of different bars can be "
    "missed (they are exact when the day high/low moves inside "
    "one bar), and volume without a known trade price leaves "
    "that bar without buy volume (or without the bar).",
    "MIS publishes no trade feed or aggressor side: derived "
    "trades (volume increments) are classified with the quote "
    "rule against the previous snapshot's best bid/ask and the "
    "tick test as fallback (taker_buy_method = classified); "
    "they are not exposed as public trades.",
    "Order books are MIS 5-level snapshots (sizes converted "
    "from board lots to shares); there is no WebSocket, no "
    "funding, open interest or taker volume.",
)
"""Capability notes shared by the venues whose live data is MIS."""
MIS_SECONDS_NOTE = (
    "Seconds timeframes are not offered: 5-second snapshots cannot make "
    "honest seconds bars."
)
"""Seconds note of the official sources (MIS)."""
_FALLBACK_SECONDS_NOTE = (
    "Fallback: MIS snapshots cannot make honest seconds bars, so "
    "seconds bars need Shioaji."
)
"""Replaces ``MIS_SECONDS_NOTE`` when Shioaji serves seconds bars."""


def sinopac_notes(config: sinopac_lib.SinopacConfig) -> tuple[str, ...]:
    """Capability notes of the SinoPac Shioaji source.

    Args:
        config: Shioaji settings (budgets).

    Returns:
        Notes.
    """
    return (
        "Bars, trades and books come from SinoPac Shioaji "
        "(JOSEPH_TW_DATA_SOURCE=sinopac with a Shioaji API key; "
        f"{sinopac_lib.DOCS_URL}): 1m from Shioaji kbars (history from "
        "2020-03-02; 3m-12h aggregated on the XTAI grid), live 1m and "
        "seconds (1s-30s) bars from the tick stream, seconds history from "
        f"the per-day tick history of the newest "
        f"{config.seconds_history_days} sessions, books from the 5-level "
        "bid/ask stream. Volumes are board lots in shares; odd lots, "
        "after-hours fixed-price and block trades are excluded. Data is "
        "real time (no delay). Listings, tick ladders, price limits and "
        "names stay the official lists; 1d, 1w and 1M stay the official "
        "daily reports.",
        "Buy volume is Shioaji's tick_type (1 外盤 at the ask: buy, 2 內盤 "
        "at the bid: sell, 0 undetermined). SinoPac does not document it "
        "as the exchange's aggressor flag (code 0 '無法判定' marks a "
        "classification, and its C# docs swap 1 and 2), so "
        "taker_buy_method = classified; a bar with an undetermined tick "
        "has no buy volume. Public trades carry the same classified side.",
        "Shioaji quotas: 50 market-data queries per 10 s; in trading hours "
        "at most 10 tick-history and 270 kbar queries a day (this engine "
        f"spends at most {config.intraday_ticks_calls} and "
        f"{config.intraday_kbars_calls}); a daily data allowance of 500 MB "
        "(2 GB / 10 GB with 30-day API trading value 1-100M / over 100M "
        "TWD), reset 08:00 Taipei, beyond which queries come back empty; "
        "200 subscriptions (a tick and a bid/ask one per code, at most "
        f"{config.max_symbols} codes); 5 connections per person (the "
        "execution engine uses one) and 1,000 logins a day.",
        "Coverage: the tick stream's cumulative day volume proves that no "
        "trade was missed (a jump starts a new segment whose first bar is "
        "partial); bid/ask updates prove delivery up to 2 s before their "
        "time. Kbars are labeled by the end of their minute; Shioaji "
        "timestamps are Taipei wall-clock time (inferred from SinoPac's "
        "examples).",
        "Fallback: when Shioaji cannot serve (login, data allowance, "
        "outage, query budget, missing SDK) the official sources below "
        "serve bars, books and snapshots (source twse.mis or tpex.mis); a "
        "bar never mixes sources, an aggregate spanning both is not "
        "emitted, and live sessions show feed_state POLL, transport "
        "REST_POLL and DEGRADED health with the reason. Seconds bars and "
        "trades need Shioaji.",
    )


def month_complete(year: int, month: int, read_ms: int) -> bool:
    """Checks whether a monthly report read at a time held the whole month.

    A month is complete once it has ended: a read on its own last days (or
    the 29th-31st of a short read) may still miss the sessions after it.

    Args:
        year: Year.
        month: Month.
        read_ms: When the report was read.

    Returns:
        True when the read happened on or after the next month's first day
        (Taipei), so the cached report can never change.
    """
    following = datetime.date(year + month // 12, month % 12 + 1, 1)
    return taipei_date(read_ms) >= following


def _span_end(spans: Sequence[candle_rules.Span], ts_ms: int) -> int | None:
    """Returns the end of the span containing an instant.

    Args:
        spans: Disjoint ascending spans.
        ts_ms: Instant.

    Returns:
        Span end, or ``None`` when no span contains it.
    """
    for low, high in spans:
        if low <= ts_ms < high:
            return high
    return None


def _overlaps(spans: Sequence[candle_rules.Span], low: int, high: int) -> bool:
    """Checks whether ``[low, high)`` overlaps any span.

    Args:
        spans: Spans.
        low: Inclusive start.
        high: Exclusive end.

    Returns:
        True on overlap.
    """
    return any(a < high and b > low for a, b in spans)


def provider(candle: market.Candle) -> str:
    """Returns the source family of a bar (``sinopac``, ``twse``, ...).

    Args:
        candle: Candle.

    Returns:
        The part of ``source`` before the first dot.
    """
    return candle.source.split(".", 1)[0]


Layer: TypeAlias = tuple[list[market.Candle], list[candle_rules.Span]]
"""One source's one-minute bars and the whole minutes it covers."""


def combine_layers(layers: Sequence[Layer]) -> Layer:
    """Combines one-minute bars of several sources, best source first.

    A source's bar is kept only for a minute no better source has a bar
    for or covers; its coverage only where no better source's coverage
    is, so a minute is covered by exactly one source.

    Args:
        layers: ``(bars, whole-minute spans)`` per source, preferred first.

    Returns:
        Ascending bars and the merged coverage.
    """
    claimed: list[candle_rules.Span] = []
    taken: dict[int, market.Candle] = {}
    spans: list[candle_rules.Span] = []
    for bars, covered in layers:
        for bar in bars:
            if bar.start_ms in taken or _overlaps(
                claimed, bar.start_ms, bar.end_ms
            ):
                continue
            taken[bar.start_ms] = bar
        spans.extend(candle_rules.subtract_spans(covered, claimed))
        claimed = candle_rules.merge_spans([*claimed, *covered])
    return [taken[s] for s in sorted(taken)], candle_rules.merge_spans(spans)


def aggregate_minutes(
    minutes: Sequence[market.Candle],
    timeframe: timeframes.Timeframe,
    covered: Sequence[candle_rules.Span],
    now_ms: int,
    covered_to: Callable[[int], int | None] | None = None,
) -> Layer:
    """Aggregates one-minute bars on the XTAI grid, one source per bar.

    Args:
        minutes: One-minute bars (any order).
        timeframe: Target intraday resolution.
        covered: Coverage of the minutes.
        now_ms: Receipt time.
        covered_to: Maps a bar start to the time its minutes are complete
            through (``None`` skips the bar); by default the end of the
            coverage span holding the start.

    Returns:
        Aggregates and the coverage without the slots of bars that would
        mix sources (they are not emitted); for ``1m`` the minutes as given
        and the merged coverage.
    """
    spans = candle_rules.merge_spans(covered)
    if timeframe is _M1:
        return list(minutes), spans
    calendar = sessions.XTAI
    groups: dict[int, list[market.Candle]] = {}
    for bar in sorted(minutes, key=lambda c: c.start_ms):
        groups.setdefault(calendar.floor(timeframe, bar.start_ms), []).append(
            bar
        )
    out: list[market.Candle] = []
    holes: list[candle_rules.Span] = []
    for start, group in sorted(groups.items()):
        if len({provider(bar) for bar in group}) > 1:
            holes.append((start, calendar.end_of(timeframe, start)))
            continue
        if covered_to is None:
            through = _span_end(spans, start)
            through = start if through is None else through
        else:
            found = covered_to(start)
            if found is None:
                continue
            through = found
        out.extend(
            session_bars.aggregate(
                group, timeframe, now_ms=now_ms, covered_to_ms=through
            )
        )
    return out, candle_rules.subtract_spans(spans, holes)


def _touched_minutes(
    event: sinopac_lib.Event, minutes: dict[int, market.Candle]
) -> set[int]:
    """Returns the streamed minutes a feed event changes.

    A new segment drops the open tick-built minutes (they lost trades).

    Args:
        event: Feed event.
        minutes: Minutes held by the stream (pruned in place).

    Returns:
        Minute starts to rebuild.
    """
    if event.kind == "trade" and event.trade is not None:
        return {sessions.XTAI.floor(_M1, event.trade.venue_event_ts_ms)}
    if event.kind == "segment":
        stale = {
            start
            for start, bar in minutes.items()
            if not bar.is_final and provider(bar) == sinopac_lib.SOURCE
        }
        for start in stale:
            del minutes[start]
        return stale
    if event.kind == "proof":
        return {start for start, bar in minutes.items() if not bar.is_final}
    return set()


def _changed(before: market.Candle | None, after: market.Candle) -> bool:
    """Checks whether a streamed bar changed in values or finality.

    Args:
        before: Last emitted observation.
        after: New observation.

    Returns:
        True when it should be emitted.
    """
    if before is None:
        return True
    return (
        before.open,
        before.high,
        before.low,
        before.close,
        before.base_volume,
        before.is_final,
        before.taker_buy_base_volume,
    ) != (
        after.open,
        after.high,
        after.low,
        after.close,
        after.base_volume,
        after.is_final,
        after.taker_buy_base_volume,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _TodayKbars:
    """Kbars of today read during the session.

    Attributes:
        day: Trading date.
        bars: Final one-minute bars.
        covered_to_ms: Every minute ending by this time is in ``bars``.
        fetched_ms: When read.
    """

    day: datetime.date
    bars: list[market.Candle]
    covered_to_ms: int
    fetched_ms: int


@dataclasses.dataclass(slots=True)
class _Month:
    """Cached month of daily bars.

    Attributes:
        bars: Daily candles.
        fetched_ms: When read.
    """

    bars: list[market.Candle]
    fetched_ms: int


def _day_due(cached: _Month, now_ms: int) -> bool:
    """Checks whether a closed session is still missing from a month.

    Args:
        cached: Cached month.
        now_ms: Current time.

    Returns:
        True when today's session has closed but has no row yet.
    """
    session = sessions.XTAI.session(taipei_date(now_ms))
    if session is None or now_ms < session.close_ms:
        return False
    return all(bar.start_ms != session.open_ms for bar in cached.bars)


class TaiwanExchangeAdapter(base.BaseVenueAdapter):
    """``SparseBarsPort`` shared by the exchanges MIS serves (TWSE, TPEx).

    Daily history comes from the exchange's own monthly report
    (``_fetch_month``); intraday bars are recorded from MIS snapshots of
    the ``MIS_MARKET`` channel; both follow the XTAI calendar. Subclasses
    provide the listings, capabilities, holiday schedule, monthly report
    and bulk snapshots.

    Attributes:
        VENUE: Venue served (``twse``; subclasses override it).
        INSTRUMENT_TTL_MS: How long listings are cached (6 hours).
        TRADE_BUY_VOLUME_METHOD: ``classified``: neither MIS nor Shioaji
            publishes the exchange's aggressor side.
        SPARSE_BARS: True: a minute or day without trades has no bar.
        MIS_MARKET: MIS channel prefix of the venue's listings.
        MIS_SOURCE_NAME: Source stamped on MIS-built bars, snapshots and
            books.
        SJ_EXCHANGE: Shioaji contract group of the venue's listings.
        mis: MIS snapshot client of the venue's channel.
        listing: Listing facts per security code, refreshed with the
            instruments.
    """

    VENUE = instruments.Venue.TWSE
    INSTRUMENT_TTL_MS = 6 * 3_600_000
    TRADE_BUY_VOLUME_METHOD = market.BuyVolumeMethod.CLASSIFIED
    SPARSE_BARS = True
    MIS_MARKET = MIS_MARKET
    MIS_SOURCE_NAME = MIS_SOURCE
    SJ_EXCHANGE = "TSE"

    def __init__(
        self,
        openapi: http.VenueHttp,
        www: http.VenueHttp,
        mis: http.VenueHttp,
        clock: clock_lib.Clock,
        verified_at_ms: int | None = None,
        *,
        sleep: Sleep = asyncio.sleep,
        poll_interval_s: float = POLL_INTERVAL_S,
        sinopac: sinopac_lib.SinopacSource | None = None,
        fugle: fugle_lib.FugleSource | None = None,
        bar_archive: bar_archive_lib.BarArchive | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            openapi: Client for the exchange's OpenAPI.
            www: Client for the exchange's website reports.
            mis: Client for ``MIS_URL``.
            clock: Time source.
            verified_at_ms: When capabilities were last verified live.
            sleep: Sleep function of the recorder (injectable for tests).
            poll_interval_s: Minimum seconds between recorder passes.
            sinopac: Shared Shioaji source (``JOSEPH_TW_DATA_SOURCE=
                sinopac``), or ``None`` for the official sources only.
            fugle: Free-tier Fugle source for per-symbol candles and quotes.
            bar_archive: Durable store of recorded one-minute bars and
                their coverage (``None`` keeps them in memory only).
        """
        super().__init__(openapi, clock)
        self._www = www
        self._mis_http = mis
        self.mis = MisClient(mis, clock, market=self.MIS_MARKET)
        self._verified_at_ms = verified_at_ms
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._months: dict[tuple[str, int, int], _Month] = {}
        self._month_reads: dict[
            tuple[str, int, int], asyncio.Future[list[market.Candle]]
        ] = {}
        self._builders: dict[str, snapshot_bars.SnapshotBarBuilder] = {}
        self._watch: dict[str, int] = {}
        self._poller: asyncio.Task[None] | None = None
        self._calendar_day: datetime.date | None = None
        self.listing: dict[str, ListingMeta] = {}
        self._sinopac = sinopac
        self._fugle = fugle
        if sinopac is not None:
            sinopac.acquire()
        self._archive = bar_archive
        self._persisted: dict[str, int] = {}
        self._persisted_at: dict[str, int] = {}
        self._pruned_day: datetime.date | None = None
        self._kbar_days: collections.OrderedDict[
            tuple[str, datetime.date], list[market.Candle]
        ] = collections.OrderedDict()
        self._tick_days: collections.OrderedDict[
            tuple[str, datetime.date], sinopac_lib.DayTicks
        ] = collections.OrderedDict()
        self._today_kbars: dict[str, _TodayKbars] = {}
        self._fallback_reason: str | None = None

    def instrument_id(self, venue_market_id: str) -> instruments.InstrumentId:
        """Builds the canonical id of a listed code.

        Args:
            venue_market_id: Security code.

        Returns:
            Canonical id (``etf`` for listed funds, else ``stock``).
        """
        meta = self.listing.get(venue_market_id)
        market_type = (
            instruments.MarketType.ETF
            if meta is not None and meta.etf
            else instruments.MarketType.STOCK
        )
        return instruments.InstrumentId(
            self.VENUE, market_type, venue_market_id
        )

    async def refresh_calendar(self) -> set[datetime.date]:
        """Adds newly published closures to the XTAI calendar.

        Returns:
            Closures added (implemented by subclasses).

        Raises:
            NotImplementedError: Always in the base class.
        """
        raise NotImplementedError

    async def _fetch_month(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> list[market.Candle]:
        """Reads one month of the exchange's daily report.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            Daily candles of the month (implemented by subclasses).

        Raises:
            NotImplementedError: Always in the base class.
        """
        raise NotImplementedError

    # Daily history -----------------------------------------------------

    async def _month(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> list[market.Candle]:
        """Returns one month of daily bars, cached while it cannot change.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            Daily candles of the month. Concurrent reads of one month share
            a single request.
        """
        key = (instrument.venue_market_id, year, month)
        if self._month_cached(instrument, year, month):
            return self._months[key].bars
        running = self._month_reads.get(key)
        if running is None:
            running = asyncio.ensure_future(
                self._fetch_month(instrument, year, month)
            )
            self._month_reads[key] = running

            def done(finished: asyncio.Future[list[market.Candle]]) -> None:
                """Caches a finished read and forgets it.

                Args:
                    finished: The month's read.
                """
                if self._month_reads.get(key) is finished:
                    del self._month_reads[key]
                if not finished.cancelled() and finished.exception() is None:
                    self._months[key] = _Month(
                        finished.result(), self._clock.now_ms()
                    )

            running.add_done_callback(done)
        return await asyncio.shield(running)

    def _month_cached(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> bool:
        """Tells whether ``_month`` would answer without a request.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            True when the cached report is still valid.
        """
        cached = self._months.get((instrument.venue_market_id, year, month))
        if cached is None:
            return False
        if month_complete(year, month, cached.fetched_ms):
            return True
        now = self._clock.now_ms()
        ttl = _DUE_MONTH_TTL_MS if _day_due(cached, now) else _MONTH_TTL_MS
        return now - cached.fetched_ms < ttl

    async def _daily(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        wanted: int | None,
        cold_budget: int = MAX_MONTHS_PER_CALL,
    ) -> tuple[list[market.Candle], list[candle_rules.Span], bool]:
        """Collects daily bars of a range, newest months first.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            wanted: Stop once this many bars in range are collected.
            cold_budget: Most uncached months read (one paced request each);
                the call stops before the next one beyond it.

        Returns:
            Ascending final daily bars plus, during or after today's
            session, today's non-final bar from MIS when the monthly report
            has no row yet; the spans of the months read, each up to the
            day before it was read (a trading day there without a row had
            no trades); and whether every month of the range was read.
        """
        now = self._clock.now_ms()
        first = self._first_day(instrument, start_ms)
        last = taipei_date(min(end_ms - 1, now))
        rows: dict[int, market.Candle] = {}
        covered: list[candle_rules.Span] = []
        year, month = last.year, last.month
        months = 0
        cold = 0
        while (year, month) >= (
            first.year,
            first.month,
        ) and months < MAX_MONTHS_PER_CALL:
            if not self._month_cached(instrument, year, month):
                if cold >= cold_budget:
                    break
                cold += 1
            for bar in await self._month(instrument, year, month):
                rows[bar.start_ms] = bar
            months += 1
            following = datetime.date(year + month // 12, month % 12 + 1, 1)
            read_on = taipei_date(
                self._months[
                    (instrument.venue_market_id, year, month)
                ].fetched_ms
            )
            covered.append(
                (
                    taipei_ms(datetime.date(year, month, 1)),
                    taipei_ms(min(following, read_on)),
                )
            )
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
            # A month read can reveal the listing date (TPEx marks it in
            # its daily report), which moves the first month worth reading.
            first = self._first_day(instrument, start_ms)
            if (
                wanted is not None
                and sum(1 for s in rows if start_ms <= s < end_ms) >= wanted
            ):
                break
        complete = (year, month) < (first.year, first.month)
        today = sessions.XTAI.session(taipei_date(now))
        if (
            today is not None
            and today.open_ms <= now
            and today.open_ms not in rows
            and start_ms <= today.open_ms < end_ms
        ):
            live = await self._live_daily(instrument)
            if live is not None and live.start_ms == today.open_ms:
                rows[live.start_ms] = live
        return (
            [rows[s] for s in sorted(rows) if start_ms <= s < end_ms],
            covered,
            complete,
        )

    def _first_day(
        self, instrument: instruments.InstrumentId, start_ms: int
    ) -> datetime.date:
        """Returns the first date worth reading for a range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive range start.

        Returns:
            The latest of the range start, the listing date (when known;
            it can become known while months are read) and the floor.
        """
        meta = self.listing.get(instrument.venue_market_id)
        floor = max(
            _HISTORY_FLOOR,
            meta.listed if meta and meta.listed else _HISTORY_FLOOR,
        )
        return max(floor, taipei_date(max(start_ms, 0)))

    async def _live_daily(
        self, instrument: instruments.InstrumentId
    ) -> market.Candle | None:
        """Builds today's in-progress daily bar (Shioaji stream, else MIS).

        Args:
            instrument: Instrument.

        Returns:
            Non-final candle (regular-session volume only), or ``None``.
        """
        feed = await self._live_feed(instrument)
        if feed is not None:
            live = self._feed_daily(instrument, feed)
            if live is not None:
                return live
        quotes = await self.mis.quotes([instrument.venue_market_id])
        quote = quotes.get(instrument.venue_market_id)
        if quote is None:
            return None
        snap = quote.snapshot
        session = sessions.XTAI.session(snap.day)
        last = quote.last
        if session is None or last is None or snap.day_open is None:
            return None
        return session_bars.candle(
            instrument_id=instrument,
            timeframe=_D1,
            start_ms=session.open_ms,
            open_=snap.day_open,
            high=snap.day_high or last,
            low=snap.day_low or last,
            close=last,
            base_volume=snap.cumulative_volume,
            now_ms=self._clock.now_ms(),
            source=self.MIS_SOURCE_NAME,
            is_final=False,
            event_ts_ms=snap.data_ms,
        )

    # Intraday ----------------------------------------------------------

    def watch(self, code: str) -> None:
        """Records interest in a code so the recorder polls it.

        Starts the recorder task when it is not running.

        Args:
            code: Security code.
        """
        self._watch[code] = self._clock.now_ms()
        if code not in self._builders:
            self._builders[code] = snapshot_bars.SnapshotBarBuilder(
                self.instrument_id(code), source=self.MIS_SOURCE_NAME
            )
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self._poll_loop())

    def ingest(self, quotes: Iterable[MisQuote]) -> int:
        """Applies MIS quotes to the recorded bars.

        Args:
            quotes: Parsed quotes.

        Returns:
            Snapshots applied.
        """
        applied = 0
        for quote in quotes:
            builder = self._builders.get(quote.code)
            if builder is None:
                continue
            before = builder.applied
            builder.apply(quote.snapshot)
            applied += builder.applied - before
        return applied

    async def poll_once(self) -> int:
        """Polls every watched code once.

        Codes nobody requested for ``_WATCH_TTL_MS`` are dropped first; the
        recordings are then written to the archive when due.

        Returns:
            Snapshots applied.
        """
        now = self._clock.now_ms()
        for code in [
            c for c, t in self._watch.items() if now - t > _WATCH_TTL_MS
        ]:
            del self._watch[code]
        if not self._watch:
            return 0
        quotes = await self.mis.quotes(sorted(self._watch))
        applied = self.ingest(quotes.values())
        self.persist_recordings(now)
        return applied

    def persist_recordings(self, now_ms: int, *, force: bool = False) -> int:
        """Writes recorded final minutes and their coverage to the archive.

        Only whole minutes inside the recording's coverage are written,
        together with every bar there, so the archive never claims a quiet
        minute whose bar it lacks.

        Args:
            now_ms: Current time.
            force: Write even when the last write is recent.

        Returns:
            Bars written.
        """
        archive = self._archive
        if archive is None:
            return 0
        written = 0
        for code, builder in self._builders.items():
            if (
                not force
                and now_ms - self._persisted_at.get(code, 0) < _PERSIST_EVERY_MS
            ):
                continue
            self._persisted_at[code] = now_ms
            since = self._persisted.get(code, 0)
            spans = [
                aligned
                for span in builder.coverage()
                if (aligned := sinopac_lib.whole_minutes(span, grace=False))
                is not None
                and aligned[1] > since
            ]
            spans = candle_rules.clip_spans(spans, since, 2**63)
            if not spans:
                continue
            low, high = spans[0][0], spans[-1][1]
            bars = [
                bar
                for bar in builder.bars(low, high, now_ms)
                if bar.is_final
                and _span_end(spans, bar.start_ms) is not None
                and bar.end_ms <= high
            ]
            try:
                archive.put(
                    builder.instrument_id, self.MIS_SOURCE_NAME, bars, spans
                )
            except Exception as err:  # noqa: BLE001 - isolation point: a failing archive write must not stop recording
                _LOG.warning(
                    "%s: archiving recorded bars failed: %s",
                    self.VENUE.value,
                    type(err).__name__,
                )
                continue
            self._persisted[code] = high
            written += len(bars)
        self._prune_archive(now_ms)
        return written

    def _prune_archive(self, now_ms: int) -> None:
        """Prunes archived bars past the retention once a day.

        Args:
            now_ms: Current time.
        """
        today = taipei_date(now_ms)
        if self._archive is None or self._pruned_day == today:
            return
        self._pruned_day = today
        try:
            self._archive.prune(now_ms - ARCHIVE_RETENTION_MS)
        except Exception as err:  # noqa: BLE001 - isolation point: a failing prune must not stop recording
            _LOG.warning("bar archive prune failed: %s", type(err).__name__)

    def _recording_window(self, now_ms: int) -> int:
        """Returns milliseconds until the next recording window (0 if in).

        Args:
            now_ms: Current time.

        Returns:
            Delay before polling should resume (an hour when no session
            is left in the next 15 days).
        """
        calendar = sessions.XTAI
        for session in calendar.sessions(
            now_ms - 86_400_000, now_ms + 15 * 86_400_000
        ):
            start = session.open_ms - _PRE_OPEN_MS
            stop = session.close_ms + _POST_CLOSE_MS
            if now_ms < stop:
                return max(0, start - now_ms)
        return 3_600_000

    async def _poll_loop(self) -> None:
        """Polls watched codes during sessions until nothing is watched."""
        while self._watch:
            delay_ms = self._recording_window(self._clock.now_ms())
            if delay_ms > 0:
                await self._sleep(min(60.0, delay_ms / 1000))
                continue
            try:
                await self.poll_once()
            except errors.JosephError as err:
                _LOG.warning(
                    "%s MIS poll failed: %s", self.VENUE.value, err.message
                )
            await self._sleep(self._poll_interval_s)

    def _intraday(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> Layer:
        """Returns recorded intraday bars.

        Args:
            instrument: Instrument.
            timeframe: Intraday resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending candles inside coverage segments (archived minutes
            of earlier recordings included) and the coverage.
        """
        builder = self._builders[instrument.venue_market_id]
        now = self._clock.now_ms()
        calendar = sessions.XTAI
        try:
            first = calendar.floor(timeframe, start_ms)
        except ValueError:
            first = start_ms
        ones, covered = self._mis_minutes(instrument, first, end_ms)

        def covered_to(start: int) -> int | None:
            """Returns how far the minutes of a bar are recorded.

            Args:
                start: Bar start.

            Returns:
                Recording or archive end, or ``None`` outside both.
            """
            segment = builder.segment_at(start)
            if segment is not None:
                return segment[1]
            return _span_end(covered, start)

        if timeframe is _M1:
            return ones, covered
        return aggregate_minutes(ones, timeframe, covered, now, covered_to)

    def _mis_minutes(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> Layer:
        """Returns recorded MIS minutes, live and archived.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            One-minute bars (the live recording wins over the archive) and
            their coverage.
        """
        now = self._clock.now_ms()
        builder = self._builders.get(instrument.venue_market_id)
        live = [] if builder is None else builder.bars(start_ms, end_ms, now)
        covered = [] if builder is None else builder.coverage()
        if self._archive is None:
            return live, candle_rules.merge_spans(covered)
        try:
            stored = self._archive.minutes(
                instrument, self.MIS_SOURCE_NAME, start_ms, end_ms
            )
            stored_spans = self._archive.coverage(
                instrument, self.MIS_SOURCE_NAME, start_ms, end_ms
            )
        except Exception as err:  # noqa: BLE001 - isolation point: a failing archive read leaves the live recording served
            _LOG.warning("bar archive read failed: %s", type(err).__name__)
            return live, candle_rules.merge_spans(covered)
        by_start = {bar.start_ms: bar for bar in stored}
        by_start.update({bar.start_ms: bar for bar in live})
        return (
            [by_start[s] for s in sorted(by_start)],
            candle_rules.merge_spans([*covered, *stored_spans]),
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches daily history or recorded intraday bars.

        Intraday requests also start (or keep alive) MIS recording of the
        instrument, so later calls return more bars.

        Args:
            instrument: Instrument.
            timeframe: Resolution (1m and above).
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles on the XTAI calendar.
        """
        fetched = await self.fetch_candle_coverage(
            instrument, timeframe, start_ms, end_ms, limit
        )
        return list(fetched.candles)

    async def fetch_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> candle_rules.CandleCoverage:
        """Fetches daily history or recorded intraday bars with coverage.

        Args:
            instrument: Instrument.
            timeframe: Resolution (1m and above).
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles on the XTAI calendar and the spans proven
            complete: the Shioaji or MIS minutes for intraday bars, the
            Shioaji ticks for seconds bars, the monthly reports read for
            daily, weekly and monthly bars.

        Raises:
            errors.NotFoundError: When the venue does not list the
                instrument.
            errors.UnsupportedError: For a timeframe the venue does not
                serve, or seconds bars without Shioaji.
        """
        self._require_timeframe(timeframe)
        await self._require(instrument)
        if self._fugle is not None:
            return await self._fugle.fetch_candle_coverage(
                instrument, timeframe, start_ms, end_ms, limit
            )
        if end_ms <= start_ms:
            return candle_rules.CandleCoverage((), (), complete=True)
        if timeframe.is_seconds:
            return await self._seconds_history(
                instrument, timeframe, start_ms, end_ms, limit
            )
        if timeframe in _INTRADAY:
            if self._sinopac_on:
                minutes, covered, complete = await self._sinopac_minutes(
                    instrument, timeframe, start_ms, end_ms, limit
                )
                rows, covered = aggregate_minutes(
                    minutes, timeframe, covered, self._clock.now_ms()
                )
                return base.candle_coverage(
                    rows, start_ms, end_ms, limit, covered, complete=complete
                )
            code = instrument.venue_market_id
            self.watch(code)
            rows, covered = self._intraday(
                instrument, timeframe, start_ms, end_ms
            )
            return base.candle_coverage(
                rows, start_ms, end_ms, limit, covered, complete=True
            )
        calendar = sessions.XTAI
        try:
            grid = calendar.floor(timeframe, start_ms)
        except ValueError:
            grid = start_ms
        per_bar = {
            _D1: 1,
            timeframes.Timeframe.W1: 5,
            timeframes.Timeframe.MN1: 23,
        }
        wanted = None if limit is None else limit * per_bar[timeframe]
        daily, covered, complete = await self._daily(
            instrument,
            min(grid, start_ms),
            end_ms,
            wanted,
            COLD_MONTHS_PER_DAILY_CALL
            if timeframe is _D1
            else COLD_MONTHS_PER_PERIODIC_CALL,
        )
        if timeframe is not _D1:
            finals = [bar.end_ms for bar in daily if bar.is_final]
            daily = session_bars.aggregate(
                daily,
                timeframe,
                now_ms=self._clock.now_ms(),
                covered_to_ms=max(finals, default=0),
            )
        return base.candle_coverage(
            daily, start_ms, end_ms, limit, covered, complete=complete
        )

    # SinoPac Shioaji ---------------------------------------------------

    @property
    def _sinopac_on(self) -> bool:
        """Whether an enabled Shioaji source is configured."""
        return self._sinopac is not None and self._sinopac.enabled

    def _with_sinopac(
        self, caps: capabilities.VenueCapabilities
    ) -> capabilities.VenueCapabilities:
        """Adds what the Shioaji source serves to the official capabilities.

        Args:
            caps: Capabilities of the official sources.

        Returns:
            Capabilities (unchanged without a Shioaji source).
        """
        source = self._sinopac
        if source is None:
            return self._with_fugle(caps)
        if not source.enabled:
            return dataclasses.replace(
                caps,
                notes=(
                    *caps.notes,
                    "JOSEPH_TW_DATA_SOURCE=sinopac is set but Shioaji is "
                    f"disabled ({source.unavailable()}): official sources "
                    "only.",
                ),
            )
        notes = tuple(
            _FALLBACK_SECONDS_NOTE if note == MIS_SECONDS_NOTE else note
            for note in caps.notes
        )
        return dataclasses.replace(
            caps,
            display_name=f"{caps.display_name} (SinoPac Shioaji)",
            market_data=dataclasses.replace(
                caps.market_data,
                native_ws_candles=True,
                native_ws_trades=True,
                trades_history=True,
            ),
            timeframes=(*timeframes.SECONDS, *caps.timeframes),
            trade_built_timeframes=timeframes.SECONDS,
            notes=(
                *sinopac_notes(source.config),
                *notes,
                source.status_note(),
            ),
        )

    def price_adjustment(
        self,
        timeframe: timeframes.Timeframe,
    ) -> market.PriceAdjustment:
        """Reports the configured Taiwan source's historical price basis.

        Args:
            timeframe: Native or derived stock/ETF candle interval.

        Returns:
            Fugle's configured daily policy, otherwise native RAW prices.
        """
        if self._fugle is not None:
            return self._fugle.price_adjustment(timeframe)
        return market.PriceAdjustment.RAW

    async def price_basis(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
    ) -> Mapping[int, decimal.Decimal]:
        """Reads Fugle's current daily corporate-action factors.

        Args:
            instrument: TWSE or TPEx stock/ETF identity.
            start_ms: Inclusive UTC start, epoch milliseconds.
            end_ms: Exclusive UTC end, epoch milliseconds.

        Returns:
            Daily session start mapped to the adjusted/raw close factor.

        Raises:
            errors.UnsupportedError: Without Fugle (prices are raw).
        """
        if self._fugle is None:
            raise errors.UnsupportedError("raw official prices have no basis")
        return await self._fugle.price_basis(instrument, start_ms, end_ms)

    def _with_fugle(
        self, caps: capabilities.VenueCapabilities
    ) -> capabilities.VenueCapabilities:
        """Declare Fugle candle and quote access when it is configured.

        Args:
            caps: Official venue capabilities used for listings and bulk data.

        Returns:
            Per-symbol Fugle capabilities, or official capabilities when
            Fugle is not configured.
        """
        if self._fugle is None:
            return caps
        return dataclasses.replace(
            caps,
            display_name=f"{caps.display_name} (Fugle free tier)",
            aggregated_timeframes=tuple(
                tf
                for tf in caps.timeframes
                if tf not in (timeframes.Timeframe.M1, timeframes.Timeframe.D1)
            ),
            notes=(
                "Fugle per-symbol 1m history begins 2023-05-23 and daily "
                "history begins in 2010; longer bars are aggregated on "
                "the XTAI session calendar. Today's minute candles and "
                "quotes use Fugle REST polling, not a WebSocket stream.",
                "All historical intervals use the configured corporate-action "
                "policy. Minute prices use confirmed daily adjusted/raw "
                "ratios before aggregation; daily prices come from Fugle. "
                "Volume and turnover "
                "remain reported values.",
                "Fugle's 13:30 closing auction is folded into the final "
                "session minute. Bulk screening snapshots and order books "
                "remain separately sourced from the exchanges.",
            ),
            docs_url=fugle_lib.DOCS_URL,
            verified_at_ms=None,
            requires_credentials=True,
            credentials_configured=True,
        )

    def _fallback(self, err: errors.JosephError) -> None:
        """Logs a switch to the official sources once per reason.

        Args:
            err: Why Shioaji could not serve.
        """
        if err.message != self._fallback_reason:
            self._fallback_reason = err.message
            _LOG.info(
                "%s: %s; serving official data", self.VENUE.value, err.message
            )

    async def _live_feed(
        self,
        instrument: instruments.InstrumentId,
        wait_s: float = 0.0,
        *,
        subscribe: bool = True,
        today_only: bool = True,
    ) -> sinopac_lib.Feed | None:
        """Returns today's Shioaji stream of a code in trading hours.

        Args:
            instrument: Instrument.
            wait_s: Seconds to wait for today's first book.
            subscribe: Subscribe the code when it is not streamed yet
                (snapshots only read codes already streamed).
            today_only: Return the feed only once it has today's data.

        Returns:
            Feed, or ``None`` (no source, unavailable, outside trading
            hours, not streamed or, with ``today_only``, nothing streamed
            today yet).
        """
        source = self._sinopac
        if source is None or source.unavailable() is not None:
            return None
        now = self._clock.now_ms()
        if not sinopac_lib.in_trading_hours(now):
            return None
        code = instrument.venue_market_id
        if not subscribe:
            existing = source.feed(code)
            today = taipei_date(now)
            if existing is None or existing.day != today:
                return None
            return existing
        try:
            feed = await source.watch(code, self.SJ_EXCHANGE, instrument)
        except errors.JosephError as err:
            self._fallback(err)
            return None
        today = taipei_date(now)
        if wait_s > 0 and (feed.day != today or feed.book is None):
            queue = source.listen(feed)
            try:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(wait_s):
                        while feed.day != today or feed.book is None:
                            await queue.get()
            finally:
                source.unlisten(feed, queue)
        return feed if feed.day == today or not today_only else None

    def _feed_daily(
        self, instrument: instruments.InstrumentId, feed: sinopac_lib.Feed
    ) -> market.Candle | None:
        """Builds today's in-progress daily bar from the tick stream.

        Args:
            instrument: Instrument.
            feed: Feed.

        Returns:
            Non-final candle (board lots only), or ``None``.
        """
        quote = feed.quote
        if quote is None:
            return None
        session = sessions.XTAI.session(taipei_date(quote.ts_ms))
        if session is None:
            return None
        return session_bars.candle(
            instrument_id=instrument,
            timeframe=_D1,
            start_ms=session.open_ms,
            open_=quote.open,
            high=quote.high,
            low=quote.low,
            close=quote.close,
            base_volume=quote.total_volume,
            now_ms=self._clock.now_ms(),
            source=sinopac_lib.BOOK_SOURCE,
            is_final=False,
            event_ts_ms=quote.ts_ms,
        )

    async def _sinopac_minutes(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None,
    ) -> tuple[list[market.Candle], list[candle_rules.Span], bool]:
        """Collects one-minute bars of a range, Shioaji first.

        Closed sessions come from kbars (memory, the bar archive, then
        Shioaji); today from the tick stream, today's kbars before it and
        MIS when Shioaji cannot serve; closed sessions Shioaji cannot serve
        from archived MIS recordings.

        Args:
            instrument: Instrument.
            timeframe: Target resolution (sizes the newest-kept window).
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum target bars (newest kept).

        Returns:
            One-minute bars, their whole-minute coverage and whether every
            session of the range was read.
        """
        calendar = sessions.XTAI
        now = self._clock.now_ms()
        try:
            first = calendar.floor(timeframe, start_ms)
        except ValueError:
            first = start_ms
        stop = min(end_ms, now + 1)
        if stop <= first:
            return [], [], True
        found = [s for s in calendar.sessions(first, stop) if s.open_ms <= now]
        found.reverse()
        wanted = MAX_INTRADAY_SESSIONS
        if limit is not None:
            per_bar = max(1, timeframe.duration_ms // _M1.duration_ms)
            # An XTAI session has 270 minutes (09:00-13:30), plus two
            # sessions of slack for the rounding and today's partial one.
            wanted = min(wanted, limit * per_bar // 270 + 2)
        complete = len(found) <= wanted
        found = found[:wanted]
        settle = sinopac_lib.DAY_SETTLE_MS
        closed = [s for s in found if now >= s.close_ms + settle]
        layers: list[Layer] = []
        bars, spans, missing = await self._kbar_history(instrument, closed)
        layers.append((bars, spans))
        for session in missing:
            layers.append(
                self._mis_session(instrument, session.open_ms, session.close_ms)
            )
        for session in found:
            if now < session.close_ms + settle:
                layers.append(
                    await self._today_minutes(instrument, session, now)
                )
        minutes, covered = combine_layers(layers)
        return minutes, covered, complete

    def _mis_session(
        self, instrument: instruments.InstrumentId, open_ms: int, close_ms: int
    ) -> Layer:
        """Returns recorded MIS minutes of one session as a fallback layer.

        Args:
            instrument: Instrument.
            open_ms: Session open.
            close_ms: Session close.

        Returns:
            Bars and whole-minute coverage inside the session.
        """
        end = close_ms + sessions.XTAI.close_grace_ms
        bars, covered = self._mis_minutes(instrument, open_ms, end)
        aligned = [
            span
            for raw in candle_rules.clip_spans(covered, open_ms, end)
            if (span := sinopac_lib.whole_minutes(raw, grace=False)) is not None
        ]
        return bars, aligned

    async def _kbar_history(
        self,
        instrument: instruments.InstrumentId,
        closed: Sequence[sessions.Session],
    ) -> tuple[
        list[market.Candle], list[candle_rules.Span], list[sessions.Session]
    ]:
        """Reads closed sessions' kbars: memory, archive, then Shioaji.

        Args:
            instrument: Instrument.
            closed: Settled sessions.

        Returns:
            Bars, the sessions covered and the sessions Shioaji could not
            serve.
        """
        code = instrument.venue_market_id
        bars: list[market.Candle] = []
        spans: list[candle_rules.Span] = []
        missing: list[sessions.Session] = []
        for session in closed:
            key = (code, taipei_date(session.open_ms))
            cached = self._kbar_days.get(key)
            if cached is None:
                missing.append(session)
                continue
            self._kbar_days.move_to_end(key)
            bars.extend(cached)
            spans.append((session.open_ms, session.close_ms))
        if missing:
            missing = self._archived_kbars(instrument, missing, bars, spans)
        if missing:
            missing = await self._fetch_kbars(instrument, missing, bars, spans)
        return bars, spans, missing

    def _remember_kbars(
        self, code: str, day: datetime.date, bars: list[market.Candle]
    ) -> None:
        """Caches a closed session's kbars (bounded, least recent out).

        Args:
            code: Security code.
            day: Trading date.
            bars: Its one-minute bars.
        """
        self._kbar_days[(code, day)] = bars
        while len(self._kbar_days) > _KBAR_CACHE_DAYS:
            self._kbar_days.popitem(last=False)

    def _archived_kbars(
        self,
        instrument: instruments.InstrumentId,
        missing: Sequence[sessions.Session],
        bars: list[market.Candle],
        spans: list[candle_rules.Span],
    ) -> list[sessions.Session]:
        """Serves sessions whose kbars the archive holds completely.

        Args:
            instrument: Instrument.
            missing: Sessions not in memory.
            bars: Collected bars (extended).
            spans: Collected coverage (extended).

        Returns:
            Sessions the archive does not hold.
        """
        archive = self._archive
        if archive is None:
            return list(missing)
        low = min(s.open_ms for s in missing)
        high = max(s.close_ms for s in missing)
        try:
            covered = archive.coverage(
                instrument, sinopac_lib.SOURCE, low, high
            )
            stored = archive.minutes(instrument, sinopac_lib.SOURCE, low, high)
        except Exception as err:  # noqa: BLE001 - isolation point: a failing archive read falls back to Shioaji queries
            _LOG.warning("bar archive read failed: %s", type(err).__name__)
            return list(missing)
        left = []
        for session in missing:
            through = _span_end(covered, session.open_ms)
            if through is None or through < session.close_ms:
                left.append(session)
                continue
            day_bars = [
                bar
                for bar in stored
                if session.open_ms <= bar.start_ms < session.close_ms
            ]
            self._remember_kbars(
                instrument.venue_market_id,
                taipei_date(session.open_ms),
                day_bars,
            )
            bars.extend(day_bars)
            spans.append((session.open_ms, session.close_ms))
        return left

    async def _fetch_kbars(
        self,
        instrument: instruments.InstrumentId,
        missing: Sequence[sessions.Session],
        bars: list[market.Candle],
        spans: list[candle_rules.Span],
    ) -> list[sessions.Session]:
        """Reads closed sessions from Shioaji, up to 30 days per query.

        Args:
            instrument: Instrument.
            missing: Sessions neither in memory nor archived (newest first).
            bars: Collected bars (extended).
            spans: Collected coverage (extended).

        Returns:
            Sessions Shioaji could not serve.
        """
        source = self._sinopac
        code = instrument.venue_market_id
        floor = sinopac_lib.HISTORY_FLOOR
        left = [s for s in missing if taipei_date(s.open_ms) < floor]
        pending = [s for s in missing if taipei_date(s.open_ms) >= floor]
        while pending:
            last_day = taipei_date(pending[0].open_ms)
            window = [
                s
                for s in pending
                if (last_day - taipei_date(s.open_ms)).days
                < sinopac_lib.KBARS_MAX_DAYS
            ]
            pending = pending[len(window) :]
            if source is None or source.history_unavailable():
                left.extend(window)
                continue
            try:
                rows = await source.kbars(
                    code,
                    self.SJ_EXCHANGE,
                    taipei_date(window[-1].open_ms),
                    last_day,
                )
            except errors.JosephError as err:
                self._fallback(err)
                left.extend(window)
                continue
            parsed = sinopac_lib.parse_kbars(
                rows, instrument, fetched_ms=self._clock.now_ms()
            )
            for session in window:
                day_bars = [
                    bar
                    for bar in parsed
                    if session.open_ms <= bar.start_ms < session.close_ms
                ]
                span = (session.open_ms, session.close_ms)
                self._remember_kbars(
                    code, taipei_date(session.open_ms), day_bars
                )
                self._archive_put(instrument, day_bars, [span])
                bars.extend(day_bars)
                spans.append(span)
        return left

    def _archive_put(
        self,
        instrument: instruments.InstrumentId,
        bars: Sequence[market.Candle],
        covered: Sequence[candle_rules.Span],
    ) -> None:
        """Archives a closed session's kbars (best effort).

        Args:
            instrument: Instrument.
            bars: Final one-minute bars.
            covered: Their coverage.
        """
        if self._archive is None:
            return
        try:
            self._archive.put(instrument, sinopac_lib.SOURCE, bars, covered)
        except Exception as err:  # noqa: BLE001 - isolation point: a failing archive write must not fail the fetch
            _LOG.warning("bar archive write failed: %s", type(err).__name__)

    async def _today_minutes(
        self,
        instrument: instruments.InstrumentId,
        session: sessions.Session,
        now_ms: int,
    ) -> Layer:
        """Collects the minutes of the running session.

        The tick stream serves what it covers; today's kbars the minutes
        before it (read at most twice: at the first request and once the
        stream's first minute has ended); MIS recording what neither can.

        Args:
            instrument: Instrument.
            session: Today's session.
            now_ms: Current time.

        Returns:
            Bars and whole-minute coverage.
        """
        source = self._sinopac
        if source is None:
            raise AssertionError("_today_minutes needs a Shioaji source")
        code = instrument.venue_market_id
        day = taipei_date(session.open_ms)
        layers: list[Layer] = []
        feed = await self._live_feed(instrument, today_only=False)
        need_to = now_ms
        proving = feed is not None and feed.day == day and bool(feed.segments)
        if feed is not None and proving:
            ticks = [
                span
                for raw in feed.coverage()
                if (span := sinopac_lib.whole_minutes(raw)) is not None
            ]
            layers.append((feed.minute_bars(now_ms), ticks))
            need_to = feed.segments[0].from_ms
        needed = min(sessions.XTAI.ceil(_M1, need_to), session.close_ms)
        known = self._today_kbars.get(code)
        if known is not None and known.day != day:
            known = None
        # Until the stream proves minutes, kbars are re-read sparingly (the
        # trading-hours budget is small) and MIS fills the live edge.
        every = _TODAY_KBARS_EVERY_MS * (1 if proving else 5)
        if (
            needed > session.open_ms
            and (known is None or known.covered_to_ms < needed)
            and (known is None or now_ms - known.fetched_ms >= every)
            and source.history_unavailable() is None
        ):
            try:
                rows = await source.kbars(code, self.SJ_EXCHANGE, day, day)
            except errors.JosephError as err:
                self._fallback(err)
            else:
                fetched = self._clock.now_ms()
                through = sinopac_lib.whole_minutes(
                    (session.open_ms, fetched - sinopac_lib.KBAR_SETTLE_MS)
                )
                known = _TodayKbars(
                    day,
                    sinopac_lib.parse_kbars(
                        rows, instrument, fetched_ms=fetched
                    ),
                    session.open_ms if through is None else through[1],
                    fetched,
                )
                self._today_kbars[code] = known
        if known is not None and known.covered_to_ms > session.open_ms:
            layers.append(
                (known.bars, [(session.open_ms, known.covered_to_ms)])
            )
        if not proving:
            self.watch(code)
            layers.append(
                self._mis_session(instrument, session.open_ms, session.close_ms)
            )
        return combine_layers(layers)

    def _history_from(self, now_ms: int) -> int:
        """Returns the oldest session open served from tick history.

        Args:
            now_ms: Current time.

        Returns:
            Open of the session ``seconds_history_days`` before the latest.
        """
        if self._sinopac is None:
            raise AssertionError("_history_from needs a Shioaji source")
        calendar = sessions.XTAI
        session = calendar.session_at_or_before(now_ms)
        for _ in range(self._sinopac.config.seconds_history_days):
            session = calendar.previous_session(session)
        return session.open_ms

    async def _day_ticks(
        self, instrument: instruments.InstrumentId, session: sessions.Session
    ) -> sinopac_lib.DayTicks | None:
        """Reads a closed session's ticks (cached).

        Args:
            instrument: Instrument.
            session: Settled session.

        Returns:
            Trades, or ``None`` when Shioaji cannot serve them.
        """
        source = self._sinopac
        code = instrument.venue_market_id
        day = taipei_date(session.open_ms)
        key = (code, day)
        cached = self._tick_days.get(key)
        if cached is not None:
            self._tick_days.move_to_end(key)
            return cached
        if (
            source is None
            or source.history_unavailable() is not None
            or day < sinopac_lib.HISTORY_FLOOR
        ):
            return None
        try:
            rows = await source.ticks(code, self.SJ_EXCHANGE, day)
        except errors.JosephError as err:
            self._fallback(err)
            return None
        parsed = sinopac_lib.parse_ticks(
            rows, instrument, day, fetched_ms=self._clock.now_ms()
        )
        self._tick_days[key] = parsed
        while len(self._tick_days) > _TICK_CACHE_DAYS:
            self._tick_days.popitem(last=False)
        return parsed

    async def _live_trades(
        self, instrument: instruments.InstrumentId, from_ms: int
    ) -> list[tuple[Sequence[market.Trade], int, int]]:
        """Returns today's streamed trades per continuity segment.

        When the range starts before the first segment, today's tick
        history is merged once (it spends one trading-hours tick query).

        Args:
            instrument: Instrument.
            from_ms: Start of the wanted range.

        Returns:
            ``(trades, complete from, complete until)`` per segment.
        """
        feed = await self._live_feed(instrument)
        source = self._sinopac
        if feed is None or source is None or feed.day is None:
            return []
        if (
            feed.segments
            and from_ms < feed.segments[0].from_ms
            and not feed.seeded
            and source.budget_left("ticks") > 0
        ):
            feed.seeded = True
            try:
                rows = await source.ticks(
                    instrument.venue_market_id, self.SJ_EXCHANGE, feed.day
                )
            except errors.JosephError as err:
                self._fallback(err)
            else:
                feed.seed(
                    sinopac_lib.parse_ticks(
                        rows,
                        instrument,
                        feed.day,
                        fetched_ms=self._clock.now_ms(),
                    )
                )
        return [
            (feed.segment_trades(index), segment.from_ms, segment.proven_ms)
            for index, segment in enumerate(feed.segments)
        ]

    async def _session_trades(
        self,
        instrument: instruments.InstrumentId,
        session: sessions.Session,
        from_ms: int,
        now_ms: int,
    ) -> list[tuple[Sequence[market.Trade], int, int]] | None:
        """Returns a session's complete trade pieces.

        Args:
            instrument: Instrument.
            session: Session.
            from_ms: Start of the wanted range.
            now_ms: Current time.

        Returns:
            ``(trades, complete from, complete until)`` pieces, or ``None``
            when a closed session's ticks are unavailable.
        """
        if now_ms < session.close_ms + sinopac_lib.DAY_SETTLE_MS:
            return await self._live_trades(instrument, from_ms)
        day = await self._day_ticks(instrument, session)
        if day is None:
            return None
        end = session.close_ms + sessions.XTAI.close_grace_ms
        return [(day.trades, session.open_ms, end)]

    async def _seconds_history(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None,
    ) -> candle_rules.CandleCoverage:
        """Builds seconds bars from Shioaji ticks (history and stream).

        Args:
            instrument: Instrument.
            timeframe: Seconds resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending seconds bars and the spans their ticks cover.

        Raises:
            errors.UnsupportedError: Without a Shioaji source.
        """
        if not self._sinopac_on:
            raise errors.UnsupportedError(
                f"{self.VENUE.value} seconds bars need SinoPac Shioaji "
                "(JOSEPH_TW_DATA_SOURCE=sinopac)"
            )
        calendar = sessions.XTAI
        now = self._clock.now_ms()
        first = calendar.ceil(timeframe, start_ms)
        stop = min(end_ms, now)
        if stop <= first:
            return candle_rules.CandleCoverage((), (), complete=True)
        wanted = (
            base.MAX_TRADE_BARS
            if limit is None
            else min(limit, base.MAX_TRADE_BARS)
        )
        first = max(
            first,
            base.newest_window_start(
                timeframe, first, stop, wanted, calendar=calendar
            ),
        )
        allowed = self._history_from(now)
        complete = first >= allowed
        bars: list[market.Candle] = []
        spans: list[candle_rules.Span] = []
        grace = calendar.close_grace_ms
        for session in calendar.sessions(max(first, allowed), stop):
            lo = max(first, session.open_ms)
            hi = min(stop, session.close_ms + grace)
            pieces = await self._session_trades(instrument, session, lo, now)
            if pieces is None:
                complete = False
                continue
            for trades, cover_from, cover_to in pieces:
                low = max(cover_from, lo - _SEED_MS)
                high = min(cover_to, hi)
                if high <= max(cover_from, lo):
                    continue
                times = [t.venue_event_ts_ms for t in trades]
                window = trades[
                    bisect.bisect_left(times, low) : bisect.bisect_left(
                        times, high
                    )
                ]
                built = trade_bars.bars_from_coverage(
                    trades_lib.TradeCoverage(tuple(window), low, high),
                    instrument,
                    timeframe,
                    source=sinopac_lib.TICK_SOURCE,
                    now_ms=now,
                    buy_volume_method=market.BuyVolumeMethod.CLASSIFIED,
                )
                bars.extend(b for b in built if lo <= b.start_ms < hi)
                spans.append((max(cover_from, lo), high))
        return base.candle_coverage(
            bars, start_ms, end_ms, limit, spans, complete=complete
        )

    async def _trade_history(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int,
    ) -> trades_lib.TradeCoverage:
        """Collects the newest complete Shioaji trades of a range.

        Sessions are walked newest first; the result stops at the first
        session (or continuity break) it cannot serve completely.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            max_pages: Request budget of the base implementation; unused
                with Shioaji, which reads a whole day per query.

        Returns:
            Complete trades ``[from, to)`` with ``to <= end``.

        Raises:
            errors.UnsupportedError: Without a Shioaji source (from the
                base implementation).
        """
        if not self._sinopac_on:
            return await super()._trade_history(
                instrument, start_ms, end_ms, max_pages
            )
        del max_pages
        calendar = sessions.XTAI
        now = self._clock.now_ms()
        stop = min(end_ms, now)
        start = max(start_ms, self._history_from(now))
        collected: list[market.Trade] = []
        cover_from = cover_to = stop
        started = False
        newer_open: int | None = None
        grace = calendar.close_grace_ms
        for session in reversed(calendar.sessions(start, stop)):
            lo = max(start, session.open_ms)
            hi = min(stop, session.close_ms + grace)
            pieces = await self._session_trades(instrument, session, lo, now)
            if not pieces:
                break
            for trades, piece_from, piece_to in reversed(pieces):
                low, high = max(piece_from, lo), min(piece_to, hi)
                if high <= low:
                    continue
                joins = (
                    not started
                    or high >= cover_from
                    or (
                        newer_open == cover_from
                        and piece_to >= session.close_ms + grace
                    )
                )
                if not joins:
                    return trades_lib.TradeCoverage(
                        tuple(collected), cover_from, cover_to
                    )
                if not started:
                    cover_to, started = high, True
                collected[:0] = [
                    t for t in trades if low <= t.venue_event_ts_ms < high
                ]
                cover_from = low
            if cover_from != max(session.open_ms, start):
                break
            newer_open = session.open_ms
        return trades_lib.TradeCoverage(tuple(collected), cover_from, cover_to)

    # Shioaji streams ---------------------------------------------------

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams Shioaji-built bars (official sources have no stream).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of candle updates.
        """
        if not self._sinopac_on:
            return super().stream_candles(instrument, timeframe, tracker)
        return base.candles_of(
            self.stream_candle_coverage(instrument, timeframe, tracker)
        )

    def stream_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[candle_rules.CandleCoverage]:
        """Streams bars from Shioaji's ticks with the spans they prove.

        The stream fails (``VenueUnavailableError``) when Shioaji does, so
        the feed router falls back to polling the official sources
        visibly (``POLL``, ``DEGRADED``).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of candle updates with coverage.
        """
        if not self._sinopac_on:
            return super().stream_candle_coverage(
                instrument, timeframe, tracker
            )
        self._require_timeframe(timeframe)
        if timeframe.is_seconds:
            return self._seconds_stream(instrument, timeframe, tracker)
        if timeframe in _INTRADAY:
            return self._minute_stream(instrument, timeframe, tracker)
        return self._daily_stream(instrument, timeframe, tracker)

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Streams Shioaji trades (classified side).

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.

        Returns:
            Async iterator of trades.
        """
        if not self._sinopac_on:
            return super().stream_trades(instrument, tracker)
        return self._trade_stream(instrument, tracker)

    async def _open_feed(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
    ) -> tuple[
        sinopac_lib.SinopacSource,
        sinopac_lib.Feed,
        asyncio.Queue[sinopac_lib.Event],
    ]:
        """Subscribes a code and registers a stream on its feed.

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.

        Returns:
            ``(source, feed, queue)``.
        """
        source = self._sinopac
        if source is None:
            raise AssertionError("_open_feed needs a Shioaji source")
        await self._require(instrument)
        feed = await source.watch(
            instrument.venue_market_id, self.SJ_EXCHANGE, instrument
        )
        queue = source.listen(feed)
        if tracker is not None:
            tracker.set_transport(
                quality.Transport.NATIVE_WS, quality.FeedState.LIVE
            )
        return source, feed, queue

    async def _next_event(
        self,
        queue: asyncio.Queue[sinopac_lib.Event],
        tracker: health.FeedHealthTracker | None,
    ) -> sinopac_lib.Event:
        """Waits for a feed event (``idle`` after a quiet second).

        Args:
            queue: Feed queue.
            tracker: Optional health tracker.

        Returns:
            Event.

        Raises:
            errors.VenueUnavailableError: When Shioaji failed.
        """
        try:
            event = await asyncio.wait_for(queue.get(), timeout=_STREAM_IDLE_S)
        except TimeoutError:
            return sinopac_lib.Event("idle")
        if event.kind == "down":
            raise errors.VenueUnavailableError(
                f"sinopac unavailable: {event.reason}"
            )
        if tracker is not None:
            tracker.record_message(self._clock.now_ms())
        return event

    async def _seconds_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Builds seconds bars from the tick stream.

        Coverage follows the feed's proofs within its newest segment; a
        new segment (missed trade) restarts the builder.

        Args:
            instrument: Instrument.
            timeframe: Seconds resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates with newly proven spans.
        """
        source, feed, queue = await self._open_feed(instrument, tracker)
        builder = trade_bars.TradeBarBuilder(
            instrument,
            timeframe,
            source=sinopac_lib.TICK_SOURCE,
            buy_volume_method=market.BuyVolumeMethod.CLASSIFIED,
        )

        def restart(now: int) -> tuple[list[market.Candle], int | None]:
            """Restarts the builder on the feed's newest segment.

            Only the segment's trades of the last 300 bars are replayed.

            Args:
                now: Current time.

            Returns:
                Bars of the segment's retained trades and where the
                coverage restarts (``None`` without a segment).
            """
            if not feed.segments:
                builder.start_segment(None)
                return [], None
            segment = feed.segments[-1]
            horizon = now - 300 * timeframe.duration_ms
            start = max(segment.from_ms, horizon)
            builder.start_segment(start)
            kept = [
                t
                for t in feed.segment_trades(len(feed.segments) - 1)
                if t.venue_event_ts_ms >= start
            ]
            bars = [
                *builder.add(kept, now),
                *builder.finalize_through(segment.proven_ms, now),
            ]
            return bars, start

        try:
            first, cover_from = restart(self._clock.now_ms())
            # Proofs count from the newest restart (read only while
            # ``cover_from`` is set).
            proven = 0
            if first:
                yield candle_rules.CandleCoverage(tuple(first))
            while True:
                event = await self._next_event(queue, tracker)
                now = self._clock.now_ms()
                candles: list[market.Candle] = []
                covered: tuple[candle_rules.Span, ...] = ()
                if event.kind == "segment":
                    candles, cover_from = restart(now)
                    proven = 0
                elif event.kind == "trade" and event.trade is not None:
                    candles = builder.add([event.trade], now)
                elif event.kind == "proof" and event.proven_ms is not None:
                    candles = builder.finalize_through(event.proven_ms, now)
                    if cover_from is not None and event.proven_ms > max(
                        proven, cover_from
                    ):
                        proven = event.proven_ms
                        covered = ((cover_from, proven),)
                if candles or covered:
                    yield candle_rules.CandleCoverage(tuple(candles), covered)
        finally:
            source.unlisten(feed, queue)

    async def _minute_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Streams 1m bars from ticks, or aggregates them on the XTAI grid.

        The current bar's earlier minutes are seeded from the history path
        (kbars, or MIS when Shioaji could not serve them); an aggregate
        mixing sources is not emitted.

        Args:
            instrument: Instrument.
            timeframe: Intraday resolution.
            tracker: Optional health tracker.

        Yields:
            Candle updates with the stream's newly proven minutes.
        """
        source, feed, queue = await self._open_feed(instrument, tracker)
        calendar = sessions.XTAI
        emitted: dict[int, market.Candle] = {}
        try:
            now = self._clock.now_ms()
            group_from = calendar.floor(timeframe, now)
            seed, seed_spans, _ = await self._sinopac_minutes(
                instrument, _M1, group_from, now + 1, None
            )
            minutes = {bar.start_ms: bar for bar in seed}

            def spans_now() -> list[candle_rules.Span]:
                """Returns the seed's and the stream's whole minutes.

                Returns:
                    Merged coverage.
                """
                ticks = [
                    span
                    for raw in feed.coverage()
                    if (span := sinopac_lib.whole_minutes(raw)) is not None
                ]
                return candle_rules.merge_spans([*seed_spans, *ticks])

            def changed(starts: Iterable[int], at: int) -> list[market.Candle]:
                """Re-aggregates the bars holding some minutes.

                Args:
                    starts: Minute starts that changed.
                    at: Receipt time.

                Returns:
                    Aggregates whose values or finality changed.
                """
                spans = spans_now()
                out = []
                for group in sorted(
                    {calendar.floor(timeframe, s) for s in starts}
                ):
                    members = [
                        bar
                        for bar in minutes.values()
                        if calendar.floor(timeframe, bar.start_ms) == group
                    ]
                    rows, _ = aggregate_minutes(members, timeframe, spans, at)
                    for row in rows:
                        if _changed(emitted.get(row.start_ms), row):
                            emitted[row.start_ms] = row
                            out.append(row)
                return out

            first = changed(list(minutes), now)
            yield candle_rules.CandleCoverage(tuple(first), tuple(spans_now()))
            while True:
                event = await self._next_event(queue, tracker)
                now = self._clock.now_ms()
                touched = _touched_minutes(event, minutes)
                for start in touched:
                    bar = feed.minute_bar(start, now)
                    if bar is not None:
                        minutes[start] = bar
                rows = changed(touched, now) if touched else []
                covered = tuple(spans_now()) if event.kind == "proof" else ()
                if rows or covered:
                    yield candle_rules.CandleCoverage(tuple(rows), covered)
                cutoff = now - 2 * timeframe.duration_ms - _D1.duration_ms
                for kept in (minutes, emitted):
                    for start in [s for s in kept if s < cutoff]:
                        del kept[start]
        finally:
            source.unlisten(feed, queue)

    async def _daily_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[candle_rules.CandleCoverage, None]:
        """Streams today's bar from the tick stream (or its week, month).

        Args:
            instrument: Instrument.
            timeframe: ``1d``, ``1w`` or ``1M``.
            tracker: Optional health tracker.

        Yields:
            Candle updates (today's bar is not final), without coverage.
        """
        source, feed, queue = await self._open_feed(instrument, tracker)
        calendar = sessions.XTAI
        last: market.Candle | None = None
        try:
            now = self._clock.now_ms()
            period = calendar.floor(timeframe, now)
            seeded = await self.fetch_candle_coverage(
                instrument, _D1, period, now + 1
            )
            days = {bar.start_ms: bar for bar in seeded.candles}
            while True:
                live = self._feed_daily(instrument, feed)
                if live is not None:
                    days[live.start_ms] = live
                if days:
                    if timeframe is _D1:
                        newest = days[max(days)]
                    else:
                        finals = [b.end_ms for b in days.values() if b.is_final]
                        newest = session_bars.aggregate(
                            days.values(),
                            timeframe,
                            now_ms=self._clock.now_ms(),
                            covered_to_ms=max(finals, default=0),
                        )[-1]
                    if _changed(last, newest):
                        last = newest
                        yield candle_rules.CandleCoverage((newest,))
                while (await self._next_event(queue, tracker)).kind != "quote":
                    pass
        finally:
            source.unlisten(feed, queue)

    async def _trade_stream(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[market.Trade, None]:
        """Yields streamed trades.

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.

        Yields:
            Trades (classified side from ``tick_type``).
        """
        source, feed, queue = await self._open_feed(instrument, tracker)
        try:
            while True:
                event = await self._next_event(queue, tracker)
                if event.kind == "trade" and event.trade is not None:
                    yield event.trade
        finally:
            source.unlisten(feed, queue)

    # Snapshots and books -------------------------------------------------

    async def _quote(self, instrument: instruments.InstrumentId) -> MisQuote:
        """Fetches the MIS quote of one instrument.

        Args:
            instrument: Instrument.

        Returns:
            Quote.

        Raises:
            errors.MissingDataError: When MIS has no record.
        """
        await self._require(instrument)
        quote = (await self.mis.quotes([instrument.venue_market_id])).get(
            instrument.venue_market_id
        )
        if quote is None:
            raise errors.MissingDataError(f"MIS has no data for {instrument}")
        if instrument.venue_market_id in self._builders:
            self.ingest([quote])
        return quote

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Fetches the snapshot (last trade, best quotes, day volume).

        ``volume_24h_base`` is the regular session's volume so far in
        shares and ``change_24h_pct`` the change from the previous close.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot (Shioaji's tick and bid/ask stream while it streams the
            code today, else MIS).

        Raises:
            errors.MissingDataError: When MIS has no record of the code.
        """
        await self._require(instrument)
        if self._fugle is not None:
            return await self._fugle.fetch_snapshot(instrument)
        feed = await self._live_feed(instrument, subscribe=False)
        if feed is not None and self._sinopac is not None:
            live = self._sinopac.live_snapshot(feed)
            if live is not None:
                return live
        quote = await self._quote(instrument)
        last = quote.last
        change = None
        if last is not None and quote.previous_close:
            change = (
                (last - quote.previous_close)
                / quote.previous_close
                * decimals.HUNDRED
            )
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source=self.MIS_SOURCE_NAME,
            as_of_ms=quote.snapshot.data_ms,
            last=last,
            best_bid=quote.snapshot.best_bid,
            best_ask=quote.snapshot.best_ask,
            volume_24h_base=quote.snapshot.cumulative_volume,
            change_24h_pct=change,
        )

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Fetches the 5-level book (sizes in shares).

        Args:
            instrument: Instrument.
            depth: Levels per side (MIS publishes at most 5).

        Returns:
            Order book (Shioaji's bid/ask stream while it streams the code
            today, else MIS).

        Raises:
            errors.MissingDataError: When MIS has no record of the code.
        """
        feed = await self._live_feed(instrument, wait_s=_BOOK_WAIT_S)
        if feed is not None and feed.book is not None:
            book = feed.book
            return dataclasses.replace(
                book,
                bids=book.bids[: max(depth, 0)],
                asks=book.asks[: max(depth, 0)],
            )
        quote = await self._quote(instrument)
        return books.snapshot_book(
            instrument,
            quote.bids[: max(depth, 0)],
            quote.asks[: max(depth, 0)],
            ts_ms=quote.snapshot.data_ms,
            received_ms=quote.received_ms,
            source=self.MIS_SOURCE_NAME,
        )

    async def fetch_open_interest(  # noqa: DOC201 - always raises UnsupportedError; never returns
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Cash securities have no open interest.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError(
            f"{self.VENUE.value} securities have no open interest"
        )

    async def fetch_funding(  # noqa: DOC201 - always raises UnsupportedError; never returns
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Cash securities have no funding.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError(
            f"{self.VENUE.value} securities have no funding"
        )

    async def close(self) -> None:
        """Stops the recorder, archives it and closes the clients."""
        self._watch.clear()
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poller
        self.persist_recordings(self._clock.now_ms(), force=True)
        if self._sinopac is not None:
            await self._sinopac.close()
        if self._fugle is not None:
            await self._fugle.close()
        await self._http.close()
        await self._www.close()
        await self._mis_http.close()


class TwseAdapter(TaiwanExchangeAdapter):
    """``SparseBarsPort`` for TWSE-listed stocks and ETFs.

    Attributes:
        VENUE: ``twse``.
        listing: Listing facts per security code (``t187ap03_L`` and
            ``t187ap47_L``), refreshed with the instruments.
    """

    VENUE = instruments.Venue.TWSE

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return self._with_sinopac(
            capabilities.VenueCapabilities(
                venue=self.VENUE,
                display_name="Taiwan Stock Exchange",
                market_types=(
                    instruments.MarketType.STOCK,
                    instruments.MarketType.ETF,
                ),
                market_data=capabilities.MarketDataCapabilities(
                    rest_history=True,
                    book_snapshot=True,
                    classified_delta=True,
                ),
                timeframes=_TF,
                aggregated_timeframes=(
                    *(tf for tf in _INTRADAY if tf is not _M1),
                    *_PERIODIC,
                ),
                notes=(
                    "Daily bars come from STOCK_DAY (one month per request, "
                    "volume in shares, raw prices: not adjusted for dividends "
                    "or splits); they include odd-lot, after-hours fixed-price "
                    "and block trades. 1w and 1M are aggregated from them.",
                    *MIS_NOTES,
                    MIS_SECONDS_NOTE,
                    "Bulk snapshots for screening are the last session's "
                    "official values (STOCK_DAY_ALL); turnover is in TWD.",
                    "Bond ETFs listed on TPEx (e.g. 00679B, 00937B) are "
                    "served by the tpex venue; TWSE-listed bond ETFs (e.g. "
                    "00865B) are served here, with asset class BOND.",
                    "Requests to www.twse.com.tw, openapi.twse.com.tw and "
                    "MIS are paced at one per 2 s per host, without bursts "
                    "(TWSE blocks clients above roughly 3 requests per 5 s; "
                    "MIS is shared with tpex).",
                ),
                docs_url=DOCS_URL,
                verified_at_ms=self._verified_at_ms,
            )
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Fetches the listed universe and refreshes the calendar.

        Returns:
            Instruments and empty native metadata.
        """
        day_all, companies, funds, first_five, halts = await asyncio.gather(
            self._http.get("/v1/exchangeReport/STOCK_DAY_ALL"),
            self._http.get("/v1/opendata/t187ap03_L"),
            self._http.get("/v1/opendata/t187ap47_L"),
            self._http.get("/v1/exchangeReport/TWT88U"),
            self._http.get("/v1/exchangeReport/TWTAWU"),
        )
        rows, meta = parse_instruments(
            day_all or [],
            companies or [],
            funds or [],
            first_five or [],
            halts or [],
            now_ms=self._clock.now_ms(),
        )
        self.listing = meta
        await self.refresh_calendar()
        return rows, {}

    async def refresh_calendar(self) -> set[datetime.date]:
        """Adds newly published closures to the XTAI calendar (daily).

        Returns:
            Closures added (empty when already refreshed today or when the
            schedules are unreachable).
        """
        now = self._clock.now_ms()
        today = taipei_date(now)
        if self._calendar_day == today:
            return set()
        entries: list[tuple[datetime.date, str]] = []
        try:
            entries.extend(
                parse_holidays_openapi(
                    await self._http.get("/v1/holidaySchedule/holidaySchedule")
                    or []
                )
            )
            for year in (today.year, today.year + 1):
                entries.extend(
                    parse_holidays_rwd(
                        await self._www.get(
                            "/rwd/zh/holidaySchedule/holidaySchedule",
                            params={"response": "json", "date": f"{year}0101"},
                        )
                        or {}
                    )
                )
        except errors.JosephError as err:
            _LOG.warning("twse holiday schedule unavailable: %s", err.message)
            return set()
        closures = schedule_closures(entries, today)
        added = {
            day for day in closures if sessions.XTAI.session(day) is not None
        }
        if added:
            sessions.XTAI.add_closures(added)
            _LOG.info("twse: %d closures added to XTAI", len(added))
        self._calendar_day = today
        return added

    async def _fetch_month(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> list[market.Candle]:
        """Reads one ``STOCK_DAY`` month.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            Daily candles of the month.
        """
        payload = await self._www.get(
            "/rwd/zh/afterTrading/STOCK_DAY",
            params={
                "date": f"{year:04d}{month:02d}01",
                "stockNo": instrument.venue_market_id,
                "response": "json",
            },
        )
        return parse_stock_day(payload or {}, instrument, self._clock.now_ms())

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Returns the last session's official values of every listing.

        Returns:
            Snapshots (as of that session's close; turnover in TWD).
        """
        await self.list_instruments()
        payload = await self._http.get("/v1/exchangeReport/STOCK_DAY_ALL")
        now = self._clock.now_ms()
        out = []
        for row in payload or []:
            listed = self._instruments.get(str(row.get("Code", "")).strip())
            close = number(row.get("ClosingPrice"))
            if listed is None or close is None:
                continue
            try:
                session = sessions.XTAI.session(
                    roc_date(str(row.get("Date", "")))
                )
            except ValueError:
                session = None
            change = number(row.get("Change"))
            previous = None if change is None else close - change
            out.append(
                market.MarketSnapshot(
                    instrument_id=listed.instrument_id,
                    feed_state=quality.FeedState.LIVE,
                    source=REST_SOURCE,
                    as_of_ms=session.close_ms if session is not None else now,
                    last=close,
                    turnover_24h_quote=number(row.get("TradeValue")),
                    volume_24h_base=number(row.get("TradeVolume")),
                    change_24h_pct=(
                        None
                        if previous is None or previous <= 0 or change is None
                        else change / previous * decimals.HUNDRED
                    ),
                )
            )
        return out
