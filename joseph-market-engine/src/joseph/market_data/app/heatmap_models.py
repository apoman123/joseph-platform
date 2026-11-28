# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public heatmap contracts: data sets, metrics, tiles and provenance.

Every metric value is a decimal string. A value that the selected source
cannot supply is absent from ``HeatmapTile.metrics`` and the metric itself
is listed with ``available=False`` and a reason, never estimated.
"""

from __future__ import annotations

from typing import Literal
from typing import TypeAlias

import pydantic

HeatmapType: TypeAlias = Literal["stocks", "etfs", "crypto"]
"""Heatmap families: stocks, exchange-traded funds or crypto coins."""

GroupingId: TypeAlias = Literal["sector", "asset_class", "none"]
"""Tile grouping: stock sector, ETF asset class or a flat map."""


class HeatmapMetric(pydantic.BaseModel):
    """One size or colour metric and whether the source supplies it.

    Attributes:
        id: Stable metric id, e.g. ``market_cap`` or ``change_1d``.
        label: Human label, e.g. ``Change 1D, %``.
        format: Value format: currency amount, percent, plain number or
            ratio (relative volume).
        scale: Colour scale of a colour metric: ``diverging`` (red/grey/green)
            or ``sequential`` (single-hue intensity); None for size metrics.
        available: Whether at least the source can supply the metric.
        reason: Why the metric is unavailable, or None when available.
    """

    id: str
    label: str
    format: Literal["currency", "percent", "number", "ratio"]
    scale: Literal["diverging", "sequential"] | None = None
    available: bool
    reason: str | None = None


class HeatmapGrouping(pydantic.BaseModel):
    """A grouping option of the selected data set.

    Attributes:
        id: ``sector``, ``asset_class`` or ``none``.
        label: Human label, e.g. ``Sector``.
        available: Whether tiles carry this grouping.
        reason: Why the grouping is unavailable, or None.
    """

    id: GroupingId
    label: str
    available: bool
    reason: str | None = None


class HeatmapDataset(pydantic.BaseModel):
    """A selectable data set (an index, a market or a coin universe).

    Attributes:
        id: Stable id, e.g. ``us_sp500`` or ``crypto_all``.
        type: Heatmap family.
        label: Human label, e.g. ``S&P 500``.
        section: Menu section, e.g. ``United States``, ``Taiwan``, ``Crypto``.
        currency: Quote currency of prices and sizes (``USD`` or ``TWD``).
        default_limit: Tiles shown by default (the largest first).
        available: Whether the configured sources can serve it.
        reason: Why it is unavailable, or None.
    """

    id: str
    type: HeatmapType
    label: str
    section: str
    currency: str
    default_limit: int
    available: bool
    reason: str | None = None


class HeatmapCatalog(pydantic.BaseModel):
    """Every data set and the default per heatmap family.

    Attributes:
        datasets: Data sets in menu order.
        defaults: Default data set id per heatmap family.
    """

    datasets: list[HeatmapDataset]
    defaults: dict[str, str]


class HeatmapProvenance(pydantic.BaseModel):
    """Where one part of the heatmap comes from.

    Attributes:
        field: What the source supplies, e.g. ``Sector & market cap``.
        source: Source label, e.g. ``Nasdaq.com screener (unofficial,
            delayed)``.
        as_of_ms: When the source data was fetched or observed, UTC epoch
            milliseconds, or None when it is not loaded yet.
        note: Optional caveat such as ``derived: shares x last price``.
    """

    field: str
    source: str
    as_of_ms: int | None = None
    note: str | None = None


class HeatmapTile(pydantic.BaseModel):
    """One symbol of the map.

    Attributes:
        key: Unique key within the data set (ticker or coin id).
        symbol: Display ticker.
        name: Company, fund or coin name, or None.
        group: Sector (stocks), asset class (ETFs) or None (crypto).
        industry: Industry within the sector, or None.
        exchange: Listing exchange, or None.
        instrument_id: Canonical instrument to chart, or None when no
            configured venue lists it.
        chart_unavailable_reason: Why no chart can open, or None.
        price: Last price as a decimal string, or None.
        metrics: Available metric values by metric id (decimal strings);
            a missing key means the value is unavailable for this symbol.
        as_of_ms: Quote observation or fetch time, or None.
        error: Per-symbol source problem, or None.
    """

    key: str
    symbol: str
    name: str | None = None
    group: str | None = None
    industry: str | None = None
    exchange: str | None = None
    instrument_id: str | None = None
    chart_unavailable_reason: str | None = None
    price: str | None = None
    metrics: dict[str, str] = pydantic.Field(default_factory=dict)
    as_of_ms: int | None = None
    error: str | None = None


class HeatmapSnapshot(pydantic.BaseModel):
    """A heatmap of one data set with metric availability and provenance.

    Attributes:
        type: Heatmap family.
        dataset: Data set id.
        dataset_label: Data set label.
        currency: Currency of prices and currency-valued metrics.
        tiles: Tiles, largest default size first.
        universe_count: Members of the data set before the limit.
        limit: Requested maximum tiles.
        priced_count: Tiles with a price.
        size_metrics: Size-by options with availability.
        color_metrics: Colour-by options with availability.
        groupings: Group-by options with availability.
        default_size: Default size metric id.
        default_color: Default colour metric id.
        default_grouping: Default grouping id.
        provenance: Source of each part of the data.
        source_errors: Per-source failures limiting the result.
        pending: True while a slow source is still loading; the client
            should poll again soon for fuller coverage.
        generated_at_ms: Response time, UTC epoch milliseconds.
        demo: True for synthetic DEMO data.
    """

    type: HeatmapType
    dataset: str
    dataset_label: str
    currency: str
    tiles: list[HeatmapTile]
    universe_count: int
    limit: int
    priced_count: int
    size_metrics: list[HeatmapMetric]
    color_metrics: list[HeatmapMetric]
    groupings: list[HeatmapGrouping]
    default_size: str
    default_color: str
    default_grouping: GroupingId
    provenance: list[HeatmapProvenance]
    source_errors: dict[str, str] = pydantic.Field(default_factory=dict)
    pending: bool = False
    generated_at_ms: int
    demo: bool = False
