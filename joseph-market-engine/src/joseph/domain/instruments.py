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
    MOCK = "mock"


class MarketType(enum.StrEnum):
    """Product type at a venue (contract ``MarketType``)."""

    PERP = "perp"
    SPOT = "spot"
    FUTURE = "future"


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
    UNKNOWN = "UNKNOWN"


_ID_PATTERN = re.compile(
    r"^(binance|hyperliquid|lighter|bitget|mexc|mock):(perp|spot|future):"
    r"([A-Za-z0-9._/-]{1,64})$"
)


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
        }
