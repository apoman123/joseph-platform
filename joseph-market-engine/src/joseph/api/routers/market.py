# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Public market-data REST endpoints.

Routes: ``/v1/venues``, ``/v1/instruments``, ``/v1/candles``,
``/v1/calendars/{calendar_id}/sessions`` and ``/v1/market/*``. Query
parameter names match ``joseph-terminal/packages/contracts/src/client/
http.ts``; responses are contract objects. The routes are public (no
bearer token); domain errors are rendered by ``api.errors``.
"""

from __future__ import annotations

import enum

import fastapi

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import quality
from joseph.market_data.app import service as market_service


def _enum[EnumType: enum.Enum](
    kind: type[EnumType], value: str | None, field: str
) -> EnumType | None:
    """Parses an optional enum query parameter.

    Args:
        kind: Enum class.
        value: Raw value.
        field: Parameter name for errors.

    Returns:
        Enum member, or ``None`` when the parameter is absent or empty.

    Raises:
        errors.ValidationFailedError: On an unknown value (422).
    """
    if value is None or value == "":
        return None
    try:
        return kind(value)
    except ValueError as err:
        raise errors.ValidationFailedError(
            f"{field} has an unsupported value {value!r}"
        ) from err


def build_router(
    market_data: market_service.MarketDataService,
) -> fastapi.APIRouter:
    """Builds the market-data router.

    Args:
        market_data: Market-data service.

    Returns:
        Router mounted at ``/v1``.
    """
    router = fastapi.APIRouter(prefix="/v1", tags=["market"])

    @router.get("/venues")
    async def venues() -> dict[str, object]:
        """Lists venues with capabilities and live feed health.

        Returns:
            Contract ``VenueStatusList``.
        """
        rows = [
            {"capabilities": caps.to_contract(), "health": feed.to_contract()}
            for caps, feed in market_data.venue_status()
        ]
        return {"venues": rows, "as_of_ms": _now(market_data)}

    @router.get("/instruments")
    async def list_instruments(
        venue: str | None = None,
        market_type: str | None = None,
        q: str | None = None,
        limit: int = fastapi.Query(500, ge=1, le=5000),
    ) -> dict[str, object]:
        """Searches exact-venue instruments.

        Args:
            venue: Optional contract ``Venue`` filter.
            market_type: Optional contract ``MarketType`` filter.
            q: Optional search text.
            limit: Maximum results (1..5000, 422 outside).

        Returns:
            Contract ``InstrumentList``.

        Raises:
            errors.ValidationFailedError: On an unknown ``venue`` or
                ``market_type`` (422).
        """
        rows = await market_data.registry.search(
            q,
            venue=_enum(instruments.Venue, venue, "venue"),
            market_type=_enum(
                instruments.MarketType, market_type, "market_type"
            ),
            limit=limit,
            interactive=True,
        )
        return {
            "instruments": [row.to_contract() for row in rows],
            "as_of_ms": _now(market_data),
        }

    @router.get("/candles")
    async def candles(
        *,
        instrument_id: str,
        tf: str,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int | None = None,
        feed_mode: str | None = None,
    ) -> dict[str, object]:
        """Returns historical candles with provenance and gaps.

        Args:
            instrument_id: Canonical instrument id.
            tf: Timeframe value, such as ``1h``.
            from_ms: Inclusive start, UTC epoch milliseconds.
            to_ms: Exclusive end, UTC epoch milliseconds.
            limit: Maximum bars (default 500, max 5000).
            feed_mode: Contract ``FeedMode`` (``AUTO`` when absent);
                ``REPLAY`` reads recordings, the other modes the venue.

        Returns:
            Contract ``CandleBatch``.

        Raises:
            errors.ValidationFailedError: On an invalid id, timeframe,
                range, limit or ``feed_mode`` (422).
            errors.NotFoundError: If the venue is not enabled (404).
            errors.MissingDataError: For ``REPLAY`` without a replay source
                (422).
        """
        mode = _enum(quality.FeedMode, feed_mode, "feed_mode")
        batch = await market_data.get_batch(
            instrument_id,
            tf,
            from_ms=from_ms,
            to_ms=to_ms,
            limit=limit,
            feed_mode=mode or quality.FeedMode.AUTO,
        )
        return batch.to_contract()

    @router.get("/calendars/{calendar_id}/sessions")
    async def calendar_sessions(
        calendar_id: str,
        from_ms: int = fastapi.Query(..., ge=0),
        to_ms: int = fastapi.Query(..., ge=0),
    ) -> dict[str, object]:
        """Lists the trading sessions of a calendar in a window.

        The window ``[from_ms, to_ms)`` may span at most 400 days. A 24x7
        calendar never closes and returns no sessions.

        Args:
            calendar_id: Contract ``CalendarId`` (``24x7``, ``XTAI``,
                ``XNYS``, ``USBOND``).
            from_ms: Inclusive window start, UTC epoch milliseconds.
            to_ms: Exclusive window end, UTC epoch milliseconds.

        Returns:
            Contract ``SessionList``.

        Raises:
            errors.NotFoundError: For an unknown calendar (404).
            errors.ValidationFailedError: For an empty or too long window
                (422).
        """
        return market_data.calendar_sessions(calendar_id, from_ms, to_ms)

    @router.get("/market/snapshot")
    async def snapshot(instrument_id: str) -> dict[str, object]:
        """Returns the latest market snapshot (cached for one second).

        Args:
            instrument_id: Canonical instrument id.

        Returns:
            Contract ``MarketSnapshot``, flagged stale when older than the
            service's ``stale_after_ms``.

        Raises:
            errors.ValidationFailedError: On a malformed id (422).
            errors.NotFoundError: If the venue is not enabled (404).
        """
        result = await market_data.get_snapshot(instrument_id)
        return result.to_contract(_now(market_data), market_data.stale_after_ms)

    @router.get("/market/orderbook")
    async def orderbook(
        instrument_id: str,
        depth: int = fastapi.Query(50, ge=1, le=1000),
    ) -> dict[str, object]:
        """Returns an order-book snapshot.

        Args:
            instrument_id: Canonical instrument id.
            depth: Levels per side (1..1000, 422 outside).

        Returns:
            Contract ``OrderBook``.

        Raises:
            errors.ValidationFailedError: On a malformed id (422).
            errors.NotFoundError: If the venue is not enabled (404).
        """
        book = await market_data.get_order_book(instrument_id, depth)
        return book.to_contract()

    return router


def _now(market_data: market_service.MarketDataService) -> int:
    """Returns the service clock time.

    Args:
        market_data: Service whose clock is read.

    Returns:
        UTC epoch milliseconds.
    """
    return market_data.now_ms()
