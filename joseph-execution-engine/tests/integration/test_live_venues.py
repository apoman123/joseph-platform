"""End-to-end LIVE trading through the real engine wiring (Binance mocked).

The venue is simulated with respx (REST) and a scripted WebSocket; every
other component is the production composition: vault-held credentials,
account verification, Risk Gateway, OMS outbox, reconciliation and the
private-stream supervisor. Safety cases prove that a closed gate, missing
credentials, a kill switch or missing AUTO_LIVE consent never let an order
request reach the venue.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import json
from typing import Any

import httpx
import pytest
import respx

from joseph_execution import bootstrap
from joseph_execution.adapters import market_static
from joseph_execution.adapters import vault
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import live
from joseph_execution.domain import enums
from joseph_execution.domain import intents
from joseph_execution.domain import market
from joseph_execution.platform import clock as clock_lib
from joseph_execution.testing import harness
from joseph_execution.testing import venue_fakes

D = decimal.Decimal
S = enums.OrderState
API = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
BTC = "binance:perp:BTCUSDT"
KEYS = {"api_key": "live-pub-key", "api_secret": "LIVE-SECRET-binance-77"}
_ORDER_PATHS = (
    "/fapi/v1/order",
    "/fapi/v1/algoOrder",
    "/fapi/v1/marginType",
    "/fapi/v1/leverage",
)


@dataclasses.dataclass(slots=True)
class Engine:
    """A wired engine with a Binance account.

    Attributes:
        container: Container.
        clock: Manual wall clock.
        mono: Manual monotonic clock (streams).
        connector: Fake WebSocket connector.
        account_id: Binance account id.
    """

    container: bootstrap.Container
    clock: clock_lib.ManualClock
    mono: venue_fakes.ManualMonotonic
    connector: venue_fakes.FakeConnector
    account_id: str = ""


def _engine(gate: bool) -> Engine:
    """Builds an engine whose market gateway lists Binance BTCUSDT (LIVE).

    Args:
        gate: ``live_trading_enabled``.

    Returns:
        Engine.
    """
    clock = clock_lib.ManualClock(1_795_000_000_000)
    meta = market.InstrumentMeta(
        instrument_id=BTC,
        venue="binance",
        tick_size=D("0.1"),
        lot_size=D("0.001"),
        min_qty=D("0.001"),
        status=enums.InstrumentStatus.TRADING,
        min_notional=D(5),
        max_leverage=D(50),
        quote="USDT",
    )
    gateway = market_static.StaticMarketDataGateway(
        clock, (*market_static.MOCK_INSTRUMENTS, meta)
    )
    gateway.set_mark(BTC, D(95000))
    gateway.feed_state = enums.FeedState.LIVE
    settings = harness.settings(live_trading_enabled=gate)
    container = bootstrap.build_container(settings, clock, gateway)
    mono = venue_fakes.ManualMonotonic()
    connector = venue_fakes.FakeConnector([], mono)
    container.registry.live.update(
        catalog.build_live_adapters(
            credentials.VaultCredentialSource(
                container.uow_factory, vault.AesGcmVault(b"k" * 32)
            ),
            clock,
            live.VenueOptions(live_trading_enabled=gate),
            directory=directory.RepositoryOrderDirectory(container.uow_factory),
            connect=connector,
            monotonic=mono,
            sleep=mono.sleep,
        )
    )
    container.services.accounts.ensure_paper_account(harness.TENANT_A.tenant_id)
    return Engine(container, clock, mono, connector)


async def _connect(engine: Engine, *, live_mode: bool = False) -> str:
    """Stores Binance credentials for tenant A.

    Args:
        engine: Engine.
        live_mode: Force CONNECTED + LIVE without verification.

    Returns:
        Account id.
    """
    summary = await engine.container.services.accounts.connect_start(
        harness.TENANT_A, "binance", "Main", KEYS, True
    )
    account_id = str(summary["account_id"])
    if live_mode:
        with engine.container.uow_factory() as uow:
            account = uow.accounts.get(account_id)
            assert account is not None
            uow.accounts.save(
                dataclasses.replace(
                    account,
                    status=enums.AccountStatus.CONNECTED,
                    modes=(enums.OrderMode.LIVE,),
                )
            )
    engine.account_id = account_id
    return account_id


def _mock_venue(
    router: respx.MockRouter, order: dict[str, Any]
) -> dict[str, respx.Route]:
    """Mocks a healthy Binance account.

    Args:
        router: Router.
        order: ``POST /fapi/v1/order`` answer.

    Returns:
        Routes by path.
    """
    routes = {
        "time": router.get(f"{API}/fapi/v1/time").mock(
            return_value=httpx.Response(
                200, json={"serverTime": 1_795_000_000_000}
            )
        ),
        "config": router.get(f"{API}/fapi/v1/accountConfig").mock(
            return_value=httpx.Response(
                200, json={"canTrade": True, "dualSidePosition": False}
            )
        ),
        "rules": router.get(f"{SAPI}/sapi/v1/account/apiRestrictions").mock(
            return_value=httpx.Response(
                200, json={"enableFutures": True, "enableWithdrawals": False}
            )
        ),
        "account": router.get(f"{API}/fapi/v3/account").mock(
            return_value=httpx.Response(
                200,
                json={
                    "totalMarginBalance": "10000",
                    "availableBalance": "9000",
                },
            )
        ),
        "margin": router.post(f"{API}/fapi/v1/marginType").mock(
            return_value=httpx.Response(
                200, json={"code": 200, "msg": "success"}
            )
        ),
        "leverage": router.post(f"{API}/fapi/v1/leverage").mock(
            return_value=httpx.Response(200, json={"leverage": 2})
        ),
        "order": router.post(f"{API}/fapi/v1/order").mock(
            return_value=httpx.Response(200, json=order)
        ),
        "trades": router.get(f"{API}/fapi/v1/userTrades").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 5,
                        "orderId": 1001,
                        "price": "95010",
                        "qty": "0.010",
                        "commission": "0.38",
                        "commissionAsset": "USDT",
                        "maker": False,
                        "time": 1_795_000_000_000,
                    }
                ],
            )
        ),
        "positions": router.get(f"{API}/fapi/v3/positionRisk").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "positionAmt": "0.010",
                        "entryPrice": "95010",
                    }
                ],
            )
        ),
    }
    for path in (
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/allOrders",
        "/fapi/v1/allAlgoOrders",
    ):
        routes[path] = router.get(f"{API}{path}").mock(
            return_value=httpx.Response(200, json=[])
        )
    routes["listen"] = router.post(f"{API}/fapi/v1/listenKey").mock(
        return_value=httpx.Response(200, json={"listenKey": "LK"})
    )
    return routes


def _intent(account_id: str, **overrides: object) -> intents.OrderIntentRequest:
    """Builds a LIVE intent for the Binance account.

    Args:
        account_id: Account.
        **overrides: Contract field overrides.

    Returns:
        Request.
    """
    body = harness.intent_body(
        account_id=account_id, instrument_id=BTC, mode="LIVE", **overrides
    )
    return intents.OrderIntentRequest.from_contract(body)


async def _submit(
    engine: Engine, request: intents.OrderIntentRequest, key: str
) -> Any:
    """Previews then submits a manual LIVE intent.

    Args:
        engine: Engine.
        request: Request.
        key: Idempotency key.

    Returns:
        Submit result.
    """
    oms = engine.container.services.oms
    preview = await oms.preview(harness.TENANT_A, request)
    tokened = dataclasses.replace(
        preview.normalized, preview_token=preview.token
    )
    return await oms.submit_intent(harness.TENANT_A, tokened, key)


def _order_calls(
    routes: dict[str, respx.Route], router: respx.MockRouter
) -> int:
    """Counts requests to order-changing endpoints.

    Args:
        routes: Routes.
        router: Router.

    Returns:
        Number of order, algo, margin and leverage requests.
    """
    del routes
    return sum(
        1
        for call in router.calls
        if call.request.method != "GET"
        and call.request.url.path in _ORDER_PATHS
    )


async def test_live_order_flows_end_to_end_with_reconciliation() -> None:
    """Verify, preview, submit, fill, reconcile: a LIVE order really works."""
    engine = _engine(gate=True)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_venue(
            router,
            {
                "orderId": 1001,
                "status": "FILLED",
                "executedQty": "0.010",
                "updateTime": 1,
            },
        )
        account_id = await _connect(engine)
        verified = await engine.container.services.accounts.verify(
            harness.TENANT_A, account_id
        )
        assert verified["status"] == "CONNECTED"
        assert verified["modes"] == ["LIVE"]
        assert verified["capabilities"]["status"] == "AVAILABLE"
        result = await _submit(engine, _intent(account_id), "live-1")
        assert result.decision.allowed, result.decision.failures()
        (order,) = result.orders
        fresh = engine.container.services.oms.get_order(
            harness.TENANT_A, order.order_id
        )
        assert fresh.state is S.FILLED
        assert fresh.filled_base == D("0.010")
        sent = dict(
            pair.split("=")
            for pair in routes["order"]
            .calls.last.request.url.query.decode()
            .split("&")
        )
        assert sent["newClientOrderId"] == fresh.client_order_id
        assert sent["type"] == "MARKET" and sent["quantity"] == "0.01"
        assert (
            dict(
                pair.split("=")
                for pair in routes["margin"]
                .calls.last.request.url.query.decode()
                .split("&")
            )["marginType"]
            == "ISOLATED"
        )
        report = await engine.container.services.reconciler.run_once()
        assert report.mismatches == 0
        with engine.container.uow_factory() as uow:
            (position,) = [
                p
                for p in uow.positions.list(account_id=account_id)
                if p.mode is enums.OrderMode.LIVE
            ]
            audit = [e.action for e in uow.audit.chain("tenant-a")]
        assert position.signed_qty == D("0.010") and not position.unknown
        assert "order.submitted" in audit and "credential.stored" in audit
        everything = json.dumps([str(c.request.url) for c in router.calls])
        assert KEYS["api_secret"] not in everything


async def test_private_stream_fills_resting_orders_through_the_supervisor() -> (
    None
):
    """A resting LIVE order fills from the user stream via the supervisor."""
    engine = _engine(gate=True)
    with respx.mock(assert_all_called=False) as router:
        _mock_venue(
            router,
            {
                "orderId": 2002,
                "status": "NEW",
                "executedQty": "0",
                "updateTime": 1,
            },
        )
        account_id = await _connect(engine)
        await engine.container.services.accounts.verify(
            harness.TENANT_A, account_id
        )
        result = await _submit(
            engine,
            _intent(
                account_id,
                order_type="limit",
                limit_price="94000",
                time_in_force="GTC",
            ),
            "live-2",
        )
        (order,) = result.orders
        cid = engine.container.services.oms.get_order(
            harness.TENANT_A, order.order_id
        ).client_order_id
        frame = json.dumps(
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": 1,
                "T": 1,
                "o": {
                    "s": "BTCUSDT",
                    "c": cid,
                    "S": "BUY",
                    "o": "LIMIT",
                    "x": "TRADE",
                    "X": "FILLED",
                    "i": 2002,
                    "l": "0.010",
                    "z": "0.010",
                    "L": "94000",
                    "N": "USDT",
                    "n": "0.19",
                    "T": 1_795_000_000_500,
                    "t": 99,
                    "m": True,
                },
            }
        )
        engine.connector._scripts.append(venue_fakes.Script([frame]))
        supervisor = engine.container.live_streams
        assert supervisor is not None
        assert [a.account_id for a in supervisor.scope()] == [account_id]
        await supervisor.sync()
        for _ in range(200):
            if supervisor.applied:
                break
            await asyncio.sleep(0)
        assert supervisor.running() == [account_id]
        await supervisor.stop()
        fresh = engine.container.services.oms.get_order(
            harness.TENANT_A, order.order_id
        )
        assert fresh.state is S.FILLED
        assert (
            engine.connector.urls[0]
            == "wss://fstream.binance.com/private/ws/LK"
        )
        jobs = bootstrap.background_jobs(engine.container)
        assert supervisor.run_forever in jobs


async def test_closed_gate_and_missing_credentials_make_zero_venue_calls() -> (
    None
):
    """Gate off or no stored credentials: blocked, and nothing is sent."""
    for gate, revoke in ((False, False), (True, True)):
        engine = _engine(gate=gate)
        with respx.mock(assert_all_called=False) as setup:
            setup.route().mock(side_effect=httpx.ConnectError("offline"))
            account_id = await _connect(engine, live_mode=True)
        if revoke:
            with engine.container.uow_factory() as uow:
                uow.credentials.delete_for_account(account_id)
        engine.container.registry.live["binance"].calls.clear()  # type: ignore[attr-defined]
        with respx.mock(assert_all_called=False) as router:
            result = await _submit(engine, _intent(account_id), f"g-{gate}")
            assert not result.decision.allowed
            codes = {c.code for c in result.decision.failures()}
            assert codes & {"LIVE_TRADING_DISABLED", "TRADE_MODE_DISABLED"}
            assert router.calls.call_count == 0
        assert not engine.container.registry.live["binance"].calls  # type: ignore[attr-defined]


async def test_kill_switch_and_missing_consent_never_send_orders() -> None:
    """Kill switch and unconsented AUTO_LIVE block before any order call."""
    engine = _engine(gate=True)
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_venue(
            router, {"orderId": 1, "status": "NEW", "executedQty": "0"}
        )
        account_id = await _connect(engine)
        await engine.container.services.accounts.verify(
            harness.TENANT_A, account_id
        )
        await engine.container.services.kill_switches.set(
            harness.TENANT_A,
            enums.KillSwitchScope.GLOBAL,
            None,
            True,
            False,
            False,
            "drill",
        )
        blocked = await _submit(engine, _intent(account_id), "k-1")
        assert blocked.decision.primary_code() == "KILL_SWITCH_ENGAGED"
        await engine.container.services.kill_switches.set(
            harness.TENANT_A,
            enums.KillSwitchScope.GLOBAL,
            None,
            False,
            False,
            False,
            "release",
        )
        strategy = await engine.container.services.oms.submit_intent(
            harness.TENANT_A,
            _intent(
                account_id,
                source="STRATEGY",
                strategy_version_id="s:v1",
                signal_id="sig-1",
            ),
            "c-1",
        )
        assert not strategy.decision.allowed
        assert strategy.orders == ()
        assert _order_calls(routes, router) == 0
    adapter = engine.container.registry.live["binance"]
    assert "submit_order" not in adapter.calls  # type: ignore[attr-defined]


@pytest.mark.parametrize("venue", ["bitget", "hyperliquid", "lighter", "mexc"])
async def test_other_venues_fail_closed_without_credentials(venue: str) -> None:
    """Every venue refuses accounts without stored credentials."""
    engine = _engine(gate=True)
    adapter = engine.container.registry.live[venue]
    with respx.mock(assert_all_called=False) as router:
        caps = await adapter.capabilities("acct-unknown")
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        assert router.calls.call_count == 0
