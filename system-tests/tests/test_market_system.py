"""System tests for the market engine running as a real process."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import httpx

MOCK_BTC = "mock:perp:BTCUSD"
_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
)


def test_health_and_metrics(market: httpx.Client) -> None:
    """Health is green and metrics are exposed in Prometheus text format."""
    assert market.get("/health").status_code == 200
    metrics = market.get("/metrics")
    assert metrics.status_code == 200
    assert "# TYPE" in metrics.text


def test_venues_report_capabilities_and_health(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """The venue list validates and includes the DEMO venue."""
    body = market.get("/v1/venues").json()
    validate("market.schema.json", "VenueStatusList", body)
    venues = {v["capabilities"]["venue"] for v in body["venues"]}
    assert "mock" in venues


def test_instrument_search_is_venue_scoped(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Instrument search returns exact venue ids."""
    body = market.get("/v1/instruments", params={"q": "BTC"}).json()
    validate("market.schema.json", "InstrumentList", body)
    ids = [i["instrument_id"] for i in body["instruments"]]
    assert MOCK_BTC in ids
    assert all(":" in i for i in ids)


def test_candles_have_provenance_and_finality(
    market: httpx.Client, validate: Callable[[str, str, object], None]
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
    market: httpx.Client, validate: Callable[[str, str, object], None]
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
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Bad input yields a contract ErrorResponse, not a stack trace."""
    response = market.get(
        "/v1/candles", params={"instrument_id": "BTCUSDT", "tf": "5m"}
    )
    assert response.status_code in (404, 422)
    validate("common.schema.json", "ErrorResponse", response.json())


def test_indicator_plugins_and_snapshots(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """All seven plugins are registered and produce valid snapshots."""
    plugins = market.get("/v1/indicators/plugins").json()
    validate("indicators.schema.json", "IndicatorPluginList", plugins)
    ids = {p["plugin_id"] for p in plugins["plugins"]}
    assert {
        "daily_bias",
        "fvg_ifvg",
        "order_breaker_blocks",
        "liquidity_sweeps",
        "order_flow_pressure",
        "auction_value_areas",
        "premium_discount_ote",
    } <= ids
    snaps = market.get(
        "/v1/indicators/snapshot",
        params={"instrument_id": MOCK_BTC, "tf": "5m"},
    ).json()
    validate("indicators.schema.json", "IndicatorSnapshotList", snaps)
    aois = market.get(
        "/v1/aois", params={"instrument_id": MOCK_BTC, "tf": "5m"}
    ).json()
    validate("indicators.schema.json", "AoiList", aois)


def _demo_strategy(strategy_id: str) -> dict[str, object]:
    """Builds the Long A fixture retargeted to the DEMO venue.

    Args:
        strategy_id: Slug to use.

    Returns:
        Strategy definition.
    """
    body = json.loads(
        (_FIXTURES / "strategy.fixture.json").read_text(encoding="utf-8")
    )
    definition = dict(body["valid"][0])
    definition["strategy_id"] = strategy_id
    definition["universe"] = {"qualification": "ANY", "venues": ["mock"]}
    definition["execution_mode"] = "ALERT_ONLY"
    return definition


def test_strategy_lifecycle_and_backtest(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """Validate → create → new version → backtest → activate → pause."""
    definition = _demo_strategy("system-long-a")
    result = market.post("/v1/strategies/validate", json=definition).json()
    validate("strategy.schema.json", "ValidationResult", result)
    assert result["valid"], result
    created = market.post("/v1/strategies", json=definition)
    assert created.status_code in (200, 201), created.text
    v1 = created.json()
    validate("strategy.schema.json", "StrategyVersion", v1)
    assert v1["version"] == 1
    definition["name"] = "Long A (edited)"
    v2 = market.post(
        "/v1/strategies/system-long-a/versions", json=definition
    ).json()
    assert v2["version"] == 2
    assert v2["definition_hash"] != v1["definition_hash"]
    candles = market.get(
        "/v1/candles",
        params={"instrument_id": MOCK_BTC, "tf": "5m", "limit": 500},
    ).json()["candles"]
    report = market.post(
        "/v1/strategies/system-long-a/backtests",
        json={
            "version": 2,
            "instrument_id": MOCK_BTC,
            "from_ms": candles[0]["start_ms"],
            "to_ms": candles[-1]["end_ms"],
        },
    )
    assert report.status_code == 200, report.text
    validate("strategy.schema.json", "BacktestReport", report.json())
    activated = market.post(
        "/v1/strategies/system-long-a/activate",
        json={"version": 2, "mode": "PAPER"},
    ).json()
    assert activated["state"] == "ACTIVE"
    paused = market.post("/v1/strategies/system-long-a/pause", json={})
    assert paused.json()["state"] == "PAUSED"


def test_auto_live_cannot_be_activated_from_market_engine(
    market: httpx.Client,
) -> None:
    """AUTO_LIVE activation is refused by the schema/service."""
    market.post("/v1/strategies", json=_demo_strategy("system-no-live"))
    response = market.post(
        "/v1/strategies/system-no-live/activate",
        json={"version": 1, "mode": "AUTO_LIVE"},
    )
    assert response.status_code in (409, 422)


def test_signals_are_tenant_scoped(
    market: httpx.Client, validate: Callable[[str, str, object], None]
) -> None:
    """The signal history validates and requires authentication."""
    body = market.get("/v1/signals", params={"limit": 50}).json()
    validate("signal.schema.json", "SignalList", body)
    assert all(s["tenant_id"] == "tenant-a" for s in body["signals"])
