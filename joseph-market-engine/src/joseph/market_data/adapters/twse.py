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
  4-120 s. TWSE blocks clients above roughly 3 requests per 5 s, so MIS and
  ``www`` requests are paced at one per 2 s.
* Holidays: ``openapi.../holidaySchedule/holidaySchedule`` (the latest
  published year) and ``www.../rwd/zh/holidaySchedule/holidaySchedule``
  (per year). Entries named ...開始交易 or ...最後交易 are trading days;
  every other weekday entry is a closure added to the XTAI calendar.

Intraday bars are reconstructed from MIS snapshots recorded while the
engine runs (``snapshot_bars``); there is no intraday history.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import decimal
import logging
import zoneinfo
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.domain import books
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import session_bars
from joseph.market_data.domain import snapshot_bars

_LOG = logging.getLogger(__name__)

OPENAPI_URL = "https://openapi.twse.com.tw"
WWW_URL = "https://www.twse.com.tw"
MIS_URL = "https://mis.twse.com.tw"
DOCS_URL = "https://openapi.twse.com.tw/"
MIS_SOURCE = "twse.mis"
REST_SOURCE = "twse.rest"
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
_MONTH_TTL_MS = 30 * 60_000
_DUE_MONTH_TTL_MS = 10 * 60_000
_MIS_CACHE_MS = 4_000
_MIS_BATCH = 40
_WATCH_TTL_MS = 30 * 60_000
_PRE_OPEN_MS = 5 * 60_000
_POST_CLOSE_MS = 2 * 60_000
POLL_INTERVAL_S = 5.0
Sleep = Callable[[float], Awaitable[None]]


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
    """Parses a TWSE number (comma groups; ``--``/``-``/empty is missing).

    Args:
        text: Raw value.

    Returns:
        Decimal or ``None``.
    """
    raw = str(text).replace(",", "").strip()
    if raw in ("", "-", "--", "---", "----", "X0.00"):
        return None
    try:
        return decimal.Decimal(raw)
    except decimal.InvalidOperation:
        return None


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
        ``BOND`` for bond funds, else ``EQUITY``.
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
        if company is not None:
            market_type = instruments.MarketType.STOCK
            ladder = STOCK_TICKS
            asset = instruments.AssetClass.EQUITY
            limit: decimal.Decimal | None = _PRICE_LIMIT
            listed_text = str(company.get("上市日期", ""))
        elif fund is not None and not (
            len(code) == 6 and code[5] in _FOREIGN_CURRENCY
        ):
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
                display_symbol=f"{code} {name}".strip(),
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
    """Converts one ``STOCK_DAY`` month to final daily candles.

    Args:
        payload: ``{stat: "OK", fields, data: [[date, shares, value, open,
            high, low, close, change, transactions, note], ...]}``.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Ascending candles (days without a trade are skipped).
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
        received_ms: Receipt time.
    """

    code: str
    snapshot: snapshot_bars.Snapshot
    previous_close: decimal.Decimal | None
    bids: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    asks: tuple[tuple[decimal.Decimal, decimal.Decimal], ...]
    received_ms: int

    @property
    def last(self) -> decimal.Decimal | None:
        """Returns the newest known trade price (``z``, else ``trade.z``)."""
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


def mis_key(code: str) -> str:
    """Returns the MIS channel of a TWSE security.

    Args:
        code: Security code.

    Returns:
        ``tse_{code}.tw``.
    """
    return f"tse_{code}.tw"


class MisClient:
    """Batched, cached and coalesced MIS snapshot requests.

    Concurrent callers within a few milliseconds share one request (up to
    ``batch`` symbols), and a symbol fetched within ``cache_ms`` is served
    from memory, so screening and the recorder stay within TWSE's limits.
    """

    def __init__(
        self,
        http_client: http.VenueHttp,
        clock: clock_lib.Clock,
        *,
        cache_ms: int = _MIS_CACHE_MS,
        batch: int = _MIS_BATCH,
        coalesce_s: float = 0.02,
    ) -> None:
        """Initializes the client.

        Args:
            http_client: HTTP client for ``MIS_URL``.
            clock: Time source.
            cache_ms: Reuse window per symbol.
            batch: Symbols per request.
            coalesce_s: Wait before a request so concurrent callers join.
        """
        self._http = http_client
        self._clock = clock
        self._cache_ms = cache_ms
        self._batch = batch
        self._coalesce_s = coalesce_s
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
        """Sends the pending symbols in batches and wakes the callers."""
        await asyncio.sleep(self._coalesce_s)
        codes = sorted(self._pending)
        waiters = self._waiters
        self._pending = set()
        self._waiters = []
        try:
            for i in range(0, len(codes), self._batch):
                await self._fetch(codes[i : i + self._batch])
        except errors.JosephError as err:
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(err)
            return
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

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
                "ex_ch": "|".join(mis_key(code) for code in codes),
                "json": "1",
                "delay": "0",
                "_": str(self._clock.now_ms()),
            },
        )
        if not isinstance(payload, Mapping) or payload.get("rtcode") != "0000":
            raise errors.VenueUnavailableError("twse MIS returned an error")
        now = self._clock.now_ms()
        for code in codes:
            self._cache[code] = None
            self._cached_at[code] = now
        for record in payload.get("msgArray") or []:
            quote = parse_mis_record(record, now)
            if quote is not None:
                self._cache[quote.code] = quote
                self._cached_at[quote.code] = now


@dataclasses.dataclass(slots=True)
class _Month:
    """Cached STOCK_DAY month.

    Attributes:
        bars: Daily candles.
        fetched_ms: When read.
    """

    bars: list[market.Candle]
    fetched_ms: int


class TwseAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for TWSE-listed stocks and ETFs."""

    VENUE = instruments.Venue.TWSE
    INSTRUMENT_TTL_MS = 6 * 3_600_000

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
    ) -> None:
        """Initializes the adapter.

        Args:
            openapi: Client for ``OPENAPI_URL``.
            www: Client for ``WWW_URL``.
            mis: Client for ``MIS_URL``.
            clock: Time source.
            verified_at_ms: When capabilities were last verified live.
            sleep: Sleep function of the recorder (injectable for tests).
            poll_interval_s: Minimum seconds between recorder passes.
        """
        super().__init__(openapi, clock)
        self._www = www
        self._mis_http = mis
        self.mis = MisClient(mis, clock)
        self._verified_at_ms = verified_at_ms
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._months: dict[tuple[str, int, int], _Month] = {}
        self._builders: dict[str, snapshot_bars.SnapshotBarBuilder] = {}
        self._watch: dict[str, int] = {}
        self._poller: asyncio.Task[None] | None = None
        self._calendar_day: datetime.date | None = None
        self.listing: dict[str, ListingMeta] = {}

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
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
                "Seconds timeframes are not offered: 5-second snapshots "
                "cannot make honest seconds bars.",
                "MIS publishes no trade feed or aggressor side: derived "
                "trades (volume increments) are classified with the quote "
                "rule against the previous snapshot's best bid/ask and the "
                "tick test as fallback (taker_buy_method = classified); "
                "they are not exposed as public trades.",
                "Order books are MIS 5-level snapshots (sizes converted "
                "from board lots to shares); there is no WebSocket, no "
                "funding, open interest or taker volume.",
                "Bulk snapshots for screening are the last session's "
                "official values (STOCK_DAY_ALL); turnover is in TWD.",
                "Taiwan-listed bond ETFs on TPEx (e.g. 00679B, 00937B) are "
                "not TWSE listings and are not served; TWSE-listed bond "
                "ETFs (e.g. 00865B) are, with asset class BOND.",
                "Requests are paced at one per 2 s per host (TWSE blocks "
                "clients above roughly 3 requests per 5 s).",
            ),
            docs_url=DOCS_URL,
            verified_at_ms=self._verified_at_ms,
        )

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

    # Daily history -----------------------------------------------------

    async def _month(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> list[market.Candle]:
        """Returns one STOCK_DAY month, cached while it cannot change.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            Daily candles of the month.
        """
        code = instrument.venue_market_id
        key = (code, year, month)
        now = self._clock.now_ms()
        today = taipei_date(now)
        cached = self._months.get(key)
        if cached is not None:
            if (year, month) < (today.year, today.month) and (
                taipei_date(cached.fetched_ms) > datetime.date(year, month, 28)
            ):
                return cached.bars
            ttl = (
                _DUE_MONTH_TTL_MS
                if self._day_due(cached, now)
                else _MONTH_TTL_MS
            )
            if now - cached.fetched_ms < ttl:
                return cached.bars
        payload = await self._www.get(
            "/rwd/zh/afterTrading/STOCK_DAY",
            params={
                "date": f"{year:04d}{month:02d}01",
                "stockNo": code,
                "response": "json",
            },
        )
        bars = parse_stock_day(payload or {}, instrument, self._clock.now_ms())
        self._months[key] = _Month(bars, self._clock.now_ms())
        return bars

    @staticmethod
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

    async def _daily(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        wanted: int | None,
    ) -> list[market.Candle]:
        """Collects daily bars of a range, newest months first.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            wanted: Stop once this many bars in range are collected.

        Returns:
            Ascending final daily bars plus, during or after today's
            session, today's non-final bar from MIS when STOCK_DAY has no
            row yet.
        """
        now = self._clock.now_ms()
        meta = self.listing.get(instrument.venue_market_id)
        floor = max(
            _HISTORY_FLOOR,
            meta.listed if meta and meta.listed else _HISTORY_FLOOR,
        )
        first = max(floor, taipei_date(max(start_ms, 0)))
        last = taipei_date(min(end_ms - 1, now))
        rows: dict[int, market.Candle] = {}
        year, month = last.year, last.month
        months = 0
        while (year, month) >= (
            first.year,
            first.month,
        ) and months < MAX_MONTHS_PER_CALL:
            for bar in await self._month(instrument, year, month):
                rows[bar.start_ms] = bar
            months += 1
            if (
                wanted is not None
                and sum(1 for s in rows if start_ms <= s < end_ms) >= wanted
            ):
                break
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
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
        return [rows[s] for s in sorted(rows) if start_ms <= s < end_ms]

    async def _live_daily(
        self, instrument: instruments.InstrumentId
    ) -> market.Candle | None:
        """Builds today's in-progress daily bar from MIS.

        Args:
            instrument: Instrument.

        Returns:
            Non-final candle (regular-session volume only), or ``None``.
        """
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
            source=MIS_SOURCE,
            is_final=False,
            event_ts_ms=snap.data_ms,
        )

    # Intraday ----------------------------------------------------------

    def watch(self, code: str) -> None:
        """Records interest in a code so the recorder polls it.

        Args:
            code: Security code.
        """
        self._watch[code] = self._clock.now_ms()
        if code not in self._builders:
            self._builders[code] = snapshot_bars.SnapshotBarBuilder(
                self.instrument_id(code), source=MIS_SOURCE
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
        return self.ingest(quotes.values())

    def _recording_window(self, now_ms: int) -> int:
        """Returns milliseconds until the next recording window (0 if in).

        Args:
            now_ms: Current time.

        Returns:
            Delay before polling should resume.
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
                _LOG.warning("twse MIS poll failed: %s", err.message)
            await self._sleep(self._poll_interval_s)

    def _intraday(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns recorded intraday bars.

        Args:
            instrument: Instrument.
            timeframe: Intraday resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending candles inside coverage segments.
        """
        builder = self._builders[instrument.venue_market_id]
        now = self._clock.now_ms()
        calendar = sessions.XTAI
        try:
            first = calendar.floor(timeframe, start_ms)
        except ValueError:
            first = start_ms
        ones = builder.bars(first, end_ms, now)
        if timeframe is _M1:
            return ones
        groups: dict[int, list[market.Candle]] = {}
        for bar in ones:
            groups.setdefault(
                calendar.floor(timeframe, bar.start_ms), []
            ).append(bar)
        out = []
        for start, group in sorted(groups.items()):
            segment = builder.segment_at(start)
            if segment is None:
                continue
            out.extend(
                session_bars.aggregate(
                    group, timeframe, now_ms=now, covered_to_ms=segment[1]
                )
            )
        return out

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
        self._require_timeframe(timeframe)
        await self._require(instrument)
        if end_ms <= start_ms:
            return []
        if timeframe in _INTRADAY:
            self.watch(instrument.venue_market_id)
            rows = self._intraday(instrument, timeframe, start_ms, end_ms)
            return base.clip_range(rows, start_ms, end_ms, limit)
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
        daily = await self._daily(
            instrument, min(grid, start_ms), end_ms, wanted
        )
        if timeframe is not _D1:
            finals = [bar.end_ms for bar in daily if bar.is_final]
            daily = session_bars.aggregate(
                daily,
                timeframe,
                now_ms=self._clock.now_ms(),
                covered_to_ms=max(finals, default=0),
            )
        return base.clip_range(daily, start_ms, end_ms, limit)

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
        """Fetches the MIS snapshot (last trade, best quotes, day volume).

        ``volume_24h_base`` is the regular session's volume so far in
        shares and ``change_24h_pct`` the change from the previous close.

        Args:
            instrument: Instrument.

        Returns:
            Snapshot.
        """
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
            source=MIS_SOURCE,
            as_of_ms=quote.snapshot.data_ms,
            last=last,
            best_bid=quote.snapshot.best_bid,
            best_ask=quote.snapshot.best_ask,
            volume_24h_base=quote.snapshot.cumulative_volume,
            change_24h_pct=change,
        )

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

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Returns the MIS 5-level book (sizes in shares).

        Args:
            instrument: Instrument.
            depth: Levels per side (MIS publishes at most 5).

        Returns:
            Order book.
        """
        quote = await self._quote(instrument)
        return books.snapshot_book(
            instrument,
            quote.bids[: max(depth, 0)],
            quote.asks[: max(depth, 0)],
            ts_ms=quote.snapshot.data_ms,
            received_ms=quote.received_ms,
            source=MIS_SOURCE,
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Cash securities have no open interest.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("twse securities have no open interest")

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Cash securities have no funding.

        Args:
            instrument: Instrument.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("twse securities have no funding")

    async def close(self) -> None:
        """Stops the recorder and closes the HTTP clients."""
        self._watch.clear()
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poller
        await self._http.close()
        await self._www.close()
        await self._mis_http.close()
