"""Integration tests of OMS flows against the paper venue simulator."""

from __future__ import annotations

import decimal

import pytest

from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.testing import harness

D = decimal.Decimal
S = enums.OrderState
A = harness.TENANT_A


def _req(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a domain request from the harness body.

    Args:
        **overrides: Body overrides.

    Returns:
        Request.
    """
    return intents.OrderIntentRequest.from_contract(
        harness.intent_body(**overrides)
    )


def _limit(price: str, **overrides: object) -> intents.OrderIntentRequest:
    """Builds a GTC limit buy.

    Args:
        price: Limit price.
        **overrides: Overrides.

    Returns:
        Request.
    """
    return _req(
        order_type="limit", limit_price=price, time_in_force="GTC", **overrides
    )


async def test_market_order_fills_and_releases_reservation() -> None:
    """A market buy fills, creates a position and releases margin."""
    h = harness.build()
    oms = h.container.services.oms
    result = await oms.submit_intent(A, _req(), "k1")
    assert result.decision.allowed and not result.replayed
    order = result.orders[0]
    assert order.state is S.FILLED and order.exchange_order_id
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_total(harness.PAPER_A) == 0
        fills = uow.fills.list("tenant-a")
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert len(fills) == 1
    assert "intent.created" in actions and "order.submitted" in actions
    positions = await oms.list_positions(A)
    assert positions[0]["quantity_base"] == "0.01"


async def test_limit_order_rests_then_fills_as_maker() -> None:
    """A resting limit keeps its reservation until the venue fills it."""
    h = harness.build()
    oms = h.container.services.oms
    result = await oms.submit_intent(A, _limit("94000"), "k1")
    order = result.orders[0]
    assert order.state is S.ACKNOWLEDGED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) > 0
    h.set_mark(harness.BTC, "93900")
    await h.pump()
    filled = oms.get_order(A, order.order_id)
    assert filled.state is S.FILLED
    assert filled.avg_fill_price == D(94000)
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is None


async def test_partial_fills_keep_reservation_until_terminal() -> None:
    """Partial fills do not release the reservation early."""
    h = harness.build()
    h.container.paper.faults.partial_fill_fraction = D("0.5")
    oms = h.container.services.oms
    order = (
        await oms.submit_intent(A, _limit("94000", quantity_base="0.040"), "k1")
    ).orders[0]
    h.set_mark(harness.BTC, "93000")
    await h.pump()
    partial = oms.get_order(A, order.order_id)
    assert partial.state is S.PARTIALLY_FILLED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is not None
    for _ in range(30):
        await h.pump()
    done = oms.get_order(A, order.order_id)
    assert done.state is S.FILLED and done.filled_base == D("0.04")
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is None
        assert sum(f.quantity_base for f in uow.fills.list("tenant-a")) == (
            D("0.04")
        )


async def test_bracket_children_and_oco() -> None:
    """Fills spawn reduce-only SL/TP; one filling cancels the other."""
    h = harness.build()
    oms = h.container.services.oms
    bracket = {
        "stop_loss": {"trigger": "94000", "basis": "mark"},
        "take_profit": [{"trigger": "96000", "fraction": "1"}],
    }
    result = await oms.submit_intent(A, _req(bracket=bracket), "k1")
    orders = oms.list_orders(A)
    children = [o for o in orders if o.parent_order_id]
    assert {c.role for c in children} == {
        enums.OrderRole.STOP_LOSS,
        enums.OrderRole.TAKE_PROFIT,
    }
    assert all(
        c.reduce_only and c.side is enums.OrderSide.SELL for c in children
    )
    assert all(c.state is S.ACKNOWLEDGED for c in children)
    h.set_mark(harness.BTC, "93950")
    await h.pump()
    states = {o.role: o.state for o in oms.list_orders(A) if o.parent_order_id}
    assert states[enums.OrderRole.STOP_LOSS] is S.FILLED
    assert states[enums.OrderRole.TAKE_PROFIT] is S.CANCELED
    assert await oms.list_positions(A) == []
    assert result.orders[0].role is enums.OrderRole.PRIMARY


async def test_close_never_flips_and_oversized_reduce_is_rejected() -> None:
    """Closes are reduce-only and capped at the position size."""
    h = harness.build()
    oms = h.container.services.oms
    await oms.submit_intent(A, _req(quantity_base="0.020"), "open")
    pos_id = (await oms.list_positions(A))[0]["position_id"]
    half = await oms.close_request(
        A, str(pos_id), D("0.5"), enums.OrderType.MARKET, None
    )
    assert half.quantity_base == D("0.01") and half.reduce_only
    assert half.position_effect is enums.PositionEffect.REDUCE
    too_big = _req(
        side="sell",
        position_effect="close",
        reduce_only=True,
        quantity_base="0.030",
    )
    rejected = await oms.submit_intent(A, too_big, "big")
    assert rejected.decision.decision is enums.Decision.REJECT
    assert rejected.orders == ()
    full = await oms.close_request(
        A, str(pos_id), D(1), enums.OrderType.MARKET, None
    )
    await oms.submit_intent(A, full, "close")
    assert await oms.list_positions(A) == []
    with pytest.raises(errors.ExecutionError) as info:
        await oms.close_request(
            A, str(pos_id), D(1), enums.OrderType.MARKET, None
        )
    assert info.value.code == "CONFLICT"


async def test_full_close_completes_across_partial_fills() -> None:
    """A reduce-only close split into partial fills ends exactly flat."""
    h = harness.build()
    h.container.paper.faults.partial_fill_fraction = D("0.5")
    oms = h.container.services.oms
    await oms.submit_intent(A, _req(quantity_base="0.020"), "open")
    for _ in range(30):
        await h.pump()
    positions = await oms.list_positions(A)
    assert D(str(positions[0]["quantity_base"])) == D("0.02")
    close = await oms.close_request(
        A,
        str(positions[0]["position_id"]),
        D(1),
        enums.OrderType.MARKET,
        None,
    )
    result = await oms.submit_intent(A, close, "close")
    for _ in range(30):
        await h.pump()
    assert await oms.list_positions(A) == []
    final = oms.get_order(A, result.orders[0].order_id)
    assert final.state is S.FILLED and final.filled_base == D("0.02")


async def test_reverse_is_close_then_open() -> None:
    """Reversal is two explicit steps: confirmed flat, then open."""
    h = harness.build()
    oms = h.container.services.oms
    await oms.submit_intent(A, _req(), "open")
    pos_id = str((await oms.list_positions(A))[0]["position_id"])
    close = await oms.close_request(
        A, pos_id, D(1), enums.OrderType.MARKET, None
    )
    await oms.submit_intent(A, close, "close")
    assert await oms.list_positions(A) == []
    await oms.submit_intent(A, _req(side="sell"), "short")
    positions = await oms.list_positions(A)
    assert positions[0]["side"] == "short"


async def test_cancel_resting_and_cancel_fill_race() -> None:
    """Cancels release margin; a race resolves to the venue's FILLED."""
    h = harness.build()
    oms = h.container.services.oms
    first = (await oms.submit_intent(A, _limit("94000"), "a")).orders[0]
    canceled = await oms.cancel_order(A, first.order_id)
    assert canceled.state is S.CANCELED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_total(harness.PAPER_A) == 0
    second = (await oms.submit_intent(A, _limit("94000"), "b")).orders[0]
    h.container.paper.faults.fill_before_cancel_next = 1
    raced = await oms.cancel_order(A, second.order_id)
    assert raced.state is S.FILLED
    assert raced.filled_base == D("0.01")
    again = await oms.cancel_order(A, second.order_id)
    assert again.state is S.FILLED


async def test_amend_version_tick_and_success() -> None:
    """Amend checks the version, the tick grid and then applies."""
    h = harness.build()
    oms = h.container.services.oms
    order = (await oms.submit_intent(A, _limit("94000"), "a")).orders[0]
    with pytest.raises(errors.ExecutionError) as stale:
        await oms.amend_order(
            A,
            order.order_id,
            D(93900),
            None,
            None,
            expected_version=order.version - 1,
        )
    assert stale.value.code == "CONFLICT"
    with pytest.raises(errors.ExecutionError) as tick:
        await oms.amend_order(
            A, order.order_id, D("93900.05"), None, None, order.version
        )
    assert tick.value.code == "INVALID_TICK_SIZE"
    amended = await oms.amend_order(
        A, order.order_id, D(93900), None, D("0.02"), order.version
    )
    assert amended.limit_price == D(93900)
    assert amended.quantity_base == D("0.02")
    with pytest.raises(errors.ExecutionError) as band:
        await oms.amend_order(A, order.order_id, D(10000), None, None, None)
    assert band.value.code == "RISK_LIMIT"


async def test_kill_switch_blocks_opening_allows_reduce_and_options() -> None:
    """Engaging blocks new exposure; cancel/flatten run only on request."""
    h = harness.build()
    svc = h.container.services
    await svc.oms.submit_intent(A, _req(), "open")
    resting = await svc.oms.submit_intent(A, _limit("90000"), "rest")
    await svc.kill_switches.set(
        A,
        enums.KillSwitchScope.ACCOUNT,
        harness.PAPER_A,
        True,
        False,
        False,
        "test",
    )
    assert svc.oms.get_order(A, resting.orders[0].order_id).state is (
        S.ACKNOWLEDGED
    )
    blocked = await svc.oms.submit_intent(A, _req(), "blocked")
    assert blocked.decision.decision is enums.Decision.BLOCK
    assert blocked.decision.primary_code() == "KILL_SWITCH_ENGAGED"
    pos_id = str((await svc.oms.list_positions(A))[0]["position_id"])
    close = await svc.oms.close_request(
        A, pos_id, D(1), enums.OrderType.MARKET, None
    )
    closed = await svc.oms.submit_intent(A, close, "close")
    assert closed.decision.allowed
    await svc.kill_switches.set(
        A, enums.KillSwitchScope.GLOBAL, None, False, False, False, "release"
    )
    await svc.kill_switches.set(
        A,
        enums.KillSwitchScope.ACCOUNT,
        harness.PAPER_A,
        False,
        False,
        False,
        "release",
    )
    await svc.oms.submit_intent(A, _req(), "again")
    await svc.kill_switches.set(
        A, enums.KillSwitchScope.GLOBAL, None, True, True, True, "panic"
    )
    assert svc.oms.get_order(A, resting.orders[0].order_id).state is (
        S.CANCELED
    )
    assert await svc.oms.list_positions(A) == []
    with h.container.uow_factory() as uow:
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert actions.count("kill_switch.engaged") == 2


async def test_stale_market_data_blocks_new_exposure() -> None:
    """Stale quotes block opening orders with MARKET_STALE."""
    h = harness.build()
    h.market.set_mark(harness.BTC, D(95000), as_of_ms=h.clock.now_ms())
    h.clock.advance(60_000)
    result = await h.container.services.oms.submit_intent(A, _req(), "k")
    assert result.decision.decision is enums.Decision.BLOCK
    assert result.decision.primary_code() == "MARKET_STALE"
    h.market.set_book_stale(harness.ETH)
    h.market.set_mark(harness.ETH, D(3400))
    eth = await h.container.services.oms.submit_intent(
        A, _req(instrument_id=harness.ETH, quantity_base="0.10"), "eth"
    )
    assert eth.decision.decision is enums.Decision.BLOCK


async def test_daily_loss_pauses_new_exposure() -> None:
    """Breaching the daily loss limit pauses opening orders."""
    h = harness.build()
    svc = h.container.services
    with h.container.uow_factory() as uow:
        uow.risk_limits.put(
            "tenant-a", harness.PAPER_A, {"max_daily_loss": "10"}, 0
        )
    await svc.oms.submit_intent(A, _req(quantity_base="0.100"), "open")
    h.set_mark(harness.BTC, "94000")
    pos_id = str((await svc.oms.list_positions(A))[0]["position_id"])
    close = await svc.oms.close_request(
        A, pos_id, D(1), enums.OrderType.MARKET, None
    )
    await svc.oms.submit_intent(A, close, "close")
    paused = await svc.oms.submit_intent(A, _req(), "next")
    assert paused.decision.decision is enums.Decision.PAUSE


async def test_insufficient_margin_and_tenant_isolation() -> None:
    """Oversized orders fail margin; other tenants see nothing."""
    h = harness.build(paper_starting_equity=D(1000))
    h.container.services.accounts.ensure_paper_account("tenant-c")
    svc = h.container.services
    big = await svc.oms.submit_intent(
        A, _req(quantity_base="1.000", leverage="1"), "big"
    )
    assert big.decision.primary_code() == "INSUFFICIENT_MARGIN"
    order = (await svc.oms.submit_intent(A, _limit("94000"), "x")).orders[0]
    with pytest.raises(errors.ExecutionError) as info:
        svc.oms.get_order(harness.TENANT_B, order.order_id)
    assert info.value.code == "NOT_FOUND"
    with pytest.raises(errors.ExecutionError):
        await svc.oms.cancel_order(harness.TENANT_B, order.order_id)
    assert svc.oms.list_orders(harness.TENANT_B) == []
    with pytest.raises(errors.ExecutionError):
        await svc.oms.submit_intent(harness.TENANT_B, _req(), "steal")
