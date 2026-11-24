# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""End-to-end LIVE trading through the real engine wiring (Binance mocked).

The venue is simulated with respx (REST) and a scripted WebSocket; every
other component is the production composition: vault-held credentials,
account verification, Risk Gateway, OMS outbox, reconciliation and the
private-stream supervisor. Safety cases prove that a closed gate, missing
credentials, a kill switch, a missing account LIVE arming or missing
AUTO_LIVE consent never let an order request reach the venue.
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
from joseph_execution.adapters import step_up
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

_Decimal = decimal.Decimal
_State = enums.OrderState
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
        container: The engine's composition root.
        clock: Manual wall clock.
        mono: Manual monotonic clock of the private streams.
        connector: Fake WebSocket connector.
        account_id: Binance account id once ``_connect`` has run, else empty.
    """

    container: bootstrap.Container
    clock: clock_lib.ManualClock
    mono: venue_fakes.ManualMonotonic
    connector: venue_fakes.FakeConnector
    account_id: str = ""


def _engine(gate: bool) -> Engine:
    """Builds an engine whose market gateway lists Binance BTCUSDT (LIVE).

    Args:
        gate: Whether the global LIVE gate is open
            (``live_trading_enabled``).

    Returns:
        The engine with tenant A's paper account and the BTCUSDT mark at
        95000; no Binance account is connected yet.
    """
    clock = clock_lib.ManualClock(1_795_000_000_000)
    meta = market.InstrumentMeta(
        instrument_id=BTC,
        venue="binance",
        tick_size=_Decimal("0.1"),
        lot_size=_Decimal("0.001"),
        min_qty=_Decimal("0.001"),
        status=enums.InstrumentStatus.TRADING,
        min_notional=_Decimal(5),
        max_leverage=_Decimal(50),
        quote="USDT",
    )
    gateway = market_static.StaticMarketDataGateway(
        clock, (*market_static.MOCK_INSTRUMENTS, meta)
    )
    gateway.set_mark(BTC, _Decimal(95000))
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
        engine: Engine; its ``account_id`` is set to the new account.
        live_mode: Whether to mark the account CONNECTED and LIVE-capable
            without verifying it.

    Returns:
        The new account id.
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


async def _arm(engine: Engine, account_id: str) -> dict[str, object]:
    """Arms manual LIVE orders on an account with a valid step-up code.

    Args:
        engine: Engine.
        account_id: Verified account.

    Returns:
        Account summary.
    """
    return await engine.container.services.accounts.grant_live_consent(
        harness.TENANT_A,
        account_id,
        step_up.totp(harness.TOTP_SECRET, engine.clock.now_ms()),
        True,
    )


def _mock_venue(
    router: respx.MockRouter, order: dict[str, Any]
) -> dict[str, respx.Route]:
    """Mocks a healthy Binance account.

    Args:
        router: respx router to register the routes on.
        order: ``POST /fapi/v1/order`` answer.

    Returns:
        Routes keyed by a short name (``order``, ``margin``, ``listen`` ...),
        or by path for the open and historical order lists.
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
        account_id: Binance account to trade.
        **overrides: Contract body fields passed on to
            ``harness.intent_body``.

    Returns:
        The LIVE intent request on Binance BTCUSDT.
    """
    body = harness.intent_body(
        account_id=account_id, instrument_id=BTC, mode="LIVE", **overrides
    )
    return intents.OrderIntentRequest.from_contract(body)


async def _submit(
    engine: Engine, request: intents.OrderIntentRequest, key: str
) -> Any:
    """Previews a request for tenant A and submits it with the preview token.

    Args:
        engine: Engine.
        request: Intent request.
        key: Idempotency key.

    Returns:
        The OMS submit result.
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
        routes: Unused; the count comes from every call ``router`` saw.
        router: respx router of the test.

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
    """A LIVE order verifies, previews, submits, fills and reconciles."""
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
        capabilities = verified["capabilities"]
        assert isinstance(capabilities, dict)
        assert capabilities["status"] == "AVAILABLE"
        assert (await _arm(engine, account_id))["live_armed_until_ms"]
        result = await _submit(engine, _intent(account_id), "live-1")
        assert result.decision.allowed, result.decision.failures()
        (order,) = result.orders
        fresh = engine.container.services.oms.get_order(
            harness.TENANT_A, order.order_id
        )
        assert fresh.state is _State.FILLED
        assert fresh.filled_base == _Decimal("0.010")
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
        assert position.signed_qty == _Decimal("0.010") and not position.unknown
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
        await _arm(engine, account_id)
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
        assert fresh.state is _State.FILLED
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
        engine.container.registry.live["binance"].calls.clear()  # type: ignore[attr-defined]  # registry is typed TradingAdapter; this is a LiveVenueAdapter
        with respx.mock(assert_all_called=False) as router:
            result = await _submit(engine, _intent(account_id), f"g-{gate}")
            assert not result.decision.allowed
            codes = {c.code for c in result.decision.failures()}
            assert codes & {"LIVE_TRADING_DISABLED", "TRADE_MODE_DISABLED"}
            assert router.calls.call_count == 0
        assert not engine.container.registry.live["binance"].calls  # type: ignore[attr-defined]  # registry is typed TradingAdapter; this is a LiveVenueAdapter


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
        await _arm(engine, account_id)
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
    assert "submit_order" not in adapter.calls  # type: ignore[attr-defined]  # registry is typed TradingAdapter; this is a LiveVenueAdapter


async def test_unarmed_manual_live_orders_never_reach_the_venue() -> None:
    """Unarmed sends nothing, armed sends, disarmed sends only closes."""
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
        await engine.container.services.accounts.verify(
            harness.TENANT_A, account_id
        )
        blocked = await _submit(engine, _intent(account_id), "unarmed")
        assert blocked.decision.primary_code() == "LIVE_NOT_ARMED"
        assert blocked.orders == ()
        assert _order_calls(routes, router) == 0
        await _arm(engine, account_id)
        sent = await _submit(engine, _intent(account_id), "armed")
        assert sent.decision.allowed, sent.decision.failures()
        assert routes["order"].call_count == 1
        disarmed = await engine.container.services.accounts.revoke_live_consent(
            harness.TENANT_A, account_id
        )
        assert disarmed["live_armed_until_ms"] is None
        again = await _submit(engine, _intent(account_id), "disarmed")
        assert again.decision.primary_code() == "LIVE_NOT_ARMED"
        position = next(
            p
            for p in await engine.container.services.oms.list_positions(
                harness.TENANT_A, account_id
            )
            if p["mode"] == "LIVE"
        )
        close = await engine.container.services.oms.close_request(
            harness.TENANT_A,
            str(position["position_id"]),
            _Decimal(1),
            enums.OrderType.MARKET,
            None,
        )
        reduced = await _submit(engine, close, "close-disarmed")
        assert reduced.decision.allowed, reduced.decision.failures()
        mode_check = next(
            c for c in reduced.decision.checks if c.check == "mode.allowed"
        )
        assert mode_check.outcome is enums.CheckOutcome.WARN
        assert routes["order"].call_count == 2
    with engine.container.uow_factory() as uow:
        actions = [e.action for e in uow.audit.chain("tenant-a")]
    assert actions.count("live_arming.granted") == 1
    assert actions.count("live_arming.revoked") == 1


@pytest.mark.parametrize("venue", ["bitget", "hyperliquid", "lighter", "mexc"])
async def test_other_venues_fail_closed_without_credentials(venue: str) -> None:
    """Every venue refuses accounts without stored credentials."""
    engine = _engine(gate=True)
    adapter = engine.container.registry.live[venue]
    with respx.mock(assert_all_called=False) as router:
        caps = await adapter.capabilities("acct-unknown")
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        assert router.calls.call_count == 0
