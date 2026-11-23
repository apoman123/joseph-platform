"""US Treasury par and par real yield curves (official, free, no key).

Source: the Daily Treasury Par Yield Curve Rates and Daily Treasury Par Real
Yield Curve Rates published by the US Department of the Treasury, read from
the per-year CSV export of home.treasury.gov::

    /resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv
        /{year}/all?type=daily_treasury_yield_curve
        &field_tdr_date_value={year}&page&_format=csv

(``type=daily_treasury_real_yield_curve`` for real yields). Verified live
2026-11-23 for every year 1990-2026 (nominal) and 2003-2026 (real):

* One request per curve and calendar year; the all-years export is refused
  (HTTP 403). Rows are newest first, ``Date`` is ``MM/DD/YYYY``, values are
  yields in percent with two decimals; an empty cell means the tenor was
  not published that day.
* Column labels vary (``1 Mo``, ``1.5 Month``, ``10 Yr``, real ``5 YR``) and
  tenors come and go (see ``TENORS``); labels are normalized, so a year
  without a column simply has no bar for that tenor.
* The XML feed (``pages/xml?data=...``) carries the same values in a ~20x
  larger payload, so the CSV export is used.

Bars are daily on the USBOND calendar with ``O=H=L=C`` = the yield and zero
volume; weekly and monthly bars are aggregated from them on the same
calendar. A day's yields are published in the evening (New York); the
current year is re-read lazily, at most every ``_POLL_MS`` once a new day is
due. Rate series are not tradable.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import datetime
import decimal
import logging
import re
import zoneinfo
from collections.abc import Mapping
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
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import session_bars

_LOG = logging.getLogger(__name__)

REST_URL = "https://home.treasury.gov"
CSV_PATH = (
    "/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
)
NOMINAL = "daily_treasury_yield_curve"
REAL = "daily_treasury_real_yield_curve"
DOCS_URL = "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed"
SOURCE = "treasury.csv"
_NY = zoneinfo.ZoneInfo("America/New_York")
_TF = (
    timeframes.Timeframe.D1,
    timeframes.Timeframe.W1,
    timeframes.Timeframe.MN1,
)
_D1 = timeframes.Timeframe.D1
_FIRST_YEAR = {NOMINAL: 1990, REAL: 2003}
_PUBLISH_AT = datetime.time(18, 0)
"""New York time after which a session's yields are expected."""
_POLL_MS = 15 * 60_000
"""Minimum interval between re-reads while a new day is due."""
_REFRESH_MS = 6 * 3_600_000
"""Re-read interval of the current year otherwise."""
_TICK = decimal.Decimal("0.01")
_LABEL = re.compile(r"^\s*([0-9.]+)\s*(mo|month|yr|year)s?\s*$", re.IGNORECASE)
_SESSIONS_PER_BAR = {
    timeframes.Timeframe.D1: 1,
    timeframes.Timeframe.W1: 5,
    timeframes.Timeframe.MN1: 23,
}


@dataclasses.dataclass(frozen=True, slots=True)
class Tenor:
    """One published yield series.

    Attributes:
        code: Native id (``UST10Y``, ``TIPS5Y``).
        curve: ``NOMINAL`` or ``REAL``.
        key: Normalized column key (``10y``, ``1.5m``).
        name: Human-readable name.
        first_published: First date Treasury published the tenor in this
            series (verified from the CSV export).
    """

    code: str
    curve: str
    key: str
    name: str
    first_published: datetime.date


def _date(text: str) -> datetime.date:
    """Parses an ISO date.

    Args:
        text: ``YYYY-MM-DD``.

    Returns:
        Date.
    """
    return datetime.date.fromisoformat(text)


TENORS: tuple[Tenor, ...] = (
    Tenor("UST1M", NOMINAL, "1m", "1-month par yield", _date("2001-07-31")),
    Tenor("UST1.5M", NOMINAL, "1.5m", "6-week par yield", _date("2025-02-18")),
    Tenor("UST2M", NOMINAL, "2m", "2-month par yield", _date("2018-10-16")),
    Tenor("UST3M", NOMINAL, "3m", "3-month par yield", _date("1990-01-02")),
    Tenor("UST4M", NOMINAL, "4m", "4-month par yield", _date("2022-10-19")),
    Tenor("UST6M", NOMINAL, "6m", "6-month par yield", _date("1990-01-02")),
    Tenor("UST1Y", NOMINAL, "1y", "1-year par yield", _date("1990-01-02")),
    Tenor("UST2Y", NOMINAL, "2y", "2-year par yield", _date("1990-01-02")),
    Tenor("UST3Y", NOMINAL, "3y", "3-year par yield", _date("1990-01-02")),
    Tenor("UST5Y", NOMINAL, "5y", "5-year par yield", _date("1990-01-02")),
    Tenor("UST7Y", NOMINAL, "7y", "7-year par yield", _date("1990-01-02")),
    Tenor("UST10Y", NOMINAL, "10y", "10-year par yield", _date("1990-01-02")),
    Tenor("UST20Y", NOMINAL, "20y", "20-year par yield", _date("1993-10-01")),
    Tenor("UST30Y", NOMINAL, "30y", "30-year par yield", _date("1990-01-02")),
    Tenor("TIPS5Y", REAL, "5y", "5-year par real yield", _date("2003-01-02")),
    Tenor("TIPS7Y", REAL, "7y", "7-year par real yield", _date("2003-01-02")),
    Tenor(
        "TIPS10Y", REAL, "10y", "10-year par real yield", _date("2003-01-02")
    ),
    Tenor(
        "TIPS20Y", REAL, "20y", "20-year par real yield", _date("2004-07-27")
    ),
    Tenor(
        "TIPS30Y", REAL, "30y", "30-year par real yield", _date("2010-02-22")
    ),
)
_BY_CODE = {tenor.code: tenor for tenor in TENORS}

Rows = dict[datetime.date, dict[str, decimal.Decimal]]


def column_key(label: str) -> str | None:
    """Normalizes a CSV column label to a tenor key.

    Args:
        label: Header such as ``1 Mo``, ``1.5 Month``, ``10 Yr`` or ``5 YR``.

    Returns:
        ``1m``, ``1.5m``, ``10y``, ``5y``..., or ``None`` for other columns.
    """
    match = _LABEL.match(label.strip().strip('"'))
    if match is None:
        return None
    unit = "m" if match.group(2).lower().startswith("mo") else "y"
    return f"{match.group(1)}{unit}"


def parse_csv(text: str) -> Rows:
    """Parses one year of a Treasury yield-curve CSV export.

    Args:
        text: CSV body (header ``Date,"1 Mo",...``; newest row first).

    Returns:
        Yields in percent per date and tenor key (blank cells omitted).

    Raises:
        errors.VenueUnavailableError: If the body is not the expected CSV.
    """
    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if not header or header[0].strip().lower() != "date":
        raise errors.VenueUnavailableError(
            "treasury returned an unexpected yield-curve CSV"
        )
    keys = [column_key(label) for label in header[1:]]
    rows: Rows = {}
    for record in reader:
        if not record or not record[0].strip():
            continue
        month, mday, year = (int(part) for part in record[0].split("/"))
        day = datetime.date(year, month, mday)
        values: dict[str, decimal.Decimal] = {}
        for key, cell in zip(keys, record[1:], strict=False):
            text_value = cell.strip()
            if key is None or not text_value or text_value.upper() == "N/A":
                continue
            values[key] = base.dec(text_value)
        rows[day] = values
    return rows


def ny_date(ts_ms: int) -> datetime.date:
    """Returns the New York date of an instant.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Local date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _NY).date()


def due_day(now_ms: int) -> datetime.date | None:
    """Returns the newest session whose yields should be published by now.

    Args:
        now_ms: Current time.

    Returns:
        Session date (publication expected after 18:00 New York).
    """
    calendar = sessions.USBOND
    day = ny_date(now_ms)
    for _ in range(14):
        found = calendar.session(day)
        if found is not None:
            publish = datetime.datetime.combine(day, _PUBLISH_AT, tzinfo=_NY)
            if int(publish.timestamp() * 1000) <= now_ms:
                return day
        day -= datetime.timedelta(days=1)
    return None


def parse_instrument(tenor: Tenor, now_ms: int) -> instruments.Instrument:
    """Builds the instrument of one yield series.

    Args:
        tenor: Series.
        now_ms: Listing time.

    Returns:
        Non-tradable rate instrument quoted in percent.
    """
    return instruments.Instrument(
        key=instruments.InstrumentKey(
            instrument_id=instruments.InstrumentId(
                instruments.Venue.TREASURY,
                instruments.MarketType.RATE,
                tenor.code,
            ),
            canonical_base=tenor.code,
            quote="PCT",
            settlement="USD",
        ),
        display_symbol=tenor.code,
        tick_size=_TICK,
        lot_size=decimals.ONE,
        min_qty=decimals.ZERO,
        contract_multiplier=decimals.ONE,
        linear=True,
        status=instruments.InstrumentStatus.UNKNOWN,
        asset_class=instruments.AssetClass.BOND,
        source=SOURCE,
        as_of_ms=now_ms,
        name=f"US Treasury {tenor.name}",
    )


def daily_bars(
    rows: Rows, tenor: Tenor, now_ms: int
) -> tuple[list[market.Candle], int]:
    """Converts parsed rows of one tenor to USBOND daily bars.

    Args:
        rows: Parsed yields (any years of the tenor's curve).
        tenor: Series.
        now_ms: Receipt time.

    Returns:
        Ascending bars and the number of dated rows skipped because the
        date is not a USBOND session.
    """
    instrument_id = instruments.InstrumentId(
        instruments.Venue.TREASURY, instruments.MarketType.RATE, tenor.code
    )
    calendar = sessions.USBOND
    bars: list[market.Candle] = []
    skipped = 0
    for day in sorted(rows):
        value = rows[day].get(tenor.key)
        if value is None:
            continue
        found = calendar.session(day)
        if found is None:
            skipped += 1
            continue
        bars.append(
            session_bars.candle(
                instrument_id=instrument_id,
                timeframe=_D1,
                start_ms=found.open_ms,
                open_=value,
                high=value,
                low=value,
                close=value,
                base_volume=decimals.ZERO,
                now_ms=now_ms,
                source=SOURCE,
                is_final=True,
            )
        )
    return bars, skipped


@dataclasses.dataclass(slots=True)
class _Year:
    """Cached rows of one curve and year.

    Attributes:
        rows: Parsed yields.
        fetched_ms: When they were read.
    """

    rows: Rows
    fetched_ms: int


class TreasuryAdapter(base.BaseVenueAdapter):
    """``MarketDataPort`` for Treasury par and par real yields."""

    VENUE = instruments.Venue.TREASURY
    INSTRUMENT_TTL_MS = 86_400_000

    def __init__(
        self,
        http_client: http.VenueHttp,
        clock: clock_lib.Clock,
        verified_at_ms: int | None = None,
    ) -> None:
        """Initializes the adapter.

        Args:
            http_client: HTTP client for ``REST_URL``.
            clock: Time source.
            verified_at_ms: When capabilities were last verified live.
        """
        super().__init__(http_client, clock)
        self._verified_at_ms = verified_at_ms
        self._years: dict[tuple[str, int], _Year] = {}
        self._year_locks: dict[tuple[str, int], asyncio.Lock] = {}

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns verified capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.VENUE,
            display_name="US Treasury yield curves",
            market_types=(instruments.MarketType.RATE,),
            market_data=capabilities.MarketDataCapabilities(rest_history=True),
            timeframes=_TF,
            aggregated_timeframes=(
                timeframes.Timeframe.W1,
                timeframes.Timeframe.MN1,
            ),
            notes=(
                "Official Daily Treasury Par Yield Curve (UST*, from 1990) "
                "and Par Real Yield Curve (TIPS*, from 2003) rates; values "
                "are yields in percent, not prices.",
                "Rate series are published indicative values and are not "
                "tradable; candles carry O=H=L=C=yield and zero volume.",
                "Daily bars only (one per USBOND session with a published "
                "value); 1w and 1M are aggregated from them on USBOND.",
                "A day's yields are published in the New York evening; the "
                "new day is polled for from 18:00 New York at most every "
                "15 minutes. There is no intraday or streaming data.",
                "Tenors start and stop: 1M from 2001-07-31, 20Y from "
                "1993-10-01, 2M from 2018-10-16, 4M from 2022-10-19, 1.5M "
                "(6-week) from 2025-02-18, TIPS20Y from 2004-07-27, TIPS30Y "
                "from 2010-02-22; the 30Y par yield was not published "
                "2002-02-19 to 2006-02-08 and the 3M on 2008-12-10/18/24. "
                "Days without a value have no bar.",
                "Treasury publishes nothing on some USBOND sessions: most "
                "Good Fridays (it did publish in 2015, 2021 and 2026), "
                "Martin Luther King Jr. Day before 1998 and the NYSE special "
                "closures 1994-04-27, 2001-09-11/12, 2004-06-11, 2012-10-30 "
                "and 2018-12-05; those sessions have no bar.",
            ),
            docs_url=DOCS_URL,
            verified_at_ms=self._verified_at_ms,
        )

    def instrument_id(self, venue_market_id: str) -> instruments.InstrumentId:
        """Builds the canonical id of a series.

        Args:
            venue_market_id: Series code such as ``UST10Y``.

        Returns:
            Canonical instrument id.
        """
        return instruments.InstrumentId(
            self.VENUE, instruments.MarketType.RATE, venue_market_id
        )

    async def _load_instruments(
        self,
    ) -> tuple[list[instruments.Instrument], dict[str, Mapping[str, Any]]]:
        """Lists the published series (no request needed).

        Returns:
            Instruments and empty metadata.
        """
        now = self._clock.now_ms()
        return [parse_instrument(tenor, now) for tenor in TENORS], {}

    def _stale(self, curve: str, year: int, entry: _Year, now_ms: int) -> bool:
        """Decides whether a cached year must be re-read.

        Args:
            curve: Curve type.
            year: Calendar year.
            entry: Cached rows.
            now_ms: Current time.

        Returns:
            True when newer rows can exist.
        """
        due = due_day(now_ms)
        if due is None or due.year < year:
            return False
        if due.year > year:
            # A past year is complete once read after its last session.
            return ny_date(entry.fetched_ms).year == year and (
                max(entry.rows, default=datetime.date(year, 1, 1))
                < datetime.date(year, 12, 31)
                and not self._year_complete(entry, year)
            )
        newest = max(entry.rows, default=None)
        age = now_ms - entry.fetched_ms
        if newest is None or newest < due:
            return age >= _POLL_MS
        return age >= _REFRESH_MS

    @staticmethod
    def _year_complete(entry: _Year, year: int) -> bool:
        """Checks that a past year's rows reach its last USBOND session.

        Args:
            entry: Cached rows.
            year: Calendar year.

        Returns:
            True when the newest row is the year's last session.
        """
        calendar = sessions.USBOND
        day = datetime.date(year, 12, 31)
        while calendar.session(day) is None:
            day -= datetime.timedelta(days=1)
        return max(entry.rows, default=day - datetime.timedelta(days=1)) >= day

    async def _year(self, curve: str, year: int) -> Rows:
        """Returns one curve-year, reading it when missing or stale.

        Args:
            curve: ``NOMINAL`` or ``REAL``.
            year: Calendar year.

        Returns:
            Parsed rows (empty before the curve's first year).
        """
        if year < _FIRST_YEAR[curve]:
            return {}
        key = (curve, year)
        lock = self._year_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self._clock.now_ms()
            entry = self._years.get(key)
            if entry is not None and not self._stale(curve, year, entry, now):
                return entry.rows
            text = await self._http.get_text(
                CSV_PATH.format(year=year),
                params={
                    "type": curve,
                    "field_tdr_date_value": str(year),
                    "page": "",
                    "_format": "csv",
                },
            )
            rows = parse_csv(text)
            self._years[key] = _Year(rows, self._clock.now_ms())
            return rows

    def _tenor(self, instrument: instruments.InstrumentId) -> Tenor:
        """Resolves the series of an instrument.

        Args:
            instrument: Canonical id.

        Returns:
            Tenor.

        Raises:
            errors.NotFoundError: For an unknown series.
        """
        tenor = _BY_CODE.get(instrument.venue_market_id)
        if instrument.venue is not self.VENUE or tenor is None:
            raise errors.NotFoundError(f"unknown instrument {instrument}")
        return tenor

    async def _daily(
        self,
        tenor: Tenor,
        start_ms: int,
        end_ms: int,
        wanted: int | None,
    ) -> tuple[list[market.Candle], int]:
        """Collects daily bars of a range, newest years first.

        Args:
            tenor: Series.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            wanted: Stop once this many bars in range are collected.

        Returns:
            Ascending daily bars in ``[start, end)`` and the close of the
            newest published session of the curve (data is complete before
            it).
        """
        now = self._clock.now_ms()
        first_year = max(
            _FIRST_YEAR[tenor.curve], ny_date(max(start_ms, 0)).year
        )
        last_year = ny_date(min(end_ms - 1, now)).year
        rows: Rows = {}
        newest: datetime.date | None = None
        for year in range(last_year, first_year - 1, -1):
            year_rows = await self._year(tenor.curve, year)
            if year_rows and newest is None:
                newest = max(year_rows)
            rows.update(year_rows)
            if wanted is not None:
                count = sum(
                    1
                    for bar in daily_bars(rows, tenor, now)[0]
                    if start_ms <= bar.start_ms < end_ms
                )
                if count >= wanted:
                    break
        bars, skipped = daily_bars(rows, tenor, now)
        if skipped:
            _LOG.debug(
                "treasury %s: %d rows outside USBOND sessions",
                tenor.code,
                skipped,
            )
        covered = 0
        if newest is not None:
            found = sessions.USBOND.session(newest)
            covered = found.close_ms if found is not None else 0
        return [b for b in bars if start_ms <= b.start_ms < end_ms], covered

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches daily, weekly or monthly yield bars.

        Args:
            instrument: Series.
            timeframe: ``1d``, ``1w`` or ``1M``.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending candles on the USBOND calendar.
        """
        self._require_timeframe(timeframe)
        tenor = self._tenor(instrument)
        if end_ms <= start_ms:
            return []
        calendar = sessions.USBOND
        try:
            grid_start = calendar.floor(timeframe, start_ms)
        except ValueError:
            grid_start = start_ms
        wanted = None if limit is None else limit * _SESSIONS_PER_BAR[timeframe]
        daily, covered = await self._daily(
            tenor, min(grid_start, start_ms), end_ms, wanted
        )
        now = self._clock.now_ms()
        rows = (
            daily
            if timeframe is _D1
            else session_bars.aggregate(
                daily, timeframe, now_ms=now, covered_to_ms=covered
            )
        )
        return base.clip_range(rows, start_ms, end_ms, limit)

    async def _latest(self, tenor: Tenor) -> market.Candle | None:
        """Returns the newest published daily bar of a series.

        Args:
            tenor: Series.

        Returns:
            Bar, or ``None`` when none was published in the last two years.
        """
        now = self._clock.now_ms()
        bars, _ = await self._daily(tenor, now - 2 * 366 * 86_400_000, now, 1)
        return bars[-1] if bars else None

    def _snapshot(
        self, tenor: Tenor, bar: market.Candle
    ) -> market.MarketSnapshot:
        """Builds a snapshot from the newest bar.

        Args:
            tenor: Series.
            bar: Newest daily bar.

        Returns:
            Snapshot with ``last`` = the yield in percent.
        """
        return market.MarketSnapshot(
            instrument_id=self.instrument_id(tenor.code),
            feed_state=quality.FeedState.LIVE,
            source=SOURCE,
            as_of_ms=bar.end_ms,
            last=bar.close,
        )

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Returns the newest published yield.

        Args:
            instrument: Series.

        Returns:
            Snapshot (no bid/ask, volume or change fields).

        Raises:
            errors.MissingDataError: Without a recent published value.
        """
        tenor = self._tenor(instrument)
        bar = await self._latest(tenor)
        if bar is None:
            raise errors.MissingDataError(f"no recent value for {instrument}")
        return self._snapshot(tenor, bar)

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Returns the newest published value of every series.

        Returns:
            Snapshots of series published in the last two years.
        """
        rows = []
        for tenor in TENORS:
            bar = await self._latest(tenor)
            if bar is not None:
                rows.append(self._snapshot(tenor, bar))
        return rows

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Rate series have no order book.

        Args:
            instrument: Series.
            depth: Ignored.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument, depth
        raise errors.UnsupportedError("treasury yields have no order book")

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Rate series have no open interest.

        Args:
            instrument: Series.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("treasury yields have no open interest")

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Rate series have no funding.

        Args:
            instrument: Series.

        Raises:
            errors.UnsupportedError: Always.
        """
        del instrument
        raise errors.UnsupportedError("treasury yields have no funding")
