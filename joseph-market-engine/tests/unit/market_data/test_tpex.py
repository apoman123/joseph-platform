# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the TPEx adapter helpers, listings and schedule."""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.market_data.adapters import tpex
from joseph.market_data.adapters import twse

_D = decimal.Decimal
_TPE = datetime.timezone(datetime.timedelta(hours=8))
_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "venues"
_ID = instruments.InstrumentId.parse("tpex:stock:6488")
_SCHEDULE = "/www/zh-tw/bulletin/tradingDate"


def _load(name: str) -> Any:
    """Reads a TPEx fixture.

    Args:
        name: File name without extension.

    Returns:
        Parsed JSON.
    """
    return json.loads((_FIXTURES / "tpex" / f"{name}.json").read_text("utf-8"))


def _ms(year: int, month: int, day: int, hour: int = 10) -> int:
    """Returns a Taipei wall-clock time in epoch milliseconds.

    Args:
        year: Calendar year.
        month: Month, 1-12.
        day: Day of the month.
        hour: Hour of the day, Taipei time.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, tzinfo=_TPE)
    return int(moment.timestamp() * 1000)


class _Http:
    """Scripted ``VenueHttp`` stand-in.

    Attributes:
        answers: Response body, error to raise or ``params`` callable, keyed
            by path.
        calls: Every ``(path, params)`` requested, in order.
    """

    def __init__(self, answers: dict[str, Any]) -> None:
        """Initializes the stub.

        Args:
            answers: Response body, error to raise or callable that maps the
                query parameters to a body, keyed by path.
        """
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Records the request and returns the scripted answer of its path.

        Args:
            path: Request path; must be scripted.
            params: Query parameters, passed to a callable answer.

        Returns:
            The scripted body, or what the callable answer returns.

        Raises:
            errors.JosephError: The answer scripted for ``path`` is an error.
        """
        self.calls.append((path, params))
        answer = self.answers[path]
        if isinstance(answer, errors.JosephError):
            raise answer
        return answer(params) if callable(answer) else answer

    async def close(self) -> None:
        """Closes nothing."""


def _adapter(
    clock: clock_lib.Clock, www: dict[str, Any] | None = None
) -> tpex.TpexAdapter:
    """Builds an adapter over scripted clients.

    Args:
        clock: Time source of the adapter.
        www: Answers of the website client by path; none when omitted. The
            OpenAPI and MIS clients answer nothing.

    Returns:
        The adapter.
    """
    return tpex.TpexAdapter(
        _Http({}),  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        _Http(www or {}),  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        _Http({}),  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        clock,
    )


def _schedule(rows: str) -> dict[str, Any]:
    """Wraps table rows the way ``bulletin/tradingDate`` returns them.

    Args:
        rows: HTML ``<tr>`` rows.

    Returns:
        The response body.
    """
    return {"data": {"html": f"<table>{rows}</table>"}, "stat": "ok"}


def _schedule_with_2030_holiday(params: dict[str, Any]) -> dict[str, Any]:
    """Answers ``bulletin/tradingDate`` with a closure on 2030-03-05.

    Args:
        params: Query parameters; ``date`` is the year asked for.

    Returns:
        A schedule with the closure for the year 2030, "no data" otherwise.
    """
    if params["date"] != "2030":
        return {"stat": "no data"}
    return _schedule(
        "<tr><td>測試休市</td><td>3月5日</td><td>二</td><td></td></tr>"
    )


@pytest.fixture(autouse=True)
def _restore_xtai() -> Iterator[None]:
    """Restores the XTAI closures added at runtime after each test.

    Yields:
        Nothing; the test runs while suspended.
    """
    saved = set(sessions.XTAI._extra_closures)
    yield
    sessions.XTAI._extra_closures.clear()
    sessions.XTAI.add_closures(saved)


def test_codes_dates_and_channels_follow_tpex_formats() -> None:
    """ROC dates with listing marks, ETF codes and ``otc_`` MIS keys parse."""
    assert tpex.trading_day("115/11/23") == (datetime.date(2026, 11, 23), False)
    assert tpex.trading_day("106/01/17*") == (datetime.date(2017, 1, 17), True)
    assert tpex.trading_day("106/01/17\uff0a")[1]
    with pytest.raises(ValueError, match="ROC"):
        tpex.trading_day("x")
    assert tpex.is_etf_code("00679B") and tpex.is_etf_code("006201")
    assert tpex.is_etf_code("00888")
    for other in ("00687C", "00636K", "6488", "8349A", "020025", "0012345"):
        assert not tpex.is_etf_code(other)
    assert twse.mis_key("6488", tpex.MIS_MARKET) == "otc_6488.tw"


def test_etf_classes_and_limits_follow_codes_and_published_limits() -> None:
    """B/D codes and bond names are bonds; 9999.95 is no limit; L is 20 %."""
    bond = instruments.AssetClass.BOND
    equity = instruments.AssetClass.EQUITY
    assert tpex.etf_asset_class("00679B", "元大美債20年") is bond
    assert tpex.etf_asset_class("00980D", "主動聯博投等入息") is bond
    assert tpex.etf_asset_class("00999", "示範公司債") is bond
    assert tpex.etf_asset_class("00980T", "平衡凱基美國TOP") is equity
    assert tpex.etf_asset_class("00998", "債股平衡") is equity
    unlimited = {"NextLimitUp": "9999.95", "NextLimitDown": "0.01"}
    limited = {"NextLimitUp": "28.98", "NextLimitDown": "23.72"}
    assert tpex.etf_price_limit("00679B", unlimited) is None
    assert tpex.etf_price_limit("006201", limited) == _D(10)
    assert tpex.etf_price_limit("00999L", limited) == _D(20)
    assert tpex.etf_price_limit("00999R", limited) == _D(10)


def test_listing_rules_mark_halts_first_five_days_and_exclusions() -> None:
    """Suspensions halt; first-five-day listings have no limit; 8349A drops."""
    now = _ms(2026, 11, 18)
    quotes = [
        {"SecuritiesCompanyCode": "7848", "CompanyName": "騏億鑫"},
        {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶"},
        {"SecuritiesCompanyCode": "8349A", "CompanyName": "恒耀甲特"},
        {
            "SecuritiesCompanyCode": "00679B",
            "CompanyName": "元大美債20年",
            "NextLimitUp": "9999.95",
        },
    ]
    companies = [
        {"SecuritiesCompanyCode": "7848", "DateOfListing": "20261116"},
        {"SecuritiesCompanyCode": "6488", "DateOfListing": "bad"},
        {
            "SecuritiesCompanyCode": "8084",
            "CompanyAbbreviation": "巨虹",
            "DateOfListing": "20040308",
            "Symbol": "CHIP HOPE　　",
        },
        {"SecuritiesCompanyCode": "4804", "CompanyName": "大略"},
    ]
    first_five = [
        {
            "SecuritiesCompanyCode": "7848",
            "EndDateForStabilizationOperation": "1151120",
        },
        {"SecuritiesCompanyCode": "1", "EndDateForStabilizationOperation": ""},
    ]
    modes = [
        {"SecuritiesCompanyCode": "6488", "SuspensionOfTrading": ""},
        {"SecuritiesCompanyCode": "8084", "SuspensionOfTrading": "\uff39"},
    ]
    rows, meta = tpex.parse_instruments(
        quotes, companies, first_five, modes, now_ms=now
    )
    by_id = {str(r.instrument_id): r for r in rows}
    assert set(by_id) == {
        "tpex:stock:7848",
        "tpex:stock:6488",
        "tpex:etf:00679B",
        "tpex:stock:8084",
    }
    assert by_id["tpex:stock:7848"].price_limit_pct is None
    assert by_id["tpex:stock:6488"].price_limit_pct == _D(10)
    assert by_id["tpex:stock:6488"].name is None
    halted = by_id["tpex:stock:8084"]
    assert halted.status is instruments.InstrumentStatus.HALTED
    assert (halted.name, halted.local_name) == ("CHIP HOPE", "巨虹")
    assert meta["7848"].listed == datetime.date(2026, 11, 16)
    assert meta["8084"].listed == datetime.date(2004, 3, 8)
    assert meta["6488"].listed is None and meta["00679B"].etf
    later, _ = tpex.parse_instruments(
        quotes[:1], companies, first_five, [], now_ms=_ms(2026, 11, 23)
    )
    assert later[0].price_limit_pct == _D(10)


def test_schedule_closures_read_stock_rows_only() -> None:
    """Bond-system rows are skipped; the stock row names no-trading days."""
    payload = _load("trading_date_2026")
    closures = tpex.schedule_closures(payload, 2026, datetime.date(2026, 1, 1))
    assert datetime.date(2026, 2, 10) not in closures  # Bond last day.
    assert datetime.date(2026, 2, 11) not in closures  # Stock last day.
    assert {datetime.date(2026, 2, 12), datetime.date(2026, 2, 13)} <= closures
    assert datetime.date(2026, 2, 20) in closures  # Multi-date cell.
    assert datetime.date(2026, 10, 26) in closures  # Continuation row.
    assert datetime.date(2026, 1, 2) not in closures  # 開始交易.
    assert all(day.weekday() < 5 for day in closures)
    late = tpex.schedule_closures(payload, 2026, datetime.date(2026, 12, 1))
    assert late == {datetime.date(2026, 12, 25)}
    assert (
        tpex.schedule_closures({"stat": "none"}, 2028, datetime.date.min)
        == set()
    )
    odd = _schedule(
        "<tr><td>x</td><td>2月30日</td><td>一</td><td>y</td></tr>"
        "<tr><td>紀念日</td><td>3月3日</td><td>五</td></tr>"
    )
    assert tpex.schedule_closures(odd, 2028, datetime.date.min) == {
        datetime.date(2028, 3, 3)
    }


async def test_refresh_calendar_adds_new_closures_once_a_day() -> None:
    """A newly published weekday closure is added to XTAI, once per day."""
    holiday = datetime.date(2030, 3, 5)
    assert sessions.XTAI.session(holiday) is not None
    adapter = _adapter(
        clock_lib.ManualClock(_ms(2030, 1, 2)),
        {_SCHEDULE: _schedule_with_2030_holiday},
    )
    assert await adapter.refresh_calendar() == {holiday}
    assert sessions.XTAI.session(holiday) is None
    assert await adapter.refresh_calendar() == set()


async def test_refresh_calendar_tolerates_unreachable_schedules() -> None:
    """An unreachable schedule gives no closures instead of an error."""
    adapter = _adapter(
        clock_lib.ManualClock(_ms(2030, 1, 2)),
        {_SCHEDULE: errors.VenueUnavailableError("down")},
    )
    assert await adapter.refresh_calendar() == set()


def test_trading_stock_rows_scale_and_skip_bad_rows() -> None:
    """Thousands scale to shares and TWD; bad rows skip; ``*`` marks listing."""
    payload = {
        "stat": "ok",
        "tables": [
            {
                "data": [
                    [
                        *("115/11/23", "12,123", "5,356,199", "410.50"),
                        *("471.00", "410.50", "460.50", "19.00", "16,986"),
                    ],
                    ["115/11/20", "0", "0", "--", "--", "--", "--", "", "0"],
                    ["bad", "1", "1", "1", "1", "1", "1", "", "1"],
                    ["115/11/22", "1", "1", "1", "1", "1", "1", "", "1"],
                ]
            }
        ],
    }
    bars, listed = tpex.parse_trading_stock(payload, _ID, _ms(2026, 11, 23, 16))
    assert listed is None and len(bars) == 1  # 11/22 is a Sunday.
    bar = bars[0]
    assert bar.base_volume == _D(12_123_000)
    assert bar.quote_turnover == _D(5_356_199_000)
    assert bar.trade_count == 16986 and bar.is_final
    session = sessions.XTAI.session(datetime.date(2026, 11, 23))
    assert session is not None and bar.start_ms == session.open_ms
    assert tpex.parse_trading_stock({"stat": "error"}, _ID, 0) == ([], None)
    _, first = tpex.parse_trading_stock(
        _load("trading_stock_00679B_201701"), _ID, _ms(2026, 11, 23)
    )
    assert first == datetime.date(2017, 1, 17)


async def test_listing_date_learned_from_history_survives_reloads() -> None:
    """A listing day read from tradingStock is kept across list refreshes."""
    clock = clock_lib.ManualClock(_ms(2026, 11, 23, 16))
    openapi = _Http(
        {
            "/v1/tpex_mainboard_quotes": _load("mainboard_quotes"),
            "/v1/mopsfin_t187ap03_O": _load("t187ap03_O"),
            "/v1/tpex_ipo_no_limit": _load("ipo_no_limit"),
            "/v1/tpex_cmode": _load("cmode"),
        }
    )
    www = _Http(
        {
            _SCHEDULE: {"stat": "none"},
            "/www/zh-tw/afterTrading/tradingStock": _load(
                "trading_stock_00679B_201701"
            ),
        }
    )
    adapter = tpex.TpexAdapter(
        openapi,  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        www,  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        _Http({}),  # type: ignore[arg-type]  # _Http stands in for VenueHttp
        clock,
    )
    await adapter.list_instruments()
    bond = instruments.InstrumentId.parse("tpex:etf:00679B")
    await adapter._fetch_month(bond, 2017, 1)
    assert adapter.listing["00679B"].listed == datetime.date(2017, 1, 17)
    clock.advance(7 * 3_600_000)
    await adapter.list_instruments()
    assert adapter.listing["00679B"].listed == datetime.date(2017, 1, 17)
    assert adapter.listing["00679B"].etf
    assert adapter.instrument_id("00679B") == bond
