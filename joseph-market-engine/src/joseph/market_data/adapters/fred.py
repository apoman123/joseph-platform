# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""FRED daily Treasury yield observations through the free API key tier.

FRED series are separate instrument IDs from the Treasury par yield curve:
``treasury:rate:FRED_DGS10`` never silently replaces ``UST10Y``. No
intraday bar or streaming capability is inferred from daily observations.
"""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Mapping

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
from joseph.market_data.domain import session_bars

REST_URL = "https://api.stlouisfed.org"
DOCS_URL = "https://fred.stlouisfed.org/docs/api/fred/series_observations.html"
SOURCE = "fred.rest"
SERIES: Mapping[str, str] = {
    "DGS1MO": "1-month constant-maturity Treasury yield",
    "DGS3MO": "3-month constant-maturity Treasury yield",
    "DGS6MO": "6-month constant-maturity Treasury yield",
    "DGS1": "1-year constant-maturity Treasury yield",
    "DGS2": "2-year constant-maturity Treasury yield",
    "DGS5": "5-year constant-maturity Treasury yield",
    "DGS10": "10-year constant-maturity Treasury yield",
    "DGS20": "20-year constant-maturity Treasury yield",
    "DGS30": "30-year constant-maturity Treasury yield",
    "T10Y2Y": "10-year minus 2-year Treasury yield spread",
    "T10Y3M": "10-year minus 3-month Treasury yield spread",
    "DFII10": "10-year inflation-indexed Treasury yield",
    "T10YIE": "10-year breakeven inflation rate",
}
_D1 = timeframes.Timeframe.D1
_TICK = decimal.Decimal("0.01")


def series_id(instrument: instruments.InstrumentId) -> str | None:
    """Resolve a namespaced FRED instrument to its series code.

    Args:
        instrument: Requested instrument.

    Returns:
        Whitelisted FRED series code, or ``None`` for another ID.
    """
    if (
        instrument.venue is instruments.Venue.TREASURY
        and instrument.market_type is instruments.MarketType.RATE
        and instrument.venue_market_id.startswith("FRED_")
    ):
        code = instrument.venue_market_id.removeprefix("FRED_")
        return code if code in SERIES else None
    return None


class FredSource:
    """Fetch whitelisted FRED daily observations for Treasury rate charts.

    Attributes:
        api_key: FRED API key, kept out of logs.
        http: Rate-limited FRED HTTP client.
        clock: Time source for receipt timestamps.
    """

    def __init__(
        self, api_key: str, http: http.VenueHttp, clock: clock_lib.Clock
    ) -> None:
        """Store the key and client without issuing a request.

        Args:
            api_key: Nonempty registered FRED API key.
            http: Rate-limited HTTP client.
            clock: Time source.

        Raises:
            ValueError: If the key is empty.
        """
        if not api_key:
            raise ValueError("JOSEPH_FRED_API_KEY is required")
        self.api_key = api_key
        self.http = http
        self.clock = clock

    def list_instruments(self) -> list[instruments.Instrument]:
        """List explicitly whitelisted, non-tradable FRED rate series.

        Returns:
            Treasury rate instruments with source ``fred.rest``.
        """
        now = self.clock.now_ms()
        return [
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=instruments.InstrumentId(
                        instruments.Venue.TREASURY,
                        instruments.MarketType.RATE,
                        f"FRED_{code}",
                    ),
                    canonical_base=f"FRED_{code}",
                    quote="PCT",
                    settlement="USD",
                ),
                display_symbol=code,
                tick_size=_TICK,
                lot_size=decimals.ONE,
                min_qty=decimals.ZERO,
                contract_multiplier=decimals.ONE,
                linear=True,
                status=instruments.InstrumentStatus.UNKNOWN,
                asset_class=instruments.AssetClass.BOND,
                source=SOURCE,
                as_of_ms=now,
                name=f"FRED {name}",
            )
            for code, name in SERIES.items()
        ]

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetch published daily observations and aggregate longer periods.

        Args:
            instrument: ``treasury:rate:FRED_*`` series ID.
            timeframe: 1d, 1w or 1M.
            start_ms: Inclusive UTC epoch milliseconds.
            end_ms: Exclusive UTC epoch milliseconds.
            limit: Optional maximum newest bars.

        Returns:
            Ascending bars with percent values and zero volume.

        Raises:
            errors.NotFoundError: For an unknown FRED series.
            errors.UnsupportedError: For intraday resolutions.
            errors.VenueUnavailableError: For malformed FRED responses.
        """
        code = series_id(instrument)
        if code is None:
            raise errors.NotFoundError(f"unknown FRED instrument {instrument}")
        if timeframe not in (
            _D1,
            timeframes.Timeframe.W1,
            timeframes.Timeframe.MN1,
        ):
            raise errors.UnsupportedError("FRED observations are daily only")
        if end_ms <= start_ms:
            return []
        calendar = sessions.USBOND
        try:
            grid_start = calendar.floor(timeframe, start_ms)
        except ValueError:
            grid_start = start_ms
        from_day = datetime.datetime.fromtimestamp(
            min(grid_start, start_ms) / 1000, datetime.UTC
        ).date()
        to_day = datetime.datetime.fromtimestamp(
            min(end_ms - 1, self.clock.now_ms()) / 1000, datetime.UTC
        ).date()
        if from_day > to_day:
            return []
        body = await self.http.get(
            "/fred/series/observations",
            params={
                "series_id": code,
                "api_key": self.api_key,
                "file_type": "json",
                "observation_start": from_day.isoformat(),
                "observation_end": to_day.isoformat(),
                "sort_order": "asc",
                "limit": 100000,
            },
        )
        if not isinstance(body, dict) or not isinstance(
            body.get("observations"), list
        ):
            raise errors.VenueUnavailableError(
                "FRED observations are malformed"
            )
        bars: list[market.Candle] = []
        now = self.clock.now_ms()
        for row in body["observations"]:
            if not isinstance(row, dict):
                raise errors.VenueUnavailableError(
                    "FRED observation is malformed"
                )
            value = row.get("value")
            if value in (None, "."):
                continue
            day = datetime.date.fromisoformat(str(row["date"]))
            found = calendar.session(day)
            if found is None:
                continue
            rate = base.dec(value)
            bars.append(
                session_bars.candle(
                    instrument_id=instrument,
                    timeframe=_D1,
                    start_ms=found.open_ms,
                    open_=rate,
                    high=rate,
                    low=rate,
                    close=rate,
                    base_volume=decimals.ZERO,
                    now_ms=now,
                    source=SOURCE,
                    is_final=True,
                )
            )
        if timeframe is not _D1:
            bars = session_bars.aggregate(
                bars,
                timeframe,
                now_ms=now,
                covered_to_ms=max((bar.end_ms for bar in bars), default=0),
                source=SOURCE,
            )
        return base.clip_range(bars, start_ms, end_ms, limit)

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Return the latest published daily FRED observation.

        Args:
            instrument: Whitelisted FRED rate instrument.

        Returns:
            Latest yield or spread as a non-tradable snapshot.

        Raises:
            errors.MissingDataError: If no observation exists in two years.
        """
        now = self.clock.now_ms()
        bars = await self.fetch_candles(
            instrument,
            _D1,
            now - 2 * 366 * 86_400_000,
            now,
            limit=1,
        )
        if not bars:
            raise errors.MissingDataError(
                f"no recent FRED value for {instrument}"
            )
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.LIVE,
            source=SOURCE,
            as_of_ms=bars[-1].end_ms,
            last=bars[-1].close,
        )

    async def close(self) -> None:
        """Close the FRED HTTP client.

        Returns:
            None after the client closes.
        """
        await self.http.close()
