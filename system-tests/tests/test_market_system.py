# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests for the market engine running as a real process."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeAlias

import httpx

MOCK_BTC = "mock:perp:BTCUSD"
_Validate: TypeAlias = Callable[[str, str, object], None]
BUILTIN_INDICATORS = (
    "builtin:sma",
    "builtin:ema",
    "builtin:anchored_vwap",
    "builtin:regression_channel",
    "builtin:bollinger_bands",
    "builtin:rsi",
    "builtin:atr",
    "builtin:volume_delta",
    "builtin:pivot_points",
)
"""The built-in indicator scripts, in their fixed catalog order."""


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


def test_snapshot_orderbook_and_builtin_filter_scan(
    market: httpx.Client, validate: _Validate
) -> None:
    """Snapshot and order book conform; built-in filters scan in process."""
    snap = market.get(
        "/v1/market/snapshot", params={"instrument_id": MOCK_BTC}
    ).json()
    validate("market.schema.json", "MarketSnapshot", snap)
    book = market.get(
        "/v1/market/orderbook",
        params={"instrument_id": MOCK_BTC, "depth": 20},
    ).json()
    validate("market.schema.json", "OrderBook", book)
    submitted = market.post(
        "/v1/script-scans",
        json={
            "venue": "mock",
            "market_type": "perp",
            "conditions": [
                {"script_id": "builtin:liquidity-qualification"},
                {
                    "script_id": "builtin:price",
                    "inputs": {"operator": "above", "value": 0},
                },
            ],
        },
    )
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()
    for _ in range(200):
        job = market.get(f"/v1/script-scans/{job['job_id']}").json()
        if job["status"] not in {"pending", "running"}:
            break
        time.sleep(0.05)
    assert job["status"] == "complete", job
    matched = {row["instrument_id"] for row in job["matches"]}
    assert MOCK_BTC in matched
    for row in job["matches"]:
        validate("market.schema.json", "MarketSnapshot", row["snapshot"])


def test_unknown_instrument_returns_contract_error(
    market: httpx.Client, validate: _Validate
) -> None:
    """Bad input yields a contract ErrorResponse, not a stack trace."""
    response = market.get(
        "/v1/candles", params={"instrument_id": "BTCUSDT", "tf": "5m"}
    )
    assert response.status_code in (404, 422)
    validate("common.schema.json", "ErrorResponse", response.json())


def test_builtin_indicator_catalog_and_default_studies(
    market: httpx.Client, validate: _Validate
) -> None:
    """The built-in indicators come first; default studies run in a batch."""
    catalog = market.get(
        "/v1/scripts/catalog", params={"kind": "indicator"}
    ).json()
    validate("python-scripts.schema.json", "CatalogList", catalog)
    ids = tuple(script["script_id"] for script in catalog["scripts"])
    assert ids[: len(BUILTIN_INDICATORS)] == BUILTIN_INDICATORS
    batch = market.post(
        "/v1/scripts/run-batch",
        json={
            "instrument_id": MOCK_BTC,
            "tf": "5m",
            "limit": 2000,
            "include_open": True,
            "runs": [
                {"script_id": "builtin:ema"},
                {"script_id": "builtin:volume_delta"},
                {"script_id": "builtin:pivot_points"},
            ],
        },
    )
    assert batch.status_code == 200, batch.text
    body = batch.json()
    validate("python-scripts.schema.json", "ScriptBatchResult", body)
    assert all(entry["error"] is None for entry in body["results"]), body
    pivots = body["results"][2]["result"]
    assert any(visual["kind"] == "line" for visual in pivots["visuals"])


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
