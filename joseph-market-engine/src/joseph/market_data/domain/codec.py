"""Decoding of contract JSON into domain market objects (pure)."""

from __future__ import annotations

from collections.abc import Mapping

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes


def candle_from_contract(
    payload: Mapping[str, object],
    feed_state: quality.FeedState | None = None,
    source: str | None = None,
) -> market.Candle:
    """Decodes a contract ``Candle`` object.

    Args:
        payload: JSON object matching ``market.schema.json#/$defs/Candle``.
        feed_state: Optional provenance override (e.g. ``REPLAY``).
        source: Optional source override.

    Returns:
        Candle.

    Raises:
        ValueError: If a field is missing or malformed.
    """
    try:
        return market.Candle(
            instrument_id=instruments.InstrumentId.parse(
                str(payload["instrument_id"])
            ),
            timeframe=timeframes.parse(str(payload["timeframe"])),
            start_ms=int(str(payload["start_ms"])),
            end_ms=int(str(payload["end_ms"])),
            open=decimals.to_decimal(payload["open"]),
            high=decimals.to_decimal(payload["high"]),
            low=decimals.to_decimal(payload["low"]),
            close=decimals.to_decimal(payload["close"]),
            base_volume=decimals.to_decimal(payload["base_volume"]),
            is_final=bool(payload["is_final"]),
            source=source or str(payload.get("source", "replay")),
            feed_state=feed_state
            or quality.FeedState(str(payload.get("feed_state", "REPLAY"))),
            received_at_ms=int(str(payload.get("received_at_ms", 0))),
            revision=int(str(payload.get("revision", 0))),
            quote_turnover=decimals.to_optional_decimal(
                payload.get("quote_turnover")
            ),
            trade_count=(
                None
                if payload.get("trade_count") is None
                else int(str(payload["trade_count"]))
            ),
            event_ts_ms=(
                None
                if payload.get("event_ts_ms") is None
                else int(str(payload["event_ts_ms"]))
            ),
            taker_buy_base_volume=decimals.to_optional_decimal(
                payload.get("taker_buy_base_volume")
            ),
        )
    except KeyError as err:
        raise ValueError(f"candle missing field {err}") from err
