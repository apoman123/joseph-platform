# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the SinoPac Shioaji adapter against a fake SDK."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import decimal
import types
from typing import Any

import pytest

from joseph_execution.adapters import market_static
from joseph_execution.adapters.brokers import sinopac
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_shioaji

D = decimal.Decimal
S = enums.OrderState
MONDAY = datetime.date(2026, 11, 23)
ACCOUNT = "acct-sp"
NUMBER = "9A95-0000001"
CONFIG = sinopac.SinopacConfig(
    api_key="key-123456",
    secret_key="secret-123456",
    ca_path="/run/secrets/Sinopac.pfx",
    ca_password="ca-pass-123",
    person_id="A123456789",
)


class _Rig:
    """Adapter wired to a fake Shioaji.

    Attributes:
        adapter: Adapter under test.
        api: Fake SDK client.
        factory: Fake factory.
        clock: Clock.
        market: Static market data (given to the adapter on request).
    """

    def __init__(
        self,
        config: sinopac.SinopacConfig | None = CONFIG,
        gate: str | None = None,
        market: bool = False,
    ) -> None:
        """Builds the rig.

        Args:
            config: Adapter settings.
            gate: Gate reason.
            market: Give the adapter market data (TWSE/TPEx fixtures with
                00679B as a BOND ETF).
        """
        self.clock = clock_lib.ManualClock(fixtures.taipei(MONDAY, 10))
        self.api = fake_shioaji.FakeShioaji()
        self.factory = fake_shioaji.FakeSjFactory(self.api)
        self.market = market_static.StaticMarketDataGateway(self.clock)
        fixtures.install(self.market, MONDAY - datetime.timedelta(days=7))
        self.adapter = sinopac.SinopacAdapter(
            config,
            lambda: self.factory,
            self.clock,
            {ACCOUNT: NUMBER}.get,
            gate,
            runner=fake_shioaji.run_inline,
            market_data=self.market if market else None,
        )

    def cid(self, n: int, age_ms: int = 0) -> str:
        """Builds a Joseph client id.

        Args:
            n: Distinguishing number.
            age_ms: Age.

        Returns:
            Client id.
        """
        return f"j{self.clock.now_ms() - age_ms:012x}{n:018x}"


def _req(cid: str, **overrides: object) -> trading.VenueOrderRequest:
    """Builds a TWSE board-lot limit buy.

    Args:
        cid: Client id.
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": ACCOUNT,
        "instrument_id": fixtures.TSMC,
        "client_order_id": cid,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D(2000),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "limit_price": D(600),
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]


async def _settle() -> None:
    """Lets threadsafe callbacks run."""
    for _ in range(3):
        await asyncio.sleep(0)


async def test_gated_and_unconfigured_adapters_never_log_in() -> None:
    """Gated or unconfigured adapters refuse without creating a client."""
    for rig in (_Rig(gate="live trading is disabled"), _Rig(config=None)):
        caps = await rig.adapter.capabilities(ACCOUNT)
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await rig.adapter.submit_order(_req(rig.cid(1)))
        assert info.value.code == "LIVE_TRADING_DISABLED"
        for call in (
            rig.adapter.cancel_order(ACCOUNT, "x"),
            rig.adapter.amend_order(ACCOUNT, "x", D(1), None, None),
            rig.adapter.get_order_by_client_id(ACCOUNT, "x"),
            rig.adapter.account_snapshot(ACCOUNT),
            rig.adapter.list_positions(ACCOUNT),
            rig.adapter.list_open_orders(ACCOUNT),
            rig.adapter.list_recent_fills(ACCOUNT),
        ):
            with pytest.raises(errors.AdapterUnavailableError):
                await call
        with pytest.raises(errors.AdapterUnavailableError):
            await anext(rig.adapter.stream_account_events(ACCOUNT))
        assert not rig.adapter.bind(ACCOUNT)
        assert await rig.adapter.resync() == 0
        assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
        assert rig.factory.created == 0 and rig.api.calls == []
    unconfigured = await _Rig(config=None).adapter.capabilities(ACCOUNT)
    assert "JOSEPH_SINOPAC_API_KEY" in unconfigured.notes[-1]
    assert "secret-123456" not in repr(CONFIG)


async def test_login_activates_the_ca_and_negotiates() -> None:
    """Login and CA activation precede trading; simulation is reported."""
    rig = _Rig()
    caps = await rig.adapter.capabilities(ACCOUNT)
    assert caps.status is enums.TradingStatus.AVAILABLE
    assert caps.tradable_venues == ("twse", "tpex") and caps.odd_lot
    assert caps.simulation and rig.api.simulation
    assert rig.api.calls[:2] == ["login", "activate_ca"]
    assert await rig.adapter.health() is enums.HealthState.HEALTHY
    assert rig.adapter.bind(ACCOUNT) and not rig.adapter.bind("nobody")
    missing = await rig.adapter.capabilities("nobody")
    assert "no SinoPac account number" in missing.notes[-1]


async def test_production_requires_ca_and_signing() -> None:
    """Production needs the CA and a signed (simulation-tested) account."""
    production = dataclasses.replace(CONFIG, simulation=False)
    rig = _Rig(production)
    rig.api.accounts[0].signed = False
    caps = await rig.adapter.capabilities(ACCOUNT)
    assert "simulation-mode test" in caps.notes[-1]
    no_ca = _Rig(dataclasses.replace(production, ca_path=None))
    caps = await no_ca.adapter.capabilities(ACCOUNT)
    assert "CA certificate" in caps.notes[-1]
    refused = _Rig(production)
    refused.api.ca_ok = False
    assert "refused" in (await refused.adapter.capabilities(ACCOUNT)).notes[-1]
    broken = _Rig()
    broken.api.login_failures = 1
    assert (
        "login failed" in (await broken.adapter.capabilities(ACCOUNT)).notes[-1]
    )
    other = _Rig()
    other.api.accounts = [fake_shioaji.FakeAccount(account_id="9")]
    assert "***0001" in (await other.adapter.capabilities(ACCOUNT)).notes[-1]


async def test_ca_activation_errors_are_reported() -> None:
    """An SDK error during CA activation keeps trading unavailable."""
    rig = _Rig()

    def boom(**kwargs: object) -> bool:
        """Raises like a wrong CA password.

        Args:
            **kwargs: Ignored.

        Raises:
            ValueError: Always.
        """
        raise ValueError("bad password")

    rig.api.activate_ca = boom  # type: ignore[method-assign]
    caps = await rig.adapter.capabilities(ACCOUNT)
    assert "CA activation failed" in caps.notes[-1]


def test_order_mapping() -> None:
    """Board lots, odd lots, price types and ROD/IOC/FOK map to Shioaji."""
    common = sinopac.order_fields(_req("j1"), 1000, "acct")
    assert common == {
        "action": "Buy",
        "price": 600.0,
        "quantity": 2,
        "price_type": "LMT",
        "time_in_force": "ROD",
        "order_lot": "Common",
        "order_cond": "Cash",
        "custom_field": sinopac.tag("j1"),
        "account": "acct",
    }
    odd = sinopac.order_fields(_req("j2", quantity_base=D(300)), 1000, "a")
    assert (odd["order_lot"], odd["quantity"]) == ("IntradayOdd", 300)
    market = sinopac.order_fields(
        _req(
            "j3",
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
            side=enums.OrderSide.SELL,
        ),
        1000,
        "a",
    )
    assert (market["price_type"], market["price"], market["action"]) == (
        "MKT",
        0.0,
        "Sell",
    )
    fok = sinopac.order_fields(
        _req("j4", time_in_force=enums.TimeInForce.FOK), 1000, "a"
    )
    assert fok["time_in_force"] == "FOK"
    for bad in (
        _req("b1", time_in_force=enums.TimeInForce.GTC),
        _req(
            "b2",
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D(590),
        ),
        _req("b3", quantity_base=D(1500)),
        _req("b4", quantity_base=D(300), time_in_force=enums.TimeInForce.IOC),
        _req("b5", limit_price=D("600.1234567890123456789")),
    ):
        with pytest.raises(errors.AdapterUnavailableError):
            sinopac.order_fields(bad, 1000, "a")


def test_tags_and_helpers() -> None:
    """Tags fit Shioaji's 6-character custom_field; helpers map values."""
    tag = sinopac.tag("j0193f0a0b1c2777777777777777777")
    assert len(tag) == 6 and tag.isalnum() and tag.isupper()
    assert sinopac.tag("a") != sinopac.tag("b")
    assert sinopac.text(None) == ""
    assert sinopac.text(types.SimpleNamespace(value="Filled")) == "Filled"
    assert sinopac.shares(2, "Common", 1000) == D(2000)
    assert sinopac.shares(7, "IntradayOdd", 1000) == D(7)
    assert sinopac.market_type("0050") == "etf"
    assert sinopac.market_type("2330") == "stock"
    inactive = types.SimpleNamespace(
        status=types.SimpleNamespace(status="Inactive", deal_quantity=0)
    )
    assert sinopac.map_status(inactive) is None


async def test_submit_and_callbacks_report_order_and_deals() -> None:
    """Orders acknowledge via the callback; deals become share fills."""
    rig = _Rig()
    cid = rig.cid(1)
    result = await rig.adapter.submit_order(_req(cid))
    assert result.accepted and result.state.status is S.ACKNOWLEDGED
    placed = rig.api.trades[0]
    assert (placed.order.quantity, placed.order.order_lot) == (2, "Common")
    again = await rig.adapter.submit_order(_req(cid))
    assert again.accepted and len(rig.api.trades) == 1
    rig.api.deal(placed, 1, 600.0, "1001")
    rig.api.deal(placed, 1, 599.0, "1002")
    rig.api.deal(placed, 1, 599.0, "1002")
    await _settle()
    events = rig.adapter.drain_events(ACCOUNT)
    fills = [e.fill for e in events if e.fill]
    assert [f.quantity_base for f in fills if f] == [D(1000), D(1000)]
    assert fills[0] is not None and fills[0].fee == D(855)
    statuses = [e.order.status for e in events if e.order]
    assert statuses == [S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED]
    listed = await rig.adapter.list_recent_fills(ACCOUNT)
    assert len(listed) == 2
    assert await rig.adapter.list_open_orders(ACCOUNT) == []
    rig.api.order_cb("FDEAL", {})  # type: ignore[misc]
    rig.api.order_cb("SDEAL", {"trade_id": "unknown"})  # type: ignore[misc]
    await _settle()


async def test_odd_lot_orders_are_in_shares() -> None:
    """Intraday odd-lot orders send and fill share quantities."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid, quantity_base=D(300)))
    trade = rig.api.trades[0]
    assert (trade.order.order_lot, trade.order.quantity) == ("IntradayOdd", 300)
    rig.api.deal(trade, 300, 601.0, "7")
    await _settle()
    fill = next(e.fill for e in rig.adapter.drain_events(ACCOUNT) if e.fill)
    assert fill.quantity_base == D(300)
    with pytest.raises(errors.ExecutionError) as info:
        await rig.adapter.amend_order(ACCOUNT, cid, D(602), None, None)
    assert info.value.code == "UNSUPPORTED"


async def test_rejections_and_errors() -> None:
    """Rejections come back as REJECTED; SDK errors keep their meaning."""
    rig = _Rig()
    rig.api.reject_next = "price outside limit"
    rejected = await rig.adapter.submit_order(_req(rig.cid(1)))
    assert not rejected.accepted and rejected.state.status is S.REJECTED
    await _settle()
    events = rig.adapter.drain_events(ACCOUNT)
    assert events[-1].order is not None
    assert events[-1].order.reason == "price outside limit"
    refused_type = type("AccountNotSignError", (Exception,), {})
    rig.api.place_error = refused_type("not signed")
    with pytest.raises(errors.AdapterUnavailableError):
        await rig.adapter.submit_order(_req(rig.cid(2)))
    unknown_type = type("OrderOutcomeUnknownError", (Exception,), {})
    rig.api.place_error = unknown_type("no ack")
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.submit_order(_req(rig.cid(3)))
    for instrument in (
        "twse:stock:6488",
        "twse:stock:9999",
        "tpex:stock:2330",
        "tpex:stock:7777",
        fixtures.AAPL,
    ):
        with pytest.raises(errors.AdapterUnavailableError):
            await rig.adapter.submit_order(
                _req(rig.cid(4), instrument_id=instrument)
            )


async def test_tpex_orders_use_the_otc_contract() -> None:
    """TPEx ids resolve in Stocks.OTC; fills carry Taiwan fees and tax."""
    rig = _Rig()
    stock = rig.cid(1)
    result = await rig.adapter.submit_order(
        _req(stock, instrument_id=fixtures.GLOBALWAFERS, limit_price=D(460))
    )
    assert result.accepted
    trade = rig.api.trades[0]
    assert (trade.contract.code, trade.contract.exchange) == ("6488", "OTC")
    assert (trade.order.order_lot, trade.order.quantity) == ("Common", 2)
    rig.api.deal(trade, 1, 460.0, "1")
    await _settle()
    fill = next(e.fill for e in rig.adapter.drain_events(ACCOUNT) if e.fill)
    assert fill.quantity_base == D(1000) and fill.fee == D(655)
    odd = rig.cid(2)
    await rig.adapter.submit_order(
        _req(
            odd,
            instrument_id=fixtures.YUANTA_US20,
            quantity_base=D(500),
            limit_price=D("26.15"),
            side=enums.OrderSide.SELL,
        )
    )
    bond = rig.api.trades[1]
    assert (bond.contract.code, bond.contract.exchange) == ("00679B", "OTC")
    assert (bond.order.order_lot, bond.order.quantity) == ("IntradayOdd", 500)
    second = _Rig()
    second.api = rig.api
    second.factory.api = rig.api
    found = await second.adapter.get_order_by_client_id(ACCOUNT, odd)
    assert found is not None
    assert second.adapter._tracked[odd].instrument_id == fixtures.YUANTA_US20


def test_exchange_mapping_helpers() -> None:
    """Shioaji exchanges map to venues; the emerging board is not routed."""
    assert sinopac.venue_of("TSE") == "twse"
    assert sinopac.venue_of("OTC") == "tpex"
    assert sinopac.venue_of("OES") is None
    assert sinopac.instrument_id("00679B", "OTC") == fixtures.YUANTA_US20
    assert sinopac.instrument_id("2330", "TSE") == fixtures.TSMC
    assert sinopac.instrument_id("2330", "") is None
    assert sinopac.instrument_id("7777", "OES") is None
    info = types.SimpleNamespace(base=types.SimpleNamespace(exchange="OTC"))
    assert sinopac.exchange_of(info) == "OTC"
    assert sinopac.exchange_of(None) == ""
    emerging, note = sinopac.unmanaged_holding("7777", "OES")
    assert emerging == "oes:stock:7777" and "興櫃" in note
    unknown, note = sinopac.unmanaged_holding("9999", "")
    assert unknown == "unknown:stock:9999"
    assert "not in Shioaji's contract list" in note
    other, note = sinopac.unmanaged_holding("01234T", "XYZ")
    assert other == "xyz:stock:01234T" and "exchange XYZ" in note


async def test_unresolved_holdings_are_reported_unmanaged() -> None:
    """Emerging-board and unknown codes are unmanaged, never ``twse``."""
    rig = _Rig()
    rig.api.position_rows = [
        fake_shioaji.FakeStockPosition("2330", "Buy", 1000, 590.0, 600.0),
        fake_shioaji.FakeStockPosition("00679B", "Buy", 2000, 26.0, 26.1),
        fake_shioaji.FakeStockPosition("7777", "Buy", 3000, 35.0, 36.0),
        fake_shioaji.FakeStockPosition("9999", "Buy", 500, 10.0, 10.0),
    ]
    positions = {
        p.instrument_id: p for p in await rig.adapter.list_positions(ACCOUNT)
    }
    assert sorted(positions) == [
        "oes:stock:7777",
        fixtures.YUANTA_US20,
        fixtures.TSMC,
        "unknown:stock:9999",
    ]
    assert positions[fixtures.TSMC].managed
    assert positions[fixtures.YUANTA_US20].managed
    emerging = positions["oes:stock:7777"]
    assert not emerging.managed and emerging.signed_qty == D(3000)
    assert "emerging-board (興櫃)" in (emerging.unmanaged_note or "")
    missing = positions["unknown:stock:9999"]
    assert not missing.managed
    assert "exchange is unknown" in (missing.unmanaged_note or "")
    snap = await rig.adapter.account_snapshot(ACCOUNT)
    held = 600_000 + 52_200 + 108_000 + 5_000
    assert snap.equity == D(1_000_000 - 90_000 + held)


async def test_bond_etf_fills_follow_the_tax_window() -> None:
    """Fill fees skip the sale tax on BOND ETFs only through 2026-12-31."""
    rig = _Rig(market=True)
    cid = rig.cid(1)
    await rig.adapter.submit_order(
        _req(
            cid,
            instrument_id=fixtures.YUANTA_US20,
            side=enums.OrderSide.SELL,
            limit_price=D(26),
        )
    )
    trade = rig.api.trades[0]
    last_day = fixtures.taipei(datetime.date(2026, 12, 31), 13) / 1000
    first_taxed = fixtures.taipei(datetime.date(2027, 1, 1), 9, 30) / 1000
    rig.api.deal(trade, 1, 26.0, "1", ts=last_day)
    rig.api.deal(trade, 1, 26.0, "2", ts=first_taxed)
    await _settle()
    fees = [e.fill.fee for e in rig.adapter.drain_events(ACCOUNT) if e.fill]
    assert fees == [D(37), D(37 + 26)]
    blind = _Rig()
    other = blind.cid(1)
    await blind.adapter.submit_order(
        _req(
            other,
            instrument_id=fixtures.YUANTA_US20,
            side=enums.OrderSide.SELL,
            limit_price=D(26),
        )
    )
    blind.api.deal(blind.api.trades[0], 1, 26.0, "1", ts=last_day)
    await _settle()
    unclassified = next(
        e.fill for e in blind.adapter.drain_events(ACCOUNT) if e.fill
    )
    assert unclassified.fee == D(37 + 26)


async def test_cancel_paths() -> None:
    """Cancels confirm via the SDK, fail, or stay pending for reconcile."""
    rig = _Rig()
    first = rig.cid(1)
    await rig.adapter.submit_order(_req(first))
    state = await rig.adapter.cancel_order(ACCOUNT, first)
    assert state.status is S.CANCELED
    assert (await rig.adapter.cancel_order(ACCOUNT, first)).status is (
        S.CANCELED
    )
    second = rig.cid(2)
    await rig.adapter.submit_order(_req(second))
    rig.api.cancel_mode = "hold"
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.cancel_order(ACCOUNT, second)
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.get_order_by_client_id(ACCOUNT, second)
    rig.clock.advance(31_000)
    still = await rig.adapter.get_order_by_client_id(ACCOUNT, second)
    assert still is not None and still.status is S.ACKNOWLEDGED
    third = rig.cid(3)
    await rig.adapter.submit_order(_req(third))
    rig.api.cancel_mode = "fail"
    with pytest.raises(errors.AdapterTimeoutError):
        await rig.adapter.cancel_order(ACCOUNT, third)
    await _settle()
    state = await rig.adapter.get_order_by_client_id(ACCOUNT, third)
    assert state is not None and state.status is S.ACKNOWLEDGED
    with pytest.raises(errors.ExecutionError) as missing:
        await rig.adapter.cancel_order(ACCOUNT, rig.cid(9))
    assert missing.value.code == "NOT_FOUND"


async def test_cancel_events_and_partial_cancels() -> None:
    """A cancel event after partial fills reports CANCELED with fills."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    trade = rig.api.trades[0]
    rig.api.deal(trade, 1, 600.0, "1")
    await rig.adapter.cancel_order(ACCOUNT, cid)
    await _settle()
    events = rig.adapter.drain_events(ACCOUNT)
    final = [e.order for e in events if e.order][-1]
    assert final is not None and final.status is S.CANCELED
    assert final.filled_base == D(1000)


async def test_amend_price_and_quantity() -> None:
    """Prices change and quantities only shrink, in whole lots."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid, quantity_base=D(3000)))
    state = await rig.adapter.amend_order(ACCOUNT, cid, D(599), None, D(2000))
    assert state.status is S.ACKNOWLEDGED
    trade = rig.api.trades[0]
    assert (trade.order.price, trade.order.quantity) == (599.0, 2)
    for limit, trigger, qty, code in (
        (None, D(590), None, "UNSUPPORTED"),
        (None, None, D(5000), "UNSUPPORTED"),
        (None, None, D(1500), "INVALID_LOT_SIZE"),
    ):
        with pytest.raises(errors.ExecutionError) as info:
            await rig.adapter.amend_order(ACCOUNT, cid, limit, trigger, qty)
        assert info.value.code == code
    with pytest.raises(errors.ExecutionError) as missing:
        await rig.adapter.amend_order(ACCOUNT, rig.cid(5), D(1), None, None)
    assert missing.value.code == "NOT_FOUND"


async def test_amend_failure_events_conflict() -> None:
    """A failed update reported by the callback raises a conflict."""
    rig = _Rig()
    cid = rig.cid(1)
    await rig.adapter.submit_order(_req(cid))
    original = rig.api.update_order

    def failing(trade: Any, price: Any = None, qty: Any = None) -> Any:
        """Reports a failed price update.

        Args:
            trade: Trade.
            price: Price.
            qty: Quantity.

        Returns:
            Trade.
        """
        del price, qty
        rig.api._order_event(trade, "UpdatePrice", "77", "locked")
        return trade

    rig.api.update_order = failing  # type: ignore[method-assign]
    # Deliver callbacks synchronously, as an SDK reply would before return.
    rig.api.set_order_callback(rig.adapter._on_order)
    with pytest.raises(errors.ExecutionError) as info:
        await rig.adapter.amend_order(ACCOUNT, cid, D(601), None, None)
    assert info.value.code == "CONFLICT"
    rig.api.update_order = original  # type: ignore[method-assign]


async def test_restart_finds_orders_by_tag() -> None:
    """After a restart orders are found by custom_field; absence is recent."""
    first = _Rig()
    cid = first.cid(1)
    await first.adapter.submit_order(_req(cid))
    second = _Rig()
    second.api = first.api
    second.factory.api = first.api
    found = await second.adapter.get_order_by_client_id(ACCOUNT, cid)
    assert found is not None and found.status is S.ACKNOWLEDGED
    assert (
        await second.adapter.get_order_by_client_id(ACCOUNT, second.cid(2))
        is None
    )
    with pytest.raises(errors.AdapterTimeoutError):
        await second.adapter.get_order_by_client_id(
            ACCOUNT, second.cid(3, age_ms=13 * 3_600_000)
        )
    trade = first.api.trades[0]
    trade.status.status = "Filled"
    trade.status.deal_quantity = 2
    assert await second.adapter.resync() == 1
    events = second.adapter.drain_events(ACCOUNT)
    assert events[0].order is not None
    assert events[0].order.status is S.FILLED
    assert events[0].order.filled_base == D(2000)
    second.adapter.bind(ACCOUNT)
    await second.adapter.close()
    assert "logout" in first.api.calls


async def test_balances_positions_and_pacing() -> None:
    """Balances net pending settlements and respect the trading limit."""
    rig = _Rig()
    rig.api.position_rows = [
        fake_shioaji.FakeStockPosition("2330", "Buy", 1500, 590.0, 600.0),
        fake_shioaji.FakeStockPosition("0050", "Sell", 10, 150.0, 151.0),
        fake_shioaji.FakeStockPosition("6488", "Buy", 0, 440.0, 460.0),
    ]
    snap = await rig.adapter.account_snapshot(ACCOUNT)
    assert snap.currency == "TWD"
    assert snap.settled_cash == D(1_000_000)
    assert snap.unsettled_cash == D(-90_000)
    assert snap.available_margin == D(910_000)
    assert snap.equity == D(1_000_000 - 90_000 + 900_000 - 1510)
    cached = await rig.adapter.account_snapshot(ACCOUNT)
    assert cached is snap
    rig.clock.advance(6_000)
    rig.api.limit = 100_000
    capped = await rig.adapter.account_snapshot(ACCOUNT)
    assert capped.available_margin == D(100_000)
    positions = await rig.adapter.list_positions(ACCOUNT)
    assert {p.instrument_id: p.signed_qty for p in positions} == {
        fixtures.TSMC: D(1500),
        fixtures.YUANTA_50: D(-10),
        fixtures.GLOBALWAFERS: D(0),
    }
    assert "list_positions:Share" in rig.api.calls


async def test_session_events_drive_health() -> None:
    """Session events update health and resync after reconnects."""
    rig = _Rig()
    await rig.adapter.capabilities(ACCOUNT)
    rig.api.session_event(12)
    await _settle()
    assert await rig.adapter.health() is enums.HealthState.DEGRADED
    rig.api.session_event(1)
    await _settle()
    assert await rig.adapter.health() is enums.HealthState.UNAVAILABLE
    rig.api.session_event(13)
    await _settle()
    assert await rig.adapter.health() is enums.HealthState.HEALTHY
    event = None
    await rig.adapter.submit_order(_req(rig.cid(1)))
    await _settle()
    async for item in rig.adapter.stream_account_events(ACCOUNT):
        event = item
        break
    assert event is not None


async def test_threaded_runner_and_real_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default runner uses a thread; the factory needs the extra."""
    assert await sinopac._thread_runner(lambda: 41 + 1) == 42

    def missing(name: str) -> object:
        """Raises ImportError.

        Args:
            name: Module.

        Raises:
            ImportError: Always.
        """
        raise ImportError(name)

    monkeypatch.setattr(sinopac.importlib, "import_module", missing)
    with pytest.raises(errors.AdapterUnavailableError):
        sinopac.ShioajiFactory()
    module = types.SimpleNamespace(
        Shioaji=lambda simulation: ("api", simulation),
        StockOrder=lambda **k: ("order", k),
        Unit=types.SimpleNamespace(Share="SHARE"),
    )
    monkeypatch.setattr(sinopac.importlib, "import_module", lambda n: module)
    factory = sinopac.ShioajiFactory()
    assert factory.client(True) == ("api", True)
    assert factory.stock_order({"price": 1.0}) == ("order", {"price": 1.0})
    assert factory.share_unit() == "SHARE"
