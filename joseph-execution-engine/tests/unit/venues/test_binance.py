# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Binance USDⓈ-M adapter: negotiation, request shapes, errors, stream."""

from __future__ import annotations

import decimal
import json
import urllib.parse
from typing import Any

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import binance
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import signing
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

_Decimal = decimal.Decimal
_State = enums.OrderState
API = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
CID = "j0123456789abcdef0123456789abcd"
KEYS = {"api_key": "pub-key", "api_secret": "binance-secret-value"}


def _adapter(
    fields: dict[str, str] | None = None,
    *,
    gate: bool = True,
    known: list[directory.KnownOrder] | None = None,
    connector: venue_fakes.FakeConnector | None = None,
    clock: venue_fakes.ManualMonotonic | None = None,
) -> binance.BinanceFuturesAdapter:
    """Builds an adapter for account ``acct``.

    Args:
        fields: Credential fields of ``acct``; ``None`` stores none.
        gate: Whether the global LIVE gate is open.
        known: OMS orders visible through the directory.
        connector: Fake WebSocket connector; one without scripts by default.
        clock: Manual monotonic clock; a new one by default.

    Returns:
        The adapter, on a manual wall clock at 1_795_000_000_000 ms.
    """
    mono = clock or venue_fakes.ManualMonotonic()
    source = credentials.StaticCredentials(
        {} if fields is None else {"acct": fields}
    )
    lookup = _Directory(known or [])
    return binance.BinanceFuturesAdapter(
        catalog.DOCUMENTED["binance"],
        source,
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=gate),
        directory=lookup,
        connect=connector or venue_fakes.FakeConnector([], mono),
        monotonic=mono,
        sleep=mono.sleep,
    )


class _Directory:
    """In-memory order directory keyed by client order id."""

    def __init__(self, known: list[directory.KnownOrder]) -> None:
        """Initializes the directory.

        Args:
            known: Orders the directory knows.
        """
        self._known = {k.client_order_id: k for k in known}

    def find(
        self, account_id: str, client_order_id: str
    ) -> directory.KnownOrder | None:
        """Finds a known order by client id.

        Args:
            account_id: Ignored; the tests use one account.
            client_order_id: Client order id.

        Returns:
            The order, or ``None`` when it is not known.
        """
        del account_id
        return self._known.get(client_order_id)

    def recent(
        self, account_id: str, since_ms: int
    ) -> list[directory.KnownOrder]:
        """Lists every known order, whatever the account and time.

        Args:
            account_id: Ignored; the tests use one account.
            since_ms: Ignored lower bound, epoch milliseconds.

        Returns:
            The known orders.
        """
        del account_id, since_ms
        return list(self._known.values())


def _known(
    cid: str, kind: enums.OrderType = enums.OrderType.LIMIT
) -> directory.KnownOrder:
    """Builds a directory entry.

    Args:
        cid: Client order id.
        kind: Order type.

    Returns:
        A LIVE, non-reduce-only BTCUSDT entry.
    """
    return directory.KnownOrder(
        cid, "binance:perp:BTCUSDT", enums.OrderMode.LIVE, 1, False, kind
    )


def _request(**overrides: object) -> trading.VenueOrderRequest:
    """Builds an order request for account ``acct``.

    Args:
        **overrides: Request fields that replace the defaults.

    Returns:
        By default a GTC limit buy of 0.002 BTCUSDT at 50000, 3x cross.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": "binance:perp:BTCUSDT",
        "client_order_id": CID,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": _Decimal("0.002"),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "limit_price": _Decimal("50000"),
        "leverage": _Decimal(3),
        "margin_mode": enums.MarginMode.CROSS,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


def _params(request: httpx.Request) -> dict[str, str]:
    """Decodes query parameters, asserting the HMAC signature and API key.

    Args:
        request: Captured request.

    Returns:
        Parameters (without the signature).
    """
    raw = request.url.query.decode()
    body, _, signature = raw.rpartition("&signature=")
    assert signing.hmac_sha256_hex(KEYS["api_secret"], body) == signature
    assert request.headers["X-MBX-APIKEY"] == KEYS["api_key"]
    return dict(urllib.parse.parse_qsl(body))


def _time_route() -> respx.Route:
    """Mocks ``GET /fapi/v1/time`` 100 ms after the manual wall clock.

    Returns:
        The mocked route.
    """
    return respx.get(f"{API}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1_795_000_000_100})
    )


def _healthy(
    hedge: bool = False, futures: bool = True, withdraw: bool = False
) -> None:
    """Mocks a healthy, permitted account.

    Args:
        hedge: Hedge position mode.
        futures: ``enableFutures`` on the key.
        withdraw: ``enableWithdrawals`` on the key.
    """
    _time_route()
    respx.get(f"{API}/fapi/v1/accountConfig").mock(
        return_value=httpx.Response(
            200,
            json={
                "feeTier": 0,
                "canTrade": True,
                "canDeposit": True,
                "canWithdraw": True,
                "dualSidePosition": hedge,
                "updateTime": 0,
                "multiAssetsMargin": False,
                "tradeGroupId": -1,
            },
        )
    )
    respx.get(f"{SAPI}/sapi/v1/account/apiRestrictions").mock(
        return_value=httpx.Response(
            200,
            json={
                "ipRestrict": False,
                "createTime": 1623840271000,
                "enableReading": True,
                "enableWithdrawals": withdraw,
                "enableFutures": futures,
            },
        )
    )


@respx.mock(assert_all_called=False)
async def test_gate_off_and_no_credentials_make_no_network_calls() -> None:
    """Closed gate or missing keys: unavailable, refused, zero requests."""
    for adapter in (_adapter(KEYS, gate=False), _adapter(None)):
        caps = await adapter.capabilities("acct")
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await adapter.submit_order(_request())
        assert info.value.code == "LIVE_TRADING_DISABLED"
        with pytest.raises(errors.AdapterUnavailableError):
            await adapter.amend_order("acct", CID, _Decimal(1), None, None)
    assert (
        await _adapter(KEYS, gate=False).health()
        is enums.HealthState.UNAVAILABLE
    )
    incomplete = _adapter({"api_key": "x"})
    caps = await incomplete.capabilities("acct")
    assert "missing api_secret" in caps.notes[-1]
    with pytest.raises(errors.AdapterUnavailableError) as info:
        await incomplete.cancel_order("acct", CID)
    assert info.value.code == "AUTH_SCOPE_MISSING"
    assert respx.calls.call_count == 0


@respx.mock
async def test_negotiation_requires_health_permission_and_one_way() -> None:
    """AVAILABLE only with futures permission, no withdrawals, one-way."""
    _healthy()
    adapter = _adapter(KEYS)
    caps = await adapter.capabilities("acct")
    assert caps.status is enums.TradingStatus.AVAILABLE
    assert caps.position_modes == ("ONE_WAY",)
    assert any("MAINNET" in n for n in caps.notes)
    assert any("withdrawals disabled" in n for n in caps.notes)
    assert await adapter.health() is enums.HealthState.HEALTHY
    assert "binance-secret-value" not in repr(adapter)
    assert await _adapter(KEYS).health() is enums.HealthState.UNAVAILABLE


@pytest.mark.parametrize(
    ("hedge", "futures", "withdraw", "expected"),
    [
        (True, True, False, "UNSUPPORTED: the account uses Hedge"),
        (False, False, False, "permission missing: enable Futures"),
        (False, True, True, "can withdraw"),
    ],
)
@respx.mock
async def test_negotiation_refusals(
    hedge: bool, futures: bool, withdraw: bool, expected: str
) -> None:
    """Hedge mode, missing futures scope and withdrawal keys fail closed."""
    _healthy(hedge, futures, withdraw)
    caps = await _adapter(KEYS).capabilities("acct")
    assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
    assert expected in caps.notes[-1]


@respx.mock
async def test_invalid_key_and_unhealthy_venue_are_reported() -> None:
    """``-2015`` reads as permission missing; a dead venue as unhealthy."""
    _time_route()
    respx.get(f"{API}/fapi/v1/accountConfig").mock(
        return_value=httpx.Response(
            401,
            json={
                "code": -2015,
                "msg": "Invalid API-key, IP, or permissions for action.",
            },
        )
    )
    caps = await _adapter(KEYS).capabilities("acct")
    assert caps.notes[-1].startswith("AUTH_SCOPE_MISSING: permission missing")
    assert "-2015" in caps.notes[-1]
    respx.get(f"{API}/fapi/v1/time").mock(side_effect=httpx.ConnectError("x"))
    other = _adapter(KEYS)
    caps = await other.capabilities("acct")
    assert caps.notes[-1].startswith("Venue health check failed")
    assert await other.health() is enums.HealthState.UNAVAILABLE


@respx.mock
async def test_limit_submit_configures_margin_and_signs_order() -> None:
    """A limit order sets margin type and leverage, then posts the order."""
    _time_route()
    margin = respx.post(f"{API}/fapi/v1/marginType").mock(
        return_value=httpx.Response(
            400, json={"code": -4046, "msg": "No need to change margin type."}
        )
    )
    leverage = respx.post(f"{API}/fapi/v1/leverage").mock(
        return_value=httpx.Response(
            200, json={"leverage": 3, "symbol": "BTCUSDT"}
        )
    )
    order = respx.post(f"{API}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "clientOrderId": CID,
                "orderId": 22542179,
                "status": "NEW",
                "executedQty": "0",
                "updateTime": 1566818724722,
            },
        )
    )
    adapter = _adapter(KEYS)
    result = await adapter.submit_order(
        _request(time_in_force=enums.TimeInForce.POST_ONLY)
    )
    assert result.accepted and result.state.status is _State.ACKNOWLEDGED
    assert result.state.exchange_order_id == "22542179"
    assert _params(margin.calls.last.request)["marginType"] == "CROSSED"
    assert _params(leverage.calls.last.request)["leverage"] == "3"
    sent = _params(order.calls.last.request)
    assert sent == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "0.002",
        "newClientOrderId": CID,
        "newOrderRespType": "RESULT",
        "price": "50000",
        "timeInForce": "GTX",
        "recvWindow": "5000",
        "timestamp": "1795000000100",
    }
    await adapter.submit_order(_request(client_order_id=f"{CID}-x"))
    assert leverage.call_count == 1
    await adapter.aclose()


@respx.mock
async def test_stop_orders_use_the_algo_order_api() -> None:
    """Stops go to ``/fapi/v1/algoOrder`` as reduce-only conditionals."""
    _time_route()
    algo = respx.post(f"{API}/fapi/v1/algoOrder").mock(
        return_value=httpx.Response(
            200,
            json={
                "algoId": 2146760,
                "clientAlgoId": f"{CID}-sl",
                "algoType": "CONDITIONAL",
                "orderType": "STOP_MARKET",
                "algoStatus": "NEW",
                "createTime": 1750485492076,
            },
        )
    )
    result = await _adapter(KEYS).submit_order(
        _request(
            client_order_id=f"{CID}-sl",
            side=enums.OrderSide.SELL,
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=_Decimal("45000"),
            trigger_basis=enums.PriceBasis.MARK,
            reduce_only=True,
        )
    )
    assert result.state.exchange_order_id == "algo:2146760"
    sent = _params(algo.calls.last.request)
    assert sent["algoType"] == "CONDITIONAL" and sent["type"] == "STOP_MARKET"
    assert (
        sent["triggerPrice"] == "45000" and sent["workingType"] == "MARK_PRICE"
    )
    assert sent["reduceOnly"] == "true" and sent["clientAlgoId"] == f"{CID}-sl"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (
            400,
            {"code": -2019, "msg": "Margin is insufficient."},
            "INSUFFICIENT_MARGIN",
        ),
        (
            400,
            {"code": -4164, "msg": "Order's notional must be no smaller"},
            "MIN_NOTIONAL",
        ),
        (
            400,
            {"code": -4014, "msg": "Price not increased by tick size."},
            "INVALID_TICK_SIZE",
        ),
        (
            400,
            {"code": -4003, "msg": "Quantity less than zero."},
            "INVALID_LOT_SIZE",
        ),
        (
            400,
            {"code": -5022, "msg": "Post Only order will be rejected."},
            "VALIDATION_FAILED",
        ),
        (403, {"code": -2015, "msg": "Invalid API-key"}, "AUTH_SCOPE_MISSING"),
    ],
)
@respx.mock
async def test_venue_rejections_become_rejected_results(
    status: int, body: dict[str, object], code: str
) -> None:
    """Definite venue rejections are REJECTED with the mapped error code."""
    _time_route()
    respx.post(f"{API}/fapi/v1/order").mock(
        return_value=httpx.Response(status, json=body)
    )
    result = await _adapter(KEYS).submit_order(
        _request(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            reduce_only=True,
        )
    )
    assert not result.accepted and result.state.status is _State.REJECTED
    assert result.state.reason is not None and result.state.reason.startswith(
        code
    )


def test_error_mapping_is_distinct_and_non_retryable() -> None:
    """Margin, parameter and permission errors are distinct classes."""
    margin = binance.map_error(
        400, {"code": -2019, "msg": "Margin is insufficient."}
    )
    assert (
        isinstance(margin, errors.InsufficientMarginError)
        and not margin.retryable
    )
    params = binance.map_error(400, {"code": -1111, "msg": "Precision"})
    assert (
        isinstance(params, errors.OrderRejectedError) and not params.retryable
    )
    denied = binance.map_error(401, {"code": -2015, "msg": "Invalid"})
    assert isinstance(denied, errors.VenuePermissionError)
    assert isinstance(binance.map_error(503, "x"), errors.AdapterTimeoutError)
    assert isinstance(
        binance.map_error(400, {"code": -1003, "msg": "x"}),
        errors.RateLimitedError,
    )
    assert (
        binance.map_error(400, {"code": -2011, "msg": "x"}).code == "NOT_FOUND"
    )
    assert (
        binance.map_error(400, {"code": -4120, "msg": "x"}).code
        == "UNSUPPORTED_ORDER_TYPE"
    )
    assert binance.map_error(400, {"code": "bad"}).code == "VALIDATION_FAILED"
    stale = binance.map_error(400, {"code": -1021, "msg": "ts"})
    assert isinstance(stale, errors.AdapterUnavailableError)


@respx.mock
async def test_clock_resync_duplicates_and_unknown_outcomes() -> None:
    """``-1021`` resyncs once; duplicates resolve; 5xx/timeout are unknown."""
    times = _time_route()
    order = respx.post(f"{API}/fapi/v1/order").mock(
        side_effect=[
            httpx.Response(
                400, json={"code": -1021, "msg": "outside recvWindow"}
            ),
            httpx.Response(
                200, json={"orderId": 7, "status": "NEW", "executedQty": "0"}
            ),
            httpx.Response(
                400, json={"code": -4116, "msg": "ClientOrderId is duplicated."}
            ),
            httpx.Response(503, json={"code": -1007, "msg": "Unknown error"}),
            httpx.ReadTimeout("slow"),
            httpx.Response(429, headers={"Retry-After": "1"}),
        ]
    )
    respx.get(f"{API}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 7,
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.001",
            },
        )
    )
    adapter = _adapter(KEYS, known=[_known(CID)])
    market = _request(
        order_type=enums.OrderType.MARKET, limit_price=None, reduce_only=True
    )
    first = await adapter.submit_order(market)
    assert first.accepted and times.call_count == 2
    duplicate = await adapter.submit_order(market)
    assert duplicate.state.status is _State.PARTIALLY_FILLED
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(market)
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(market)
    with pytest.raises(errors.RateLimitedError):
        await adapter.submit_order(market)
    assert order.call_count == 6


@respx.mock
async def test_query_cancel_and_amend_by_client_id() -> None:
    """Orders are found, cancelled and modified by client id and symbol."""
    _time_route()
    query = respx.get(f"{API}/fapi/v1/order").mock(
        side_effect=[
            httpx.Response(
                400, json={"code": -2013, "msg": "Order does not exist."}
            ),
            httpx.Response(
                200,
                json={"orderId": 9, "status": "FILLED", "executedQty": "0.002"},
            ),
            httpx.Response(
                200,
                json={
                    "orderId": 9,
                    "side": "BUY",
                    "origQty": "0.002",
                    "price": "50000",
                },
            ),
        ]
    )
    respx.delete(f"{API}/fapi/v1/order").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"orderId": 9, "status": "CANCELED", "executedQty": "0"},
            ),
            httpx.Response(
                400, json={"code": -2011, "msg": "Unknown order sent."}
            ),
        ]
    )
    amend = respx.put(f"{API}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200, json={"orderId": 9, "status": "NEW", "executedQty": "0"}
        )
    )
    adapter = _adapter(KEYS, known=[_known(CID)])
    assert await adapter.get_order_by_client_id("acct", CID) is None
    assert query.calls.last.request.url.params["origClientOrderId"] == CID
    canceled = await adapter.cancel_order("acct", CID)
    assert canceled.status is _State.CANCELED
    raced = await adapter.cancel_order("acct", CID)
    assert raced.status is _State.FILLED
    amended = await adapter.amend_order(
        "acct", CID, _Decimal("49000"), None, None
    )
    assert amended.status is _State.ACKNOWLEDGED
    sent = _params(amend.calls.last.request)
    assert sent["price"] == "49000" and sent["quantity"] == "0.002"
    assert sent["side"] == "BUY"
    with pytest.raises(errors.OrderRejectedError):
        await adapter.amend_order("acct", CID, None, _Decimal(1), None)
    unknown = _adapter(KEYS)
    with pytest.raises(errors.AdapterUnavailableError):
        await unknown.get_order_by_client_id("acct", "j-unknown")


@respx.mock
async def test_triggered_algo_order_resolves_through_its_real_order() -> None:
    """A triggered stop reports the matching-engine order's fill state."""
    _time_route()
    respx.get(f"{API}/fapi/v1/algoOrder").mock(
        return_value=httpx.Response(
            200,
            json={
                "algoId": 5,
                "clientAlgoId": f"{CID}-sl",
                "algoStatus": "FINISHED",
                "actualOrderId": "77",
            },
        )
    )
    respx.get(f"{API}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={"orderId": 77, "status": "FILLED", "executedQty": "0.002"},
        )
    )
    respx.delete(f"{API}/fapi/v1/algoOrder").mock(
        return_value=httpx.Response(
            200, json={"algoId": 5, "code": "200", "msg": "success"}
        )
    )
    adapter = _adapter(
        KEYS, known=[_known(f"{CID}-sl", enums.OrderType.STOP_MARKET)]
    )
    state = await adapter.get_order_by_client_id("acct", f"{CID}-sl")
    assert state is not None and state.status is _State.FILLED
    assert state.exchange_order_id == "algo:5"
    canceled = await adapter.cancel_order("acct", f"{CID}-sl")
    assert canceled.status is _State.FILLED


@respx.mock
async def test_reads_map_positions_balances_orders_and_fills() -> None:
    """Snapshots, positions, open orders and fills parse documented shapes."""
    _time_route()
    respx.get(f"{API}/fapi/v3/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "totalMarginBalance": "126.72469206",
                "availableBalance": "100.5",
            },
        )
    )
    respx.get(f"{API}/fapi/v3/positionRisk").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "ADAUSDT",
                    "positionAmt": "-30",
                    "entryPrice": "0.385",
                },
                {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"},
            ],
        )
    )
    respx.get(f"{API}/fapi/v1/openOrders").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "clientOrderId": CID,
                    "orderId": 11,
                    "status": "NEW",
                    "executedQty": "0",
                }
            ],
        )
    )
    respx.get(f"{API}/fapi/v1/openAlgoOrders").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "clientAlgoId": f"{CID}-sl",
                    "algoId": 3,
                    "algoStatus": "NEW",
                }
            ],
        )
    )
    trades = [
        {
            "id": 698759,
            "orderId": 25851813,
            "price": "7819.01",
            "qty": "0.002",
            "commission": "0.07819010",
            "commissionAsset": "USDT",
            "maker": False,
            "time": 1569514978020,
        },
        {
            "id": 1,
            "orderId": 999,
            "price": "1",
            "qty": "1",
            "commission": "0",
            "time": 1,
        },
    ]

    def by_symbol(request: httpx.Request) -> httpx.Response:
        """Answers BTCUSDT trades only.

        Args:
            request: Request.

        Returns:
            Response.
        """
        wanted = request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json=trades if wanted else [])

    respx.get(f"{API}/fapi/v1/userTrades").mock(side_effect=by_symbol)
    all_orders = respx.get(f"{API}/fapi/v1/allOrders").mock(
        return_value=httpx.Response(
            200, json=[{"clientOrderId": CID, "orderId": 25851813}]
        )
    )
    respx.get(f"{API}/fapi/v1/allAlgoOrders").mock(
        return_value=httpx.Response(200, json=[])
    )
    adapter = _adapter(KEYS, known=[_known(CID)])
    snap = await adapter.account_snapshot("acct")
    assert snap.equity == _Decimal(
        "126.72469206"
    ) and snap.available_margin == _Decimal("100.5")
    positions = await adapter.list_positions("acct")
    assert [(p.instrument_id, p.signed_qty) for p in positions] == [
        ("binance:perp:ADAUSDT", _Decimal(-30))
    ]
    opened = await adapter.list_open_orders("acct")
    assert {o.client_order_id for o in opened} == {CID, f"{CID}-sl"}
    fills = await adapter.list_recent_fills("acct", 0)
    assert len(fills) == 1 and fills[0].exchange_fill_id == "BTCUSDT:698759"
    assert (
        fills[0].fee == _Decimal("0.07819010") and fills[0].liquidity == "taker"
    )
    assert all_orders.call_count >= 1


def test_stream_parser_on_documented_payloads() -> None:
    """Documented ORDER_TRADE_UPDATE/ALGO_UPDATE/ACCOUNT_UPDATE parse."""
    adapter = _adapter(KEYS)
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    trade = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1568879465651,
        "T": 1568879465650,
        "o": {
            "s": "BTCUSDT",
            "c": CID,
            "S": "SELL",
            "o": "LIMIT",
            "f": "GTC",
            "q": "0.002",
            "p": "50000",
            "ap": "50000",
            "sp": "0",
            "x": "TRADE",
            "X": "PARTIALLY_FILLED",
            "i": 8886774,
            "l": "0.001",
            "z": "0.001",
            "L": "50000",
            "N": "USDT",
            "n": "0.02",
            "T": 1568879465650,
            "t": 42,
            "m": True,
            "R": False,
            "ps": "BOTH",
            "rp": "0",
        },
    }
    updates = adapter.parse(session, json.dumps(trade))
    assert (
        updates[0].fill is not None
        and updates[0].fill.exchange_fill_id == "BTCUSDT:42"
    )
    assert updates[0].fill.liquidity == "maker"
    assert updates[1].order is not None
    assert updates[1].order.status is _State.PARTIALLY_FILLED
    assert updates[1].order.filled_base == _Decimal("0.001")
    algo: dict[str, Any] = {
        "e": "ALGO_UPDATE",
        "T": 1750515742297,
        "E": 1750515742303,
        "o": {
            "caid": f"{CID}-sl",
            "aid": 2148719,
            "at": "CONDITIONAL",
            "o": "STOP_MARKET",
            "s": "BTCUSDT",
            "S": "SELL",
            "q": "0.002",
            "X": "CANCELED",
            "ai": "",
            "aq": "0",
            "tt": 0,
            "rm": "Reduce Only reject",
        },
    }
    (cancel,) = adapter.parse(session, json.dumps(algo))
    assert cancel.order is not None and cancel.order.status is _State.CANCELED
    assert cancel.order.reason == "Reduce Only reject"
    algo["o"].update({"X": "TRIGGERED", "ai": "555"})
    assert adapter.parse(session, json.dumps(algo)) == []
    assert session.order_ids["555"] == f"{CID}-sl"
    algo["o"].update({"X": "FINISHED", "aq": "0.002"})
    (done,) = adapter.parse(session, json.dumps(algo))
    assert done.order is not None and done.order.status is _State.FILLED
    account = {
        "e": "ACCOUNT_UPDATE",
        "E": 1564745798939,
        "T": 1564745798938,
        "a": {
            "m": "ORDER",
            "B": [
                {
                    "a": "USDT",
                    "wb": "122624.12345678",
                    "cw": "100.12345678",
                    "bc": "50.12345678",
                }
            ],
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.002",
                    "ep": "50000",
                    "up": "0",
                    "mt": "cross",
                    "ps": "BOTH",
                }
            ],
        },
    }
    position, balance = adapter.parse(session, json.dumps(account))
    assert (
        position.position is not None
        and position.position.signed_qty == _Decimal("0.002")
    )
    assert balance.balance is not None and balance.balance.equity == _Decimal(
        "122624.12345678"
    )
    expired = adapter.parse(session, '{"e":"listenKeyExpired","E":1}')
    assert expired[0].reconnect
    assert adapter.parse(session, '{"e":"MARGIN_CALL"}') == []
    assert adapter.parse(session, b"[]") == []


@respx.mock
async def test_user_stream_uses_listen_keys_and_resyncs() -> None:
    """The stream opens ``/private/ws/<listenKey>`` and replays REST first."""
    _time_route()
    respx.post(f"{API}/fapi/v1/listenKey").mock(
        return_value=httpx.Response(200, json={"listenKey": "LK1"})
    )
    for path, body in (
        ("/fapi/v1/userTrades", []),
        ("/fapi/v1/openOrders", []),
        ("/fapi/v1/openAlgoOrders", []),
        ("/fapi/v3/positionRisk", []),
        (
            "/fapi/v3/account",
            {"totalMarginBalance": "10", "availableBalance": "5"},
        ),
    ):
        respx.get(f"{API}{path}").mock(
            return_value=httpx.Response(200, json=body)
        )
    mono = venue_fakes.ManualMonotonic()
    frame = json.dumps(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "BTCUSDT",
                "c": CID,
                "x": "NEW",
                "X": "NEW",
                "i": 5,
                "z": "0",
                "T": 1,
            },
        }
    )
    connector = venue_fakes.FakeConnector([venue_fakes.Script([frame])], mono)
    adapter = _adapter(
        KEYS, connector=connector, clock=mono, known=[_known(CID)]
    )
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    assert connector.urls == ["wss://fstream.binance.com/private/ws/LK1"]
    first, second = events
    assert first.resync and first.balance is not None
    assert second.order is not None and not second.resync
    assert second.sequence > first.sequence
