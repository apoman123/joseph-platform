"""Integration tests: securities through the Risk Gateway, OMS and API."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
from collections.abc import Callable
from collections.abc import Iterator

import pytest
from fastapi import testclient

from joseph_execution import bootstrap
from joseph_execution.adapters import vault
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import intents
from joseph_execution.platform import config
from joseph_execution.testing import equities as fixtures
from joseph_execution.testing import fake_ib
from joseph_execution.testing import harness

D = decimal.Decimal
S = enums.OrderState
EXEC = "execution.schema.json"
MONDAY = datetime.date(2026, 11, 23)
TWD_A = accounts.paper_account_id("tenant-a", "TWD")
Validate = Callable[[str, str, object], None]


def _build(**securities: object) -> harness.Harness:
    """Builds a harness with the TWD paper account and securities data.

    Args:
        **securities: ``SecuritiesSettings`` overrides.

    Returns:
        Harness with the clock inside Monday's TWSE session.
    """
    sec = config.SecuritiesSettings(
        paper_twd_starting_cash=D(3_000_000),
        **securities,  # type: ignore[arg-type]
    )
    h = harness.build(securities=sec)
    h.clock.current_ms = fixtures.taipei(MONDAY, 10)
    fixtures.install(h.market, MONDAY - datetime.timedelta(days=7))
    return h


def _twse(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a PAPER board-lot limit buy of 2330.

    Args:
        **overrides: Field overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": TWD_A,
        "instrument_id": fixtures.TSMC,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "limit_price": D(601),
        "quantity_base": D(1000),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "mode": enums.OrderMode.PAPER,
        "source": enums.IntentSource.MANUAL_TICKET,
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]


async def test_twse_paper_order_fills_in_shares() -> None:
    """A TWSE board-lot order fills in session with TWD cash accounting."""
    h = _build()
    oms = h.container.services.oms
    result = await oms.submit_intent(harness.TENANT_A, _twse(), "tw-1")
    assert result.decision.allowed, result.decision.failures()
    assert result.orders[0].state is S.FILLED
    positions = await oms.list_positions(harness.TENANT_A, TWD_A)
    assert positions[0]["quantity_base"] == "1000"
    fill = oms.list_fills(harness.TENANT_A, TWD_A)[0]
    assert fill.fee == D(856) and fill.fee_asset == "TWD"
    summary = await h.container.services.accounts.summary(
        harness.TENANT_A, TWD_A
    )
    assert summary["currency"] == "TWD"
    assert summary["unsettled_cash"] == "-601856"
    assert summary["settled_cash"] == "3000000"


async def test_tpex_paper_orders_follow_taiwan_rules() -> None:
    """TPEx orders fill in the TWD paper account with Taiwan fees and lots."""
    h = _build()
    oms = h.container.services.oms
    stock = _twse(instrument_id=fixtures.GLOBALWAFERS, limit_price=D(462))
    result = await oms.submit_intent(harness.TENANT_A, stock, "tp-1")
    assert result.decision.allowed, result.decision.failures()
    assert result.orders[0].state is S.FILLED
    fill = oms.list_fills(harness.TENANT_A, TWD_A)[0]
    assert fill.fee_asset == "TWD"
    assert fill.fee == (fill.price * 1000 * D("0.001425")).to_integral_value(
        decimal.ROUND_FLOOR
    )
    beyond = _twse(instrument_id=fixtures.GLOBALWAFERS, limit_price=D(486))
    refused = await oms.submit_intent(harness.TENANT_A, beyond, "tp-2")
    assert not refused.decision.allowed
    unlimited = _twse(
        instrument_id=fixtures.YUANTA_US20,
        limit_price=D("26.2"),
        quantity_base=D(500),
    )
    bond = await oms.submit_intent(harness.TENANT_A, unlimited, "tp-3")
    assert bond.decision.allowed, bond.decision.failures()
    usd = _twse(account_id=harness.PAPER_A, instrument_id=fixtures.YUANTA_US20)
    wrong = await oms.submit_intent(harness.TENANT_A, usd, "tp-4")
    assert not wrong.decision.allowed


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"account_id": harness.PAPER_A}, "UNSUPPORTED"),
        ({"instrument_id": fixtures.UST10Y, "limit_price": D("4.25")}, None),
        ({"quantity_base": D(1500)}, "INVALID_LOT_SIZE"),
        ({"limit_price": D(661)}, "RISK_LIMIT"),
        ({"limit_price": D("600.5")}, "INVALID_TICK_SIZE"),
        ({"leverage": D(2)}, "RISK_LIMIT"),
    ],
)
async def test_securities_rules_reject_orders(
    overrides: dict[str, object], code: str | None
) -> None:
    """Routing, lots, limits, ticks and leverage reject invalid orders."""
    h = _build()
    result = await h.container.services.oms.submit_intent(
        harness.TENANT_A, _twse(**overrides), "bad"
    )
    assert not result.decision.allowed
    if code is not None:
        assert result.decision.primary_code() == code
    assert result.orders == ()


async def test_odd_lots_and_long_only_selling() -> None:
    """Odd lots trade as limit DAY orders; selling is capped by holdings."""
    h = _build()
    oms = h.container.services.oms
    odd = await oms.submit_intent(
        harness.TENANT_A, _twse(quantity_base=D(300)), "odd"
    )
    assert odd.decision.allowed and odd.orders[0].state is S.FILLED
    oversell = await oms.submit_intent(
        harness.TENANT_A,
        _twse(side=enums.OrderSide.SELL, quantity_base=D(1000)),
        "oversell",
    )
    assert oversell.decision.primary_code() == "VALIDATION_FAILED"
    sell = await oms.submit_intent(
        harness.TENANT_A,
        _twse(
            side=enums.OrderSide.SELL, quantity_base=D(300), limit_price=D(599)
        ),
        "sell",
    )
    assert sell.decision.allowed and sell.orders[0].state is S.FILLED


async def test_closed_market_rejects_market_orders_and_rests_limits() -> None:
    """While closed market orders are rejected and limits wait for the open."""
    h = _build()
    oms = h.container.services.oms
    h.clock.current_ms = fixtures.taipei(MONDAY, 15)
    market_order = _twse(
        order_type=enums.OrderType.MARKET,
        limit_price=None,
        time_in_force=enums.TimeInForce.IOC,
    )
    rejected = await oms.submit_intent(harness.TENANT_A, market_order, "m")
    assert rejected.decision.decision is enums.Decision.REJECT
    resting = await oms.submit_intent(harness.TENANT_A, _twse(), "rest")
    assert resting.decision.allowed
    assert resting.orders[0].state is S.ACKNOWLEDGED
    h.clock.current_ms = fixtures.taipei(MONDAY + datetime.timedelta(days=1), 9)
    await h.pump()
    order = oms.get_order(harness.TENANT_A, resting.orders[0].order_id)
    assert order.state is S.FILLED


async def test_queue_policy_queues_day_market_orders() -> None:
    """With the queue policy a DAY market order fills at the next open."""
    h = _build(closed_market_orders="queue")
    assert h.container.services.oms.settings.queue_closed_market_orders
    oms = h.container.services.oms
    h.clock.current_ms = fixtures.taipei(MONDAY, 15)
    day_market = _twse(order_type=enums.OrderType.MARKET, limit_price=None)
    queued = await oms.submit_intent(harness.TENANT_A, day_market, "q")
    assert queued.decision.allowed
    warnings = [
        c.message for c in queued.decision.checks if c.outcome.value == "WARN"
    ]
    assert any("queues as DAY" in w for w in warnings)
    h.clock.current_ms = fixtures.taipei(MONDAY + datetime.timedelta(days=1), 9)
    await h.pump()
    order = oms.get_order(harness.TENANT_A, queued.orders[0].order_id)
    assert order.state is S.FILLED


async def test_close_requests_respect_board_lots() -> None:
    """Closes cover whole board lots; odd remainders close with limits."""
    h = _build()
    oms = h.container.services.oms
    await oms.submit_intent(harness.TENANT_A, _twse(), "lot")
    await oms.submit_intent(
        harness.TENANT_A, _twse(quantity_base=D(300)), "odd"
    )
    position = (await oms.list_positions(harness.TENANT_A, TWD_A))[0]
    position_id = str(position["position_id"])
    market_close = await oms.close_request(
        harness.TENANT_A, position_id, D(1), enums.OrderType.MARKET, None
    )
    assert market_close.quantity_base == D(1000)
    assert market_close.position_effect is enums.PositionEffect.REDUCE
    limit_close = await oms.close_request(
        harness.TENANT_A, position_id, D(1), enums.OrderType.LIMIT, D(599)
    )
    assert limit_close.time_in_force is enums.TimeInForce.DAY
    done = await oms.submit_intent(harness.TENANT_A, market_close, "close")
    assert done.decision.allowed, done.decision.failures()
    with pytest.raises(errors.ExecutionError) as info:
        await oms.close_request(
            harness.TENANT_A, position_id, D(1), enums.OrderType.MARKET, None
        )
    assert info.value.code == "VALIDATION_FAILED"
    odd_close = await oms.close_request(
        harness.TENANT_A, position_id, D(1), enums.OrderType.LIMIT, D(599)
    )
    assert odd_close.quantity_base == D(300)


async def test_amend_uses_the_ladder_tick() -> None:
    """Amended prices must sit on the TWSE ladder."""
    h = _build()
    oms = h.container.services.oms
    resting = await oms.submit_intent(
        harness.TENANT_A, _twse(limit_price=D(590)), "rest"
    )
    order_id = resting.orders[0].order_id
    with pytest.raises(errors.ExecutionError) as info:
        await oms.amend_order(
            harness.TENANT_A, order_id, D("590.5"), None, None, None
        )
    assert info.value.code == "INVALID_TICK_SIZE"
    amended = await oms.amend_order(
        harness.TENANT_A, order_id, D(595), None, None, None
    )
    assert amended.limit_price == D(595)


@pytest.fixture
def api() -> Iterator[tuple[harness.Harness, testclient.TestClient]]:
    """Provides a harness and an API client.

    Yields:
        Harness and client.
    """
    h = _build()
    with testclient.TestClient(bootstrap.build_app(h.container)) as client:
        yield h, client


def test_api_accepts_securities_orders(
    api: tuple[harness.Harness, testclient.TestClient],
    contract_validate: Validate,
) -> None:
    """DAY, outside_rth and TWSE ids pass the API and contracts."""
    _, client = api
    body = harness.intent_body(
        account_id=TWD_A,
        instrument_id=fixtures.TSMC,
        order_type="limit",
        limit_price="601",
        quantity_base="1000",
        time_in_force="DAY",
        leverage="1",
    )
    preview = client.post(
        "/v1/orders/preview", json=body, headers=harness.headers()
    )
    assert preview.status_code == 200, preview.json()
    contract_validate(EXEC, "OrderPreview", preview.json())
    assert preview.json()["allowed"]
    rth = client.post(
        "/v1/orders/preview",
        json={**body, "outside_rth": True},
        headers=harness.headers(),
    ).json()
    contract_validate(EXEC, "OrderPreview", rth)
    assert not rth["allowed"]
    assert rth["normalized"]["outside_rth"] is True
    accounts_list = client.get("/v1/accounts", headers=harness.headers()).json()
    contract_validate(EXEC, "AccountList", accounts_list)
    ids = {a["account_id"] for a in accounts_list["accounts"]}
    assert {harness.PAPER_A, TWD_A} <= ids


def test_broker_connections_take_only_the_account_number(
    api: tuple[harness.Harness, testclient.TestClient],
    contract_validate: Validate,
) -> None:
    """Broker connections store the account number; logins stay secret."""
    _, client = api
    hdr = harness.headers()
    connect = client.post(
        "/v1/accounts/connect/start",
        json={
            "venue": "ibkr",
            "label": "IBKR paper",
            "credentials": {"account": "DU1234567"},
            "withdrawal_permission_disabled": True,
        },
        headers=hdr,
    )
    assert connect.status_code == 201, connect.json()
    summary = connect.json()
    contract_validate(EXEC, "AccountSummary", summary)
    assert summary["currency"] == "USD"
    assert summary["capabilities"]["status"] == "TRADING_UNAVAILABLE"
    assert summary["capabilities"]["tradable_venues"] == ["nasdaq", "nyse"]
    verified = client.post(
        f"/v1/accounts/{summary['account_id']}/verify", headers=hdr
    ).json()
    assert verified["status"] == "TRADING_UNAVAILABLE"
    cathay = client.post(
        "/v1/accounts/connect/start",
        json={
            "venue": "cathay",
            "credentials": {"account": "8880-1234567"},
            "withdrawal_permission_disabled": True,
        },
        headers=hdr,
    ).json()
    assert cathay["currency"] == "TWD"
    assert "Awaiting the Cathay API SDK" in cathay["capabilities"]["notes"][-1]
    for bad in (
        {"account": "9A95-0000001", "api_key": "k"},
        {"account": "x"},
    ):
        refused = client.post(
            "/v1/accounts/connect/start",
            json={
                "venue": "sinopac",
                "credentials": bad,
                "withdrawal_permission_disabled": True,
            },
            headers=hdr,
        )
        assert refused.status_code == 422, bad


async def test_live_ibkr_order_end_to_end() -> None:
    """With every gate satisfied an IBKR order fills through the OMS."""
    h = harness.build(
        live_trading_enabled=True,
        securities=config.SecuritiesSettings(ibkr_enabled=True),
    )
    h.clock.current_ms = fixtures.new_york(MONDAY, 10)
    fixtures.install(h.market, MONDAY)
    h.market.feed_state = enums.FeedState.LIVE
    ib = fake_ib.FakeIB()
    ib.summary = [
        fake_ib.FakeAccountValue("DU1234567", "NetLiquidation", "50000", "USD"),
        fake_ib.FakeAccountValue("DU1234567", "AvailableFunds", "40000", "USD"),
    ]
    settings = h.container.settings
    lookup = bootstrap.broker_account_lookup(
        h.container.uow_factory, vault.AesGcmVault(settings.vault_master_key)
    )
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(ack_timeout_s=0.05, commission_wait_s=0.01),
        lambda: fake_ib.FakeIbFactory(ib),
        h.market,
        h.clock,
        lookup,
    )
    h.container.brokers["ibkr"] = adapter
    h.container.registry.live["ibkr"] = adapter
    svc = h.container.services
    connected = await svc.accounts.connect_start(
        harness.TENANT_A, "ibkr", "IBKR", {"account": "DU1234567"}, True
    )
    account_id = str(connected["account_id"])
    verified = await svc.accounts.verify(harness.TENANT_A, account_id)
    assert verified["status"] == "CONNECTED" and verified["modes"] == ["LIVE"]
    assert verified["equity"] == "50000"
    assert verified["live_armed_until_ms"] is None
    armed = await svc.accounts.grant_live_consent(
        harness.TENANT_A, account_id, h.totp(), True, 3_600_000
    )
    assert armed["live_armed_until_ms"] == h.clock.now_ms() + 3_600_000
    request = intents.OrderIntentRequest(
        account_id=account_id,
        instrument_id=fixtures.AAPL,
        side=enums.OrderSide.BUY,
        position_effect=enums.PositionEffect.OPEN,
        order_type=enums.OrderType.LIMIT,
        limit_price=D("190.01"),
        quantity_base=D(10),
        time_in_force=enums.TimeInForce.DAY,
        reduce_only=False,
        mode=enums.OrderMode.LIVE,
        source=enums.IntentSource.MANUAL_TICKET,
    )
    with pytest.raises(errors.ExecutionError) as info:
        await svc.oms.submit_intent(harness.TENANT_A, request, "no-preview")
    assert info.value.code == "PREVIEW_REQUIRED"
    preview = await svc.oms.preview(harness.TENANT_A, request)
    assert preview.allowed, [
        c.to_contract() for c in preview.decision.failures()
    ]
    tokened = dataclasses.replace(
        preview.normalized, preview_token=preview.token
    )
    result = await svc.oms.submit_intent(harness.TENANT_A, tokened, "live-1")
    order = result.orders[0]
    assert order.state is S.ACKNOWLEDGED
    assert ib.placed[0].orderRef == order.client_order_id
    ib.execute(ib.trades()[0], 10, 190.01, commission=1.0)
    applied = await bootstrap.pump_brokers(h.container)
    assert applied >= 1
    filled = svc.oms.get_order(harness.TENANT_A, order.order_id)
    assert filled.state is S.FILLED
    position = (await svc.oms.list_positions(harness.TENANT_A, account_id))[0]
    assert position["quantity_base"] == "10" and position["mode"] == "LIVE"
    twse = dataclasses.replace(
        request, instrument_id=fixtures.TSMC, limit_price=D(601)
    )
    refused = await svc.oms.preview(harness.TENANT_A, twse)
    assert refused.decision.primary_code() == "UNSUPPORTED"
