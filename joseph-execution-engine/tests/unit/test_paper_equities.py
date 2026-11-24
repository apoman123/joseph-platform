# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for paper trading of exchange-listed securities."""

from __future__ import annotations

import datetime
import decimal

from joseph_execution.adapters import market_static
from joseph_execution.adapters import paper
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures

D = decimal.Decimal
S = enums.OrderState
MONDAY = datetime.date(2026, 11, 23)
TWD = accounts.paper_account_id("t", "TWD")
USD = accounts.paper_account_id("t")


def _setup(
    now_ms: int,
) -> tuple[
    paper.PaperTradingAdapter,
    market_static.StaticMarketDataGateway,
    clock_lib.ManualClock,
]:
    """Builds a paper adapter with TWSE and US instruments.

    Args:
        now_ms: Start time of the manual clock, epoch milliseconds.

    Returns:
        The adapter, with 3,000,000 TWD of paper cash; its gateway, holding
        the securities fixtures from the week before ``MONDAY``; the clock.
    """
    clock = clock_lib.ManualClock(now_ms)
    gateway = market_static.StaticMarketDataGateway(clock)
    fixtures.install(gateway, MONDAY - datetime.timedelta(days=7))
    adapter = paper.PaperTradingAdapter(
        gateway, clock, starting_cash={"TWD": D(3_000_000)}
    )
    return adapter, gateway, clock


def _req(cid: str, **overrides: object) -> trading.VenueOrderRequest:
    """Builds a TWSE limit buy of one board lot.

    Args:
        cid: Client order id.
        **overrides: Request fields that replace the defaults.

    Returns:
        By default a DAY buy of 1000 TSMC (2330) shares at 601 TWD on the
        TWD paper account.
    """
    fields: dict[str, object] = {
        "account_id": TWD,
        "instrument_id": fixtures.TSMC,
        "client_order_id": cid,
        "side": enums.OrderSide.BUY,
        "order_type": enums.OrderType.LIMIT,
        "quantity_base": D(1000),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "limit_price": D(601),
    }
    fields.update(overrides)
    return trading.VenueOrderRequest(**fields)  # type: ignore[arg-type]  # dict[str, object] holds each field's own type


async def test_orders_wait_for_the_open_and_fill_in_session() -> None:
    """Orders placed before the open rest and fill once the session opens."""
    adapter, _, clock = _setup(fixtures.taipei(MONDAY, 8))
    result = await adapter.submit_order(_req("pre"))
    assert result.state.status is S.ACKNOWLEDGED and not result.fills
    assert await adapter.tick() == 0
    clock.current_ms = fixtures.taipei(MONDAY, 9, 1)
    assert await adapter.tick() == 1
    positions = await adapter.list_positions(TWD)
    assert positions[0].signed_qty == D(1000)
    fill = (await adapter.list_recent_fills(TWD))[0]
    assert fill.price == D(601) and fill.fee_asset == "TWD"


async def test_ioc_is_canceled_while_closed() -> None:
    """IOC orders are canceled while the market is closed."""
    adapter, _, _ = _setup(fixtures.taipei(MONDAY, 14))
    result = await adapter.submit_order(
        _req("ioc", time_in_force=enums.TimeInForce.IOC)
    )
    assert result.state.status is S.CANCELED
    assert "closed" in (result.state.reason or "")


async def test_day_orders_expire_at_the_close() -> None:
    """A resting DAY order expires when its session closes."""
    adapter, _, clock = _setup(fixtures.taipei(MONDAY, 10))
    result = await adapter.submit_order(_req("rest", limit_price=D(590)))
    assert result.state.status is S.ACKNOWLEDGED
    clock.current_ms = fixtures.taipei(MONDAY, 13, 31)
    await adapter.tick()
    state = await adapter.get_order_by_client_id(TWD, "rest")
    assert state is not None and state.status is S.EXPIRED


async def test_market_fills_clamp_to_daily_limits() -> None:
    """Market fills clamp to the +-10% band; without a close there is none."""
    adapter, gateway, _ = _setup(fixtures.taipei(MONDAY, 10))
    gateway.set_mark(fixtures.TSMC, D(700))
    result = await adapter.submit_order(
        _req(
            "mkt",
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
        )
    )
    assert result.fills[0].price == D(660)
    gateway.set_previous_close(fixtures.TSMC, None)
    again = await adapter.submit_order(
        _req(
            "mkt2",
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
        )
    )
    assert again.fills[0].price == D(701)


async def test_cash_settles_t_plus_two() -> None:
    """Purchases are unsettled until the T+2 session opens."""
    adapter, _, clock = _setup(fixtures.taipei(MONDAY, 10))
    result = await adapter.submit_order(_req("buy"))
    fill = result.fills[0]
    assert fill.fee == D(856)
    snap = await adapter.account_snapshot(TWD)
    assert snap.currency == "TWD"
    assert snap.settled_cash == D(3_000_000)
    assert snap.unsettled_cash == D(-601_856)
    clock.current_ms = fixtures.taipei(MONDAY + datetime.timedelta(days=2), 9)
    settled = await adapter.account_snapshot(TWD)
    assert settled.settled_cash == D(3_000_000 - 601_856)
    assert settled.unsettled_cash == D(0)
    sell = await adapter.submit_order(
        _req("sell", side=enums.OrderSide.SELL, limit_price=D(599))
    )
    assert sell.fills[0].fee == D(853 + 1797)
    after = await adapter.account_snapshot(TWD)
    assert after.unsettled_cash == D(599_000 - 853 - 1797)


async def test_bond_etf_sales_follow_the_tax_suspension() -> None:
    """Bond ETF sales pay no tax on 2026-12-31 and 0.1% on 2027-01-01."""
    last_day = datetime.date(2026, 12, 31)
    adapter, gateway, clock = _setup(fixtures.taipei(last_day, 10))
    gateway.set_sessions("XTAI", fixtures.xtai_sessions(last_day, 7))
    bought = await adapter.submit_order(
        _req(
            "bond-buy",
            instrument_id=fixtures.YUANTA_US20,
            limit_price=D("26.2"),
        )
    )
    assert bought.fills[0].fee == D(37)
    sale: dict[str, object] = {
        "instrument_id": fixtures.YUANTA_US20,
        "side": enums.OrderSide.SELL,
        "quantity_base": D(500),
        "limit_price": D("25.8"),
    }
    suspended = await adapter.submit_order(_req("bond-sell-1", **sale))
    assert suspended.fills[0].fee == D(18)
    clock.current_ms = fixtures.taipei(
        last_day + datetime.timedelta(days=1), 10
    )
    taxed = await adapter.submit_order(_req("bond-sell-2", **sale))
    assert taxed.fills[0].fee == D(18 + 12)


async def test_us_equities_settle_t_plus_one() -> None:
    """US purchases settle at the next session open (T+1)."""
    adapter, _, clock = _setup(fixtures.new_york(MONDAY, 10))
    request = _req(
        "aapl",
        account_id=USD,
        instrument_id=fixtures.AAPL,
        quantity_base=D(10),
        limit_price=D("190.01"),
    )
    result = await adapter.submit_order(request)
    assert result.fills and result.fills[0].fee_asset == "USD"
    snap = await adapter.account_snapshot(USD)
    assert snap.unsettled_cash is not None and snap.unsettled_cash < 0
    clock.current_ms = fixtures.new_york(
        MONDAY + datetime.timedelta(days=1), 9, 30
    )
    assert (await adapter.account_snapshot(USD)).unsettled_cash == D(0)


async def test_settlement_falls_back_to_weekdays() -> None:
    """Without session data settlement skips weekends only."""
    adapter, gateway, clock = _setup(fixtures.taipei(MONDAY, 10))
    await adapter.submit_order(_req("buy"))
    gateway.set_sessions("XTAI", [])
    friday = MONDAY + datetime.timedelta(days=4)
    clock.current_ms = fixtures.taipei(friday, 10)
    gateway.set_sessions("XTAI", fixtures.xtai_sessions(friday, 1))
    await adapter.submit_order(_req("fri", limit_price=D(601)))
    gateway.set_sessions("XTAI", [])
    pending = adapter._acct(TWD).pending
    tuesday = MONDAY + datetime.timedelta(days=8)
    assert pending[-1].settles_at_ms == fixtures.taipei(tuesday, 10)


async def test_long_only_sells_are_capped() -> None:
    """The paper cash account never sells shares it does not hold."""
    adapter, _, _ = _setup(fixtures.taipei(MONDAY, 10))
    result = await adapter.submit_order(
        _req("naked", side=enums.OrderSide.SELL, limit_price=D(599))
    )
    assert result.state.status is S.CANCELED
    assert "long-only" in (result.state.reason or "")


async def test_partial_fills_keep_board_lots() -> None:
    """Partial fills of board-lot orders are whole board lots."""
    adapter, _, _ = _setup(fixtures.taipei(MONDAY, 10))
    await adapter.submit_order(
        _req("big", quantity_base=D(3000), limit_price=D(590))
    )
    adapter.faults.partial_fill_fraction = D("0.5")
    order = adapter._acct(TWD).orders["big"]
    order.request = _req("big", quantity_base=D(3000), limit_price=D(605))
    await adapter.tick()
    assert order.filled == D(1000)


async def test_unknown_sessions_keep_orders_waiting() -> None:
    """Without a calendar securities orders wait instead of filling."""
    adapter, gateway, _ = _setup(fixtures.taipei(MONDAY, 10))
    gateway.unavailable = True
    result = await adapter.submit_order(_req("blind"))
    assert result.state.status is S.ACKNOWLEDGED and not result.fills


async def test_capabilities_follow_the_account_currency() -> None:
    """Paper capabilities describe a long-only cash account per currency."""
    adapter, _, _ = _setup(fixtures.taipei(MONDAY, 10))
    twd = await adapter.capabilities(TWD)
    assert twd.tradable_venues == ("twse", "tpex")
    assert twd.currencies == ("TWD",)
    assert twd.cash_account and not twd.short_selling and twd.odd_lot
    usd = await adapter.capabilities(USD)
    assert usd.tradable_venues == ("nasdaq", "nyse", "cboe")
    assert enums.TimeInForce.DAY in usd.time_in_force
    crypto = await adapter.submit_order(
        _req(
            "btc",
            account_id=USD,
            instrument_id="mock:perp:BTCUSD",
            quantity_base=D("0.01"),
            order_type=enums.OrderType.MARKET,
            limit_price=None,
            time_in_force=enums.TimeInForce.IOC,
        )
    )
    assert crypto.fills and crypto.fills[0].fee_asset == "USD"
    snap = await adapter.account_snapshot(USD)
    assert snap.unsettled_cash == D(0)
    assert snap.settled_cash == snap.equity
