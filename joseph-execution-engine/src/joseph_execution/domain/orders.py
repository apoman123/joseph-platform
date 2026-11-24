# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Order aggregate with an explicit state machine.

Transitions not listed in ``TRANSITIONS`` raise ``InvalidTransitionError``.
Terminal states are absorbing. Fills are idempotent by venue fill id and the
filled quantity can never exceed the order quantity.
"""

from __future__ import annotations

import dataclasses
import decimal

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors

S = enums.OrderState

TRANSITIONS: dict[enums.OrderState, frozenset[enums.OrderState]] = {
    S.DRAFT: frozenset({S.VALIDATED, S.REJECTED}),
    S.VALIDATED: frozenset({S.RESERVED, S.REJECTED}),
    S.RESERVED: frozenset({S.SUBMITTING, S.CANCELED, S.FAILED}),
    S.SUBMITTING: frozenset(
        {
            S.ACKNOWLEDGED,
            S.PARTIALLY_FILLED,
            S.FILLED,
            S.REJECTED,
            S.FAILED,
            S.UNKNOWN_RECONCILING,
            S.CANCELED,
            S.EXPIRED,
        }
    ),
    S.UNKNOWN_RECONCILING: frozenset(
        {
            S.SUBMITTING,
            S.ACKNOWLEDGED,
            S.PARTIALLY_FILLED,
            S.FILLED,
            S.REJECTED,
            S.CANCELED,
            S.EXPIRED,
            S.FAILED,
        }
    ),
    S.ACKNOWLEDGED: frozenset(
        {
            S.PARTIALLY_FILLED,
            S.FILLED,
            S.CANCEL_PENDING,
            S.CANCELED,
            S.EXPIRED,
            S.REJECTED,
        }
    ),
    S.PARTIALLY_FILLED: frozenset(
        {S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_PENDING, S.CANCELED, S.EXPIRED}
    ),
    S.CANCEL_PENDING: frozenset(
        {
            S.CANCEL_PENDING,
            S.CANCELED,
            S.FILLED,
            S.ACKNOWLEDGED,
            S.PARTIALLY_FILLED,
            S.EXPIRED,
        }
    ),
    S.FILLED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELED: frozenset(),
    S.EXPIRED: frozenset(),
    S.FAILED: frozenset(),
}


def can_transition(current: enums.OrderState, target: enums.OrderState) -> bool:
    """Checks whether a transition is legal.

    Args:
        current: Current state.
        target: Requested state.

    Returns:
        True when ``target`` is reachable in one step.
    """
    return target in TRANSITIONS[current]


@dataclasses.dataclass(frozen=True, slots=True)
class Fill:
    """Execution of (part of) an order.

    Attributes:
        fill_id: Joseph fill id.
        order_id: Order filled.
        account_id: Account.
        instrument_id: Instrument.
        exchange_fill_id: Venue fill id (idempotency key per account).
        side: Order side.
        price: Fill price.
        quantity_base: Filled base quantity.
        fee: Fee paid (positive) or rebate (negative) in quote currency.
        fee_asset: Fee currency.
        liquidity: ``maker`` or ``taker``.
        ts_ms: Venue fill time.
        mode: Order mode.
        realized_pnl: PnL realized by this fill, net of its fee.
    """

    fill_id: str
    order_id: str
    account_id: str
    instrument_id: str
    exchange_fill_id: str
    side: enums.OrderSide
    price: decimal.Decimal
    quantity_base: decimal.Decimal
    fee: decimal.Decimal
    fee_asset: str
    liquidity: str
    ts_ms: int
    mode: enums.OrderMode
    realized_pnl: decimal.Decimal = decimals.ZERO

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Fill``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "exchange_fill_id": self.exchange_fill_id,
            "side": self.side.value,
            "price": decimals.fmt(self.price),
            "quantity_base": decimals.fmt(self.quantity_base),
            "fee": decimals.fmt(self.fee),
            "fee_asset": self.fee_asset,
            "liquidity": self.liquidity,
            "ts_ms": self.ts_ms,
            "mode": self.mode.value,
        }


@dataclasses.dataclass(slots=True)
class Order:
    """Mutable order aggregate; mutate only through its methods.

    Attributes:
        order_id: Joseph order id.
        intent_id: Originating intent.
        tenant_id: Owning tenant.
        account_id: Account.
        instrument_id: Instrument.
        client_order_id: Venue client order id (unique per account).
        mode: Order mode.
        role: Role within the intent.
        side: Side.
        order_type: Type.
        time_in_force: Time in force.
        quantity_base: Order quantity.
        reduce_only: Reduce-only flag.
        state: Current state.
        created_at_ms: Creation time.
        updated_at_ms: Last update time.
        version: Optimistic concurrency version (increments on mutation).
        filled_base: Cumulative filled quantity.
        avg_fill_price: Volume-weighted fill price.
        limit_price: Limit price.
        trigger_price: Trigger price.
        trigger_basis: Trigger basis.
        exchange_order_id: Venue order id once acknowledged.
        parent_order_id: Parent for bracket children.
        state_reason: Explanation of the current state.
        strategy_version_id: Strategy version for strategy orders.
        signal_id: Originating signal.
        submit_attempts: Number of venue submissions.
        manual_intervention: True when automatic recovery stopped.
        fill_ids: Venue fill ids already applied.
    """

    order_id: str
    intent_id: str
    tenant_id: str
    account_id: str
    instrument_id: str
    client_order_id: str
    mode: enums.OrderMode
    role: enums.OrderRole
    side: enums.OrderSide
    order_type: enums.OrderType
    time_in_force: enums.TimeInForce
    quantity_base: decimal.Decimal
    reduce_only: bool
    state: enums.OrderState
    created_at_ms: int
    updated_at_ms: int
    version: int = 0
    filled_base: decimal.Decimal = decimals.ZERO
    avg_fill_price: decimal.Decimal | None = None
    limit_price: decimal.Decimal | None = None
    trigger_price: decimal.Decimal | None = None
    trigger_basis: enums.PriceBasis | None = None
    exchange_order_id: str | None = None
    parent_order_id: str | None = None
    state_reason: str | None = None
    strategy_version_id: str | None = None
    signal_id: str | None = None
    submit_attempts: int = 0
    manual_intervention: bool = False
    fill_ids: set[str] = dataclasses.field(default_factory=set)

    @property
    def remaining_base(self) -> decimal.Decimal:
        """Returns the unfilled quantity."""
        return self.quantity_base - self.filled_base

    def transition(
        self, target: enums.OrderState, now_ms: int, reason: str | None = None
    ) -> None:
        """Moves to ``target`` if the state machine allows it.

        Args:
            target: Requested state.
            now_ms: Mutation time.
            reason: Optional explanation stored as ``state_reason``.

        Raises:
            InvalidTransitionError: If the transition is illegal.
        """
        if not can_transition(self.state, target):
            raise errors.InvalidTransitionError(self.state.value, target.value)
        self.state = target
        if reason is not None:
            self.state_reason = reason
        self._touch(now_ms)

    def apply_fill(self, fill: Fill, now_ms: int) -> bool:
        """Applies a fill idempotently and advances the fill state.

        Fills on terminal orders are recorded (venue truth) without changing
        the terminal state.

        Args:
            fill: Venue fill.
            now_ms: Mutation time.

        Returns:
            True if the fill was new, False for a duplicate.

        Raises:
            ExecutionError: If the fill would exceed the order quantity.
        """
        if fill.exchange_fill_id in self.fill_ids:
            return False
        if fill.quantity_base <= decimals.ZERO:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", "fill quantity must be positive"
            )
        new_filled = self.filled_base + fill.quantity_base
        if new_filled > self.quantity_base:
            raise errors.ExecutionError(
                "CONFLICT",
                f"overfill on {self.order_id}: {new_filled} > "
                f"{self.quantity_base}",
            )
        previous_cost = (
            self.avg_fill_price or decimals.ZERO
        ) * self.filled_base
        self.avg_fill_price = (
            previous_cost + fill.price * fill.quantity_base
        ) / new_filled
        self.filled_base = new_filled
        self.fill_ids.add(fill.exchange_fill_id)
        if not self.state.is_terminal:
            if new_filled == self.quantity_base:
                target = S.FILLED
            elif self.state is S.CANCEL_PENDING:
                target = S.CANCEL_PENDING
            else:
                target = S.PARTIALLY_FILLED
            self.transition(target, now_ms)
        else:
            self.state_reason = "fill received after terminal venue report"
            self._touch(now_ms)
        return True

    def acknowledge(self, exchange_order_id: str | None, now_ms: int) -> None:
        """Records a venue acknowledgement when still pre-ack.

        Args:
            exchange_order_id: Venue order id.
            now_ms: Mutation time.
        """
        if exchange_order_id:
            self.exchange_order_id = exchange_order_id
        if self.state in (S.SUBMITTING, S.UNKNOWN_RECONCILING):
            self.transition(S.ACKNOWLEDGED, now_ms)
        else:
            self._touch(now_ms)

    def amend(
        self,
        now_ms: int,
        limit_price: decimal.Decimal | None = None,
        trigger_price: decimal.Decimal | None = None,
        quantity_base: decimal.Decimal | None = None,
    ) -> None:
        """Applies a venue-confirmed amendment of a working order.

        Args:
            now_ms: Mutation time.
            limit_price: New limit price.
            trigger_price: New trigger price.
            quantity_base: New total quantity (>= filled quantity).

        Raises:
            ExecutionError: If the order is not working or the quantity
                would fall below the filled quantity.
        """
        if not self.state.is_working:
            raise errors.ExecutionError(
                "CONFLICT", f"cannot amend an order in {self.state.value}"
            )
        if quantity_base is not None:
            if quantity_base <= self.filled_base:
                raise errors.ExecutionError(
                    "VALIDATION_FAILED",
                    "amended quantity must exceed the filled quantity",
                )
            self.quantity_base = quantity_base
        if limit_price is not None:
            self.limit_price = limit_price
        if trigger_price is not None:
            self.trigger_price = trigger_price
        self._touch(now_ms)

    def _touch(self, now_ms: int) -> None:
        """Bumps the version and update time.

        Args:
            now_ms: Mutation time.
        """
        self.version += 1
        self.updated_at_ms = max(self.updated_at_ms, now_ms)

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Order``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "order_id": self.order_id,
            "intent_id": self.intent_id,
            "tenant_id": self.tenant_id,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "client_order_id": self.client_order_id,
            "exchange_order_id": self.exchange_order_id,
            "parent_order_id": self.parent_order_id,
            "mode": self.mode.value,
            "role": self.role.value,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "quantity_base": decimals.fmt(self.quantity_base),
            "filled_base": decimals.fmt(self.filled_base),
            "avg_fill_price": decimals.fmt_optional(self.avg_fill_price),
            "limit_price": decimals.fmt_optional(self.limit_price),
            "trigger_price": decimals.fmt_optional(self.trigger_price),
            "reduce_only": self.reduce_only,
            "state": self.state.value,
            "state_reason": self.state_reason,
            "strategy_version_id": self.strategy_version_id,
            "signal_id": self.signal_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "version": self.version,
        }
