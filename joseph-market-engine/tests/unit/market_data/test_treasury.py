# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for Treasury CSV parsing, publication timing and caching."""

from __future__ import annotations

import datetime
import decimal
import zoneinfo
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import treasury

_D = decimal.Decimal
_NY = zoneinfo.ZoneInfo("America/New_York")
_TENOR = next(t for t in treasury.TENORS if t.code == "UST10Y")


def _ms(year: int, month: int, day: int, hour: int = 12) -> int:
    """Returns a New York wall time in epoch milliseconds.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, tzinfo=_NY)
    return int(moment.timestamp() * 1000)


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ('"1 Mo"', "1m"),
        ("1.5 Month", "1.5m"),
        ("10 Yr", "10y"),
        ("5 YR", "5y"),
        ("Date", None),
        ("Extrapolation Factor", None),
    ],
)
def test_column_labels_normalize(label: str, key: str | None) -> None:
    """Every observed label style maps to one tenor key."""
    assert treasury.column_key(label) == key


def test_csv_parsing_skips_blanks_and_rejects_other_bodies() -> None:
    """Blank and N/A cells are omitted; a non-CSV body is an error."""
    rows = treasury.parse_csv(
        'Date,"1 Mo","30 Yr",Other\n11/20/2026,4.19,,9\n\n10/11/2010,N/A,,\n'
    )
    assert rows[datetime.date(2026, 11, 20)] == {"1m": _D("4.19")}
    assert rows[datetime.date(2010, 10, 11)] == {}
    with pytest.raises(errors.VenueUnavailableError):
        treasury.parse_csv("<html>blocked</html>")
    with pytest.raises(errors.VenueUnavailableError):
        treasury.parse_csv("")


def test_due_day_waits_for_the_evening_publication() -> None:
    """Yields are due after 18:00 New York on a session day."""
    assert treasury.due_day(_ms(2026, 11, 20, 17)) == datetime.date(
        2026, 11, 19
    )
    assert treasury.due_day(_ms(2026, 11, 20, 19)) == datetime.date(
        2026, 11, 20
    )
    assert treasury.due_day(_ms(2026, 11, 22)) == datetime.date(2026, 11, 20)
    assert treasury.due_day(_ms(2026, 11, 26, 20)) == datetime.date(
        2026, 11, 25
    )


def test_daily_bars_skip_non_session_dates() -> None:
    """A published row on a non-session day yields no bar."""
    rows = {
        datetime.date(2026, 11, 22): {"10y": _D(4)},  # A Sunday.
        datetime.date(2026, 11, 20): {"10y": _D("4.92")},
        datetime.date(2026, 11, 19): {"2y": _D(4)},
    }
    bars, skipped = treasury.daily_bars(rows, _TENOR, _ms(2026, 11, 23))
    assert skipped == 1
    assert [b.close for b in bars] == [_D("4.92")]


class _Csv:
    """Scripted CSV source counting requests.

    Attributes:
        calls: Requested years.
    """

    def __init__(self, bodies: dict[int, str]) -> None:
        """Initializes the stub.

        Args:
            bodies: CSV text per year.
        """
        self.bodies = bodies
        self.calls: list[int] = []

    async def get_text(self, path: str, params: dict[str, Any]) -> str:
        """Returns the CSV of the requested year.

        Args:
            path: Path containing the year.
            params: Query parameters.

        Returns:
            CSV text.
        """
        del params
        year = int(path.split("/")[-2])
        self.calls.append(year)
        return self.bodies.get(year, 'Date,"10 Yr"\n')

    async def close(self) -> None:
        """Closes nothing."""


_HEADER = 'Date,"10 Yr"\n'


async def test_current_year_is_polled_until_the_new_day_appears() -> None:
    """The year is re-read every 15 minutes while a day is due, else rarely."""
    clock = clock_lib.ManualClock(_ms(2026, 11, 20, 18) + 60_000)
    source = _Csv({2026: _HEADER + "11/19/2026,4.90\n"})
    adapter = treasury.TreasuryAdapter(source, clock)  # type: ignore[arg-type]
    first = await adapter._year(treasury.NOMINAL, 2026)
    assert max(first) == datetime.date(2026, 11, 19)
    clock.advance(60_000)
    await adapter._year(treasury.NOMINAL, 2026)
    assert source.calls == [2026]
    source.bodies[2026] = _HEADER + "11/20/2026,4.92\n11/19/2026,4.90\n"
    clock.advance(15 * 60_000)
    await adapter._year(treasury.NOMINAL, 2026)
    assert source.calls == [2026, 2026]
    clock.advance(60 * 60_000)
    await adapter._year(treasury.NOMINAL, 2026)
    assert source.calls == [2026, 2026]
    clock.advance(6 * 3_600_000)
    await adapter._year(treasury.NOMINAL, 2026)
    assert source.calls == [2026, 2026, 2026]
    assert await adapter._year(treasury.REAL, 2002) == {}


async def test_past_years_are_read_once_when_complete() -> None:
    """A finished year is cached; one read before year end is refreshed."""
    clock = clock_lib.ManualClock(_ms(2025, 12, 30, 20))
    source = _Csv({2025: _HEADER + "12/30/2025,4.10\n"})
    adapter = treasury.TreasuryAdapter(source, clock)  # type: ignore[arg-type]
    await adapter._year(treasury.NOMINAL, 2025)
    clock.current_ms = _ms(2026, 1, 5, 20)
    source.bodies[2025] = _HEADER + "12/31/2025,4.12\n12/30/2025,4.10\n"
    await adapter._year(treasury.NOMINAL, 2025)
    await adapter._year(treasury.NOMINAL, 2025)
    assert source.calls == [2025, 2025]
    await adapter._year(treasury.NOMINAL, 2027)
    await adapter._year(treasury.NOMINAL, 2027)
    assert source.calls == [2025, 2025, 2027]


async def test_snapshot_without_recent_values_is_missing_data() -> None:
    """A series without values in two years has no snapshot."""
    clock = clock_lib.ManualClock(_ms(2026, 11, 23))
    adapter = treasury.TreasuryAdapter(_Csv({}), clock)  # type: ignore[arg-type]
    with pytest.raises(errors.MissingDataError):
        await adapter.fetch_snapshot(
            instruments.InstrumentId.parse("treasury:rate:UST10Y")
        )
    assert await adapter.fetch_snapshots() == []
    assert (
        await adapter.fetch_candles(
            instruments.InstrumentId.parse("treasury:rate:UST10Y"),
            timeframes.Timeframe.D1,
            10,
            5,
        )
        == []
    )
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_open_interest(
            instruments.InstrumentId.parse("treasury:rate:UST10Y")
        )
    with pytest.raises(errors.UnsupportedError):
        await adapter.fetch_funding(
            instruments.InstrumentId.parse("treasury:rate:UST10Y")
        )
    assert adapter.instrument_id("UST2Y").market_type is (
        instruments.MarketType.RATE
    )
