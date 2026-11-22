"""Liveness and metrics endpoints."""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph.market_data.app import service as market_service
from joseph.platform import config
from joseph.platform import metrics as metrics_lib

SERVICE = "joseph-market-engine"
VERSION = "0.5.0"


def build_router(
    market_data: market_service.MarketDataService,
    metrics: metrics_lib.MetricsRegistry,
    settings: config.Settings,
) -> fastapi.APIRouter:
    """Builds ``/health`` and ``/metrics``.

    Args:
        market_data: Market-data service (for venue health).
        metrics: Metrics registry.
        settings: Settings.

    Returns:
        Router.
    """
    router = fastapi.APIRouter(tags=["health"])

    @router.get("/health")
    async def health() -> dict[str, object]:
        """Reports liveness and per-venue feed health.

        Returns:
            Health document.
        """
        venues = {
            caps.venue.value: feed.state.value
            for caps, feed in market_data.venue_status()
        }
        return {
            "status": "ok",
            "service": SERVICE,
            "version": VERSION,
            "demo_only": settings.demo_only,
            "venues": venues,
        }

    @router.get("/metrics", response_class=responses.PlainTextResponse)
    async def metrics_endpoint() -> str:
        """Exposes Prometheus metrics.

        Returns:
            Text exposition document.
        """
        return metrics.render()

    return router
