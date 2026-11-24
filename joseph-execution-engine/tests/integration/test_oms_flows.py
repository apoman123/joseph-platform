# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests of OMS flows against the paper venue simulator."""

from __future__ import annotations

import dataclasses
import decimal

import pytest

from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.ports import trading
from joseph_execution.testing import harness

_Decimal = decimal.Decimal
_State = enums.OrderState
_TENANT_A = harness.TENANT_A


def _req(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a domain request from the harness body.

    Args:
        **overrides: Contract body fields passed on to
            ``harness.intent_body``.

    Returns:
        The request; by default a 0.010 BTC paper market buy for tenant A.
    """
    return intents.OrderIntentRequest.from_contract(
        harness.intent_body(**overrides)
    )


def _limit(price: str, **overrides: object) -> intents.OrderIntentRequest:
    """Builds a GTC limit buy.

    Args:
        price: Limit price, as a decimal string.
        **overrides: Further body fields passed on to ``_req``.

    Returns:
        The limit request.
    """
    return _req(
        order_type="limit", limit_price=price, time_in_force="GTC", **overrides
    )


async def test_market_order_fills_and_releases_reservation() -> None:
    """A market buy fills, creates a position and releases margin."""
    h = harness.build()
    oms = h.container.services.oms
    result = await oms.submit_intent(_TENANT_A, _req(), "k1")
    assert result.decision.allowed and not result.replayed
    order = result.orders[0]
    assert order.state is _State.FILLED and order.exchange_order_id
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_total(harness.PAPER_A) == 0
        fills = uow.fills.list("tenant-a")
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert len(fills) == 1
    assert "intent.created" in actions and "order.submitted" in actions
    positions = await oms.list_positions(_TENANT_A)
    assert positions[0]["quantity_base"] == "0.01"


async def test_limit_order_rests_then_fills_as_maker() -> None:
    """A resting limit keeps its reservation until the venue fills it."""
    h = harness.build()
    oms = h.container.services.oms
    result = await oms.submit_intent(_TENANT_A, _limit("94000"), "k1")
    order = result.orders[0]
    assert order.state is _State.ACKNOWLEDGED
    with h.container.uow_factory() as uow:
        reserved = uow.reservations.active_for_order(order.order_id)
        assert reserved is not None and reserved > 0
    h.set_mark(harness.BTC, "93900")
    await h.pump()
    filled = oms.get_order(_TENANT_A, order.order_id)
    assert filled.state is _State.FILLED
    assert filled.avg_fill_price == _Decimal(94000)
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is None


async def test_partial_fills_keep_reservation_until_terminal() -> None:
    """Partial fills do not release the reservation early."""
    h = harness.build()
    h.container.paper.faults.partial_fill_fraction = _Decimal("0.5")
    oms = h.container.services.oms
    order = (
        await oms.submit_intent(
            _TENANT_A, _limit("94000", quantity_base="0.040"), "k1"
        )
    ).orders[0]
    h.set_mark(harness.BTC, "93000")
    await h.pump()
    partial = oms.get_order(_TENANT_A, order.order_id)
    assert partial.state is _State.PARTIALLY_FILLED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is not None
    for _ in range(30):
        await h.pump()
    done = oms.get_order(_TENANT_A, order.order_id)
    assert done.state is _State.FILLED and done.filled_base == _Decimal("0.04")
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_for_order(order.order_id) is None
        assert sum(f.quantity_base for f in uow.fills.list("tenant-a")) == (
            _Decimal("0.04")
        )


async def test_bracket_children_and_oco() -> None:
    """Fills spawn reduce-only SL/TP; one filling cancels the other."""
    h = harness.build()
    oms = h.container.services.oms
    bracket = {
        "stop_loss": {"trigger": "94000", "basis": "mark"},
        "take_profit": [{"trigger": "96000", "fraction": "1"}],
    }
    result = await oms.submit_intent(_TENANT_A, _req(bracket=bracket), "k1")
    orders = oms.list_orders(_TENANT_A)
    children = [o for o in orders if o.parent_order_id]
    assert {c.role for c in children} == {
        enums.OrderRole.STOP_LOSS,
        enums.OrderRole.TAKE_PROFIT,
    }
    assert all(
        c.reduce_only and c.side is enums.OrderSide.SELL for c in children
    )
    assert all(c.state is _State.ACKNOWLEDGED for c in children)
    h.set_mark(harness.BTC, "93950")
    await h.pump()
    states = {
        o.role: o.state for o in oms.list_orders(_TENANT_A) if o.parent_order_id
    }
    assert states[enums.OrderRole.STOP_LOSS] is _State.FILLED
    assert states[enums.OrderRole.TAKE_PROFIT] is _State.CANCELED
    assert await oms.list_positions(_TENANT_A) == []
    assert result.orders[0].role is enums.OrderRole.PRIMARY


async def test_close_never_flips_and_oversized_reduce_is_rejected() -> None:
    """Closes are reduce-only and capped at the position size."""
    h = harness.build()
    oms = h.container.services.oms
    await oms.submit_intent(_TENANT_A, _req(quantity_base="0.020"), "open")
    pos_id = (await oms.list_positions(_TENANT_A))[0]["position_id"]
    half = await oms.close_request(
        _TENANT_A, str(pos_id), _Decimal("0.5"), enums.OrderType.MARKET, None
    )
    assert half.quantity_base == _Decimal("0.01") and half.reduce_only
    assert half.position_effect is enums.PositionEffect.REDUCE
    too_big = _req(
        side="sell",
        position_effect="close",
        reduce_only=True,
        quantity_base="0.030",
    )
    rejected = await oms.submit_intent(_TENANT_A, too_big, "big")
    assert rejected.decision.decision is enums.Decision.REJECT
    assert rejected.orders == ()
    full = await oms.close_request(
        _TENANT_A, str(pos_id), _Decimal(1), enums.OrderType.MARKET, None
    )
    await oms.submit_intent(_TENANT_A, full, "close")
    assert await oms.list_positions(_TENANT_A) == []
    with pytest.raises(errors.ExecutionError) as info:
        await oms.close_request(
            _TENANT_A, str(pos_id), _Decimal(1), enums.OrderType.MARKET, None
        )
    assert info.value.code == "CONFLICT"


async def test_full_close_completes_across_partial_fills() -> None:
    """A reduce-only close split into partial fills ends exactly flat."""
    h = harness.build()
    h.container.paper.faults.partial_fill_fraction = _Decimal("0.5")
    oms = h.container.services.oms
    await oms.submit_intent(_TENANT_A, _req(quantity_base="0.020"), "open")
    for _ in range(30):
        await h.pump()
    positions = await oms.list_positions(_TENANT_A)
    assert _Decimal(str(positions[0]["quantity_base"])) == _Decimal("0.02")
    close = await oms.close_request(
        _TENANT_A,
        str(positions[0]["position_id"]),
        _Decimal(1),
        enums.OrderType.MARKET,
        None,
    )
    result = await oms.submit_intent(_TENANT_A, close, "close")
    for _ in range(30):
        await h.pump()
    assert await oms.list_positions(_TENANT_A) == []
    final = oms.get_order(_TENANT_A, result.orders[0].order_id)
    assert final.state is _State.FILLED and final.filled_base == _Decimal(
        "0.02"
    )


async def test_reverse_is_close_then_open() -> None:
    """Reversal is two explicit steps: confirmed flat, then open."""
    h = harness.build()
    oms = h.container.services.oms
    await oms.submit_intent(_TENANT_A, _req(), "open")
    pos_id = str((await oms.list_positions(_TENANT_A))[0]["position_id"])
    close = await oms.close_request(
        _TENANT_A, pos_id, _Decimal(1), enums.OrderType.MARKET, None
    )
    await oms.submit_intent(_TENANT_A, close, "close")
    assert await oms.list_positions(_TENANT_A) == []
    await oms.submit_intent(_TENANT_A, _req(side="sell"), "short")
    positions = await oms.list_positions(_TENANT_A)
    assert positions[0]["side"] == "short"


async def test_cancel_resting_and_cancel_fill_race() -> None:
    """Cancels release margin; a race resolves to the venue's FILLED."""
    h = harness.build()
    oms = h.container.services.oms
    first = (await oms.submit_intent(_TENANT_A, _limit("94000"), "a")).orders[0]
    canceled = await oms.cancel_order(_TENANT_A, first.order_id)
    assert canceled.state is _State.CANCELED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_total(harness.PAPER_A) == 0
    second = (await oms.submit_intent(_TENANT_A, _limit("94000"), "b")).orders[
        0
    ]
    h.container.paper.faults.fill_before_cancel_next = 1
    raced = await oms.cancel_order(_TENANT_A, second.order_id)
    assert raced.state is _State.FILLED
    assert raced.filled_base == _Decimal("0.01")
    again = await oms.cancel_order(_TENANT_A, second.order_id)
    assert again.state is _State.FILLED


async def test_amend_version_tick_and_success() -> None:
    """Amend checks the version, tick grid and price band, then applies."""
    h = harness.build()
    oms = h.container.services.oms
    order = (await oms.submit_intent(_TENANT_A, _limit("94000"), "a")).orders[0]
    with pytest.raises(errors.ExecutionError) as stale:
        await oms.amend_order(
            _TENANT_A,
            order.order_id,
            _Decimal(93900),
            None,
            None,
            expected_version=order.version - 1,
        )
    assert stale.value.code == "CONFLICT"
    with pytest.raises(errors.ExecutionError) as tick:
        await oms.amend_order(
            _TENANT_A,
            order.order_id,
            _Decimal("93900.05"),
            None,
            None,
            order.version,
        )
    assert tick.value.code == "INVALID_TICK_SIZE"
    amended = await oms.amend_order(
        _TENANT_A,
        order.order_id,
        _Decimal(93900),
        None,
        _Decimal("0.02"),
        order.version,
    )
    assert amended.limit_price == _Decimal(93900)
    assert amended.quantity_base == _Decimal("0.02")
    with pytest.raises(errors.ExecutionError) as band:
        await oms.amend_order(
            _TENANT_A, order.order_id, _Decimal(10000), None, None, None
        )
    assert band.value.code == "RISK_LIMIT"


async def test_kill_switch_blocks_opening_allows_reduce_and_options() -> None:
    """Engaging blocks new exposure; cancel/flatten run only on request."""
    h = harness.build()
    services = h.container.services
    await services.oms.submit_intent(_TENANT_A, _req(), "open")
    resting = await services.oms.submit_intent(
        _TENANT_A, _limit("90000"), "rest"
    )
    await services.kill_switches.set(
        _TENANT_A,
        enums.KillSwitchScope.ACCOUNT,
        harness.PAPER_A,
        True,
        False,
        False,
        "test",
    )
    assert services.oms.get_order(
        _TENANT_A, resting.orders[0].order_id
    ).state is (_State.ACKNOWLEDGED)
    blocked = await services.oms.submit_intent(_TENANT_A, _req(), "blocked")
    assert blocked.decision.decision is enums.Decision.BLOCK
    assert blocked.decision.primary_code() == "KILL_SWITCH_ENGAGED"
    pos_id = str(
        (await services.oms.list_positions(_TENANT_A))[0]["position_id"]
    )
    close = await services.oms.close_request(
        _TENANT_A, pos_id, _Decimal(1), enums.OrderType.MARKET, None
    )
    closed = await services.oms.submit_intent(_TENANT_A, close, "close")
    assert closed.decision.allowed
    await services.kill_switches.set(
        _TENANT_A,
        enums.KillSwitchScope.GLOBAL,
        None,
        False,
        False,
        False,
        "release",
    )
    await services.kill_switches.set(
        _TENANT_A,
        enums.KillSwitchScope.ACCOUNT,
        harness.PAPER_A,
        False,
        False,
        False,
        "release",
    )
    await services.oms.submit_intent(_TENANT_A, _req(), "again")
    await services.kill_switches.set(
        _TENANT_A, enums.KillSwitchScope.GLOBAL, None, True, True, True, "panic"
    )
    assert services.oms.get_order(
        _TENANT_A, resting.orders[0].order_id
    ).state is (_State.CANCELED)
    assert await services.oms.list_positions(_TENANT_A) == []
    with h.container.uow_factory() as uow:
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert actions.count("kill_switch.engaged") == 2


async def test_stale_market_data_blocks_new_exposure() -> None:
    """Stale quotes (MARKET_STALE) and stale books block opening orders."""
    h = harness.build()
    h.market.set_mark(harness.BTC, _Decimal(95000), as_of_ms=h.clock.now_ms())
    h.clock.advance(60_000)
    result = await h.container.services.oms.submit_intent(
        _TENANT_A, _req(), "k"
    )
    assert result.decision.decision is enums.Decision.BLOCK
    assert result.decision.primary_code() == "MARKET_STALE"
    h.market.set_book_stale(harness.ETH)
    h.market.set_mark(harness.ETH, _Decimal(3400))
    eth = await h.container.services.oms.submit_intent(
        _TENANT_A, _req(instrument_id=harness.ETH, quantity_base="0.10"), "eth"
    )
    assert eth.decision.decision is enums.Decision.BLOCK


async def test_daily_loss_pauses_new_exposure() -> None:
    """Breaching the daily loss limit pauses opening orders."""
    h = harness.build()
    services = h.container.services
    with h.container.uow_factory() as uow:
        uow.risk_limits.put(
            "tenant-a", harness.PAPER_A, {"max_daily_loss": "10"}, 0
        )
    await services.oms.submit_intent(
        _TENANT_A, _req(quantity_base="0.100"), "open"
    )
    h.set_mark(harness.BTC, "94000")
    pos_id = str(
        (await services.oms.list_positions(_TENANT_A))[0]["position_id"]
    )
    close = await services.oms.close_request(
        _TENANT_A, pos_id, _Decimal(1), enums.OrderType.MARKET, None
    )
    await services.oms.submit_intent(_TENANT_A, close, "close")
    paused = await services.oms.submit_intent(_TENANT_A, _req(), "next")
    assert paused.decision.decision is enums.Decision.PAUSE


async def test_insufficient_margin_and_tenant_isolation() -> None:
    """Oversized orders fail margin; other tenants see nothing."""
    h = harness.build(paper_starting_equity=_Decimal(1000))
    h.container.services.accounts.ensure_paper_account("tenant-c")
    services = h.container.services
    big = await services.oms.submit_intent(
        _TENANT_A, _req(quantity_base="1.000", leverage="1"), "big"
    )
    assert big.decision.primary_code() == "INSUFFICIENT_MARGIN"
    order = (
        await services.oms.submit_intent(_TENANT_A, _limit("94000"), "x")
    ).orders[0]
    with pytest.raises(errors.ExecutionError) as info:
        services.oms.get_order(harness.TENANT_B, order.order_id)
    assert info.value.code == "NOT_FOUND"
    with pytest.raises(errors.ExecutionError):
        await services.oms.cancel_order(harness.TENANT_B, order.order_id)
    assert services.oms.list_orders(harness.TENANT_B) == []
    with pytest.raises(errors.ExecutionError):
        await services.oms.submit_intent(harness.TENANT_B, _req(), "steal")


async def test_reconciler_skips_unmanaged_holdings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unmanaged holdings never block the account; mislabelled ones do."""
    h = harness.build()
    oms = h.container.services.oms
    assert (await oms.submit_intent(_TENANT_A, _req(), "k1")).decision.allowed
    paper = h.container.paper
    real = paper.list_positions
    emerging = trading.VenuePosition(
        "oes:stock:7777",
        _Decimal(3000),
        _Decimal(35),
        unmanaged_note="7777 is an emerging-board (興櫃) holding",
    )
    extra = [emerging]

    async def with_holding(account_id: str) -> list[trading.VenuePosition]:
        """Adds the extra holdings to the simulator's positions.

        Args:
            account_id: Account to list.

        Returns:
            The simulator's positions followed by ``extra``.
        """
        return [*await real(account_id), *extra]

    monkeypatch.setattr(paper, "list_positions", with_holding)
    report = await h.container.services.reconciler.run_once()
    assert (report.mismatches, report.unmanaged) == (0, 1)
    assert (await oms.submit_intent(_TENANT_A, _req(), "k2")).decision.allowed
    extra[:] = [
        dataclasses.replace(
            emerging, instrument_id="twse:stock:7777", unmanaged_note=None
        )
    ]
    report = await h.container.services.reconciler.run_once()
    assert (report.mismatches, report.unmanaged) == (1, 0)
    blocked = await oms.submit_intent(_TENANT_A, _req(), "k3")
    assert blocked.decision.primary_code() == "POSITION_UNKNOWN"
