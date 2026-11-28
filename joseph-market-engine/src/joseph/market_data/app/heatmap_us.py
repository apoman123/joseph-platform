# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""US stock and ETF heatmaps.

Members, sector, industry and market capitalization come from the Nasdaq.com
screener (and Wikipedia / Nasdaq.com for index members). Quotes come from
the configured Alpaca feed when its keys exist: session quotes for the
one-day and extended-hours metrics and split-adjusted daily bars (cached per
New York day) for performance, relative volume and 1W/1M sizes. Without
Alpaca the screener's delayed last sale, change and volume are shown and the
metrics that need bars are reported unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import decimal
import zoneinfo
from collections.abc import Coroutine
from collections.abc import Sequence
from typing import Any

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.market_data.adapters import us_equities
from joseph.market_data.app import heatmap_cache
from joseph.market_data.app import heatmap_catalog as catalog
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import heatmap_parts as parts
from joseph.market_data.app import ports
from joseph.market_data.app import service
from joseph.market_data.domain import heatmap_metrics as rules

DAY_MS = 24 * 3_600_000
FALLBACK_TTL_MS = 10 * 60_000
"""Screener lifetime while it is also the quote source (delayed quotes)."""
QUOTE_TTL_MS = 15_000
"""Lifetime of Alpaca session quotes."""
HISTORY_DAYS = 400
"""Calendar days of daily bars read (one year plus the YTD margin)."""
US_VENUES = (
    instruments.Venue.NASDAQ,
    instruments.Venue.NYSE,
    instruments.Venue.CBOE,
)
_NEW_YORK = zoneinfo.ZoneInfo("America/New_York")
SCREENER_LABEL = "Nasdaq.com screener (unofficial, delayed)"
NO_KEYS = (
    "needs Alpaca daily bars: set JOSEPH_ALPACA_KEY_ID and "
    "JOSEPH_ALPACA_SECRET_KEY"
)
INTRADAY = "intraday changes are not loaded for heatmaps (daily data only)"
NO_CHART = (
    "No chart on configured venues: US charts need Alpaca keys "
    "(JOSEPH_ALPACA_KEY_ID, JOSEPH_ALPACA_SECRET_KEY)"
)
_BAR_METRICS = (
    "perf_1w",
    "perf_1m",
    "perf_3m",
    "perf_6m",
    "perf_ytd",
    "perf_1y",
    "relative_volume",
    "volume_1w",
    "volume_1m",
    "turnover_1w",
    "turnover_1m",
)


def _fallback_metrics(
    row: us_equities.ScreenerRow,
) -> dict[str, decimal.Decimal | None]:
    """Reads the screener's own delayed values.

    Args:
        row: Screener row.

    Returns:
        Market cap, change, volume and an estimated turnover.
    """
    turnover = None
    if row.last is not None and row.volume is not None:
        turnover = (row.last * row.volume).quantize(decimals.ONE)
    return {
        "market_cap": row.market_cap.quantize(decimals.ONE)
        if row.market_cap is not None
        else None,
        "change_1d": row.change_pct,
        "volume_1d": row.volume,
        "turnover_1d": turnover,
    }


class UsEquities:
    """Builds the US stock and ETF data sets.

    Attributes:
        source: Screener and index-member source, or None when disabled.
    """

    def __init__(
        self,
        source: us_equities.UsEquitySource | None,
        market_data: service.MarketDataService,
        index: parts.InstrumentIndex,
        clock: clock_lib.Clock,
    ) -> None:
        """Creates empty caches.

        Args:
            source: Screener and member source, or None (no public sources).
            market_data: Market data of the configured venues (Alpaca).
            index: Enabled-instrument lookup.
            clock: Time source.
        """
        self.source = source
        self._market = market_data
        self._index = index
        self._clock = clock
        self._screener: dict[
            str, heatmap_cache.SourceCache[list[us_equities.ScreenerRow]]
        ] = {
            exchange: heatmap_cache.SourceCache(
                f"nasdaq.com screener ({exchange})", clock, DAY_MS
            )
            for exchange in us_equities.EXCHANGES
        }
        self._member_lists: dict[str, heatmap_cache.SourceCache[list[str]]] = {
            "us_sp500": heatmap_cache.SourceCache(
                "wikipedia S&P 500", clock, DAY_MS
            ),
            "us_nasdaq100": heatmap_cache.SourceCache(
                "nasdaq.com Nasdaq-100", clock, DAY_MS
            ),
        }
        self._quotes: dict[
            instruments.InstrumentId, tuple[int, rules.SessionQuote]
        ] = {}
        self._quotes_ms: int | None = None
        self._bars: dict[
            instruments.InstrumentId,
            tuple[datetime.date, list[rules.DailyBar]],
        ] = {}
        self._bars_ms: int | None = None
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}

    def alpaca_venues(self) -> list[instruments.Venue]:
        """Lists the enabled US venues whose adapter serves heatmap quotes.

        Returns:
            Venues with ``HeatmapQuotesPort`` adapters.
        """
        return [
            venue
            for venue in US_VENUES
            if venue in self._market.registry.venues
            and isinstance(self._market.adapter(venue), ports.HeatmapQuotesPort)
        ]

    def unavailable(self, spec: catalog.DatasetSpec) -> str | None:
        """Explains why a data set cannot be served.

        Args:
            spec: US data set.

        Returns:
            Reason, or None when it can be served.
        """
        if spec.type == "etfs":
            if not self.alpaca_venues():
                return (
                    "US ETF quotes need Alpaca keys (JOSEPH_ALPACA_KEY_ID, "
                    "JOSEPH_ALPACA_SECRET_KEY); no keyless source lists ETF "
                    "quotes"
                )
            return None
        if self.source is None:
            return "public heatmap sources are disabled (DEMO-only engine)"
        return None

    def _today(self) -> datetime.date:
        """Returns the current New York date.

        Returns:
            Date.
        """
        moment = datetime.datetime.fromtimestamp(
            self._clock.now_ms() / 1000, _NEW_YORK
        )
        return moment.date()

    async def _run(
        self,
        key: str,
        work: Coroutine[Any, Any, None] | None,
        budget_s: float,
    ) -> None:
        """Waits for a background job within a budget.

        Args:
            key: Job name.
            work: Newly started job, or None to wait for a running one.
            budget_s: Seconds to wait (0 does not wait).
        """
        task = self._jobs.get(key)
        if work is not None:
            task = asyncio.create_task(work)
            self._jobs[key] = task
        if task is not None and not task.done() and budget_s > 0:
            await asyncio.wait({task}, timeout=budget_s)

    def _running(self, key: str) -> bool:
        """Tells whether a background job runs.

        Args:
            key: Job name.

        Returns:
            True while it runs.
        """
        task = self._jobs.get(key)
        return task is not None and not task.done()

    async def _load_quotes(
        self, ids: Sequence[instruments.InstrumentId]
    ) -> None:
        """Loads session quotes per venue into the cache.

        Args:
            ids: Instruments to refresh.
        """
        for venue in dict.fromkeys(item.venue for item in ids):
            adapter = self._market.adapter(venue)
            if not isinstance(adapter, ports.HeatmapQuotesPort):
                continue
            group = [item for item in ids if item.venue == venue]
            try:
                loaded = await adapter.fetch_session_quotes(group)
            except errors.JosephError as err:
                self._errors["alpaca quotes"] = err.message[:300]
                continue
            self._errors.pop("alpaca quotes", None)
            now = self._clock.now_ms()
            self._quotes_ms = now
            for key, quote in loaded.items():
                self._quotes[key] = (now, quote)

    async def _load_bars(
        self, ids: Sequence[instruments.InstrumentId], today: datetime.date
    ) -> None:
        """Loads daily bars per venue into the per-day cache.

        Args:
            ids: Instruments without today's history.
            today: New York date the history is cached for.
        """
        start = today - datetime.timedelta(days=HISTORY_DAYS)
        for venue in dict.fromkeys(item.venue for item in ids):
            adapter = self._market.adapter(venue)
            if not isinstance(adapter, ports.HeatmapQuotesPort):
                continue
            group = [item for item in ids if item.venue == venue]
            try:
                loaded = await adapter.fetch_daily_bars_batch(group, start)
            except errors.JosephError as err:
                self._errors["alpaca daily bars"] = err.message[:300]
                continue
            self._errors.pop("alpaca daily bars", None)
            self._bars_ms = self._clock.now_ms()
            for key, bars in loaded.items():
                self._bars[key] = (today, bars)

    async def _alpaca(
        self,
        ids: Sequence[instruments.InstrumentId],
        budget: parts.Budget,
        *,
        history: Sequence[instruments.InstrumentId],
    ) -> bool:
        """Refreshes quotes and history in the background within a budget.

        Args:
            ids: Instruments needing session quotes.
            budget: Request budget.
            history: Instruments needing daily bars.

        Returns:
            True while a load is still running for instruments without data.
        """
        now = self._clock.now_ms()
        stale = [
            item
            for item in ids
            if now - self._quotes.get(item, (-QUOTE_TTL_MS, None))[0]
            >= QUOTE_TTL_MS
        ]
        missing = [item for item in ids if item not in self._quotes]
        if stale and not self._running("quotes"):
            await self._run("quotes", self._load_quotes(stale), 0)
        if missing:
            await self._run("quotes", None, budget.left())
        today = self._today()
        old = [
            item
            for item in history
            if self._bars.get(item, (None, []))[0] != today
        ]
        if old and not self._running("bars"):
            await self._run("bars", self._load_bars(old, today), 0)
        if old:
            await self._run("bars", None, budget.left())
        return (self._running("quotes") and bool(missing)) or (
            self._running("bars") and bool(old)
        )

    def _alpaca_metrics(
        self, instrument: instruments.InstrumentId
    ) -> tuple[
        dict[str, decimal.Decimal | None], decimal.Decimal | None, int | None
    ]:
        """Computes Alpaca-based metrics of one instrument.

        Args:
            instrument: Listing.

        Returns:
            Metrics, price and quote time (all empty without a quote).
        """
        cached = self._quotes.get(instrument)
        if cached is None:
            return {}, None, None
        quote = cached[1]
        values: dict[str, decimal.Decimal | None] = dict(
            rules.session_metrics(quote)
        )
        price = quote.today.close if quote.today else quote.last
        history = self._bars.get(instrument)
        if history is not None and quote.today is not None:
            bars = [bar for bar in history[1] if bar.day != quote.today.day]
            bars.append(quote.today)
            values.update(
                rules.history_metrics(
                    bars, last=quote.today.close, today=quote.today.day
                )
            )
        return values, price, quote.last_ms

    async def _screener_rows(
        self, spec: catalog.DatasetSpec, budget: parts.Budget, part: parts.Part
    ) -> list[us_equities.ScreenerRow]:
        """Loads the screener rows of a data set's exchanges.

        Args:
            spec: Data set.
            budget: Request budget.
            part: Result collecting errors and the loading state.

        Returns:
            Rows, one per symbol.
        """
        source = self.source
        if source is None:
            return []
        exchanges = {
            "us_nasdaq_composite": ["nasdaq"],
            "us_nyse": ["nyse"],
        }.get(spec.id, list(us_equities.EXCHANGES))
        ttl = DAY_MS if self.alpaca_venues() else FALLBACK_TTL_MS

        async def one(exchange: str) -> list[us_equities.ScreenerRow] | None:
            """Reads one exchange's cached rows.

            Args:
                exchange: Screener exchange filter.

            Returns:
                Rows, or None while loading.
            """
            return await self._screener[exchange].get(
                lambda: source.screener(exchange),
                budget_s=budget.left(),
                ttl_ms=ttl,
            )

        loaded = await asyncio.gather(*(one(name) for name in exchanges))
        rows: dict[str, us_equities.ScreenerRow] = {}
        for exchange, result in zip(exchanges, loaded, strict=True):
            cache = self._screener[exchange]
            part.note_cache(cache.name, cache)
            for row in result or ():
                rows.setdefault(row.symbol, row)
        stamps = [
            stamp
            for name in exchanges
            if (stamp := self._screener[name].loaded_ms) is not None
        ]
        part.provenance.append(
            models.HeatmapProvenance(
                field="Sector, industry & market cap",
                source=SCREENER_LABEL,
                as_of_ms=min(stamps) if stamps else None,
                note="Fetched every 24 h; Nasdaq.com sector taxonomy",
            )
        )
        return list(rows.values())

    async def _members(
        self, spec: catalog.DatasetSpec, budget: parts.Budget, part: parts.Part
    ) -> set[str] | None:
        """Loads index members.

        Args:
            spec: Data set.
            budget: Request budget.
            part: Result collecting errors and provenance.

        Returns:
            Member tickers, or None for exchange-wide data sets.
        """
        cache = self._member_lists.get(spec.id)
        source = self.source
        if cache is None or source is None:
            return None
        load = source.sp500 if spec.id == "us_sp500" else source.nasdaq100
        members = await cache.get(load, budget_s=budget.left())
        part.note_cache(cache.name, cache)
        part.provenance.append(
            models.HeatmapProvenance(
                field="Members",
                source="Wikipedia: List of S&P 500 companies"
                if spec.id == "us_sp500"
                else "Nasdaq.com Nasdaq-100 list",
                as_of_ms=cache.loaded_ms,
            )
        )
        return set(members or ())

    async def _listings(
        self, budget: parts.Budget, part: parts.Part
    ) -> dict[str, instruments.Instrument]:
        """Maps tickers to enabled US instruments.

        Args:
            budget: Request budget.
            part: Result noting a still-loading registry.

        Returns:
            Instruments by exchange ticker (stocks before ETFs).
        """
        rows = await self._index.rows(budget, US_VENUES)
        if rows is None:
            part.pending = True
            return {}
        out: dict[str, instruments.Instrument] = {}
        for row in sorted(
            rows,
            key=lambda item: item.instrument_id.market_type.value != "stock",
        ):
            out.setdefault(row.instrument_id.venue_market_id, row)
        return out

    def _quote_provenance(self, part: parts.Part, alpaca: bool) -> None:
        """Adds quote provenance, reasons and errors.

        Args:
            part: Result.
            alpaca: Whether Alpaca quotes are used.
        """
        part.source_errors.update(self._errors if alpaca else {})
        for metric in ("change_1h", "change_4h"):
            part.reasons[metric] = INTRADAY
        if alpaca:
            feed = "Alpaca"
            with contextlib.suppress(errors.JosephError):
                feed = (
                    self._market.adapter(self.alpaca_venues()[0])
                    .capabilities()
                    .display_name
                )
            part.provenance.append(
                models.HeatmapProvenance(
                    field="Quotes, change, gap, volatility, volume",
                    source=f"{feed} snapshots",
                    as_of_ms=self._quotes_ms,
                    note="Configured feed coverage applies (IEX is not "
                    "the consolidated tape)",
                )
            )
            part.provenance.append(
                models.HeatmapProvenance(
                    field="Performance, relative volume, 1W/1M sizes",
                    source="Alpaca daily bars (split-adjusted)",
                    as_of_ms=self._bars_ms,
                    note="Cached per New York trading day",
                )
            )
            for metric in ("premarket_change", "postmarket_change"):
                part.reasons[metric] = (
                    "shown only while the latest trade is outside regular hours"
                )
            return
        part.provenance.append(
            models.HeatmapProvenance(
                field="Quotes, change, volume",
                source=SCREENER_LABEL,
                note="Delayed last sale; turnover estimated as last sale × "  # noqa: RUF001 - UI label sign
                "volume",
            )
        )
        for metric in _BAR_METRICS:
            part.reasons[metric] = NO_KEYS
        for metric in ("premarket_change", "postmarket_change"):
            part.reasons[metric] = "needs Alpaca latest trades (keys)"
        for metric in ("gap", "volatility_1d"):
            part.reasons[metric] = (
                "the Nasdaq.com screener publishes no open, high or low"
            )

    async def stocks(
        self, spec: catalog.DatasetSpec, limit: int, budget: parts.Budget
    ) -> parts.Part:
        """Builds a US stock data set.

        Args:
            spec: US stock data set.
            limit: Maximum tiles (largest market caps).
            budget: Request budget.

        Returns:
            Tiles and metadata.
        """
        part = parts.Part()
        rows = await self._screener_rows(spec, budget, part)
        members = await self._members(spec, budget, part)
        if members is not None:
            rows = [row for row in rows if row.symbol in members]
            if members and rows and len(rows) < len(members):
                part.source_errors["members"] = (
                    f"{len(members) - len(rows)} members are missing from "
                    "the screener"
                )
        part.universe_count = len(rows)
        rows.sort(
            key=lambda row: (
                -(row.market_cap if row.market_cap is not None else -1),
                row.symbol,
            )
        )
        rows = rows[:limit]
        listings = await self._listings(budget, part)
        alpaca = bool(self.alpaca_venues())
        ids = [
            listings[row.symbol].instrument_id
            for row in rows
            if row.symbol in listings
        ]
        if alpaca and ids:
            part.pending |= await self._alpaca(ids, budget, history=ids)
        for row in rows:
            listing = listings.get(row.symbol)
            values = _fallback_metrics(row)
            price, as_of, error = row.last, None, None
            if alpaca and listing is not None:
                live, live_price, as_of = self._alpaca_metrics(
                    listing.instrument_id
                )
                if live:
                    values = {"market_cap": values["market_cap"], **live}
                    price = live_price
                else:
                    error = "Alpaca quote pending; delayed screener values"
            elif alpaca:
                error = "not listed on the configured US venues"
            part.tiles.append(
                models.HeatmapTile(
                    key=row.symbol,
                    symbol=row.symbol,
                    name=row.name,
                    group=row.sector,
                    industry=row.industry,
                    exchange=row.exchange,
                    instrument_id=str(listing.instrument_id)
                    if listing
                    else None,
                    chart_unavailable_reason=None if listing else NO_CHART,
                    price=rules.to_text(price) if price is not None else None,
                    metrics=parts.texts(values),
                    as_of_ms=as_of,
                    error=error,
                )
            )
        self._quote_provenance(part, alpaca)
        return part

    async def etfs(
        self, spec: catalog.DatasetSpec, limit: int, budget: parts.Budget
    ) -> parts.Part:
        """Builds the US ETF data set from the configured Alpaca venues.

        Args:
            spec: ``us_etfs``.
            limit: Maximum tiles (largest turnover).
            budget: Request budget.

        Returns:
            Tiles grouped by an asset class classified from the fund name.
        """
        del spec
        part = parts.Part()
        listings = await self._listings(budget, part)
        funds = [
            row
            for row in listings.values()
            if row.instrument_id.market_type is instruments.MarketType.ETF
        ]
        part.universe_count = len(funds)
        ids = [row.instrument_id for row in funds]
        if ids:
            part.pending |= await self._alpaca(ids, budget, history=[])
        tiles = []
        for row in funds:
            values, price, as_of = self._alpaca_metrics(row.instrument_id)
            tiles.append(
                models.HeatmapTile(
                    key=row.instrument_id.venue_market_id,
                    symbol=row.display_symbol,
                    name=row.name,
                    group=rules.classify_fund(row.name),
                    exchange=row.listing_exchange,
                    instrument_id=str(row.instrument_id),
                    price=rules.to_text(price) if price is not None else None,
                    metrics=parts.texts(values),
                    as_of_ms=as_of,
                    error=None if values else "quote pending",
                )
            )
        part.tiles = parts.rank(tiles, "turnover_1d")[:limit]
        top = [
            instruments.InstrumentId.parse(tile.instrument_id)
            for tile in part.tiles
            if tile.instrument_id
        ]
        if top:
            part.pending |= await self._alpaca(top, budget, history=top)
            for index, tile in enumerate(part.tiles):
                values, price, as_of = self._alpaca_metrics(top[index])
                tile.metrics = parts.texts(values)
        part.provenance.append(
            models.HeatmapProvenance(
                field="Members",
                source="Configured US exchange listings (ETFs)",
            )
        )
        part.provenance.append(
            models.HeatmapProvenance(
                field="Asset class",
                source="Classified from fund name",
                note="Keyword rules; not an official classification",
            )
        )
        self._quote_provenance(part, True)
        return part

    async def close(self) -> None:
        """Cancels background loads."""
        for task in self._jobs.values():
            task.cancel()
        for task in self._jobs.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for screener in self._screener.values():
            await screener.close()
        for members in self._member_lists.values():
            await members.close()
