"""Rules for exchange-listed securities and broker routing.

Pure functions shared by the Risk Gateway, the OMS and the paper
simulator:

* Routing: crypto accounts trade their own venue; broker accounts trade
  the exchanges their broker serves (``ibkr`` -> Nasdaq/NYSE, ``sinopac``
  and ``cathay`` -> TWSE); paper accounts trade in their own currency.
  Treasury rates are published series and are never tradable.
* Sessions from exchange calendars, DAY-order expiry and settlement days.
* Price-dependent tick ladders and daily price limits (TWSE +-10% from the
  reference price), board lots and intraday odd lots.
* Estimated TWSE brokerage fees and securities transaction tax.
"""

from __future__ import annotations

import dataclasses
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
    {enums.Venue.TWSE.value, enums.Venue.NASDAQ.value, enums.Venue.NYSE.value}
)
"""Securities exchanges reached through a broker."""

RATE_VENUES = frozenset({enums.Venue.TREASURY.value})
"""Publishers of rate series that are never tradable."""

SECURITY_TYPES = frozenset({"stock", "etf"})
"""Market types of exchange-listed cash securities."""

BROKER_EXCHANGES: Mapping[str, tuple[str, ...]] = {
    enums.Broker.IBKR.value: (enums.Venue.NASDAQ.value, enums.Venue.NYSE.value),
    enums.Broker.SINOPAC.value: (enums.Venue.TWSE.value,),
    enums.Broker.CATHAY.value: (enums.Venue.TWSE.value,),
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
    enums.Venue.NASDAQ.value: "USD",
    enums.Venue.NYSE.value: "USD",
}
"""Trading currency of each exchange's listings."""

SETTLEMENT_DAYS: Mapping[str, int] = {"XTAI": 2, "XNYS": 1}
"""Settlement cycle in trading days by calendar.

TWSE settles T+2. US equities settle T+1 since 2024-05-28 (SEC rule
15c6-1 as amended).
"""

DISPLAY_NAMES: Mapping[str, str] = {
    enums.Broker.IBKR.value: "Interactive Brokers",
    enums.Broker.SINOPAC.value: "SinoPac",
    enums.Broker.CATHAY.value: "Cathay Securities",
    enums.Venue.TWSE.value: "TWSE",
    enums.Venue.NASDAQ.value: "Nasdaq",
    enums.Venue.NYSE.value: "NYSE",
    enums.Venue.TREASURY.value: "US Treasury",
}
"""Human-facing names used in risk messages."""

TWSE_BROKERAGE_RATE = decimal.Decimal("0.001425")
"""Statutory maximum brokerage commission (0.1425%) before discounts."""

TWSE_STOCK_TAX_RATE = decimal.Decimal("0.003")
"""Securities transaction tax on stock sales (0.3%)."""

TWSE_ETF_TAX_RATE = decimal.Decimal("0.001")
"""Securities transaction tax on ETF sales (0.1%)."""

_DAY_MS = 86_400_000


def _name(code: str) -> str:
    """Returns the display name of a venue or broker code.

    Args:
        code: Venue or broker code.

    Returns:
        Display name, else the code.
    """
    return DISPLAY_NAMES.get(code, code)


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
        Parts.
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
        Exchange currency for securities, else the quote (``USD`` default).
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


def route(  # noqa: PLR0911
    account_venue: str,
    is_paper: bool,
    account_currency: str,
    instrument_id: str,
) -> Route:
    """Decides whether an account may trade an instrument.

    Args:
        account_venue: Account venue (``mock`` for paper accounts).
        is_paper: Whether the account is a paper account.
        account_currency: Account settlement currency.
        instrument_id: Canonical instrument id.

    Returns:
        Verdict with a clear explanation.
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
            names = " and ".join(_name(v) for v in served)
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
        Price on the tick grid.
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
    """Daily price limits of one session.

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

    TWSE rounds limit-up down and limit-down up to the tick that applies at
    the limit price.

    Args:
        reference: Reference price (previous close).
        instrument: Instrument metadata with ``price_limit_pct``.

    Returns:
        Limits, or ``None`` when the instrument has no daily limit.
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
    """Market state at one instant.

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

    Args:
        trade_ms: Trade time.
        days: Settlement cycle in business days.

    Returns:
        Trade time plus ``days`` weekdays.
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
    """Cash buying power of a securities (cash) account.

    Attributes:
        currency: Currency.
        available: Cash available for new purchases after open orders.
        settled: Settled cash, if known.
        unsettled: Net unsettled amount (negative = payable), if known.
        source: Where the figures come from.
    """

    currency: str
    available: decimal.Decimal
    settled: decimal.Decimal | None = None
    unsettled: decimal.Decimal | None = None
    source: str = "joseph"


def twse_fee(
    side: enums.OrderSide,
    notional: decimal.Decimal,
    market_type: str,
) -> decimal.Decimal:
    """Estimates TWSE brokerage fee plus transaction tax for one fill.

    Uses the statutory maximum commission (brokers discount it) and the
    sale tax (0.3% stocks, 0.1% ETFs); each part is floored to whole TWD.
    Minimum commissions are not modelled.

    Args:
        side: Fill side (tax applies to sales).
        notional: Shares times price in TWD.
        market_type: ``stock`` or ``etf``.

    Returns:
        Estimated charge in TWD.
    """
    brokerage = decimals.floor_to_step(
        notional * TWSE_BROKERAGE_RATE, decimals.ONE
    )
    tax = decimals.ZERO
    if side is enums.OrderSide.SELL:
        rate = (
            TWSE_ETF_TAX_RATE if market_type == "etf" else (TWSE_STOCK_TAX_RATE)
        )
        tax = decimals.floor_to_step(notional * rate, decimals.ONE)
    return brokerage + tax
