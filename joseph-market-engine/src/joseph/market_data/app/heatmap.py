# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Heatmap service (work in progress stub)."""

from __future__ import annotations

from joseph.domain import errors
from joseph.market_data.app import heatmap_catalog as catalog_lib
from joseph.market_data.app import heatmap_models as models
from joseph.market_data.app import universe


class HeatmapService:
    """Builds heatmaps.

    Attributes:
        universe: Shared instruments and quotes.
    """

    def __init__(self, source: universe.UniverseService) -> None:
        """Connects the sources.

        Args:
            source: Shared market universe.
        """
        self.universe = source

    def catalog(self) -> models.HeatmapCatalog:
        """Lists data sets.

        Returns:
            Catalogue.
        """
        return models.HeatmapCatalog(
            datasets=[
                models.HeatmapDataset(
                    id=row.id,
                    type=row.type,
                    label=row.label,
                    section=row.section,
                    currency=row.currency,
                    default_limit=row.default_limit,
                    available=True,
                )
                for row in catalog_lib.DATASETS
            ],
            defaults=dict(catalog_lib.DEFAULTS),
        )

    async def snapshot(
        self,
        kind: models.HeatmapType,
        *,
        dataset: str | None = None,
        limit: int | None = None,
    ) -> models.HeatmapSnapshot:
        """Builds a heatmap.

        Args:
            kind: Family.
            dataset: Data set id.
            limit: Maximum tiles.

        Raises:
            errors.UnsupportedError: Always (stub).
        """
        raise errors.UnsupportedError(f"{kind} {dataset} {limit}")
