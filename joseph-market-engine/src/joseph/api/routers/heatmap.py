# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated stock, ETF and crypto heatmaps with explicit provenance."""

from collections.abc import Callable
from typing import Annotated

import fastapi

from joseph.domain import identity
from joseph.market_data.app import heatmap
from joseph.market_data.app import heatmap_catalog
from joseph.market_data.app import heatmap_models


def build_router(
    service: heatmap.HeatmapService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the heatmap catalogue and snapshot routes.

    Args:
        service: Heatmap service over the configured and public sources.
        get_principal: Application bearer authentication dependency.

    Returns:
        Router serving bounded heatmaps that never invent missing values.
    """
    router = fastapi.APIRouter(prefix="/v1/heatmap", tags=["heatmap"])
    owner = Annotated[identity.Principal, fastapi.Depends(get_principal)]

    @router.get("")
    async def snapshot(
        principal: owner,
        type: heatmap_models.HeatmapType = "stocks",  # noqa: A002 - public query name
        dataset: Annotated[
            str | None, fastapi.Query(max_length=40, pattern=r"^[a-z0-9_]+$")
        ] = None,
        limit: Annotated[
            int | None, fastapi.Query(ge=1, le=heatmap_catalog.MAX_LIMIT)
        ] = None,
    ) -> heatmap_models.HeatmapSnapshot:
        """Loads one data set within a short time budget.

        Slow sources keep loading in the background; the response then
        carries the cached or partial data with ``pending`` set.

        Args:
            principal: Authenticated caller.
            type: Heatmap family: stocks, etfs or crypto.
            dataset: Data set id of that family, default its preferred one.
            limit: Maximum tiles, default the data set's own size.

        Returns:
            Tiles, metric availability, coverage and provenance.
        """
        del principal
        return await service.snapshot(type, dataset=dataset, limit=limit)

    @router.get("/catalog")
    async def catalog(principal: owner) -> heatmap_models.HeatmapCatalog:
        """Lists every data set with its availability.

        Args:
            principal: Authenticated caller.

        Returns:
            Data sets in menu order and the default per family.
        """
        del principal
        return service.catalog()

    return router
