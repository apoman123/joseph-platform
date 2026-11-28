# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Heatmap metric rules: changes, performance, volume and fund classes."""

from __future__ import annotations

import datetime
import decimal

import pytest

from joseph.domain import sessions
from joseph.market_data.domain import heatmap_metrics as rules

D = decimal.Decimal


def _bar(
    day: datetime.date,
    close: str,
    volume: str = "100",
    *,
    open_: str | None = None,
    vwap: str | None = None,
) -> rules.DailyBar:
    """Builds a bar.

    Args:
        day: Session date.
        close: Close.
        volume: Volume.
        open_: Open, default the close.
        vwap: Optional VWAP.

    Returns:
        Bar with high/low around the close.
    """
    price = D(close)
    return rules.DailyBar(
        day,
        D(open_ or close),
        price + 1,
        price - 1,
        price,
        D(volume),
        D(vwap) if vwap else None,
    )


def test_pct_and_day_metrics() -> None:
    """Computes change, gap and range only from present inputs."""
    assert rules.pct(D(110), D(100)) == D("10.0000")
    assert rules.pct(D(1), D(0)) is None
    assert rules.pct(None, D(1)) is None
    values = rules.day_metrics(
        last=D(102),
        previous_close=D(100),
        open_=D(101),
        high=D(105),
        low=D(100),
    )
    assert values == {
        "change_1d": D("2.0000"),
        "gap": D("1.0000"),
        "volatility_1d": D("5.0000"),
    }
    assert rules.day_metrics(last=D(1), previous_close=None) == {}


def test_history_metrics_need_covering_history() -> None:
    """Measures performance only when the look-back date is covered."""
    today = datetime.date(2026, 11, 20)
    days = [today - datetime.timedelta(days=n) for n in range(400, -1, -1)]
    bars = [_bar(day, str(100 + n), "10") for n, day in enumerate(days)]
    values = rules.history_metrics(bars, last=D(500), today=today)
    reference = 100 + days.index(today - datetime.timedelta(days=7))
    assert values["perf_1w"] == rules.pct(D(500), D(reference))
    assert values["perf_ytd"] == rules.pct(
        D(500), D(100 + days.index(datetime.date(2025, 12, 31)))
    )
    assert values["volume_1w"] == D(50)
    assert values["volume_1m"] == D(210)
    assert values["relative_volume"] == D("1.0000")
    short = rules.history_metrics(bars[-10:], last=D(500), today=today)
    assert "perf_1m" not in short and "perf_1w" in short
    assert "volume_1m" not in short and "relative_volume" not in short
    assert rules.history_metrics([], last=D(1), today=today) == {}


def test_relative_volume_compares_today_with_ten_sessions() -> None:
    """Divides today's volume by the average of the previous ten."""
    today = datetime.date(2026, 11, 20)
    bars = [
        _bar(today - datetime.timedelta(days=11 - n), "10", "100")
        for n in range(11)
    ]
    bars[-1] = _bar(today, "10", "250")
    assert rules.history_metrics(bars, last=D(10), today=today)[
        "relative_volume"
    ] == D("2.5000")
    stale = rules.history_metrics(
        bars[:-1], last=D(10), today=today
    )  # no bar today
    assert "relative_volume" not in stale


def test_turnover_prefers_vwap() -> None:
    """Uses VWAP times volume, else the close."""
    day = datetime.date(2026, 11, 20)
    assert _bar(day, "10", "5", vwap="11").turnover == D(55)
    assert _bar(day, "10", "5").turnover == D(50)


def _ms(day: datetime.date, hour: int, minute: int = 0) -> int:
    """Returns a New York wall time in UTC milliseconds.

    Args:
        day: Date.
        hour: New York hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    session = sessions.XNYS.session(day)
    assert session is not None
    return session.open_ms + ((hour - 9) * 60 + minute - 30) * 60_000


@pytest.mark.parametrize(
    ("hour", "metric", "expected"),
    [
        (8, "premarket_change", D("5.0000")),
        (17, "postmarket_change", D("-1.0000")),
        (11, None, None),
    ],
)
def test_extended_hours_changes(
    hour: int, metric: str | None, expected: decimal.Decimal | None
) -> None:
    """Measures pre-market against the previous close, post against today's.

    Args:
        hour: New York hour of the latest trade.
        metric: Metric expected, or None during regular hours.
        expected: Expected value.
    """
    day = datetime.date(2026, 11, 20)
    previous = _bar(datetime.date(2026, 11, 19), "100")
    today = _bar(day, "100", open_="99")
    last = D(105) if hour < 9 else D(99)
    if hour < 9:
        # Before the open the latest daily bar is the previous session.
        quote = rules.SessionQuote(last, _ms(day, hour), previous, None)
    else:
        quote = rules.SessionQuote(last, _ms(day, hour), today, previous)
    values = rules.session_metrics(quote)
    extended = {"premarket_change", "postmarket_change"} & set(values)
    if metric is None:
        assert not extended
        assert values["change_1d"] == D("0.0000")
        assert values["gap"] == D("-1.0000")
    else:
        assert values[metric] == expected


def test_session_metrics_without_bars() -> None:
    """Reports nothing without bars or a trade."""
    assert (
        rules.session_metrics(rules.SessionQuote(None, None, None, None)) == {}
    )
    saturday = _ms(datetime.date(2026, 11, 20), 12) + 86_400_000
    assert (
        rules.session_metrics(rules.SessionQuote(D(1), saturday, None, None))
        == {}
    )


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("iShares 20+ Year Treasury Bond ETF", "Fixed income"),
        ("SPDR Gold Shares", "Commodity"),
        ("iShares Bitcoin Trust ETF", "Crypto"),
        ("Invesco CurrencyShares Japanese Yen Trust", "Currency"),
        ("iShares Core Growth Allocation ETF", "Multi-asset/Alternative"),
        ("SPDR S&P 500 ETF Trust", "Equity"),
        ("元大美債20年", "Fixed income"),
        ("期元大S&P黃金", "Commodity"),
        ("元大台灣50", "Equity"),
        (None, "Equity"),
    ],
)
def test_fund_classes(name: str | None, label: str) -> None:
    """Classifies funds by name keywords.

    Args:
        name: Fund name.
        label: Expected asset class.
    """
    assert rules.classify_fund(name) == label
