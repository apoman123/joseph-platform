# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the order aggregate and its state machine."""

from __future__ import annotations

import decimal
import itertools
from collections.abc import Callable
from typing import TypeAlias

import pytest

from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import orders

S = enums.OrderState
D = decimal.Decimal
_Validate: TypeAlias = Callable[[str, str, object], None]


def _order(
    state: enums.OrderState = S.ACKNOWLEDGED, qty: str = "1"
) -> orders.Order:
    """Builds a paper limit buy of the mock BTC perp at 100.

    Args:
        state: Initial state.
        qty: Base quantity, as a decimal string.

    Returns:
        Order ``o1`` (client id ``c1``) in ``state``, version 0.
    """
    return orders.Order(
        order_id="o1",
        intent_id="i1",
        tenant_id="t",
        account_id="a",
        instrument_id="mock:perp:BTCUSD",
        client_order_id="c1",
        mode=enums.OrderMode.PAPER,
        role=enums.OrderRole.PRIMARY,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        time_in_force=enums.TimeInForce.GTC,
        quantity_base=D(qty),
        reduce_only=False,
        state=state,
        created_at_ms=1,
        updated_at_ms=1,
        limit_price=D(100),
    )


def _fill(fid: str, qty: str, price: str = "100") -> orders.Fill:
    """Builds a maker buy fill of order ``o1`` without fees.

    Args:
        fid: Venue fill id; the fill id is ``f-<fid>``.
        qty: Base quantity, as a decimal string.
        price: Fill price, as a decimal string.

    Returns:
        The fill.
    """
    return orders.Fill(
        fill_id=f"f-{fid}",
        order_id="o1",
        account_id="a",
        instrument_id="mock:perp:BTCUSD",
        exchange_fill_id=fid,
        side=enums.OrderSide.BUY,
        price=D(price),
        quantity_base=D(qty),
        fee=D(0),
        fee_asset="USD",
        liquidity="maker",
        ts_ms=2,
        mode=enums.OrderMode.PAPER,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    list(itertools.product(S, S)),
    ids=lambda s: s.value,
)
def test_transition_table_is_exhaustive(
    current: enums.OrderState, target: enums.OrderState
) -> None:
    """Table transitions succeed; any other raises and keeps the state."""
    order = _order(current)
    allowed = target in orders.TRANSITIONS[current]
    if allowed:
        order.transition(target, 5)
        assert order.state is target
    else:
        with pytest.raises(errors.InvalidTransitionError):
            order.transition(target, 5)
        assert order.state is current


def test_terminal_states_are_absorbing() -> None:
    """Terminal states have no outgoing transitions."""
    for state in enums.TERMINAL_STATES:
        assert orders.TRANSITIONS[state] == frozenset()
        assert state.is_terminal


def test_every_state_has_a_row() -> None:
    """The transition table covers every state."""
    assert set(orders.TRANSITIONS) == set(S)


def test_version_increments_on_each_mutation() -> None:
    """Each transition increments the order version by one."""
    order = _order()
    order.transition(S.PARTIALLY_FILLED, 2)
    order.transition(S.CANCEL_PENDING, 3)
    assert order.version == 2


def test_fill_is_idempotent_by_venue_fill_id() -> None:
    """Applying the same venue fill twice changes nothing."""
    order = _order(qty="2")
    assert order.apply_fill(_fill("x", "1"), 3)
    assert not order.apply_fill(_fill("x", "1"), 4)
    assert order.filled_base == D(1)
    assert order.state is S.PARTIALLY_FILLED


def test_fills_complete_order_with_weighted_average() -> None:
    """Fills accumulate to FILLED with a volume-weighted price."""
    order = _order(qty="2")
    order.apply_fill(_fill("a", "1", "100"), 3)
    order.apply_fill(_fill("b", "1", "110"), 4)
    assert order.state is S.FILLED
    assert order.avg_fill_price == D(105)


def test_overfill_is_rejected() -> None:
    """An overfill raises CONFLICT and leaves the filled quantity at zero."""
    order = _order(qty="1")
    with pytest.raises(errors.ExecutionError) as info:
        order.apply_fill(_fill("a", "1.5"), 3)
    assert info.value.code == "CONFLICT"
    assert order.filled_base == 0


def test_fill_during_cancel_pending_stays_pending() -> None:
    """A partial fill keeps CANCEL_PENDING; the completing fill sets FILLED."""
    order = _order(S.CANCEL_PENDING, qty="2")
    order.apply_fill(_fill("a", "1"), 3)
    assert order.state is S.CANCEL_PENDING
    order.apply_fill(_fill("b", "1"), 4)
    assert order.state is S.FILLED


def test_late_fill_on_terminal_order_keeps_state() -> None:
    """Venue truth is recorded without leaving a terminal state."""
    order = _order(S.CANCELED, qty="2")
    assert order.apply_fill(_fill("a", "1"), 3)
    assert order.state is S.CANCELED
    assert order.filled_base == D(1)
    assert order.state_reason


def test_amend_requires_working_order_and_quantity_above_filled() -> None:
    """Amendments only apply to working orders and never undercut fills."""
    order = _order(qty="2")
    order.apply_fill(_fill("a", "1"), 3)
    with pytest.raises(errors.ExecutionError):
        order.amend(4, quantity_base=D(1))
    order.amend(4, limit_price=D(99), quantity_base=D(3))
    assert order.limit_price == D(99)
    assert order.quantity_base == D(3)
    with pytest.raises(errors.ExecutionError):
        _order(S.FILLED).amend(5, limit_price=D(1))


def test_acknowledge_sets_exchange_id() -> None:
    """Acknowledgement moves SUBMITTING to ACKNOWLEDGED with the venue id."""
    order = _order(S.SUBMITTING)
    order.acknowledge("x-1", 3)
    assert order.state is S.ACKNOWLEDGED
    assert order.exchange_order_id == "x-1"


def test_contract_shape(contract_validate: _Validate) -> None:
    """Orders and fills serialize to valid contract objects."""
    order = _order()
    order.apply_fill(_fill("a", "0.5"), 3)
    contract_validate(
        "execution.schema.json",
        "Order",
        order.to_contract(),
    )
    contract_validate(
        "execution.schema.json",
        "Fill",
        _fill("a", "0.5").to_contract(),
    )
