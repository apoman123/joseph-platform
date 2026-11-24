# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Liveness and metrics endpoints."""

from __future__ import annotations

import fastapi
from fastapi import responses

from joseph.market_data.app import service as market_service
from joseph.platform import config
from joseph.platform import metrics as metrics_lib

SERVICE = "joseph-market-engine"
# Reported by ``/health`` and as the OpenAPI version; kept equal to the
# project version in ``pyproject.toml``.
VERSION = "0.5.0"


def build_router(
    market_data: market_service.MarketDataService,
    metrics: metrics_lib.MetricsRegistry,
    settings: config.Settings,
) -> fastapi.APIRouter:
    """Builds ``/health`` and ``/metrics``.

    Both routes are public (no bearer token).

    Args:
        market_data: Market-data service (for venue health).
        metrics: Metrics registry to render.
        settings: Settings (``demo_only`` is reported).

    Returns:
        Router serving ``/health`` and ``/metrics``.
    """
    router = fastapi.APIRouter(tags=["health"])

    @router.get("/health")
    async def health() -> dict[str, object]:
        """Reports liveness and per-venue feed health.

        The status is always ``ok`` with HTTP 200 while the process serves
        requests; a degraded venue feed shows only in ``venues``.

        Returns:
            Health document: ``status``, ``service``, ``version``,
            ``demo_only`` and ``venues`` (venue id to feed state).
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
            Prometheus text exposition document.
        """
        return metrics.render()

    return router
