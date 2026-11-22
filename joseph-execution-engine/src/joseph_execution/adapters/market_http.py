"""HTTP market gateway reading the market engine's public read models.

Failures return ``None`` so the Risk Gateway reports UNKNOWN and fails
closed; nothing is fabricated.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

import httpx

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import market

_LOG = logging.getLogger(__name__)


class HttpMarketDataGateway:
    """Reads ``/v1/instruments``, ``/v1/market/snapshot`` and orderbook."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        metadata_ttl_s: float = 300.0,
    ) -> None:
        """Initializes the gateway.

        Args:
            base_url: Market engine URL, e.g. ``http://localhost:8000``.
            client: Optional shared HTTP client.
            metadata_ttl_s: Instrument metadata cache lifetime.
        """
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._ttl = metadata_ttl_s
        self._meta: dict[str, tuple[float, market.InstrumentMeta]] = {}

    async def close(self) -> None:
        """Closes the HTTP client."""
        await self._client.aclose()

    async def _get(
        self, path: str, params: Mapping[str, str]
    ) -> dict[str, object] | None:
        """Performs a GET and parses JSON.

        Args:
            path: Path.
            params: Query parameters.

        Returns:
            JSON object or ``None`` on any failure.
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
        """Fetches instrument metadata (cached).

        Args:
            instrument_id: Canonical id.

        Returns:
            Metadata or ``None``.
        """
        cached = self._meta.get(instrument_id)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
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
            Quote or ``None``.
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
            Book or ``None``.
        """
        body = await self._get(
            "/v1/market/orderbook",
            {"instrument_id": instrument_id, "depth": str(depth)},
        )
        return None if body is None else parse_book(body)


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
    )


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
        Book view.
    """

    def levels(raw: object) -> tuple[tuple[object, object], ...]:
        """Parses level pairs.

        Args:
            raw: List of ``[price, qty]``.

        Returns:
            Parsed levels.
        """
        if not isinstance(raw, list):
            return ()
        return tuple(
            (decimals.to_decimal(p), decimals.to_decimal(q)) for p, q in raw
        )

    return market.BookView(
        instrument_id=str(body["instrument_id"]),
        bids=levels(body.get("bids")),  # type: ignore[arg-type]
        asks=levels(body.get("asks")),  # type: ignore[arg-type]
        as_of_ms=int(str(body.get("snapshot_ts_ms", 0))),
        stale=bool(body.get("stale", True)),
    )
