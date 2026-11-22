"""Inputs of a risk evaluation, gathered by the OMS before checks run."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Sequence

from joseph_execution.domain import accounts
from joseph_execution.domain import deployments
from joseph_execution.domain import enums
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
        now_ms: Evaluation time.
        limits: Effective account limits.
        live_trading_enabled: Global LIVE flag.
        emergency_halt: Platform-wide halt flag.
        account: Target account (``None`` if missing).
        capabilities: Negotiated adapter capabilities.
        adapter_health: Adapter/private stream health.
        instrument: Instrument metadata.
        quote: Latest quote.
        book: Order book.
        position: Current position of the instrument in this mode.
        open_position_count: Open positions of the account (this mode).
        gross_notional: Gross notional of the account's positions.
        equity: Account equity.
        available_margin: Free margin after reservations.
        daily_realized_pnl: Realized PnL since UTC midnight.
        account_state_unknown: Unknown orders or position mismatch exist.
        kill_switches: Kill switches of the tenant.
        deployment: Deployment for STRATEGY intents.
        signal_age_ms: Age of the originating signal.
        signal_intents: Intents already created for the signal.
        strategy_open_orders: Working opening orders of the same strategy
            and instrument.
        recent_rejects: Venue rejects inside the spike window.
        notional: Order notional at the reference price.
        margin_required: Margin needed by the order.
        slippage_pct: Estimated market-order slippage.
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

    @property
    def risk_reducing(self) -> bool:
        """Whether the order can only reduce the current position.

        True only for reduce-only orders on the side opposite to an existing
        position with quantity not exceeding it.
        """
        req = self.request
        if not req.reduce_only or self.position is None:
            return False
        qty = self.position.signed_qty
        if qty == 0:
            return False
        opposite = enums.OrderSide.SELL if qty > 0 else enums.OrderSide.BUY
        return req.side is opposite and req.quantity_base <= abs(qty)
