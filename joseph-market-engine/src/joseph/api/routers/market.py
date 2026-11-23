"""Public market-data REST endpoints.

Routes: ``/v1/venues``, ``/v1/instruments``, ``/v1/candles``,
``/v1/calendars/{calendar_id}/sessions`` and ``/v1/market/*``. Query
parameter names match ``joseph-terminal/packages/contracts/src/client/
http.ts``; responses are contract objects.
"""

from __future__ import annotations

import fastapi

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import quality
from joseph.market_data.app import service as market_service
from joseph.screening.app import service as screening_service
from joseph.screening.domain import policy as policy_lib


def _enum[E](kind: type[E], value: str | None, field: str) -> E | None:
    """Parses an optional enum query parameter.

    Args:
        kind: Enum class.
        value: Raw value.
        field: Parameter name for errors.

    Returns:
        Enum member or ``None``.

    Raises:
        errors.ValidationFailedError: On an unknown value.
    """
    if value is None or value == "":
        return None
    try:
        return kind(value)  # type: ignore[call-arg]
    except ValueError as err:
        raise errors.ValidationFailedError(
            f"{field} has an unsupported value {value!r}"
        ) from err


def build_router(
    market_data: market_service.MarketDataService,
    qualification: screening_service.QualificationService,
) -> fastapi.APIRouter:
    """Builds the market-data router.

    Args:
        market_data: Market-data service.
        qualification: Qualification service.

    Returns:
        Router.
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
            venue: Optional venue.
            market_type: Optional market type.
            q: Optional search text.
            limit: Maximum results.

        Returns:
            Contract ``InstrumentList``.
        """
        rows = await market_data.registry.search(
            q,
            venue=_enum(instruments.Venue, venue, "venue"),
            market_type=_enum(
                instruments.MarketType, market_type, "market_type"
            ),
            limit=limit,
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
            tf: Timeframe.
            from_ms: Inclusive start.
            to_ms: Exclusive end.
            limit: Maximum bars.
            feed_mode: Optional ``REPLAY`` to read recordings.

        Returns:
            Contract ``CandleBatch``.
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
            from_ms: Inclusive window start.
            to_ms: Exclusive window end.

        Returns:
            Contract ``SessionList``.
        """
        return market_data.calendar_sessions(calendar_id, from_ms, to_ms)

    @router.get("/market/snapshot")
    async def snapshot(instrument_id: str) -> dict[str, object]:
        """Returns the latest market snapshot.

        Args:
            instrument_id: Canonical instrument id.

        Returns:
            Contract ``MarketSnapshot``.
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
            depth: Levels per side.

        Returns:
            Contract ``OrderBook``.
        """
        book = await market_data.get_order_book(instrument_id, depth)
        return book.to_contract()

    @router.get("/market/qualification")
    async def qualification_report(
        venue: str | None = None,
        status: str | None = None,
        refresh: bool = False,
    ) -> dict[str, object]:
        """Returns qualification results with every gate's evidence.

        Args:
            venue: Optional venue.
            status: Optional status filter.
            refresh: Force a fresh scan.

        Returns:
            Contract ``QualificationReport``.
        """
        report = await qualification.report(
            _enum(instruments.Venue, venue, "venue"),
            _enum(policy_lib.Status, status, "status"),
            refresh=refresh,
        )
        return report.to_contract()

    return router


def _now(market_data: market_service.MarketDataService) -> int:
    """Returns the service clock time.

    Args:
        market_data: Service.

    Returns:
        UTC epoch milliseconds.
    """
    return market_data.now_ms()
