# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Stock, ETF and crypto heatmaps over configured and keyless sources.

A snapshot never waits longer than a short budget: slow public sources keep
loading in the background and the response carries the cached or partial
data with ``pending`` set. Every metric that the answering sources cannot
supply is listed as unavailable with a reason; tiles never carry estimated
values (a derived value, such as Taiwan market capitalization, is labelled
in the provenance).
"""

from __future__ import annotations

import dataclasses

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.market_data.adapters import coingecko
from joseph.market_data.adapters import taiwan_open_data
from joseph.market_data.adapters import us_equities
from joseph.market_data.app import heatmap_catalog as catalog_lib
from joseph.market_data.app import heatmap_crypto
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import heatmap_parts as parts
from joseph.market_data.app import heatmap_tw
from joseph.market_data.app import heatmap_us
from joseph.market_data.app import universe

BUDGET_S = 4.0
"""Longest time a snapshot request waits for its sources."""
_DEFAULT_REASON = "not available from the selected sources"
_KINDS: tuple[models.HeatmapType, ...] = ("stocks", "etfs", "crypto")


@dataclasses.dataclass(frozen=True)
class HeatmapSources:
    """Keyless public sources; each is None when disabled (DEMO-only mode).

    Attributes:
        us: Nasdaq.com screener and index members.
        taiwan: TWSE and TPEx open data.
        coins: CoinGecko coin markets.
    """

    us: us_equities.UsEquitySource | None = None
    taiwan: taiwan_open_data.TaiwanOpenData | None = None
    coins: coingecko.CoinGeckoSource | None = None

    async def close(self) -> None:
        """Closes the sources' owned clients."""
        for source in (self.us, self.taiwan, self.coins):
            if source is not None:
                await source.close()


class HeatmapService:
    """Builds heatmaps from the configured feeds and public sources.

    Attributes:
        universe: Shared instruments and quotes of the configured venues.
        sources: Public metadata and quote sources.
        budget_s: Longest wait per snapshot, in seconds.
    """

    def __init__(
        self,
        source: universe.UniverseService,
        sources: HeatmapSources | None = None,
        *,
        clock: clock_lib.Clock | None = None,
        budget_s: float = BUDGET_S,
    ) -> None:
        """Wires the builders.

        Args:
            source: Shared market universe and quote cache.
            sources: Public sources; None disables them.
            clock: Time source of the caches, default the system clock.
            budget_s: Longest wait per snapshot, in seconds.
        """
        self.universe = source
        self.sources = sources or HeatmapSources()
        self.budget_s = budget_s
        market = source.market_data
        clock = clock or clock_lib.SystemClock()
        index = parts.InstrumentIndex(market.registry, clock)
        self._index = index
        self._us = heatmap_us.UsEquities(self.sources.us, market, index, clock)
        self._tw = heatmap_tw.TaiwanEquities(
            self.sources.taiwan, source, index, clock
        )
        self._crypto = heatmap_crypto.CryptoCoins(
            self.sources.coins, index, clock
        )

    def _reason(self, spec: catalog_lib.DatasetSpec) -> str | None:
        """Explains why a data set cannot be served.

        Args:
            spec: Data set.

        Returns:
            Reason, or None when it can be served.
        """
        if spec.type == "crypto":
            return self._crypto.unavailable(spec)
        if spec.currency == "TWD":
            return self._tw.unavailable(spec)
        return self._us.unavailable(spec)

    def _default(self, kind: models.HeatmapType) -> str:
        """Chooses the default data set of a family.

        Args:
            kind: Family.

        Returns:
            The preferred data set when available, else the first available
            one, else the preferred one.
        """
        preferred = catalog_lib.DEFAULTS[kind]
        spec = catalog_lib.dataset(preferred)
        if spec is not None and self._reason(spec) is None:
            return preferred
        for row in catalog_lib.DATASETS:
            if row.type == kind and self._reason(row) is None:
                return row.id
        return preferred

    def catalog(self) -> models.HeatmapCatalog:
        """Lists every data set with its availability.

        Returns:
            Data sets in menu order and the default per family.
        """
        datasets = []
        for row in catalog_lib.DATASETS:
            reason = self._reason(row)
            datasets.append(
                models.HeatmapDataset(
                    id=row.id,
                    type=row.type,
                    label=row.label,
                    section=row.section,
                    currency=row.currency,
                    default_limit=row.default_limit,
                    available=reason is None,
                    reason=reason,
                )
            )
        return models.HeatmapCatalog(
            datasets=datasets,
            defaults={kind: self._default(kind) for kind in _KINDS},
        )

    async def _build(
        self,
        spec: catalog_lib.DatasetSpec,
        limit: int,
        budget: parts.Budget,
    ) -> parts.Part:
        """Runs the builder of a data set.

        Args:
            spec: Data set.
            limit: Maximum tiles.
            budget: Request budget.

        Returns:
            Builder result.
        """
        if spec.type == "crypto":
            return await self._crypto.coins(spec, limit, budget)
        if spec.currency == "TWD":
            if spec.type == "etfs":
                return await self._tw.etfs(spec, limit, budget)
            return await self._tw.stocks(spec, limit, budget)
        if spec.type == "etfs":
            return await self._us.etfs(spec, limit, budget)
        return await self._us.stocks(spec, limit, budget)

    async def snapshot(
        self,
        kind: models.HeatmapType,
        *,
        dataset: str | None = None,
        limit: int | None = None,
    ) -> models.HeatmapSnapshot:
        """Builds a heatmap of one data set within the time budget.

        Args:
            kind: Family: stocks, etfs or crypto.
            dataset: Data set id of that family, default its preferred one.
            limit: Maximum tiles, default the data set's own size.

        Returns:
            Tiles (largest default size first), metric availability,
            coverage and provenance.

        Raises:
            errors.ValidationFailedError: For an unknown data set or one of
                another family.
        """
        spec = catalog_lib.dataset(dataset or self._default(kind))
        if spec is None or spec.type != kind:
            raise errors.ValidationFailedError(
                f"unknown {kind} heatmap data set {dataset!r}"
            )
        size = min(limit or spec.default_limit, catalog_lib.MAX_LIMIT)
        reason = self._reason(spec)
        part = parts.Part()
        if reason is not None:
            part.source_errors["dataset"] = reason
        else:
            part = await self._build(spec, size, parts.Budget(self.budget_s))
        default_size = catalog_lib.DEFAULT_SIZE[kind]
        tiles = parts.rank(part.tiles, default_size)
        unavailable = {}
        for spec_row in (*catalog_lib.sizes(kind), *catalog_lib.colors(kind)):
            if spec_row.id == catalog_lib.MONO:
                continue
            if not any(spec_row.id in tile.metrics for tile in tiles):
                unavailable[spec_row.id] = part.reasons.get(
                    spec_row.id, _DEFAULT_REASON
                )
        if kind == "etfs":
            unavailable["aum"] = (
                "fund assets under management are not available from free "
                "data sources"
            )
        grouping = catalog_lib.DEFAULT_GROUPING[kind]
        grouping_reasons: dict[str, str] = {}
        if grouping != "none" and not any(tile.group for tile in tiles):
            grouping_reasons[grouping] = "no tile carries a group"
        return models.HeatmapSnapshot(
            type=kind,
            dataset=spec.id,
            dataset_label=spec.label,
            currency=spec.currency,
            tiles=tiles,
            universe_count=part.universe_count,
            limit=size,
            priced_count=sum(1 for tile in tiles if tile.price is not None),
            size_metrics=catalog_lib.metric_rows(
                catalog_lib.sizes(kind), unavailable
            ),
            color_metrics=catalog_lib.metric_rows(
                catalog_lib.colors(kind), unavailable
            ),
            groupings=catalog_lib.groupings(kind, grouping_reasons),
            default_size=default_size,
            default_color="change_1d",
            default_grouping=grouping,
            provenance=part.provenance,
            source_errors=part.source_errors,
            pending=part.pending,
            generated_at_ms=self.universe.market_data.now_ms(),
        )

    async def close(self) -> None:
        """Cancels background loads and closes the public sources."""
        await self._us.close()
        await self._tw.close()
        await self._crypto.close()
        await self._index.close()
        await self.sources.close()
