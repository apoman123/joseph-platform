# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Enumerations mirroring ``execution.schema.json`` and ``common``.

Member values are wire values of the contracts and must not change.
"""

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
    BYBIT = "bybit"
    OKX = "okx"
    COINBASE = "coinbase"
    KRAKEN = "kraken"
    DERIV = "deriv"
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
    """Securities brokers (contract ``Broker``).

    The values double as the ``venue`` of broker accounts.
    """

    IBKR = "ibkr"
    SINOPAC = "sinopac"
    CATHAY = "cathay"


class OrderSide(enum.StrEnum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> OrderSide:
        """The other side: ``SELL`` for ``BUY`` and vice versa."""
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

    ``DAY`` (TWSE ``ROD``, rest of day) expires at the close of the
    exchange session and applies to exchange-listed securities only.
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
    """Trigger price basis: the mark price or the last trade price."""

    MARK = "mark"
    LAST = "last"


class IntentSource(enum.StrEnum):
    """Where an intent originated.

    ``EMERGENCY`` intents are the flattening orders of a kill switch.
    """

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
    """Order mode: whether an order reaches a real venue.

    ``PAPER`` orders fill in the paper simulator and ``SHADOW`` orders are
    only recorded; neither sends anything to a venue. ``LIVE`` sends real
    orders and is opt-in: the Risk Gateway's ``mode.allowed`` check fails
    it unless the account lists ``LIVE`` in its modes, the global
    live-trading gate (off by default) is on and the venue adapter reports
    ``AVAILABLE``. Strategy orders also need an enabled ``AUTO_LIVE``
    deployment with valid consent, and manual orders an armed account
    (risk-reducing ones are let through with a warning).
    """

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class OrderState(enum.StrEnum):
    """OMS (order management system) order states (contract ``OrderState``).

    The permitted transitions are ``orders.TRANSITIONS``.
    """

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
        """Whether the state is absorbing (no transition leaves it)."""
        return self in TERMINAL_STATES

    @property
    def is_working(self) -> bool:
        """Whether the order may rest or fill at the venue."""
        return self in (
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.CANCEL_PENDING,
        )


# Final outcomes; ``UNKNOWN_RECONCILING`` is not one: reconciliation still
# resolves it.
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

    PASS = "PASS"  # noqa: S105 - outcome enum value, not a password.
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    UNKNOWN = "UNKNOWN"


class Decision(enum.StrEnum):
    """Risk Gateway verdict; only ``ALLOW`` authorizes an order.

    ``REJECT``: a check found the order itself invalid. ``PAUSE``: a check
    that pauses trading failed. ``BLOCK``: any other check failed or could
    not decide (fail closed). A decision carries the most severe verdict,
    ranked ALLOW < REJECT < PAUSE < BLOCK.
    """

    ALLOW = "ALLOW"
    REJECT = "REJECT"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"


class KillSwitchScope(enum.StrEnum):
    """Kill switch scope (``GLOBAL`` covers every account and strategy).

    Scopes never cross tenants: ``GLOBAL`` means the whole tenant.
    """

    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"


class DeploymentMode(enum.StrEnum):
    """Strategy deployment mode.

    ``PAPER`` and ``SHADOW`` deployments are enabled on creation and send
    PAPER and SHADOW intents. ``AUTO_LIVE`` sends LIVE intents and is
    opt-in: it starts ``PENDING_CONSENT``, is enabled only by an expiring,
    step-up-verified consent, and every one of its orders still has to pass
    the LIVE gates of ``OrderMode``.
    """

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    AUTO_LIVE = "AUTO_LIVE"

    @property
    def order_mode(self) -> OrderMode:
        """The order mode of this deployment's intents (AUTO_LIVE: LIVE)."""
        if self is DeploymentMode.PAPER:
            return OrderMode.PAPER
        if self is DeploymentMode.SHADOW:
            return OrderMode.SHADOW
        return OrderMode.LIVE


class DeploymentState(enum.StrEnum):
    """Deployment lifecycle state.

    Only ``ENABLED`` deployments trade. ``PENDING_CONSENT`` is an
    ``AUTO_LIVE`` deployment awaiting consent; ``DISABLED`` was switched off
    by the user (its consent dropped). ``REVOKED`` is reserved: the
    ``Deployment`` contract lists it, but deployments have no revoke
    operation, so no engine path sets it. The engine still treats it like
    ``DISABLED``: such a deployment never trades and cannot be given
    consent.
    """

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
    """Market-data provenance (contract ``FeedState``).

    LIVE orders that add exposure need ``LIVE`` data (Risk Gateway check
    ``data.quote_fresh``).
    """

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
