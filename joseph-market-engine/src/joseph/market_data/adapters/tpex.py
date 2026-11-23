"""Taipei Exchange (TPEx, 櫃買中心) market-data adapter (official, free).

Serves the TPEx mainboard (上櫃): common stocks and ETFs, including the
bond ETFs most Taiwan bond funds list here (00679B, 00937B). The emerging
stock board (興櫃), which trades by dealer quotes rather than the
exchange's matching, is not served. Sources (all public, verified live
2026-11-23):

* Instruments: OpenAPI ``www.tpex.org.tw/openapi/v1`` (swagger at
  ``/openapi/swagger.json``): ``tpex_mainboard_quotes`` (上櫃股票收盤行情:
  every mainboard security with the last session's board-lot values and
  the next session's limit prices; refreshed about 16:00 Taipei),
  ``mopsfin_t187ap03_O`` (上櫃股票基本資料: companies, Gregorian listing
  dates, English short names), ``tpex_ipo_no_limit`` (首五日無漲跌幅:
  stabilization windows of the first five trading days) and ``tpex_cmode``
  (變更交易/分盤/停止交易; flags are a full-width Y, U+FF39). Suspended
  companies are absent from the quotes and are listed as ``HALTED``. ETFs
  are the ``00``-prefixed codes; foreign-currency counters (sixth character
  K or C,
  e.g. ``00687C``, whose trading unit is 100 units), preferred shares
  (``8349A``), ETNs (``02xxxx``) and warrants are not served.
* Rules (``/zh-tw/mainboard/trading/rules/system.html``): the tick ladders
  are TWSE's (stocks 0.01/0.05/0.1/0.5/1/5 at 10/50/100/500/1000; ETFs
  0.01 below 50, else 0.05); the price limit is 10 % of the reference
  price (domestic leveraged/inverse ETFs: 10 % times the multiple); new
  listings' first five days, foreign-constituent ETFs and second-listed
  foreign stocks have none, which ``tpex_mainboard_quotes`` and MIS show
  as limit-up 9999.95. Board lot 1,000 shares; settlement T+2. MIS limit
  prices and quote ladders matched these rules on 2026-11-23.
* Daily history: ``/www/zh-tw/afterTrading/tradingStock?code=6488&date=
  2026/11/01&response=json`` (個股日成交資訊, data from 1994) gives one
  month per request: ROC dates (``115/11/23``; the listing day is marked
  ``106/01/17*``), volume in thousands of shares (field ``成交張數`` or
  ``成交仟股``) and turnover in thousands of TWD, both rounded, and raw
  prices. A day includes regular, odd-lot and after-hours fixed-price
  trades, not block trades (the report's own note). Months without data
  come back with ``stat`` ``ok`` and no rows.
* Live data: TWSE's MIS (``ex_ch=otc_6488.tw``), parsed and paced exactly
  like TWSE's (``twse.MisClient``); the venues share MIS's rate budget.
* Holidays: ``/www/zh-tw/bulletin/tradingDate?date=2027&response=json``
  returns the year's 開(休)市日期表 as an HTML table. TPEx sessions are
  the XTAI calendar: TPEx's daily index history had exactly the XTAI
  sessions in every month from 2005-01 to 2026-11, and its 2026 and 2027
  schedules list exactly the XTAI closures.

Intraday bars, coverage and the classified delta work as on TWSE (see
``twse``); bars are sparse (``SPARSE_BARS``).
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import html
import logging
import re
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import twse
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import session_bars

_LOG = logging.getLogger(__name__)

OPENAPI_URL = "https://www.tpex.org.tw/openapi"
WWW_URL = "https://www.tpex.org.tw"
DOCS_URL = "https://www.tpex.org.tw/openapi/"
MIS_SOURCE = "tpex.mis"
REST_SOURCE = "tpex.rest"
MIS_MARKET = "otc"
"""MIS channel prefix of TPEx listings (``otc_6488.tw``)."""
LISTING_EXCHANGE = "TPEx"
_THOUSAND = decimal.Decimal(1000)
_PRICE_LIMIT = decimal.Decimal(10)
_LEVERAGED_LIMIT = decimal.Decimal(20)
_ZERO_TICK = decimal.Decimal("0.01")
UNLIMITED_UP = decimal.Decimal("9999.95")
"""Limit-up price TPEx and MIS publish for securities without a limit."""
_FOREIGN_CURRENCY = frozenset("KC")
_FLAG = frozenset({"Y", "\uff39"})
"""``tpex_cmode`` flags (a full-width Y)."""
_LISTING_MARKS = ("*", "\uff0a")
"""Marks of a listing day in ``tradingStock`` (asterisk, full width)."""
_TRADING_DAY_MARKERS = ("開始交易", "最後交易")
_DATE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_D1 = timeframes.Timeframe.D1


def trading_day(text: str) -> tuple[datetime.date, bool]:
    """Parses a ``tradingStock`` date cell.

    Args:
        text: ``115/11/23``, or ``106/01/17*`` on the listing day.

    Returns:
        Date and whether it is marked as the listing day.

    Raises:
        ValueError: For malformed text.
    """
    raw = text.strip()
    marked = raw.endswith(_LISTING_MARKS)
    return twse.roc_date(raw.rstrip("".join(_LISTING_MARKS) + " ")), marked


def is_etf_code(code: str) -> bool:
    """Checks whether a TPEx code is a TWD-traded ETF.

    Args:
        code: Security code.

    Returns:
        True for ``00``-prefixed five- or six-character codes other than
        foreign-currency counters (sixth character K or C).
    """
    if not code.startswith("00") or len(code) not in (5, 6):
        return False
    return not (len(code) == 6 and code[5] in _FOREIGN_CURRENCY)


def etf_asset_class(code: str, name: str) -> instruments.AssetClass:
    """Classifies a TPEx ETF as BOND or EQUITY.

    Args:
        code: Security code (sixth character B: bond ETF; D: active bond
            ETF).
        name: Short Chinese name.

    Returns:
        ``BOND`` for bond funds, else ``EQUITY``.
    """
    if len(code) == 6 and code[5] in "BD":
        return instruments.AssetClass.BOND
    if "債" in name and "股" not in name:
        return instruments.AssetClass.BOND
    return instruments.AssetClass.EQUITY


def unlimited(record: Mapping[str, Any]) -> bool:
    """Checks whether a quotes row publishes no price limit.

    Args:
        record: ``tpex_mainboard_quotes`` row.

    Returns:
        True when the next limit-up is the 9999.95 sentinel.
    """
    return twse.number(record.get("NextLimitUp")) == UNLIMITED_UP


def etf_price_limit(
    code: str, record: Mapping[str, Any]
) -> decimal.Decimal | None:
    """Returns an ETF's daily price limit in percent.

    Verified with the published limit prices 2026-11-23: bond and other
    foreign-constituent ETFs (00679B, 00937B) are unlimited, domestic ETFs
    (006201, 00888) move 10 %; TPEx's rule multiplies 10 % by a domestic
    leveraged fund's multiple (2x: 20 %).

    Args:
        code: Security code.
        record: ``tpex_mainboard_quotes`` row.

    Returns:
        Limit, or ``None`` when unlimited.
    """
    if unlimited(record):
        return None
    return _LEVERAGED_LIMIT if code.endswith("L") else _PRICE_LIMIT


def _first_five_codes(
    rows: Iterable[Mapping[str, Any]], today: datetime.date
) -> set[str]:
    """Returns codes in their first five trading days (no price limit).

    Args:
        rows: ``tpex_ipo_no_limit`` rows.
        today: Taipei date.

    Returns:
        Codes.
    """
    codes = set()
    for row in rows:
        try:
            last = twse.roc_date(
                str(row.get("EndDateForStabilizationOperation", ""))
            )
        except ValueError:
            continue
        if today <= last:
            codes.add(str(row.get("SecuritiesCompanyCode", "")).strip())
    return codes


def _suspended_codes(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """Returns codes whose trading is suspended (停止交易).

    Args:
        rows: ``tpex_cmode`` rows.

    Returns:
        Codes.
    """
    return {
        str(row.get("SecuritiesCompanyCode", "")).strip()
        for row in rows
        if str(row.get("SuspensionOfTrading", "")).strip() in _FLAG
    }


def _english(company: Mapping[str, Any]) -> str | None:
    """Returns a company's English short name.

    Args:
        company: ``mopsfin_t187ap03_O`` row.

    Returns:
        ``Symbol`` without full-width padding, or ``None``.
    """
    text = html.unescape(str(company.get("Symbol") or "")).strip(" 　")
    return text or None


def _instrument(
    code: str,
    market_type: instruments.MarketType,
    asset: instruments.AssetClass,
    limit: decimal.Decimal | None,
    status: instruments.InstrumentStatus,
    *,
    english: str | None,
    local: str,
    now_ms: int,
) -> instruments.Instrument:
    """Builds one TPEx instrument.

    Args:
        code: Security code.
        market_type: ``stock`` or ``etf``.
        asset: Asset class.
        limit: Price limit in percent, ``None`` when unlimited.
        status: Trading status.
        english: English name.
        local: Chinese short name.
        now_ms: Fetch time.

    Returns:
        Instrument.
    """
    etf = market_type is instruments.MarketType.ETF
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(
                instruments.Venue.TPEX, market_type, code
            ),
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
        status=status,
        asset_class=asset,
        source=REST_SOURCE,
        as_of_ms=now_ms,
        listing_exchange=LISTING_EXCHANGE,
        tick_ladder=twse.ETF_TICKS if etf else twse.STOCK_TICKS,
        price_limit_pct=limit,
        board_lot=twse.BOARD_LOT,
        name=english,
        local_name=html.unescape(local).strip() or None,
    )


def parse_instruments(
    quotes: Iterable[Mapping[str, Any]],
    companies: Iterable[Mapping[str, Any]],
    first_five: Iterable[Mapping[str, Any]],
    modes: Iterable[Mapping[str, Any]],
    *,
    now_ms: int,
) -> tuple[list[instruments.Instrument], dict[str, twse.ListingMeta]]:
    """Builds TPEx mainboard stock and ETF instruments.

    Args:
        quotes: ``tpex_mainboard_quotes`` rows (the trading universe).
        companies: ``mopsfin_t187ap03_O`` rows.
        first_five: ``tpex_ipo_no_limit`` rows.
        modes: ``tpex_cmode`` rows.
        now_ms: Fetch time.

    Returns:
        Instruments and listing metadata per code (ETF listing dates are
        not published here; the daily report's listing-day mark fills
        them in later).
    """
    today = twse.taipei_date(now_ms)
    by_company = {
        str(r.get("SecuritiesCompanyCode", "")).strip(): r for r in companies
    }
    no_limit = _first_five_codes(first_five, today)
    suspended = _suspended_codes(modes)
    trading = instruments.InstrumentStatus.TRADING
    halted = instruments.InstrumentStatus.HALTED
    rows: list[instruments.Instrument] = []
    meta: dict[str, twse.ListingMeta] = {}
    for record in quotes:
        code = str(record.get("SecuritiesCompanyCode", "")).strip()
        name = str(record.get("CompanyName", "")).strip()
        company = by_company.get(code)
        listed: datetime.date | None = None
        if company is not None:
            market_type = instruments.MarketType.STOCK
            asset = instruments.AssetClass.EQUITY
            limit = None if unlimited(record) else _PRICE_LIMIT
            english = _english(company)
            try:
                listed = twse.listing_date(str(company.get("DateOfListing")))
            except ValueError:
                listed = None
        elif is_etf_code(code):
            market_type = instruments.MarketType.ETF
            asset = etf_asset_class(code, name)
            limit = etf_price_limit(code, record)
            english = None
        else:
            continue
        if code in no_limit:
            limit = None
        rows.append(
            _instrument(
                code,
                market_type,
                asset,
                limit,
                halted if code in suspended else trading,
                english=english,
                local=name,
                now_ms=now_ms,
            )
        )
        meta[code] = twse.ListingMeta(
            name, listed, market_type is instruments.MarketType.ETF
        )
    for code, company in by_company.items():
        if code in meta or code not in suspended:
            continue
        name = str(
            company.get("CompanyAbbreviation") or company.get("CompanyName")
        ).strip()
        try:
            listed = twse.listing_date(str(company.get("DateOfListing")))
        except ValueError:
            listed = None
        rows.append(
            _instrument(
                code,
                instruments.MarketType.STOCK,
                instruments.AssetClass.EQUITY,
                _PRICE_LIMIT,
                halted,
                english=_english(company),
                local=name,
                now_ms=now_ms,
            )
        )
        meta[code] = twse.ListingMeta(name, listed, False)
    return rows, meta


def _cells(row: str) -> list[str]:
    """Extracts the text of a table row's cells.

    Args:
        row: Inner HTML of a ``<tr>``.

    Returns:
        Cell texts with tags removed and whitespace collapsed.
    """
    return [
        _SPACE.sub(" ", html.unescape(_TAG.sub(" ", cell))).strip()
        for cell in _CELL.findall(row)
    ]


def _dates(text: str, year: int) -> list[datetime.date]:
    """Reads every ``M月D日`` in a text.

    Args:
        text: Cell text.
        year: Year of the schedule.

    Returns:
        Dates (invalid ones skipped).
    """
    out = []
    for month, day in _DATE.findall(text):
        try:
            out.append(datetime.date(year, int(month), int(day)))
        except ValueError:
            continue
    return out


def schedule_closures(
    payload: Mapping[str, Any], year: int, today: datetime.date
) -> set[datetime.date]:
    """Selects future weekday stock-market closures from a TPEx schedule.

    Rows name a holiday (a closure, possibly listing several dates in one
    cell; continuation rows carry only dates) or a first/last trading day
    (...開始交易日, ...最後交易日: a session). Rows about the bond systems
    (債券) are skipped; the stock system's last-trading-day row names the
    following no-trading days (...市場無交易) in its description.

    Args:
        payload: ``bulletin/tradingDate`` body (``{"data": {"html": ...},
            "stat": "ok"}``; unpublished years carry only a ``stat``
            message).
        year: Year requested.
        today: Taipei date (earlier dates are covered by the table).

    Returns:
        Weekday dates without a session.
    """
    data = payload.get("data")
    table = str(data.get("html", "")) if isinstance(data, Mapping) else ""
    closures: set[datetime.date] = set()
    sessions_named: set[datetime.date] = set()
    for row in _ROW.findall(table):
        cells = _cells(row)
        dated = [
            i
            for i, cell in enumerate(cells)
            if _DATE.search(cell) and not _DATE.sub("", cell).strip(" 、,")
        ]
        if not dated:
            continue
        at = dated[0]
        name = cells[0] if at > 0 else ""
        description = cells[-1] if len(cells) > at + 2 else ""
        days = _dates(cells[at], year)
        if "債券" in name:
            continue
        if any(marker in name for marker in _TRADING_DAY_MARKERS):
            sessions_named.update(days)
            if "股票" in name and "無交易" in description:
                closures.update(_dates(description.split("無交易")[0], year))
            continue
        closures.update(days)
    return {
        day
        for day in closures - sessions_named
        if day >= today and day.weekday() < 5
    }


def parse_trading_stock(
    payload: Mapping[str, Any],
    instrument_id: instruments.InstrumentId,
    now_ms: int,
) -> tuple[list[market.Candle], datetime.date | None]:
    """Converts one ``tradingStock`` month to daily candles.

    Args:
        payload: ``{stat: "ok", tables: [{fields, data: [[date, volume
            (thousand shares), turnover (thousand TWD), open, high, low,
            close, change, transactions], ...]}]}``.
        instrument_id: Instrument.
        now_ms: Receipt time.

    Returns:
        Ascending candles (volume in shares, turnover in TWD; days
        without a trade are skipped) and the listing day when the month
        marks one.
    """
    if str(payload.get("stat", "")).lower() != "ok":
        return [], None
    calendar = sessions.XTAI
    out = []
    listed = None
    for table in payload.get("tables") or []:
        for row in table.get("data") or []:
            try:
                day, first = trading_day(str(row[0]))
            except (ValueError, IndexError):
                continue
            if first:
                listed = day
            prices = [twse.number(row[i]) for i in (3, 4, 5, 6)]
            volume = twse.number(row[1])
            if any(p is None for p in prices) or volume is None:
                continue
            session = calendar.session(day)
            if session is None:
                _LOG.debug(
                    "tpex %s: %s is not an XTAI session", instrument_id, day
                )
                continue
            open_, high, low, close = (p for p in prices if p is not None)
            turnover = twse.number(row[2])
            count = twse.number(row[8])
            out.append(
                session_bars.candle(
                    instrument_id=instrument_id,
                    timeframe=_D1,
                    start_ms=session.open_ms,
                    open_=open_,
                    high=high,
                    low=low,
                    close=close,
                    base_volume=volume * _THOUSAND,
                    now_ms=now_ms,
                    source=REST_SOURCE,
                    is_final=session.close_ms <= now_ms,
                    quote_turnover=(
                        None if turnover is None else turnover * _THOUSAND
                    ),
                    trade_count=None if count is None else int(count),
                )
            )
    return sorted(out, key=lambda c: c.start_ms), listed


class TpexAdapter(twse.TaiwanExchangeAdapter):
    """``SparseBarsPort`` for TPEx mainboard stocks and ETFs."""

    VENUE = instruments.Venue.TPEX
    MIS_MARKET = MIS_MARKET
    MIS_SOURCE_NAME = MIS_SOURCE

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="Taipei Exchange (TPEx)",
            market_types=(
                instruments.MarketType.STOCK,
                instruments.MarketType.ETF,
            ),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                book_snapshot=True,
                classified_delta=True,
            ),
            timeframes=timeframes.MINUTE_AND_ABOVE,
            aggregated_timeframes=(
                *(
                    tf
                    for tf in timeframes.MINUTE_AND_ABOVE
                    if tf.duration_ms < _D1.duration_ms
                    and tf is not timeframes.Timeframe.M1
                ),
                timeframes.Timeframe.W1,
                timeframes.Timeframe.MN1,
            ),
            notes=(
                "TPEx mainboard (上櫃) stocks and ETFs, including bond ETFs "
                "(asset class BOND, e.g. 00679B, 00937B). The emerging "
                "stock board (興櫃), preferred shares, ETNs, warrants and "
                "the foreign-currency ETF counters (sixth code character K "
                "or C) are not served.",
                "Daily bars come from TPEx's daily trading report "
                "(tradingStock, one month per request, raw prices: not "
                "adjusted for dividends or splits). TPEx publishes volume "
                "in thousands of shares and turnover in thousands of TWD, "
                "rounded, so daily volume is exact only to 1,000 shares. "
                "They include odd-lot and after-hours fixed-price trades "
                "but not block trades. 1w and 1M are aggregated from them.",
                *twse.MIS_NOTES,
                "Price limits are 10 % of the reference price (2x domestic "
                "leveraged ETFs 20 %); securities for which TPEx publishes "
                "limit-up 9999.95 (foreign-constituent and bond ETFs, the "
                "first five days of a new listing) have none.",
                "Bulk snapshots for screening are the last session's "
                "official board-lot values (tpex_mainboard_quotes, "
                "published about 16:00 Taipei; odd lots and after-hours "
                "trades excluded); turnover is in TWD.",
                "Requests to www.tpex.org.tw are paced at one per 2 s; MIS "
                "is TWSE's host and shares the TWSE venue's pace.",
            ),
            docs_url=DOCS_URL,
            verified_at_ms=self._verified_at_ms,
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Fetches the mainboard universe and refreshes the calendar.

        Returns:
            Instruments and empty native metadata.
        """
        quotes, companies, first_five, modes = await asyncio.gather(
            self._http.get("/v1/tpex_mainboard_quotes"),
            self._http.get("/v1/mopsfin_t187ap03_O"),
            self._http.get("/v1/tpex_ipo_no_limit"),
            self._http.get("/v1/tpex_cmode"),
        )
        rows, meta = parse_instruments(
            quotes or [],
            companies or [],
            first_five or [],
            modes or [],
            now_ms=self._clock.now_ms(),
        )
        for code, known in self.listing.items():
            fresh = meta.get(code)
            if fresh is not None and fresh.listed is None and known.listed:
                meta[code] = twse.ListingMeta(
                    fresh.name, known.listed, fresh.etf
                )
        self.listing = meta
        await self.refresh_calendar()
        return rows, {}

    async def refresh_calendar(self) -> set[datetime.date]:
        """Adds closures from TPEx's published schedules to XTAI (daily).

        Returns:
            Closures added (empty when already refreshed today or when the
            schedules are unreachable).
        """
        today = twse.taipei_date(self._clock.now_ms())
        if self._calendar_day == today:
            return set()
        closures: set[datetime.date] = set()
        try:
            for year in (today.year, today.year + 1):
                payload = await self._www.get(
                    "/www/zh-tw/bulletin/tradingDate",
                    params={"date": str(year), "response": "json"},
                )
                closures |= schedule_closures(payload or {}, year, today)
        except errors.JosephError as err:
            _LOG.warning("tpex holiday schedule unavailable: %s", err.message)
            return set()
        added = {
            day for day in closures if sessions.XTAI.session(day) is not None
        }
        if added:
            sessions.XTAI.add_closures(added)
            _LOG.info("tpex: %d closures added to XTAI", len(added))
        self._calendar_day = today
        return added

    async def _fetch_month(
        self, instrument: instruments.InstrumentId, year: int, month: int
    ) -> list[market.Candle]:
        """Reads one ``tradingStock`` month.

        A month marking the listing day records it, so older months are
        not requested.

        Args:
            instrument: Instrument.
            year: Year.
            month: Month.

        Returns:
            Daily candles of the month.
        """
        code = instrument.venue_market_id
        payload = await self._www.get(
            "/www/zh-tw/afterTrading/tradingStock",
            params={
                "code": code,
                "date": f"{year:04d}/{month:02d}/01",
                "response": "json",
            },
        )
        bars, listed = parse_trading_stock(
            payload or {}, instrument, self._clock.now_ms()
        )
        known = self.listing.get(code)
        if listed is not None and known is not None and known.listed is None:
            self.listing[code] = twse.ListingMeta(known.name, listed, known.etf)
        return bars

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Returns the last session's official values of every listing.

        Returns:
            Snapshots (board-lot trading as of that session's close;
            turnover in TWD).
        """
        await self.list_instruments()
        payload = await self._http.get("/v1/tpex_mainboard_quotes")
        now = self._clock.now_ms()
        out = []
        for row in payload or []:
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            listed = self._instruments.get(code)
            close = twse.number(row.get("Close"))
            if listed is None or close is None:
                continue
            try:
                session = sessions.XTAI.session(
                    twse.roc_date(str(row.get("Date", "")))
                )
            except ValueError:
                session = None
            change = twse.number(row.get("Change"))
            previous = None if change is None else close - change
            out.append(
                market.MarketSnapshot(
                    instrument_id=listed.instrument_id,
                    feed_state=quality.FeedState.LIVE,
                    source=REST_SOURCE,
                    as_of_ms=session.close_ms if session is not None else now,
                    last=close,
                    turnover_24h_quote=twse.number(
                        row.get("TransactionAmount")
                    ),
                    volume_24h_base=twse.number(row.get("TradingShares")),
                    change_24h_pct=(
                        None
                        if previous is None or previous <= 0 or change is None
                        else change / previous * decimals.HUNDRED
                    ),
                )
            )
        return out
