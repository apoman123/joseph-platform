# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""MEXC futures adapter: permission probe, contracts, errors, stream."""

from __future__ import annotations

import decimal
import json

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import mexc
from joseph_execution.adapters.venues import mexc_protobuf
from joseph_execution.adapters.venues import signing
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

_Decimal = decimal.Decimal
_State = enums.OrderState
API = "https://api.mexc.com"
CID = "j0123456789abcdef0123456789abcd"
KEYS = {"api_key": "mx0aBYs33eIilxBW5C", "api_secret": "mexc-secret-value"}
DETAIL = {
    "symbol": "BTC_USDT",
    "contractSize": 0.0001,
    "priceUnit": 0.1,
    "volUnit": 1,
    "minVol": 1,
    "apiAllowed": True,
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


def _known(
    cid: str,
    kind: enums.OrderType = enums.OrderType.LIMIT,
    oid: str | None = None,
) -> directory.KnownOrder:
    """Builds a directory entry.

    Args:
        cid: Client order id.
        kind: Order type.
        oid: Venue order id, if known.

    Returns:
        A LIVE, non-reduce-only BTC_USDT entry.
    """
    return directory.KnownOrder(
        cid, "mexc:perp:BTC_USDT", enums.OrderMode.LIVE, 1, False, kind, oid
    )


def _adapter(
    fields: dict[str, str] | None = None,
    known: list[directory.KnownOrder] | None = None,
    connector: venue_fakes.FakeConnector | None = None,
    mono: venue_fakes.ManualMonotonic | None = None,
    urls: dict[str, str] | None = None,
) -> mexc.MexcFuturesAdapter:
    """Builds an adapter for account ``acct``.

    Args:
        fields: Credential fields of ``acct``; ``KEYS`` when empty.
        known: OMS orders visible through the directory.
        connector: Fake WebSocket connector; one without scripts by default.
        mono: Manual monotonic clock; a new one by default.
        urls: Endpoint URL overrides, such as ``ws_url``.

    Returns:
        The LIVE-enabled adapter, on a manual wall clock at
        1_795_000_000_000 ms.
    """
    clock = mono or venue_fakes.ManualMonotonic()
    return mexc.MexcFuturesAdapter(
        catalog.DOCUMENTED["mexc"],
        credentials.StaticCredentials({"acct": fields or KEYS}),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True),
        directory=_Directory(known or []),
        connect=connector or venue_fakes.FakeConnector([], clock),
        monotonic=clock,
        sleep=clock.sleep,
        urls=urls,
    )


def _pb(member: str, channel: str, **fields: object) -> bytes:
    """Encodes a private push as the Protobuf endpoint would.

    Args:
        member: ``ContractWrapper`` payload member, such as ``orderChange``.
        channel: Wrapper ``channel`` text.
        **fields: Payload fields (decimals as strings, like MEXC).

    Returns:
        Serialized ``ContractWrapper``.
    """
    schema = mexc_protobuf.SCHEMA
    wrapper = schema.message_class("ContractWrapper")(  # type: ignore[call-arg]  # runtime-built message class takes its fields
        channel=channel, symbol="BTC_USDT", ts=1760942212000
    )
    payload = schema.message_class(member[0].upper() + member[1:])(**fields)
    getattr(wrapper, member).CopyFrom(payload)
    return wrapper.SerializeToString()


def _ok(data: object) -> httpx.Response:
    """Builds a MEXC success answer.

    Args:
        data: Value of the ``data`` field.

    Returns:
        A 200 response with MEXC's success envelope.
    """
    return httpx.Response(200, json={"success": True, "code": 0, "data": data})


def _fail(code: int, message: str) -> httpx.Response:
    """Builds a MEXC error answer: HTTP 200 with ``success`` false.

    Args:
        code: Venue error code.
        message: Venue message.

    Returns:
        The error response.
    """
    return httpx.Response(
        200, json={"success": False, "code": code, "message": message}
    )


def _check(request: httpx.Request) -> None:
    """Recomputes the signature of a private request and asserts headers.

    Args:
        request: Captured request.
    """
    timestamp = request.headers["Request-Time"]
    payload = (
        request.content.decode()
        if request.method == "POST"
        else request.url.query.decode()
    )
    assert request.headers["Signature"] == signing.hmac_sha256_hex(
        KEYS["api_secret"], f"{KEYS['api_key']}{timestamp}{payload}"
    )
    assert request.headers["ApiKey"] == KEYS["api_key"]
    assert request.headers["Recv-Window"] == "5"


def _detail() -> respx.Route:
    """Mocks the contract detail of BTC_USDT (``DETAIL``).

    Returns:
        The mocked route.
    """
    return respx.get(f"{API}/api/v1/contract/detail").mock(
        return_value=_ok(DETAIL)
    )


def _request(**overrides: object) -> trading.VenueOrderRequest:
    """Builds an order request for account ``acct``.

    Args:
        **overrides: Request fields that replace the defaults.

    Returns:
        By default a post-only limit sell of 0.0005 BTC at 150000, 2x
        isolated.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": "mexc:perp:BTC_USDT",
        "client_order_id": CID,
        "side": enums.OrderSide.SELL,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": _Decimal("0.0005"),
        "time_in_force": enums.TimeInForce.POST_ONLY,
        "reduce_only": False,
        "limit_price": _Decimal("150000"),
        "leverage": _Decimal(2),
        "margin_mode": enums.MarginMode.ISOLATED,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


@respx.mock
async def test_negotiation_probes_mode_and_order_permission() -> None:
    """One-way mode and a harmless cancel probe prove the permission."""
    respx.get(f"{API}/api/v1/contract/ping").mock(return_value=_ok(1))
    respx.get(f"{API}/api/v1/private/position/position_mode").mock(
        return_value=_ok(2)
    )
    probe = respx.post(f"{API}/api/v1/private/order/cancel").mock(
        return_value=_ok(
            [{"orderId": 0, "errorCode": 2040, "errorMsg": "order not exist"}]
        )
    )
    caps = await _adapter().capabilities("acct")
    assert caps.status is enums.TradingStatus.AVAILABLE
    assert json.loads(probe.calls.last.request.content) == [0]
    _check(probe.calls.last.request)
    assert any("no sandbox" in n for n in caps.notes)
    respx.post(f"{API}/api/v1/private/order/cancel").mock(
        return_value=_fail(702, "Please enable API Key write access")
    )
    caps = await _adapter().capabilities("acct")
    assert (
        "permission missing: MEXC futures order placement needs KYC"
        in caps.notes[-1]
    )
    respx.get(f"{API}/api/v1/private/position/position_mode").mock(
        return_value=_ok(1)
    )
    caps = await _adapter().capabilities("acct")
    assert caps.notes[-1].startswith("UNSUPPORTED")
    sandbox = await _adapter({**KEYS, "environment": "testnet"}).capabilities(
        "acct"
    )
    assert "no test environment" in sandbox.notes[-1]


@respx.mock
async def test_orders_convert_to_contracts_and_sign_bodies() -> None:
    """Base quantity becomes ``vol`` contracts; fields follow the docs."""
    _detail()
    create = respx.post(f"{API}/api/v1/private/order/create").mock(
        return_value=_ok({"orderId": "739113577038255616", "ts": 1761888808839})
    )
    adapter = _adapter()
    result = await adapter.submit_order(_request())
    assert result.state.exchange_order_id == "739113577038255616"
    _check(create.calls.last.request)
    assert json.loads(create.calls.last.request.content) == {
        "symbol": "BTC_USDT",
        "vol": "5",
        "side": 3,
        "openType": 1,
        "positionMode": 2,
        "leverage": 2,
        "type": 2,
        "price": "150000",
        "externalOid": CID,
    }
    off_grid = await adapter.submit_order(
        _request(quantity_base=_Decimal("0.00015"))
    )
    assert off_grid.state.status is _State.REJECTED
    assert off_grid.state.reason is not None
    assert off_grid.state.reason.startswith("INVALID_LOT_SIZE")


@respx.mock
async def test_market_and_plan_orders() -> None:
    """Market orders carry the ticker price; stops are 7-day plan orders."""
    _detail()
    respx.get(f"{API}/api/v1/contract/ticker").mock(
        return_value=_ok({"lastPrice": 95000.5})
    )
    create = respx.post(f"{API}/api/v1/private/order/create").mock(
        return_value=_ok({"orderId": "1"})
    )
    plan = respx.post(f"{API}/api/v1/private/planorder/place/v2").mock(
        return_value=_ok("739206374277809664")
    )
    adapter = _adapter()
    await adapter.submit_order(
        _request(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
            reduce_only=True,
            side=enums.OrderSide.BUY,
        )
    )
    body = json.loads(create.calls.last.request.content)
    assert body["type"] == 5 and body["price"] == "95000.5"
    assert body["side"] == 2 and body["reduceOnly"] is True
    stop = await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-sl",
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=_Decimal("80000"),
            reduce_only=True,
        )
    )
    assert stop.state.exchange_order_id == "plan:739206374277809664"
    sent = json.loads(plan.calls.last.request.content)
    assert sent["triggerType"] == 2 and sent["trend"] == 2
    assert sent["executeCycle"] == 2 and sent["orderType"] == 5
    assert sent["side"] == 4


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (2005, "INSUFFICIENT_MARGIN"),
        (200005, "AUTH_SCOPE_MISSING"),
        (2015, "INVALID_LOT_SIZE"),
        (7008, "MIN_NOTIONAL"),
        (600, "VALIDATION_FAILED"),
    ],
)
@respx.mock
async def test_rejections_are_rejected_results(
    code: int, expected: str
) -> None:
    """Margin, KYC/permission and parameter errors become REJECTED."""
    _detail()
    respx.post(f"{API}/api/v1/private/order/create").mock(
        return_value=_fail(code, "refused")
    )
    result = await _adapter().submit_order(_request())
    assert result.state.status is _State.REJECTED
    assert result.state.reason is not None and result.state.reason.startswith(
        expected
    )


def test_error_mapping_families() -> None:
    """Unknown, rate and not-found codes map to their families."""
    assert isinstance(
        mexc.map_error(200, {"code": 9999}), errors.AdapterTimeoutError
    )
    assert isinstance(
        mexc.map_error(200, {"code": 510}), errors.RateLimitedError
    )
    assert mexc.map_error(200, {"code": 2040}).code == "NOT_FOUND"
    assert mexc.map_error(200, {"code": 2042}).code == "CONFLICT"
    assert mexc.map_error(200, {"code": 2007}).code == "INVALID_TICK_SIZE"
    assert isinstance(mexc.map_error(503, "x"), errors.AdapterTimeoutError)
    assert mexc.map_error(200, {"code": "x"}).code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "value", ["BTC USDT", "BTC_USDT&side=2", "a+b", "50%", "x/y", "\u00e9"]
)
@respx.mock(assert_all_called=False)
async def test_query_value_needing_encoding_is_refused_before_signing(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A query value that would need URL-encoding is never signed or sent."""
    route = respx.get(f"{API}/api/v1/private/order/list/order_deals/v3").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    signed: list[object] = []
    monkeypatch.setattr(
        signing, "hmac_sha256_hex", lambda *args: signed.append(args)
    )
    adapter = _adapter()
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    with pytest.raises(errors.AdapterUnavailableError) as info:
        await adapter._private(
            session,
            "GET",
            "/api/v1/private/order/list/order_deals/v3",
            [("symbol", value), ("page_num", "1")],
        )
    assert info.value.code == "VALIDATION_FAILED"
    assert "'symbol'" in info.value.message
    assert route.call_count == 0 and signed == []


@respx.mock
async def test_query_cancel_and_no_amend() -> None:
    """Orders resolve by externalOid; cancels use the venue order id."""
    _detail()
    by_external = respx.get(
        f"{API}/api/v1/private/order/external/BTC_USDT/{CID}"
    ).mock(
        side_effect=[
            _fail(2040, "order not exist"),
            _ok(
                {
                    "orderId": "7",
                    "symbol": "BTC_USDT",
                    "state": 2,
                    "dealVol": 1,
                    "externalOid": CID,
                }
            ),
            _ok(
                {
                    "orderId": "7",
                    "symbol": "BTC_USDT",
                    "state": 4,
                    "dealVol": 1,
                    "externalOid": CID,
                }
            ),
        ]
    )
    cancel = respx.post(f"{API}/api/v1/private/order/cancel").mock(
        return_value=_ok(
            [{"orderId": 7, "errorCode": 0, "errorMsg": "success"}]
        )
    )
    adapter = _adapter(known=[_known(CID)])
    assert await adapter.get_order_by_client_id("acct", CID) is None
    state = await adapter.cancel_order("acct", CID)
    assert state.status is _State.CANCELED and state.filled_base == _Decimal(
        "0.0001"
    )
    assert json.loads(cancel.calls.last.request.content) == [7]
    assert by_external.call_count == 3
    with pytest.raises(errors.AdapterUnavailableError) as info:
        await adapter.amend_order("acct", CID, _Decimal(1), None, None)
    assert info.value.code == "UNSUPPORTED"


@respx.mock
async def test_reads_convert_contracts_to_base_units() -> None:
    """Positions and fills are reported in base units."""
    _detail()
    respx.get(f"{API}/api/v1/private/account/assets").mock(
        return_value=_ok(
            [
                {
                    "currency": "USDT",
                    "equity": 32.8,
                    "availableOpen": 30.1,
                    "availableBalance": 31,
                }
            ]
        )
    )
    respx.get(f"{API}/api/v1/private/position/open_positions").mock(
        return_value=_ok(
            [
                {
                    "symbol": "BTC_USDT",
                    "positionType": 2,
                    "holdVol": 5,
                    "holdAvgPrice": 109777.5,
                }
            ]
        )
    )
    respx.get(f"{API}/api/v1/private/order/list/open_orders").mock(
        return_value=_ok(
            [
                {
                    "orderId": "7",
                    "symbol": "BTC_USDT",
                    "state": 2,
                    "dealVol": 0,
                    "externalOid": CID,
                }
            ]
        )
    )
    respx.get(f"{API}/api/v1/private/order/list/order_deals/v3").mock(
        return_value=_ok(
            [
                {
                    "id": "9466823344",
                    "symbol": "BTC_USDT",
                    "vol": 1,
                    "price": 0.18695,
                    "fee": 0.01,
                    "feeCurrency": "USDT",
                    "orderId": "7",
                    "timestamp": 1795000000000,
                    "taker": True,
                },
                {
                    "id": "1",
                    "symbol": "BTC_USDT",
                    "vol": 1,
                    "price": 1,
                    "fee": 0,
                    "orderId": "999",
                    "timestamp": 1795000000000,
                },
            ]
        )
    )
    adapter = _adapter(known=[_known(CID, oid="7")])
    snap = await adapter.account_snapshot("acct")
    assert snap.equity == _Decimal(
        "32.8"
    ) and snap.available_margin == _Decimal("30.1")
    (position,) = await adapter.list_positions("acct")
    assert position.signed_qty == _Decimal("-0.0005")
    (order,) = await adapter.list_open_orders("acct")
    assert order.client_order_id == CID
    (fill,) = await adapter.list_recent_fills("acct", 0)
    assert (
        fill.quantity_base == _Decimal("0.0001") and fill.liquidity == "taker"
    )


async def test_stream_parser_on_documented_pushes() -> None:
    """Order, deal, position, asset and plan pushes parse as documented."""
    mono = venue_fakes.ManualMonotonic()
    adapter = _adapter(mono=mono)
    adapter._contracts["BTC_USDT"] = (
        mono(),
        mexc.Contract("BTC_USDT", _Decimal("0.0001"), _Decimal(1), True),
    )
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    order = {
        "channel": "push.personal.order",
        "data": {
            "orderId": 123456789,
            "symbol": "BTC_USDT",
            "positionId": 987654321,
            "price": 45000.5,
            "vol": 10,
            "leverage": 20,
            "side": 1,
            "category": 1,
            "orderType": 1,
            "dealAvgPrice": 45000.0,
            "dealVol": 5,
            "state": 2,
            "errorCode": 0,
            "externalOid": CID,
            "updateTime": 1760942212000,
        },
        "ts": 1760942212000,
    }
    (update,) = await adapter.parse(session, json.dumps(order))
    assert (
        update.order is not None
        and update.order.status is _State.PARTIALLY_FILLED
    )
    assert update.order.filled_base == _Decimal("0.0005")
    deal = {
        "channel": "push.personal.order.deal",
        "data": {
            "id": 987654321,
            "symbol": "BTC_USDT",
            "side": 1,
            "vol": 10,
            "price": 45000.5,
            "feeCurrency": "USDT",
            "fee": 0.225,
            "timestamp": 1760942212000,
            "isTaker": True,
            "orderId": 123456789,
            "externalOid": CID,
        },
    }
    (fill,) = await adapter.parse(session, json.dumps(deal))
    assert fill.fill is not None and fill.fill.client_order_id == CID
    assert fill.fill.quantity_base == _Decimal("0.0010")
    position = {
        "channel": "push.personal.position",
        "data": {
            "symbol": "BTC_USDT",
            "holdVol": 10,
            "positionType": 1,
            "state": 3,
        },
    }
    (flat,) = await adapter.parse(session, json.dumps(position))
    assert flat.position is not None and flat.position.signed_qty == 0
    asset = {
        "channel": "push.personal.asset",
        "data": {
            "currency": "USDT",
            "positionMargin": 5000.0,
            "frozenBalance": 1000.0,
            "availableBalance": 9000.0,
        },
        "ts": 1,
    }
    (balance,) = await adapter.parse(session, json.dumps(asset))
    assert balance.balance is not None and balance.balance.equity == _Decimal(
        15000
    )
    session.plans["55"] = f"{CID}-sl"
    plan = {
        "channel": "push.personal.plan.order",
        "data": {"id": 55, "state": 2, "orderId": 0},
    }
    (canceled,) = await adapter.parse(session, json.dumps(plan))
    assert (
        canceled.order is not None and canceled.order.status is _State.CANCELED
    )
    assert await adapter.parse(session, '{"channel":"pong","data":1}') == []
    assert (
        await adapter.parse(session, '{"channel":"rs.error","data":"x"}') == []
    )


@respx.mock
async def test_stream_logs_in_and_relies_on_default_pushes() -> None:
    """The stream signs ``apiKey + reqTime``; a refused login raises."""
    _detail()
    respx.get(f"{API}/api/v1/private/order/list/open_orders").mock(
        return_value=_ok([])
    )
    respx.get(f"{API}/api/v1/private/position/open_positions").mock(
        return_value=_ok([])
    )
    respx.get(f"{API}/api/v1/private/account/assets").mock(
        return_value=_ok(
            [{"currency": "USDT", "equity": 1, "availableOpen": 1}]
        )
    )
    mono = venue_fakes.ManualMonotonic()
    deal = json.dumps(
        {
            "channel": "push.personal.order.deal",
            "data": {
                "id": 1,
                "symbol": "BTC_USDT",
                "vol": 1,
                "price": 1,
                "orderId": 5,
                "externalOid": CID,
                "timestamp": 1,
            },
        }
    )
    connector = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"channel":"rs.login","data":"success"}', deal])],
        mono,
    )
    adapter = _adapter(connector=connector, mono=mono)
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    login = json.loads(connector.connections[0].sent[0])
    param = login["param"]
    assert param["signature"] == signing.hmac_sha256_hex(
        KEYS["api_secret"], f"{KEYS['api_key']}{param['reqTime']}"
    )
    assert events[-1].fill is not None
    refused = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"channel":"rs.error","data":"bad"}'])], mono
    )
    with pytest.raises(errors.VenuePermissionError):
        await venue_fakes.collect(
            _adapter(connector=refused, mono=mono).stream_account_events(
                "acct"
            ),
            1,
        )


async def test_stream_parser_on_protobuf_pushes() -> None:
    """Binary pushes parse like their JSON twins (decimals as strings)."""
    mono = venue_fakes.ManualMonotonic()
    adapter = _adapter(mono=mono)
    adapter._contracts["BTC_USDT"] = (
        mono(),
        mexc.Contract("BTC_USDT", _Decimal("0.0001"), _Decimal(1), True),
    )
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    order = _pb(
        "orderChange",
        "push.personal.order",
        orderId="123456789",
        symbol="BTC_USDT",
        price="45000.5",
        vol="10",
        dealVol="5",
        state=2,
        externalOid=CID,
        updateTime=1760942212000,
    )
    (update,) = await adapter.parse(session, order)
    assert update.order is not None
    assert update.order.status is _State.PARTIALLY_FILLED
    assert update.order.client_order_id == CID
    assert update.order.filled_base == _Decimal("0.0005")
    assert update.order.reason is None
    maker = _pb(
        "orderDealDetail",
        "push.personal.order.deal",
        id="987654321",
        symbol="BTC_USDT",
        vol="10",
        price="45000.5",
        feeCurrency="USDT",
        fee="0.225",
        timestamp=1760942212000,
        orderId="123456789",
    )
    (fill,) = await adapter.parse(session, maker)
    assert fill.fill is not None and fill.fill.client_order_id == CID
    assert fill.fill.quantity_base == _Decimal("0.0010")
    assert fill.fill.price == _Decimal("45000.5") and fill.fill.fee == _Decimal(
        "0.225"
    )
    assert fill.fill.liquidity == "maker"
    position = _pb(
        "positionChange",
        "push.personal.position",
        symbol="BTC_USDT",
        holdVol="10",
        positionType=2,
        state=1,
        holdAvgPrice="45000",
    )
    (short,) = await adapter.parse(session, position)
    assert short.position is not None
    assert short.position.signed_qty == _Decimal("-0.0010")
    asset = _pb(
        "assetChange",
        "push.personal.asset",
        currency="USDT",
        positionMargin="5000",
        frozenBalance="1000",
        availableBalance="9000",
    )
    (balance,) = await adapter.parse(session, asset)
    assert balance.balance is not None and balance.balance.equity == _Decimal(
        15000
    )
    assert balance.balance.ts_ms == 1760942212000
    session.plans["55"] = f"{CID}-sl"
    plan = _pb(
        "contractPlanOrder",
        "push.personal.plan.order",
        id="55",
        state=2,
        orderId="0",
    )
    (canceled,) = await adapter.parse(session, plan)
    assert (
        canceled.order is not None and canceled.order.status is _State.CANCELED
    )
    assert "0" not in session.order_ids


@respx.mock
async def test_stream_logs_in_on_the_protobuf_endpoint() -> None:
    """``ws_url`` selects the Protobuf endpoint; JSON acks still log in."""
    _detail()
    respx.get(f"{API}/api/v1/private/order/list/open_orders").mock(
        return_value=_ok([])
    )
    respx.get(f"{API}/api/v1/private/position/open_positions").mock(
        return_value=_ok([])
    )
    respx.get(f"{API}/api/v1/private/account/assets").mock(
        return_value=_ok(
            [{"currency": "USDT", "equity": 1, "availableOpen": 1}]
        )
    )
    mono = venue_fakes.ManualMonotonic()
    deal = _pb(
        "orderDealDetail",
        "push.personal.order.deal",
        id="1",
        symbol="BTC_USDT",
        vol="1",
        price="1",
        orderId="5",
        externalOid=CID,
        timestamp=1,
        taker=True,
    )
    connector = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"channel":"rs.login","data":"success"}', deal])],
        mono,
    )
    adapter = _adapter(
        connector=connector, mono=mono, urls={"ws_url": mexc.WS_PROTOBUF_URL}
    )
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    assert connector.urls == [mexc.WS_PROTOBUF_URL]
    fill = events[-1].fill
    assert fill is not None and fill.client_order_id == CID
    assert fill.liquidity == "taker"
