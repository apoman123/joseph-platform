"""Chaos tests: lost ACKs, timeouts, duplicate/out-of-order events, crashes.

The invariant under test (V5 §12.2 #3): an uncertain submit is never sent a
second time unless the venue confirms it does not have the order.
"""

from __future__ import annotations

import decimal

from joseph_execution import bootstrap
from joseph_execution.adapters import paper
from joseph_execution.domain import enums
from joseph_execution.domain import intents
from joseph_execution.testing import harness

D = decimal.Decimal
S = enums.OrderState
A = harness.TENANT_A


def _req(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a request from the harness body.

    Args:
        **overrides: Overrides.

    Returns:
        Request.
    """
    return intents.OrderIntentRequest.from_contract(
        harness.intent_body(**overrides)
    )


async def test_ack_loss_reconciles_without_duplicate_submit() -> None:
    """A lost ACK ends FILLED via reconciliation with one submit call."""
    h = harness.build()
    h.container.paper.faults.drop_ack_next = 1
    oms = h.container.services.oms
    order = (await oms.submit_intent(A, _req(), "k")).orders[0]
    assert order.state is S.UNKNOWN_RECONCILING
    blocked = await oms.submit_intent(A, _req(), "k2")
    assert blocked.decision.primary_code() == "POSITION_UNKNOWN"
    h.clock.advance(5_000)
    await oms.dispatch_pending()
    final = oms.get_order(A, order.order_id)
    assert final.state is S.FILLED
    assert h.container.paper.submit_calls == 1
    with h.container.uow_factory() as uow:
        assert len(uow.fills.list("tenant-a")) == 1
    assert (
        h.container.services.metrics.value(
            "order_unknown_total", {"mode": "PAPER"}
        )
        == 1
    )


async def test_timeout_before_acceptance_resubmits_once() -> None:
    """Confirmed absence allows exactly one resubmission."""
    h = harness.build()
    h.container.paper.faults.timeout_before_next = 1
    oms = h.container.services.oms
    order = (await oms.submit_intent(A, _req(), "k")).orders[0]
    assert order.state is S.UNKNOWN_RECONCILING
    h.clock.advance(5_000)
    await oms.dispatch_pending()
    final = oms.get_order(A, order.order_id)
    assert final.state is S.FILLED
    assert final.submit_attempts == 2
    assert h.container.paper.submit_calls == 2
    with h.container.uow_factory() as uow:
        assert len(uow.fills.list("tenant-a")) == 1


async def test_no_query_capability_requires_manual_intervention() -> None:
    """Without client-id queries the OMS stops and never resends."""
    h = harness.build()
    h.container.paper.faults.drop_ack_next = 1
    h.container.paper.faults.query_by_client_id = False
    oms = h.container.services.oms
    order = (await oms.submit_intent(A, _req(), "k")).orders[0]
    for _ in range(3):
        h.clock.advance(60_000)
        await oms.dispatch_pending()
    final = oms.get_order(A, order.order_id)
    assert final.state is S.UNKNOWN_RECONCILING
    assert final.manual_intervention
    assert h.container.paper.submit_calls == 1


async def test_failing_queries_back_off_then_escalate() -> None:
    """Repeated query timeouts back off and finally escalate."""
    h = harness.build()
    h.container.paper.faults.drop_ack_next = 1
    h.container.paper.faults.query_fails_next = 100
    oms = h.container.services.oms
    order = (await oms.submit_intent(A, _req(), "k")).orders[0]
    for _ in range(12):
        h.clock.advance(600_000)
        await oms.dispatch_pending()
    final = oms.get_order(A, order.order_id)
    assert final.manual_intervention
    assert h.container.paper.submit_calls == 1


async def test_worker_crash_after_marking_submitting_is_recovered() -> None:
    """An order stuck in SUBMITTING is reconciled, not blindly resent."""
    h = harness.build(background_tasks=False)
    oms = h.container.services.oms
    oms.settings = type(oms.settings)(inline_dispatch=False)
    order = (await oms.submit_intent(A, _req(), "k")).orders[0]
    assert order.state is S.RESERVED
    with h.container.uow_factory() as uow:
        stored = uow.orders.get(order.order_id)
        assert stored is not None
        version = stored.version
        stored.transition(S.SUBMITTING, h.clock.now_ms())
        uow.orders.save(stored, version)
    await oms.dispatch_pending()
    assert oms.get_order(A, order.order_id).state is S.UNKNOWN_RECONCILING
    h.clock.advance(5_000)
    await oms.dispatch_pending()
    final = oms.get_order(A, order.order_id)
    assert final.state is S.FILLED
    assert h.container.paper.submit_calls == 1


async def test_duplicate_and_reordered_events_apply_once() -> None:
    """Duplicated and reversed stream events never double count."""
    h = harness.build()
    h.container.paper.faults = paper.FaultPlan(
        duplicate_events=True, reverse_events=True
    )
    oms = h.container.services.oms
    resting = await oms.submit_intent(
        A,
        _req(
            order_type="limit",
            limit_price="94000",
            time_in_force="GTC",
            quantity_base="0.020",
        ),
        "k",
    )
    for _ in range(3):
        h.set_mark(harness.BTC, "93500")
        await h.pump()
    order = oms.get_order(A, resting.orders[0].order_id)
    assert order.state is S.FILLED
    assert order.filled_base == D("0.02")
    with h.container.uow_factory() as uow:
        fills = uow.fills.list("tenant-a")
    assert sum(f.quantity_base for f in fills) == D("0.02")
    positions = await oms.list_positions(A)
    assert positions[0]["quantity_base"] == "0.02"


async def test_dropped_fill_events_are_recovered_from_status() -> None:
    """Status reports with more fills than known pull the missing fills."""
    h = harness.build()
    h.container.paper.faults.drop_fill_events = True
    oms = h.container.services.oms
    order = (
        await oms.submit_intent(
            A,
            _req(order_type="limit", limit_price="94000", time_in_force="GTC"),
            "k",
        )
    ).orders[0]
    h.set_mark(harness.BTC, "93000")
    await h.pump()
    assert oms.get_order(A, order.order_id).state is S.FILLED


async def test_reconciler_applies_missed_fills_and_flags_mismatch() -> None:
    """Reconciliation repairs missed fills and blocks on mismatches."""
    h = harness.build()
    oms = h.container.services.oms
    order = (
        await oms.submit_intent(
            A,
            _req(order_type="limit", limit_price="94000", time_in_force="GTC"),
            "k",
        )
    ).orders[0]
    h.set_mark(harness.BTC, "93000")
    await h.container.paper.tick()
    h.container.paper.drain_events(harness.PAPER_A)
    assert oms.get_order(A, order.order_id).state is S.ACKNOWLEDGED
    report = await h.container.services.reconciler.run_once()
    assert report.fills_applied == 1
    assert oms.get_order(A, order.order_id).state is S.FILLED
    sim = h.container.paper._acct(harness.PAPER_A)
    qty, entry = sim.positions[harness.BTC]
    sim.positions[harness.BTC] = (qty * 2, entry)
    report = await h.container.services.reconciler.run_once()
    assert report.mismatches == 1
    blocked = await oms.submit_intent(A, _req(), "after-mismatch")
    assert blocked.decision.primary_code() == "POSITION_UNKNOWN"
    sim.positions[harness.BTC] = (qty, entry)
    report = await h.container.services.reconciler.run_once()
    assert report.cleared == 1
    allowed = await oms.submit_intent(A, _req(), "after-fix")
    assert allowed.decision.allowed


async def test_cancel_timeout_is_resolved_by_reconciliation() -> None:
    """A lost cancel response stays CANCEL_PENDING until reconciled."""
    h = harness.build()
    oms = h.container.services.oms
    order = (
        await oms.submit_intent(
            A,
            _req(order_type="limit", limit_price="94000", time_in_force="GTC"),
            "k",
        )
    ).orders[0]
    h.container.paper.faults.cancel_timeout_next = 1
    pending = await oms.cancel_order(A, order.order_id)
    assert pending.state is S.CANCEL_PENDING
    h.clock.advance(1_000)
    await oms.dispatch_pending()
    assert oms.get_order(A, order.order_id).state is S.CANCELED
    with h.container.uow_factory() as uow:
        assert uow.reservations.active_total(harness.PAPER_A) == 0


async def test_venue_outage_fails_cleanly_and_rejects_count() -> None:
    """Refusals before sending fail the order; reject spikes pause."""
    h = harness.build()
    oms = h.container.services.oms
    h.container.paper.faults.unavailable = True
    blocked = await oms.submit_intent(A, _req(), "down")
    assert blocked.decision.primary_code() == "VENUE_UNAVAILABLE"
    h.container.paper.faults.unavailable = False
    h.container.paper.faults.reject_next = 5
    for n in range(5):
        result = await oms.submit_intent(A, _req(), f"r{n}")
        assert result.orders[0].state is S.REJECTED
    paused = await oms.submit_intent(A, _req(), "after-rejects")
    assert paused.decision.decision is enums.Decision.PAUSE
    assert isinstance(bootstrap.Container, type)
