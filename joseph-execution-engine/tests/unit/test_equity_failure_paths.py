# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Failure paths of the securities support: every unknown fails closed."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import decimal
from typing import Any
from typing import NoReturn

import pytest

from joseph_execution import bootstrap
from joseph_execution.adapters import market_static
from joseph_execution.adapters import paper
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import positions
from joseph_execution.oms import equity_inputs
from joseph_execution.oms import valuation
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import harness

_Decimal = decimal.Decimal
_State = enums.OrderState
MONDAY = datetime.date(2026, 11, 23)


class _FailingGateway(market_static.StaticMarketDataGateway):
    """Static gateway whose securities calls raise ``ExecutionError``.

    Attributes:
        fail: Names of the methods that raise (``instrument``,
            ``sessions``, ``previous_close``); the others delegate.
    """

    def __init__(self, clock: clock_lib.ManualClock) -> None:
        """Initializes the gateway.

        Args:
            clock: Manual clock shared with the code under test.
        """
        super().__init__(clock)
        self.fail: set[str] = set()

    def _check(self, name: str) -> None:
        """Raises when ``name`` is set to fail.

        Args:
            name: Method name.

        Raises:
            MarketDataUnavailableError: ``name`` is in ``fail``.
        """
        if name in self.fail:
            raise errors.MarketDataUnavailableError(name)

    async def instrument(self, instrument_id: str) -> Any:
        """Raises when ``instrument`` is set to fail, else delegates.

        Args:
            instrument_id: Instrument.

        Returns:
            The base gateway's metadata.

        Raises:
            MarketDataUnavailableError: ``instrument`` is in ``fail``.
        """
        self._check("instrument")
        return await super().instrument(instrument_id)

    async def sessions(self, calendar: str, from_ms: int, to_ms: int) -> Any:
        """Raises when ``sessions`` is set to fail, else delegates.

        Args:
            calendar: Calendar id.
            from_ms: Window start, epoch milliseconds.
            to_ms: Window end, epoch milliseconds.

        Returns:
            The base gateway's sessions.

        Raises:
            MarketDataUnavailableError: ``sessions`` is in ``fail``.
        """
        self._check("sessions")
        return await super().sessions(calendar, from_ms, to_ms)

    async def previous_close(self, instrument_id: str) -> Any:
        """Raises when ``previous_close`` is set to fail, else delegates.

        Args:
            instrument_id: Instrument.

        Returns:
            The base gateway's previous close.

        Raises:
            MarketDataUnavailableError: ``previous_close`` is in ``fail``.
        """
        self._check("previous_close")
        return await super().previous_close(instrument_id)


def _gateway(now_ms: int) -> tuple[_FailingGateway, clock_lib.ManualClock]:
    """Builds a failing gateway with securities fixtures.

    Args:
        now_ms: Start time of the manual clock, epoch milliseconds.

    Returns:
        The gateway, holding the securities fixtures from the week before
        ``MONDAY``, and its clock; nothing fails until ``fail`` is set.
    """
    clock = clock_lib.ManualClock(now_ms)
    gateway = _FailingGateway(clock)
    fixtures.install(gateway, MONDAY - datetime.timedelta(days=7))
    return gateway, clock


def _closed_socket_place_order(contract: Any, order: Any) -> NoReturn:
    """Fails like ``placeOrder`` on a socket that closed after sending.

    Args:
        contract: Ignored IB contract.
        order: Ignored IB order.

    Raises:
        OSError: Always.
    """
    raise OSError("socket closed")


async def _lost_open_orders() -> NoReturn:
    """Fails like ``reqAllOpenOrdersAsync`` on a lost connection.

    Raises:
        ConnectionError: Always.
    """
    raise ConnectionError("lost")


def _order(cid: str, **overrides: object) -> trading.VenueOrderRequest:
    """Builds a TWSE market buy for the TWD paper account.

    Args:
        cid: Client order id.
        **overrides: Request fields that replace the defaults.

    Returns:
        By default an IOC market buy of 1000 TSMC (2330) shares.
    """
    fields: dict[str, object] = {
        "account_id": accounts.paper_account_id("t", "TWD"),
        "instrument_id": fixtures.TSMC,
        "client_order_id": cid,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.MARKET,
        "quantity_base": _Decimal(1000),
        "time_in_force": enums.TimeInForce.IOC,
        "reduce_only": False,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


async def test_paper_maps_market_data_errors_to_unknowns() -> None:
    """Paper securities treat failing calendars and closes as unknown."""
    gateway, _ = _gateway(fixtures.taipei(MONDAY, 10))
    adapter = paper.PaperTradingAdapter(gateway, gateway._clock)
    gateway.fail = {"previous_close"}
    filled = await adapter.submit_order(_order("a"))
    assert filled.fills[0].price == _Decimal(601)
    gateway.fail = {"sessions"}
    waiting = await adapter.submit_order(
        _order(
            "b",
            order_type=enums.OrderType.LIMIT,
            limit_price=_Decimal(601),
            time_in_force=enums.TimeInForce.DAY,
        )
    )
    assert waiting.state.status is _State.ACKNOWLEDGED and not waiting.fills
    gateway.fail = {"instrument"}
    blind = await adapter.submit_order(
        _order(
            "c",
            order_type=enums.OrderType.LIMIT,
            limit_price=_Decimal(601),
            time_in_force=enums.TimeInForce.DAY,
        )
    )
    assert not blind.fills
    us = await paper.PaperTradingAdapter(
        _gateway(fixtures.new_york(MONDAY, 10))[0],
        clock_lib.ManualClock(fixtures.new_york(MONDAY, 10)),
    ).submit_order(
        _order(
            "d",
            account_id=accounts.paper_account_id("t"),
            instrument_id=fixtures.AAPL,
            quantity_base=_Decimal(5),
        )
    )
    assert us.fills[0].price == _Decimal("190.01")


async def test_equity_inputs_fail_closed() -> None:
    """Gathering maps errors to unknown sessions, closes and cash."""
    gateway, clock = _gateway(fixtures.taipei(MONDAY, 10))
    gateway.fail = {"sessions", "previous_close"}
    assert (
        await equity_inputs.sessions_around(gateway, "XTAI", clock.now_ms())
        is None
    )
    h = harness.build()
    account = accounts.Account(
        "acct-ib",
        "tenant-a",
        "ibkr",
        "IB",
        enums.AccountStatus.CONNECTED,
        (enums.OrderMode.LIVE,),
        "USD",
        1,
    )
    position = positions.Position(
        "p", "tenant-a", "acct-ib", fixtures.AAPL, enums.OrderMode.LIVE
    )
    position.apply_fill(
        enums.OrderSide.BUY, _Decimal(1), _Decimal(190), _Decimal(0), 1
    )
    account_valuation = valuation.Valuation(
        None, None, None, (position,), {}, True
    )
    refused = h.container.brokers["ibkr"]
    inputs = await equity_inputs.gather(
        gateway,
        h.container.uow_factory,
        refused,
        account,
        enums.OrderMode.LIVE,
        fixtures.twse(),
        account_valuation,
        clock.now_ms(),
    )
    assert inputs.sessions is None and inputs.previous_close is None
    assert inputs.cash is None and inputs.gross_notional is None
    paper_inputs = await equity_inputs.gather(
        gateway,
        h.container.uow_factory,
        refused,
        account,
        enums.OrderMode.PAPER,
        None,
        dataclasses.replace(account_valuation, positions=()),
        clock.now_ms(),
    )
    assert paper_inputs.cash is None and paper_inputs.sessions is None


async def test_broker_account_lookup_returns_none_when_unknown() -> None:
    """The lookup ignores unknown, paper, revoked and foreign-key accounts."""
    h = harness.build()
    services = h.container.services
    key = h.container.settings.vault_master_key
    lookup = bootstrap.broker_account_lookup(
        h.container.uow_factory, vault.AesGcmVault(key)
    )
    assert lookup("missing") is None
    assert lookup(harness.PAPER_A) is None
    ib = await services.accounts.connect_start(
        harness.TENANT_A, "ibkr", "IB", {"account": "DU7654321"}, True
    )
    ib_id = str(ib["account_id"])
    assert lookup(ib_id) == "DU7654321"
    other_key = bootstrap.broker_account_lookup(
        h.container.uow_factory, vault.AesGcmVault(b"z" * 32)
    )
    assert other_key(ib_id) is None
    crypto = await services.accounts.connect_start(
        harness.TENANT_A,
        "binance",
        "B",
        {"api_key": "public", "api_secret": "secret-value"},
        True,
    )
    assert lookup(str(crypto["account_id"])) is None
    await services.accounts.revoke(harness.TENANT_A, ib_id)
    assert lookup(ib_id) is None


async def test_broker_event_job_resyncs_and_survives_errors() -> None:
    """The background job resyncs brokers and keeps running on errors."""
    h = harness.build(
        tick_interval_ms=1,
        securities=config.SecuritiesSettings(broker_resync_interval_ms=0),
    )
    calls: list[str] = []

    async def failing_resync() -> NoReturn:
        """Records the call and fails.

        Raises:
            RuntimeError: Always.
        """
        calls.append("resync")
        raise RuntimeError("broker down")

    adapter = h.container.brokers["cathay"]
    adapter.resync = failing_resync  # type: ignore[method-assign]  # injects a failure into this instance
    job = bootstrap.background_jobs(h.container)[-1]
    task = asyncio.ensure_future(job())
    for _ in range(20):
        await asyncio.sleep(0.005)
        if len(calls) >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


async def test_ibkr_edge_paths() -> None:
    """IBKR ignores foreign fills; odd states and dead sockets fail closed."""
    clock = clock_lib.ManualClock(fixtures.new_york(MONDAY, 10))
    gateway, _ = _gateway(clock.now_ms())
    gateway.fail = {"instrument"}
    ib = fake_ib.FakeIB()
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(ack_timeout_s=0.02, commission_wait_s=0.01),
        lambda: fake_ib.FakeIbFactory(ib),
        gateway,
        clock,
        {"acct": "DU1234567"}.get,
    )
    cid = f"j{clock.now_ms():012x}{1:018x}"
    request = trading.VenueOrderRequest(
        account_id="acct",
        instrument_id=fixtures.AAPL,
        client_order_id=cid,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        quantity_base=_Decimal(1),
        time_in_force=enums.TimeInForce.DAY,
        reduce_only=False,
        limit_price=_Decimal(190),
    )
    result = await adapter.submit_order(request)
    assert result.accepted
    trade = ib.trades()[0]
    foreign = fake_ib.FakeTrade(
        fake_ib.FakeContract("MSFT"),
        fake_ib.FakeOrder(orderRef="manual", account="DU1234567", orderId=99),
        fake_ib.FakeOrderStatus("Submitted"),
    )
    ib._trades.append(foreign)
    ib.orderStatusEvent.emit(foreign)
    assert await adapter.list_open_orders("acct") != []
    trade.orderStatus.status = "ValidationError"
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(request)
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.get_order_by_client_id("acct", cid)
    trade.orderStatus.status = "Inactive"
    assert ibkr.map_status(trade) is _State.REJECTED
    trade.orderStatus.status = "Submitted"
    trade.order.orderRef = cid
    other = fake_ib.FakeFill(
        fake_ib.FakeContract("MSFT"),
        fake_ib.FakeExecution("e1", "U999", 1, 1.0, "manual", 99),
        fake_ib.FakeCommission(),
    )
    ib._fills.append(other)
    assert await adapter.list_recent_fills("acct") == []
    ib.errorEvent.emit(12345, 201, "unknown order", None)
    ib.placeOrder = _closed_socket_place_order  # type: ignore[method-assign]  # injects a failure into this fake
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(
            dataclasses.replace(request, client_order_id=cid[:-1] + "2")
        )
    ib.reqAllOpenOrdersAsync = _lost_open_orders  # type: ignore[method-assign]  # injects a failure into this fake
    assert await adapter.resync() == 0
