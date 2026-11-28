# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Taiwan stock and ETF heatmaps (TWD, never mixed with USD data sets).

Companies, industries and issued shares come from TWSE and TPEx open data;
market capitalization is derived as issued shares times the last price.
Quotes come from the configured Taiwan feed when its venue is enabled, and
otherwise from the official end-of-day quotes of the same open data.
"""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import Mapping

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import taiwan_open_data as tw
from joseph.market_data.app import heatmap_cache
from joseph.market_data.app import heatmap_catalog as catalog
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import heatmap_parts as parts
from joseph.market_data.app import ports
from joseph.market_data.app import universe as universe_lib
from joseph.market_data.domain import heatmap_metrics as rules

COMPANIES_TTL_MS = 24 * 3_600_000
DAILY_TTL_MS = 10 * 60_000
"""Lifetime of the end-of-day files (published once after the close)."""
LIVE_BUDGET_S = 3.0
"""Longest wait for configured live quotes within a request."""
_VENUES = {"twse": instruments.Venue.TWSE, "tpex": instruments.Venue.TPEX}
_EXCHANGE = {"twse": "TWSE", "tpex": "TPEx"}
NO_HISTORY = (
    "needs daily history; the official open data publishes only the latest "
    "session"
)
NO_CHART = (
    "No chart on configured venues: Taiwan charts need "
    "JOSEPH_TW_DATA_SOURCE=fugle with a Fugle key"
)


def _daily_metrics(quote: tw.TwDailyQuote) -> dict[str, decimal.Decimal | None]:
    """Computes metrics of an official daily quote.

    Args:
        quote: End-of-day quote.

    Returns:
        Change, gap, volatility, volume and traded value.
    """
    values: dict[str, decimal.Decimal | None] = dict(
        rules.day_metrics(
            last=quote.close,
            previous_close=quote.previous_close,
            open_=quote.open,
            high=quote.high,
            low=quote.low,
        )
    )
    values["volume_1d"] = quote.volume
    values["turnover_1d"] = quote.value
    return values


def _live_metrics(
    snapshot: market.MarketSnapshot,
) -> dict[str, decimal.Decimal | None]:
    """Reads metrics of a configured-feed snapshot.

    Args:
        snapshot: Snapshot of the configured Taiwan feed.

    Returns:
        Change, volume and turnover.
    """
    change = snapshot.change_24h_pct
    return {
        "change_1d": change.quantize(decimal.Decimal("0.0001"))
        if change is not None
        else None,
        "volume_1d": snapshot.volume_24h_base,
        "turnover_1d": snapshot.turnover_24h_quote.quantize(decimals.ONE)
        if snapshot.turnover_24h_quote is not None
        else None,
    }


class TaiwanEquities:
    """Builds the Taiwan stock and ETF data sets.

    Attributes:
        source: TWSE/TPEx open data, or None when disabled.
    """

    def __init__(
        self,
        source: tw.TaiwanOpenData | None,
        universe: universe_lib.UniverseService,
        index: parts.InstrumentIndex,
        clock: clock_lib.Clock,
    ) -> None:
        """Creates empty caches.

        Args:
            source: Open-data source, or None (no public sources).
            universe: Shared quote loader of the configured venues.
            index: Enabled-instrument lookup.
            clock: Time source.
        """
        self.source = source
        self._universe = universe
        self._index = index
        self._companies = {
            name: heatmap_cache.SourceCache[list[tw.TwCompany]](
                f"{name} company profiles", clock, COMPANIES_TTL_MS
            )
            for name in _VENUES
        }
        self._daily = {
            name: heatmap_cache.SourceCache[list[tw.TwDailyQuote]](
                f"{name} daily quotes", clock, DAILY_TTL_MS
            )
            for name in _VENUES
        }

    def unavailable(self, spec: catalog.DatasetSpec) -> str | None:
        """Explains why a data set cannot be served.

        Args:
            spec: Taiwan data set.

        Returns:
            Reason, or None.
        """
        del spec
        if self.source is None:
            return "public heatmap sources are disabled (DEMO-only engine)"
        return None

    async def _load(
        self, market_name: str, budget: parts.Budget, part: parts.Part
    ) -> tuple[list[tw.TwCompany], dict[str, tw.TwDailyQuote]]:
        """Loads the profiles and daily quotes of one market.

        Args:
            market_name: ``twse`` or ``tpex``.
            budget: Request budget.
            part: Result collecting errors.

        Returns:
            Companies and daily quotes by code.
        """
        source = self.source
        if source is None:
            return [], {}
        companies_cache = self._companies[market_name]
        daily_cache = self._daily[market_name]
        companies, daily = await asyncio.gather(
            companies_cache.get(
                lambda: source.companies(market_name), budget_s=budget.left()
            ),
            daily_cache.get(
                lambda: source.daily(market_name), budget_s=budget.left()
            ),
        )
        part.note_cache(companies_cache.name, companies_cache)
        part.note_cache(daily_cache.name, daily_cache)
        return companies or [], {row.code: row for row in daily or ()}

    async def _live(
        self,
        market_name: str,
        codes: list[str],
        budget: parts.Budget,
        part: parts.Part,
    ) -> tuple[
        dict[str, instruments.Instrument], dict[str, market.MarketSnapshot]
    ]:
        """Loads configured-feed listings and quotes of one market.

        Args:
            market_name: ``twse`` or ``tpex``.
            codes: Codes shown.
            budget: Request budget.
            part: Result collecting errors.

        Returns:
            Listings and live snapshots by code (no snapshots without the
            venue or when its feed has no selected-symbol bulk quotes).
        """
        venue = _VENUES[market_name]
        rows = await self._index.rows(budget, [venue])
        if rows is None:
            part.pending = True
            return {}, {}
        listings = {row.instrument_id.venue_market_id: row for row in rows}
        selected = [listings[code] for code in codes if code in listings]
        if not selected or not isinstance(
            self._universe.market_data.adapter(venue), ports.SnapshotBatchPort
        ):
            # A per-symbol feed (Fugle) would spend its chart budget on a
            # whole market; the official end-of-day files serve instead.
            return listings, {}
        quotes, failures = await self._universe.quotes(
            selected, budget_s=min(LIVE_BUDGET_S, budget.left())
        )
        if failures and not quotes:
            part.source_errors[f"{venue.value} quotes"] = next(
                iter(failures.values())
            )
        return listings, {
            key.split(":")[-1]: value for key, value in quotes.items()
        }

    def _provenance(
        self, part: parts.Part, market_names: list[str], live: bool
    ) -> None:
        """Adds provenance and unavailable reasons.

        Args:
            part: Result.
            market_names: Markets used.
            live: Whether configured live quotes were used.
        """
        for name in market_names:
            part.provenance.append(
                models.HeatmapProvenance(
                    field=f"{_EXCHANGE[name]} quotes",
                    source="Configured Taiwan feed"
                    if live
                    else f"{_EXCHANGE[name]} official end-of-day open data",
                    as_of_ms=self._daily[name].loaded_ms,
                )
            )
        for metric in ("perf_1w", "perf_1m", "perf_3m", "perf_6m"):
            part.reasons[metric] = NO_HISTORY
        for metric in (
            "perf_ytd",
            "perf_1y",
            "relative_volume",
            "volume_1w",
            "volume_1m",
            "turnover_1w",
            "turnover_1m",
        ):
            part.reasons[metric] = NO_HISTORY
        for metric in ("change_1h", "change_4h"):
            part.reasons[metric] = (
                "intraday changes are not loaded for heatmaps (daily data only)"
            )
        for metric in ("premarket_change", "postmarket_change"):
            part.reasons[metric] = "no extended-hours session data for Taiwan"
        if live:
            for metric in ("gap", "volatility_1d"):
                part.reasons[metric] = (
                    "the configured feed snapshot has no open, high or low"
                )

    def _tile(
        self,
        *,
        code: str,
        name: str,
        group: str | None,
        exchange: str,
        quote: tw.TwDailyQuote | None,
        live: market.MarketSnapshot | None,
        listing: instruments.Instrument | None,
        shares: decimal.Decimal | None,
    ) -> models.HeatmapTile:
        """Builds one Taiwan tile.

        Args:
            code: Security code.
            name: Chinese short name.
            group: Industry or asset class.
            exchange: ``TWSE`` or ``TPEx``.
            quote: Official daily quote, or None.
            live: Configured-feed snapshot, or None.
            listing: Enabled instrument, or None.
            shares: Issued shares for the derived market cap, or None.

        Returns:
            Tile.
        """
        values: dict[str, decimal.Decimal | None] = {}
        price: decimal.Decimal | None = None
        as_of: int | None = None
        if live is not None and live.last is not None:
            values = _live_metrics(live)
            price, as_of = live.last, live.as_of_ms
        elif quote is not None:
            values = _daily_metrics(quote)
            price, as_of = quote.close, quote.as_of_ms
        if shares is not None and price is not None:
            values["market_cap"] = (shares * price).quantize(decimals.ONE)
        return models.HeatmapTile(
            key=code,
            symbol=code,
            name=name,
            group=group,
            exchange=exchange,
            instrument_id=str(listing.instrument_id) if listing else None,
            chart_unavailable_reason=None if listing else NO_CHART,
            price=rules.to_text(price) if price is not None else None,
            metrics=parts.texts(values),
            as_of_ms=as_of,
            error=None
            if price is not None
            else "no trade in the latest session",
        )

    async def stocks(
        self, spec: catalog.DatasetSpec, limit: int, budget: parts.Budget
    ) -> parts.Part:
        """Builds a TWSE or TPEx stock data set.

        Args:
            spec: ``tw_twse`` or ``tw_tpex``.
            limit: Maximum tiles (largest market caps).
            budget: Request budget.

        Returns:
            Tiles grouped by industry.
        """
        part = parts.Part()
        name = "twse" if spec.id == "tw_twse" else "tpex"
        companies, daily = await self._load(name, budget, part)
        stocks = [row for row in companies if tw.is_stock_code(row.code)]
        part.universe_count = len(stocks)
        listings, live = await self._live(
            name, [row.code for row in stocks], budget, part
        )
        tiles = []
        for company in stocks:
            quote = daily.get(company.code)
            shares = company.shares
            if shares is None and quote is not None:
                shares = quote.shares
            tiles.append(
                self._tile(
                    code=company.code,
                    name=company.name,
                    group=company.industry or "Unclassified",
                    exchange=_EXCHANGE[name],
                    quote=quote,
                    live=live.get(company.code),
                    listing=listings.get(company.code),
                    shares=shares,
                )
            )
        part.tiles = parts.rank(tiles, "market_cap")[:limit]
        part.provenance.append(
            models.HeatmapProvenance(
                field="Companies, industry & issued shares",
                source=f"{_EXCHANGE[name]} open data company profiles",
                as_of_ms=self._companies[name].loaded_ms,
                note="Market cap derived: issued shares × last price",  # noqa: RUF001 - UI label sign
            )
        )
        self._provenance(part, [name], bool(live))
        return part

    async def etfs(
        self, spec: catalog.DatasetSpec, limit: int, budget: parts.Budget
    ) -> parts.Part:
        """Builds the Taiwan ETF data set (TWSE and TPEx).

        Args:
            spec: ``tw_etfs``.
            limit: Maximum tiles (largest turnover).
            budget: Request budget.

        Returns:
            Tiles grouped by an asset class classified from the fund name.
        """
        del spec
        part = parts.Part()
        tiles: list[models.HeatmapTile] = []
        live_used = False
        for name in _VENUES:
            _, daily = await self._load(name, budget, part)
            funds: Mapping[str, tw.TwDailyQuote] = {
                code: row for code, row in daily.items() if tw.is_etf_code(code)
            }
            part.universe_count += len(funds)
            listings, live = await self._live(name, list(funds), budget, part)
            live_used |= bool(live)
            tiles.extend(
                self._tile(
                    code=code,
                    name=row.name,
                    group=rules.classify_fund(row.name),
                    exchange=_EXCHANGE[name],
                    quote=row,
                    live=live.get(code),
                    listing=listings.get(code),
                    shares=None,
                )
                for code, row in funds.items()
            )
        part.tiles = parts.rank(tiles, "turnover_1d")[:limit]
        part.provenance.append(
            models.HeatmapProvenance(
                field="Members & asset class",
                source="TWSE/TPEx daily quotes (ETF codes)",
                note="Asset class classified from fund name",
            )
        )
        self._provenance(part, list(_VENUES), live_used)
        return part

    async def close(self) -> None:
        """Cancels background loads."""
        for companies in self._companies.values():
            await companies.close()
        for daily in self._daily.values():
            await daily.close()
