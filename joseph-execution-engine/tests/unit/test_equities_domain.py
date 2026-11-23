"""Unit tests for securities rules: routing, ticks, limits, lots, sessions."""

from __future__ import annotations

import dataclasses
import datetime
import decimal

import pytest

from joseph_execution.adapters.brokers import base
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import market
from joseph_execution.testing import equities as fixtures

D = decimal.Decimal
MONDAY = datetime.date(2026, 11, 23)


@pytest.mark.parametrize(
    ("venue", "paper", "currency", "instrument", "allowed"),
    [
        ("binance", False, "USD", "binance:perp:BTCUSDT", True),
        ("binance", False, "USD", "bitget:perp:BTCUSDT", False),
        ("hyperliquid", False, "USD", "hyperliquid:perp:BTC", True),
        ("ibkr", False, "USD", "nasdaq:stock:AAPL", True),
        ("ibkr", False, "USD", "nyse:etf:SPY", True),
        ("ibkr", False, "USD", "twse:stock:2330", False),
        ("sinopac", False, "TWD", "twse:stock:2330", True),
        ("sinopac", False, "TWD", "tpex:stock:6488", True),
        ("sinopac", False, "TWD", "tpex:etf:00679B", True),
        ("sinopac", False, "TWD", "nasdaq:stock:AAPL", False),
        ("cathay", False, "TWD", "twse:etf:0050", True),
        ("cathay", False, "TWD", "tpex:etf:00937B", True),
        ("cathay", False, "TWD", "nyse:stock:IBM", False),
        ("ibkr", False, "USD", "tpex:stock:6488", False),
        ("mock", True, "USD", "mock:perp:BTCUSD", True),
        ("mock", True, "USD", "binance:perp:BTCUSDT", True),
        ("mock", True, "USD", "nasdaq:stock:AAPL", True),
        ("mock", True, "USD", "twse:stock:2330", False),
        ("mock", True, "TWD", "twse:stock:2330", True),
        ("mock", True, "TWD", "tpex:etf:00679B", True),
        ("mock", True, "USD", "tpex:stock:6488", False),
        ("mock", True, "TWD", "mock:perp:BTCUSD", False),
        ("mock", True, "TWD", "nasdaq:stock:AAPL", False),
        ("ibkr", False, "USD", "treasury:rate:UST10Y", False),
        ("mock", True, "USD", "treasury:rate:UST10Y", False),
    ],
)
def test_route_matrix(
    venue: str, paper: bool, currency: str, instrument: str, allowed: bool
) -> None:
    """Accounts trade only the venues their broker or currency serves."""
    verdict = equities.route(venue, paper, currency, instrument)
    assert verdict.allowed is allowed, verdict.message
    assert verdict.message


def test_route_messages_name_the_rule() -> None:
    """Routing errors explain the broker's exchanges and rate series."""
    ib = equities.route("ibkr", False, "USD", "twse:stock:2330")
    assert "Nasdaq and NYSE" in ib.message
    rate = equities.route("ibkr", False, "USD", fixtures.UST10Y)
    assert "rate series" in rate.message
    paper = equities.route("mock", True, "USD", fixtures.TSMC)
    assert "TWD paper account" in paper.message
    sinopac = equities.route("sinopac", False, "TWD", fixtures.AAPL)
    assert "TWSE and TPEx listings" in sinopac.message
    tpex = equities.route("cathay", False, "TWD", fixtures.GLOBALWAFERS)
    assert tpex.allowed and "TPEx via Cathay Securities" in tpex.message


def test_instrument_helpers() -> None:
    """Instrument ids parse leniently and classify securities and rates."""
    ref = equities.parse_instrument_id("twse:stock:2330")
    assert (ref.venue, ref.market_type, ref.symbol) == ("twse", "stock", "2330")
    assert equities.parse_instrument_id("junk").market_type == ""
    assert equities.is_security(fixtures.SPY)
    assert not equities.is_security("binance:perp:BTCUSDT")
    assert equities.is_rate("treasury:rate:UST10Y")
    assert equities.is_rate("mock:rate:X")
    assert equities.is_broker("sinopac") and not equities.is_broker("mexc")
    assert equities.instrument_currency(fixtures.TSMC) == "TWD"
    assert equities.instrument_currency(fixtures.AAPL, "USD") == "USD"
    assert equities.instrument_currency("mock:stock:X") == "USD"


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        ("9.99", "0.01"),
        ("10", "0.05"),
        ("49.95", "0.05"),
        ("50", "0.1"),
        ("100", "0.5"),
        ("499.5", "0.5"),
        ("500", "1"),
        ("999", "1"),
        ("1000", "5"),
    ],
)
def test_twse_tick_ladder(price: str, tick: str) -> None:
    """The tick at each price comes from the TWSE ladder."""
    assert fixtures.twse().tick_for(D(price)) == D(tick)
    assert fixtures.tpex().tick_for(D(price)) == D(tick)


@pytest.mark.parametrize(
    ("price", "tick"),
    [("49.99", "0.01"), ("50", "0.05"), ("57.75", "0.05")],
)
def test_tpex_etf_tick_ladder(price: str, tick: str) -> None:
    """TPEx ETFs tick 0.01 below 50 TWD and 0.05 from 50 (as on TWSE)."""
    assert fixtures.tpex("00791B", etf=True).tick_for(D(price)) == D(tick)


def test_ticks_and_rounding() -> None:
    """Prices are checked and rounded on the rung they fall in."""
    tsmc = fixtures.twse()
    assert equities.on_tick(D("600"), tsmc)
    assert not equities.on_tick(D("600.5"), tsmc)
    assert equities.on_tick(D("100.5"), tsmc)
    assert not equities.on_tick(D("100.1"), tsmc)
    assert equities.round_to_tick(D("600.4"), tsmc) == D(600)
    assert equities.round_to_tick(D("99.97"), tsmc) == D(100)
    etf = fixtures.twse("0050", etf=True)
    assert etf.tick_for(D(150)) == D("0.05")
    crypto = market.InstrumentMeta(
        "mock:perp:BTCUSD",
        "mock",
        D("0.1"),
        D("0.001"),
        D("0.001"),
        enums.InstrumentStatus.TRADING,
    )
    assert crypto.tick_for(D(95000)) == D("0.1")
    assert crypto.continuous and crypto.market_type == "perp"
    assert not tsmc.continuous and tsmc.market_type == "stock"


@pytest.mark.parametrize(
    ("reference", "low", "high"),
    [
        ("600", "540", "660"),
        ("105", "94.5", "115.5"),
        ("9.5", "8.55", "10.45"),
        ("1000", "900", "1100"),
        ("51", "45.9", "56.1"),
        ("101", "90.9", "111"),
    ],
)
def test_twse_price_limits(reference: str, low: str, high: str) -> None:
    """Daily limits round inwards to the tick at the limit price."""
    limits = equities.price_limits(D(reference), fixtures.twse())
    assert limits is not None
    assert (limits.low, limits.high) == (D(low), D(high))
    assert limits.contains(D(reference))
    assert limits.clamp(D(high) + 10) == D(high)
    assert limits.clamp(D(0)) == D(low)


def test_tpex_price_limits_match_published_limit_prices() -> None:
    """TPEx limits equal TPEx's published next-session limit prices."""
    # tpex_mainboard_quotes 2026-11-20: 6488 close 441.50 -> 397.50-485.50,
    # 3105 381.00 -> 343.00-419.00; ETF 00888 24.31 -> 21.88-26.74.
    for code, ref, low, high in (
        ("6488", "441.5", "397.5", "485.5"),
        ("3105", "381", "343", "419"),
    ):
        limits = equities.price_limits(D(ref), fixtures.tpex(code))
        assert limits is not None
        assert (limits.low, limits.high) == (D(low), D(high))
    etf = equities.price_limits(D("24.31"), fixtures.tpex("00888", etf=True))
    assert etf is not None and (etf.low, etf.high) == (D("21.88"), D("26.74"))
    bond = fixtures.tpex("00679B", etf=True, limited=False)
    assert equities.price_limits(D("26.15"), bond) is None


def test_no_limits_without_percentage() -> None:
    """Instruments without a daily limit or reference have no band."""
    assert equities.price_limits(D(100), fixtures.us()) is None
    assert equities.price_limits(D(0), fixtures.twse()) is None


@pytest.mark.parametrize(
    ("quantity", "lot", "kind"),
    [
        ("1000", "1000", equities.LotKind.BOARD),
        ("3000", "1000", equities.LotKind.BOARD),
        ("1", "1000", equities.LotKind.ODD),
        ("999", "1000", equities.LotKind.ODD),
        ("1500", "1000", equities.LotKind.MIXED),
        ("7", None, equities.LotKind.BOARD),
    ],
)
def test_lot_kind(
    quantity: str, lot: str | None, kind: equities.LotKind
) -> None:
    """Quantities classify as board lots, odd lots or invalid mixes."""
    board = None if lot is None else D(lot)
    assert equities.lot_kind(D(quantity), board) is kind


def test_session_state_and_day_expiry() -> None:
    """Sessions report open/closed, the next open and DAY expiry."""
    sessions = fixtures.xtai_sessions(MONDAY, 7)
    before = fixtures.taipei(MONDAY, 8)
    during = fixtures.taipei(MONDAY, 10)
    after = fixtures.taipei(MONDAY, 14)
    state = equities.session_state(sessions, during)
    assert state.open and state.current is sessions[0]
    closed = equities.session_state(sessions, after)
    assert not closed.open and closed.next_session is sessions[1]
    assert equities.session_state(sessions, before).next_session is sessions[0]
    assert equities.day_order_expiry(sessions, during) == sessions[0].close_ms
    assert equities.day_order_expiry(sessions, after) == sessions[1].close_ms
    assert equities.day_order_expiry([], after) is None
    assert sessions[0].contains(during) and not sessions[0].contains(after)


def test_settlement_counts_trading_days() -> None:
    """T+2 skips the weekend; missing sessions fall back to weekdays."""
    sessions = fixtures.xtai_sessions(MONDAY, 14)
    thursday = MONDAY + datetime.timedelta(days=3)
    trade = fixtures.taipei(thursday, 10)
    next_monday = MONDAY + datetime.timedelta(days=7)
    assert equities.settlement_ms(sessions, trade, 2) == fixtures.taipei(
        next_monday, 9
    )
    assert equities.settlement_ms(sessions, fixtures.taipei(MONDAY, 1), 2) is (
        None
    )
    assert equities.settlement_ms(sessions[:2], trade, 2) is None
    friday_noon = fixtures.taipei(MONDAY + datetime.timedelta(days=4), 12)
    fallback = equities.fallback_settlement_ms(friday_noon, 1)
    monday_noon = fixtures.taipei(MONDAY + datetime.timedelta(days=7), 12)
    assert fallback == monday_noon


def test_taiwan_fees() -> None:
    """Brokerage applies to every fill; the sale tax to sells only."""
    assert equities.is_taiwan("twse") and equities.is_taiwan("tpex")
    assert not equities.is_taiwan("nasdaq")
    buy = equities.taiwan_fee(enums.OrderSide.BUY, D(600_000), "stock")
    assert buy == D(855)
    sell = equities.taiwan_fee(enums.OrderSide.SELL, D(600_000), "stock")
    assert sell == D(855 + 1800)
    etf = equities.taiwan_fee(enums.OrderSide.SELL, D(150_000), "etf")
    assert etf == D(213 + 150)


def test_paper_account_ids() -> None:
    """Paper accounts encode their currency without colliding."""
    assert accounts.paper_account_id("t1") == "acct-paper-t1"
    twd = accounts.paper_account_id("t1", "TWD")
    assert twd == "acct-twd-paper-t1"
    assert accounts.paper_currency(twd) == "TWD"
    assert accounts.paper_currency("acct-paper-t1") == "USD"
    assert accounts.paper_currency("acct-paper-x-twd") == "USD"


def test_capabilities_serialize_new_fields() -> None:
    """Capability flags for brokers appear in the contract form."""
    caps = accounts.TradingCapabilities(
        "ibkr",
        enums.TradingStatus.AVAILABLE,
        (),
        (enums.TimeInForce.DAY,),
        False,
        True,
        True,
        False,
        True,
        False,
        ("ONE_WAY",),
        (),
        True,
        True,
        tradable_venues=("nasdaq",),
        outside_rth=True,
        currencies=("USD",),
    )
    body = caps.to_contract()
    assert body["time_in_force"] == ["DAY"]
    assert body["tradable_venues"] == ["nasdaq"]
    assert body["outside_rth"] is True and body["odd_lot"] is False
    assert dataclasses.replace(caps, simulation=True).to_contract()[
        "simulation"
    ]


def test_client_id_helpers() -> None:
    """Joseph client ids are recognized and carry their creation time."""
    cid = "j" + "0193f0a0b1c2" + "7" * 18
    assert base.is_joseph_client_id(cid)
    assert base.is_joseph_client_id(cid + "-sl")
    assert not base.is_joseph_client_id("manual-order")
    assert base.client_order_ms(cid) == int("0193f0a0b1c2", 16)
    assert base.client_order_ms("x") is None
    assert base.mask("DU1234567") == "***4567"
    assert base.mask("12") == "***"
