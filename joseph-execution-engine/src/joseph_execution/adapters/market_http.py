# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HTTP market gateway reading the market engine's public read models.

Failures return ``None`` so the Risk Gateway reports UNKNOWN and fails
closed; nothing is fabricated.
"""

from __future__ import annotations

import decimal
import logging
import time
from collections.abc import Mapping
from collections.abc import Sequence

import httpx

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import market

_LOG = logging.getLogger(__name__)

# Cache lifetimes in seconds: trading calendars change rarely; the previous
# close changes once a day but must pick up a new final bar soon after it.
_SESSION_TTL_S = 3_600.0
_CLOSE_TTL_S = 300.0


class HttpMarketDataGateway:
    """Reads instruments, snapshots, books, calendars and daily closes."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        metadata_ttl_s: float = 300.0,
    ) -> None:
        """Initializes the gateway.

        Args:
            base_url: Market engine URL, e.g. ``http://localhost:8000``.
            client: Optional shared HTTP client; by default the gateway
                creates one with a 5 s timeout.
            metadata_ttl_s: Instrument metadata cache lifetime in seconds.
        """
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._ttl = metadata_ttl_s
        self._meta: dict[str, tuple[float, market.InstrumentMeta]] = {}
        self._sessions: dict[
            tuple[str, int, int],
            tuple[float, tuple[market.TradingSession, ...]],
        ] = {}
        self._closes: dict[str, tuple[float, decimal.Decimal]] = {}

    async def close(self) -> None:
        """Closes the HTTP client."""
        await self._client.aclose()

    async def _get(
        self, path: str, params: Mapping[str, str]
    ) -> dict[str, object] | None:
        """Performs a GET and parses JSON.

        Args:
            path: Path below the base URL.
            params: Query parameters.

        Returns:
            The JSON object, or ``None`` on a transport error, a non-200
            status or a body that is not a JSON object. A 200 response
            whose body is not JSON at all raises the decoder's
            ``ValueError``.
        """
        try:
            response = await self._client.get(
                f"{self._base}{path}", params=dict(params)
            )
        except httpx.HTTPError as err:
            _LOG.warning(
                "market engine %s failed: %s", path, type(err).__name__
            )
            return None
        if response.status_code != 200:
            return None
        body = response.json()
        return body if isinstance(body, dict) else None

    async def instrument(
        self, instrument_id: str
    ) -> market.InstrumentMeta | None:
        """Fetches instrument metadata (cached for ``metadata_ttl_s``).

        Args:
            instrument_id: Canonical id.

        Returns:
            Metadata, or ``None`` when the request fails or the engine lists
            no exact match.
        """
        cached = self._meta.get(instrument_id)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        # Canonical ids are ``venue:kind:symbol``; the instrument search takes
        # the venue and the native symbol, and may return near matches.
        venue, _, symbol = instrument_id.partition(":")
        native = symbol.partition(":")[2]
        body = await self._get("/v1/instruments", {"venue": venue, "q": native})
        if body is None:
            return None
        rows = body.get("instruments")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("instrument_id") == (
                instrument_id
            ):
                meta = parse_instrument(row)
                self._meta[instrument_id] = (time.monotonic(), meta)
                return meta
        return None

    async def quote(self, instrument_id: str) -> market.MarketQuote | None:
        """Fetches the latest snapshot.

        Args:
            instrument_id: Canonical id.

        Returns:
            Quote, or ``None`` when the request fails.
        """
        body = await self._get(
            "/v1/market/snapshot", {"instrument_id": instrument_id}
        )
        return None if body is None else parse_snapshot(body)

    async def order_book(
        self, instrument_id: str, depth: int = 50
    ) -> market.BookView | None:
        """Fetches the reconciled order book.

        Args:
            instrument_id: Canonical id.
            depth: Levels per side.

        Returns:
            Book, or ``None`` when the request fails.
        """
        body = await self._get(
            "/v1/market/orderbook",
            {"instrument_id": instrument_id, "depth": str(depth)},
        )
        return None if body is None else parse_book(body)

    async def sessions(
        self, calendar: str, from_ms: int, to_ms: int
    ) -> Sequence[market.TradingSession] | None:
        """Reads ``/v1/calendars/{id}/sessions`` (cached for an hour).

        Args:
            calendar: Calendar id.
            from_ms: Window start, UTC epoch milliseconds.
            to_ms: Window end, UTC epoch milliseconds.

        Returns:
            Sessions, ``()`` for ``24x7`` or ``None`` on failure.
        """
        if calendar == market.CONTINUOUS_CALENDAR:
            return ()
        # Windows that fall in the same hours share one cache entry.
        hour = 3_600_000
        key = (calendar, from_ms // hour, to_ms // hour)
        cached = self._sessions.get(key)
        if cached is not None and time.monotonic() - cached[0] < (
            _SESSION_TTL_S
        ):
            return cached[1]
        body = await self._get(
            f"/v1/calendars/{calendar}/sessions",
            {"from_ms": str(from_ms), "to_ms": str(to_ms)},
        )
        if body is None:
            return None
        try:
            parsed = parse_sessions(body)
        except (KeyError, TypeError, ValueError):
            _LOG.warning("malformed session list for %s", calendar)
            return None
        self._sessions[key] = (time.monotonic(), parsed)
        return parsed

    async def previous_close(
        self, instrument_id: str
    ) -> decimal.Decimal | None:
        """Reads the last final daily candle close (cached 5 minutes).

        Args:
            instrument_id: Canonical id.

        Returns:
            Close, or ``None`` on failure or without a final daily bar
            (failures are not cached).
        """
        cached = self._closes.get(instrument_id)
        if cached is not None and time.monotonic() - cached[0] < _CLOSE_TTL_S:
            return cached[1]
        body = await self._get(
            "/v1/candles",
            {"instrument_id": instrument_id, "tf": "1d", "limit": "5"},
        )
        close = None if body is None else parse_previous_close(body)
        if close is not None:
            self._closes[instrument_id] = (time.monotonic(), close)
        return close


def parse_sessions(
    body: Mapping[str, object],
) -> tuple[market.TradingSession, ...]:
    """Parses a contract ``SessionList``.

    Args:
        body: Contract object.

    Returns:
        Sessions in open order.

    Raises:
        KeyError: When a session lacks a field.
        TypeError: When ``sessions`` is not a list.
        ValueError: When a timestamp is not an integer.
    """
    rows = body["sessions"]
    if not isinstance(rows, list):
        raise TypeError("sessions must be a list")
    parsed = [
        market.TradingSession(
            day=str(row["day"]),
            open_ms=int(str(row["open_ms"])),
            close_ms=int(str(row["close_ms"])),
        )
        for row in rows
    ]
    return tuple(sorted(parsed, key=lambda s: s.open_ms))


def parse_previous_close(body: Mapping[str, object]) -> decimal.Decimal | None:
    """Finds the close of the latest final bar of a ``CandleBatch``.

    Args:
        body: Contract object.

    Returns:
        Close, or ``None`` when no final bar exists.
    """
    rows = body.get("candles")
    finals = [
        row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get("is_final") and "close" in row
    ]
    if not finals:
        return None
    latest = max(finals, key=lambda row: int(str(row.get("start_ms", 0))))
    try:
        return decimals.to_decimal(latest["close"])
    except ValueError:
        return None


def _ladder(raw: object) -> tuple[market.TickStep, ...]:
    """Parses a contract tick ladder.

    Args:
        raw: List of ``TickStep`` objects.

    Returns:
        Steps sorted by ``min_price``.
    """
    if not isinstance(raw, list):
        return ()
    return tuple(
        sorted(
            market.TickStep(
                decimals.to_decimal(step["min_price"]),
                decimals.to_decimal(step["tick_size"]),
            )
            for step in raw
            if isinstance(step, dict)
        )
    )


def parse_instrument(row: Mapping[str, object]) -> market.InstrumentMeta:
    """Parses a contract ``Instrument``.

    Args:
        row: Contract object.

    Returns:
        Metadata.
    """
    key = row.get("key") or {}
    return market.InstrumentMeta(
        instrument_id=str(row["instrument_id"]),
        venue=str(key.get("venue", "")) if isinstance(key, dict) else "",
        tick_size=decimals.to_decimal(row["tick_size"]),
        lot_size=decimals.to_decimal(row["lot_size"]),
        min_qty=decimals.to_decimal(row["min_qty"]),
        status=enums.InstrumentStatus(str(row.get("status", "UNKNOWN"))),
        contract_multiplier=decimals.to_decimal(
            row.get("contract_multiplier", "1")
        ),
        min_notional=decimals.to_optional(row.get("min_notional")),
        max_qty=decimals.to_optional(row.get("max_qty")),
        max_leverage=decimals.to_optional(row.get("max_leverage")),
        quote=str(key.get("quote", "USD")) if isinstance(key, dict) else "USD",
        calendar=str(row.get("calendar") or market.CONTINUOUS_CALENDAR),
        listing_exchange=None
        if row.get("listing_exchange") is None
        else str(row["listing_exchange"]),
        tick_ladder=_ladder(row.get("tick_ladder")),
        price_limit_pct=decimals.to_optional(row.get("price_limit_pct")),
        board_lot=decimals.to_optional(row.get("board_lot")),
        asset_class=_asset_class(row.get("asset_class")),
    )


def _asset_class(raw: object) -> enums.AssetClass:
    """Parses a contract ``AssetClass`` leniently.

    Args:
        raw: Contract value.

    Returns:
        Asset class; ``UNKNOWN`` for missing or unrecognized values.
    """
    try:
        return enums.AssetClass(str(raw))
    except ValueError:
        return enums.AssetClass.UNKNOWN


def parse_snapshot(body: Mapping[str, object]) -> market.MarketQuote:
    """Parses a contract ``MarketSnapshot``.

    Args:
        body: Contract object.

    Returns:
        Quote; a snapshot flagged stale is reported as STALE.
    """
    state = enums.FeedState(str(body.get("feed_state", "UNAVAILABLE")))
    if body.get("stale"):
        state = enums.FeedState.STALE
    as_of = int(str(body.get("as_of_ms", 0)))
    return market.MarketQuote(
        instrument_id=str(body["instrument_id"]),
        feed_state=state,
        as_of_ms=as_of,
        mark=decimals.to_optional(body.get("mark")),
        last=decimals.to_optional(body.get("last")),
        best_bid=decimals.to_optional(body.get("best_bid")),
        best_ask=decimals.to_optional(body.get("best_ask")),
    )


def parse_book(body: Mapping[str, object]) -> market.BookView:
    """Parses a contract ``OrderBook``.

    Args:
        body: Contract object.

    Returns:
        Book view; a book without a ``stale`` flag counts as stale.
    """
    return market.BookView(
        instrument_id=str(body["instrument_id"]),
        bids=_levels(body.get("bids")),
        asks=_levels(body.get("asks")),
        as_of_ms=int(str(body.get("snapshot_ts_ms", 0))),
        stale=bool(body.get("stale", True)),
    )


def _levels(raw: object) -> tuple[tuple[decimal.Decimal, decimal.Decimal], ...]:
    """Parses book level pairs.

    Args:
        raw: List of ``[price, qty]`` pairs.

    Returns:
        ``(price, quantity)`` levels in the order given; empty when ``raw``
        is not a list.
    """
    if not isinstance(raw, list):
        return ()
    return tuple(
        (decimals.to_decimal(p), decimals.to_decimal(q)) for p, q in raw
    )
