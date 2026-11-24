# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Rules for exchange-listed securities and broker routing.

Pure functions shared by the Risk Gateway, the OMS and the paper
simulator:

* Routing: crypto accounts trade their own venue; broker accounts trade
  the exchanges their broker serves (``ibkr`` -> Nasdaq/NYSE/Cboe BZX,
  ``sinopac`` and ``cathay`` -> TWSE and TPEx); paper accounts trade in
  their own currency. Treasury rates are published series and are never
  tradable.
* Sessions from exchange calendars, DAY-order expiry and settlement days.
* Price-dependent tick ladders and daily price limits (TWSE and TPEx +-10%
  from the reference price), board lots and intraday odd lots.
* Estimated Taiwan brokerage fees and the securities transaction tax by
  trade date (stocks, ETFs, bond ETFs during the statutory suspension).

TPEx (Taipei Exchange) mainboard listings follow the TWSE rules here, as
TPEx publishes them ("上櫃有價證券交易系統簡介",
https://www.tpex.org.tw/zh-tw/mainboard/trading/rules/system.html,
read 2026-11-23): the same stock and ETF tick ladders, a 10% limit from
the reference price (none for foreign-constituent ETFs and the first five
days of a new listing; the market engine publishes ``price_limit_pct``
null then), 1,000-share board lots, intraday odd lots of 1-999 shares,
09:00-13:30 sessions on the XTAI calendar and T+2 settlement. The
emerging-stock board (興櫃) is not a ``tpex`` listing.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
from collections.abc import Mapping
from collections.abc import Sequence

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import market

CRYPTO_VENUES = frozenset(
    {
        enums.Venue.BINANCE.value,
        enums.Venue.HYPERLIQUID.value,
        enums.Venue.LIGHTER.value,
        enums.Venue.BITGET.value,
        enums.Venue.MEXC.value,
    }
)
"""Venues whose accounts trade the venue's own instruments."""

EXCHANGE_VENUES = frozenset(
    {
        enums.Venue.TWSE.value,
        enums.Venue.TPEX.value,
        enums.Venue.NASDAQ.value,
        enums.Venue.NYSE.value,
        enums.Venue.CBOE.value,
    }
)
"""Securities exchanges reached through a broker."""

TAIWAN_VENUES = frozenset({enums.Venue.TWSE.value, enums.Venue.TPEX.value})
"""Taiwan exchanges: Taiwan fees and taxes, TWD, board and odd lots."""

RATE_VENUES = frozenset({enums.Venue.TREASURY.value})
"""Publishers of rate series that are never tradable."""

SECURITY_TYPES = frozenset({"stock", "etf"})
"""Market types of exchange-listed cash securities."""

BROKER_EXCHANGES: Mapping[str, tuple[str, ...]] = {
    enums.Broker.IBKR.value: (
        enums.Venue.NASDAQ.value,
        enums.Venue.NYSE.value,
        enums.Venue.CBOE.value,
    ),
    enums.Broker.SINOPAC.value: (
        enums.Venue.TWSE.value,
        enums.Venue.TPEX.value,
    ),
    enums.Broker.CATHAY.value: (
        enums.Venue.TWSE.value,
        enums.Venue.TPEX.value,
    ),
}
"""Exchange venues each broker routes orders to."""

BROKER_CURRENCY: Mapping[str, str] = {
    enums.Broker.IBKR.value: "USD",
    enums.Broker.SINOPAC.value: "TWD",
    enums.Broker.CATHAY.value: "TWD",
}
"""Settlement currency of each broker's securities account."""

EXCHANGE_CURRENCY: Mapping[str, str] = {
    enums.Venue.TWSE.value: "TWD",
    enums.Venue.TPEX.value: "TWD",
    enums.Venue.NASDAQ.value: "USD",
    enums.Venue.NYSE.value: "USD",
    enums.Venue.CBOE.value: "USD",
}
"""Trading currency of each exchange's listings."""

SETTLEMENT_DAYS: Mapping[str, int] = {"XTAI": 2, "XNYS": 1}
"""Settlement cycle in trading days by calendar.

TWSE and TPEx settle T+2. US equities settle T+1 since 2024-05-28 (SEC rule
15c6-1 as amended).
"""

DISPLAY_NAMES: Mapping[str, str] = {
    enums.Broker.IBKR.value: "Interactive Brokers",
    enums.Broker.SINOPAC.value: "SinoPac",
    enums.Broker.CATHAY.value: "Cathay Securities",
    enums.Venue.TWSE.value: "TWSE",
    enums.Venue.TPEX.value: "TPEx",
    enums.Venue.NASDAQ.value: "Nasdaq",
    enums.Venue.NYSE.value: "NYSE",
    enums.Venue.CBOE.value: "Cboe BZX",
    enums.Venue.TREASURY.value: "US Treasury",
}
"""Human-facing names used in risk messages."""

TAIWAN_BROKERAGE_RATE = decimal.Decimal("0.001425")
"""Statutory maximum brokerage commission (0.1425%) before discounts."""

_DAY_MS = 86_400_000  # One UTC day in milliseconds.


def _name(code: str) -> str:
    """Returns the display name of a venue or broker code.

    Args:
        code: Venue or broker code.

    Returns:
        Display name, else the code.
    """
    return DISPLAY_NAMES.get(code, code)


def _listing(names: Sequence[str]) -> str:
    """Joins names as plain English (``A, B and C``).

    Args:
        names: Names in order.

    Returns:
        The joined names.
    """
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentRef:
    """Parts of a canonical ``venue:market_type:symbol`` instrument id.

    Attributes:
        venue: Venue code.
        market_type: Market type such as ``perp`` or ``stock``.
        symbol: Venue-native symbol.
    """

    venue: str
    market_type: str
    symbol: str


def parse_instrument_id(instrument_id: str) -> InstrumentRef:
    """Splits an instrument id leniently (missing parts become empty).

    Args:
        instrument_id: Canonical id such as ``twse:stock:2330``.

    Returns:
        Parts; the symbol keeps any further ``:``.
    """
    venue, _, rest = instrument_id.partition(":")
    market_type, _, symbol = rest.partition(":")
    return InstrumentRef(venue, market_type, symbol)


def is_security(instrument_id: str) -> bool:
    """Checks whether an instrument is an exchange-listed cash security.

    Args:
        instrument_id: Canonical id.

    Returns:
        True for ``stock`` and ``etf`` market types.
    """
    return parse_instrument_id(instrument_id).market_type in SECURITY_TYPES


def is_rate(instrument_id: str) -> bool:
    """Checks whether an instrument is a published, non-tradable rate.

    Args:
        instrument_id: Canonical id.

    Returns:
        True for rate venues or the ``rate`` market type.
    """
    ref = parse_instrument_id(instrument_id)
    return ref.venue in RATE_VENUES or ref.market_type == "rate"


def is_broker(venue: str) -> bool:
    """Checks whether an account venue is a securities broker.

    Args:
        venue: Account venue (contract ``TradingVenue``).

    Returns:
        True for ``ibkr``, ``sinopac`` and ``cathay``.
    """
    return venue in BROKER_EXCHANGES


def instrument_currency(instrument_id: str, quote: str | None = None) -> str:
    """Returns the currency an instrument trades and settles in.

    Args:
        instrument_id: Canonical id.
        quote: Quote currency from instrument metadata, if known.

    Returns:
        ``quote`` when given, else the currency of the instrument's
        exchange venue, else ``USD``.
    """
    venue = parse_instrument_id(instrument_id).venue
    if quote:
        return quote
    return EXCHANGE_CURRENCY.get(venue, "USD")


@dataclasses.dataclass(frozen=True, slots=True)
class Route:
    """Routing verdict for an account and an instrument.

    Attributes:
        allowed: Whether the account may trade the instrument.
        message: Plain-language explanation.
    """

    allowed: bool
    message: str


def route(  # noqa: PLR0911 - one return per routing rule, in precedence order.
    account_venue: str,
    is_paper: bool,
    account_currency: str,
    instrument_id: str,
) -> Route:
    """Decides whether an account may trade an instrument.

    Rules in order: rate series are never tradable; a paper account trades
    exchange listings that settle in its currency, and only the USD paper
    account trades other (simulated venue) instruments; broker accounts
    trade only the exchanges of ``BROKER_EXCHANGES``; venue accounts trade
    only their own venue.

    Args:
        account_venue: Account venue (``mock`` for paper accounts).
        is_paper: Whether the account is a paper account.
        account_currency: Account settlement currency.
        instrument_id: Canonical instrument id.

    Returns:
        Verdict with a plain-language explanation.
    """
    ref = parse_instrument_id(instrument_id)
    if is_rate(instrument_id):
        return Route(
            False,
            f"{instrument_id} is a published {_name(ref.venue)} rate series, "
            "not a tradable instrument.",
        )
    if is_paper:
        if ref.venue in EXCHANGE_VENUES:
            needed = EXCHANGE_CURRENCY[ref.venue]
            if needed != account_currency:
                return Route(
                    False,
                    f"{_name(ref.venue)} listings settle in {needed}; this "
                    f"paper account holds {account_currency}. Use the "
                    f"{needed} paper account.",
                )
            return Route(True, f"Paper {_name(ref.venue)} trading.")
        if account_currency != "USD":
            return Route(
                False,
                f"The {account_currency} paper account trades "
                f"{account_currency} securities only.",
            )
        return Route(True, "Paper trading on a simulated venue.")
    if is_broker(account_venue):
        served = BROKER_EXCHANGES[account_venue]
        if ref.venue not in served:
            names = _listing([_name(v) for v in served])
            return Route(
                False,
                f"{_name(account_venue)} accounts route {names} listings; "
                f"{instrument_id} is not one of them.",
            )
        return Route(
            True,
            f"Routed to {_name(ref.venue)} via {_name(account_venue)}.",
        )
    if ref.venue != account_venue:
        return Route(
            False,
            f"{account_venue} accounts trade {account_venue} instruments "
            f"only; {instrument_id} is listed on {ref.venue or 'unknown'}.",
        )
    return Route(True, f"Order goes to {account_venue}.")


# ---------------------------------------------------------------------------
# Ticks and price limits
# ---------------------------------------------------------------------------


def ceil_to_step(
    value: decimal.Decimal, step: decimal.Decimal
) -> decimal.Decimal:
    """Rounds up to a multiple of ``step``.

    Args:
        value: Value to round.
        step: Positive increment.

    Returns:
        Smallest multiple of ``step`` not below ``value``.

    Raises:
        ValueError: If ``step`` is not positive.
    """
    return -decimals.floor_to_step(-value, step)


def round_to_tick(
    price: decimal.Decimal, instrument: market.InstrumentMeta
) -> decimal.Decimal:
    """Rounds a price to the nearest tick of the ladder rung it falls in.

    Args:
        price: Price.
        instrument: Instrument metadata.

    Returns:
        Price on the tick grid (half-even on a tie).

    Raises:
        ValueError: If the tick at ``price`` is not positive.
    """
    return decimals.round_to_step(price, instrument.tick_for(price))


def on_tick(price: decimal.Decimal, instrument: market.InstrumentMeta) -> bool:
    """Checks a price against the (ladder-aware) tick grid.

    Args:
        price: Price.
        instrument: Instrument metadata.

    Returns:
        True when the price is a multiple of its tick.
    """
    return decimals.is_multiple(price, instrument.tick_for(price))


@dataclasses.dataclass(frozen=True, slots=True)
class PriceLimits:
    """The daily price limits of one session, both inclusive.

    Attributes:
        reference: Reference price (previous close).
        low: Limit-down price.
        high: Limit-up price.
    """

    reference: decimal.Decimal
    low: decimal.Decimal
    high: decimal.Decimal

    def contains(self, price: decimal.Decimal) -> bool:
        """Checks a price against the limits.

        Args:
            price: Price.

        Returns:
            True when ``low <= price <= high``.
        """
        return self.low <= price <= self.high

    def clamp(self, price: decimal.Decimal) -> decimal.Decimal:
        """Clamps a price into the limits.

        Args:
            price: Price.

        Returns:
            Clamped price.
        """
        return min(max(price, self.low), self.high)


def price_limits(
    reference: decimal.Decimal, instrument: market.InstrumentMeta
) -> PriceLimits | None:
    """Computes the daily limits from a reference price.

    TWSE and TPEx round limit-up down and limit-down up to the tick that
    applies at the limit price.

    Args:
        reference: Reference price (previous close).
        instrument: Instrument metadata with ``price_limit_pct``.

    Returns:
        Limits, or ``None`` when the instrument has no daily limit or the
        reference price is not positive.
    """
    pct = instrument.price_limit_pct
    if pct is None or reference <= decimals.ZERO:
        return None
    band = pct / decimals.HUNDRED
    raw_high = reference * (decimals.ONE + band)
    raw_low = reference * (decimals.ONE - band)
    high = decimals.floor_to_step(raw_high, instrument.tick_for(raw_high))
    low = ceil_to_step(raw_low, instrument.tick_for(raw_low))
    return PriceLimits(reference, low, high)


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------


class LotKind(enum.StrEnum):
    """How a quantity relates to the board lot."""

    BOARD = "board"
    ODD = "odd"
    MIXED = "mixed"


def lot_kind(
    quantity: decimal.Decimal, board_lot: decimal.Decimal | None
) -> LotKind:
    """Classifies a quantity against the board lot.

    Args:
        quantity: Shares.
        board_lot: Shares per board lot, or ``None`` for single units.

    Returns:
        BOARD for whole lots (or no board lot), ODD below one lot and MIXED
        for more than one lot that is not a whole number of lots.
    """
    if board_lot is None or board_lot <= decimals.ZERO:
        return LotKind.BOARD
    if decimals.is_multiple(quantity, board_lot):
        return LotKind.BOARD
    if quantity < board_lot:
        return LotKind.ODD
    return LotKind.MIXED


# ---------------------------------------------------------------------------
# Sessions and settlement
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SessionState:
    """The market state at one instant.

    Attributes:
        open: Whether a session is in progress.
        current: The session in progress, if any.
        next_session: The next session to open, if known.
    """

    open: bool
    current: market.TradingSession | None
    next_session: market.TradingSession | None


def session_state(
    sessions: Sequence[market.TradingSession], now_ms: int
) -> SessionState:
    """Finds the session in progress and the next one.

    Args:
        sessions: Sessions around ``now_ms`` (any order).
        now_ms: Current time.

    Returns:
        State; ``next_session`` is ``None`` when none is known.
    """
    ordered = sorted(sessions, key=lambda s: s.open_ms)
    current = next((s for s in ordered if s.contains(now_ms)), None)
    upcoming = next((s for s in ordered if s.open_ms > now_ms), None)
    return SessionState(current is not None, current, upcoming)


def day_order_expiry(
    sessions: Sequence[market.TradingSession], accepted_ms: int
) -> int | None:
    """Returns when a DAY order accepted at a time expires.

    Args:
        sessions: Sessions around the acceptance time.
        accepted_ms: Acceptance time.

    Returns:
        Close of the session in progress, else of the next session;
        ``None`` when unknown.
    """
    state = session_state(sessions, accepted_ms)
    target = state.current or state.next_session
    return None if target is None else target.close_ms


def settlement_ms(
    sessions: Sequence[market.TradingSession], trade_ms: int, days: int
) -> int | None:
    """Returns when a trade settles: the open of the T+``days`` session.

    Args:
        sessions: Sessions covering the trade and the following days.
        trade_ms: Trade time.
        days: Settlement cycle in trading days.

    Returns:
        Settlement time, or ``None`` when the sessions do not reach it.
    """
    ordered = sorted(sessions, key=lambda s: s.open_ms)
    started = [i for i, s in enumerate(ordered) if s.open_ms <= trade_ms]
    if not started:
        return None
    target = started[-1] + days
    if target >= len(ordered):
        return None
    return ordered[target].open_ms


def fallback_settlement_ms(trade_ms: int, days: int) -> int:
    """Approximates settlement by skipping weekends (no holiday data).

    Weekdays are counted by UTC date.

    Args:
        trade_ms: Trade time.
        days: Settlement cycle in business days.

    Returns:
        Trade time plus whole days until ``days`` weekdays have passed.
    """
    result = trade_ms
    remaining = days
    while remaining > 0:
        result += _DAY_MS
        weekday = (result // _DAY_MS + 3) % 7  # 1970-01-01 was a Thursday.
        if weekday < 5:
            remaining -= 1
    return result


# ---------------------------------------------------------------------------
# Cash
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CashBalance:
    """The cash buying power of a securities (cash) account.

    Attributes:
        currency: Currency of the amounts.
        available: Cash available for new purchases after open orders.
        settled: Settled cash, if known.
        unsettled: Net unsettled amount (negative = payable), if known.
        source: Where the figures come from: the adapter name for broker
            figures, ``joseph`` when computed locally.
    """

    currency: str
    available: decimal.Decimal
    settled: decimal.Decimal | None = None
    unsettled: decimal.Decimal | None = None
    source: str = "joseph"


def is_taiwan(venue: str) -> bool:
    """Checks whether a venue is a Taiwan exchange (TWSE or TPEx).

    Args:
        venue: Venue code.

    Returns:
        True for ``twse`` and ``tpex``.
    """
    return venue in TAIWAN_VENUES


# ---------------------------------------------------------------------------
# Taiwan securities transaction tax (證券交易稅)
# ---------------------------------------------------------------------------
#
# Source: 證券交易稅條例 as last amended 2025-01-02 (民國114年1月2日) in the
# Ministry of Finance law database, read 2026-11-23:
# https://law-out.mof.gov.tw/LawContent.aspx?id=FL006079
# A bill extending the bond ETF suspension to 2036-12-31 and adding active
# bond ETFs from 2027-01-01 passed the Executive Yuan on 2026-10-01:
# https://www.ey.gov.tw/Page/9277F759E41CCD91/4d7b8a90-f834-4035-ab54-7728b91f243b
# and the Legislative Yuan Finance Committee's first review on 2026-11-11:
# https://www.cna.com.tw/news/afe/202611110159.aspx
# It is not law as of 2026-11-23 (no third reading). Only enacted windows
# are modelled: once the bill is promulgated, move the end of
# BOND_ETF_TAX_SUSPENSION and add active bond ETFs.

TAIWAN_STOCK_TAX_RATE = decimal.Decimal("0.003")
"""Sale tax on shares: 證券交易稅條例 §2(1), 千分之三 (0.3%)."""

TAIWAN_ETF_TAX_RATE = decimal.Decimal("0.001")
"""Sale tax on ETF units: §2(2), 千分之一 (0.1%).

ETF beneficiary certificates are "其他經政府核准之有價證券".
"""

TAIWAN_DAY_TRADE_TAX_RATE = decimal.Decimal("0.0015")
"""Sale tax of a same-day round trip in shares: §2-2, 千分之一點五 (0.15%).

Applies when the same account at the same broker buys with cash and sells
the same quantity of the same TWSE or TPEx stock on one trading day
(現股當日沖銷); ETFs are not covered.
"""

TAIPEI = datetime.timezone(datetime.timedelta(hours=8), "Asia/Taipei")
"""Taiwan time (UTC+8, no daylight saving): tax rules go by trade date."""

_NOT_INDEX_BOND_ETF = frozenset("LMRSUVAD")
"""Sixth code characters of ETFs outside the §2-1 bond ETF suspension.

From 中華民國證券市場編碼原則 (Financial Supervisory Commission,
2020-05-01):
https://www.twse.com.tw/staticFiles/announcement/announcement/1090007509-2.pdf
Leveraged ETFs end in L (M in foreign currency) and inverse ETFs in R (S),
leveraged and inverse bond ETFs included, which §2-1 excludes; futures ETFs
(U, V) are futures trust funds, not the securities investment trust funds
§2-1 names. Active ETFs end in A, active bond ETFs in D (e.g. 00983D,
listed on TWSE as 主動式ETF); they are not index funds (指數股票型基金),
so the suspension does not cover them (the pending bill would, from
2027-01-01).
"""


@dataclasses.dataclass(frozen=True, slots=True)
class TaxWindow:
    """A range of Taipei trade dates, both inclusive, for a tax rule.

    Attributes:
        first: First trade date.
        last: Last trade date.
    """

    first: datetime.date
    last: datetime.date

    def contains(self, day: datetime.date) -> bool:
        """Checks whether a trade date falls inside the window.

        Args:
            day: Taipei trade date.

        Returns:
            True when ``first <= day <= last``.
        """
        return self.first <= day <= self.last


BOND_ETF_TAX_SUSPENSION = TaxWindow(
    datetime.date(2017, 1, 1), datetime.date(2026, 12, 31)
)
"""§2-1(2): no tax on bond ETF sales, 2017-01-01 through 2026-12-31.

The tax on listed and OTC index ETFs investing mainly in bonds is
suspended "自中華民國一百零六年一月一日起至一百十五年十二月三十一日止";
leveraged and inverse bond ETFs are excluded. Outside the window bond ETFs
pay the 0.1% ETF rate.
"""

DAY_TRADE_TAX_REDUCTION = TaxWindow(
    datetime.date(2017, 4, 28), datetime.date(2027, 12, 31)
)
"""§2-2: the 0.15% day-trade rate for brokered trades, through 2027-12-31.

"自中華民國一百零六年四月二十八日起至一百十六年十二月三十一日止" (the
Legislative Yuan extended it on 2024-12-31; amendment of 2025-01-02).
"""


class TaiwanTaxClass(enum.StrEnum):
    """Which securities transaction tax a TWSE/TPEx sale pays."""

    STOCK = "stock"
    ETF = "etf"
    BOND_ETF = "bond_etf"


def taipei_date(ts_ms: int) -> datetime.date:
    """Returns the Taipei calendar date of a time.

    Args:
        ts_ms: UTC epoch milliseconds.

    Returns:
        Taipei date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, TAIPEI).date()


def taiwan_tax_class(
    instrument_id: str, asset_class: enums.AssetClass | None
) -> TaiwanTaxClass:
    """Classifies a TWSE/TPEx listing for the transaction tax.

    Bond ETFs are ETFs the market engine classifies as ``BOND``, less the
    funds §2-1 excludes by their code (see ``_NOT_INDEX_BOND_ETF``).

    Args:
        instrument_id: Canonical id such as ``tpex:etf:00679B``.
        asset_class: Asset class from the market engine, ``None`` or
            ``UNKNOWN`` when not known (charged as a plain ETF).

    Returns:
        Tax class.
    """
    ref = parse_instrument_id(instrument_id)
    if ref.market_type != "etf":
        return TaiwanTaxClass.STOCK
    if (
        asset_class is enums.AssetClass.BOND
        and ref.symbol[5:6] not in _NOT_INDEX_BOND_ETF
    ):
        return TaiwanTaxClass.BOND_ETF
    return TaiwanTaxClass.ETF


def taiwan_tax_rate(
    tax_class: TaiwanTaxClass, trade_ms: int, *, day_trade: bool = False
) -> decimal.Decimal:
    """Returns the securities transaction tax rate of a sale.

    Args:
        tax_class: Tax class of the listing.
        trade_ms: Trade time; the Taipei trade date selects the rule.
        day_trade: The sale closes a same-day round trip (現股當沖). The
            engine does not set it: whether a sale qualifies depends on
            the broker's day-trading agreement with the investor and on the
            whole day's buys and sells in the account (including trades
            outside Joseph), which are only netted after the close, so
            estimates charge the full 0.3% and the broker's statement
            decides.

    Returns:
        Rate on the sale price.
    """
    day = taipei_date(trade_ms)
    if tax_class is TaiwanTaxClass.BOND_ETF:
        if BOND_ETF_TAX_SUSPENSION.contains(day):
            return decimals.ZERO
        return TAIWAN_ETF_TAX_RATE
    if tax_class is TaiwanTaxClass.ETF:
        return TAIWAN_ETF_TAX_RATE
    if day_trade and DAY_TRADE_TAX_REDUCTION.contains(day):
        return TAIWAN_DAY_TRADE_TAX_RATE
    return TAIWAN_STOCK_TAX_RATE


def taiwan_fee(
    side: enums.OrderSide,
    notional: decimal.Decimal,
    tax_class: TaiwanTaxClass,
    trade_ms: int,
    *,
    day_trade: bool = False,
) -> decimal.Decimal:
    """Estimates Taiwan brokerage fee plus transaction tax for one fill.

    TWSE and TPEx listings pay the same statutory rates: the maximum
    commission (brokers discount it) and the sale tax of
    ``taiwan_tax_rate``; each part is floored to whole TWD. Minimum
    commissions are not modelled.

    Args:
        side: Fill side (tax applies to sales).
        notional: Shares times price in TWD.
        tax_class: Tax class of the listing (``taiwan_tax_class``).
        trade_ms: Trade time.
        day_trade: The sale closes a same-day round trip (see
            ``taiwan_tax_rate``).

    Returns:
        Estimated charge in TWD.
    """
    brokerage = decimals.floor_to_step(
        notional * TAIWAN_BROKERAGE_RATE, decimals.ONE
    )
    tax = decimals.ZERO
    if side is enums.OrderSide.SELL:
        rate = taiwan_tax_rate(tax_class, trade_ms, day_trade=day_trade)
        tax = decimals.floor_to_step(notional * rate, decimals.ONE)
    return brokerage + tax
