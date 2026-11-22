"""Unit tests for the paper, shadow, live, resolver and market adapters."""

from __future__ import annotations

import dataclasses
import decimal

import httpx
import pytest
import respx

from joseph_execution.adapters import market_http
from joseph_execution.adapters import market_static
from joseph_execution.adapters import paper
from joseph_execution.adapters import resolver
from joseph_execution.adapters import shadow
from joseph_execution.adapters.venues import catalog
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading

D = decimal.Decimal
S = enums.OrderState
BTC = "mock:perp:BTCUSD"


def _setup() -> tuple[
    paper.PaperTradingAdapter, market_static.StaticMarketDataGateway
]:
    """Builds a paper adapter over a static gateway.

    Returns:
        Adapter and gateway.
    """
    clock = clock_lib.ManualClock()
    gateway = market_static.StaticMarketDataGateway(clock)
    return paper.PaperTradingAdapter(gateway, clock), gateway


def _req(cid: str, **overrides: object) -> trading.VenueOrderRequest:
    """Builds a venue request.

    Args:
        cid: Client id.
        **overrides: Overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": "a",
        "instrument_id": BTC,
        "client_order_id": cid,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.MARKET,
        "quantity_base": D("0.01"),
        "time_in_force": enums.TimeInForce.IOC,
        "reduce_only": False,
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]


async def test_market_order_fills_at_ask_with_taker_fee() -> None:
    """Market orders fill immediately at the ask."""
    adapter, _ = _setup()
    result = await adapter.submit_order(_req("c1"))
    assert result.accepted and result.state.status is S.FILLED
    assert result.fills[0].price == D("95000.1")
    assert result.fills[0].liquidity == "taker"
    positions = await adapter.list_positions("a")
    assert positions[0].signed_qty == D("0.01")


async def test_duplicate_client_id_is_deduplicated() -> None:
    """Resubmitting a client id returns the existing order."""
    adapter, _ = _setup()
    await adapter.submit_order(_req("c1"))
    again = await adapter.submit_order(_req("c1"))
    assert again.state.status is S.FILLED
    assert len(await adapter.list_recent_fills("a")) == 1


async def test_limit_rests_then_fills_as_maker_on_tick() -> None:
    """Resting limits fill when the market crosses them."""
    adapter, gateway = _setup()
    req = _req(
        "c1",
        order_type=enums.OrderType.LIMIT,
        limit_price=D(94000),
        time_in_force=enums.TimeInForce.GTC,
    )
    result = await adapter.submit_order(req)
    assert result.state.status is S.ACKNOWLEDGED and not result.fills
    assert await adapter.tick() == 0
    gateway.set_mark(BTC, D(93990))
    assert await adapter.tick() == 1
    state = await adapter.get_order_by_client_id("a", "c1")
    assert state is not None and state.status is S.FILLED
    fills = await adapter.list_recent_fills("a")
    assert fills[0].price == D(94000) and fills[0].liquidity == "maker"


async def test_partial_fills_on_tick() -> None:
    """Partial fill fraction splits resting fills."""
    adapter, gateway = _setup()
    adapter.faults.partial_fill_fraction = D("0.5")
    await adapter.submit_order(
        _req(
            "c1",
            order_type=enums.OrderType.LIMIT,
            limit_price=D(94000),
            quantity_base=D("0.02"),
            time_in_force=enums.TimeInForce.GTC,
        )
    )
    gateway.set_mark(BTC, D(93000))
    await adapter.tick()
    state = await adapter.get_order_by_client_id("a", "c1")
    assert state is not None and state.status is S.PARTIALLY_FILLED
    assert state.filled_base == D("0.01")
    for _ in range(20):
        await adapter.tick()
    state = await adapter.get_order_by_client_id("a", "c1")
    assert state is not None and state.status is S.FILLED
    fills = await adapter.list_recent_fills("a")
    assert sum(f.quantity_base for f in fills) == D("0.02")


async def test_stop_triggers_and_reduce_only_is_enforced() -> None:
    """Stops trigger on mark; reduce-only never opens a position."""
    adapter, gateway = _setup()
    lonely = await adapter.submit_order(
        _req("r1", side=enums.OrderSide.SELL, reduce_only=True)
    )
    assert lonely.state.status is S.CANCELED
    await adapter.submit_order(_req("open"))
    await adapter.submit_order(
        _req(
            "sl",
            side=enums.OrderSide.SELL,
            reduce_only=True,
            order_type=enums.OrderType.STOP_MARKET,
            trigger_price=D(94000),
            time_in_force=enums.TimeInForce.GTC,
            quantity_base=D(1),
        )
    )
    gateway.set_mark(BTC, D(94500))
    assert await adapter.tick() == 0
    gateway.set_mark(BTC, D(93900))
    assert await adapter.tick() == 1
    positions = await adapter.list_positions("a")
    assert positions == []
    stop = await adapter.get_order_by_client_id("a", "sl")
    assert stop is not None and stop.filled_base == D("0.01")


async def test_post_only_and_ioc_semantics() -> None:
    """Crossing post-only orders reject; IOC limits cancel if not crossing."""
    adapter, _ = _setup()
    post = await adapter.submit_order(
        _req(
            "p",
            order_type=enums.OrderType.LIMIT,
            limit_price=D(96000),
            time_in_force=enums.TimeInForce.POST_ONLY,
        )
    )
    assert post.state.status is S.REJECTED
    ioc = await adapter.submit_order(
        _req("i", order_type=enums.OrderType.LIMIT, limit_price=D(90000))
    )
    assert ioc.state.status is S.CANCELED


async def test_fault_injection() -> None:
    """Faults produce timeouts, rejects, races and stream anomalies."""
    adapter, _ = _setup()
    adapter.faults.drop_ack_next = 1
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(_req("lost"))
    found = await adapter.get_order_by_client_id("a", "lost")
    assert found is not None and found.status is S.FILLED
    adapter.faults.timeout_before_next = 1
    with pytest.raises(errors.AdapterTimeoutError):
        await adapter.submit_order(_req("never"))
    assert await adapter.get_order_by_client_id("a", "never") is None
    adapter.faults.reject_next = 1
    rejected = await adapter.submit_order(_req("rej"))
    assert not rejected.accepted
    adapter.faults.unavailable = True
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.submit_order(_req("down"))
    assert await adapter.health() is enums.HealthState.UNAVAILABLE
    adapter.faults = paper.FaultPlan(duplicate_events=True, reverse_events=True)
    adapter.drain_events("a")
    await adapter.submit_order(_req("dup"))
    events = adapter.drain_events("a")
    assert len(events) == 4
    assert events[0].order is not None
    adapter.faults = paper.FaultPlan(query_by_client_id=False)
    with pytest.raises(errors.AdapterUnavailableError):
        await adapter.get_order_by_client_id("a", "dup")
    caps = await adapter.capabilities("a")
    assert not caps.query_by_client_id


async def test_cancel_race_reports_fill() -> None:
    """A fill that lands before the cancel is reported as FILLED."""
    adapter, _ = _setup()
    await adapter.submit_order(
        _req(
            "c",
            order_type=enums.OrderType.LIMIT,
            limit_price=D(94000),
            time_in_force=enums.TimeInForce.GTC,
        )
    )
    adapter.faults.fill_before_cancel_next = 1
    state = await adapter.cancel_order("a", "c")
    assert state.status is S.FILLED


async def test_shadow_records_without_sending() -> None:
    """Shadow orders are recorded with deviation from the mark."""
    clock = clock_lib.ManualClock()
    gateway = market_static.StaticMarketDataGateway(clock)
    adapter = shadow.ShadowAdapter(gateway, clock)
    await adapter.submit_order(_req("s1"))
    assert len(adapter.records) == 1
    record = adapter.records[0]
    assert record.decision_mark == D(95000)
    assert record.deviation_bps is not None and record.deviation_bps > 0
    caps = await adapter.capabilities("a")
    assert "never sent" in caps.notes[0]


async def test_live_adapters_fail_closed(contract_validate: object) -> None:
    """Every live venue adapter refuses trading and reports unavailable."""
    adapters = catalog.build_live_adapters()
    assert sorted(adapters) == [
        "binance",
        "bitget",
        "hyperliquid",
        "lighter",
        "mexc",
    ]
    for venue, adapter in adapters.items():
        caps = await adapter.capabilities("x")
        assert caps.status is enums.TradingStatus.TRADING_UNAVAILABLE
        contract_validate(
            "execution.schema.json",  # type: ignore[operator]
            "TradingCapabilities",
            caps.to_contract(),
        )
        assert await adapter.health() is enums.HealthState.UNAVAILABLE
        with pytest.raises(errors.AdapterUnavailableError) as info:
            await adapter.submit_order(_req("x"))
        assert info.value.code == "LIVE_TRADING_DISABLED"
        for call in (
            adapter.cancel_order("a", "x"),
            adapter.get_order_by_client_id("a", "x"),
            adapter.list_positions("a"),
        ):
            with pytest.raises(errors.AdapterUnavailableError):
                await call
        assert adapter.calls[0] == "submit_order", venue
    mexc = await adapters["mexc"].capabilities("x")
    assert any("eligibility" in note for note in mexc.notes)


def test_resolver_routes_by_mode() -> None:
    """PAPER and SHADOW never resolve to a live venue adapter."""
    adapter, gateway = _setup()
    shadow_adapter = shadow.ShadowAdapter(gateway, clock_lib.ManualClock())
    live = catalog.build_live_adapters()
    registry = resolver.AdapterRegistry(adapter, shadow_adapter, live)
    account = accounts.Account(
        "a", "t", "binance", "x", enums.AccountStatus.CONNECTED, (), "USD", 1
    )
    assert registry.resolve(account, enums.OrderMode.PAPER) is adapter
    assert registry.resolve(account, enums.OrderMode.SHADOW) is shadow_adapter
    assert registry.resolve(account, enums.OrderMode.LIVE) is live["binance"]
    mock = dataclasses.replace(account, venue="mock")
    assert registry.resolve(mock, enums.OrderMode.LIVE).name == "mock"


async def test_static_gateway_staleness_and_books() -> None:
    """Pinned quotes age; stale books are flagged; outages return None."""
    clock = clock_lib.ManualClock()
    gateway = market_static.StaticMarketDataGateway(clock)
    quote = await gateway.quote(BTC)
    assert quote is not None and quote.feed_state is enums.FeedState.DEMO
    gateway.set_mark(BTC, D(95000), as_of_ms=clock.now_ms())
    clock.advance(20_000)
    aged = await gateway.quote(BTC)
    assert aged is not None and aged.age_ms(clock.now_ms()) == 20_000
    gateway.set_book_stale(BTC)
    book = await gateway.order_book(BTC)
    assert book is not None and book.stale
    gateway.unavailable = True
    assert await gateway.quote(BTC) is None
    assert await gateway.instrument(BTC) is None


@respx.mock
async def test_http_gateway_parses_market_engine_contracts() -> None:
    """The HTTP gateway parses contract payloads and fails closed."""
    base = "http://market.test"
    respx.get(f"{base}/v1/instruments").mock(
        return_value=httpx.Response(
            200,
            json={
                "instruments": [
                    {
                        "instrument_id": "binance:perp:BTCUSDT",
                        "key": {"venue": "binance", "quote": "USDT"},
                        "tick_size": "0.1",
                        "lot_size": "0.001",
                        "min_qty": "0.001",
                        "min_notional": "100",
                        "contract_multiplier": "1",
                        "status": "TRADING",
                        "max_leverage": "125",
                    }
                ],
                "as_of_ms": 1,
            },
        )
    )
    respx.get(f"{base}/v1/market/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "instrument_id": "binance:perp:BTCUSDT",
                "mark": "95000",
                "last": "95001",
                "best_bid": "94999",
                "best_ask": "95001",
                "feed_state": "LIVE",
                "as_of_ms": 5,
                "stale": False,
                "data_age_ms": 1,
                "source": "binance.ws",
            },
        )
    )
    respx.get(f"{base}/v1/market/orderbook").mock(
        return_value=httpx.Response(
            200,
            json={
                "instrument_id": "binance:perp:BTCUSDT",
                "bids": [["94999", "1"]],
                "asks": [["95001", "2"]],
                "snapshot_ts_ms": 5,
                "received_at_ms": 5,
                "depth": 1,
                "stale": False,
                "source": "binance.rest",
            },
        )
    )
    gateway = market_http.HttpMarketDataGateway(base)
    meta = await gateway.instrument("binance:perp:BTCUSDT")
    assert meta is not None and meta.min_notional == D(100)
    quote = await gateway.quote("binance:perp:BTCUSDT")
    assert quote is not None and quote.feed_state is enums.FeedState.LIVE
    book = await gateway.order_book("binance:perp:BTCUSDT")
    assert book is not None and book.asks[0] == (D(95001), D(2))
    respx.get(f"{base}/v1/market/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "instrument_id": "binance:perp:BTCUSDT",
                "feed_state": "LIVE",
                "stale": True,
                "as_of_ms": 1,
            },
        )
    )
    stale = await gateway.quote("binance:perp:BTCUSDT")
    assert stale is not None and stale.feed_state is enums.FeedState.STALE
    respx.get(f"{base}/v1/market/snapshot").mock(
        side_effect=httpx.ConnectError("down")
    )
    assert await gateway.quote("binance:perp:BTCUSDT") is None
    await gateway.close()
