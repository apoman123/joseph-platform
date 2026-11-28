# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests for the market engine running as a real process."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import httpx

MOCK_BTC = "mock:perp:BTCUSD"
_Validate: TypeAlias = Callable[[str, str, object], None]
BUILTIN_PLUGINS = (
    "sma",
    "ema",
    "anchored_vwap",
    "regression_channel",
    "bollinger_bands",
    "rsi",
    "atr",
    "volume_delta",
    "pivot_points",
)
"""The built-in indicator plugins, in their fixed registration order."""


def test_health_and_metrics(market: httpx.Client) -> None:
    """Health is green and metrics are exposed in Prometheus text format."""
    assert market.get("/health").status_code == 200
    metrics = market.get("/metrics")
    assert metrics.status_code == 200
    assert "# TYPE" in metrics.text


def test_venues_report_capabilities_and_health(
    market: httpx.Client, validate: _Validate
) -> None:
    """The venue list validates and includes the DEMO venue."""
    body = market.get("/v1/venues").json()
    validate("market.schema.json", "VenueStatusList", body)
    venues = {v["capabilities"]["venue"] for v in body["venues"]}
    assert "mock" in venues


def test_instrument_search_is_venue_scoped(
    market: httpx.Client, validate: _Validate
) -> None:
    """Instrument search returns venue-qualified ids, DEMO BTC among them."""
    body = market.get("/v1/instruments", params={"q": "BTC"}).json()
    validate("market.schema.json", "InstrumentList", body)
    ids = [i["instrument_id"] for i in body["instruments"]]
    assert MOCK_BTC in ids
    assert all(":" in i for i in ids)


def test_candles_have_provenance_and_finality(
    market: httpx.Client, validate: _Validate
) -> None:
    """Candles validate, are ordered, and only the newest may be open."""
    body = market.get(
        "/v1/candles",
        params={"instrument_id": MOCK_BTC, "tf": "5m", "limit": 300},
    ).json()
    validate("market.schema.json", "CandleBatch", body)
    candles = body["candles"]
    assert len(candles) >= 100
    starts = [c["start_ms"] for c in candles]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert all(c["feed_state"] == "DEMO" for c in candles)
    assert all(c["is_final"] for c in candles[:-1])


def test_snapshot_orderbook_and_qualification(
    market: httpx.Client, validate: _Validate
) -> None:
    """Snapshot, order book and qualification conform to contracts."""
    snap = market.get(
        "/v1/market/snapshot", params={"instrument_id": MOCK_BTC}
    ).json()
    validate("market.schema.json", "MarketSnapshot", snap)
    book = market.get(
        "/v1/market/orderbook",
        params={"instrument_id": MOCK_BTC, "depth": 20},
    ).json()
    validate("market.schema.json", "OrderBook", book)
    report = market.get("/v1/market/qualification").json()
    validate("qualification.schema.json", "QualificationReport", report)


def test_unknown_instrument_returns_contract_error(
    market: httpx.Client, validate: _Validate
) -> None:
    """Bad input yields a contract ErrorResponse, not a stack trace."""
    response = market.get(
        "/v1/candles", params={"instrument_id": "BTCUSDT", "tf": "5m"}
    )
    assert response.status_code in (404, 422)
    validate("common.schema.json", "ErrorResponse", response.json())


def test_indicator_plugins_and_snapshots(
    market: httpx.Client, validate: _Validate
) -> None:
    """The built-ins come first, in order; snapshots and AOIs validate."""
    plugins = market.get("/v1/indicators/plugins").json()
    validate("indicators.schema.json", "IndicatorPluginList", plugins)
    ids = tuple(p["plugin_id"] for p in plugins["plugins"])
    assert ids[: len(BUILTIN_PLUGINS)] == BUILTIN_PLUGINS
    snaps = market.get(
        "/v1/indicators/snapshot",
        params={"instrument_id": MOCK_BTC, "tf": "5m"},
    ).json()
    validate("indicators.schema.json", "IndicatorSnapshotList", snaps)
    aois = market.get(
        "/v1/aois", params={"instrument_id": MOCK_BTC, "tf": "5m"}
    ).json()
    validate("indicators.schema.json", "AoiList", aois)


def test_strategy_backtest_and_activation_lifecycle(
    market: httpx.Client, validate: _Validate
) -> None:
    """A built-in strategy backtests, activates, pauses, resumes, deletes."""
    report = market.post(
        "/v1/scripts/builtin:ema-trend-long/run",
        json={
            "instrument_id": MOCK_BTC,
            "tf": "5m",
            "limit": 500,
            "inputs": {"require_flow": False},
        },
    )
    assert report.status_code == 200, report.text
    body = report.json()
    validate("python-scripts.schema.json", "ScriptResult", body)
    assert body["kind"] == "strategy"
    assert body["version"] == 0
    assert "trade_count" in body["metrics"]
    assert len(body["trades"]) >= body["metrics"]["trade_count"]
    assert all(o["action"] in {"long", "close"} for o in body["orders"])
    created = market.post(
        "/v1/strategy-activations",
        json={
            "script_id": "builtin:ema-trend-long",
            "instrument_ids": [MOCK_BTC],
            "tf": "5m",
            "mode": "ALERT_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    activation = created.json()
    validate("python-scripts.schema.json", "StrategyActivation", activation)
    assert activation["state"] == "ACTIVE"
    activation_id = activation["activation_id"]
    paused = market.post(f"/v1/strategy-activations/{activation_id}/pause")
    assert paused.json()["state"] == "PAUSED"
    resumed = market.post(f"/v1/strategy-activations/{activation_id}/resume")
    assert resumed.json()["state"] == "ACTIVE"
    deleted = market.delete(f"/v1/strategy-activations/{activation_id}")
    assert deleted.status_code == 204


def test_auto_live_cannot_be_activated_from_market_engine(
    market: httpx.Client, validate: _Validate
) -> None:
    """AUTO_LIVE activation is refused with 409 TRADE_MODE_DISABLED."""
    response = market.post(
        "/v1/strategy-activations",
        json={
            "script_id": "builtin:ema-trend-long",
            "instrument_ids": [MOCK_BTC],
            "tf": "5m",
            "mode": "AUTO_LIVE",
        },
    )
    assert response.status_code == 409, response.text
    validate("common.schema.json", "ErrorResponse", response.json())
    assert response.json()["code"] == "TRADE_MODE_DISABLED"
    activations = market.get("/v1/strategy-activations").json()
    assert all(a["mode"] != "AUTO_LIVE" for a in activations["activations"])


def test_signals_are_tenant_scoped(
    market: httpx.Client, validate: _Validate
) -> None:
    """The signal history validates and holds only tenant A's signals."""
    body = market.get("/v1/signals", params={"limit": 50}).json()
    validate("signal.schema.json", "SignalList", body)
    assert all(s["tenant_id"] == "tenant-a" for s in body["signals"])
