# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Inputs of a risk evaluation, gathered by the OMS before checks run.

OMS: the order management system (``oms.service``).
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Sequence

from joseph_execution.domain import accounts
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import identity
from joseph_execution.domain import intents
from joseph_execution.domain import kill_switch
from joseph_execution.domain import market
from joseph_execution.domain import positions
from joseph_execution.risk import limits


@dataclasses.dataclass(frozen=True, slots=True)
class RiskContext:
    """Snapshot of everything the checks may consult.

    ``None`` means the value is unknown; checks must then report UNKNOWN
    rather than assume a safe default.

    Attributes:
        request: Normalized intent request.
        principal: Caller.
        now_ms: Evaluation time, UTC epoch milliseconds.
        limits: Effective account limits (stored values clamped to the
            platform hard caps, in the account currency).
        live_trading_enabled: Deployment-wide LIVE flag
            (``JOSEPH_LIVE_TRADING_ENABLED``).
        emergency_halt: Platform-wide halt flag (``JOSEPH_EMERGENCY_HALT``).
        account: Target account (``None`` if missing).
        capabilities: Negotiated adapter capabilities (``None`` when the
            adapter could not report them).
        adapter_health: Adapter/private stream health (``None`` when the
            adapter could not report it).
        instrument: Instrument metadata.
        quote: Latest quote.
        book: Order book.
        position: Current position of the instrument in this mode.
        open_position_count: Positions of the account in this mode with a
            non-zero quantity.
        gross_notional: Gross notional of the account's positions at marks.
        equity: Account equity.
        available_margin: Free margin after positions and reservations.
        daily_realized_pnl: Realized PnL since 00:00 UTC (negative is a
            loss).
        account_state_unknown: Orders in UNKNOWN_RECONCILING or positions
            flagged unknown exist on the account.
        kill_switches: Kill switches of the tenant, engaged or not.
        deployment: Deployment for STRATEGY intents.
        signal_age_ms: Age of the originating signal in milliseconds.
        signal_intents: Intents already created for the signal.
        strategy_open_orders: Working opening (not reduce-only) orders of
            the same strategy version and instrument.
        recent_rejects: Venue rejects inside ``limits.reject_window_ms``,
            counted up to ``limits.reject_spike_threshold + 1``.
        notional: Order notional at the reference price (quantity x price
            x contract multiplier).
        margin_required: Margin needed by the order.
        slippage_pct: Estimated market-order slippage in percent of the
            best price (``None`` when the book is too thin).
        sessions: Exchange sessions around ``now_ms`` for securities
            (``None`` when unknown or not gathered).
        previous_close: Reference price of daily limits (securities).
        cash: Cash buying power of the account (securities).
        estimated_fee: Estimated fee of the order.
        queue_closed_market_orders: Configuration allows DAY market
            orders to queue for the next open while the exchange is
            closed.
        live_arming: Current LIVE arming of the account (manual LIVE
            orders need a valid one), ``None`` when never armed.
    """

    request: intents.OrderIntentRequest
    principal: identity.Principal
    now_ms: int
    limits: limits.RiskLimits
    live_trading_enabled: bool
    emergency_halt: bool
    account: accounts.Account | None
    capabilities: accounts.TradingCapabilities | None
    adapter_health: enums.HealthState | None
    instrument: market.InstrumentMeta | None
    quote: market.MarketQuote | None
    book: market.BookView | None
    position: positions.Position | None
    open_position_count: int
    gross_notional: decimal.Decimal | None
    equity: decimal.Decimal | None
    available_margin: decimal.Decimal | None
    daily_realized_pnl: decimal.Decimal | None
    account_state_unknown: bool
    kill_switches: Sequence[kill_switch.KillSwitch]
    deployment: deployments.Deployment | None
    signal_age_ms: int | None
    signal_intents: int
    strategy_open_orders: int
    recent_rejects: int
    notional: decimal.Decimal | None
    margin_required: decimal.Decimal | None
    slippage_pct: decimal.Decimal | None
    sessions: tuple[market.TradingSession, ...] | None = None
    previous_close: decimal.Decimal | None = None
    cash: equities.CashBalance | None = None
    estimated_fee: decimal.Decimal | None = None
    queue_closed_market_orders: bool = False
    live_arming: accounts.LiveArming | None = None

    @property
    def security(self) -> bool:
        """Whether the order is for an exchange-listed security."""
        return equities.is_security(self.request.instrument_id)

    @property
    def long_qty(self) -> decimal.Decimal:
        """The long base quantity held (0 when flat, short or unknown)."""
        if self.position is None or self.position.signed_qty <= 0:
            return decimal.Decimal(0)
        return self.position.signed_qty

    @property
    def risk_reducing(self) -> bool:
        """Whether the order can only reduce the current position.

        True only for a reduce-only order on the side opposite to a known,
        non-flat position, for at most the position's quantity. Several
        checks let such orders through while new exposure is blocked.
        """
        req = self.request
        if not req.reduce_only or self.position is None:
            return False
        qty = self.position.signed_qty
        if qty == 0:
            return False
        opposite = enums.OrderSide.SELL if qty > 0 else enums.OrderSide.BUY
        return req.side is opposite and req.quantity_base <= abs(qty)
