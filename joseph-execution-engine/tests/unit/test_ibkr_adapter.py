"""Unit tests for the Interactive Brokers adapter against a fake IB."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import types
from typing import Any

import pytest

from joseph_execution.adapters import market_static
from joseph_execution.adapters.brokers import base
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib

D = decimal.Decimal
S = enums.OrderState
MONDAY = datetime.date(2026, 11, 23)
ACCOUNT = "acct-ib"
NUMBER = "DU1234567"


class _Rig:
    """Adapter wired to a fake IB.

    Attributes:
        adapter: Adapter under test.
        ib: Fake client.
        factory: Fake factory.
        clock: Clock.
        gateway: Market gateway.
    """

    def __init__(self, gate: str | None = None, **config: Any) -> None:
        """Builds the rig.

        Args:
            gate: Gate reason.
            **config: ``IbkrConfig`` overrides.
        """
        self.clock = clock_lib.ManualClock(fixtures.new_york(MONDAY, 10))
        self.gateway = market_static.StaticMarketDataGateway(self.clock)
        fixtures.install(self.gateway, MONDAY)
        self.ib = fake_ib.FakeIB()
        self.factory = fake_ib.FakeIbFactory(self.ib)
        settings = {
            "ack_timeout_s": 0.05,
            "commission_wait_s": 0.01,
            "reconnect_base_s": 0.001,
            "reconnect_max_s": 0.002,
            **config,
        }
        self.adapter = ibkr.IbkrAdapter(
            ibkr.IbkrConfig(**settings),
            lambda: self.factory,
            self.gateway,
            self.clock,
            {ACCOUNT: NUMBER}.get,
            gate,
        )

    def cid(self, n: int, age_ms: int = 0) -> str:
        """Builds a Joseph client id created ``age_ms`` ago.

        Args:
            n: Distinguishing number.
            age_ms: Age.

        Returns:
            Client id.
        """
        return f"j{self.clock.now_ms() - age_ms:012x}{n:018x}"


def _req(cid: str, **overrides: object) -> trading.VenueOrderRequest:
    """Builds a venue request for AAPL.

    Args:
        cid: Client id.
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": ACCOUNT,
        "instrument_id": fixtures.AAPL,
        "client_order_id": cid,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D(10),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "limit_price": D("190.01"),
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]


async def test_gated_adapter_never_creates_a_client() -> None:
    """While gated nothing connects and every call is refused."""
    rig = _Rig(gate="live trading is disabled")
    caps = await rig.adapter.capabilities(ACCOUNT)
    assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
    assert "live trading is disabled" in caps.notes[-1]
    assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
    calls = (
        rig.adapter.submit_order(_req(rig.cid(1))),
        rig.adapter.cancel_order(ACCOUNT, "x"),
        rig.adapter.amend_order(ACCOUNT, "x", D(1), None, None),
        rig.adapter.get_order_by_client_id(ACCOUNT, "x"),
        rig.adapter.account_snapshot(ACCOUNT),
        rig.adapter.list_positions(ACCOUNT),
        rig.adapter.list_open_orders(ACCOUNT),
        rig.adapter.list_recent_fills(ACCOUNT),
    )
    for call in calls:
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await call
        assert info.value.code == "LIVE_TRADING_DISABLED"
    with pytest.raises(errors.AdapterUnavailableError):
        await anext(rig.adapter.stream_account_events(ACCOUNT))
    assert not rig.adapter.bind(ACCOUNT)
    assert await rig.adapter.resync() == 0
    assert rig.factory.created == 0 and rig.ib.connects == []
    assert "submit_order" in rig.adapter.calls


async def test_capabilities_negotiate_with_the_gateway() -> None:
    """Capabilities connect and verify the managed account."""
    rig = _Rig(host="ib-gateway", port=4004, client_id=9)
    caps = await rig.adapter.capabilities(ACCOUNT)
    assert caps.status is enums.TradingStatus.AVAILABLE
    assert caps.tradable_venues == ("nasdaq", "nyse", "cboe")
    assert caps.outside_rth and caps.cash_account and caps.simulation
    assert rig.ib.connects == [
        {"host": "ib-gateway", "port": 4004, "client_id": 9}
    ]
    assert await rig.adapter.health() is enums.HealthState.HEALTHY
    assert set(rig.ib.requests) >= {"open", "completed", "executions"}
    unbound = await rig.adapter.capabilities("other")
    assert "no IBKR account number" in unbound.notes[-1]
    rig.ib.managed = ["U7654321"]
    foreign = await rig.adapter.capabilities(ACCOUNT)
    assert foreign.status is enums.TradingStatus.TRADING_UNAVAILABLE
    assert "***4567" in foreign.notes[-1]


async def test_readonly_and_unreachable_gateways_are_unavailable() -> None:
    """Read-only sessions and a down gateway cannot trade."""
    readonly = _Rig(readonly=True)
    caps = await readonly.adapter.capabilities(ACCOUNT)
    assert "read-only" in caps.notes[-1]
    down = _Rig()
    down.ib.connect_failures = 1
    caps = await down.adapter.capabilities(ACCOUNT)
    assert "unreachable" in caps.notes[-1]
    assert await down.adapter.health() is enums.HealthState.UNAVAILABLE
    with pytest.raises(errors.AdapterUnavailableError):
        await _Rig(short_selling=True).adapter.submit_order(
            _req("x", account_id="nobody")
        )


def test_order_mapping() -> None:
    """Venue requests map to IBKR order types, TIF and outsideRth."""
    limit = ibkr.order_fields(_req("j1", outside_rth=True), NUMBER)
    assert limit == {
        "action": "BUY",
        "totalQuantity": 10,
        "orderType": "LMT",
        "tif": "DAY",
        "outsideRth": True,
        "orderRef": "j1",
        "account": NUMBER,
        "transmit": True,
        "lmtPrice": D("190.01"),
    }
    market = ibkr.order_fields(
        _req(
            "j2",
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            side=enums.OrderSide.SELL,
            time_in_force=enums.TimeInForce.IOC,
        ),
        NUMBER,
    )
    assert (market["orderType"], market["action"], market["tif"]) == (
        "MKT",
        "SELL",
        "IOC",
    )
    assert "lmtPrice" not in market
    stop = ibkr.order_fields(
        _req(
            "j3",
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D(180),
            time_in_force=enums.TimeInForce.GTC,
        ),
        NUMBER,
    )
    assert (stop["orderType"], stop["auxPrice"]) == ("STP", D(180))
    stop_limit = ibkr.order_fields(
        _req(
            "j4",
            order_type=enums.OrderType.STOP_LIMIT,
            trigger_price=D(185),
        ),
        NUMBER,
    )
    assert stop_limit["orderType"] == "STP LMT"
    assert (stop_limit["lmtPrice"], stop_limit["auxPrice"]) == (
        D("190.01"),
        D(185),
    )
    with pytest.raises(errors.AdapterUnavailableError):
        ibkr.order_fields(_req("j5", time_in_force=enums.TimeInForce.FOK), "x")
    with pytest.raises(errors.AdapterUnavailableError):
        ibkr.order_fields(_req("j6", quantity_base=D("0.5")), "x")


def test_contract_naming() -> None:
    """Listing exchanges map to primaryExchange; share classes use spaces."""
    assert ibkr.primary_exchange("Nasdaq", "nasdaq") == "NASDAQ"
    assert ibkr.primary_exchange("NYSE Arca", "nyse") == "ARCA"
    assert ibkr.primary_exchange("NYSE American", "nyse") == "AMEX"
    assert ibkr.primary_exchange(None, "nyse") == "NYSE"
    assert ibkr.primary_exchange("Cboe", "nasdaq") == "NASDAQ"
    assert ibkr.primary_exchange("Cboe BZX", "cboe") == "BATS"
    assert ibkr.primary_exchange(None, "cboe") == "BATS"
    with pytest.raises(errors.AdapterUnavailableError):
        ibkr.primary_exchange(None, "twse")
    assert ibkr.ib_symbol("BRK.B") == "BRK B"
    assert ibkr.joseph_symbol("BRK B") == "BRK.B"
    assert ibkr.fill_id("0000e0d5.65f1a2b3.01.02") == (
        "ibkr-0000e0d5.65f1a2b3.01"
    )
    assert ibkr.fill_id("abc") == "ibkr-abc"


async def test_submit_acknowledges_and_is_idempotent() -> None:
    """Orders are qualified, placed once and acknowledged."""
    rig = _Rig()
    cid = rig.cid(1)
    request = _req(cid, instrument_id=fixtures.SPY, limit_price=D(580))
    result = await rig.adapter.submit_order(request)
    assert result.accepted and result.state.status is S.ACKNOWLEDGED
    assert result.state.exchange_order_id == "900001"
    placed = rig.ib.placed[0]
    assert placed.orderRef == cid and placed.account == NUMBER
    assert rig.ib.trades()[0].contract.primaryExchange == "ARCA"
    again = await rig.adapter.submit_order(request)
    assert again.accepted and len(rig.ib.placed) == 1
    events = rig.adapter.drain_events(ACCOUNT)
    assert [e.order.status for e in events if e.order] == [S.ACKNOWLEDGED]
    assert rig.adapter.event_accounts() == []


async def test_cboe_listings_qualify_on_bats() -> None:
    """A Cboe BZX ETF is SMART-routed with primaryExchange BATS."""
    rig = _Rig()
    result = await rig.adapter.submit_order(
        _req(rig.cid(1), instrument_id=fixtures.GOVT, limit_price=D(23))
    )
    assert result.accepted
    contract = rig.ib.trades()[0].contract
    assert (contract.symbol, contract.exchange) == ("GOVT", "SMART")
    assert contract.primaryExchange == "BATS"


async def test_rejections_and_unknown_outcomes() -> None:
    """Error 201 rejects; a missing acknowledgement is an unknown outcome."""
    rig = _Rig()
    rig.ib.reject_next = (201, "Order rejected - reason: no permissions")
    rejected = await rig.adapter.submit_order(_req(rig.cid(1)))
    assert not rejected.accepted and rejected.state.status is S.REJECTED
    assert "no permissions" in (rejected.state.reason or "")
    rig.ib.ack = None
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.submit_order(_req(rig.cid(2)))
    rig.ib.unknown_symbols.add("MSFT")
    with pytest.raises(errors.AdapterUnavailableError) as info:
        await rig.adapter.submit_order(
            _req(rig.cid(3), instrument_id="nasdaq:stock:MSFT")
        )
    assert info.value.code == "UNSUPPORTED"
    with pytest.raises(errors.AdapterUnavailableError):
        await rig.adapter.submit_order(
            _req(rig.cid(4), instrument_id="binance:perp:BTCUSDT")
        )
    assert len(rig.ib.placed) == 2


async def test_fills_carry_commissions() -> None:
    """Fills are emitted with their commission, or after a short wait."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    trade = rig.ib.trades()[0]
    rig.ib.execute(trade, 4, 190.01, commission=1.25)
    rig.ib.execute(trade, 6, 190.0, commission=None)
    events = rig.adapter.drain_events(ACCOUNT)
    fills = [e.fill for e in events if e.fill]
    assert fills[0] is not None and fills[0].fee == D("1.25")
    assert len(fills) == 1
    await asyncio.sleep(0.05)
    late = [e.fill for e in rig.adapter.drain_events(ACCOUNT) if e.fill]
    assert late[0] is not None and late[0].fee == D(0)
    assert late[0].quantity_base == D(6) and late[0].liquidity == "taker"
    states = [e.order.status for e in events if e.order]
    assert S.PARTIALLY_FILLED in states
    listed = await rig.adapter.list_recent_fills(ACCOUNT)
    assert {f.price for f in listed} == {D("190.01"), D(190)}
    assert await rig.adapter.list_recent_fills(ACCOUNT, 2**62) == []
    assert await rig.adapter.list_open_orders(ACCOUNT) == []


async def test_cancel_paths() -> None:
    """Cancels confirm, conflict on IBKR errors or stay unknown."""
    rig = _Rig()
    first = rig.cid(1)
    await rig.adapter.submit_order(_req(first))
    state = await rig.adapter.cancel_order(ACCOUNT, first)
    assert state.status is S.CANCELED
    assert (await rig.adapter.cancel_order(ACCOUNT, first)).status is (
        S.CANCELED
    )
    assert len(rig.ib.canceled) == 1
    second = rig.cid(2)
    await rig.adapter.submit_order(_req(second))
    rig.ib.cancel_mode = "error"
    with pytest.raises(errors.ExecutionError) as info:
        await rig.adapter.cancel_order(ACCOUNT, second)
    assert info.value.code == "CONFLICT"
    third = rig.cid(3)
    await rig.adapter.submit_order(_req(third))
    rig.ib.cancel_mode = "hold"
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.cancel_order(ACCOUNT, third)
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.get_order_by_client_id(ACCOUNT, third)
    with pytest.raises(errors.ExecutionError) as missing:
        await rig.adapter.cancel_order(ACCOUNT, rig.cid(9))
    assert missing.value.code == "NOT_FOUND"


async def test_amend_modifies_the_working_order() -> None:
    """Amendments re-place the same IBKR order id."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    state = await rig.adapter.amend_order(ACCOUNT, cid, D(189), None, D(20))
    assert state.status is S.ACKNOWLEDGED
    order = rig.ib.trades()[0].order
    assert (order.lmtPrice, order.totalQuantity) == (D(189), 20)
    assert rig.ib.placed[-1] is order
    stop = rig.cid(2)
    await rig.adapter.submit_order(
        _req(
            stop,
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D(180),
        )
    )
    await rig.adapter.amend_order(ACCOUNT, stop, None, D(181), None)
    assert rig.ib.trades()[1].order.auxPrice == D(181)
    await rig.adapter.cancel_order(ACCOUNT, cid)
    with pytest.raises(errors.ExecutionError) as done:
        await rig.adapter.amend_order(ACCOUNT, cid, D(188), None, None)
    assert done.value.code == "CONFLICT"
    with pytest.raises(errors.ExecutionError) as missing:
        await rig.adapter.amend_order(ACCOUNT, rig.cid(7), D(1), None, None)
    assert missing.value.code == "NOT_FOUND"


async def test_query_by_client_id() -> None:
    """Orders are found by orderRef; absence is confirmed only if recent."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    found = await rig.adapter.get_order_by_client_id(ACCOUNT, cid)
    assert found is not None and found.status is S.ACKNOWLEDGED
    assert await rig.adapter.get_order_by_client_id(ACCOUNT, rig.cid(2)) is None
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.get_order_by_client_id(
            ACCOUNT, rig.cid(3, age_ms=13 * 3_600_000)
        )
    restarted = _Rig()
    restarted.ib = rig.ib
    restarted.factory.ib = rig.ib
    rig.ib._connected = False
    state = await restarted.adapter.get_order_by_client_id(ACCOUNT, cid)
    assert state is not None and state.client_order_id == cid


async def test_connectivity_codes_drive_health() -> None:
    """TWS connectivity codes map to adapter health and trigger resyncs."""
    rig = _Rig()
    await rig.adapter.capabilities(ACCOUNT)
    rig.ib.errorEvent.emit(-1, 1100, "Connectivity lost", None)
    assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
    rig.ib.requests.clear()
    rig.ib.errorEvent.emit(-1, 1102, "restored - data maintained", None)
    await asyncio.sleep(0)
    assert await rig.adapter.health() is enums.HealthState.HEALTHY
    assert "open" in rig.ib.requests
    rig.ib.errorEvent.emit(-1, 100, "Max rate exceeded", None)
    assert await rig.adapter.health() is enums.HealthState.DEGRADED
    rig.ib.errorEvent.emit(-1, 504, "Not connected", None)
    assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
    rig.ib.errorEvent.emit(-1, 2104, "Market data farm OK", None)
    rig.ib.errorEvent.emit(77, 202, "Order canceled", None)


async def test_reconnect_resyncs_missed_events() -> None:
    """After a disconnect the adapter reconnects and replays missed fills."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    rig.adapter.drain_events(ACCOUNT)
    rig.ib.connect_failures = 1
    rig.ib.disconnect()
    assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
    rig.ib.execute(rig.ib.trades()[0], 10, 190.01, emit=False)
    for _ in range(50):
        await asyncio.sleep(0.005)
        if await rig.adapter.health() is enums.HealthState.HEALTHY:
            break
    assert await rig.adapter.health() is enums.HealthState.HEALTHY
    assert len(rig.ib.connects) == 3
    events = rig.adapter.drain_events(ACCOUNT)
    assert any(e.fill is not None for e in events)
    assert any(e.order and e.order.status is S.FILLED for e in events)
    assert await rig.adapter.resync() == 0
    await rig.adapter.close()
    assert not rig.ib.isConnected()
    assert await rig.adapter.resync() == 0


async def test_bound_accounts_receive_orders_placed_before_restart() -> None:
    """Bound accounts route events of orders this process did not place."""
    first = _Rig()
    cid = first.cid(1)
    await first.adapter.submit_order(_req(cid))
    second = _Rig()
    second.ib = first.ib
    second.factory.ib = first.ib
    first.ib._connected = False
    assert second.adapter.bind(ACCOUNT)
    assert not second.adapter.bind("unknown")
    await second.adapter.capabilities(ACCOUNT)
    assert second.adapter.event_accounts() == [ACCOUNT]
    first.ib.execute(first.ib.trades()[0], 10, 190.0)
    events = second.adapter.drain_events(ACCOUNT)
    assert any(e.fill is not None for e in events)


async def test_account_summary_and_positions() -> None:
    """Balances and positions map from the IBKR account summary."""
    rig = _Rig()
    rig.ib.summary = [
        fake_ib.FakeAccountValue(NUMBER, "NetLiquidation", "25000.5", "USD"),
        fake_ib.FakeAccountValue(NUMBER, "AvailableFunds", "12000", "USD"),
        fake_ib.FakeAccountValue(NUMBER, "SettledCash", "9000", "USD"),
        fake_ib.FakeAccountValue(NUMBER, "TotalCashValue", "10000", "USD"),
        fake_ib.FakeAccountValue("U999", "NetLiquidation", "1", "USD"),
    ]
    snap = await rig.adapter.account_snapshot(ACCOUNT)
    assert (snap.equity, snap.available_margin) == (D("25000.5"), D(12000))
    assert (snap.settled_cash, snap.unsettled_cash) == (D(9000), D(1000))
    rig.ib.summary = rig.ib.summary[:1]
    with pytest.raises(errors.AdapterUnavailableError):
        await rig.adapter.account_snapshot(ACCOUNT)
    await rig.adapter.submit_order(
        _req(rig.cid(1), instrument_id=fixtures.SPY, limit_price=D(580))
    )
    spy = rig.ib.trades()[0].contract
    rig.ib.position_rows = [
        fake_ib.FakePosition(NUMBER, spy, 5, 579.5),
        fake_ib.FakePosition(
            NUMBER, fake_ib.FakeContract("BRK B", "NYSE", conId=1), 2, 400.0
        ),
        fake_ib.FakePosition(
            NUMBER, fake_ib.FakeContract("QQQ", "NASDAQ", conId=2), -3, 1.0
        ),
        fake_ib.FakePosition(
            NUMBER, fake_ib.FakeContract("XYZ", "BATS", conId=3), 1, 1.0
        ),
        fake_ib.FakePosition(
            NUMBER, fake_ib.FakeContract("OTC", "PINK", conId=4), 1, 1.0
        ),
    ]
    rows = {
        p.instrument_id: p.signed_qty
        for p in await rig.adapter.list_positions(ACCOUNT)
    }
    assert rows == {
        fixtures.SPY: D(5),
        "nyse:stock:BRK.B": D(2),
        "nasdaq:stock:QQQ": D(-3),
        "cboe:stock:XYZ": D(1),
    }


async def test_streams_buffered_events() -> None:
    """The private stream yields buffered events."""
    rig = _Rig()
    await rig.adapter.submit_order(_req(rig.cid(1)))
    event = await anext(rig.adapter.stream_account_events(ACCOUNT))
    assert event.order is not None


async def test_pacer_limits_requests_per_window() -> None:
    """The pacer waits once the window is full."""
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        """Advances fake time.

        Args:
            seconds: Delay.
        """
        slept.append(seconds)
        now[0] += seconds

    pacer = base.Pacer(2, 1.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        await pacer.acquire()
    assert pacer.waits == 1 and slept == [1.0]
    await base.Pacer(0, 1.0).acquire()


def test_real_factory_requires_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ib_async factory fails closed when the extra is missing."""

    def missing(name: str) -> object:
        """Raises ImportError.

        Args:
            name: Module.

        Raises:
            ImportError: Always.
        """
        raise ImportError(name)

    monkeypatch.setattr(ibkr.importlib, "import_module", missing)
    with pytest.raises(errors.AdapterUnavailableError):
        ibkr.IbAsyncFactory()


def test_real_factory_builds_ib_async_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory forwards to ib_async constructors."""
    module = types.SimpleNamespace(
        IB=lambda: "client",
        Stock=lambda *a, **k: ("stock", a, k),
        Order=lambda **k: ("order", k),
        ExecutionFilter=lambda **k: ("filter", k),
    )
    monkeypatch.setattr(ibkr.importlib, "import_module", lambda name: module)
    factory = ibkr.IbAsyncFactory()
    assert factory.client() == "client"
    assert factory.stock("AAPL", "NASDAQ") == (
        "stock",
        ("AAPL", "SMART", "USD"),
        {"primaryExchange": "NASDAQ"},
    )
    assert factory.order({"tif": "DAY"}) == ("order", {"tif": "DAY"})
    assert factory.execution_filter("DU1") == ("filter", {"acctCode": "DU1"})
