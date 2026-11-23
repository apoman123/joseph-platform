"""Canonical market-data value objects.

Candles, trades, order books, funding and open interest normalized from any
venue. Prices and quantities are ``Decimal``; timestamps are UTC epoch ms.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes

_SPREAD_QUANTUM = decimal.Decimal("0.000001")
"""Spread percentages are reported to 1e-6 % (display precision)."""


class BuyVolumeMethod(enum.StrEnum):
    """Origin of a bar's buy volume (contract ``BuyVolumeMethod``)."""

    TAKER = "taker"
    """Venue-published aggressor side: true delta."""
    CLASSIFIED = "classified"
    """Trades classified against quotes (Lee-Ready / quote rule)."""


@dataclasses.dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV bar over ``[start_ms, end_ms)``.

    Only ``is_final`` bars may confirm signals. A correction of the same bar
    increments ``revision``; it never retroactively erases fired signals.

    Attributes:
        instrument_id: Exact venue instrument.
        timeframe: Bar resolution.
        start_ms: Inclusive UTC start.
        end_ms: Exclusive UTC end.
        open: Opening price.
        high: Highest traded price.
        low: Lowest traded price.
        close: Last price within the bar.
        base_volume: Volume in base units.
        is_final: True once the bar is closed and confirmed.
        source: Origin such as ``binance.ws``.
        feed_state: Provenance at receipt.
        received_at_ms: When Joseph received the data.
        revision: Correction counter for the same bar.
        quote_turnover: Optional quote-currency turnover.
        trade_count: Optional number of trades.
        event_ts_ms: Optional venue event timestamp.
        taker_buy_base_volume: Optional base volume of trades whose
            aggressor bought (venue-published or summed from the venue's
            public trades); enables true volume delta. ``None`` when the
            venue publishes neither.
        taker_buy_method: How ``taker_buy_base_volume`` was obtained:
            ``taker`` from the venue's aggressor flag (true delta) or
            ``classified`` from trades against quotes (Lee-Ready / quote
            rule) where the market publishes no aggressor side, as on
            stock exchanges. ``None`` means ``taker`` when a volume is set.
    """

    instrument_id: instruments.InstrumentId
    timeframe: timeframes.Timeframe
    start_ms: int
    end_ms: int
    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    base_volume: decimal.Decimal
    is_final: bool
    source: str
    feed_state: quality.FeedState
    received_at_ms: int
    revision: int = 0
    quote_turnover: decimal.Decimal | None = None
    trade_count: int | None = None
    event_ts_ms: int | None = None
    taker_buy_base_volume: decimal.Decimal | None = None
    taker_buy_method: BuyVolumeMethod | None = None

    def __post_init__(self) -> None:
        """Validates bar invariants.

        Raises:
            ValueError: If boundaries or OHLC relationships are inconsistent.
        """
        calendar = sessions.for_instrument(self.instrument_id)
        if not calendar.is_aligned(self.timeframe, self.start_ms):
            raise ValueError("start_ms is not aligned to the timeframe")
        if self.end_ms != calendar.end_of(self.timeframe, self.start_ms):
            raise ValueError("end_ms must equal the bar end of start_ms")
        if self.low > self.high:
            raise ValueError("low must be <= high")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open outside [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close outside [low, high]")
        if self.base_volume < decimals.ZERO:
            raise ValueError("volume must be non-negative")
        taker = self.taker_buy_base_volume
        if taker is not None and not decimals.ZERO <= taker <= self.base_volume:
            raise ValueError("taker buy volume outside [0, base_volume]")
        if taker is None and self.taker_buy_method is not None:
            raise ValueError("taker_buy_method needs taker_buy_base_volume")

    @property
    def buy_volume_method(self) -> BuyVolumeMethod | None:
        """Returns how the buy volume was obtained (``None`` without one)."""
        if self.taker_buy_base_volume is None:
            return None
        return self.taker_buy_method or BuyVolumeMethod.TAKER

    @property
    def body_high(self) -> decimal.Decimal:
        """Returns ``max(open, close)``."""
        return max(self.open, self.close)

    @property
    def body_low(self) -> decimal.Decimal:
        """Returns ``min(open, close)``."""
        return min(self.open, self.close)

    @property
    def is_bullish(self) -> bool:
        """Returns True when close > open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Returns True when close < open."""
        return self.close < self.open

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Candle`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "timeframe": self.timeframe.value,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "open": decimals.format_decimal(self.open),
            "high": decimals.format_decimal(self.high),
            "low": decimals.format_decimal(self.low),
            "close": decimals.format_decimal(self.close),
            "base_volume": decimals.format_decimal(self.base_volume),
            "quote_turnover": decimals.format_optional(self.quote_turnover),
            "trade_count": self.trade_count,
            "taker_buy_base_volume": decimals.format_optional(
                self.taker_buy_base_volume
            ),
            "taker_buy_method": (
                None
                if self.buy_volume_method is None
                else self.buy_volume_method.value
            ),
            "is_final": self.is_final,
            "revision": self.revision,
            "source": self.source,
            "feed_state": self.feed_state.value,
            "received_at_ms": self.received_at_ms,
            "event_ts_ms": self.event_ts_ms,
        }


class AggressorSide(enum.StrEnum):
    """Taker side of a trade as reported by the venue."""

    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True, slots=True)
class Trade:
    """Single (or venue-aggregated) public trade.

    Attributes:
        instrument_id: Exact venue instrument.
        trade_id: Venue trade id.
        venue_event_ts_ms: Venue timestamp.
        received_at_ms: Receipt timestamp.
        price: Trade price.
        base_quantity: Quantity in base units.
        aggressor_side: Taker side, ``UNKNOWN`` if not published.
        is_aggregated: True when the venue aggregates fills.
        quote_quantity: Optional quote notional.
    """

    instrument_id: instruments.InstrumentId
    trade_id: str
    venue_event_ts_ms: int
    received_at_ms: int
    price: decimal.Decimal
    base_quantity: decimal.Decimal
    aggressor_side: AggressorSide
    is_aggregated: bool
    quote_quantity: decimal.Decimal | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class BookLevel:
    """One price level of an order book.

    Attributes:
        price: Level price.
        quantity: Resting base quantity.
    """

    price: decimal.Decimal
    quantity: decimal.Decimal


@dataclasses.dataclass(frozen=True, slots=True)
class OrderBook:
    """Order book view; bids descending, asks ascending.

    Attributes:
        instrument_id: Exact venue instrument.
        bids: Bid levels, best first.
        asks: Ask levels, best first.
        snapshot_ts_ms: Venue/snapshot time.
        received_at_ms: Receipt time.
        source: Origin such as ``binance.rest``.
        stale: True if the book failed sequence checks or aged out.
        venue_sequence: Last applied venue sequence/update id.
        checksum_verified: ``None`` when the venue has no checksum.
    """

    instrument_id: instruments.InstrumentId
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    snapshot_ts_ms: int
    received_at_ms: int
    source: str
    stale: bool = False
    venue_sequence: int | None = None
    checksum_verified: bool | None = None

    @property
    def best_bid(self) -> decimal.Decimal | None:
        """Returns the best bid price, or ``None`` for an empty side."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> decimal.Decimal | None:
        """Returns the best ask price, or ``None`` for an empty side."""
        return self.asks[0].price if self.asks else None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``OrderBook`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "venue_sequence": self.venue_sequence,
            "snapshot_ts_ms": self.snapshot_ts_ms,
            "received_at_ms": self.received_at_ms,
            "bids": [
                [
                    decimals.format_decimal(lvl.price),
                    decimals.format_decimal(lvl.quantity),
                ]
                for lvl in self.bids
            ],
            "asks": [
                [
                    decimals.format_decimal(lvl.price),
                    decimals.format_decimal(lvl.quantity),
                ]
                for lvl in self.asks
            ],
            "depth": max(len(self.bids), len(self.asks)),
            "stale": self.stale,
            "source": self.source,
            "checksum_verified": self.checksum_verified,
        }


class FundingKind(enum.StrEnum):
    """Whether a funding rate is a projection or a settled payment."""

    PREDICTED = "PREDICTED"
    SETTLED = "SETTLED"


@dataclasses.dataclass(frozen=True, slots=True)
class Funding:
    """Funding rate observation.

    Attributes:
        instrument_id: Exact venue instrument.
        rate: Rate per interval as a decimal fraction (0.0001 = 0.01%).
        interval_hours: Funding interval length in hours.
        kind: Predicted or settled.
        effective_time_ms: Time the rate applies to.
        source: Data origin.
        as_of_ms: Observation time.
        next_funding_ms: Optional next settlement time.
    """

    instrument_id: instruments.InstrumentId
    rate: decimal.Decimal
    interval_hours: float
    kind: FundingKind
    effective_time_ms: int
    source: str
    as_of_ms: int
    next_funding_ms: int | None = None

    @property
    def normalized_8h_pct(self) -> decimal.Decimal:
        """Returns ``rate * 100 * 8 / interval_hours`` for comparison only."""
        interval = decimals.to_decimal(self.interval_hours)
        return self.rate * decimals.HUNDRED * decimal.Decimal(8) / interval

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Funding`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "rate": decimals.format_decimal(self.rate),
            "interval_hours": self.interval_hours,
            "normalized_8h_pct": decimals.format_decimal(
                self.normalized_8h_pct
            ),
            "kind": self.kind.value,
            "effective_time_ms": self.effective_time_ms,
            "next_funding_ms": self.next_funding_ms,
            "source": self.source,
            "as_of_ms": self.as_of_ms,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class OpenInterest:
    """Open interest observation.

    Attributes:
        instrument_id: Exact venue instrument.
        base_quantity: Open interest in base units.
        notional_usd: Open interest notional in USD (base * mark).
        source: Data origin.
        as_of_ms: Observation time.
        mark_price: Mark price used for the notional.
    """

    instrument_id: instruments.InstrumentId
    base_quantity: decimal.Decimal
    notional_usd: decimal.Decimal
    source: str
    as_of_ms: int
    mark_price: decimal.Decimal | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``OpenInterest`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "base_quantity": decimals.format_decimal(self.base_quantity),
            "notional_usd": decimals.format_decimal(self.notional_usd),
            "mark_price": decimals.format_optional(self.mark_price),
            "source": self.source,
            "as_of_ms": self.as_of_ms,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Latest market state for one instrument. Missing values stay ``None``.

    Attributes:
        instrument_id: Exact venue instrument.
        feed_state: Provenance of the snapshot.
        source: Data origin.
        as_of_ms: Observation time.
        last: Last traded price.
        mark: Mark price.
        index: Index price.
        best_bid: Best bid.
        best_ask: Best ask.
        turnover_24h_quote: Rolling 24h quote turnover.
        volume_24h_base: Rolling 24h base volume.
        change_24h_pct: Rolling 24h price change in percent.
        funding: Latest funding observation.
        open_interest: Latest open interest observation.
    """

    instrument_id: instruments.InstrumentId
    feed_state: quality.FeedState
    source: str
    as_of_ms: int
    last: decimal.Decimal | None = None
    mark: decimal.Decimal | None = None
    index: decimal.Decimal | None = None
    best_bid: decimal.Decimal | None = None
    best_ask: decimal.Decimal | None = None
    turnover_24h_quote: decimal.Decimal | None = None
    volume_24h_base: decimal.Decimal | None = None
    change_24h_pct: decimal.Decimal | None = None
    funding: Funding | None = None
    open_interest: OpenInterest | None = None

    @property
    def spread_pct(self) -> decimal.Decimal | None:
        """Returns ``(ask - bid) / mid * 100`` or ``None`` if unavailable."""
        if self.best_bid is None or self.best_ask is None:
            return None
        mid = (self.best_bid + self.best_ask) / 2
        if mid <= decimals.ZERO:
            return None
        return (self.best_ask - self.best_bid) / mid * decimals.HUNDRED

    def to_contract(
        self, now_ms: int, stale_after_ms: int
    ) -> dict[str, object]:
        """Serializes to the contract ``MarketSnapshot`` object.

        Args:
            now_ms: Current time used to compute ``data_age_ms``.
            stale_after_ms: Age above which the snapshot is flagged stale.

        Returns:
            JSON-compatible dict.
        """
        age = max(0, now_ms - self.as_of_ms)
        return {
            "instrument_id": str(self.instrument_id),
            "last": decimals.format_optional(self.last),
            "mark": decimals.format_optional(self.mark),
            "index": decimals.format_optional(self.index),
            "best_bid": decimals.format_optional(self.best_bid),
            "best_ask": decimals.format_optional(self.best_ask),
            "spread_pct": decimals.format_optional(
                None
                if self.spread_pct is None
                else self.spread_pct.quantize(_SPREAD_QUANTUM)
            ),
            "turnover_24h_quote": decimals.format_optional(
                self.turnover_24h_quote
            ),
            "volume_24h_base": decimals.format_optional(self.volume_24h_base),
            "change_24h_pct": decimals.format_optional(self.change_24h_pct),
            "funding": None
            if self.funding is None
            else self.funding.to_contract(),
            "open_interest": None
            if self.open_interest is None
            else self.open_interest.to_contract(),
            "feed_state": self.feed_state.value,
            "source": self.source,
            "as_of_ms": self.as_of_ms,
            "data_age_ms": age,
            "stale": age > stale_after_ms,
        }
