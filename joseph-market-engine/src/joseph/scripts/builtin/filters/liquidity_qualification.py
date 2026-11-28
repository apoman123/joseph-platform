# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Liquidity qualification: strict turnover, depth and funding gates.

An instrument matches only when it is liquid enough to enter and leave
without moving the price: enough turnover, a tight spread and little
slippage for an order of about USD 50K (and, for perpetuals, enough open
interest behind the turnover). Gates use **strict** comparators: a value
equal to its threshold fails.

Perpetual futures (crypto venues and DEMO perps):

========================  ===================  ===============  ======
Gate                      Crypto / RWA         Equity perp      Strict
========================  ===================  ===============  ======
24h quote turnover        > USD 50,000,000     > USD 30,000,000  ``>``
Open interest (USD)       > USD 10,000,000     > USD 10,000,000  ``>``
24h turnover / OI         > 0.20               > 0.20            ``>``
Best bid/ask spread       < 0.10 %             < 0.10 %          ``<``
USD 50K worst-side slip   < 0.25 %             < 0.25 %          ``<``
========================  ===================  ===============  ======

Exchange-listed securities (stocks and ETFs on ``twse``, ``tpex``,
``nasdaq``, ``nyse``, ``cboe``, and DEMO stocks) have no funding or open
interest, so those gates do not apply. Turnover is the latest session's,
in the listing currency; slippage walks the book for about USD 50K (TWD
1.5M on TWSE and TPEx, where one tick can be 0.5 % of the price):

====================  ==========  ===========  ========================
Gate                  US equity   US bond ETF  TWSE/TPEx equity, bond
====================  ==========  ===========  ========================
Session turnover      > USD 20M   > USD 10M    > TWD 300M, > TWD 100M
Best bid/ask spread   < 0.10 %    < 0.10 %     < 0.60 %
Worst-side slippage   < 0.25 %    < 0.25 %     < 1.00 %
====================  ==========  ===========  ========================

Unknown asset classes use the stricter crypto set for perpetuals. Rate
series (``treasury:rate:*``) are not tradable and never match; on Binance
only USDT-quoted contracts are eligible (turnover is compared in USD).

Funding is normalized to 8 hours (``rate * 100 * 8 / interval_hours``);
``|x| >= 0.10`` is a watch level and ``|x| >= 0.30`` high risk.

Decision:

* no match if any known gate fails, the instrument is not trading, or
  funding is high risk while ``reject_high_funding_risk`` is set;
* otherwise unavailable (``joseph_script.Unavailable``) if any gate input
  or, where funding applies, the funding rate is missing or older than
  ``max_data_age_s`` (never treated as zero or as a pass);
* otherwise a match (watch-level funding does not block).
"""

from __future__ import annotations

import decimal
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any

import joseph_script

if TYPE_CHECKING:
    from joseph.scripts import worker

D = decimal.Decimal
"""Exact decimal arithmetic for gate comparisons."""

QUALIFIED = "QUALIFIED"
"""Every gate passes."""
REJECTED = "REJECTED"
"""A known gate fails."""
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
"""No gate fails but an input is missing or stale."""

EXCHANGE_VENUES = frozenset({"twse", "tpex", "nasdaq", "nyse", "cboe"})
"""Venues that are securities exchanges."""

FUNDING_WATCH_PCT = D("0.10")
"""Absolute 8h funding in percent at or above which funding is watched."""
FUNDING_HIGH_RISK_PCT = D("0.30")
"""Absolute 8h funding in percent at or above which funding is high risk."""


def thresholds(
    turnover: str,
    *,
    open_interest: str | None = "10000000",
    ratio: str | None = "0.20",
    spread: str = "0.10",
    slippage: str = "0.25",
    currency: str = "USD",
    notional: str = "50000",
    funding: bool = True,
) -> dict[str, Any]:
    """Builds one asset class's gate thresholds.

    Args:
        turnover: Minimum (exclusive) 24h or session quote turnover.
        open_interest: Minimum (exclusive) USD open interest; None skips it.
        ratio: Minimum (exclusive) turnover / open interest; None skips it.
        spread: Maximum (exclusive) spread in percent.
        slippage: Maximum (exclusive) worst-side slippage in percent.
        currency: Turnover and book-walk currency.
        notional: Book-walk order size in that currency.
        funding: Whether funding is assessed.

    Returns:
        Threshold mapping with decimal values.
    """
    return {
        "turnover": D(turnover),
        "open_interest": None if open_interest is None else D(open_interest),
        "ratio": None if ratio is None else D(ratio),
        "spread": D(spread),
        "slippage": D(slippage),
        "currency": currency,
        "notional": D(notional),
        "funding": funding,
    }


CRYPTO = thresholds("50000000")
"""Crypto and RWA perpetuals, and perpetuals of unknown asset class."""
EQUITY_PERP = thresholds("30000000")
"""Equity perpetuals on crypto venues."""
US_EQUITY = thresholds(
    "20000000", open_interest=None, ratio=None, funding=False
)
"""Stocks and equity ETFs on Nasdaq, NYSE and Cboe (and DEMO stocks)."""
US_BOND = thresholds("10000000", open_interest=None, ratio=None, funding=False)
"""Bond ETFs on Nasdaq, NYSE and Cboe."""
TW_EQUITY = thresholds(
    "300000000",
    open_interest=None,
    ratio=None,
    spread="0.60",
    slippage="1.00",
    currency="TWD",
    notional="1500000",
    funding=False,
)
"""Stocks and equity ETFs on TWSE and TPEx (amounts in TWD)."""
TW_BOND = {**TW_EQUITY, "turnover": D("100000000")}
"""Bond ETFs on TWSE and TPEx (amounts in TWD)."""


def parts(instrument_id: str) -> tuple[str, str, str]:
    """Splits a canonical instrument id.

    Args:
        instrument_id: Text such as ``binance:perp:BTCUSDT``.

    Returns:
        Venue, market type and venue market id.
    """
    venue, market_type, symbol = [*instrument_id.split(":", 2), "", ""][:3]
    return venue, market_type, symbol


def is_listed(instrument_id: str) -> bool:
    """Tells an exchange-listed security from a derivative.

    Args:
        instrument_id: Canonical instrument id.

    Returns:
        True for stocks and ETFs and for exchange venues.
    """
    venue, market_type, _ = parts(instrument_id)
    return market_type in {"stock", "etf"} or venue in EXCHANGE_VENUES


def limits_for(instrument_id: str, asset_class: str) -> dict[str, Any]:
    """Selects the thresholds of an instrument.

    Args:
        instrument_id: Canonical instrument id.
        asset_class: CRYPTO, RWA, EQUITY, BOND or UNKNOWN.

    Returns:
        Threshold mapping.
    """
    venue = parts(instrument_id)[0]
    if is_listed(instrument_id):
        bond = asset_class == "BOND"
        if venue in {"twse", "tpex"}:
            return TW_BOND if bond else TW_EQUITY
        return US_BOND if bond else US_EQUITY
    if asset_class == "EQUITY":
        return EQUITY_PERP
    return CRYPTO


def decimal_or_none(value: Any) -> decimal.Decimal | None:
    """Parses a contract decimal string.

    Args:
        value: Decimal string, number or None.

    Returns:
        The finite decimal, or None when absent or malformed.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = D(str(value))
    except decimal.InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def walk(
    levels: list[Any], notional: decimal.Decimal
) -> decimal.Decimal | None:
    """Walks book levels until a quote notional is filled.

    Args:
        levels: ``[price, quantity]`` pairs, best first.
        notional: Quote notional to fill.

    Returns:
        Volume-weighted average fill price, or None if depth runs out.
    """
    remaining = notional
    filled = D(0)
    spent = D(0)
    for level in levels:
        price = decimal_or_none(level[0])
        quantity = decimal_or_none(level[1])
        if price is None or quantity is None or price <= 0 or quantity <= 0:
            continue
        take = min(price * quantity, remaining)
        filled += take / price
        spent += take
        remaining -= take
        if remaining <= 0:
            return spent / filled
    return None


def slippage_pct(
    book: Mapping[str, Any], notional: decimal.Decimal
) -> decimal.Decimal | None:
    """Computes worst-side book-walk slippage from the mid price.

    Args:
        book: Contract order book (bids descending, asks ascending).
        notional: Order size in quote currency.

    Returns:
        The larger of buy and sell slippage in percent, or None when the
        book is stale, one-sided, crossed or too shallow.
    """
    bids = list(book.get("bids") or [])
    asks = list(book.get("asks") or [])
    if book.get("stale") or not bids or not asks:
        return None
    best_bid = decimal_or_none(bids[0][0])
    best_ask = decimal_or_none(asks[0][0])
    if best_bid is None or best_ask is None or best_bid >= best_ask:
        return None
    mid = (best_bid + best_ask) / 2
    buy = walk(asks, notional)
    sell = walk(bids, notional)
    if buy is None or sell is None:
        return None
    return max((buy - mid) / mid * 100, (mid - sell) / mid * 100)


def spread_pct(bid: Any, ask: Any) -> decimal.Decimal | None:
    """Computes the spread in percent of the mid.

    Args:
        bid: Best bid decimal string.
        ask: Best ask decimal string.

    Returns:
        ``(ask - bid) / mid * 100``, or None without both sides or a
        positive mid.
    """
    best_bid = decimal_or_none(bid)
    best_ask = decimal_or_none(ask)
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / 2
    if mid <= 0:
        return None
    return (best_ask - best_bid) / mid * 100


def normalized_funding(funding: Mapping[str, Any]) -> decimal.Decimal | None:
    """Normalizes a funding observation to percent per 8 hours.

    Args:
        funding: Contract ``Funding`` with rate and interval_hours.

    Returns:
        ``rate * 100 * 8 / interval_hours``, or None without a positive
        interval or a rate.
    """
    rate = decimal_or_none(funding.get("rate"))
    interval = decimal_or_none(funding.get("interval_hours"))
    if rate is None or interval is None or interval <= 0:
        return None
    return rate * 100 * 8 / interval


def gate(
    name: str,
    *,
    observation: tuple[decimal.Decimal | None, int | None],
    threshold: decimal.Decimal,
    comparator: str,
    now_ms: int,
    max_age_ms: int,
) -> tuple[bool | None, str]:
    """Evaluates one strict gate.

    Args:
        name: Gate name for the reason.
        observation: Value and observation time (None when unknown, which
            counts as fresh).
        threshold: Gate threshold.
        comparator: "gt" or "lt".
        now_ms: Evaluation time.
        max_age_ms: Age above which a value is stale.

    Returns:
        True or False, or None for a missing or stale value, and a reason.
    """
    value, as_of_ms = observation
    if value is None:
        return None, f"{name} is unavailable"
    if as_of_ms is not None and now_ms - as_of_ms > max_age_ms:
        return None, f"{name} is stale"
    passed = value > threshold if comparator == "gt" else value < threshold
    symbol = ">" if comparator == "gt" else "<"
    verdict = "passes" if passed else "fails"
    try:
        shown = value.quantize(D("0.000001")).normalize()
    except decimal.InvalidOperation:
        shown = value
    limit = threshold.normalize()
    return passed, f"{name} {shown:f} {verdict} {symbol} {limit:f}"


def qualify(
    snapshot: Mapping[str, Any],
    instrument: Mapping[str, Any],
    *,
    now_ms: int,
    max_age_ms: int = 300_000,
    reject_high_funding_risk: bool = True,
) -> tuple[str, list[str]]:
    """Applies the qualification policy to one instrument.

    Args:
        snapshot: Contract snapshot, with ``order_book`` when loaded.
        instrument: Contract instrument (instrument_id, asset_class,
            status, key).
        now_ms: Evaluation time in UTC epoch milliseconds.
        max_age_ms: Inputs older than this count as missing.
        reject_high_funding_risk: Whether high-risk funding rejects.

    Returns:
        QUALIFIED, REJECTED or INSUFFICIENT_DATA and the reasons of every
        gate that did not pass.
    """
    instrument_id = str(instrument.get("instrument_id", ""))
    limits = limits_for(
        instrument_id, str(instrument.get("asset_class") or "UNKNOWN")
    )
    as_of = snapshot.get("as_of_ms")

    def fresh(observation: tuple[decimal.Decimal | None, int | None]) -> bool:
        """Tells whether an observation is present and fresh.

        Args:
            observation: Value and time.

        Returns:
            True when usable.
        """
        value, when = observation
        return value is not None and (
            when is None or now_ms - when <= max_age_ms
        )

    turnover = (decimal_or_none(snapshot.get("turnover_24h_quote")), as_of)
    interest = snapshot.get("open_interest") or {}
    open_interest = (
        decimal_or_none(interest.get("notional_usd")),
        interest.get("as_of_ms"),
    )
    ratio_value = None
    if (
        fresh(turnover)
        and fresh(open_interest)
        and open_interest[0] is not None
        and open_interest[0] > 0
        and turnover[0] is not None
    ):
        ratio_value = turnover[0] / open_interest[0]
    times = [t for t in (turnover[1], open_interest[1]) if t is not None]
    ratio = (ratio_value, max(times) if times else None)
    spread = (
        spread_pct(snapshot.get("best_bid"), snapshot.get("best_ask")),
        as_of,
    )
    slippage: tuple[decimal.Decimal | None, int | None] = (None, None)
    book = snapshot.get("order_book")
    if isinstance(book, Mapping) and not book.get("stale"):
        received = book.get("received_at_ms")
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if bids and asks:
            spread = (spread_pct(bids[0][0], asks[0][0]), received)
        slippage = (slippage_pct(book, limits["notional"]), received)
    checks = [
        (
            "TURNOVER_24H",
            turnover,
            limits["turnover"],
            "gt",
        )
    ]
    if limits["open_interest"] is not None:
        checks.append(
            ("OPEN_INTEREST_USD", open_interest, limits["open_interest"], "gt")
        )
    if limits["ratio"] is not None:
        checks.append(("TURNOVER_OI_RATIO", ratio, limits["ratio"], "gt"))
    checks.append(("SPREAD_PCT", spread, limits["spread"], "lt"))
    checks.append(("SLIPPAGE_50K_PCT", slippage, limits["slippage"], "lt"))
    results = [
        gate(
            name,
            observation=observation,
            threshold=threshold,
            comparator=comparator,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
        )
        for name, observation, threshold, comparator in checks
    ]
    reasons = [reason for passed, reason in results if passed is not True]
    failed = any(passed is False for passed, _ in results)
    missing = any(passed is None for passed, _ in results)
    if limits["funding"]:
        funding = snapshot.get("funding") or {}
        observation = (normalized_funding(funding), funding.get("as_of_ms"))
        if not fresh(observation) or observation[0] is None:
            missing = True
            reasons.append(
                "funding is unavailable; risk classification unknown"
            )
        elif (
            abs(observation[0]) >= FUNDING_HIGH_RISK_PCT
            and reject_high_funding_risk
        ):
            failed = True
            reasons.append("funding is HIGH_RISK under the default policy")
    if instrument.get("status", "TRADING") != "TRADING":
        failed = True
        reasons.append("instrument is not trading")
    if failed:
        return REJECTED, reasons
    if missing:
        return INSUFFICIENT_DATA, reasons
    return QUALIFIED, reasons


def eligible(instrument: Mapping[str, Any]) -> bool:
    """Applies the candidate rules that precede the gates.

    Args:
        instrument: Contract instrument.

    Returns:
        False for rate series and for Binance contracts not quoted in USDT.
    """
    venue, market_type, _ = parts(str(instrument.get("instrument_id", "")))
    if market_type == "rate" or venue == "treasury":
        return False
    quote = (instrument.get("key") or {}).get("quote")
    return venue != "binance" or quote in {None, "USDT"}


class LiquidityQualification(joseph_script.Filter):
    """Matches instruments that pass every liquidity gate.

    Attributes:
        METRICS: The order book, open interest and funding it reads.
    """

    ID = "liquidity-qualification"
    TITLE = "Liquidity qualification"
    SHORT_TITLE = "Liquidity"
    DESCRIPTION = (
        "Strict turnover, open-interest, spread, slippage and funding gates "
        "per asset class; missing or stale data is unavailable, never a "
        "match."
    )
    CATEGORY = "Liquidity"
    METRICS = ("order_book", "open_interest", "funding")
    INPUTS = {
        "max_data_age_s": joseph_script.Input(
            300,
            title="Maximum data age (s)",
            minimum=1,
            maximum=3600,
            step=1,
            tooltip="Older inputs count as missing.",
        ),
        "reject_high_funding_risk": joseph_script.Input(
            True,
            title="Reject high funding risk",
            tooltip="|8h funding| at or above 0.30 % excludes a perpetual.",
        ),
    }

    def matches(self, ctx: worker.Context) -> bool:
        """Qualifies the candidate.

        Args:
            ctx: Filter context with the snapshot and instrument.

        Returns:
            True when qualified, False when rejected or ineligible.

        Raises:
            joseph_script.Unavailable: When a gate input is missing or stale.
        """
        instrument = {"instrument_id": ctx.instrument_id, **ctx.instrument}
        if not eligible(instrument):
            return False
        now = ctx.snapshot.get("evaluated_at_ms")
        if not isinstance(now, int):
            raise joseph_script.Unavailable("evaluation time is unavailable")
        status, reasons = qualify(
            ctx.snapshot,
            instrument,
            now_ms=now,
            max_age_ms=int(ctx.input("max_data_age_s")) * 1000,
            reject_high_funding_risk=bool(
                ctx.input("reject_high_funding_risk")
            ),
        )
        if status == INSUFFICIENT_DATA:
            raise joseph_script.Unavailable("; ".join(reasons)[:400])
        return status == QUALIFIED
