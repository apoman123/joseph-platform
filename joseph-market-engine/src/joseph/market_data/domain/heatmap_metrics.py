# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Pure heatmap metric rules: changes, performance, volume and fund classes.

Every function returns ``None`` (or omits a key) when its inputs do not
determine the value; nothing is estimated from unrelated fields.
"""

from __future__ import annotations

import bisect
import dataclasses
import datetime
import decimal
import re
import zoneinfo
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import sessions

_PERCENT = decimal.Decimal("0.0001")
_PERIODS = (
    ("perf_1w", 7),
    ("perf_1m", 30),
    ("perf_3m", 91),
    ("perf_6m", 182),
    ("perf_1y", 365),
)
"""Performance metrics and their calendar-day look-back."""

RELATIVE_VOLUME_SESSIONS = 10
"""Sessions averaged by relative volume (today against the 10 before)."""

WEEK_SESSIONS = 5
"""Sessions summed by the 1W volume and turnover (trailing week)."""

MONTH_SESSIONS = 21
"""Sessions summed by the 1M volume and turnover (trailing month)."""


@dataclasses.dataclass(frozen=True)
class DailyBar:
    """One session's OHLCV.

    Attributes:
        day: Exchange-local session date.
        open: Opening price.
        high: High price.
        low: Low price.
        close: Closing (or latest) price.
        volume: Traded shares.
        vwap: Volume-weighted average price, None when unpublished.
    """

    day: datetime.date
    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    volume: decimal.Decimal
    vwap: decimal.Decimal | None = None

    @property
    def turnover(self) -> decimal.Decimal:
        """Traded value: volume times VWAP, else times the close."""
        return self.volume * (
            self.vwap if self.vwap is not None else self.close
        )


@dataclasses.dataclass(frozen=True)
class SessionQuote:
    """Latest trade with the current and previous daily bars.

    Attributes:
        last: Latest trade price, or None.
        last_ms: Latest trade time, UTC epoch milliseconds, or None.
        today: Current (or latest) session bar, or None.
        previous: Previous session bar, or None.
    """

    last: decimal.Decimal | None
    last_ms: int | None
    today: DailyBar | None
    previous: DailyBar | None


def pct(
    new: decimal.Decimal | None, old: decimal.Decimal | None
) -> decimal.Decimal | None:
    """Returns the percentage change from ``old`` to ``new``.

    Args:
        new: Later value.
        old: Earlier value.

    Returns:
        ``(new - old) / old * 100`` rounded to 4 places, or None when a value
        is missing or ``old`` is not positive.
    """
    if new is None or old is None or old <= 0:
        return None
    return ((new - old) / old * decimals.HUNDRED).quantize(_PERCENT)


def day_metrics(
    *,
    last: decimal.Decimal | None,
    previous_close: decimal.Decimal | None,
    open_: decimal.Decimal | None = None,
    high: decimal.Decimal | None = None,
    low: decimal.Decimal | None = None,
) -> dict[str, decimal.Decimal]:
    """Computes the one-day colour metrics.

    Args:
        last: Latest price.
        previous_close: Previous session close.
        open_: Today's open.
        high: Today's high.
        low: Today's low.

    Returns:
        ``change_1d``, ``gap`` ((open - previous close) / previous close)
        and ``volatility_1d`` ((high - low) / low), each only when its
        inputs are present.
    """
    out: dict[str, decimal.Decimal] = {}
    change = pct(last, previous_close)
    if change is not None:
        out["change_1d"] = change
    gap = pct(open_, previous_close)
    if gap is not None:
        out["gap"] = gap
    if high is not None and low is not None and high >= low:
        volatility = pct(high, low)
        if volatility is not None:
            out["volatility_1d"] = volatility
    return out


def _close_on_or_before(
    bars: Sequence[DailyBar], days: Sequence[datetime.date], day: datetime.date
) -> decimal.Decimal | None:
    """Finds the last close on or before a date.

    Args:
        bars: Bars sorted by date.
        days: Their dates.
        day: Reference date.

    Returns:
        The close, or None when the history starts after ``day``.
    """
    index = bisect.bisect_right(days, day) - 1
    return bars[index].close if index >= 0 else None


def history_metrics(
    bars: Sequence[DailyBar],
    *,
    last: decimal.Decimal | None,
    today: datetime.date,
) -> dict[str, decimal.Decimal]:
    """Computes performance, relative volume and multi-session sizes.

    Args:
        bars: Daily bars sorted by date; a bar dated ``today`` is the
            current session.
        last: Latest price (performance is measured to it).
        today: Exchange-local date of the latest price.

    Returns:
        ``perf_1w`` ... ``perf_1y`` and ``perf_ytd`` (close on or before the
        look-back date, which must be covered by the history), ``volume_1w``,
        ``volume_1m``, ``turnover_1w``, ``turnover_1m`` (trailing 5 and 21
        sessions, only with that many sessions) and ``relative_volume``
        (today's volume over the average of the 10 previous sessions).
    """
    out: dict[str, decimal.Decimal] = {}
    if not bars:
        return out
    ordered = sorted(bars, key=lambda bar: bar.day)
    days = [bar.day for bar in ordered]
    first = days[0]
    references = [
        (name, today - datetime.timedelta(days=span)) for name, span in _PERIODS
    ]
    references.append(("perf_ytd", datetime.date(today.year - 1, 12, 31)))
    for name, reference in references:
        if reference < first:
            continue
        value = pct(last, _close_on_or_before(ordered, days, reference))
        if value is not None:
            out[name] = value
    for suffix, count in (("1w", WEEK_SESSIONS), ("1m", MONTH_SESSIONS)):
        window = ordered[-count:]
        if len(window) == count:
            out[f"volume_{suffix}"] = sum(
                (bar.volume for bar in window), decimals.ZERO
            )
            out[f"turnover_{suffix}"] = sum(
                (bar.turnover for bar in window), decimals.ZERO
            ).quantize(decimals.ONE)
    current = ordered[-1]
    before = [bar for bar in ordered if bar.day < current.day][
        -RELATIVE_VOLUME_SESSIONS:
    ]
    if current.day == today and len(before) == RELATIVE_VOLUME_SESSIONS:
        average = sum((bar.volume for bar in before), decimals.ZERO) / len(
            before
        )
        if average > 0:
            out["relative_volume"] = (current.volume / average).quantize(
                _PERCENT
            )
    return out


_FUND_CLASSES = (
    (
        "Crypto",
        re.compile(
            r"bitcoin|\bether(eum)?\b|crypto|solana|\bxrp\b"
            r"|digital asset|比特幣|以太",
            re.I,
        ),
    ),
    (
        "Fixed income",
        re.compile(
            r"bond|treasur|t-bill|\bbill\b|muni|aggregate|corporate|credit"
            r"|high yield|fixed income|mortgage|\btips\b|floating rate"
            r"|債|公債|投等",
            re.I,
        ),
    ),
    (
        "Commodity",
        re.compile(
            r"gold|silver|\boil\b|crude|commodit|metals|copper|natural gas"
            r"|platinum|palladium|agricultur|黃金|白銀|原油|商品|期銅",
            re.I,
        ),
    ),
    (
        "Currency",
        re.compile(
            r"currency|\bdollar\b|\byen\b|\beuro\b|\byuan\b|\bfx\b|匯|貨幣",
            re.I,
        ),
    ),
    (
        "Multi-asset/Alternative",
        re.compile(
            r"allocation|multi-asset|balanced|hedge|alternative|volatility"
            r"|\bvix\b|managed futures|target date|多重資產|平衡",
            re.I,
        ),
    ),
)
"""Asset classes recognized in fund names, in precedence order."""


def classify_fund(name: str | None) -> str:
    """Classifies a fund by keywords of its name.

    Args:
        name: Fund name (English or Chinese).

    Returns:
        ``Crypto``, ``Fixed income``, ``Commodity``, ``Currency``,
        ``Multi-asset/Alternative`` or, without a keyword, ``Equity``.
    """
    text = name or ""
    for label, pattern in _FUND_CLASSES:
        if pattern.search(text):
            return label
    return "Equity"


def to_text(value: decimal.Decimal) -> str:
    """Formats a metric value as a contract decimal string.

    Args:
        value: Finite value.

    Returns:
        Plain decimal string without exponent or trailing zeros.
    """
    return decimals.format_decimal(value)


_NEW_YORK = zoneinfo.ZoneInfo("America/New_York")


def session_metrics(quote: SessionQuote) -> dict[str, decimal.Decimal]:
    """Computes the one-day and extended-hours metrics of a US quote.

    The one-day metrics compare the current regular-session bar with the
    previous one, so they stay regular-session values before the open and
    after the close. The pre-market change compares a latest trade before
    the open with the previous close; the post-market change compares a
    latest trade after the close with that session's close.

    Args:
        quote: Latest trade with the current and previous daily bars.

    Returns:
        ``change_1d``, ``gap``, ``volatility_1d``, ``volume_1d``,
        ``turnover_1d``, ``premarket_change`` and ``postmarket_change``
        where determined.
    """
    out: dict[str, decimal.Decimal] = {}
    today, previous = quote.today, quote.previous
    if today is not None:
        out.update(
            day_metrics(
                last=today.close,
                previous_close=previous.close if previous else None,
                open_=today.open,
                high=today.high,
                low=today.low,
            )
        )
        out["volume_1d"] = today.volume
        out["turnover_1d"] = today.turnover.quantize(decimals.ONE)
    if quote.last is None or quote.last_ms is None:
        return out
    moment = datetime.datetime.fromtimestamp(quote.last_ms / 1000, _NEW_YORK)
    session = sessions.XNYS.session(moment.date())
    if session is None:
        return out
    if quote.last_ms < session.open_ms:
        # Before the open the latest daily bar is the previous session's.
        reference = previous
        if today is not None and today.day < moment.date():
            reference = today
        value = pct(quote.last, reference.close if reference else None)
        if value is not None:
            out["premarket_change"] = value
    elif (
        quote.last_ms >= session.close_ms
        and today is not None
        and today.day == moment.date()
    ):
        value = pct(quote.last, today.close)
        if value is not None:
            out["postmarket_change"] = value
    return out
