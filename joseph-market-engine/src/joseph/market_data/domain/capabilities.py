"""Venue market-data capability declarations.

Capabilities describe only what an adapter implements and has verified
against the venue. Anything else is ``False`` with an explanatory note;
callers must reject unsupported requests instead of emulating them.
"""

from __future__ import annotations

import dataclasses

from joseph.domain import instruments
from joseph.domain import timeframes as timeframes_lib


@dataclasses.dataclass(frozen=True, slots=True)
class MarketDataCapabilities:
    """Boolean feature matrix (contract ``MarketDataCapabilities``).

    Attributes:
        rest_history: Historical candles over REST.
        native_ws_candles: Native WebSocket candle stream.
        native_ws_trades: Native WebSocket trade stream.
        native_ws_book: Native WebSocket book stream.
        book_snapshot: REST order-book snapshot.
        open_interest: Open-interest endpoint.
        funding: Funding-rate endpoint.
        trades_history: Historical trades over REST for a time range.
        taker_volume: Candles carry ``taker_buy_base_volume`` (true delta).
        trade_delta: 1m-and-above bars fully covered by recorded public
            trades carry a true delta summed from the trades' aggressor
            side, even though the venue's klines carry none.
        classified_delta: The market publishes no aggressor side (stock
            exchanges); buy volume is classified from trades against the
            prevailing quote (``taker_buy_method = classified``).
    """

    rest_history: bool = False
    native_ws_candles: bool = False
    native_ws_trades: bool = False
    native_ws_book: bool = False
    book_snapshot: bool = False
    open_interest: bool = False
    funding: bool = False
    trades_history: bool = False
    taker_volume: bool = False
    trade_delta: bool = False
    classified_delta: bool = False

    def to_contract(self) -> dict[str, bool]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class VenueCapabilities:
    """Contract ``VenueCapabilities``.

    Attributes:
        venue: Venue id.
        display_name: Human-readable name.
        market_types: Supported product types.
        market_data: Feature matrix.
        timeframes: Candle resolutions served UTC aligned, either as native
            klines or built from the venue's public trades.
        notes: Caveats such as unverified or unsupported features.
        docs_url: Official API documentation.
        verified_at_ms: When the capabilities were last verified live.
        trade_built_timeframes: Subset of ``timeframes`` that the engine
            builds from the venue's public trade stream (seconds bars),
            because the venue publishes no such klines.
        aggregated_timeframes: Subset of ``timeframes`` the engine
            aggregates from a shorter native timeframe that tiles it exactly
            (e.g. ``1M`` from ``1d`` where the venue has no monthly bars).
        data_delay_ms: Delay of the data the feed is entitled to (e.g. 15
            minutes on a free consolidated feed); ``None`` when real time.
        requires_credentials: The data source needs an API key.
        credentials_configured: Whether that key is configured on this
            deployment (always True when none is required).
    """

    venue: instruments.Venue
    display_name: str
    market_types: tuple[instruments.MarketType, ...]
    market_data: MarketDataCapabilities
    timeframes: tuple[timeframes_lib.Timeframe, ...]
    notes: tuple[str, ...]
    docs_url: str
    verified_at_ms: int | None = None
    trade_built_timeframes: tuple[timeframes_lib.Timeframe, ...] = ()
    aggregated_timeframes: tuple[timeframes_lib.Timeframe, ...] = ()
    data_delay_ms: int | None = None
    requires_credentials: bool = False
    credentials_configured: bool = True

    def __post_init__(self) -> None:
        """Validates that derived timeframes are declared timeframes.

        Raises:
            ValueError: If a trade-built or aggregated timeframe is not in
                ``timeframes``.
        """
        for name in ("trade_built_timeframes", "aggregated_timeframes"):
            if set(getattr(self, name)) - set(self.timeframes):
                raise ValueError(f"{name} must be a subset of timeframes")

    @property
    def calendar(self) -> str:
        """Returns the venue's session calendar id."""
        return instruments.calendar_code(self.venue)

    def supports_timeframe(self, timeframe: timeframes_lib.Timeframe) -> bool:
        """Checks timeframe support.

        Args:
            timeframe: Requested resolution.

        Returns:
            True when supported.
        """
        return timeframe in self.timeframes

    def is_trade_built(self, timeframe: timeframes_lib.Timeframe) -> bool:
        """Checks whether bars of a timeframe are built from public trades.

        Args:
            timeframe: Requested resolution.

        Returns:
            True when the engine aggregates the venue's trades into bars.
        """
        return timeframe in self.trade_built_timeframes

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "venue": self.venue.value,
            "display_name": self.display_name,
            "market_types": [m.value for m in self.market_types],
            "market_data": self.market_data.to_contract(),
            "timeframes": [tf.value for tf in self.timeframes],
            "trade_built_timeframes": [
                tf.value for tf in self.trade_built_timeframes
            ],
            "aggregated_timeframes": [
                tf.value for tf in self.aggregated_timeframes
            ],
            "calendar": self.calendar,
            "data_delay_ms": self.data_delay_ms,
            "requires_credentials": self.requires_credentials,
            "credentials_configured": self.credentials_configured,
            "notes": list(self.notes),
            "docs_url": self.docs_url,
            "verified_at_ms": self.verified_at_ms,
        }
