# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Enumerations mirroring ``execution.schema.json`` and ``common``."""

from __future__ import annotations

import enum


class Venue(enum.StrEnum):
    """Venue identifiers (contract ``Venue``).

    Crypto venues trade their own instruments; ``twse`` (Taiwan Stock
    Exchange), ``tpex`` (Taipei Exchange mainboard), ``nasdaq``, ``nyse``
    and ``cboe`` (Cboe BZX) are securities exchanges reached through a
    broker; ``treasury`` publishes rates that are never tradable.
    """

    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    LIGHTER = "lighter"
    BITGET = "bitget"
    MEXC = "mexc"
    TWSE = "twse"
    TPEX = "tpex"
    NASDAQ = "nasdaq"
    NYSE = "nyse"
    CBOE = "cboe"
    TREASURY = "treasury"
    MOCK = "mock"


class Broker(enum.StrEnum):
    """Securities brokers (contract ``Broker``); account venues too."""

    IBKR = "ibkr"
    SINOPAC = "sinopac"
    CATHAY = "cathay"


class OrderSide(enum.StrEnum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> OrderSide:
        """Returns the other side."""
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class PositionEffect(enum.StrEnum):
    """Intended effect on the position."""

    OPEN = "open"
    CLOSE = "close"
    REDUCE = "reduce"


class OrderType(enum.StrEnum):
    """Order type."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class TimeInForce(enum.StrEnum):
    """Time in force.

    ``DAY`` (TWSE ``ROD``) expires at the close of the exchange session and
    applies to exchange-listed securities only.
    """

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"
    DAY = "DAY"


class MarginMode(enum.StrEnum):
    """Margin mode."""

    ISOLATED = "isolated"
    CROSS = "cross"


class PriceBasis(enum.StrEnum):
    """Trigger price basis."""

    MARK = "mark"
    LAST = "last"


class IntentSource(enum.StrEnum):
    """Where an intent originated."""

    MANUAL_TICKET = "MANUAL_TICKET"
    CHART = "CHART"
    QUICK_TRADE = "QUICK_TRADE"
    STRATEGY = "STRATEGY"
    EMERGENCY = "EMERGENCY"

    @property
    def is_manual(self) -> bool:
        """Whether a human submitted the intent through the UI."""
        return self in (
            IntentSource.MANUAL_TICKET,
            IntentSource.CHART,
            IntentSource.QUICK_TRADE,
        )


class OrderMode(enum.StrEnum):
    """Order mode. LIVE is disabled by default and fails closed."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class OrderState(enum.StrEnum):
    """OMS order states (contract ``OrderState``)."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN_RECONCILING = "UNKNOWN_RECONCILING"

    @property
    def is_terminal(self) -> bool:
        """Whether the state is absorbing."""
        return self in TERMINAL_STATES

    @property
    def is_working(self) -> bool:
        """Whether the order may rest or fill at the venue."""
        return self in (
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.CANCEL_PENDING,
        )


TERMINAL_STATES = frozenset(
    {
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.FAILED,
    }
)


class OrderRole(enum.StrEnum):
    """Role of an order within an intent."""

    PRIMARY = "PRIMARY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    CLOSE = "CLOSE"


class RiskDomain(enum.StrEnum):
    """Risk check domains (contract ``RiskDomain``)."""

    MODE = "MODE"
    IDENTITY = "IDENTITY"
    ACCOUNT = "ACCOUNT"
    MARKET_STATUS = "MARKET_STATUS"
    DATA_QUALITY = "DATA_QUALITY"
    SIZE = "SIZE"
    EXPOSURE = "EXPOSURE"
    LIQUIDITY = "LIQUIDITY"
    DECISION_QUALITY = "DECISION_QUALITY"
    DUPLICATE = "DUPLICATE"
    CONNECTIVITY = "CONNECTIVITY"
    KILL_SWITCH = "KILL_SWITCH"
    PROTECTION = "PROTECTION"


class CheckOutcome(enum.StrEnum):
    """Outcome of one risk check."""

    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    UNKNOWN = "UNKNOWN"


class Decision(enum.StrEnum):
    """Risk Gateway verdict."""

    ALLOW = "ALLOW"
    REJECT = "REJECT"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"


class KillSwitchScope(enum.StrEnum):
    """Kill switch scope (GLOBAL = every account/strategy of a tenant)."""

    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"


class DeploymentMode(enum.StrEnum):
    """Strategy deployment mode."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    AUTO_LIVE = "AUTO_LIVE"

    @property
    def order_mode(self) -> OrderMode:
        """Returns the order mode used by intents of this deployment."""
        if self is DeploymentMode.PAPER:
            return OrderMode.PAPER
        if self is DeploymentMode.SHADOW:
            return OrderMode.SHADOW
        return OrderMode.LIVE


class DeploymentState(enum.StrEnum):
    """Deployment lifecycle state."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    PENDING_CONSENT = "PENDING_CONSENT"
    REVOKED = "REVOKED"


class TradingStatus(enum.StrEnum):
    """Venue trading availability for an account."""

    AVAILABLE = "AVAILABLE"
    READ_ONLY = "READ_ONLY"
    TRADING_UNAVAILABLE = "TRADING_UNAVAILABLE"


class AccountStatus(enum.StrEnum):
    """Account connection status."""

    CONNECTED = "CONNECTED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    READ_ONLY = "READ_ONLY"
    TRADING_UNAVAILABLE = "TRADING_UNAVAILABLE"
    DISCONNECTED = "DISCONNECTED"


class HealthState(enum.StrEnum):
    """Health of an adapter or feed."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class FeedState(enum.StrEnum):
    """Market-data provenance (contract ``FeedState``)."""

    LIVE = "LIVE"
    POLL = "POLL"
    REPLAY = "REPLAY"
    STALE = "STALE"
    DEMO = "DEMO"
    UNAVAILABLE = "UNAVAILABLE"
    CONNECTING = "CONNECTING"


class InstrumentStatus(enum.StrEnum):
    """Instrument trading status."""

    TRADING = "TRADING"
    HALTED = "HALTED"
    PRE_LAUNCH = "PRE_LAUNCH"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class AssetClass(enum.StrEnum):
    """Asset class of an instrument (contract ``AssetClass``).

    The market engine sets ``BOND`` on TWSE and TPEx bond ETFs, which
    selects their securities transaction tax.
    """

    CRYPTO = "CRYPTO"
    RWA = "RWA"
    EQUITY = "EQUITY"
    BOND = "BOND"
    UNKNOWN = "UNKNOWN"
