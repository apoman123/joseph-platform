"""Generates the US Treasury market (USBOND) calendar from Treasury data.

A USBOND session is a day on which the US Department of the Treasury
published its Daily Treasury Par Yield Curve Rates. They are read from the
per-year CSV export of home.treasury.gov (the source of
``joseph.market_data.adapters.treasury``; the all-years export is refused
with HTTP 403), one request per year from ``--start`` to the current year.

Every weekday without a published value is an observed closure (a row
with only blank cells counts as none): holidays, the Good Fridays on which
Treasury did not publish (it did in 2015, 2021 and 2026, for instance),
Martin Luther King Jr. Day before 1998, and closures such as 2001-09-11.
A published Saturday or Sunday would be a weekend session (none is
expected). Treasury publishes no schedule, so days after the newest
publication follow the rules of ``sessions._bond_holidays``.
The output is ``src/joseph/domain/calendar_data/usbond.json`` with the keys
of ``xtai.json``.

Usage:
    uv run python scripts/generate_usbond_calendar.py [--start 1990]

The script does not import ``joseph``: the engine loads the table it
writes at import time.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import time
import zoneinfo

import httpx

_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/{year}/all"
)
_CURVE = "daily_treasury_yield_curve"
_FIRST_YEAR = 1990
_OUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src/joseph/domain/calendar_data/usbond.json"
)
_PAUSE_S = 1.0
_ATTEMPTS = 5
_NY = zoneinfo.ZoneInfo("America/New_York")


def _today() -> datetime.date:
    """Returns today's date in New York.

    Returns:
        The local date.
    """
    return datetime.datetime.now(_NY).date()


def parse_dates(text: str) -> set[datetime.date]:
    """Reads the publication days of one year's yield-curve CSV export.

    A row whose cells are all blank (or ``N/A``) is not a publication:
    Treasury lists 2010-10-11 (Columbus Day) that way.

    Args:
        text: CSV body (header ``Date,"1 Mo",...``; ``MM/DD/YYYY`` dates).

    Returns:
        Dates with at least one published value.

    Raises:
        ValueError: If the body is not the expected CSV.
    """
    rows = list(csv.reader(text.lstrip("\ufeff").splitlines()))
    if not rows or not rows[0] or rows[0][0].strip().lower() != "date":
        raise ValueError("unexpected yield-curve CSV header")
    dates = set()
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        if not any(c.strip() and c.strip().upper() != "N/A" for c in row[1:]):
            continue
        month, day, year = (int(part) for part in row[0].split("/"))
        dates.add(datetime.date(year, month, day))
    return dates


def _year_dates(client: httpx.Client, year: int) -> set[datetime.date]:
    """Fetches the published dates of one year.

    Args:
        client: HTTP client.
        year: Calendar year.

    Returns:
        Dates with a published row.

    Raises:
        RuntimeError: If Treasury keeps failing or returns another year.
    """
    for attempt in range(_ATTEMPTS):
        try:
            response = client.get(
                _URL.format(year=year),
                params={
                    "type": _CURVE,
                    "field_tdr_date_value": str(year),
                    "page": "",
                    "_format": "csv",
                },
            )
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            dates = parse_dates(response.text)
            if any(day.year != year for day in dates):
                raise RuntimeError(f"treasury {year} returned other years")
            return dates
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"treasury {year} failed after {_ATTEMPTS} attempts")


def main() -> None:
    """Runs the generator.

    Raises:
        RuntimeError: If a past year has no published row.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=_FIRST_YEAR)
    args = parser.parse_args()
    today = _today()
    published: set[datetime.date] = set()
    with httpx.Client(timeout=60, headers={"User-Agent": "joseph"}) as client:
        for year in range(args.start, today.year + 1):
            dates = _year_dates(client, year)
            if not dates and year < today.year:
                raise RuntimeError(f"treasury {year} has no published rows")
            published |= dates
            time.sleep(_PAUSE_S)
    first = datetime.date(args.start, 1, 1)
    # The newest published row ends the observation: later days (today's
    # row appears in the New York evening) are not settled yet.
    observed_through = max(published)
    closures: list[str] = []
    weekend_sessions: list[str] = []
    day = first
    while day <= observed_through:
        if day.weekday() < 5 and day not in published:
            closures.append(day.isoformat())
        if day.weekday() >= 5 and day in published:
            weekend_sessions.append(day.isoformat())
        day += datetime.timedelta(days=1)
    payload = {
        "calendar": "USBOND",
        "source": (
            "US Treasury Daily Par Yield Curve Rates, per-year CSV "
            "(observed publication days)"
        ),
        "generated_on": today.isoformat(),
        "observed_from": first.isoformat(),
        "observed_through": observed_through.isoformat(),
        "closures": closures,
        "weekend_sessions": weekend_sessions,
        "scheduled_through": observed_through.isoformat(),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(  # noqa: T201
        f"wrote {_OUT}: {len(published)} published days through "
        f"{observed_through}, {len(closures)} weekday closures, "
        f"{len(weekend_sessions)} weekend sessions"
    )


if __name__ == "__main__":
    main()
