"""Unit tests for broker routing and securities risk checks."""

from __future__ import annotations

import dataclasses
import datetime
import decimal

import pytest

from joseph_execution.adapters.brokers import capabilities as broker_caps
from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import equities
from joseph_execution.domain import identity
from joseph_execution.domain import intents
from joseph_execution.domain import market
from joseph_execution.domain import positions
from joseph_execution.risk import checks
from joseph_execution.risk import context
from joseph_execution.risk import gateway
from joseph_execution.risk import limits
from joseph_execution.testing import equities as fixtures

D = decimal.Decimal
OUT = enums.CheckOutcome
MONDAY = datetime.date(2026, 11, 23)
OPEN_NOW = fixtures.taipei(MONDAY, 10)
CLOSED_NOW = fixtures.taipei(MONDAY, 15)
SESSIONS = tuple(fixtures.xtai_sessions(MONDAY, 10))
SINOPAC = accounts.Account(
    "acct-sp",
    "t",
    "sinopac",
    "SinoPac",
    enums.AccountStatus.CONNECTED,
    (enums.OrderMode.LIVE,),
    "TWD",
    1,
)
PAPER_TWD = accounts.Account(
    "acct-twd-paper-t",
    "t",
    "mock",
    "Paper TWD",
    enums.AccountStatus.CONNECTED,
    (enums.OrderMode.PAPER,),
    "TWD",
    1,
    D(3_000_000),
    True,
)
TSMC = fixtures.twse()
QUOTE = market.MarketQuote(
    fixtures.TSMC,
    enums.FeedState.LIVE,
    OPEN_NOW,
    D(600),
    D(600),
    D(599),
    D(601),
)
BOOK = market.BookView(
    fixtures.TSMC, ((D(599), D(5000)),), ((D(601), D(5000)),), OPEN_NOW
)


def _request(**overrides: object) -> intents.OrderIntentRequest:
    """Builds a LIVE TWSE board-lot limit buy.

    Args:
        **overrides: Field overrides.

    Returns:
        Request.
    """
    fields: dict[str, object] = {
        "account_id": SINOPAC.account_id,
        "instrument_id": fixtures.TSMC,
        "side": enums.OrderSide.BUY,
        "position_effect": enums.PositionEffect.OPEN,
        "order_type": enums.OrderType.LIMIT,
        "limit_price": D(600),
        "quantity_base": D(1000),
        "time_in_force": enums.TimeInForce.DAY,
        "reduce_only": False,
        "mode": enums.OrderMode.LIVE,
        "source": enums.IntentSource.MANUAL_TICKET,
    }
    fields.update(overrides)
    return intents.OrderIntentRequest(**fields)  # type: ignore[arg-type]


def _ctx(**overrides: object) -> context.RiskContext:
    """Builds a securities context in which every check passes.

    Args:
        **overrides: Field overrides.

    Returns:
        Context.
    """
    fields: dict[str, object] = {
        "request": _request(),
        "principal": identity.Principal("t", "u"),
        "now_ms": OPEN_NOW,
        "limits": limits.RiskLimits(
            max_account_notional=D(10_000_000),
            max_position_notional=D(5_000_000),
        ),
        "live_trading_enabled": True,
        "emergency_halt": False,
        "account": SINOPAC,
        "capabilities": broker_caps.sinopac(simulation=True),
        "adapter_health": enums.HealthState.HEALTHY,
        "instrument": TSMC,
        "quote": QUOTE,
        "book": BOOK,
        "position": None,
        "open_position_count": 0,
        "gross_notional": D(0),
        "equity": D(2_000_000),
        "available_margin": D(2_000_000),
        "daily_realized_pnl": D(0),
        "account_state_unknown": False,
        "kill_switches": (),
        "deployment": None,
        "signal_age_ms": None,
        "signal_intents": 0,
        "strategy_open_orders": 0,
        "recent_rejects": 0,
        "notional": D(600_000),
        "margin_required": D(600_855),
        "slippage_pct": D("0.1"),
        "sessions": SESSIONS,
        "previous_close": D(600),
        "cash": equities.CashBalance("TWD", D(1_000_000)),
        "estimated_fee": D(855),
    }
    fields.update(overrides)
    armed = fields["account"]
    if "live_arming" not in overrides and isinstance(armed, accounts.Account):
        # LIVE manual orders need an account arming; these tests target
        # the securities checks, so the effective account is armed.
        fields["live_arming"] = accounts.LiveArming.grant(
            armed, "user:u", int(str(fields["now_ms"])) - 1_000, 3_600_000
        )
    return context.RiskContext(**fields)  # type: ignore[arg-type]


def _held(qty: str) -> positions.Position:
    """Builds a long TWSE position.

    Args:
        qty: Shares.

    Returns:
        Position.
    """
    pos = positions.Position(
        "p", "t", SINOPAC.account_id, fixtures.TSMC, enums.OrderMode.LIVE
    )
    pos.apply_fill(enums.OrderSide.BUY, D(qty), D(590), D(0), 1)
    return pos


def test_clean_securities_order_is_allowed() -> None:
    """A board-lot TWSE limit buy in session with cash passes every check."""
    decision = gateway.RiskGateway().evaluate(_ctx())
    assert decision.allowed, [c.to_contract() for c in decision.failures()]
    names = {c.check: c.outcome for c in decision.checks}
    assert names["routing.venue"] is OUT.PASS
    assert names["market.session"] is OUT.PASS
    assert names["size.board_lot"] is OUT.PASS
    assert names["size.price_limit"] is OUT.PASS
    assert names["account.cash"] is OUT.PASS
    assert names["account.margin"] is OUT.SKIP
    assert names["size.cash_account"] is OUT.PASS


@pytest.mark.parametrize(
    ("account", "instrument", "fragment"),
    [
        (SINOPAC, fixtures.AAPL, "TWSE listings"),
        (
            dataclasses.replace(SINOPAC, venue="ibkr", currency="USD"),
            fixtures.TSMC,
            "Nasdaq and NYSE",
        ),
        (SINOPAC, fixtures.UST10Y, "rate series"),
        (
            dataclasses.replace(SINOPAC, venue="binance"),
            fixtures.TSMC,
            "binance instruments only",
        ),
    ],
)
def test_routing_rejects_unserved_venues(
    account: accounts.Account, instrument: str, fragment: str
) -> None:
    """Orders for venues an account does not serve are rejected."""
    ctx = _ctx(account=account, request=_request(instrument_id=instrument))
    result = checks.routing_venue(ctx)
    assert result.outcome is OUT.FAIL and fragment in result.message
    assert result.code == "UNSUPPORTED"
    decision = gateway.RiskGateway().evaluate(ctx)
    assert decision.decision is enums.Decision.REJECT


def test_routing_unknown_account_and_adapter_venues() -> None:
    """Unknown accounts are UNKNOWN; adapters must route the exchange."""
    assert checks.routing_venue(_ctx(account=None)).outcome is OUT.UNKNOWN
    narrow = dataclasses.replace(
        broker_caps.sinopac(), tradable_venues=("nasdaq",)
    )
    result = checks.routing_venue(_ctx(capabilities=narrow))
    assert result.outcome is OUT.FAIL and "does not route" in result.message


def test_crypto_orders_skip_securities_checks() -> None:
    """Crypto orders keep their decisions; securities checks only skip."""
    crypto_caps = accounts.TradingCapabilities(
        "mock",
        enums.TradingStatus.AVAILABLE,
        tuple(enums.OrderType),
        tuple(enums.TimeInForce),
        True,
        True,
        True,
        False,
        True,
        False,
        ("ONE_WAY",),
        (enums.MarginMode.ISOLATED,),
        True,
        True,
    )
    paper = dataclasses.replace(
        PAPER_TWD, account_id="acct", currency="USD", starting_equity=D(1)
    )
    instrument = market.InstrumentMeta(
        "mock:perp:BTCUSD",
        "mock",
        D("0.1"),
        D("0.001"),
        D("0.001"),
        enums.InstrumentStatus.TRADING,
    )
    ctx = _ctx(
        account=paper,
        capabilities=crypto_caps,
        instrument=instrument,
        request=_request(
            account_id="acct",
            instrument_id="mock:perp:BTCUSD",
            mode=enums.OrderMode.PAPER,
            limit_price=D(95000),
            quantity_base=D("0.01"),
            time_in_force=enums.TimeInForce.GTC,
        ),
        quote=dataclasses.replace(
            QUOTE, instrument_id="mock:perp:BTCUSD", mark=D(95000)
        ),
        notional=D(950),
        margin_required=D(950),
        available_margin=D(100_000),
        cash=None,
        sessions=None,
    )
    new = {
        "routing_venue",
        "market_session",
        "size_board_lot",
        "size_price_limit",
        "account_cash",
        "size_cash_account",
        "protection_bracket",
    }
    old_pipeline = tuple(s for s in checks.PIPELINE if s.fn.__name__ not in new)
    full = gateway.RiskGateway().evaluate(ctx)
    legacy = gateway.RiskGateway(old_pipeline).evaluate(ctx)
    assert full.decision is legacy.decision is enums.Decision.ALLOW
    for result in full.checks:
        if result.check in (
            "market.session",
            "size.board_lot",
            "size.price_limit",
            "account.cash",
            "size.cash_account",
            "protection.bracket_orders",
        ):
            assert result.outcome is OUT.SKIP, result.check


def test_day_and_outside_rth_need_an_exchange() -> None:
    """DAY and outside-RTH are rejected for 24x7 crypto markets."""
    crypto = _request(
        instrument_id="binance:perp:BTCUSDT",
        quantity_base=D("0.01"),
        limit_price=D(95000),
    )
    assert checks.market_session(_ctx(request=crypto)).outcome is OUT.FAIL
    gtc = dataclasses.replace(
        crypto, time_in_force=enums.TimeInForce.GTC, outside_rth=True
    )
    result = checks.market_session(_ctx(request=gtc))
    assert result.outcome is OUT.FAIL and "Outside-RTH" in result.message


def test_session_closed_rules() -> None:
    """Closed markets reject market orders and IOC; limits rest."""
    closed = {"now_ms": CLOSED_NOW}
    market_order = _request(
        order_type=enums.OrderType.MARKET,
        limit_price=None,
        time_in_force=enums.TimeInForce.IOC,
    )
    rejected = checks.market_session(_ctx(request=market_order, **closed))
    assert rejected.outcome is OUT.FAIL
    assert "rejected until the next open" in rejected.message
    ioc = _request(time_in_force=enums.TimeInForce.IOC)
    assert checks.market_session(_ctx(request=ioc, **closed)).outcome is (
        OUT.FAIL
    )
    resting = checks.market_session(_ctx(**closed))
    assert resting.outcome is OUT.WARN and "rests until" in resting.message
    unknown = checks.market_session(_ctx(sessions=None))
    assert unknown.outcome is OUT.UNKNOWN
    after_last = checks.market_session(_ctx(sessions=SESSIONS[:1], **closed))
    assert after_last.outcome is OUT.WARN
    assert "(" not in after_last.message


def test_session_queue_policy_for_market_orders() -> None:
    """With the queue policy a DAY market order queues for the open."""
    paper_caps = dataclasses.replace(
        broker_caps.sinopac(), queue_market_when_closed=True
    )
    day_market = _request(order_type=enums.OrderType.MARKET, limit_price=None)
    queued = checks.market_session(
        _ctx(
            request=day_market,
            now_ms=CLOSED_NOW,
            capabilities=paper_caps,
            queue_closed_market_orders=True,
        )
    )
    assert queued.outcome is OUT.WARN and "queues as DAY" in queued.message
    ioc_market = dataclasses.replace(
        day_market, time_in_force=enums.TimeInForce.IOC
    )
    needs_day = checks.market_session(
        _ctx(
            request=ioc_market,
            now_ms=CLOSED_NOW,
            capabilities=paper_caps,
            queue_closed_market_orders=True,
        )
    )
    assert needs_day.outcome is OUT.FAIL and "as DAY" in needs_day.message
    broker_refuses = checks.market_session(
        _ctx(
            request=day_market,
            now_ms=CLOSED_NOW,
            queue_closed_market_orders=True,
        )
    )
    assert broker_refuses.outcome is OUT.FAIL


def test_outside_rth_rules() -> None:
    """Outside-RTH needs broker support and a limit or stop-limit order."""
    flagged = _request(outside_rth=True)
    assert checks.market_session(_ctx(request=flagged)).outcome is OUT.FAIL
    ib_caps = broker_caps.ibkr()
    market_flag = _request(
        outside_rth=True, order_type=enums.OrderType.MARKET, limit_price=None
    )
    result = checks.market_session(
        _ctx(request=market_flag, capabilities=ib_caps)
    )
    assert result.outcome is OUT.FAIL and "limit" in result.message
    allowed = checks.market_session(
        _ctx(request=flagged, capabilities=ib_caps, now_ms=CLOSED_NOW)
    )
    assert allowed.outcome is OUT.WARN and "extended-hours" in allowed.message


@pytest.mark.parametrize(
    ("overrides", "outcome", "fragment"),
    [
        ({}, OUT.PASS, "board lot"),
        ({"quantity_base": D(1500)}, OUT.FAIL, "500 as an intraday"),
        ({"quantity_base": D(300)}, OUT.PASS, "odd-lot order of 300"),
        (
            {
                "quantity_base": D(300),
                "order_type": enums.OrderType.MARKET,
                "limit_price": None,
            },
            OUT.FAIL,
            "must be limit",
        ),
        (
            {"quantity_base": D(300), "time_in_force": enums.TimeInForce.IOC},
            OUT.FAIL,
            "ROD (DAY) only",
        ),
    ],
)
def test_board_and_odd_lots(
    overrides: dict[str, object], outcome: OUT, fragment: str
) -> None:
    """TWSE board lots and intraday odd lots follow exchange rules."""
    result = checks.size_board_lot(_ctx(request=_request(**overrides)))
    assert result.outcome is outcome and fragment in result.message


def test_odd_lots_need_broker_support() -> None:
    """Brokers without odd lots refuse 1-999 shares; unknown caps block."""
    odd = _request(quantity_base=D(10))
    no_odd = dataclasses.replace(broker_caps.sinopac(), odd_lot=False)
    assert (
        checks.size_board_lot(_ctx(request=odd, capabilities=no_odd)).outcome
        is OUT.FAIL
    )
    assert (
        checks.size_board_lot(_ctx(request=odd, capabilities=None)).outcome
        is OUT.UNKNOWN
    )
    us = _request(instrument_id=fixtures.AAPL, quantity_base=D(7))
    assert (
        checks.size_board_lot(
            _ctx(request=us, instrument=fixtures.us())
        ).outcome
        is OUT.SKIP
    )


def test_ladder_ticks_in_size_tick() -> None:
    """Limit prices must sit on the TWSE ladder tick of their price."""
    off = checks.size_tick(_ctx(request=_request(limit_price=D("600.5"))))
    assert off.outcome is OUT.FAIL and off.limit == "1"
    on = checks.size_tick(_ctx(request=_request(limit_price=D("100.5"))))
    assert on.outcome is OUT.PASS


@pytest.mark.parametrize(
    ("price", "outcome"),
    [
        ("660", OUT.PASS),
        ("661", OUT.FAIL),
        ("540", OUT.PASS),
        ("539", OUT.FAIL),
    ],
)
def test_daily_price_limits(price: str, outcome: OUT) -> None:
    """Limit prices stay within +-10% of the previous close."""
    result = checks.size_price_limit(
        _ctx(request=_request(limit_price=D(price)))
    )
    assert result.outcome is outcome
    if outcome is OUT.FAIL:
        assert "540-660" in result.message


def test_price_limit_skips_and_unknowns() -> None:
    """Market orders skip; an unknown reference price fails closed."""
    market_order = _request(order_type=enums.OrderType.MARKET, limit_price=None)
    assert (
        checks.size_price_limit(_ctx(request=market_order)).outcome is OUT.SKIP
    )
    assert (
        checks.size_price_limit(_ctx(previous_close=None)).outcome
        is OUT.UNKNOWN
    )
    us = _request(instrument_id=fixtures.AAPL, limit_price=D(190))
    assert (
        checks.size_price_limit(
            _ctx(request=us, instrument=fixtures.us())
        ).outcome
        is OUT.SKIP
    )


def test_cash_buying_power() -> None:
    """Purchases need cash in the instrument currency; held sells need none."""
    assert checks.account_cash(_ctx()).outcome is OUT.PASS
    short = checks.account_cash(
        _ctx(cash=equities.CashBalance("TWD", D(500_000)))
    )
    assert short.outcome is OUT.FAIL and short.code == "INSUFFICIENT_MARGIN"
    usd = checks.account_cash(_ctx(cash=equities.CashBalance("USD", D(10**9))))
    assert usd.outcome is OUT.FAIL and "trades in TWD" in usd.message
    assert checks.account_cash(_ctx(cash=None)).outcome is OUT.UNKNOWN
    assert checks.account_cash(_ctx(notional=None)).outcome is OUT.UNKNOWN
    sell = _request(side=enums.OrderSide.SELL)
    held = checks.account_cash(
        _ctx(request=sell, position=_held("1000"), cash=None)
    )
    assert held.outcome is OUT.SKIP


def test_cash_account_rules() -> None:
    """Cash accounts trade without leverage and never sell short."""
    levered = checks.size_cash_account(_ctx(request=_request(leverage=D(2))))
    assert levered.outcome is OUT.FAIL and "leverage" in levered.message
    sell = _request(side=enums.OrderSide.SELL)
    naked = checks.size_cash_account(_ctx(request=sell))
    assert naked.outcome is OUT.FAIL and "Long-only" in naked.message
    covered = checks.size_cash_account(
        _ctx(request=sell, position=_held("1000"))
    )
    assert covered.outcome is OUT.PASS
    shorting = dataclasses.replace(broker_caps.ibkr(short_selling=True))
    us_sell = _request(
        instrument_id=fixtures.AAPL, side=enums.OrderSide.SELL, leverage=D(1)
    )
    assert (
        checks.size_cash_account(
            _ctx(request=us_sell, capabilities=shorting)
        ).outcome
        is OUT.PASS
    )
    assert (
        checks.size_cash_account(_ctx(capabilities=None)).outcome is OUT.UNKNOWN
    )


def test_reduce_only_sells_on_long_only_accounts() -> None:
    """Reduce-only sells pass on long-only accounts without native flags."""
    close = _request(
        side=enums.OrderSide.SELL,
        reduce_only=True,
        position_effect=enums.PositionEffect.CLOSE,
    )
    assert checks.size_order_type(_ctx(request=close)).outcome is OUT.PASS
    cover = dataclasses.replace(close, side=enums.OrderSide.BUY)
    assert checks.size_order_type(_ctx(request=cover)).outcome is OUT.FAIL


def test_brackets_need_stop_orders() -> None:
    """Brackets fail on brokers without stop or GTC orders."""
    bracket = intents.Bracket(
        stop_loss=intents.StopLossSpec(D(570)),
        take_profit=(intents.TakeProfitSpec(D(650)),),
    )
    with_bracket = _request(bracket=bracket)
    result = checks.protection_bracket(_ctx(request=with_bracket))
    assert result.outcome is OUT.FAIL
    assert "stop-market" in result.message and "GTC" in result.message
    ok = checks.protection_bracket(
        _ctx(request=with_bracket, capabilities=broker_caps.ibkr())
    )
    assert ok.outcome is OUT.PASS
    assert checks.protection_bracket(_ctx()).outcome is OUT.SKIP
    assert (
        checks.protection_bracket(
            _ctx(request=with_bracket, capabilities=None)
        ).outcome
        is OUT.UNKNOWN
    )
    tp_only = _request(
        bracket=intents.Bracket(take_profit=(intents.TakeProfitSpec(D(650)),))
    )
    no_limit = dataclasses.replace(
        broker_caps.ibkr(), order_types=(enums.OrderType.MARKET,)
    )
    missing = checks.protection_bracket(
        _ctx(request=tp_only, capabilities=no_limit)
    )
    assert "limit" in missing.message
