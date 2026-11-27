# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated stock heatmaps with transparent market-data coverage."""

from collections.abc import Callable
from typing import Annotated
from typing import Literal

import fastapi

from joseph.domain import identity
from joseph.domain import instruments
from joseph.market_data.app import heatmap


def build_router(
    service: heatmap.HeatmapService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds quote heatmaps and optional dated company metadata routes.

    Args:
        service: Configured real stock source and owned optional fundamentals.
        get_principal: Application bearer authentication dependency.

    Returns:
        Router exposing bounded stock universes without fabricated fundamentals.
    """
    router = fastapi.APIRouter(prefix="/v1/heatmap", tags=["stock-heatmap"])
    owner = Annotated[identity.Principal, fastapi.Depends(get_principal)]

    @router.get("")
    async def snapshot(
        principal: owner,
        region: Literal["us", "taiwan"] = "us",
        venue: instruments.Venue | None = None,
        query: Annotated[str, fastapi.Query(max_length=120)] = "",
        limit: Annotated[int, fastapi.Query(ge=1, le=5000)] = 1000,
    ) -> heatmap.HeatmapSnapshot:
        """Loads actual quotes and preserves unavailable stocks.

        Loads actual quotes and preserves unavailable stocks as neutral tiles.

        Args:
            principal: Authenticated owner of optional company information.
            region: US or Taiwan listings, default US.
            venue: Optional configured exchange filter.
            query: Optional symbol or company-name search.
            limit: Explicit maximum stocks, one through five thousand.

        Returns:
            Actual quotes, market coverage, provenance and available metrics.
        """
        return await service.snapshot(
            principal, region=region, venue=venue, query=query, limit=limit
        )

    @router.get("/metadata")
    def metadata(principal: owner) -> heatmap.CompanyMetadataBatch:
        """Lists the owner's explicitly supplied, dated company fundamentals.

        Args:
            principal: Authenticated tenant owner.

        Returns:
            Company sectors/capitalizations with their original observation
            dates.
        """
        return heatmap.CompanyMetadataBatch(records=service.metadata(principal))

    @router.put("/metadata", status_code=204)
    def put_metadata(
        body: heatmap.CompanyMetadataBatch, principal: owner
    ) -> fastapi.Response:
        """Stores owned optional fundamentals separately.

        Stores owned optional fundamentals separately from provider quote data.

        Args:
            body: Explicit dated stock/ETF sectors and capitalization records.
            principal: Authenticated author.

        Returns:
            Empty response after atomic persistence of supplied records.
        """
        service.put_metadata(principal, body.records)
        return fastapi.Response(status_code=204)

    return router
