"""Hyperliquid adapter: agent checks, signed actions, precision, stream."""

from __future__ import annotations

import decimal
import importlib
import json
from typing import Any

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import hyperliquid
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import wire
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

D = decimal.Decimal
S = enums.OrderState
API = "https://api.hyperliquid.xyz"
AGENT_KEY = "0x" + "11" * 32
MASTER = "0x" + "ab" * 20
CID = "j0123456789abcdef0123456789abcd"
KEYS = {"agent_private_key": AGENT_KEY, "account_address": MASTER}
META = {
    "universe": [
        {"name": "BTC", "szDecimals": 5, "maxLeverage": 50},
        {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
    ]
}


class _Directory:
    """In-memory order directory."""

    def __init__(self, known: list[directory.KnownOrder]) -> None:
        """Initializes the directory.

        Args:
            known: Orders.
        """
        self._known = {k.client_order_id: k for k in known}

    def find(
        self, account_id: str, client_order_id: str
    ) -> directory.KnownOrder | None:
        """Finds an order.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        del account_id
        return self._known.get(client_order_id)

    def recent(
        self, account_id: str, since_ms: int
    ) -> list[directory.KnownOrder]:
        """Lists orders.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Orders.
        """
        del account_id, since_ms
        return list(self._known.values())


def _known(cid: str = CID) -> directory.KnownOrder:
    """Builds a directory entry.

    Args:
        cid: Client id.

    Returns:
        Entry.
    """
    return directory.KnownOrder(
        cid, "hyperliquid:perp:BTC", enums.OrderMode.LIVE, 1, False
    )


def _adapter(
    fields: dict[str, str] | None = None,
    known: list[directory.KnownOrder] | None = None,
    connector: venue_fakes.FakeConnector | None = None,
    mono: venue_fakes.ManualMonotonic | None = None,
) -> hyperliquid.HyperliquidAdapter:
    """Builds an adapter for account ``acct``.

    Args:
        fields: Credentials.
        known: Directory orders.
        connector: Fake WebSocket connector.
        mono: Manual monotonic clock.

    Returns:
        Adapter.
    """
    clock = mono or venue_fakes.ManualMonotonic()
    return hyperliquid.HyperliquidAdapter(
        catalog.DOCUMENTED["hyperliquid"],
        credentials.StaticCredentials({"acct": fields or KEYS}),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True, market_slippage_bps=100),
        directory=_Directory(known or []),
        connect=connector or venue_fakes.FakeConnector([], clock),
        monotonic=clock,
        sleep=clock.sleep,
    )


def _info(responses: dict[str, Any]) -> respx.Route:
    """Mocks ``/info`` by request type.

    Args:
        responses: Body by ``type``.

    Returns:
        Route.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        """Answers one info request.

        Args:
            request: Request.

        Returns:
            Response.
        """
        kind = json.loads(request.content)["type"]
        return httpx.Response(200, json=responses[kind])

    return respx.post(f"{API}/info").mock(side_effect=answer)


def _signer_of(body: dict[str, Any]) -> str:
    """Recovers the address that signed an ``/exchange`` body.

    Args:
        body: Request body.

    Returns:
        Lower-case address.
    """
    account = importlib.import_module("eth_account")
    messages = importlib.import_module("eth_account.messages")
    digest = hyperliquid.action_hash(
        body["action"],
        body["vaultAddress"],
        body["nonce"],
        body["expiresAfter"],
    )
    encoded = messages.encode_typed_data(
        full_message=hyperliquid.l1_payload(digest, True)
    )
    signature = body["signature"]
    recovered = account.Account.recover_message(
        encoded,
        vrs=(signature["v"], int(signature["r"], 16), int(signature["s"], 16)),
    )
    return str(recovered).lower()


def _exchange(*statuses: Any) -> respx.Route:
    """Mocks ``/exchange`` answers in order.

    Args:
        *statuses: ``response.data`` objects or full bodies.

    Returns:
        Route.
    """
    replies = [
        httpx.Response(
            200,
            json=s
            if isinstance(s, dict) and "status" in s
            else {"status": "ok", "response": {"type": "order", "data": s}},
        )
        for s in statuses
    ]
    return respx.post(f"{API}/exchange").mock(side_effect=replies)


def _request(**overrides: object) -> trading.VenueOrderRequest:
    """Builds an order request.

    Args:
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": "hyperliquid:perp:BTC",
        "client_order_id": CID,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D("0.001"),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "limit_price": D("50000"),
        "leverage": D(5),
        "margin_mode": enums.MarginMode.CROSS,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]


async def test_credential_checks_refuse_master_keys_and_bad_addresses() -> None:
    """The master key is never accepted as the agent; addresses validate."""
    master = hyperliquid.address_of(AGENT_KEY)
    caps = await _adapter(
        {"agent_private_key": AGENT_KEY, "account_address": master}
    ).capabilities("acct")
    assert "account's own key" in caps.notes[-1]
    caps = await _adapter(
        {"agent_private_key": AGENT_KEY, "account_address": "0x12"}
    ).capabilities("acct")
    assert "0x-prefixed" in caps.notes[-1]
    caps = await _adapter(
        {"agent_private_key": "zz", "account_address": MASTER}
    ).capabilities("acct")
    assert "not a valid" in caps.notes[-1]
    assert hyperliquid.signing_available()


@respx.mock
async def test_negotiation_checks_the_agent_role() -> None:
    """Only an agent approved for the configured account negotiates."""
    agent = hyperliquid.address_of(AGENT_KEY)
    state = {"marginSummary": {"accountValue": "100", "totalMarginUsed": "10"}}
    route = _info(
        {
            "allMids": {"BTC": "95000.5"},
            "userRole": {"role": "agent", "data": {"user": MASTER}},
            "clearinghouseState": state,
        }
    )
    caps = await _adapter().capabilities("acct")
    assert caps.status is enums.TradingStatus.AVAILABLE
    assert any(agent in n for n in caps.notes)
    role = json.loads(route.calls[1].request.content)
    assert role == {"type": "userRole", "user": agent}
    _info({"allMids": {"BTC": "1"}, "userRole": {"role": "missing"}})
    caps = await _adapter().capabilities("acct")
    assert "permission missing: approve this API wallet" in caps.notes[-1]
    _info({"allMids": {}})
    caps = await _adapter().capabilities("acct")
    assert caps.notes[-1].startswith("Venue health check failed")


@respx.mock
async def test_limit_order_sets_leverage_and_is_agent_signed() -> None:
    """Orders are signed by the agent with the documented wire format."""
    _info({"meta": META})
    exchange = _exchange(
        {"status": "ok", "response": {"type": "default"}},
        {"statuses": [{"resting": {"oid": 77738308}}]},
        {"statuses": [{"resting": {"oid": 77738309}}]},
    )
    adapter = _adapter()
    result = await adapter.submit_order(
        _request(time_in_force=enums.TimeInForce.POST_ONLY)
    )
    assert result.state.status is S.ACKNOWLEDGED
    assert result.state.exchange_order_id == "77738308"
    await adapter.submit_order(_request(client_order_id=f"{CID}-tp1"))
    assert exchange.call_count == 3
    leverage, order, _ = (json.loads(c.request.content) for c in exchange.calls)
    assert leverage["action"] == {
        "type": "updateLeverage",
        "asset": 0,
        "isCross": True,
        "leverage": 5,
    }
    assert order["action"] == {
        "type": "order",
        "orders": [
            {
                "a": 0,
                "b": True,
                "p": "50000",
                "s": "0.001",
                "r": False,
                "t": {"limit": {"tif": "Alo"}},
                "c": wire.cloid_of(CID),
            }
        ],
        "grouping": "na",
    }
    assert list(order["action"]["orders"][0]) == [
        "a",
        "b",
        "p",
        "s",
        "r",
        "t",
        "c",
    ]
    assert order["expiresAfter"] == order["nonce"] + 60_000
    assert order["nonce"] > leverage["nonce"]
    assert _signer_of(order) == hyperliquid.address_of(AGENT_KEY)
    await adapter.aclose()


@respx.mock
async def test_market_and_stop_orders_use_slippage_bands() -> None:
    """Market orders are banded IOC limits; stops are ``sl`` triggers."""
    _info({"meta": META, "allMids": {"BTC": "95000"}})
    exchange = _exchange(
        {
            "statuses": [
                {"filled": {"totalSz": "0.0005", "avgPx": "95010", "oid": 5}}
            ]
        },
        {"statuses": [{"resting": {"oid": 6}}]},
    )
    adapter = _adapter()
    market = await adapter.submit_order(
        _request(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
            reduce_only=True,
        )
    )
    assert market.state.status is S.CANCELED
    assert market.state.filled_base == D("0.0005")
    wire_order = json.loads(exchange.calls[0].request.content)["action"][
        "orders"
    ][0]
    assert wire_order["p"] == "95950" and wire_order["t"] == {
        "limit": {"tif": "Ioc"}
    }
    await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-sl",
            side=enums.OrderSide.SELL,
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D("90000"),
            reduce_only=True,
        )
    )
    stop = json.loads(exchange.calls[1].request.content)["action"]["orders"][0]
    assert stop["t"] == {
        "trigger": {"isMarket": True, "triggerPx": "90000", "tpsl": "sl"}
    }
    assert stop["p"] == "89100"
    assert wire.client_id_of_cloid(stop["c"]) == f"{CID}-sl"


@respx.mock
async def test_precision_and_unsupported_orders_are_refused_locally() -> None:
    """Price/size precision and FOK are refused before anything is sent."""
    _info({"meta": META})
    exchange = _exchange()
    adapter = _adapter()
    bad_price = await adapter.submit_order(
        _request(limit_price=D("95123.45"), reduce_only=True)
    )
    assert bad_price.state.reason is not None
    assert bad_price.state.reason.startswith("INVALID_TICK_SIZE")
    bad_size = await adapter.submit_order(
        _request(quantity_base=D("0.0000001"), reduce_only=True)
    )
    assert bad_size.state.reason is not None
    assert bad_size.state.reason.startswith("INVALID_LOT_SIZE")
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(
            _request(time_in_force=enums.TimeInForce.FOK, reduce_only=True)
        )
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(
            _request(instrument_id="hyperliquid:perp:NOPE", reduce_only=True)
        )
    fractional = await adapter.submit_order(_request(leverage=D("2.5")))
    assert fractional.state.status is S.REJECTED
    assert exchange.call_count == 0


def test_price_rule_matches_the_documented_examples() -> None:
    """Five significant figures and 6 - szDecimals decimals."""
    assert hyperliquid.valid_price(D("1670.1"), 4)
    assert not hyperliquid.valid_price(D("1670.12"), 4)
    assert hyperliquid.valid_price(D("123456"), 5)
    assert hyperliquid.valid_price(D("0.012345"), 0)
    assert not hyperliquid.valid_price(D("0.0123456"), 0)
    assert hyperliquid.price_wire(D("123456.7"), 5, decimal.ROUND_UP) == 123457


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (
            {"statuses": [{"error": "Insufficient margin to place order."}]},
            "INSUFFICIENT_MARGIN",
        ),
        (
            {"statuses": [{"error": "Order must have minimum value of $10."}]},
            "MIN_NOTIONAL",
        ),
        (
            {
                "statuses": [
                    {
                        "error": "Post only order would have immediately "
                        "matched, bbo was 1."
                    }
                ]
            },
            "VALIDATION_FAILED",
        ),
        (
            {
                "status": "err",
                "response": "L1 error: User or API Wallet 0x1 does not exist.",
            },
            "AUTH_SCOPE_MISSING",
        ),
    ],
)
@respx.mock
async def test_error_statuses_reject(answer: dict[str, Any], code: str) -> None:
    """Order errors and unknown signers become REJECTED results."""
    _info({"meta": META})
    _exchange(answer)
    result = await _adapter().submit_order(_request(reduce_only=True))
    assert result.state.status is S.REJECTED
    assert result.state.reason is not None and result.state.reason.startswith(
        code
    )


def test_error_mapping_families() -> None:
    """Tick, not-found and rate-limit strings map distinctly."""
    assert hyperliquid.map_error(
        "Price must be divisible by tick size."
    ).code == ("INVALID_TICK_SIZE")
    assert (
        hyperliquid.map_error(
            "Order was never placed, already canceled, or filled."
        ).code
        == "NOT_FOUND"
    )
    assert isinstance(
        hyperliquid.map_error("Too many requests"), errors.RateLimitedError
    )


@respx.mock
async def test_query_cancel_modify_and_fills() -> None:
    """Orders resolve by cloid; cancel/modify keep it; fills map by oid."""
    status = {
        "status": "order",
        "order": {
            "order": {
                "coin": "BTC",
                "side": "B",
                "limitPx": "50000",
                "sz": "0.0006",
                "oid": 91,
                "timestamp": 1,
                "origSz": "0.001",
                "cloid": wire.cloid_of(CID),
                "reduceOnly": False,
                "tif": "Gtc",
                "triggerPx": "0.0",
            },
            "status": "open",
            "statusTimestamp": 1724361546645,
        },
    }
    fills = [
        {
            "coin": "BTC",
            "px": "50000",
            "sz": "0.0004",
            "side": "B",
            "time": 1795000000000,
            "oid": 91,
            "crossed": False,
            "fee": "-0.001",
            "tid": 118906512037719,
            "feeToken": "USDC",
        },
        {
            "coin": "BTC",
            "px": "1",
            "sz": "1",
            "oid": 5,
            "tid": 1,
            "time": 1795000000000,
        },
    ]
    info = _info(
        {
            "orderStatus": status,
            "userFillsByTime": fills,
            "meta": META,
            "frontendOpenOrders": [
                {
                    "coin": "BTC",
                    "oid": 91,
                    "origSz": "0.001",
                    "sz": "0.001",
                    "timestamp": 3,
                }
            ],
            "clearinghouseState": {
                "assetPositions": [
                    {"position": {"coin": "BTC", "szi": "-0.5", "entryPx": "1"}}
                ],
                "marginSummary": {"accountValue": "10", "totalMarginUsed": "4"},
                "time": 5,
            },
        }
    )
    exchange = respx.post(f"{API}/exchange").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "response": {
                    "type": "cancel",
                    "data": {"statuses": ["success"]},
                },
            },
        )
    )
    adapter = _adapter(known=[_known()])
    state = await adapter.get_order_by_client_id("acct", CID)
    assert state is not None and state.status is S.PARTIALLY_FILLED
    assert state.filled_base == D("0.0004")
    asked = json.loads(info.calls.last.request.content)
    assert asked["oid"] == wire.cloid_of(CID) and asked["user"] == MASTER
    await adapter.cancel_order("acct", CID)
    cancel = json.loads(exchange.calls.last.request.content)["action"]
    assert cancel == {
        "type": "cancelByCloid",
        "cancels": [{"asset": 0, "cloid": wire.cloid_of(CID)}],
    }
    await adapter.amend_order("acct", CID, D("49000"), None, None)
    modify = json.loads(exchange.calls.last.request.content)["action"]
    assert modify["type"] == "batchModify"
    assert modify["modifies"][0]["oid"] == wire.cloid_of(CID)
    assert modify["modifies"][0]["order"]["p"] == "49000"
    (fill,) = await adapter.list_recent_fills("acct", 0)
    assert fill.exchange_fill_id == "118906512037719"
    assert fill.fee == D("-0.001") and fill.liquidity == "maker"
    (opened,) = await adapter.list_open_orders("acct")
    assert opened.client_order_id == CID
    (position,) = await adapter.list_positions("acct")
    assert position.signed_qty == D("-0.5")
    snap = await adapter.account_snapshot("acct")
    assert snap.available_margin == D(6) and snap.ts_ms == 5
    _info({"orderStatus": {"status": "unknownOid"}})
    assert await adapter.get_order_by_client_id("acct", "j" + "f" * 30) is None


def test_stream_parser_on_documented_messages() -> None:
    """``orderUpdates``, ``userFills`` and ``clearinghouseState`` parse."""
    adapter = _adapter()
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    assert adapter.parse(session, "Websocket connection established.") == []
    updates = {
        "channel": "orderUpdates",
        "data": [
            {
                "order": {
                    "coin": "BTC",
                    "side": "B",
                    "limitPx": "50000",
                    "sz": "0.0",
                    "oid": 7,
                    "timestamp": 1,
                    "origSz": "0.001",
                    "cloid": wire.cloid_of(f"{CID}-tp2"),
                },
                "status": "filled",
                "statusTimestamp": 9,
            },
            {
                "order": {
                    "coin": "BTC",
                    "sz": "0.001",
                    "oid": 8,
                    "origSz": "0.001",
                },
                "status": "perpMarginRejected",
                "statusTimestamp": 9,
            },
        ],
    }
    done, rejected = adapter.parse(session, json.dumps(updates))
    assert done.order is not None and done.order.client_order_id == f"{CID}-tp2"
    assert done.order.status is S.FILLED and done.order.filled_base == D(
        "0.001"
    )
    assert rejected.order is not None and rejected.order.status is S.REJECTED
    assert rejected.order.client_order_id == "oid:8"
    fills = {
        "channel": "userFills",
        "data": {
            "isSnapshot": False,
            "user": MASTER,
            "fills": [
                {
                    "coin": "BTC",
                    "px": "50000",
                    "sz": "0.001",
                    "oid": 7,
                    "tid": 3,
                    "crossed": True,
                    "fee": "0.02",
                    "time": 9,
                },
                {"coin": "BTC", "px": "1", "sz": "1", "oid": 99, "tid": 4},
            ],
        },
    }
    (fill,) = adapter.parse(session, json.dumps(fills))
    assert fill.fill is not None and fill.fill.liquidity == "taker"
    state = {
        "channel": "clearinghouseState",
        "data": {
            "dex": "",
            "user": MASTER,
            "clearinghouseState": {
                "assetPositions": [
                    {
                        "position": {
                            "coin": "ETH",
                            "szi": "0.0335",
                            "entryPx": "2986.3",
                        }
                    }
                ],
                "marginSummary": {
                    "accountValue": "13109.48",
                    "totalMarginUsed": "4.96",
                },
            },
        },
    }
    position, balance = adapter.parse(session, json.dumps(state))
    assert position.position is not None and position.position.signed_qty == D(
        "0.0335"
    )
    assert balance.balance is not None and balance.balance.currency == "USDC"
    assert adapter.parse(session, '{"channel":"pong"}') == []


@respx.mock
async def test_user_stream_subscribes_master_account_channels() -> None:
    """The stream subscribes order, fill and state channels of the master."""
    _info(
        {
            "userFillsByTime": [],
            "frontendOpenOrders": [],
            "clearinghouseState": {"marginSummary": {"accountValue": "1"}},
        }
    )
    mono = venue_fakes.ManualMonotonic()
    connector = venue_fakes.FakeConnector(
        [
            venue_fakes.Script(
                [
                    '{"channel":"subscriptionResponse","data":{}}',
                    '{"channel":"orderUpdates","data":[]}',
                    '{"channel":"userFills","data":{"fills":[{"oid":1,"tid":2,"px":"1","sz":"1"}]}}',
                ]
            )
        ],
        mono,
    )
    adapter = _adapter(connector=connector, mono=mono)
    adapter_session = adapter._session("acct", credentials.from_fields(KEYS))
    adapter_session.oids["1"] = CID
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    assert connector.urls == ["wss://api.hyperliquid.xyz/ws"]
    subscriptions = [
        json.loads(m)["subscription"] for m in connector.connections[0].sent
    ]
    assert subscriptions == [
        {"type": "orderUpdates", "user": MASTER},
        {"type": "userFills", "user": MASTER},
        {"type": "clearinghouseState", "user": MASTER, "dex": ""},
    ]
    assert events[-1].fill is not None  # type: ignore[attr-defined]
