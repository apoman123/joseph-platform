"""System tests for the session calendars of the market engine.

``GET /v1/calendars/{XTAI|XNYS|USBOND|24x7}/sessions`` answers from the
engine's calendars alone, so fixed historical and scheduled windows give
the same result on every run.
"""

from __future__ import annotations

import datetime
import zoneinfo
from collections.abc import Callable

import httpx
import pytest

from tests import conftest

TAIPEI = zoneinfo.ZoneInfo("Asia/Taipei")
_DAY_MS = 86_400_000


def _sessions(
    market: httpx.Client,
    validate: Callable[[str, str, object], None],
    calendar: str,
    from_ms: int,
    to_ms: int,
) -> list[dict[str, object]]:
    """Fetches and validates the sessions of a calendar window.

    Args:
        market: Market engine client.
        validate: Contract validator.
        calendar: Calendar id.
        from_ms: Inclusive window start.
        to_ms: Exclusive window end.

    Returns:
        Contract ``TradingSession`` objects.
    """
    response = market.get(
        f"/v1/calendars/{calendar}/sessions",
        params={"from_ms": from_ms, "to_ms": to_ms},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    validate("market.schema.json", "SessionList", body)
    assert body["calendar"] == calendar
    assert (body["from_ms"], body["to_ms"]) == (from_ms, to_ms)
    sessions = body["sessions"]
    assert isinstance(sessions, list)
    return sessions


def _taipei_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    """Returns the UTC epoch milliseconds of a Taipei wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=TAIPEI)
    return int(moment.timestamp() * 1000)


def test_xnys_skips_thanksgiving_and_closes_early_the_day_after(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """XNYS has no 2026-11-26 session and closes 2026-11-27 at 13:00 ET."""
    sessions = _sessions(
        market,
        validate,
        "XNYS",
        conftest.ny_ms(2026, 11, 23),
        conftest.ny_ms(2026, 11, 30),
    )
    by_day = {s["day"]: s for s in sessions}
    assert list(by_day) == [
        "2026-11-23",
        "2026-11-24",
        "2026-11-25",
        "2026-11-27",
    ]
    assert by_day["2026-11-25"]["open_ms"] == conftest.ny_ms(
        2026, 11, 25, 9, 30
    )
    assert by_day["2026-11-25"]["close_ms"] == conftest.ny_ms(2026, 11, 25, 16)
    assert by_day["2026-11-27"]["open_ms"] == conftest.ny_ms(
        2026, 11, 27, 9, 30
    )
    assert by_day["2026-11-27"]["close_ms"] == conftest.ny_ms(2026, 11, 27, 13)


def test_xtai_closes_for_lunar_new_year_2027(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """XTAI trades Feb 1 2027, is shut Feb 2-10, reopens Feb 11 09:00."""
    sessions = _sessions(
        market,
        validate,
        "XTAI",
        _taipei_ms(2027, 1, 25, 0, 0),
        _taipei_ms(2027, 2, 20, 0, 0),
    )
    days = [str(s["day"]) for s in sessions]
    assert days == [
        "2027-01-25",
        "2027-01-26",
        "2027-01-27",
        "2027-01-28",
        "2027-01-29",
        "2027-02-01",
        "2027-02-11",
        "2027-02-12",
        "2027-02-15",
        "2027-02-16",
        "2027-02-17",
        "2027-02-18",
        "2027-02-19",
    ]
    reopening = sessions[days.index("2027-02-11")]
    assert reopening["open_ms"] == _taipei_ms(2027, 2, 11, 9, 0)
    assert reopening["close_ms"] == _taipei_ms(2027, 2, 11, 13, 30)


def test_usbond_has_a_good_friday_2026_session(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Treasury published on Good Friday 2026, so 2026-04-03 is a session."""
    sessions = _sessions(
        market,
        validate,
        "USBOND",
        conftest.ny_ms(2026, 3, 30),
        conftest.ny_ms(2026, 4, 7),
    )
    by_day = {s["day"]: s for s in sessions}
    assert list(by_day) == [
        "2026-03-30",
        "2026-03-31",
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
        "2026-04-06",
    ]
    assert by_day["2026-04-03"]["open_ms"] == conftest.ny_ms(2026, 4, 3, 8)
    assert by_day["2026-04-03"]["close_ms"] == conftest.ny_ms(2026, 4, 3, 17)
    nyse = _sessions(
        market,
        validate,
        "XNYS",
        conftest.ny_ms(2026, 4, 3),
        conftest.ny_ms(2026, 4, 4),
    )
    assert nyse == []


def test_continuous_calendar_has_no_sessions(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """The 24x7 calendar never closes, so a whole year lists no sessions."""
    start = conftest.ny_ms(2026, 1, 1)
    assert (
        _sessions(market, validate, "24x7", start, start + 365 * _DAY_MS) == []
    )


def test_unknown_calendar_is_not_found(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Unknown (or wrongly cased) calendar ids are a contract 404."""
    for calendar in ("LSE", "xnys"):
        response = market.get(
            f"/v1/calendars/{calendar}/sessions",
            params={"from_ms": 0, "to_ms": 1_000},
        )
        assert response.status_code == 404, response.text
        validate("common.schema.json", "ErrorResponse", response.json())
        assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "params",
    [
        {"from_ms": 0, "to_ms": 401 * _DAY_MS},
        {"from_ms": 5_000, "to_ms": 5_000},
        {"from_ms": 9_000, "to_ms": 5_000},
        {"from_ms": -1, "to_ms": 5_000},
        {"from_ms": 5_000},
    ],
    ids=["over-400-days", "empty", "reversed", "negative", "missing-end"],
)
def test_bad_session_windows_are_rejected(
    market: httpx.Client,
    validate: Callable[[str, str, object], None],
    params: dict[str, int],
) -> None:
    """Windows over 400 days, empty, reversed or incomplete are a 422."""
    response = market.get("/v1/calendars/XNYS/sessions", params=params)
    assert response.status_code == 422, response.text
    validate("common.schema.json", "ErrorResponse", response.json())
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_a_400_day_window_is_accepted(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Exactly 400 days is the largest window and lists every session."""
    start = conftest.ny_ms(2026, 1, 1)
    sessions = _sessions(market, validate, "XNYS", start, start + 400 * _DAY_MS)
    # 251 sessions in 2026, then 2027-01-04 .. 02-04 without MLK Day.
    assert len(sessions) == 274
    assert (sessions[0]["day"], sessions[-1]["day"]) == (
        "2026-01-02",
        "2027-02-04",
    )
    opens = [s["open_ms"] for s in sessions]
    assert opens == sorted(opens)
