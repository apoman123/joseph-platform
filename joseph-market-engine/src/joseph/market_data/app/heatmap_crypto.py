# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Crypto coin heatmaps from keyless CoinGecko markets.

The top coins are cached for a minute, the stablecoin list (used to exclude
stablecoins) for an hour. A tile opens the coin's chart on an enabled venue:
the Binance USDⓈ-M perpetual ``<SYM>USDT`` first, else a spot USDT, USD or
USDC pair of another enabled venue.
"""

from __future__ import annotations

import decimal

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import coingecko
from joseph.market_data.app import heatmap_cache
from joseph.market_data.app import heatmap_catalog as catalog
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import heatmap_parts as parts
from joseph.market_data.domain import heatmap_metrics as rules

MARKETS_TTL_MS = 60_000
STABLECOINS_TTL_MS = 3_600_000
TOP_PAGES = 2
"""Pages of 250 coins read for the market-wide data sets (top 500)."""
_CHANGES = {
    "change_1h": "1h",
    "change_1d": "24h",
    "perf_1w": "7d",
    "perf_1m": "30d",
    "perf_1y": "1y",
}
_QUOTES = ("USDT", "USD", "USDC")
CRYPTO_VENUES = tuple(
    venue
    for venue in instruments.Venue
    if venue
    not in {
        *instruments.EXCHANGE_VENUES,
        *instruments.RATE_VENUES,
        instruments.Venue.MOCK,
        instruments.Venue.DERIV,
    }
)
"""Crypto venues searched for a chart."""
NO_CHART = "No chart on configured venues"
NO_WINDOW = "CoinGecko markets do not publish this window"


def coin_metrics(coin: coingecko.Coin) -> dict[str, decimal.Decimal | None]:
    """Maps a coin row to heatmap metrics.

    Args:
        coin: CoinGecko row.

    Returns:
        Sizes, changes and the 24-hour volatility (high over low).
    """
    values: dict[str, decimal.Decimal | None] = {
        "market_cap": coin.market_cap,
        "fd_market_cap": coin.fd_market_cap,
        "volume_usd_24h": coin.volume,
    }
    for metric, window in _CHANGES.items():
        change = coin.changes.get(window)
        values[metric] = (
            change.quantize(decimal.Decimal("0.0001"))
            if change is not None
            else None
        )
    if (
        coin.high_24h is not None
        and coin.low_24h is not None
        and coin.high_24h >= coin.low_24h
    ):
        values["volatility_1d"] = rules.pct(coin.high_24h, coin.low_24h)
    return values


def chart_index(
    rows: list[instruments.Instrument], venues: tuple[instruments.Venue, ...]
) -> dict[str, instruments.Instrument]:
    """Chooses one chartable instrument per base asset.

    Args:
        rows: Enabled crypto instruments.
        venues: Venue preference after Binance.

    Returns:
        Instrument by upper-case base asset.
    """

    def rank(row: instruments.Instrument) -> tuple[int, int, int]:
        """Orders candidates by preference.

        Args:
            row: Candidate.

        Returns:
            Sort key: Binance perpetual, then spot by venue and quote.
        """
        ident = row.instrument_id
        perp = (
            ident.venue is instruments.Venue.BINANCE
            and ident.market_type is instruments.MarketType.PERP
            and row.key.quote == "USDT"
        )
        return (
            0 if perp else 1,
            venues.index(ident.venue) if ident.venue in venues else 99,
            _QUOTES.index(row.key.quote),
        )

    candidates = [
        row
        for row in rows
        if row.key.quote in _QUOTES
        and (
            row.instrument_id.market_type is instruments.MarketType.SPOT
            or (
                row.instrument_id.venue is instruments.Venue.BINANCE
                and row.instrument_id.market_type is instruments.MarketType.PERP
            )
        )
        and row.status is instruments.InstrumentStatus.TRADING
    ]
    out: dict[str, instruments.Instrument] = {}
    for row in sorted(candidates, key=rank):
        out.setdefault(row.key.canonical_base.upper(), row)
    return out


class CryptoCoins:
    """Builds the crypto data sets.

    Attributes:
        source: CoinGecko source, or None when disabled.
    """

    def __init__(
        self,
        source: coingecko.CoinGeckoSource | None,
        index: parts.InstrumentIndex,
        clock: clock_lib.Clock,
    ) -> None:
        """Creates empty caches.

        Args:
            source: CoinGecko source, or None (no public sources).
            index: Enabled-instrument lookup.
            clock: Time source.
        """
        self.source = source
        self._index = index
        self._top = heatmap_cache.SourceCache[list[coingecko.Coin]](
            "coingecko markets", clock, MARKETS_TTL_MS
        )
        self._stable = heatmap_cache.SourceCache[list[coingecko.Coin]](
            "coingecko stablecoins", clock, STABLECOINS_TTL_MS
        )
        self._defi = heatmap_cache.SourceCache[list[coingecko.Coin]](
            "coingecko defi", clock, MARKETS_TTL_MS
        )

    def unavailable(self, spec: catalog.DatasetSpec) -> str | None:
        """Explains why a data set cannot be served.

        Args:
            spec: Crypto data set.

        Returns:
            Reason, or None.
        """
        del spec
        if self.source is None:
            return "public heatmap sources are disabled (DEMO-only engine)"
        return None

    async def _coins(
        self, spec: catalog.DatasetSpec, budget: parts.Budget, part: parts.Part
    ) -> list[coingecko.Coin]:
        """Loads the coins of a data set.

        Args:
            spec: Crypto data set.
            budget: Request budget.
            part: Result collecting errors.

        Returns:
            Coins, largest first.
        """
        source = self.source
        if source is None:
            return []
        if spec.id == "crypto_defi":
            coins = await self._defi.get(
                lambda: source.markets(category=coingecko.DEFI),
                budget_s=budget.left(),
            )
            part.note_cache(self._defi.name, self._defi)
            return list(coins or ())
        coins = await self._top.get(
            lambda: source.markets(pages=TOP_PAGES), budget_s=budget.left()
        )
        part.note_cache(self._top.name, self._top)
        rows = list(coins or ())
        if spec.id == "crypto_ex_btc":
            rows = [coin for coin in rows if coin.id != "bitcoin"]
        if spec.id == "crypto_ex_stable":
            stable = await self._stable.get(
                lambda: source.markets(category=coingecko.STABLECOINS),
                budget_s=budget.left(),
            )
            part.note_cache(self._stable.name, self._stable)
            if stable is None:
                # Without the list, stablecoins could not be excluded.
                return []
            ids = {coin.id for coin in stable}
            rows = [coin for coin in rows if coin.id not in ids]
        return rows

    async def coins(
        self, spec: catalog.DatasetSpec, limit: int, budget: parts.Budget
    ) -> parts.Part:
        """Builds a crypto data set.

        Args:
            spec: Crypto data set.
            limit: Maximum tiles (largest market caps).
            budget: Request budget.

        Returns:
            Tiles of a flat map.
        """
        part = parts.Part()
        coins = await self._coins(spec, budget, part)
        part.universe_count = len(coins)
        rows = await self._index.rows(budget, CRYPTO_VENUES)
        if rows is None:
            part.pending = True
        charts = chart_index(rows or [], self._index.registry.venues)
        tiles = []
        for coin in coins:
            listing = charts.get(coin.symbol)
            tiles.append(
                models.HeatmapTile(
                    key=coin.id,
                    symbol=coin.symbol,
                    name=coin.name,
                    instrument_id=str(listing.instrument_id)
                    if listing
                    else None,
                    chart_unavailable_reason=None if listing else NO_CHART,
                    price=rules.to_text(coin.price)
                    if coin.price is not None
                    else None,
                    metrics=parts.texts(coin_metrics(coin)),
                    as_of_ms=coin.as_of_ms,
                )
            )
        part.tiles = parts.rank(tiles, "market_cap")[:limit]
        for metric in ("change_4h", "perf_3m", "perf_6m", "perf_ytd"):
            part.reasons[metric] = NO_WINDOW
        part.provenance.append(
            models.HeatmapProvenance(
                field="Coins, market cap, volume & changes",
                source="CoinGecko coins/markets (keyless, USD)",
                as_of_ms=(
                    self._defi if spec.id == "crypto_defi" else self._top
                ).loaded_ms,
                note="Cached 60 s; volatility 24h = (high - low) / low",
            )
        )
        if spec.id == "crypto_ex_stable":
            part.provenance.append(
                models.HeatmapProvenance(
                    field="Stablecoin exclusion",
                    source="CoinGecko category: stablecoins",
                    as_of_ms=self._stable.loaded_ms,
                )
            )
        return part

    async def close(self) -> None:
        """Cancels background loads."""
        for cache in (self._top, self._stable, self._defi):
            await cache.close()
