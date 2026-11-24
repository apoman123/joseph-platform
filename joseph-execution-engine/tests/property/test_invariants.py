# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Property-based tests of OMS, position and rounding invariants."""

from __future__ import annotations

import asyncio
import decimal

import hypothesis
from hypothesis import strategies

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.domain import orders
from joseph_execution.domain import positions
from joseph_execution.testing import harness

D = decimal.Decimal
S = enums.OrderState
_SETTINGS = hypothesis.settings(max_examples=60, deadline=None)

_qty = strategies.decimals(min_value=D("0.001"), max_value=D(5), places=3)
_price = strategies.decimals(min_value=D(1), max_value=D(200000), places=1)


@_SETTINGS
@hypothesis.given(
    strategies.lists(
        strategies.one_of(
            strategies.tuples(strategies.just("fill"), _qty),
            strategies.tuples(
                strategies.just("state"), strategies.sampled_from(list(S))
            ),
        ),
        max_size=30,
    )
)
def test_order_invariants_under_random_operations(
    operations: list[tuple[str, object]],
) -> None:
    """Filled <= qty, terminal states absorb, versions never decrease."""
    order = orders.Order(
        order_id="o",
        intent_id="i",
        tenant_id="t",
        account_id="a",
        instrument_id="mock:perp:BTCUSD",
        client_order_id="c",
        mode=enums.OrderMode.PAPER,
        role=enums.OrderRole.PRIMARY,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        time_in_force=enums.TimeInForce.GTC,
        quantity_base=D(5),
        reduce_only=False,
        state=S.ACKNOWLEDGED,
        created_at_ms=0,
        updated_at_ms=0,
        limit_price=D(100),
    )
    version = order.version
    terminal_seen: enums.OrderState | None = None
    for n, (kind, value) in enumerate(operations):
        try:
            if kind == "fill":
                assert isinstance(value, decimal.Decimal)
                order.apply_fill(
                    orders.Fill(
                        f"f{n}",
                        "o",
                        "a",
                        "mock:perp:BTCUSD",
                        f"x{n}",
                        enums.OrderSide.BUY,
                        D(100),
                        value,
                        D(0),
                        "USD",
                        "maker",
                        n,
                        enums.OrderMode.PAPER,
                    ),
                    n,
                )
            else:
                assert isinstance(value, enums.OrderState)
                order.transition(value, n)
        except errors.ExecutionError:
            pass
        assert order.filled_base <= order.quantity_base
        assert order.version >= version
        version = order.version
        if terminal_seen is not None:
            assert order.state is terminal_seen
        elif order.state.is_terminal:
            terminal_seen = order.state


@_SETTINGS
@hypothesis.given(
    strategies.lists(
        strategies.tuples(
            strategies.sampled_from(list(enums.OrderSide)), _qty, _price
        ),
        min_size=1,
        max_size=25,
    )
)
def test_position_quantity_equals_signed_fill_sum(
    fills: list[tuple[enums.OrderSide, decimal.Decimal, decimal.Decimal]],
) -> None:
    """Net position equals the signed sum of fills; entry is positive."""
    pos = positions.Position("p", "t", "a", "x", enums.OrderMode.PAPER)
    expected = D(0)
    for n, (side, qty, price) in enumerate(fills):
        pos.apply_fill(side, qty, price, D(0), n)
        expected += qty if side is enums.OrderSide.BUY else -qty
        assert pos.signed_qty == expected
        if pos.signed_qty == 0:
            assert pos.entry_price is None
        else:
            assert pos.entry_price is not None and pos.entry_price > 0


@_SETTINGS
@hypothesis.given(
    strategies.decimals(min_value=D("0.0001"), max_value=D(1000), places=6),
    strategies.sampled_from([D("0.001"), D("0.01"), D("0.1"), D(1)]),
)
def test_rounding_never_increases_quantity(
    value: decimal.Decimal, step: decimal.Decimal
) -> None:
    """Lot flooring never increases size; tick rounding stays within half."""
    floored = decimals.floor_to_step(value, step)
    assert floored <= value
    assert value - floored < step
    assert decimals.is_multiple(floored, step)
    rounded = decimals.round_to_step(value, step)
    assert abs(rounded - value) <= step / 2


@hypothesis.settings(max_examples=15, deadline=None)
@hypothesis.given(
    strategies.lists(
        strategies.tuples(
            strategies.sampled_from(["market", "limit", "cancel", "tick"]),
            strategies.sampled_from(["buy", "sell"]),
            strategies.integers(min_value=1, max_value=5),
            strategies.integers(min_value=-300, max_value=300),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_oms_random_flows_keep_reservations_and_positions_consistent(
    steps: list[tuple[str, str, int, int]],
) -> None:
    """Random flows: reservations >= 0, positions match fills and venue."""
    asyncio.run(_run_flow(steps))


async def _run_flow(steps: list[tuple[str, str, int, int]]) -> None:
    """Executes a random OMS flow and checks invariants after each step.

    Args:
        steps: ``(action, side, size, mark_move)`` tuples.
    """
    h = harness.build()
    oms = h.container.services.oms
    a = harness.TENANT_A
    for n, (action, side, size, move) in enumerate(steps):
        qty = f"0.00{size}"
        if action == "market":
            body = harness.intent_body(side=side, quantity_base=qty)
        elif action == "limit":
            price = "94000" if side == "buy" else "96000"
            body = harness.intent_body(
                side=side,
                quantity_base=qty,
                order_type="limit",
                limit_price=price,
                time_in_force="GTC",
            )
        elif action == "cancel":
            working = [o for o in oms.list_orders(a) if o.state.is_working]
            if working:
                await oms.cancel_order(a, working[0].order_id)
            continue
        else:
            h.set_mark(harness.BTC, str(95000 + move * 10))
            await h.pump()
            continue
        await oms.submit_intent(
            a, intents.OrderIntentRequest.from_contract(body), f"k{n}"
        )
        with h.container.uow_factory() as uow:
            assert uow.reservations.active_total(harness.PAPER_A) >= 0
            for order in uow.orders.list(tenant_id="tenant-a"):
                assert order.filled_base <= order.quantity_base
                if order.state.is_terminal:
                    assert (
                        uow.reservations.active_for_order(order.order_id)
                        is None
                    )
            fills = uow.fills.list("tenant-a")
            net = sum(
                (
                    f.quantity_base
                    if f.side is enums.OrderSide.BUY
                    else -f.quantity_base
                    for f in fills
                ),
                D(0),
            )
            local = uow.positions.get(
                harness.PAPER_A, harness.BTC, enums.OrderMode.PAPER
            )
        assert (D(0) if local is None else local.signed_qty) == net
    venue = await h.container.paper.list_positions(harness.PAPER_A)
    venue_qty = venue[0].signed_qty if venue else D(0)
    with h.container.uow_factory() as uow:
        local = uow.positions.get(
            harness.PAPER_A, harness.BTC, enums.OrderMode.PAPER
        )
    assert (D(0) if local is None else local.signed_qty) == venue_qty
