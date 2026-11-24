# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bitget UTA adapter: negotiation, signed requests, errors, stream."""

from __future__ import annotations

import decimal
import json

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import bitget
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import live
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

_Decimal = decimal.Decimal
_State = enums.OrderState
API = "https://api.bitget.com"
CID = "j0123456789abcdef0123456789abcd"
KEYS = {
    "api_key": "bg-key",
    "api_secret": "bg-secret-value",
    "passphrase": "bg-pass-phrase",
}


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


def _adapter(
    fields: dict[str, str] | None = None,
    *,
    known: list[directory.KnownOrder] | None = None,
    connector: venue_fakes.FakeConnector | None = None,
    mono: venue_fakes.ManualMonotonic | None = None,
) -> bitget.BitgetFuturesAdapter:
    """Builds an adapter for account ``acct``.

    Args:
        fields: Credential fields of ``acct``; ``KEYS`` when empty.
        known: OMS orders visible through the directory.
        connector: Fake WebSocket connector; one without scripts by default.
        mono: Manual monotonic clock; a new one by default.

    Returns:
        The LIVE-enabled adapter, on a manual wall clock at
        1_795_000_000_000 ms.
    """
    clock = mono or venue_fakes.ManualMonotonic()
    return bitget.BitgetFuturesAdapter(
        catalog.DOCUMENTED["bitget"],
        credentials.StaticCredentials({"acct": fields or KEYS}),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True),
        directory=_Directory(known or []),
        connect=connector or venue_fakes.FakeConnector([], clock),
        monotonic=clock,
        sleep=clock.sleep,
    )


def _ok(data: object) -> httpx.Response:
    """Builds a Bitget success (``00000``) answer.

    Args:
        data: Value of the ``data`` field.

    Returns:
        A 200 response wrapping ``data``.
    """
    return httpx.Response(
        200,
        json={
            "code": "00000",
            "msg": "success",
            "requestTime": 1,
            "data": data,
        },
    )


def _fail(code: str, msg: str, status: int = 400) -> httpx.Response:
    """Builds an error answer.

    Args:
        code: Venue error code.
        msg: Venue message.
        status: HTTP status.

    Returns:
        The error response, with a null ``data`` field.
    """
    return httpx.Response(
        status, json={"code": code, "msg": msg, "requestTime": 1, "data": None}
    )


def _check_signature(request: httpx.Request, demo: bool = False) -> None:
    """Recomputes ``ACCESS-SIGN`` and asserts the auth headers.

    Args:
        request: Captured request.
        demo: Whether the demo-trading ``paptrading`` header must be set.
    """
    path = request.url.raw_path.decode()
    body = request.content.decode()
    expected = bitget.sign(
        KEYS["api_secret"],
        request.headers["ACCESS-TIMESTAMP"],
        request.method,
        path,
        body,
    )
    assert request.headers["ACCESS-SIGN"] == expected
    assert request.headers["ACCESS-PASSPHRASE"] == KEYS["passphrase"]
    assert ("paptrading" in request.headers) is demo


def _settings(hold: str = "one_way_mode", mode: str = "unified") -> None:
    """Mocks the server time and the account settings (BTCUSDT 3x cross).

    Args:
        hold: Account ``holdMode``.
        mode: Account ``accountMode``.
    """
    respx.get(f"{API}/api/v3/market/time").mock(
        return_value=_ok({"serverTime": "1"})
    )
    respx.get(f"{API}/api/v3/account/settings").mock(
        return_value=_ok(
            {
                "accountMode": mode,
                "holdMode": hold,
                "symbolConfigList": [
                    {
                        "category": "USDT-FUTURES",
                        "symbol": "BTCUSDT",
                        "marginMode": "crossed",
                        "leverage": "3",
                    }
                ],
            }
        )
    )


def _request(**overrides: object) -> trading.VenueOrderRequest:
    """Builds an order request for account ``acct``.

    Args:
        **overrides: Request fields that replace the defaults.

    Returns:
        By default a GTC limit buy of 0.01 BTCUSDT at 45000, 3x cross.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": "bitget:perp:BTCUSDT",
        "client_order_id": CID,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": _Decimal("0.01"),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "limit_price": _Decimal("45000"),
        "leverage": _Decimal(3),
        "margin_mode": enums.MarginMode.CROSS,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


@respx.mock
async def test_negotiation_requires_uta_one_way_and_signature() -> None:
    """UTA one-way accounts trade; hedge, Classic and bad keys do not."""
    _settings()
    adapter = _adapter()
    caps = await adapter.capabilities("acct")
    assert caps.status is enums.TradingStatus.AVAILABLE
    _check_signature(respx.calls.last.request)
    assert any("one-way" in n for n in caps.notes)
    _settings(hold="hedge_mode")
    caps = await _adapter().capabilities("acct")
    assert caps.notes[-1].startswith(
        "UNSUPPORTED: the Bitget account uses hedge"
    )
    respx.get(f"{API}/api/v3/account/settings").mock(
        return_value=_fail(
            "25245", "The account is not the unified account mode"
        )
    )
    caps = await _adapter().capabilities("acct")
    assert "Classic account" in caps.notes[-1]
    assert "permission missing" in caps.notes[-1]
    respx.get(f"{API}/api/v3/account/settings").mock(
        return_value=_fail(
            "40014", "Incorrect permissions, need trade permissions"
        )
    )
    caps = await _adapter().capabilities("acct")
    assert caps.notes[-1].startswith("AUTH_SCOPE_MISSING: permission missing")
    _settings(mode="upgrading")
    caps = await _adapter().capabilities("acct")
    assert "upgrading" in caps.notes[-1]
    incomplete = _adapter({"api_key": "k", "api_secret": "s"})
    caps = await incomplete.capabilities("acct")
    assert "missing passphrase" in caps.notes[-1]


@respx.mock
async def test_orders_and_triggers_are_signed_v3_requests() -> None:
    """Limit orders and stops post the documented v3 bodies."""
    leverage = respx.post(f"{API}/api/v3/account/set-leverage").mock(
        return_value=_ok("success")
    )
    order = respx.post(f"{API}/api/v3/trade/place-order").mock(
        return_value=_ok({"orderId": "121211212122", "clientOid": CID})
    )
    trigger = respx.post(f"{API}/api/v3/trade/place-strategy-order").mock(
        return_value=_ok({"orderId": "88", "clientOid": f"s{CID[1:]}"})
    )
    adapter = _adapter(
        {**KEYS, "environment": "demo"},
        known=[
            directory.KnownOrder(
                f"{CID}-sl",
                "bitget:perp:BTCUSDT",
                enums.OrderMode.LIVE,
                1,
                False,
                enums.OrderType.STOP_MARKET,
            )
        ],
    )
    result = await adapter.submit_order(
        _request(
            time_in_force=enums.TimeInForce.POST_ONLY,
            margin_mode=enums.MarginMode.ISOLATED,
        )
    )
    assert result.accepted and result.state.exchange_order_id == "121211212122"
    _check_signature(order.calls.last.request, demo=True)
    body = json.loads(order.calls.last.request.content)
    assert body == {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "qty": "0.01",
        "side": "buy",
        "clientOid": CID,
        "marginMode": "isolated",
        "reduceOnly": "no",
        "orderType": "limit",
        "price": "45000",
        "timeInForce": "post_only",
    }
    lever = json.loads(leverage.calls.last.request.content)
    assert lever["posSide"] == "long" and lever["marginMode"] == "isolated"
    stop = await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-sl",
            side=enums.OrderSide.SELL,
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=_Decimal("40000"),
            reduce_only=True,
        )
    )
    assert stop.state.exchange_order_id == "trigger:88"
    sent = json.loads(trigger.calls.last.request.content)
    assert sent["type"] == "trigger" and sent["triggerBy"] == "mark"
    assert sent["triggerOrderType"] == "market" and sent["reduceOnly"] == "yes"
    assert sent["clientOid"] == "s0123456789abcdef0123456789abcd"
    assert len(sent["clientOid"]) <= 32


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("25203", "INSUFFICIENT_MARGIN"),
        ("40762", "VALIDATION_FAILED"),
        ("40037", "AUTH_SCOPE_MISSING"),
    ],
)
@respx.mock
async def test_rejections_are_rejected_results(
    code: str, expected: str
) -> None:
    """Margin, parameter and key errors become REJECTED results."""
    respx.post(f"{API}/api/v3/trade/place-order").mock(
        return_value=_fail(code, "refused")
    )
    result = await _adapter().submit_order(
        _request(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            reduce_only=True,
        )
    )
    assert result.state.status is _State.REJECTED
    assert result.state.reason is not None and result.state.reason.startswith(
        expected
    )


def test_error_mapping_families() -> None:
    """Unknown, rate, timestamp, not-found and duplicate codes map."""
    assert isinstance(
        bitget.map_error(200, {"code": "40010"}), errors.AdapterTimeoutError
    )
    assert isinstance(
        bitget.map_error(429, {"code": "429"}), errors.RateLimitedError
    )
    assert isinstance(
        bitget.map_error(400, {"code": "40008"}), errors.AdapterUnavailableError
    )
    assert bitget.map_error(400, {"code": "25204"}).code == "NOT_FOUND"
    assert bitget.map_error(400, {"code": "25212"}).code == "CONFLICT"
    assert isinstance(
        bitget.map_error(502, "gateway"), errors.AdapterTimeoutError
    )


@pytest.mark.parametrize(
    "value", ["BTC USDT", "BTCUSDT&side=sell", "a+b", "50%", "x/y", "\u00e9"]
)
@respx.mock(assert_all_called=False)
async def test_query_value_needing_encoding_is_refused_before_signing(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A query value that would need URL-encoding is never signed or sent."""
    route = respx.get(f"{API}/api/v3/trade/order-info").mock(
        return_value=_ok({})
    )
    signed: list[object] = []
    monkeypatch.setattr(bitget, "sign", lambda *args: signed.append(args))
    adapter = _adapter()
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    with pytest.raises(errors.AdapterUnavailableError) as info:
        await adapter._private(
            session, "GET", "/api/v3/trade/order-info", [("clientOid", value)]
        )
    assert info.value.code == "VALIDATION_FAILED"
    assert "'clientOid'" in info.value.message
    assert route.call_count == 0 and signed == []


@respx.mock
async def test_duplicates_query_cancel_and_amend() -> None:
    """Duplicates resolve, cancels pend, amends keep the clientOid."""
    info = respx.get(f"{API}/api/v3/trade/order-info").mock(
        side_effect=[
            _ok(
                {
                    "orderId": "5",
                    "clientOid": CID,
                    "orderStatus": "live",
                    "cumExecQty": "0",
                }
            ),
            _fail("25204", "order not found"),
            _ok(
                {
                    "orderId": "5",
                    "clientOid": CID,
                    "orderStatus": "live",
                    "cumExecQty": "0",
                }
            ),
            _ok(
                {
                    "orderId": "5",
                    "clientOid": CID,
                    "orderStatus": "live",
                    "cumExecQty": "0",
                }
            ),
        ]
    )
    respx.post(f"{API}/api/v3/trade/place-order").mock(
        return_value=_fail("25212", "Duplicate clientOid")
    )
    respx.post(f"{API}/api/v3/trade/cancel-order").mock(return_value=_ok({}))
    modify = respx.post(f"{API}/api/v3/trade/modify-order").mock(
        return_value=_ok({})
    )
    adapter = _adapter(
        known=[
            directory.KnownOrder(
                CID, "bitget:perp:BTCUSDT", enums.OrderMode.LIVE, 1, False
            )
        ]
    )
    duplicate = await adapter.submit_order(_request(reduce_only=True))
    assert duplicate.accepted and duplicate.state.exchange_order_id == "5"
    assert await adapter.get_order_by_client_id("acct", CID) is None
    pending = await adapter.cancel_order("acct", CID)
    assert pending.status is _State.CANCEL_PENDING
    amended = await adapter.amend_order(
        "acct", CID, _Decimal("44000"), None, _Decimal("0.02")
    )
    assert amended.status is _State.ACKNOWLEDGED
    body = json.loads(modify.calls.last.request.content)
    assert body["clientOid"] == CID and "newClientOid" not in body
    assert body["price"] == "44000" and body["qty"] == "0.02"
    assert info.call_count == 4
    with pytest.raises(errors.OrderRejectedError):
        await adapter.amend_order("acct", CID, None, _Decimal(1), None)


@respx.mock
async def test_reads_parse_documented_shapes() -> None:
    """Assets, positions, open orders, triggers and fills parse."""
    respx.get(f"{API}/api/v3/account/assets").mock(
        return_value=_ok(
            {
                "accountEquity": "11.13919278",
                "effEquity": "6.19299777",
                "imr": "1.19299777",
            }
        )
    )
    respx.get(f"{API}/api/v3/position/current-position").mock(
        return_value=_ok(
            {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "posSide": "short",
                        "total": "0.5",
                        "avgPrice": "108674",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "posSide": "long",
                        "total": "0",
                        "avgPrice": "0",
                    },
                ]
            }
        )
    )
    respx.get(f"{API}/api/v3/trade/unfilled-orders").mock(
        return_value=_ok(
            {
                "list": [
                    {
                        "orderId": "1",
                        "clientOid": CID,
                        "orderStatus": "partially_filled",
                        "cumExecQty": "0.1",
                    }
                ]
            }
        )
    )
    respx.get(f"{API}/api/v3/trade/unfilled-strategy-orders").mock(
        return_value=_ok(
            [{"orderId": "2", "clientOid": f"s{CID[1:]}", "status": "pending"}]
        )
    )
    respx.get(f"{API}/api/v3/trade/fills").mock(
        return_value=_ok(
            {
                "list": [
                    {
                        "execId": "131111111111111111",
                        "orderId": "1",
                        "clientOid": CID,
                        "execPrice": "106950.1",
                        "execQty": "0.01",
                        "tradeScope": "taker",
                        "feeDetail": [{"feeCoin": "USDT", "fee": "0.6417006"}],
                        "createdTime": "1795000000000",
                    }
                ],
                "cursor": "",
            }
        )
    )
    adapter = _adapter()
    snap = await adapter.account_snapshot("acct")
    assert snap.equity == _Decimal(
        "11.13919278"
    ) and snap.available_margin == _Decimal(5)
    (position,) = await adapter.list_positions("acct")
    assert position.signed_qty == _Decimal("-0.5")
    orders = await adapter.list_open_orders("acct")
    assert [o.status for o in orders] == [
        _State.PARTIALLY_FILLED,
        _State.ACKNOWLEDGED,
    ]
    assert orders[1].client_order_id == f"{CID}-sl"
    (fill,) = await adapter.list_recent_fills("acct", 0)
    assert fill.fee == _Decimal("0.6417006") and fill.client_order_id == CID


def test_stream_parser_on_documented_pushes() -> None:
    """The v3 fill, order, position and account pushes parse."""
    adapter = _adapter()
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    fill_push = {
        "data": [
            {
                "symbol": "BTCUSDT",
                "orderType": "market",
                "updatedTime": "1736378720623",
                "side": "buy",
                "orderId": "1288888888888888888",
                "execPnl": "0",
                "feeDetail": [{"feeCoin": "USDT", "fee": "0.569958"}],
                "execTime": "1736378720623",
                "tradeScope": "taker",
                "tradeSide": "open",
                "execId": "1288888888888888888",
                "execLinkId": "1288888888888888888",
                "execPrice": "94993",
                "holdSide": "long",
                "execValue": "949.93",
                "category": "usdt-futures",
                "execQty": "0.01",
                "clientOid": CID,
                "isRPI": "no",
            }
        ],
        "arg": {"instType": "UTA", "topic": "fill"},
        "action": "snapshot",
        "ts": 1733904123981,
    }
    (fill,) = adapter.parse(session, json.dumps(fill_push))
    assert fill.fill is not None and fill.fill.price == _Decimal(94993)
    assert fill.fill.ts_ms == 1736378720623
    order_push = {
        "arg": {"instType": "UTA", "topic": "order"},
        "data": [
            {
                "category": "usdt-futures",
                "orderId": "9",
                "clientOid": CID,
                "orderStatus": "cancelled",
                "cumExecQty": "0",
            }
        ],
    }
    (order,) = adapter.parse(session, json.dumps(order_push))
    assert order.order is not None and order.order.status is _State.CANCELED
    spot = {"arg": {"topic": "order"}, "data": [{"category": "spot"}]}
    assert adapter.parse(session, json.dumps(spot)) == []
    position_push = {
        "arg": {"topic": "position"},
        "data": [
            {
                "symbol": "BTCUSDT",
                "posSide": "long",
                "total": "0.2",
                "avgPrice": "1",
            }
        ],
    }
    (pos,) = adapter.parse(session, json.dumps(position_push))
    assert pos.position is not None and pos.position.signed_qty == _Decimal(
        "0.2"
    )
    account_push = {
        "arg": {"topic": "account"},
        "ts": 5,
        "data": [{"accountEquity": "10", "effEquity": "9", "imr": "2"}],
    }
    (bal,) = adapter.parse(session, json.dumps(account_push))
    assert bal.balance is not None and bal.balance.available_margin == _Decimal(
        7
    )
    assert adapter.parse(session, "pong") == []
    assert adapter.parse(session, '{"event":"error","code":"30005"}') == []


@respx.mock
async def test_private_stream_logs_in_and_subscribes() -> None:
    """The stream logs in with the WS signature and subscribes 4 topics."""
    for path in (
        "/api/v3/trade/fills",
        "/api/v3/trade/unfilled-orders",
        "/api/v3/position/current-position",
    ):
        respx.get(f"{API}{path}").mock(return_value=_ok({"list": []}))
    respx.get(f"{API}/api/v3/trade/unfilled-strategy-orders").mock(
        return_value=_ok([])
    )
    respx.get(f"{API}/api/v3/account/assets").mock(
        return_value=_ok({"accountEquity": "1", "effEquity": "1", "imr": "0"})
    )
    mono = venue_fakes.ManualMonotonic()
    order_push = json.dumps(
        {
            "arg": {"topic": "order"},
            "data": [
                {
                    "category": "USDT-FUTURES",
                    "orderId": "1",
                    "clientOid": CID,
                    "orderStatus": "new",
                }
            ],
        }
    )
    refused = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"event":"error","code":"30005","msg":"bad"}'])],
        mono,
    )
    with pytest.raises(errors.VenuePermissionError):
        await venue_fakes.collect(
            _adapter(connector=refused, mono=mono).stream_account_events(
                "acct"
            ),
            1,
        )
    connector = venue_fakes.FakeConnector(
        [
            venue_fakes.Script([], OSError("dropped")),
            venue_fakes.Script(
                ["pong", '{"event":"login","code":"0"}', order_push]
            ),
        ],
        mono,
    )
    adapter = _adapter(connector=connector, mono=mono)
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    assert connector.urls[0] == "wss://ws.bitget.com/v3/ws/private"
    login = json.loads(connector.connections[0].sent[0])
    args = login["args"][0]
    assert args["sign"] == bitget.sign(
        KEYS["api_secret"], args["timestamp"], "GET", "/user/verify", ""
    )
    assert args["timestamp"] == "1795000000"
    subscribe = json.loads(connector.connections[1].sent[1])
    assert [a["topic"] for a in subscribe["args"]] == [
        "order",
        "fill",
        "position",
        "account",
    ]
    assert events[-1].order is not None
