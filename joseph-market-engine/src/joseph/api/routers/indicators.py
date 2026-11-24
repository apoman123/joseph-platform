# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Indicator read endpoints (backend-authoritative outputs for the terminal).

Routes raise domain errors; the application's exception handlers map them
to the contract ``ErrorResponse``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

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
        Plugin ids, or ``None`` for all usable plugins when the parameter is
        absent or blank.
    """
    if raw is None or not raw.strip():
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


_MAX_SETTINGS_CHARS = 4096
"""Upper bound of the ``settings`` query value."""


def _parse_settings(raw: str | None) -> dict[str, dict[str, object]] | None:
    """Parses the JSON ``settings`` parameter.

    The value maps plugin ids to setting overrides, e.g.
    ``{"ema": {"length": 50}}``. Each plugin validates its own
    overrides against its input schema when the engine is built.

    Args:
        raw: Raw parameter value.

    Returns:
        Per-plugin overrides, or ``None`` when absent.

    Raises:
        ValidationFailedError: If the value is not a JSON object of objects
            or is too long.
    """
    if raw is None or not raw.strip():
        return None
    if len(raw) > _MAX_SETTINGS_CHARS:
        raise errors.ValidationFailedError("settings is too long")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise errors.ValidationFailedError(
            f"settings is not valid JSON: {err.msg}"
        ) from err
    if not isinstance(parsed, Mapping) or not all(
        isinstance(key, str) and isinstance(value, Mapping)
        for key, value in parsed.items()
    ):
        raise errors.ValidationFailedError(
            "settings must map plugin ids to objects of overrides"
        )
    return {key: dict(value) for key, value in parsed.items()}


def build_router(
    query_service: service_lib.IndicatorQueryService,
    registry: registry_lib.PluginRegistry,
) -> fastapi.APIRouter:
    """Builds the indicators router.

    Args:
        query_service: Point-in-time query service.
        registry: Plugin registry.

    The routes are public (no bearer token). Invalid query values are
    answered with 422 ``VALIDATION_FAILED``.

    Returns:
        Router with ``/v1/indicators/plugins``, ``/v1/indicators/snapshot``,
        ``/v1/indicators/series`` and ``/v1/aois``.
    """
    router = fastapi.APIRouter(tags=["indicators"])

    @router.get("/v1/indicators/plugins")
    async def list_plugins() -> dict[str, object]:
        """Lists usable indicator plugins in registration order.

        The built-ins come first, then the plugins of installed plugin
        packages; each entry carries the kinds, events and values the
        plugin publishes and its display hints.

        Returns:
            Contract ``IndicatorPluginList``.
        """
        return registry.list_infos()

    @router.get("/v1/indicators/snapshot")
    async def get_snapshots(
        instrument_id: str,
        tf: str,
        plugin_set: str | None = fastapi.Query(None, alias="set"),
        at_ms: int | None = fastapi.Query(None, ge=0),
        settings: str | None = None,
    ) -> dict[str, object]:
        """Returns indicator snapshots as of a point in time.

        Args:
            instrument_id: Canonical instrument id.
            tf: Timeframe value, such as ``1h``.
            plugin_set: Query ``set``: comma-separated plugin ids; all
                usable plugins when absent.
            at_ms: Point in time, UTC epoch milliseconds; the latest closed
                bar when absent.
            settings: JSON object mapping plugin ids to setting overrides.

        Returns:
            Contract ``IndicatorSnapshotList``.

        Raises:
            errors.ValidationFailedError: If a parameter or a setting
                override is invalid (422).
            errors.MissingDataError: If no closed candle is available (422).
        """
        instrument, timeframe = _parse_series(instrument_id, tf)
        return await query_service.snapshots(
            instrument,
            timeframe,
            _parse_set(plugin_set),
            at_ms,
            _parse_settings(settings),
        )

    @router.get("/v1/indicators/series")
    async def get_series(
        instrument_id: str,
        tf: str,
        plugin: str,
        from_ms: int | None = fastapi.Query(None, ge=0),
        settings: str | None = None,
    ) -> dict[str, object]:
        """Returns one plugin's published values on every closed bar.

        Args:
            instrument_id: Canonical instrument id.
            tf: Timeframe value, such as ``1h``.
            plugin: Plugin id.
            from_ms: First bar start, UTC epoch milliseconds; the default
                lookback window when absent.
            settings: JSON object mapping plugin ids to setting overrides;
                only the entry for ``plugin`` is used.

        Returns:
            Contract ``IndicatorSeries``.

        Raises:
            errors.NotFoundError: If the plugin id is unknown (404).
            errors.UnsupportedError: If the plugin is disabled or removed
                (422).
            errors.ValidationFailedError: If a parameter or a setting
                override is invalid (422).
        """
        instrument, timeframe = _parse_series(instrument_id, tf)
        # Rejects an unknown or unusable plugin before any data is fetched.
        registry.get(plugin)
        overrides = _parse_settings(settings) or {}
        return await query_service.series(
            instrument, timeframe, plugin, from_ms, overrides.get(plugin)
        )

    @router.get("/v1/aois")
    async def get_aois(
        instrument_id: str,
        tf: str,
        plugin_set: str | None = fastapi.Query(None, alias="set"),
        at_ms: int | None = fastapi.Query(None, ge=0),
        settings: str | None = None,
    ) -> dict[str, object]:
        """Returns areas of interest (AOIs) and recent events.

        Args:
            instrument_id: Canonical instrument id.
            tf: Timeframe value, such as ``1h``.
            plugin_set: Query ``set``: comma-separated plugin ids; all
                usable plugins when absent.
            at_ms: Point in time, UTC epoch milliseconds; the latest closed
                bar when absent.
            settings: JSON object mapping plugin ids to setting overrides.

        Returns:
            Contract ``AoiList``.

        Raises:
            errors.ValidationFailedError: If a parameter or a setting
                override is invalid (422).
            errors.MissingDataError: If no closed candle is available (422).
        """
        instrument, timeframe = _parse_series(instrument_id, tf)
        return await query_service.aois(
            instrument,
            timeframe,
            _parse_set(plugin_set),
            at_ms,
            _parse_settings(settings),
        )

    return router
