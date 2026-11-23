"""Generates the TWSE (XTAI) trading-day table from official TWSE data.

Past trading days come from the monthly market summary (``FMTQIK``), which
lists every day the market traded, so ad hoc closures such as typhoon days
are captured. Future closures come from TWSE's published market holiday
schedule. The output is ``src/joseph/domain/calendar_data/xtai.json``.

Usage:
    uv run python scripts/generate_xtai_calendar.py [--start 2005-01]

TWSE throttles clients that send more than a few requests every five
seconds, so requests are paced; a full run takes about ten minutes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import time
import zoneinfo

import httpx

_FMTQIK = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
_SCHEDULE_OPENAPI = (
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
)
_SCHEDULE_RWD = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
_OUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src/joseph/domain/calendar_data/xtai.json"
)
_PAUSE_S = 2.2
_TRADING_NAMES = ("開始交易", "最後交易")
_TPE = zoneinfo.ZoneInfo("Asia/Taipei")


def _today() -> datetime.date:
    """Returns today's date in Taipei.

    Returns:
        The local date.
    """
    return datetime.datetime.now(_TPE).date()


def _roc_date(text: str) -> datetime.date:
    """Parses ``113/08/01`` (ROC calendar) to a date.

    Args:
        text: ROC date text.

    Returns:
        The Gregorian date.
    """
    year, month, day = (int(part) for part in text.strip().split("/"))
    return datetime.date(year + 1911, month, day)


def _month_trading_days(
    client: httpx.Client, year: int, month: int
) -> list[datetime.date]:
    """Fetches the days TWSE traded in one month.

    Args:
        client: HTTP client.
        year: Gregorian year.
        month: Month.

    Returns:
        Trading days (empty for a month without data).

    Raises:
        RuntimeError: If TWSE answers with an unexpected status.
    """
    for attempt in range(5):
        response = client.get(
            _FMTQIK,
            params={"date": f"{year}{month:02d}01", "response": "json"},
        )
        if response.status_code == 200:
            body = response.json()
            if body.get("stat", "").upper() != "OK":
                return []
            return [_roc_date(row[0]) for row in body.get("data", [])]
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"FMTQIK {year}-{month:02d} failed")


def _scheduled_closures(client: httpx.Client) -> set[datetime.date]:
    """Fetches published future closures (current and next schedule).

    Args:
        client: HTTP client.

    Returns:
        Dates the market will not trade.
    """
    closures: set[datetime.date] = set()
    rows: list[tuple[str, str]] = []
    response = client.get(_SCHEDULE_OPENAPI)
    if response.status_code == 200:
        for item in response.json():
            raw = item["Date"]
            text = f"{int(raw[:3]) + 1911}-{raw[3:5]}-{raw[5:7]}"
            rows.append((text, item["Name"]))
    for year in (_today().year, _today().year + 1):
        time.sleep(_PAUSE_S)
        response = client.get(
            _SCHEDULE_RWD,
            params={"response": "json", "date": f"{year}0101"},
        )
        if response.status_code == 200:
            rows.extend(
                (row[0], row[1]) for row in response.json().get("data", [])
            )
    for text, name in rows:
        if any(marker in name for marker in _TRADING_NAMES):
            continue
        day = datetime.date.fromisoformat(text)
        if day.weekday() < 5:
            closures.add(day)
    return closures


def main() -> None:
    """Runs the generator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2005-01")
    args = parser.parse_args()
    start_year, start_month = (int(p) for p in args.start.split("-"))
    today = _today()
    traded: set[datetime.date] = set()
    with httpx.Client(timeout=30, headers={"User-Agent": "joseph"}) as client:
        year, month = start_year, start_month
        while (year, month) <= (today.year, today.month):
            traded.update(_month_trading_days(client, year, month))
            time.sleep(_PAUSE_S)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        scheduled = _scheduled_closures(client)
    first = datetime.date(start_year, start_month, 1)
    # Days up to yesterday are settled by the observed record; the current
    # day may not be published yet.
    observed_through = today - datetime.timedelta(days=1)
    closures: list[str] = []
    weekend_sessions: list[str] = []
    day = first
    while day <= observed_through:
        if day.weekday() < 5 and day not in traded:
            closures.append(day.isoformat())
        if day.weekday() >= 5 and day in traded:
            weekend_sessions.append(day.isoformat())
        day += datetime.timedelta(days=1)
    future = sorted(d.isoformat() for d in scheduled if d > observed_through)
    payload = {
        "calendar": "XTAI",
        "source": "TWSE FMTQIK (observed) and holidaySchedule (published)",
        "generated_on": today.isoformat(),
        "observed_from": first.isoformat(),
        "observed_through": observed_through.isoformat(),
        "closures": closures + future,
        "weekend_sessions": weekend_sessions,
        "scheduled_through": max(
            [d.isoformat() for d in scheduled] or [observed_through.isoformat()]
        ),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(  # noqa: T201
        f"wrote {_OUT}: {len(closures)} observed closures, "
        f"{len(future)} scheduled, {len(weekend_sessions)} weekend sessions"
    )


if __name__ == "__main__":
    main()
