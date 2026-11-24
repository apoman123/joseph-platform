# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

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

_Decimal = decimal.Decimal
MONDAY = datetime.date(2026, 11, 23)


@pytest.mark.parametrize(
    ("venue", "paper", "currency", "instrument", "allowed"),
    [
        ("binance", False, "USD", "binance:perp:BTCUSDT", True),
        ("binance", False, "USD", "bitget:perp:BTCUSDT", False),
        ("hyperliquid", False, "USD", "hyperliquid:perp:BTC", True),
        ("ibkr", False, "USD", "nasdaq:stock:AAPL", True),
        ("ibkr", False, "USD", "nyse:etf:SPY", True),
        ("ibkr", False, "USD", "cboe:etf:GOVT", True),
        ("ibkr", False, "USD", "twse:stock:2330", False),
        ("sinopac", False, "TWD", "twse:stock:2330", True),
        ("sinopac", False, "TWD", "tpex:stock:6488", True),
        ("sinopac", False, "TWD", "tpex:etf:00679B", True),
        ("sinopac", False, "TWD", "nasdaq:stock:AAPL", False),
        ("cathay", False, "TWD", "twse:etf:0050", True),
        ("cathay", False, "TWD", "tpex:etf:00937B", True),
        ("cathay", False, "TWD", "nyse:stock:IBM", False),
        ("sinopac", False, "TWD", "cboe:etf:GOVT", False),
        ("ibkr", False, "USD", "tpex:stock:6488", False),
        ("mock", True, "USD", "mock:perp:BTCUSD", True),
        ("mock", True, "USD", "binance:perp:BTCUSDT", True),
        ("mock", True, "USD", "nasdaq:stock:AAPL", True),
        ("mock", True, "USD", "cboe:etf:GOVT", True),
        ("mock", True, "TWD", "cboe:etf:GOVT", False),
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
    assert "Nasdaq, NYSE and Cboe BZX listings" in ib.message
    rate = equities.route("ibkr", False, "USD", fixtures.UST10Y)
    assert "rate series" in rate.message
    paper = equities.route("mock", True, "USD", fixtures.TSMC)
    assert "TWD paper account" in paper.message
    sinopac = equities.route("sinopac", False, "TWD", fixtures.AAPL)
    assert "TWSE and TPEx listings" in sinopac.message
    tpex = equities.route("cathay", False, "TWD", fixtures.GLOBALWAFERS)
    assert tpex.allowed and "TPEx via Cathay Securities" in tpex.message
    cboe = equities.route("ibkr", False, "USD", fixtures.GOVT)
    assert cboe.allowed and "Cboe BZX via Interactive" in cboe.message
    usd = equities.route("mock", True, "TWD", fixtures.GOVT)
    assert "Cboe BZX listings settle in USD" in usd.message


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
    """TWSE and TPEx stocks share the tick ladder at each price."""
    assert fixtures.twse().tick_for(_Decimal(price)) == _Decimal(tick)
    assert fixtures.tpex().tick_for(_Decimal(price)) == _Decimal(tick)


@pytest.mark.parametrize(
    ("price", "tick"),
    [("49.99", "0.01"), ("50", "0.05"), ("57.75", "0.05")],
)
def test_tpex_etf_tick_ladder(price: str, tick: str) -> None:
    """TPEx ETFs tick 0.01 below 50 TWD and 0.05 from 50 (as on TWSE)."""
    assert fixtures.tpex("00791B", etf=True).tick_for(
        _Decimal(price)
    ) == _Decimal(tick)


def test_ticks_and_rounding() -> None:
    """Prices are checked and rounded on the rung they fall in."""
    tsmc = fixtures.twse()
    assert equities.on_tick(_Decimal("600"), tsmc)
    assert not equities.on_tick(_Decimal("600.5"), tsmc)
    assert equities.on_tick(_Decimal("100.5"), tsmc)
    assert not equities.on_tick(_Decimal("100.1"), tsmc)
    assert equities.round_to_tick(_Decimal("600.4"), tsmc) == _Decimal(600)
    assert equities.round_to_tick(_Decimal("99.97"), tsmc) == _Decimal(100)
    etf = fixtures.twse("0050", etf=True)
    assert etf.tick_for(_Decimal(150)) == _Decimal("0.05")
    crypto = market.InstrumentMeta(
        "mock:perp:BTCUSD",
        "mock",
        _Decimal("0.1"),
        _Decimal("0.001"),
        _Decimal("0.001"),
        enums.InstrumentStatus.TRADING,
    )
    assert crypto.tick_for(_Decimal(95000)) == _Decimal("0.1")
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
    limits = equities.price_limits(_Decimal(reference), fixtures.twse())
    assert limits is not None
    assert (limits.low, limits.high) == (_Decimal(low), _Decimal(high))
    assert limits.contains(_Decimal(reference))
    assert limits.clamp(_Decimal(high) + 10) == _Decimal(high)
    assert limits.clamp(_Decimal(0)) == _Decimal(low)


def test_tpex_price_limits_match_published_limit_prices() -> None:
    """TPEx limits equal TPEx's published next-session limit prices."""
    # tpex_mainboard_quotes 2026-11-20: 6488 close 441.50 -> 397.50-485.50,
    # 3105 381.00 -> 343.00-419.00; ETF 00888 24.31 -> 21.88-26.74.
    for code, ref, low, high in (
        ("6488", "441.5", "397.5", "485.5"),
        ("3105", "381", "343", "419"),
    ):
        limits = equities.price_limits(_Decimal(ref), fixtures.tpex(code))
        assert limits is not None
        assert (limits.low, limits.high) == (_Decimal(low), _Decimal(high))
    etf = equities.price_limits(
        _Decimal("24.31"), fixtures.tpex("00888", etf=True)
    )
    assert etf is not None and (etf.low, etf.high) == (
        _Decimal("21.88"),
        _Decimal("26.74"),
    )
    bond = fixtures.tpex("00679B", etf=True, limited=False)
    assert equities.price_limits(_Decimal("26.15"), bond) is None


def test_no_limits_without_percentage() -> None:
    """Instruments without a daily limit or reference have no band."""
    assert equities.price_limits(_Decimal(100), fixtures.us()) is None
    assert equities.price_limits(_Decimal(0), fixtures.twse()) is None


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
    board = None if lot is None else _Decimal(lot)
    assert equities.lot_kind(_Decimal(quantity), board) is kind


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
    now = fixtures.taipei(MONDAY, 10)
    stock = equities.TaiwanTaxClass.STOCK
    buy = equities.taiwan_fee(
        enums.OrderSide.BUY, _Decimal(600_000), stock, now
    )
    assert buy == _Decimal(855)
    sell = equities.taiwan_fee(
        enums.OrderSide.SELL, _Decimal(600_000), stock, now
    )
    assert sell == _Decimal(855 + 1800)
    etf = equities.taiwan_fee(
        enums.OrderSide.SELL,
        _Decimal(150_000),
        equities.TaiwanTaxClass.ETF,
        now,
    )
    assert etf == _Decimal(213 + 150)
    bond = equities.taiwan_fee(
        enums.OrderSide.SELL,
        _Decimal(150_000),
        equities.TaiwanTaxClass.BOND_ETF,
        now,
    )
    assert bond == _Decimal(213)


_BOND = enums.AssetClass.BOND
_EQUITY = enums.AssetClass.EQUITY


@pytest.mark.parametrize(
    ("instrument", "asset_class", "expected"),
    [
        ("twse:stock:2330", _EQUITY, equities.TaiwanTaxClass.STOCK),
        ("tpex:stock:6488", None, equities.TaiwanTaxClass.STOCK),
        ("twse:etf:0050", _EQUITY, equities.TaiwanTaxClass.ETF),
        ("tpex:etf:00679B", _BOND, equities.TaiwanTaxClass.BOND_ETF),
        ("twse:etf:00865B", _BOND, equities.TaiwanTaxClass.BOND_ETF),
        ("tpex:etf:00679B", None, equities.TaiwanTaxClass.ETF),
        (
            "tpex:etf:00679B",
            enums.AssetClass.UNKNOWN,
            equities.TaiwanTaxClass.ETF,
        ),
        ("tpex:etf:00680L", _BOND, equities.TaiwanTaxClass.ETF),
        ("tpex:etf:00681R", _BOND, equities.TaiwanTaxClass.ETF),
        ("tpex:etf:00980D", _BOND, equities.TaiwanTaxClass.ETF),
    ],
)
def test_taiwan_tax_classes(
    instrument: str,
    asset_class: enums.AssetClass | None,
    expected: equities.TaiwanTaxClass,
) -> None:
    """BOND ETFs are bond ETFs except leveraged, inverse and active funds."""
    assert equities.taiwan_tax_class(instrument, asset_class) is expected


@pytest.mark.parametrize(
    ("tax_class", "rate"),
    [
        (equities.TaiwanTaxClass.STOCK, "0.003"),
        (equities.TaiwanTaxClass.ETF, "0.001"),
        (equities.TaiwanTaxClass.BOND_ETF, "0"),
    ],
)
def test_taiwan_tax_rate_per_class(
    tax_class: equities.TaiwanTaxClass, rate: str
) -> None:
    """Stocks pay 0.3%, ETFs 0.1% and bond ETFs nothing in 2026."""
    now = fixtures.taipei(MONDAY, 10)
    assert equities.taiwan_tax_rate(tax_class, now) == _Decimal(rate)


@pytest.mark.parametrize(
    ("day", "hour", "rate"),
    [
        (datetime.date(2016, 12, 30), 10, "0.001"),
        (datetime.date(2017, 1, 3), 10, "0"),
        (datetime.date(2026, 12, 31), 13, "0"),
        (datetime.date(2026, 12, 31), 23, "0"),
        (datetime.date(2027, 1, 1), 0, "0.001"),
        (datetime.date(2027, 1, 4), 10, "0.001"),
    ],
)
def test_bond_etf_suspension_ends_after_2026_12_31(
    day: datetime.date, hour: int, rate: str
) -> None:
    """The bond ETF suspension covers Taipei trade dates 2017-2026 only."""
    when = fixtures.taipei(day, hour)
    bond = equities.TaiwanTaxClass.BOND_ETF
    assert equities.taiwan_tax_rate(bond, when) == _Decimal(rate)
    sale = equities.taiwan_fee(
        enums.OrderSide.SELL, _Decimal(100_000), bond, when
    )
    assert sale == _Decimal(142) + _Decimal(100_000) * _Decimal(rate)


def test_suspension_follows_the_taipei_date_not_utc() -> None:
    """2026-12-31 16:30 UTC is already 2027-01-01 in Taipei (taxed)."""
    bond = equities.TaiwanTaxClass.BOND_ETF
    late_utc = fixtures.at(datetime.date(2026, 12, 31), 16, 30, datetime.UTC)
    assert equities.taipei_date(late_utc) == datetime.date(2027, 1, 1)
    assert equities.taiwan_tax_rate(bond, late_utc) == _Decimal("0.001")
    assert equities.BOND_ETF_TAX_SUSPENSION.contains(
        datetime.date(2026, 12, 31)
    )
    assert not equities.BOND_ETF_TAX_SUSPENSION.contains(
        datetime.date(2027, 1, 1)
    )


@pytest.mark.parametrize(
    ("day", "tax_class", "rate"),
    [
        (MONDAY, equities.TaiwanTaxClass.STOCK, "0.0015"),
        (datetime.date(2027, 12, 31), equities.TaiwanTaxClass.STOCK, "0.0015"),
        (datetime.date(2028, 1, 3), equities.TaiwanTaxClass.STOCK, "0.003"),
        (MONDAY, equities.TaiwanTaxClass.ETF, "0.001"),
        (MONDAY, equities.TaiwanTaxClass.BOND_ETF, "0"),
    ],
)
def test_day_trade_rate_applies_to_stocks_until_2027_12_31(
    day: datetime.date, tax_class: equities.TaiwanTaxClass, rate: str
) -> None:
    """A same-day round trip in shares pays 0.15% through 2027-12-31."""
    when = fixtures.taipei(day, 10)
    assert equities.taiwan_tax_rate(
        tax_class, when, day_trade=True
    ) == _Decimal(rate)
    fee = equities.taiwan_fee(
        enums.OrderSide.SELL, _Decimal(600_000), tax_class, when, day_trade=True
    )
    assert fee == _Decimal(855) + _Decimal(600_000) * _Decimal(rate)


def test_paper_account_ids() -> None:
    """Paper accounts encode their currency without colliding."""
    assert accounts.paper_account_id("t1") == "acct-paper-t1"
    twd = accounts.paper_account_id("t1", "TWD")
    assert twd == "acct-twd-paper-t1"
    assert accounts.paper_currency(twd) == "TWD"
    assert accounts.paper_currency("acct-paper-t1") == "USD"
    assert accounts.paper_currency("acct-paper-x-twd") == "USD"


@pytest.mark.parametrize(
    "account_id",
    [
        "acct-xyz",
        "acct-binance-60e50eb275ec",
        "acct-live-a",
        "a",
        "acct-paper-",
        "acct-t-w-d-paper-t1",
        "user-twd-paper-t1",
    ],
)
def test_paper_currency_refuses_ids_that_are_not_paper_ids(
    account_id: str,
) -> None:
    """Only ids from paper_account_id carry a currency; others raise."""
    with pytest.raises(ValueError, match="not a paper account id"):
        accounts.paper_currency(account_id)


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
    """Joseph client ids carry their creation time; masks keep four digits."""
    # ``j`` + 30 hex digits of a UUIDv7; the first 12 are the creation ms.
    cid = "j0193f0a0b1c2" + "7" * 18
    assert base.is_joseph_client_id(cid)
    assert base.is_joseph_client_id(cid + "-sl")
    assert not base.is_joseph_client_id("manual-order")
    assert base.client_order_ms(cid) == int("0193f0a0b1c2", 16)
    assert base.client_order_ms("x") is None
    assert base.mask("DU1234567") == "***4567"
    assert base.mask("12") == "***"
