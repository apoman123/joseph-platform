# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fugle free-tier Taiwan stock candles and quote snapshots.

Historical one-minute bars begin on 2023-05-23; daily bars begin in 2010.
Longer periods are aggregated on the XTAI session calendar. The free tier
does not include Taiwan futures/options or bulk quote snapshots.
"""

from __future__ import annotations

import datetime
import decimal
import zoneinfo
from collections.abc import Mapping
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import base
from joseph.market_data.adapters import http
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import session_bars

REST_URL = "https://api.fugle.tw"
DOCS_URL = "https://developer.fugle.tw/docs/data/intro/"
SOURCE = "fugle.rest"
_PATH = "/marketdata/v1.0/stock"
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")
_MINUTE_START = datetime.date(2023, 5, 23)
_DAILY_START = datetime.date(2010, 1, 1)
_M1 = timeframes.Timeframe.M1
_D1 = timeframes.Timeframe.D1
_MAX_CALLS = 12


def _date(ts_ms: int) -> datetime.date:
    """Return the Taipei calendar date of a UTC millisecond timestamp.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Taipei calendar date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _TPE).date()


def _at_open(day: datetime.date) -> int:
    """Return the regular session open on a Taipei date.

    Args:
        day: Taipei calendar date.

    Returns:
        UTC epoch milliseconds at 09:00 Taipei.
    """
    return int(
        datetime.datetime.combine(day, datetime.time(9), _TPE).timestamp()
        * 1000
    )


def _best(levels: object) -> decimal.Decimal | None:
    """Convert the best valid Fugle quote level.

    Args:
        levels: Fugle bid or ask array.

    Returns:
        Best level price, or ``None`` when absent.
    """
    if not isinstance(levels, list) or not levels:
        return None
    first = levels[0]
    if not isinstance(first, dict) or not first.get("price"):
        return None
    return base.dec(first["price"])


def _check_exchange(
    body: Mapping[str, Any], instrument: instruments.InstrumentId
) -> None:
    """Reject a response for another listed exchange.

    Args:
        body: Fugle quote or candle response.
        instrument: Requested exchange instrument.

    Raises:
        errors.VenueUnavailableError: If the response names another venue.
    """
    exchange = str(body.get("exchange", "")).upper()
    if exchange != instrument.venue.value.upper():
        raise errors.VenueUnavailableError(
            f"fugle response exchange differs from {instrument.venue.value}"
        )


class FugleSource:
    """Read free-tier stock data through a paced Fugle HTTP client.

    Attributes:
        api_key: Fugle market-data API key; never written to logs.
        http: Rate-limited client for Fugle REST.
        clock: Clock used to mark unfinished candles and receipt times.
        adjusted_daily: Whether daily and longer historical OHLC are adjusted.
    """

    def __init__(
        self,
        api_key: str,
        http: http.VenueHttp,
        clock: clock_lib.Clock,
        *,
        adjusted_daily: bool = True,
    ) -> None:
        """Store authenticated client dependencies without making a request.

        Args:
            api_key: Nonempty Fugle API key.
            http: Rate-limited Fugle REST client.
            clock: Time source.
            adjusted_daily: Normalize daily history for corporate actions.
                Intraday prices and quoted prices remain native.

        Raises:
            ValueError: If the API key is empty.
        """
        if not api_key:
            raise ValueError("JOSEPH_FUGLE_API_KEY is required")
        self.api_key = api_key
        self.http = http
        self.clock = clock
        self.adjusted_daily = adjusted_daily

    def price_adjustment(
        self,
        timeframe: timeframes.Timeframe,
    ) -> market.PriceAdjustment:
        """Reports adjusted daily history and raw intraday/quote prices.

        Args:
            timeframe: Requested candle interval, including derived resolutions.

        Returns:
            ALL for adjusted daily and longer history; RAW for intraday bars.
        """
        if self.adjusted_daily and timeframe.duration_ms >= _D1.duration_ms:
            return market.PriceAdjustment.ALL
        return market.PriceAdjustment.RAW

    @property
    def _headers(self) -> dict[str, str]:
        """Build authentication headers without retaining copies.

        Returns:
            HTTP header mapping containing the API key.
        """
        return {"X-API-KEY": self.api_key}

    async def _request(
        self, path: str, params: dict[str, str] | None = None
    ) -> Mapping[str, Any] | None:
        """Request a Fugle object, treating a no-data 404 as empty.

        Args:
            path: Fugle path below the stock API root.
            params: Optional string query parameters.

        Returns:
            JSON object, or ``None`` for 404 without observations.

        Raises:
            errors.VenueUnavailableError: For malformed responses.
            http.HttpStatusError: For other HTTP errors.
        """
        try:
            body = await self.http.get(
                f"{_PATH}/{path}", params=params, headers=self._headers
            )
        except http.HttpStatusError as err:
            if err.status_code == 404:
                return None
            raise
        if not isinstance(body, dict):
            raise errors.VenueUnavailableError("fugle returned a non-object")
        return body

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Read a per-symbol real-time stock quote.

        Args:
            instrument: TWSE or TPEx stock/ETF identifier.

        Returns:
            Snapshot whose volume is converted from lots to shares.

        Raises:
            errors.MissingDataError: If Fugle has no quote for the symbol.
        """
        body = await self._request(
            f"intraday/quote/{instrument.venue_market_id}"
        )
        if body is None:
            raise errors.MissingDataError(
                f"Fugle has no quote for {instrument}"
            )
        _check_exchange(body, instrument)
        updated = body.get("lastUpdated") or body.get("closeTime")
        as_of = int(updated) // 1000 if updated else self.clock.now_ms()
        total = body.get("total")
        volume = total.get("tradeVolume") if isinstance(total, dict) else None
        last = base.opt_dec(body.get("closePrice"))
        previous = base.opt_dec(body.get("previousClose"))
        change = (
            (last - previous) / previous * 100
            if last is not None and previous is not None and previous > 0
            else None
        )
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source=SOURCE,
            as_of_ms=as_of,
            last=last,
            best_bid=_best(body.get("bids")),
            best_ask=_best(body.get("asks")),
            volume_24h_base=base.dec(volume) * 1000
            if volume is not None
            else None,
            change_24h_pct=change,
        )

    async def fetch_candle_coverage(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> candle_rules.CandleCoverage:
        """Read historical bars and today's Fugle minutes on the XTAI grid.

        Args:
            instrument: TWSE or TPEx stock/ETF identifier.
            timeframe: Supported minute-or-longer resolution.
            start_ms: Inclusive UTC epoch milliseconds.
            end_ms: Exclusive UTC epoch milliseconds.
            limit: Optional maximum newest bars.

        Returns:
            Candles with sparse-bar coverage; ``complete`` is false when the
            bounded request budget truncates the range.

        Raises:
            errors.UnsupportedError: For seconds resolutions.
            errors.VenueUnavailableError: For malformed Fugle data.
        """
        if timeframe.is_seconds:
            raise errors.UnsupportedError(
                "Fugle free data has no seconds history"
            )
        if end_ms <= start_ms:
            return candle_rules.CandleCoverage((), (), True)
        source_tf = _M1 if timeframe.duration_ms < _D1.duration_ms else _D1
        earliest = _MINUTE_START if source_tf is _M1 else _DAILY_START
        first_day = max(_date(start_ms), earliest)
        last_day = _date(end_ms - 1)
        today = _date(self.clock.now_ms())
        if first_day > last_day:
            return candle_rules.CandleCoverage((), (), True)
        bars: list[market.Candle] = []
        covered: list[candle_rules.Span] = []
        day = min(last_day, today)
        calls = 0
        if day == today:
            body = await self._request(
                f"intraday/candles/{instrument.venue_market_id}",
                {"timeframe": "1"},
            )
            if body is not None:
                parsed = self._bars(body, instrument, _M1)
                if source_tf is _D1:
                    parsed = session_bars.aggregate(
                        parsed,
                        _D1,
                        now_ms=self.clock.now_ms(),
                        covered_to_ms=max(
                            (bar.end_ms for bar in parsed if bar.is_final),
                            default=0,
                        ),
                        source=f"{SOURCE}.adjusted"
                        if self.adjusted_daily
                        else SOURCE,
                    )
                bars.extend(parsed)
                if parsed:
                    covered.append((parsed[0].start_ms, parsed[-1].end_ms))
            day -= datetime.timedelta(days=1)
            calls += 1
        while day >= first_day and calls < _MAX_CALLS:
            step = 30 if source_tf is _M1 else 300
            from_day = max(first_day, day - datetime.timedelta(days=step - 1))
            body = await self._request(
                f"historical/candles/{instrument.venue_market_id}",
                {
                    "from": from_day.isoformat(),
                    "to": day.isoformat(),
                    "timeframe": "1" if source_tf is _M1 else "D",
                    "sort": "asc",
                    "adjusted": "true"
                    if source_tf is _D1 and self.adjusted_daily
                    else "false",
                },
            )
            if body is not None:
                self._check_adjustment(body, source_tf)
                parsed = self._bars(body, instrument, source_tf)
                bars.extend(parsed)
                if parsed:
                    covered.append((parsed[0].start_ms, parsed[-1].end_ms))
            day = from_day - datetime.timedelta(days=1)
            calls += 1
        complete = day < first_day
        ordered = candle_rules.sort_unique(bars)
        if timeframe is not source_tf:
            aggregate_source = SOURCE
            if self.price_adjustment(timeframe) is market.PriceAdjustment.ALL:
                aggregate_source = f"{SOURCE}.adjusted"
            ordered = session_bars.aggregate(
                ordered,
                timeframe,
                now_ms=self.clock.now_ms(),
                covered_to_ms=max(
                    (bar.end_ms for bar in ordered if bar.is_final), default=0
                ),
                source=aggregate_source,
            )
        return base.candle_coverage(
            ordered, start_ms, end_ms, limit, covered, complete=complete
        )

    def _check_adjustment(
        self, body: Mapping[str, Any], timeframe: timeframes.Timeframe
    ) -> None:
        """Reject an ignored corporate-action adjustment request.

        Args:
            body: Historical response object containing provider confirmation.
            timeframe: Native minute or daily resolution requested from Fugle.

        Returns:
            None when the response agrees with the configured price basis.

        Raises:
            errors.VenueUnavailableError: If adjusted daily data is unconfirmed.
        """
        if (
            timeframe is _D1
            and self.adjusted_daily
            and body.get("adjusted") is not True
        ):
            raise errors.VenueUnavailableError(
                "Fugle did not confirm adjusted history; "
                "refusing to mix price bases"
            )

    def _bars(
        self,
        body: Mapping[str, Any],
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> list[market.Candle]:
        """Parse Fugle rows, folding its 13:30 auction into the last minute.

        Args:
            body: Fugle response object with a ``data`` array.
            instrument: Exact stock/ETF identifier.
            timeframe: Source resolution, either 1m or 1d.

        Returns:
            Ascending session-aligned candles with volume in shares.

        Raises:
            errors.VenueUnavailableError: For invalid row structure.
        """
        data = body.get("data")
        if not isinstance(data, list):
            raise errors.VenueUnavailableError("fugle candles lack data array")
        _check_exchange(body, instrument)
        if not all(isinstance(row, dict) and "date" in row for row in data):
            raise errors.VenueUnavailableError("fugle candle is not an object")
        by_start: dict[int, market.Candle] = {}
        now = self.clock.now_ms()
        for row in sorted(data, key=lambda item: str(item["date"])):
            date_text = str(row["date"])
            if timeframe is _M1:
                moment = datetime.datetime.fromisoformat(date_text)
                if moment.tzinfo is None:
                    raise errors.VenueUnavailableError(
                        "fugle minute lacks timezone"
                    )
                raw_start = int(moment.timestamp() * 1000)
                auction = moment.astimezone(_TPE).time() == datetime.time(
                    13, 30
                )
                start = sessions.XTAI.floor(
                    _M1, raw_start - 1 if auction else raw_start
                )
            else:
                start = _at_open(datetime.date.fromisoformat(date_text))
                auction = False
            volume = base.dec(row.get("volume", 0))
            if timeframe is _M1:
                volume *= 1000
            previous = by_start.get(start)
            candle = session_bars.candle(
                instrument_id=instrument,
                timeframe=timeframe,
                start_ms=start,
                open_=previous.open
                if auction and previous
                else base.dec(row["open"]),
                high=max(previous.high, base.dec(row["high"]))
                if auction and previous
                else base.dec(row["high"]),
                low=min(previous.low, base.dec(row["low"]))
                if auction and previous
                else base.dec(row["low"]),
                close=base.dec(row["close"]),
                base_volume=volume
                + (previous.base_volume if auction and previous else 0),
                now_ms=now,
                source=f"{SOURCE}.adjusted"
                if timeframe is _D1 and body.get("adjusted") is True
                else SOURCE,
                is_final=(start + 120_000 < now) if timeframe is _M1 else True,
            )
            by_start[start] = candle
        return [by_start[key] for key in sorted(by_start)]

    async def close(self) -> None:
        """Close the Fugle HTTP client.

        Returns:
            None after the client closes.
        """
        await self.http.close()
