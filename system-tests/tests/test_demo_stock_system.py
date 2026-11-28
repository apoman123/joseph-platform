# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests for the DEMO stock ``mock:stock:ACME`` (XNYS calendar).

Every query uses a fixed, past window or ``at_ms`` so the synthetic data
(a pure function of instrument and time) is identical on every run. Bar
boundaries are checked against the engine's own sessions endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import httpx

from tests import conftest

ACME = "mock:stock:ACME"
MOCK_BTC = "mock:perp:BTCUSD"
_HOUR_MS = 3_600_000
WINDOW = (conftest.ny_ms(2026, 11, 13), conftest.ny_ms(2026, 11, 21))
"""Fri 13 .. Fri 20 Nov 2026 (six sessions around a weekend)."""
FLOW_AT = conftest.ny_ms(2026, 11, 18, 14)
"""An in-session evaluation time for volume-delta snapshots."""

_Validate: TypeAlias = Callable[[str, str, object], None]


def _window_sessions(
    market: httpx.Client, validate: _Validate
) -> list[dict[str, int]]:
    """Fetches the XNYS sessions of ``WINDOW``.

    Args:
        market: Market engine client.
        validate: Contract validator.

    Returns:
        One ``{"open_ms": ..., "close_ms": ...}`` mapping per session.
    """
    body = market.get(
        "/v1/calendars/XNYS/sessions",
        params={"from_ms": WINDOW[0], "to_ms": WINDOW[1]},
    ).json()
    validate("market.schema.json", "SessionList", body)
    return [
        {"open_ms": s["open_ms"], "close_ms": s["close_ms"]}
        for s in body["sessions"]
    ]


def _candles(
    market: httpx.Client, validate: _Validate, tf: str
) -> dict[str, Any]:
    """Fetches and validates ACME candles of ``WINDOW``.

    Asserts a 200 response.

    Args:
        market: Market engine client.
        validate: Contract validator.
        tf: Timeframe, such as ``1h``.

    Returns:
        Contract ``CandleBatch``.
    """
    response = market.get(
        "/v1/candles",
        params={
            "instrument_id": ACME,
            "tf": tf,
            "from_ms": WINDOW[0],
            "to_ms": WINDOW[1],
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate("market.schema.json", "CandleBatch", body)
    return body


def test_acme_instrument_contract_fields(
    market: httpx.Client, validate: _Validate
) -> None:
    """ACME is an XNYS equity without funding, leverage or TWSE rules."""
    body = market.get("/v1/instruments", params={"q": "ACME"}).json()
    validate("market.schema.json", "InstrumentList", body)
    acme = next(i for i in body["instruments"] if i["instrument_id"] == ACME)
    assert acme["calendar"] == "XNYS"
    assert acme["asset_class"] == "EQUITY"
    assert acme["listing_exchange"] == "DEMO"
    assert acme["key"]["market_type"] == "stock"
    assert acme["key"]["quote"] == "USD"
    assert acme["status"] == "TRADING"
    assert (acme["tick_size"], acme["lot_size"]) == ("0.01", "1")
    assert acme["funding_interval_hours"] is None
    assert acme["max_leverage"] is None
    assert acme["tick_ladder"] == []
    assert acme["price_limit_pct"] is None
    assert acme["board_lot"] is None
    btc = market.get("/v1/instruments", params={"q": "BTC"}).json()
    perp = next(i for i in btc["instruments"] if i["instrument_id"] == MOCK_BTC)
    assert perp.get("calendar", "24x7") == "24x7"


def test_acme_hourly_bars_follow_xnys_sessions(
    market: httpx.Client, validate: _Validate
) -> None:
    """1h bars open at 09:30 ET, stay inside sessions and skip weekends."""
    sessions = _window_sessions(market, validate)
    assert len(sessions) == 6
    body = _candles(market, validate, "1h")
    assert body["gaps"] == []
    assert body["complete"] is True
    candles = body["candles"]
    assert isinstance(candles, list)
    for candle in candles:
        home = [
            s
            for s in sessions
            if s["open_ms"] <= candle["start_ms"] < s["close_ms"]
        ]
        assert len(home) == 1, f"bar outside sessions: {candle['start_ms']}"
        session = home[0]
        assert (candle["start_ms"] - session["open_ms"]) % _HOUR_MS == 0
        assert candle["end_ms"] <= session["close_ms"]
        assert candle["is_final"] is True
        assert candle["feed_state"] == "DEMO"
    for session in sessions:
        bars = [
            c
            for c in candles
            if session["open_ms"] <= c["start_ms"] < session["close_ms"]
        ]
        # 09:30, 10:30 ... 15:30; the last bar is cut at the 16:00 close.
        assert len(bars) == 7
        assert bars[0]["start_ms"] == session["open_ms"]
        assert bars[-1]["end_ms"] == session["close_ms"]
        assert bars[-1]["end_ms"] - bars[-1]["start_ms"] == _HOUR_MS // 2
    assert len(candles) == 42
    assert candles[0]["start_ms"] == conftest.ny_ms(2026, 11, 13, 9, 30)
    friday_close = conftest.ny_ms(2026, 11, 13, 16)
    monday_open = conftest.ny_ms(2026, 11, 16, 9, 30)
    assert not [
        c for c in candles if friday_close <= c["start_ms"] < monday_open
    ]


def test_acme_buy_volume_is_classified(
    market: httpx.Client, validate: _Validate
) -> None:
    """Every ACME bar carries classified (not taker-flag) buy volume."""
    candles = _candles(market, validate, "1h")["candles"]
    assert isinstance(candles, list)
    assert {c["taker_buy_method"] for c in candles} == {"classified"}
    for candle in candles:
        buy = float(candle["taker_buy_base_volume"])
        assert 0 <= buy <= float(candle["base_volume"])


def test_acme_daily_bars_are_sessions(
    market: httpx.Client, validate: _Validate
) -> None:
    """A 1d ACME bar spans exactly one session, open to close."""
    sessions = _window_sessions(market, validate)
    body = _candles(market, validate, "1d")
    assert body["gaps"] == []
    assert [(c["start_ms"], c["end_ms"]) for c in body["candles"]] == [
        (s["open_ms"], s["close_ms"]) for s in sessions
    ]


def test_acme_snapshot_has_no_funding_or_open_interest(
    market: httpx.Client, validate: _Validate
) -> None:
    """The ACME snapshot has no funding or OI; the DEMO venue lists stocks."""
    snap = market.get(
        "/v1/market/snapshot", params={"instrument_id": ACME}
    ).json()
    validate("market.schema.json", "MarketSnapshot", snap)
    assert snap["funding"] is None
    assert snap["open_interest"] is None
    assert snap["feed_state"] == "DEMO"
    assert snap["last"] is not None
    book = market.get(
        "/v1/market/orderbook", params={"instrument_id": ACME, "depth": 5}
    ).json()
    validate("market.schema.json", "OrderBook", book)
    venues = market.get("/v1/venues").json()
    validate("market.schema.json", "VenueStatusList", venues)
    mock = next(
        v["capabilities"]
        for v in venues["venues"]
        if v["capabilities"]["venue"] == "mock"
    )
    assert "stock" in mock["market_types"]
    assert mock["market_data"]["classified_delta"] is True


def _delta_method(market: httpx.Client, instrument: str, tf: str) -> list[str]:
    """Runs the volume delta built-in and reads its method table.

    Args:
        market: Market engine client.
        instrument: Instrument id.
        tf: Timeframe.

    Returns:
        The table's method and coverage cells.
    """
    response = market.post(
        "/v1/scripts/builtin:volume_delta/run",
        json={
            "instrument_id": instrument,
            "tf": tf,
            "to_ms": FLOW_AT,
            "limit": 50,
        },
    )
    assert response.status_code == 200, response.text
    (table,) = [
        visual
        for visual in response.json()["visuals"]
        if visual["kind"] == "table"
    ]
    return [row[1] for row in table["data"]["rows"]]


def test_volume_delta_on_acme_is_trade_classification(
    market: httpx.Client,
) -> None:
    """ACME volume delta uses trade classification; the perp, taker volume."""
    for tf in ("5m", "1h"):
        assert _delta_method(market, ACME, tf) == ["Classified trades", "100%"]
    assert _delta_method(market, MOCK_BTC, "5m") == [
        "Taker buy volume",
        "100%",
    ]


def test_acme_is_screened_with_listed_security_thresholds(
    market: httpx.Client, validate: _Validate
) -> None:
    """ACME skips the OI and funding gates and uses the US equity bar."""
    report = market.get("/v1/market/qualification").json()
    validate("qualification.schema.json", "QualificationReport", report)
    records = {r["instrument_id"]: r for r in report["records"]}
    acme, btc = records[ACME], records[MOCK_BTC]
    gates = {g["gate"]: g for g in acme["gates"]}
    assert set(gates) == {"TURNOVER_24H", "SPREAD_PCT", "SLIPPAGE_50K_PCT"}
    assert gates["TURNOVER_24H"]["threshold"] == "20000000"
    assert gates["TURNOVER_24H"]["unit"] == "USD"
    assert acme["funding"]["risk"] == "NOT_APPLICABLE"
    assert acme["funding"]["normalized_8h_pct"] is None
    assert acme["asset_class"] == "EQUITY"
    # Missing funding/OI never counts as missing data for a listed stock.
    assert acme["status"] != "INSUFFICIENT_DATA"
    perp_gates = {g["gate"]: g for g in btc["gates"]}
    assert "OPEN_INTEREST_USD" in perp_gates
    assert perp_gates["TURNOVER_24H"]["threshold"] == "50000000"
