"""Venue-scoped instrument identity and contract metadata.

``BTCUSDT`` on Binance and ``BTC`` on Hyperliquid are different instruments:
identity always includes venue, market type and the venue's native market id.
The canonical string form is ``{venue}:{market_type}:{venue_market_id}``.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
import re

from joseph.domain import decimals


class Venue(enum.StrEnum):
    """Supported venues (contract ``Venue``). ``MOCK`` is DEMO-only."""

    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    LIGHTER = "lighter"
    BITGET = "bitget"
    MEXC = "mexc"
    TWSE = "twse"
    NASDAQ = "nasdaq"
    NYSE = "nyse"
    TREASURY = "treasury"
    MOCK = "mock"


class MarketType(enum.StrEnum):
    """Product type at a venue (contract ``MarketType``).

    ``stock`` and ``etf`` are exchange-listed cash securities; ``rate`` is a
    published, non-tradable rate series such as a Treasury par yield.
    """

    PERP = "perp"
    SPOT = "spot"
    FUTURE = "future"
    STOCK = "stock"
    ETF = "etf"
    RATE = "rate"


class InstrumentStatus(enum.StrEnum):
    """Trading status reported by the venue."""

    TRADING = "TRADING"
    HALTED = "HALTED"
    PRE_LAUNCH = "PRE_LAUNCH"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class AssetClass(enum.StrEnum):
    """Asset class used to choose qualification thresholds."""

    CRYPTO = "CRYPTO"
    RWA = "RWA"
    EQUITY = "EQUITY"
    BOND = "BOND"
    UNKNOWN = "UNKNOWN"


_ID_PATTERN = re.compile(
    r"^(binance|hyperliquid|lighter|bitget|mexc|twse|nasdaq|nyse|treasury"
    r"|mock):(perp|spot|future|stock|etf|rate):([A-Za-z0-9._/-]{1,64})$"
)

EXCHANGE_VENUES = frozenset({Venue.TWSE, Venue.NASDAQ, Venue.NYSE})
"""Venues that are securities exchanges (session calendars, brokers)."""

RATE_VENUES = frozenset({Venue.TREASURY})
"""Venues that publish non-tradable rate series."""

_CALENDAR_BY_VENUE: dict[Venue, str] = {
    Venue.TWSE: "XTAI",
    Venue.NASDAQ: "XNYS",
    Venue.NYSE: "XNYS",
    Venue.TREASURY: "USBOND",
}


def calendar_code(venue: Venue, market_type: MarketType | None = None) -> str:
    """Returns the session calendar id of a venue's instruments.

    DEMO stocks and ETFs (``mock:stock:*``) follow the NYSE calendar so the
    session-aware paths can be exercised without network access.

    Args:
        venue: Venue.
        market_type: Product type, when known.

    Returns:
        Contract ``CalendarId`` (``24x7`` for crypto venues).
    """
    if venue is Venue.MOCK and market_type in (
        MarketType.STOCK,
        MarketType.ETF,
    ):
        return "XNYS"
    return _CALENDAR_BY_VENUE.get(venue, "24x7")


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class TickStep:
    """One rung of a price-dependent tick ladder.

    Attributes:
        min_price: Prices at or above this use ``tick_size``.
        tick_size: Price increment.
    """

    min_price: decimal.Decimal
    tick_size: decimal.Decimal

    def to_contract(self) -> dict[str, str]:
        """Serializes to the contract ``TickStep`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "min_price": decimals.format_decimal(self.min_price),
            "tick_size": decimals.format_decimal(self.tick_size),
        }


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """Canonical venue-scoped instrument identifier.

    Attributes:
        venue: Venue that lists the instrument.
        market_type: Product type.
        venue_market_id: Native market id at the venue (e.g. ``BTCUSDT``).
    """

    venue: Venue
    market_type: MarketType
    venue_market_id: str

    def __str__(self) -> str:
        """Returns the canonical ``venue:type:id`` text."""
        return (
            f"{self.venue.value}:{self.market_type.value}:"
            f"{self.venue_market_id}"
        )

    @classmethod
    def parse(cls, text: str) -> InstrumentId:
        """Parses a canonical instrument id string.

        Args:
            text: Text like ``"binance:perp:BTCUSDT"``.

        Returns:
            The parsed identifier.

        Raises:
            ValueError: If the text is not a canonical instrument id.
        """
        match = _ID_PATTERN.match(text)
        if match is None:
            raise ValueError(f"invalid instrument id: {text!r}")
        return cls(
            venue=Venue(match.group(1)),
            market_type=MarketType(match.group(2)),
            venue_market_id=match.group(3),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentKey:
    """Full structured identity of a contract (contract ``InstrumentKey``).

    Attributes:
        instrument_id: Canonical identifier.
        canonical_base: Base asset, e.g. ``BTC``.
        quote: Quote currency, e.g. ``USDT``.
        settlement: Margin/settlement currency, e.g. ``USDC``.
        instrument_version: Increments when venue contract terms change.
    """

    instrument_id: InstrumentId
    canonical_base: str
    quote: str
    settlement: str
    instrument_version: int = 1

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``InstrumentKey`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "venue": self.instrument_id.venue.value,
            "market_type": self.instrument_id.market_type.value,
            "venue_market_id": self.instrument_id.venue_market_id,
            "canonical_base": self.canonical_base,
            "quote": self.quote,
            "settlement": self.settlement,
            "instrument_version": self.instrument_version,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Instrument:
    """Instrument metadata with precision and contract terms.

    Attributes:
        key: Structured identity.
        display_symbol: Human-facing symbol.
        tick_size: Minimum price increment.
        lot_size: Minimum quantity increment (base units).
        min_qty: Minimum order quantity (base units).
        contract_multiplier: Base units per contract.
        linear: True for linear (quote-margined) contracts.
        status: Venue trading status.
        asset_class: Class used for qualification thresholds.
        source: Data origin such as ``binance.rest``.
        as_of_ms: When the metadata was fetched.
        max_qty: Optional maximum order quantity.
        min_notional: Optional minimum order notional in quote currency.
        funding_interval_hours: Funding interval, ``None`` when unknown.
        max_leverage: Optional maximum leverage.
        listing_exchange: Exchange that lists a security (e.g. ``NYSE
            Arca``), ``None`` for crypto.
        tick_ladder: Price-dependent ticks (TWSE); empty when ``tick_size``
            applies at every price.
        price_limit_pct: Daily price limit from the reference price in
            percent (TWSE 10), ``None`` when unlimited.
        board_lot: Shares per board lot (TWSE 1000), ``None`` when orders
            are in single units.
        name: Issuer or series name in English (e.g. ``TSMC``), ``None``
            when the venue publishes none.
        local_name: Name in the listing market's language (e.g. ``台積電``
            on TWSE), ``None`` when none.
    """

    key: InstrumentKey
    display_symbol: str
    tick_size: decimal.Decimal
    lot_size: decimal.Decimal
    min_qty: decimal.Decimal
    contract_multiplier: decimal.Decimal
    linear: bool
    status: InstrumentStatus
    asset_class: AssetClass
    source: str
    as_of_ms: int
    max_qty: decimal.Decimal | None = None
    min_notional: decimal.Decimal | None = None
    funding_interval_hours: float | None = None
    max_leverage: decimal.Decimal | None = None
    listing_exchange: str | None = None
    tick_ladder: tuple[TickStep, ...] = ()
    price_limit_pct: decimal.Decimal | None = None
    board_lot: decimal.Decimal | None = None
    name: str | None = None
    local_name: str | None = None

    @property
    def calendar(self) -> str:
        """Returns the session calendar id."""
        return calendar_code(
            self.instrument_id.venue, self.instrument_id.market_type
        )

    def tick_for(self, price: decimal.Decimal) -> decimal.Decimal:
        """Returns the tick size that applies at a price.

        Args:
            price: Price.

        Returns:
            The ladder tick at ``price``, else ``tick_size``.
        """
        tick = self.tick_size
        for step in sorted(self.tick_ladder):
            if price >= step.min_price:
                tick = step.tick_size
        return tick

    @property
    def instrument_id(self) -> InstrumentId:
        """Returns the canonical identifier."""
        return self.key.instrument_id

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Instrument`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "key": self.key.to_contract(),
            "display_symbol": self.display_symbol,
            "tick_size": decimals.format_decimal(self.tick_size),
            "lot_size": decimals.format_decimal(self.lot_size),
            "min_qty": decimals.format_decimal(self.min_qty),
            "max_qty": decimals.format_optional(self.max_qty),
            "min_notional": decimals.format_optional(self.min_notional),
            "contract_multiplier": decimals.format_decimal(
                self.contract_multiplier
            ),
            "linear": self.linear,
            "status": self.status.value,
            "funding_interval_hours": self.funding_interval_hours,
            "max_leverage": decimals.format_optional(self.max_leverage),
            "asset_class": self.asset_class.value,
            "source": self.source,
            "as_of_ms": self.as_of_ms,
            "calendar": self.calendar,
            "listing_exchange": self.listing_exchange,
            "tick_ladder": [step.to_contract() for step in self.tick_ladder],
            "price_limit_pct": decimals.format_optional(self.price_limit_pct),
            "board_lot": decimals.format_optional(self.board_lot),
            "name": self.name,
            "local_name": self.local_name,
        }
