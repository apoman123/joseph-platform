# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Lighter adapter: key checks, nonces, integer orders, errors, stream."""

from __future__ import annotations

import decimal
import json
import urllib.parse
from typing import Any

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import lighter
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import wire
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import venue_fakes

D = decimal.Decimal
S = enums.OrderState
API = "https://mainnet.zklighter.elliot.ai"
CID = "j0123456789abcdef0123456789abcd"
KEYS = {
    "api_private_key": "ab" * 40,
    "account_index": "21",
    "api_key_index": "4",
}
BOOKS = {
    "code": 200,
    "order_books": [
        {
            "symbol": "BTC",
            "market_id": 1,
            "market_type": "perp",
            "supported_size_decimals": 5,
            "supported_price_decimals": 1,
        }
    ],
}


class FakeSigner:
    """Records signing calls instead of using the native library.

    Attributes:
        calls: ``(method, args, kwargs)`` tuples.
        check: Result of ``check_client``.
    """

    def __init__(self, check: str | None = None) -> None:
        """Initializes the signer.

        Args:
            check: ``check_client`` result.
        """
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.check = check

    def check_client(self) -> str | None:
        """Returns the configured check result."""
        return self.check

    def create_auth_token_with_expiry(
        self, deadline: int = 600, *, api_key_index: int = 255
    ) -> tuple[str | None, str | None]:
        """Returns a deterministic token.

        Args:
            deadline: Lifetime.
            api_key_index: Key index.

        Returns:
            ``(token, None)``.
        """
        return f"{deadline}:21:{api_key_index}:cafe", None

    def _tx(
        self,
        name: str,
        tx_type: int,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[Any, ...]:
        """Records a signing call.

        Args:
            name: Method name.
            tx_type: Transaction type.
            args: Positional arguments.
            kwargs: Keyword arguments.

        Returns:
            ``(tx_type, tx_info, tx_hash, None)``.
        """
        self.calls.append((name, args, kwargs))
        return (
            tx_type,
            json.dumps({"Nonce": kwargs["nonce"]}),
            f"hash-{kwargs['nonce']}",
            None,
        )

    def sign_create_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Records a create.

        Args:
            *args: Fields.
            **kwargs: Nonce and key index.

        Returns:
            Signed tuple.
        """
        return self._tx("create", 14, args, kwargs)

    def sign_cancel_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Records a cancel.

        Args:
            *args: Fields.
            **kwargs: Nonce and key index.

        Returns:
            Signed tuple.
        """
        return self._tx("cancel", 15, args, kwargs)

    def sign_modify_order(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        """Records a modify.

        Args:
            *args: Fields.
            **kwargs: Nonce and key index.

        Returns:
            Signed tuple.
        """
        return self._tx("modify", 17, args, kwargs)


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


def _adapter(
    signer: FakeSigner | None = None,
    fields: dict[str, str] | None = None,
    known: list[directory.KnownOrder] | None = None,
    connector: venue_fakes.FakeConnector | None = None,
    mono: venue_fakes.ManualMonotonic | None = None,
) -> tuple[lighter.LighterAdapter, FakeSigner]:
    """Builds an adapter with a fake signer.

    Args:
        signer: Signer.
        fields: Credentials.
        known: Directory orders.
        connector: Fake WebSocket connector.
        mono: Manual monotonic clock.

    Returns:
        Adapter and signer.
    """
    fake = signer or FakeSigner()
    clock = mono or venue_fakes.ManualMonotonic()
    built: list[tuple[str, int, int, int]] = []

    def factory(
        url: str, account: int, key_index: int, key: str, chain: int
    ) -> lighter.Signer:
        """Returns the fake signer.

        Args:
            url: URL.
            account: Account index.
            key_index: Key index.
            key: Private key.
            chain: Chain id.

        Returns:
            Signer.
        """
        del key
        built.append((url, account, key_index, chain))
        return fake

    adapter = lighter.LighterAdapter(
        catalog.DOCUMENTED["lighter"],
        credentials.StaticCredentials({"acct": fields or KEYS}),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True, market_slippage_bps=100),
        directory=_Directory(known or []),
        connect=connector or venue_fakes.FakeConnector([], clock),
        monotonic=clock,
        sleep=clock.sleep,
        signer_factory=factory,
    )
    return adapter, fake


def _known(cid: str = CID) -> directory.KnownOrder:
    """Builds a directory entry.

    Args:
        cid: Client id.

    Returns:
        Entry.
    """
    return directory.KnownOrder(
        cid, "lighter:perp:BTC", enums.OrderMode.LIVE, 1, False
    )


def _request(**overrides: object) -> trading.VenueOrderRequest:
    """Builds an order request.

    Args:
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "acct",
        "instrument_id": "lighter:perp:BTC",
        "client_order_id": CID,
        "side": enums.OrderSide.SELL,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D("0.001"),
        "time_in_force": enums.TimeInForce.GTC,
        "reduce_only": False,
        "limit_price": D("150000.5"),
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]


def _get(path: str, body: object, status: int = 200) -> respx.Route:
    """Mocks a GET.

    Args:
        path: Path.
        body: JSON body.
        status: Status.

    Returns:
        Route.
    """
    return respx.get(f"{API}{path}").mock(
        return_value=httpx.Response(status, json=body)
    )


async def test_key_index_and_fields_are_validated() -> None:
    """Reserved key indexes and non-integers are refused before I/O."""
    adapter, _ = _adapter(fields={**KEYS, "api_key_index": "2"})
    caps = await adapter.capabilities("acct")
    assert "reserved" in caps.notes[-1]
    adapter, _ = _adapter(fields={**KEYS, "account_index": "x"})
    caps = await adapter.capabilities("acct")
    assert "integers" in caps.notes[-1]
    assert lighter.sdk_available()


@respx.mock
async def test_negotiation_checks_the_registered_key() -> None:
    """``check_client`` must pass and the account must exist."""
    _get("/", {"status": 200, "network_id": 1, "timestamp": 1})
    _get(
        "/api/v1/account",
        {
            "code": 200,
            "accounts": [
                {"total_asset_value": "100", "available_balance": "80"}
            ],
        },
    )
    adapter, _ = _adapter()
    caps = await adapter.capabilities("acct")
    assert caps.status is enums.TradingStatus.AVAILABLE
    adapter, _ = _adapter(FakeSigner(check="private key does not match"))
    caps = await adapter.capabilities("acct")
    assert (
        "permission missing: this API key is not registered" in caps.notes[-1]
    )
    _get("/api/v1/account", {"code": 200, "accounts": []})
    adapter, _ = _adapter()
    caps = await adapter.capabilities("acct")
    assert "account not found" in caps.notes[-1]


@respx.mock
async def test_orders_are_integer_scaled_and_nonces_increment() -> None:
    """Amounts scale by market decimals; nonces come from nextNonce once."""
    _get("/api/v1/orderBooks", BOOKS)
    nonce = _get("/api/v1/nextNonce", {"code": 200, "nonce": 42})
    send = respx.post(f"{API}/api/v1/sendTx").mock(
        return_value=httpx.Response(200, json={"code": 200, "tx_hash": "0xabc"})
    )
    adapter, signer = _adapter()
    result = await adapter.submit_order(
        _request(time_in_force=enums.TimeInForce.POST_ONLY)
    )
    assert result.accepted and result.state.status is S.ACKNOWLEDGED
    name, args, kwargs = signer.calls[0]
    index = wire.index_of(CID, 48)
    assert (name, args) == (
        "create",
        (
            1,
            index,
            100,
            1500005,
            True,
            lighter.ORDER_LIMIT,
            lighter.TIF_POST_ONLY,
            False,
            0,
            -1,
        ),
    )
    assert kwargs == {"nonce": 42, "api_key_index": 4}
    form = urllib.parse.parse_qs(send.calls.last.request.content.decode())
    assert form["tx_type"] == ["14"] and json.loads(form["tx_info"][0]) == {
        "Nonce": 42
    }
    await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-tp1",
            time_in_force=enums.TimeInForce.IOC,
            reduce_only=True,
        )
    )
    assert signer.calls[1][2]["nonce"] == 43 and nonce.call_count == 1
    assert signer.calls[1][1][6:] == (lighter.TIF_IOC, True, 0, 0)


@respx.mock
async def test_market_and_stop_orders_carry_worst_prices() -> None:
    """Market and stop-market orders sign a banded worst price."""
    _get("/api/v1/orderBooks", BOOKS)
    _get("/api/v1/nextNonce", {"code": 200, "nonce": 1})
    _get(
        "/api/v1/orderBookDetails",
        {"code": 200, "order_book_details": [{"last_trade_price": "95000"}]},
    )
    respx.post(f"{API}/api/v1/sendTx").mock(
        return_value=httpx.Response(200, json={"code": 200, "tx_hash": "h"})
    )
    adapter, signer = _adapter()
    await adapter.submit_order(
        _request(
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            side=enums.OrderSide.BUY,
        )
    )
    market = signer.calls[0][1]
    assert market[3] == 959500 and market[5:] == (
        lighter.ORDER_MARKET,
        lighter.TIF_IOC,
        False,
        0,
        0,
    )
    await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-sl",
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D("90000"),
            reduce_only=True,
        )
    )
    stop = signer.calls[1][1]
    assert stop[3] == 891000 and stop[5:] == (
        lighter.ORDER_STOP_LOSS,
        lighter.TIF_IOC,
        True,
        900000,
        -1,
    )
    await adapter.submit_order(
        _request(
            client_order_id=f"{CID}-x",
            order_type=enums.OrderType.STOP_LIMIT,
            limit_price=D("89000"),
            trigger_price=D("90000"),
        )
    )
    assert signer.calls[2][1][5:7] == (
        lighter.ORDER_STOP_LOSS_LIMIT,
        lighter.TIF_GTT,
    )


@respx.mock
async def test_local_refusals_send_nothing() -> None:
    """FOK, off-grid sizes/prices and unknown markets never reach sendTx."""
    _get("/api/v1/orderBooks", BOOKS)
    send = respx.post(f"{API}/api/v1/sendTx")
    adapter, _ = _adapter()
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(
            _request(time_in_force=enums.TimeInForce.FOK)
        )
    for bad in (
        _request(quantity_base=D("0.0000001")),
        _request(limit_price=D("1.25")),
        _request(
            order_type=enums.OrderType.STOP_MARKET,
            limit_price=None,
            trigger_price=D("1.25"),
        ),
    ):
        result = await adapter.submit_order(bad)
        assert result.state.status is S.REJECTED
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(_request(instrument_id="lighter:perp:NOPE"))
    assert send.call_count == 0


@respx.mock
async def test_nonce_errors_rejections_and_unknown_outcomes() -> None:
    """21104 refetches the nonce; rejections keep it; timeouts reset it."""
    _get("/api/v1/orderBooks", BOOKS)
    nonce = respx.get(f"{API}/api/v1/nextNonce").mock(
        side_effect=[
            httpx.Response(200, json={"code": 200, "nonce": 5}),
            httpx.Response(200, json={"code": 200, "nonce": 9}),
            httpx.Response(200, json={"code": 200, "nonce": 12}),
        ]
    )
    respx.post(f"{API}/api/v1/sendTx").mock(
        side_effect=[
            httpx.Response(
                400, json={"code": 21104, "message": "invalid nonce"}
            ),
            httpx.Response(200, json={"code": 200, "tx_hash": "h1"}),
            httpx.Response(
                400,
                json={
                    "code": 21739,
                    "message": "not enough margin to create the order",
                },
            ),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json={"code": 200, "tx_hash": "h2"}),
        ]
    )
    adapter, signer = _adapter()
    assert (await adapter.submit_order(_request())).accepted
    assert [c[2]["nonce"] for c in signer.calls] == [5, 9]
    margin = await adapter.submit_order(_request(client_order_id=f"{CID}-a"))
    assert margin.state.reason is not None
    assert margin.state.reason.startswith("INSUFFICIENT_MARGIN")
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(_request(client_order_id=f"{CID}-b"))
    assert (
        await adapter.submit_order(_request(client_order_id=f"{CID}-c"))
    ).accepted
    assert [c[2]["nonce"] for c in signer.calls] == [5, 9, 10, 10, 12]
    assert nonce.call_count == 3


def test_error_mapping_families() -> None:
    """Codes map to margin, permission, rate, not-found and grid errors."""
    assert isinstance(
        lighter.map_error(400, {"code": 21120}), errors.VenuePermissionError
    )
    assert isinstance(
        lighter.map_error(429, {"code": 23000}), errors.RateLimitedError
    )
    assert lighter.map_error(400, {"code": 21715}).code == "NOT_FOUND"
    assert lighter.map_error(400, {"code": 21702}).code == "INVALID_TICK_SIZE"
    assert lighter.map_error(400, {"code": 21701}).code == "INVALID_LOT_SIZE"
    assert isinstance(lighter.map_error(502, "x"), errors.AdapterTimeoutError)
    assert isinstance(
        lighter.map_error(401, {"code": "x"}), errors.VenuePermissionError
    )
    assert lighter.map_error(400, {"code": 1}).code == "VALIDATION_FAILED"


@respx.mock
async def test_query_cancel_modify_by_client_index() -> None:
    """Orders resolve and change by client index; pending txs are unknown."""
    _get("/api/v1/orderBooks", BOOKS)
    _get("/api/v1/nextNonce", {"code": 200, "nonce": 1})
    respx.post(f"{API}/api/v1/sendTx").mock(
        return_value=httpx.Response(200, json={"code": 200, "tx_hash": "h"})
    )
    index = wire.index_of(CID, 48)
    row = {
        "order_index": 281474976710656,
        "client_order_index": index,
        "status": "open",
        "filled_base_amount": "0.0004",
        "remaining_base_amount": "0.0006",
        "price": "150000.5",
        "trigger_price": "0",
        "updated_at": 1795000000,
    }
    orders = respx.get(f"{API}/api/v1/accountOrders").mock(
        side_effect=[
            httpx.Response(200, json={"code": 200, "orders": [row]}),
            httpx.Response(200, json={"code": 200, "orders": [row]}),
            httpx.Response(
                200, json={"code": 200, "orders": [{**row, "status": "filled"}]}
            ),
            httpx.Response(200, json={"code": 200, "orders": [row]}),
            httpx.Response(200, json={"code": 200, "orders": []}),
            httpx.Response(200, json={"code": 200, "orders": []}),
            httpx.Response(200, json={"code": 200, "orders": []}),
        ]
    )
    _get("/api/v1/tx", {"code": 200, "status": 1})
    adapter, signer = _adapter(known=[_known()])
    state = await adapter.get_order_by_client_id("acct", CID)
    assert state is not None and state.status is S.PARTIALLY_FILLED
    assert state.ts_ms == 1_795_000_000_000
    assert orders.calls[0].request.headers["Authorization"] == "25200:21:4:cafe"
    pending = await adapter.cancel_order("acct", CID)
    assert pending.status is S.CANCEL_PENDING
    assert signer.calls[-1][:2] == ("cancel", (1, index))
    finished = await adapter.cancel_order("acct", CID)
    assert finished.status is S.FILLED
    await adapter.amend_order("acct", CID, D("149000"), None, None)
    assert signer.calls[-1][:2] == ("modify", (1, index, 60, 1490000, 0))
    with pytest.raises(errors.OrderRejectedError):
        await adapter.amend_order("acct", CID, D("1"), None, None)
    await adapter.submit_order(_request(client_order_id=f"{CID}-tp1"))
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.get_order_by_client_id("acct", f"{CID}-tp1")
    assert await adapter.get_order_by_client_id("acct", "j" + "e" * 30) is None


@respx.mock
async def test_reads_balances_positions_orders_and_fills() -> None:
    """Account, positions, active orders and trades parse documented shapes."""
    account = {
        "code": 200,
        "accounts": [
            {
                "total_asset_value": "78717.29",
                "available_balance": "77431.24",
                "positions": [
                    {
                        "market_id": 1,
                        "symbol": "BTC",
                        "sign": -1,
                        "position": "0.5",
                        "avg_entry_price": "1704.29",
                    },
                    {
                        "market_id": 2,
                        "symbol": "ETH",
                        "sign": 1,
                        "position": "0",
                        "avg_entry_price": "0",
                    },
                ],
            }
        ],
    }
    _get("/api/v1/account", account)
    index = wire.index_of(CID, 48)
    _get(
        "/api/v1/accountActiveOrders",
        {
            "code": 200,
            "orders": [
                {
                    "order_index": 9,
                    "client_order_index": index,
                    "status": "open",
                    "filled_base_amount": "0",
                }
            ],
        },
    )
    trades = {
        "code": 200,
        "trades": [
            {
                "trade_id": 77,
                "price": "100",
                "size": "0.5",
                "usd_amount": "50",
                "ask_account_id": 21,
                "bid_account_id": 9,
                "ask_client_id": index,
                "is_maker_ask": True,
                "maker_fee": 28,
                "taker_fee": 200,
                "timestamp": 1795000000000,
            },
            {
                "trade_id": 78,
                "price": "100",
                "size": "1",
                "ask_account_id": 5,
                "bid_account_id": 9,
            },
            {
                "trade_id": 79,
                "price": "100",
                "size": "1",
                "ask_account_id": 21,
                "ask_client_id": 12345,
                "timestamp": 1795000000000,
            },
        ],
    }
    _get("/api/v1/trades", trades)
    adapter, _ = _adapter(known=[_known()])
    snap = await adapter.account_snapshot("acct")
    assert snap.equity == D("78717.29") and snap.currency == "USDC"
    (position,) = await adapter.list_positions("acct")
    assert position.signed_qty == D("-0.5")
    (order,) = await adapter.list_open_orders("acct")
    assert order.client_order_id == CID
    (fill,) = await adapter.list_recent_fills("acct", 0)
    assert fill.liquidity == "maker" and fill.fee == D("0.0014")


async def test_stream_parser_on_documented_messages() -> None:
    """``account_all_orders``, trades and positions updates parse."""
    adapter, _ = _adapter(known=[_known()])
    session = adapter._new_session("acct", credentials.from_fields(KEYS), False)
    adapter._markets[False] = (
        0.0 + 1e12,
        {"BTC": lighter.Market(1, "BTC", 5, 1)},
    )
    index = wire.index_of(CID, 48)
    orders = {
        "channel": "account_all_orders:21",
        "type": "update/account_all_orders",
        "orders": {
            "1": [
                {
                    "order_index": 3,
                    "client_order_index": index,
                    "status": "canceled-post-only",
                    "filled_base_amount": "0",
                }
            ]
        },
    }
    (update,) = await adapter.parse(session, json.dumps(orders))
    assert update.order is not None and update.order.status is S.REJECTED
    assert update.order.reason == "canceled-post-only"
    trades = {
        "type": "update/account_all_trades",
        "trades": {
            "1": [
                {
                    "trade_id": 1,
                    "price": "1",
                    "size": "1",
                    "ask_account_id": 21,
                    "ask_client_id": index,
                    "is_maker_ask": False,
                    "taker_fee": 0,
                    "timestamp": 1,
                }
            ]
        },
    }
    (fill,) = await adapter.parse(session, json.dumps(trades))
    assert fill.fill is not None and fill.fill.liquidity == "taker"
    positions = {
        "type": "update/account_all_positions",
        "positions": {
            "1": {
                "market_id": 1,
                "sign": 1,
                "position": "0.2",
                "avg_entry_price": "1",
            }
        },
    }
    (pos,) = await adapter.parse(session, json.dumps(positions))
    assert (
        pos.position is not None
        and pos.position.instrument_id == "lighter:perp:BTC"
    )
    assert await adapter.parse(session, '{"type":"pong"}') == []
    assert (
        await adapter.parse(session, '{"error":{"code":30009,"message":"x"}}')
        == []
    )
    with pytest.raises(errors.VenuePermissionError):
        await adapter.parse(
            session, '{"error":{"code":20013,"message":"invalid auth"}}'
        )
    assert lighter._status("canceled-expired") is S.EXPIRED


@respx.mock
async def test_account_stream_subscribes_with_auth() -> None:
    """Private channels carry the auth token; a ping keeps it alive."""
    _get("/api/v1/trades", {"code": 200, "trades": []})
    _get("/api/v1/accountActiveOrders", {"code": 200, "orders": []})
    _get(
        "/api/v1/account",
        {
            "code": 200,
            "accounts": [
                {
                    "total_asset_value": "1",
                    "available_balance": "1",
                    "positions": [],
                }
            ],
        },
    )
    mono = venue_fakes.ManualMonotonic()
    frame = json.dumps(
        {
            "type": "update/account_all_orders",
            "orders": {
                "1": [
                    {
                        "order_index": 1,
                        "client_order_index": 1,
                        "status": "open",
                    }
                ]
            },
        }
    )
    connector = venue_fakes.FakeConnector(
        [venue_fakes.Script(['{"type":"connected"}', frame])], mono
    )
    adapter, _ = _adapter(connector=connector, mono=mono)
    events = await venue_fakes.collect(adapter.stream_account_events("acct"), 2)
    assert connector.urls == ["wss://mainnet.zklighter.elliot.ai/stream"]
    sent = [json.loads(m) for m in connector.connections[0].sent]
    assert [m["channel"] for m in sent] == [
        "account_all_orders/21",
        "account_all_trades/21",
        "account_all_positions/21",
    ]
    assert all(m["auth"] == "25200:21:4:cafe" for m in sent)
    assert events[-1].order is not None  # type: ignore[attr-defined]
    await adapter.aclose()
