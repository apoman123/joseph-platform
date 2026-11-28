# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Heatmap data sets and the size, colour and grouping options per family.

The options follow the common heatmap use cases: stocks of an index or a
market sized by market capitalization, ETFs by assets (unavailable from free
sources) or turnover, and crypto coins by market capitalization; colour by
price change or performance. Availability is decided per response from the
sources that actually answered.
"""

from __future__ import annotations

import dataclasses

from joseph.market_data.app import heatmap_models as models


@dataclasses.dataclass(frozen=True)
class MetricSpec:
    """A metric definition.

    Attributes:
        id: Stable metric id.
        label: Human label.
        format: Value format.
        scale: Colour scale for colour metrics, else None.
    """

    id: str
    label: str
    format: str
    scale: str | None = None


@dataclasses.dataclass(frozen=True)
class DatasetSpec:
    """A data set definition.

    Attributes:
        id: Stable data set id.
        type: Heatmap family.
        label: Human label.
        section: Menu section.
        currency: Quote currency.
        default_limit: Default (and largest useful) tile count.
    """

    id: str
    type: models.HeatmapType
    label: str
    section: str
    currency: str
    default_limit: int


MONO = "mono"
"""Equal tile sizes; the size metric then only orders the tiles."""

_VOLUME_SIZES = (
    MetricSpec("volume_1d", "Volume 1D", "number"),
    MetricSpec("volume_1w", "Volume 1W", "number"),
    MetricSpec("volume_1m", "Volume 1M", "number"),
    MetricSpec(
        "turnover_1d",
        "Price × Volume (Turnover) 1D",  # noqa: RUF001 - UI label sign
        "currency",
    ),
    MetricSpec(
        "turnover_1w",
        "Price × Volume (Turnover) 1W",  # noqa: RUF001 - UI label sign
        "currency",
    ),
    MetricSpec(
        "turnover_1m",
        "Price × Volume (Turnover) 1M",  # noqa: RUF001 - UI label sign
        "currency",
    ),
)
STOCK_SIZES = (
    MetricSpec("market_cap", "Market cap", "currency"),
    *_VOLUME_SIZES,
    MetricSpec(MONO, "Mono size", "number"),
)
"""Stock size options; market cap is the default."""

ETF_SIZES = (
    MetricSpec("aum", "Assets under management", "currency"),
    *_VOLUME_SIZES,
    MetricSpec(MONO, "Mono size", "number"),
)
"""ETF size options; AUM is unavailable from free sources."""

CRYPTO_SIZES = (
    MetricSpec("market_cap", "Market cap", "currency"),
    MetricSpec("fd_market_cap", "Fully diluted market cap", "currency"),
    MetricSpec("volume_usd_24h", "Volume in USD 24h", "currency"),
    MetricSpec(MONO, "Mono size", "number"),
)
"""Crypto size options; market cap is the default."""

_PERFORMANCE = (
    MetricSpec("perf_1w", "Performance 1W, %", "percent", "diverging"),
    MetricSpec("perf_1m", "Performance 1M, %", "percent", "diverging"),
    MetricSpec("perf_3m", "Performance 3M, %", "percent", "diverging"),
    MetricSpec("perf_6m", "Performance 6M, %", "percent", "diverging"),
    MetricSpec("perf_ytd", "Performance YTD, %", "percent", "diverging"),
    MetricSpec("perf_1y", "Performance 1Y, %", "percent", "diverging"),
)
STOCK_COLORS = (
    MetricSpec("change_1d", "Change 1D, %", "percent", "diverging"),
    MetricSpec("change_1h", "Change 1h, %", "percent", "diverging"),
    MetricSpec("change_4h", "Change 4h, %", "percent", "diverging"),
    *_PERFORMANCE,
    MetricSpec(
        "premarket_change", "Pre-market change, %", "percent", "diverging"
    ),
    MetricSpec(
        "postmarket_change", "Post-market change, %", "percent", "diverging"
    ),
    MetricSpec("relative_volume", "Relative volume", "ratio", "sequential"),
    MetricSpec("volatility_1d", "Volatility 1D, %", "percent", "sequential"),
    MetricSpec("gap", "Gap, %", "percent", "diverging"),
)
"""Stock and ETF colour options; daily change is the default."""

CRYPTO_COLORS = (
    MetricSpec("change_1d", "Change 24h, %", "percent", "diverging"),
    MetricSpec("change_1h", "Change 1h, %", "percent", "diverging"),
    MetricSpec("change_4h", "Change 4h, %", "percent", "diverging"),
    *_PERFORMANCE,
    MetricSpec("volatility_1d", "Volatility 24h, %", "percent", "sequential"),
)
"""Crypto colour options; the 24-hour change is the default."""

DATASETS = (
    DatasetSpec("us_sp500", "stocks", "S&P 500", "United States", "USD", 510),
    DatasetSpec(
        "us_nasdaq100", "stocks", "Nasdaq 100", "United States", "USD", 110
    ),
    DatasetSpec(
        "us_nasdaq_composite",
        "stocks",
        "Nasdaq Composite",
        "United States",
        "USD",
        1000,
    ),
    DatasetSpec("us_nyse", "stocks", "NYSE", "United States", "USD", 1000),
    DatasetSpec(
        "us_all", "stocks", "All US stocks", "United States", "USD", 1000
    ),
    DatasetSpec("tw_twse", "stocks", "TWSE listed", "Taiwan", "TWD", 1000),
    DatasetSpec("tw_tpex", "stocks", "TPEx listed", "Taiwan", "TWD", 900),
    DatasetSpec("us_etfs", "etfs", "US ETFs", "United States", "USD", 500),
    DatasetSpec("tw_etfs", "etfs", "Taiwan ETFs", "Taiwan", "TWD", 400),
    DatasetSpec("crypto_all", "crypto", "All coins", "Crypto", "USD", 250),
    DatasetSpec(
        "crypto_ex_btc", "crypto", "Excluding Bitcoin", "Crypto", "USD", 250
    ),
    DatasetSpec(
        "crypto_ex_stable",
        "crypto",
        "Excluding stablecoins",
        "Crypto",
        "USD",
        250,
    ),
    DatasetSpec("crypto_defi", "crypto", "DeFi", "Crypto", "USD", 250),
)
"""Every data set in menu order."""

DEFAULTS = {"stocks": "us_sp500", "etfs": "us_etfs", "crypto": "crypto_all"}
"""Preferred data set per family (the first available one is served)."""

DEFAULT_SIZE = {
    "stocks": "market_cap",
    "etfs": "turnover_1d",
    "crypto": "market_cap",
}
"""Default size metric per family (ETF AUM is unavailable)."""

DEFAULT_GROUPING: dict[str, models.GroupingId] = {
    "stocks": "sector",
    "etfs": "asset_class",
    "crypto": "none",
}
"""Default grouping per family."""

MAX_LIMIT = 2000
"""Largest accepted ``limit``."""


def dataset(dataset_id: str) -> DatasetSpec | None:
    """Finds a data set definition.

    Args:
        dataset_id: Data set id.

    Returns:
        The definition, or None for an unknown id.
    """
    return next((row for row in DATASETS if row.id == dataset_id), None)


def sizes(kind: models.HeatmapType) -> tuple[MetricSpec, ...]:
    """Returns the size options of a family.

    Args:
        kind: Heatmap family.

    Returns:
        Size metric definitions in menu order.
    """
    return {"stocks": STOCK_SIZES, "etfs": ETF_SIZES}.get(kind, CRYPTO_SIZES)


def colors(kind: models.HeatmapType) -> tuple[MetricSpec, ...]:
    """Returns the colour options of a family.

    Args:
        kind: Heatmap family.

    Returns:
        Colour metric definitions in menu order.
    """
    return CRYPTO_COLORS if kind == "crypto" else STOCK_COLORS


def metric_rows(
    specs: tuple[MetricSpec, ...], unavailable: dict[str, str]
) -> list[models.HeatmapMetric]:
    """Builds metric options with their availability.

    Args:
        specs: Metric definitions.
        unavailable: Reason per unavailable metric id.

    Returns:
        Contract metric rows.
    """
    return [
        models.HeatmapMetric.model_validate(
            {
                "id": spec.id,
                "label": spec.label,
                "format": spec.format,
                "scale": spec.scale,
                "available": spec.id not in unavailable,
                "reason": unavailable.get(spec.id),
            }
        )
        for spec in specs
    ]


def groupings(
    kind: models.HeatmapType, unavailable: dict[str, str]
) -> list[models.HeatmapGrouping]:
    """Builds grouping options of a family.

    Args:
        kind: Heatmap family.
        unavailable: Reason per unavailable grouping id.

    Returns:
        Contract grouping rows (crypto maps are always flat).
    """
    options: list[tuple[models.GroupingId, str]] = [("none", "No group")]
    if kind == "stocks":
        options.insert(0, ("sector", "Sector"))
    if kind == "etfs":
        options.insert(0, ("asset_class", "Asset class"))
    return [
        models.HeatmapGrouping(
            id=grouping,
            label=label,
            available=grouping not in unavailable,
            reason=unavailable.get(grouping),
        )
        for grouping, label in options
    ]
