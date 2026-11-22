"""Indicator read endpoints (backend-authoritative outputs for the terminal).

Routes raise domain errors; the application's exception handlers map them
to the contract ``ErrorResponse``.
"""

from __future__ import annotations

import fastapi

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.indicators import service as service_lib
from joseph.indicators.sdk import registry as registry_lib


def _parse_series(
    instrument_id: str, tf: str
) -> tuple[instruments.InstrumentId, timeframes.Timeframe]:
    """Parses and validates series query parameters.

    Args:
        instrument_id: Canonical instrument id text.
        tf: Timeframe text.

    Returns:
        Parsed instrument id and timeframe.

    Raises:
        ValidationFailedError: If either value is invalid.
    """
    try:
        return instruments.InstrumentId.parse(instrument_id), timeframes.parse(
            tf
        )
    except ValueError as err:
        raise errors.ValidationFailedError(str(err)) from err


def _parse_set(raw: str | None) -> list[str] | None:
    """Splits the comma-separated ``set`` parameter.

    Args:
        raw: Raw parameter value.

    Returns:
        Plugin ids, or ``None`` for all plugins.
    """
    if raw is None or not raw.strip():
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_router(
    query_service: service_lib.IndicatorQueryService,
    registry: registry_lib.PluginRegistry,
) -> fastapi.APIRouter:
    """Builds the indicators router.

    Args:
        query_service: Point-in-time query service.
        registry: Plugin registry.

    Returns:
        Router with ``/v1/indicators/plugins``, ``/v1/indicators/snapshot``
        and ``/v1/aois``.
    """
    router = fastapi.APIRouter(tags=["indicators"])

    @router.get("/v1/indicators/plugins")
    async def list_plugins() -> dict[str, object]:
        """Lists usable indicator plugins (contract IndicatorPluginList)."""
        return registry.list_infos()

    @router.get("/v1/indicators/snapshot")
    async def get_snapshots(
        instrument_id: str,
        tf: str,
        plugin_set: str | None = fastapi.Query(None, alias="set"),
        at_ms: int | None = fastapi.Query(None, ge=0),
    ) -> dict[str, object]:
        """Returns indicator snapshots (contract IndicatorSnapshotList)."""
        instrument, timeframe = _parse_series(instrument_id, tf)
        return await query_service.snapshots(
            instrument, timeframe, _parse_set(plugin_set), at_ms
        )

    @router.get("/v1/aois")
    async def get_aois(
        instrument_id: str,
        tf: str,
        plugin_set: str | None = fastapi.Query(None, alias="set"),
        at_ms: int | None = fastapi.Query(None, ge=0),
    ) -> dict[str, object]:
        """Returns AOIs and recent events (contract AoiList)."""
        instrument, timeframe = _parse_series(instrument_id, tf)
        return await query_service.aois(
            instrument, timeframe, _parse_set(plugin_set), at_ms
        )

    return router
