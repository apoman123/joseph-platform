# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Supervisor, venue valuation, fail-closed refusals and plan orders."""

from __future__ import annotations

import asyncio
import decimal
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from joseph_execution.adapters.venues import base
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import http
from joseph_execution.adapters.venues import live
from joseph_execution.adapters.venues import mexc
from joseph_execution.adapters.venues import ratelimit
from joseph_execution.adapters.venues import ws
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import positions
from joseph_execution.oms import valuation
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import metrics as metrics_lib
from joseph_execution.ports import trading
from joseph_execution.reconciliation import live_streams
from joseph_execution.testing import harness
from joseph_execution.testing import venue_fakes

_Decimal = decimal.Decimal
_State = enums.OrderState
CID = "j0123456789abcdef0123456789abcd"


class _FlakyAdapter:
    """Adapter whose stream fails, then yields one event, then idles.

    Attributes:
        attempts: Streams opened.
    """

    def __init__(self) -> None:
        """Initializes the adapter."""
        self.attempts = 0

    async def stream_account_events(
        self, account_id: str
    ) -> AsyncIterator[trading.VenueEvent]:
        """Streams events, failing on the first two attempts.

        Args:
            account_id: Account the events are for.

        Yields:
            From the third attempt on, a position event and a fill of an
            unknown order; the stream then stays silent for an hour.

        Raises:
            AdapterUnavailableError: On the first attempt.
            RuntimeError: On the second attempt.
        """
        self.attempts += 1
        if self.attempts == 1:
            raise errors.AdapterUnavailableError("down")
        if self.attempts == 2:
            raise RuntimeError("bug")
        yield trading.VenueEvent(
            account_id,
            1,
            position=trading.VenuePosition(
                "binance:perp:BTCUSDT", _Decimal(1), None
            ),
        )
        yield trading.VenueEvent(
            account_id,
            2,
            fill=trading.VenueFill(
                "f",
                "unknown",
                _Decimal(1),
                _Decimal(1),
                _Decimal(0),
                "USDT",
                "taker",
                1,
            ),
        )
        await asyncio.sleep(3600)


class _Resolver:
    """Adapter resolver that returns one adapter for every account and mode.

    Attributes:
        adapter: The adapter returned.
    """

    def __init__(self, adapter: _FlakyAdapter) -> None:
        """Initializes the resolver.

        Args:
            adapter: Adapter to return.
        """
        self.adapter = adapter

    def resolve(
        self, account: accounts.Account, mode: enums.OrderMode
    ) -> _FlakyAdapter:
        """Returns the adapter, whatever the account and mode.

        Args:
            account: Ignored account.
            mode: Ignored order mode.

        Returns:
            ``adapter``.
        """
        del account, mode
        return self.adapter


def _venue_account(h: harness.Harness, status: enums.AccountStatus) -> str:
    """Stores a venue account with an open LIVE position.

    Args:
        h: Harness.
        status: Account status.

    Returns:
        The account id, ``acct-binance-<status>``; its position is long
        0.5 BTCUSDT from 90000.
    """
    account = accounts.Account(
        f"acct-binance-{status.value.lower()}",
        "tenant-a",
        "binance",
        "Main",
        status,
        (enums.OrderMode.LIVE,),
        "USD",
        1,
    )
    with h.container.uow_factory() as uow:
        uow.accounts.add(account)
        uow.positions.save(
            positions.Position(
                position_id=positions.position_id(
                    account.account_id, "binance:perp:BTCUSDT", "LIVE"
                ),
                tenant_id="tenant-a",
                account_id=account.account_id,
                instrument_id="binance:perp:BTCUSDT",
                mode=enums.OrderMode.LIVE,
                signed_qty=_Decimal("0.5"),
                entry_price=_Decimal(90000),
            )
        )
    return account.account_id


async def test_supervisor_restarts_streams_and_tracks_scope() -> None:
    """Failed streams restart; events apply; out-of-scope streams stop."""
    h = harness.build(live_trading_enabled=True)
    live_id = _venue_account(h, enums.AccountStatus.CONNECTED)
    _venue_account(h, enums.AccountStatus.DISCONNECTED)
    adapter = _FlakyAdapter()
    metrics = metrics_lib.MetricsRegistry()
    supervisor = live_streams.LiveStreamSupervisor(
        h.container.uow_factory,
        _Resolver(adapter),  # type: ignore[arg-type]  # duck-typed stand-in for the AdapterRegistry
        h.container.services.oms,
        metrics,
        poll_s=0.0,
        restart_s=0.0,
    )
    assert [a.account_id for a in supervisor.scope()] == [live_id]
    await supervisor.sync()
    for _ in range(100):
        if adapter.attempts >= 3 and metrics.value(
            "live_stream_events_total", {"venue": "binance"}
        ):
            break
        await asyncio.sleep(0)
    assert adapter.attempts >= 3
    assert (
        metrics.value("live_stream_failures_total", {"venue": "binance"}) == 2
    )
    assert supervisor.running() == [live_id]
    with h.container.uow_factory() as uow:
        for pos in uow.positions.list(account_id=live_id):
            pos.signed_qty = _Decimal(0)
            uow.positions.save(pos)
    await supervisor.sync()
    assert supervisor.running() == []
    runner = asyncio.create_task(supervisor.run_forever())
    await asyncio.sleep(0)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner


async def test_venue_valuation_uses_venue_balances() -> None:
    """Venue accounts value from the venue snapshot minus reservations."""
    h = harness.build()
    account_id = _venue_account(h, enums.AccountStatus.CONNECTED)
    with h.container.uow_factory() as uow:
        account = uow.accounts.get(account_id)
    assert account is not None
    unknown = await valuation.value_account(
        h.container.uow_factory, h.market, account, enums.OrderMode.LIVE, 1, 10
    )
    assert unknown.equity is None and unknown.stale
    snapshot = trading.VenueAccountSnapshot(
        _Decimal(1000), _Decimal(800), "USDT", 1
    )
    valued = await valuation.value_account(
        h.container.uow_factory,
        h.market,
        account,
        enums.OrderMode.LIVE,
        1,
        10,
        snapshot,
    )
    assert valued.equity == _Decimal(
        1000
    ) and valued.available_margin == _Decimal(800)
    assert valued.gross_notional is None and valued.stale


async def test_fail_closed_adapter_refuses_everything() -> None:
    """The fail-closed adapter refuses every account and trading call."""
    adapter = base.FailClosedVenueAdapter(
        catalog.DOCUMENTED["bitget"], "Cathay"
    )
    for call in (
        adapter.account_snapshot("a"),
        adapter.list_open_orders("a"),
        adapter.list_recent_fills("a"),
        adapter.amend_order("a", "c", _Decimal(1), None, None),
    ):
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await call
        assert "Cathay" in info.value.message
    with pytest.raises(errors.AdapterUnavailableError):
        await anext(adapter.stream_account_events("a"))
    assert adapter.calls[-1] == "stream_account_events"


def test_http_helpers_and_default_connector() -> None:
    """Retry-After parsing tolerates junk; the default connector is lazy."""
    assert http._retry_after({"retry-after": "soon"}) == 10.0
    assert http._retry_after({}) == 10.0
    connection = ws.default_connect("wss://example.invalid/ws")
    assert hasattr(connection, "__aenter__")


@respx.mock
async def test_http_keeps_non_json_bodies_as_text() -> None:
    """A non-JSON body is returned as text for the venue mapper."""
    respx.get("https://venue.test/html").mock(
        return_value=httpx.Response(502, text="<html>bad gateway</html>")
    )
    mono = venue_fakes.ManualMonotonic()
    client = http.VenueHttp(
        "t",
        "https://venue.test",
        ratelimit.WindowLimiter(
            "t",
            [ratelimit.Budget("weight", 9, 1.0)],
            monotonic=mono,
            sleep=mono.sleep,
        ),
    )
    reply = await client.request("GET", "/html")
    assert reply.status == 502 and "bad gateway" in reply.payload


def _mexc(known: list[directory.KnownOrder]) -> mexc.MexcFuturesAdapter:
    """Builds a MEXC adapter with a directory.

    Args:
        known: Orders the directory knows.

    Returns:
        A LIVE-enabled MEXC adapter for account ``acct`` on a manual clock.
    """

    class _Directory:
        """Order directory over ``known``."""

        def find(
            self, account_id: str, cid: str
        ) -> directory.KnownOrder | None:
            """Finds a known order by client id.

            Args:
                account_id: Ignored; the tests use one account.
                cid: Client order id.

            Returns:
                The order, or ``None`` when it is not known.
            """
            del account_id
            return next((k for k in known if k.client_order_id == cid), None)

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
            return list(known)

    mono = venue_fakes.ManualMonotonic()
    return mexc.MexcFuturesAdapter(
        catalog.DOCUMENTED["mexc"],
        credentials.StaticCredentials(
            {"acct": {"api_key": "k", "api_secret": "secret-value"}}
        ),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True),
        directory=_Directory(),
        monotonic=mono,
        sleep=mono.sleep,
    )


def _ok(data: object) -> httpx.Response:
    """Builds a MEXC success answer.

    Args:
        data: Value of the ``data`` field.

    Returns:
        A 200 response with MEXC's success envelope.
    """
    return httpx.Response(200, json={"success": True, "code": 0, "data": data})


@respx.mock
async def test_mexc_plan_orders_query_trigger_and_cancel() -> None:
    """Plan orders resolve by venue id after a restart and cancel by id."""
    api = mexc.REST_URL
    respx.get(f"{api}/api/v1/contract/detail").mock(
        return_value=_ok(
            {"symbol": "BTC_USDT", "contractSize": 0.0001, "volUnit": 1}
        )
    )
    plans = respx.get(f"{api}/api/v1/private/planorder/list/orders").mock(
        side_effect=[
            _ok([{"id": 55, "state": 1}]),
            _ok([{"id": 55, "state": 3, "orderId": 901}]),
            _ok([]),
        ]
    )
    respx.get(f"{api}/api/v1/private/order/get/901").mock(
        return_value=_ok(
            {"orderId": "901", "symbol": "BTC_USDT", "state": 3, "dealVol": 5}
        )
    )
    cancel = respx.post(f"{api}/api/v1/private/planorder/cancel").mock(
        return_value=_ok(None)
    )
    respx.get(f"{api}/api/v1/private/order/list/order_deals/v3").mock(
        return_value=_ok(
            [
                {
                    "id": "d1",
                    "symbol": "BTC_USDT",
                    "vol": 5,
                    "price": 1,
                    "orderId": "901",
                    "timestamp": 1_795_000_000_000,
                }
            ]
        )
    )
    stop = directory.KnownOrder(
        f"{CID}-sl",
        "mexc:perp:BTC_USDT",
        enums.OrderMode.LIVE,
        1,
        False,
        enums.OrderType.STOP_MARKET,
        "plan:55",
    )
    adapter = _mexc([stop])
    waiting = await adapter.get_order_by_client_id("acct", f"{CID}-sl")
    assert waiting is not None and waiting.status is _State.ACKNOWLEDGED
    triggered = await adapter.get_order_by_client_id("acct", f"{CID}-sl")
    assert triggered is not None and triggered.status is _State.FILLED
    assert triggered.filled_base == _Decimal("0.0005")
    assert triggered.exchange_order_id == "plan:55"
    assert await adapter.get_order_by_client_id("acct", f"{CID}-sl") is None
    canceled = await adapter.cancel_order("acct", f"{CID}-sl")
    assert canceled.status is _State.CANCELED
    assert json.loads(cancel.calls.last.request.content) == [
        {"symbol": "BTC_USDT", "orderId": 55}
    ]
    (fill,) = await adapter.list_recent_fills("acct", 0)
    assert fill.client_order_id == f"{CID}-sl"
    assert plans.call_count == 3
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.get_order_by_client_id("acct", "j-unknown")
    await adapter.aclose()


@respx.mock
async def test_mexc_contract_flags_and_cancel_edge_cases() -> None:
    """API-disallowed contracts and finished orders are handled locally."""
    api = mexc.REST_URL
    respx.get(f"{api}/api/v1/contract/detail").mock(
        return_value=_ok(
            [
                {
                    "symbol": "BTC_USDT",
                    "contractSize": 0.0001,
                    "volUnit": 1,
                    "apiAllowed": False,
                }
            ]
        )
    )
    respx.get(f"{api}/api/v1/private/order/external/BTC_USDT/{CID}").mock(
        return_value=_ok(
            {"orderId": "7", "symbol": "BTC_USDT", "state": 3, "dealVol": 1}
        )
    )
    known = directory.KnownOrder(
        CID, "mexc:perp:BTC_USDT", enums.OrderMode.LIVE, 1, False
    )
    adapter = _mexc([known])
    result = await adapter.submit_order(
        trading.VenueOrderRequest(
            account_id="acct",
            instrument_id="mexc:perp:BTC_USDT",
            client_order_id=CID,
            side=enums.OrderSide.BUY,
            order_type=enums.OrderType.LIMIT,
            quantity_base=_Decimal("0.0001"),
            time_in_force=enums.TimeInForce.GTC,
            reduce_only=False,
            limit_price=_Decimal(1),
        )
    )
    assert result.state.reason is not None
    assert "disallows API trading" in result.state.reason
    done = await adapter.cancel_order("acct", CID)
    assert done.status is _State.FILLED


# Complete, well-formed (but fake) credentials for each venue.
_VALID_FIELDS = {
    "binance": {"api_key": "k", "api_secret": "secret-value"},
    "bitget": {"api_key": "k", "api_secret": "secret-value", "passphrase": "p"},
    "mexc": {"api_key": "k", "api_secret": "secret-value"},
    "hyperliquid": {
        "agent_private_key": "0x" + "11" * 32,
        "account_address": "0x" + "ab" * 20,
    },
    "lighter": {
        "api_private_key": "ab" * 40,
        "account_index": "21",
        "api_key_index": "4",
    },
}


@pytest.mark.parametrize(
    ("venue", "tif"),
    [
        *((v, enums.TimeInForce.DAY) for v in sorted(_VALID_FIELDS)),
        ("hyperliquid", enums.TimeInForce.FOK),
        ("lighter", enums.TimeInForce.FOK),
    ],
)
async def test_undocumented_time_in_force_is_refused_before_sending(
    venue: str, tif: enums.TimeInForce
) -> None:
    """Securities-only DAY (or an unsupported FOK) never reaches a venue."""
    adapters = catalog.build_live_adapters(
        credentials.StaticCredentials({"acct": _VALID_FIELDS[venue]}),
        clock_lib.ManualClock(1_795_000_000_000),
        live.VenueOptions(live_trading_enabled=True),
    )
    request = trading.VenueOrderRequest(
        account_id="acct",
        instrument_id=f"{venue}:perp:BTC",
        client_order_id=CID,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        quantity_base=_Decimal(1),
        time_in_force=tif,
        reduce_only=False,
        limit_price=_Decimal(1),
    )
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await adapters[venue].submit_order(request)
        assert router.calls.call_count == 0
    assert info.value.code == "UNSUPPORTED_ORDER_TYPE"
