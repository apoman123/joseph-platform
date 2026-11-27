# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests of the market-engine HTTP and WebSocket API."""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib
from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import httpx
import httpx2
import pytest
import respx
from fastapi import testclient

from joseph import bootstrap

Validate: TypeAlias = Callable[[str, str, object], None]


def _fixture_rows(venue: str, name: str) -> list[dict[str, Any]]:
    """Reads a captured venue fixture.

    Args:
        venue: Venue directory under ``fixtures/venues``.
        name: File name without extension.

    Returns:
        The fixture's JSON rows.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "fixtures"
        / "venues"
        / venue
        / f"{name}.json"
    )
    rows: list[dict[str, Any]] = json.loads(path.read_text())
    return rows


def _error(response: httpx2.Response, status: int, code: str) -> None:
    """Asserts a contract error response with a matching trace id.

    Args:
        response: Response to check.
        status: Expected HTTP status.
        code: Expected error code.
    """
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert body["trace_id"] == response.headers["x-trace-id"]
    assert set(body) == {
        "code",
        "message",
        "trace_id",
        "retryable",
        "field_errors",
        "source_status",
    }


def test_health_and_metrics(demo_client: testclient.TestClient) -> None:
    """Liveness reports venues and metrics are Prometheus text."""
    health = demo_client.get("/health").json()
    assert health["status"] == "ok"
    assert health["demo_only"] is True
    assert "mock" in health["venues"]
    metrics = demo_client.get("/metrics")
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "joseph_http_requests_total" in metrics.text


def test_stock_monthly_default_window_returns_available_history(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """Returns stock months instead of rejecting a pre-1990 lookback.

    Args:
        demo_client: Running market API with the backend mock venue.
        contract_validate: Shared JSON Schema validator.

    Returns:
        None. Validates the HTTP response and its historical boundary.
    """
    response = demo_client.get(
        "/v1/candles",
        params={"instrument_id": "mock:stock:ACME", "tf": "1M", "limit": 500},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    contract_validate("market.schema.json", "CandleBatch", body)
    assert 0 < len(body["candles"]) < 500
    first_start = datetime.datetime.fromtimestamp(
        body["candles"][0]["start_ms"] / 1000, datetime.UTC
    )
    assert first_start.isoformat() == "1990-01-02T14:30:00+00:00"
    assert body["feed_state"] == "DEMO"
    indicator = demo_client.get(
        "/v1/indicators/series",
        params={
            "instrument_id": "mock:stock:ACME",
            "tf": "1M",
            "plugin": "ema",
            "from_ms": body["candles"][0]["start_ms"],
        },
    )
    assert indicator.status_code == 200, indicator.text
    contract_validate(
        "indicators.schema.json", "IndicatorSeries", indicator.json()
    )
    assert indicator.json()["points"]


def test_demo_endpoints_are_contract_valid(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """Every DEMO response conforms to its JSON Schema."""
    btc = "mock:perp:BTCUSD"
    checks: list[tuple[str, dict[str, str | int], str, str]] = [
        ("/v1/venues", {}, "market.schema.json", "VenueStatusList"),
        (
            "/v1/instruments",
            {"q": "btc"},
            "market.schema.json",
            "InstrumentList",
        ),
        (
            "/v1/candles",
            {"instrument_id": btc, "tf": "5m", "limit": 50},
            "market.schema.json",
            "CandleBatch",
        ),
        (
            "/v1/market/snapshot",
            {"instrument_id": btc},
            "market.schema.json",
            "MarketSnapshot",
        ),
        (
            "/v1/market/orderbook",
            {"instrument_id": btc, "depth": 10},
            "market.schema.json",
            "OrderBook",
        ),
        (
            "/v1/market/qualification",
            {},
            "qualification.schema.json",
            "QualificationReport",
        ),
    ]
    for path, params, schema, definition in checks:
        response = demo_client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        contract_validate(schema, definition, response.json())


def test_candles_carry_demo_provenance(
    demo_client: testclient.TestClient,
) -> None:
    """DEMO data is always labelled DEMO."""
    body = demo_client.get(
        "/v1/candles",
        params={
            "instrument_id": "mock:perp:ETHUSD",
            "tf": "1m",
            "limit": 20,
        },
    ).json()
    assert body["feed_state"] == "DEMO"
    assert {c["feed_state"] for c in body["candles"]} == {"DEMO"}
    assert body["complete"] is True
    assert len(body["candles"]) == 20


def test_qualification_reports_gate_evidence(
    demo_client: testclient.TestClient,
) -> None:
    """Every record exposes observed values, thresholds and reasons."""
    body = demo_client.get(
        "/v1/market/qualification", params={"venue": "mock"}
    ).json()
    statuses = {r["instrument_id"]: r for r in body["records"]}
    assert statuses["mock:perp:BTCUSD"]["status"] == "QUALIFIED"
    assert statuses["mock:perp:ETHUSD"]["risk_tags"] == ["WATCH"]
    rejected = statuses["mock:perp:SOLUSD"]
    assert rejected["status"] == "REJECTED"
    spread = next(g for g in rejected["gates"] if g["gate"] == "SPREAD_PCT")
    assert spread["passed"] is False and spread["comparator"] == "lt"
    only = demo_client.get(
        "/v1/market/qualification", params={"status": "REJECTED"}
    ).json()
    assert [r["instrument_id"] for r in only["records"]] == ["mock:perp:SOLUSD"]


def test_validation_and_not_found_errors(
    demo_client: testclient.TestClient,
) -> None:
    """Bad input yields actionable contract errors."""
    _error(
        demo_client.get(
            "/v1/candles", params={"instrument_id": "BTCUSDT", "tf": "5m"}
        ),
        422,
        "VALIDATION_FAILED",
    )
    _error(
        demo_client.get(
            "/v1/candles",
            params={"instrument_id": "mock:perp:BTCUSD", "tf": "7m"},
        ),
        422,
        "VALIDATION_FAILED",
    )
    _error(
        demo_client.get(
            "/v1/candles",
            params={"instrument_id": "mock:perp:DOGEUSD", "tf": "5m"},
        ),
        404,
        "NOT_FOUND",
    )
    _error(
        demo_client.get(
            "/v1/candles",
            params={"instrument_id": "binance:perp:BTCUSDT", "tf": "5m"},
        ),
        404,
        "NOT_FOUND",
    )
    _error(
        demo_client.get(
            "/v1/market/orderbook",
            params={"instrument_id": "mock:perp:BTCUSD", "depth": 0},
        ),
        422,
        "VALIDATION_FAILED",
    )
    _error(
        demo_client.get("/v1/instruments", params={"venue": "unknown-source"}),
        422,
        "VALIDATION_FAILED",
    )
    _error(demo_client.get("/v1/candles"), 422, "VALIDATION_FAILED")
    _error(demo_client.get("/v1/nope"), 404, "NOT_FOUND")


def test_cors_allows_configured_origin_only(
    demo_client: testclient.TestClient,
) -> None:
    """Only configured browser origins are allowed."""
    ok = demo_client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == (
        "http://localhost:3000"
    )
    denied = demo_client.get(
        "/health", headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in denied.headers


def test_all_venues_served_from_captured_fixtures(
    venue_client: tuple[testclient.TestClient, respx.Router],
    contract_validate: Validate,
) -> None:
    """Each crypto venue serves candles and snapshots from captured fixtures."""
    client, _ = venue_client
    venues = client.get("/v1/venues").json()
    contract_validate("market.schema.json", "VenueStatusList", venues)
    assert {v["capabilities"]["venue"] for v in venues["venues"]} == {
        "binance",
        "hyperliquid",
        "lighter",
        "bitget",
        "mexc",
        "twse",
        "tpex",
        "nasdaq",
        "nyse",
        "cboe",
        "treasury",
    }
    for instrument in (
        "binance:perp:BTCUSDT",
        "hyperliquid:perp:BTC",
        "lighter:perp:BTC",
        "bitget:perp:BTCUSDT",
        "mexc:perp:BTC_USDT",
    ):
        batch = client.get(
            "/v1/candles",
            params={"instrument_id": instrument, "tf": "5m", "limit": 3},
        )
        assert batch.status_code == 200, (instrument, batch.text)
        contract_validate("market.schema.json", "CandleBatch", batch.json())
        assert batch.json()["feed_state"] == "LIVE"
        snapshot = client.get(
            "/v1/market/snapshot", params={"instrument_id": instrument}
        )
        assert snapshot.status_code == 200, (instrument, snapshot.text)


def test_search_never_merges_same_symbol_across_venues(
    venue_client: tuple[testclient.TestClient, respx.Router],
) -> None:
    """BTC on each venue is a separate instrument."""
    client, _ = venue_client
    ids = [
        i["instrument_id"]
        for i in client.get("/v1/instruments", params={"q": "BTC"}).json()[
            "instruments"
        ]
    ]
    for expected in (
        "binance:perp:BTCUSDT",
        "hyperliquid:perp:BTC",
        "lighter:perp:BTC",
        "bitget:perp:BTCUSDT",
        "mexc:perp:BTC_USDT",
    ):
        assert expected in ids
    assert len(ids) == len(set(ids))


def test_weekly_bars_are_aggregated_not_relabelled(
    venue_client: tuple[testclient.TestClient, respx.Router],
) -> None:
    """Hyperliquid 1w is folded from 1d on Mondays, not its Thursday weeks."""
    client, _ = venue_client
    response = client.get(
        "/v1/candles",
        params={"instrument_id": "hyperliquid:perp:BTC", "tf": "1w"},
    )
    assert response.status_code == 200, response.text
    candles = response.json()["candles"]
    assert candles
    monday_anchor = 4 * 86_400_000  # 1970-01-05 was a Monday.
    for candle in candles:
        assert (candle["start_ms"] - monday_anchor) % (7 * 86_400_000) == 0
        assert candle["source"] == "hyperliquid.rest.agg1d"


@pytest.mark.parametrize(
    ("side_effect", "status", "code", "retryable"),
    [
        (httpx.Response(429), 429, "RATE_LIMITED", True),
        (httpx.ReadTimeout("slow"), 503, "VENUE_UNAVAILABLE", True),
        (httpx.Response(502), 503, "VENUE_UNAVAILABLE", True),
    ],
)
def test_venue_failures_map_to_contract_errors(
    venue_client: tuple[testclient.TestClient, respx.Router],
    side_effect: httpx.Response | Exception,
    status: int,
    code: str,
    retryable: bool,
    binance_handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Rate limits and outages are retryable errors recorded in venue health."""
    client, router = venue_client
    original = binance_handler

    def failing(request: httpx.Request) -> httpx.Response:
        """Fails klines requests and serves everything else.

        Args:
            request: Request to the Binance host.

        Returns:
            The configured failure response for klines, else the fixture.

        Raises:
            Exception: The configured transport error, for klines.
        """
        if not request.url.path.endswith("/klines"):
            return original(request)
        if isinstance(side_effect, Exception):
            raise side_effect
        return side_effect

    router.route(host="fapi.binance.com").mock(side_effect=failing)
    response = client.get(
        "/v1/candles",
        params={
            "instrument_id": "binance:perp:BTCUSDT",
            "tf": "5m",
            "limit": 3,
        },
    )
    _error(response, status, code)
    assert response.json()["retryable"] is retryable
    venues = client.get("/v1/venues").json()["venues"]
    binance = next(v for v in venues if v["capabilities"]["venue"] == "binance")
    assert binance["health"]["last_error"]


def _receive_until(ws: Any, kind: str, limit: int = 20) -> dict[str, Any]:
    """Receives envelopes until one of ``kind`` arrives.

    Envelopes of other types are read and dropped.

    Args:
        ws: WebSocket session.
        kind: Envelope type.
        limit: Maximum frames to read.

    Returns:
        The matching envelope as decoded JSON.

    Raises:
        AssertionError: If not received within ``limit`` frames.
    """
    for _ in range(limit):
        envelope: dict[str, Any] = json.loads(ws.receive_text())
        if envelope["type"] == kind:
            return envelope
    raise AssertionError(f"no {kind} envelope")


def test_ws_streams_contract_valid_candles(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """Subscribing to candles streams sequenced DEMO candles."""
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        {
                            "channel": "candles",
                            "instrument_id": "mock:perp:BTCUSD",
                            "timeframe": "1m",
                        },
                        {"channel": "feed_health"},
                    ],
                }
            )
        )
        subscribed = _receive_until(ws, "subscribed")
        contract_validate("stream.schema.json", "StreamEnvelope", subscribed)
        assert subscribed["sequence"] == 0
        candle = _receive_until(ws, "candle.updated")
        contract_validate("stream.schema.json", "StreamEnvelope", candle)
        contract_validate("market.schema.json", "Candle", candle["payload"])
        assert candle["sequence"] > 0
        assert candle["channel"] == "candles:mock:perp:BTCUSD:1m"
        ws.send_text(
            json.dumps(
                {
                    "action": "unsubscribe",
                    "channels": [
                        {
                            "channel": "candles",
                            "instrument_id": "mock:perp:BTCUSD",
                            "timeframe": "1m",
                        }
                    ],
                }
            )
        )
        after = _receive_until(ws, "subscribed")
        assert after["payload"] == {"channels": ["feed_health"]}


def test_ws_rejects_bad_frames_and_actions(
    demo_client: testclient.TestClient,
) -> None:
    """Malformed input yields error envelopes, not disconnects."""
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text("not json")
        assert _receive_until(ws, "error")["payload"]["code"] == (
            "VALIDATION_FAILED"
        )
        ws.send_text(json.dumps({"action": "dance"}))
        assert _receive_until(ws, "error")
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        {
                            "channel": "candles",
                            "instrument_id": "mock:perp:BTCUSD",
                        }
                    ],
                }
            )
        )
        assert _receive_until(ws, "error")
        ws.send_text(json.dumps({"action": "ping"}))
        assert _receive_until(ws, "heartbeat")


def test_ws_signals_require_token_and_are_tenant_scoped(
    demo_client: testclient.TestClient,
    demo_container: bootstrap.Container,
    tokens: tuple[str, str],
) -> None:
    """Signals need a token and never cross tenants."""
    token_a, token_b = tokens
    with demo_client.websocket_connect("/v1/stream/market") as anon:
        anon.send_text(
            json.dumps(
                {"action": "subscribe", "channels": [{"channel": "signals"}]}
            )
        )
        error = _receive_until(anon, "error")
        assert error["payload"]["code"] == "UNAUTHENTICATED"
    url = "/v1/stream/market?token="
    with (
        demo_client.websocket_connect(url + token_a) as ws_a,
        demo_client.websocket_connect(url + token_b) as ws_b,
    ):
        for ws in (ws_a, ws_b):
            ws.send_text(
                json.dumps(
                    {
                        "action": "subscribe",
                        "channels": [{"channel": "signals"}],
                    }
                )
            )
            assert _receive_until(ws, "subscribed")["payload"] == {
                "channels": ["signals"]
            }
        hub = demo_container.stream_hub
        demo_client.portal.call(  # type: ignore[union-attr]  # the portal is set while the client is open
            lambda: hub.publish(
                "signals",
                "signal.created",
                {"for": "tenant-02"},
                tenant_id="tenant-02",
            )
        )
        demo_client.portal.call(  # type: ignore[union-attr]  # the portal is set while the client is open
            lambda: hub.publish(
                "signals",
                "signal.created",
                {"for": "tenant-01"},
                tenant_id="tenant-01",
            )
        )
        got_a = _receive_until(ws_a, "signal.created")
        got_b = _receive_until(ws_b, "signal.created")
        assert got_a["payload"] == {"for": "tenant-01"}
        assert got_b["payload"] == {"for": "tenant-02"}


def test_ws_invalid_token_is_rejected(
    demo_client: testclient.TestClient,
) -> None:
    """A bad token closes the socket with an error envelope."""
    with demo_client.websocket_connect("/v1/stream/market?token=wrong") as ws:
        assert json.loads(ws.receive_text())["payload"]["code"] == (
            "UNAUTHENTICATED"
        )


def test_ws_resume_replays_or_requires_snapshot(
    demo_client: testclient.TestClient, demo_container: bootstrap.Container
) -> None:
    """``last_sequence`` resumes within retention, else snapshot_required."""
    hub = demo_container.stream_hub

    def burst() -> int:
        """Publishes three health envelopes.

        Returns:
            The sequence number of the first one.
        """
        first = hub.publish("feed_health", "feed.health", {"n": 1}).sequence
        hub.publish("feed_health", "feed.health", {"n": 2})
        hub.publish("feed_health", "feed.health", {"n": 3})
        return first

    first = demo_client.portal.call(burst)  # type: ignore[union-attr]  # the portal is set while the client is open
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [{"channel": "feed_health"}],
                    "last_sequence": first,
                }
            )
        )
        _receive_until(ws, "subscribed")
        replayed = [
            e
            for e in (json.loads(ws.receive_text()) for _ in range(4))
            if e["sequence"] > 0
        ]
        assert [e["payload"] for e in replayed][-2:] == [{"n": 2}, {"n": 3}]

    def flood() -> None:
        """Publishes five envelopes more than the hub retains."""
        for i in range(demo_container.settings.stream_retention + 5):
            hub.publish("qualification", "qualification.changed", {"i": i})

    demo_client.portal.call(flood)  # type: ignore[union-attr]  # the portal is set while the client is open
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [{"channel": "feed_health"}],
                    "last_sequence": 1,
                }
            )
        )
        assert _receive_until(ws, "snapshot_required")


def test_ws_resume_after_server_restart_requires_snapshot(
    demo_client: testclient.TestClient,
) -> None:
    """A ``last_sequence`` past the hub's newest means a restart."""
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [{"channel": "feed_health"}],
                    "last_sequence": 10**9,
                }
            )
        )
        envelope = _receive_until(ws, "snapshot_required")
    assert "restarted" in envelope["payload"]["reason"]
    assert envelope["payload"]["channels"] == ["feed_health"]


def test_ws_native_mode_on_rest_only_venue_is_unsupported(
    venue_client: tuple[testclient.TestClient, respx.Router],
) -> None:
    """NATIVE_WS for Lighter is refused instead of silently polling."""
    client, _ = venue_client
    with client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        {
                            "channel": "candles",
                            "instrument_id": "lighter:perp:BTC",
                            "timeframe": "5m",
                            "feed_mode": "NATIVE_WS",
                        }
                    ],
                }
            )
        )
        error = _receive_until(ws, "error")
        assert error["payload"]["code"] == "UNSUPPORTED"
        assert _receive_until(ws, "subscribed")["payload"] == {"channels": []}


@pytest.mark.parametrize("tf", ["1s", "5s", "30s", "1M"])
def test_seconds_and_monthly_candles_are_served_for_demo(
    demo_client: testclient.TestClient, contract_validate: Validate, tf: str
) -> None:
    """DEMO serves seconds (from synthetic trades) and calendar months."""
    response = demo_client.get(
        "/v1/candles",
        params={"instrument_id": "mock:perp:BTCUSD", "tf": tf, "limit": 12},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    contract_validate("market.schema.json", "CandleBatch", body)
    assert body["feed_state"] == "DEMO"
    assert body["candles"]
    assert all(c["timeframe"] == tf for c in body["candles"])
    assert all(c["taker_buy_base_volume"] is not None for c in body["candles"])


def test_venue_capabilities_expose_trade_built_timeframes(
    venue_client: tuple[testclient.TestClient, respx.Router],
    contract_validate: Validate,
) -> None:
    """Every venue lists its trade-built seconds and taker-volume flag."""
    client, _ = venue_client
    body = client.get("/v1/venues").json()
    contract_validate("market.schema.json", "VenueStatusList", body)
    caps = {
        v["capabilities"]["venue"]: v["capabilities"] for v in body["venues"]
    }
    no_trades = {"twse", "tpex", "treasury"}  # Snapshots / published rates.
    for venue, cap in caps.items():
        if venue in no_trades:
            assert cap["trade_built_timeframes"] == [], venue
            assert not {"1s", "5s", "15s", "30s"} & set(cap["timeframes"])
            continue
        assert cap["trade_built_timeframes"] == ["1s", "5s", "15s", "30s"], (
            venue
        )
        assert cap["market_data"]["native_ws_trades"], venue
    assert caps["twse"]["calendar"] == caps["tpex"]["calendar"] == "XTAI"
    assert caps["nasdaq"]["requires_credentials"] is True
    assert caps["treasury"]["calendar"] == "USBOND"
    assert caps["binance"]["market_data"]["taker_volume"] is True
    assert caps["bitget"]["market_data"]["taker_volume"] is False
    assert "1M" in caps["binance"]["timeframes"]
    assert caps["binance"]["aggregated_timeframes"] == ["10m", "45m", "8h"]
    for venue in ("hyperliquid", "lighter"):
        assert "1M" in caps[venue]["timeframes"], venue
        assert "1M" in caps[venue]["aggregated_timeframes"], venue
    assert caps["hyperliquid"]["aggregated_timeframes"] == [
        "10m",
        "45m",
        "6h",
        "8h",
        "1w",
        "1M",
    ]
    for venue in ("bitget", "mexc", "hyperliquid", "lighter"):
        assert caps[venue]["market_data"]["trade_delta"] is True, venue
    assert caps["binance"]["market_data"]["trade_delta"] is False


def test_aggregated_monthly_and_unbacked_seconds_are_explicit(
    venue_client: tuple[testclient.TestClient, respx.Router],
    contract_validate: Validate,
) -> None:
    """Hyperliquid 1M is aggregated from 1d; its seconds history is a gap."""
    client, _ = venue_client
    monthly = client.get(
        "/v1/candles",
        params={"instrument_id": "hyperliquid:perp:BTC", "tf": "1M"},
    )
    assert monthly.status_code == 200, monthly.text
    months = monthly.json()
    contract_validate("market.schema.json", "CandleBatch", months)
    july = 1_782_864_000_000  # 2026-07-01, the first fixture day.
    by_start = {c["start_ms"]: c for c in months["candles"]}
    days = [
        row
        for row in _fixture_rows("hyperliquid", "candles_1d")
        if july <= int(row["t"]) < 1_785_542_400_000
    ]
    assert len(days) == 31
    assert decimal.Decimal(by_start[july]["open"]) == decimal.Decimal(
        str(days[0]["o"])
    )
    assert decimal.Decimal(by_start[july]["close"]) == decimal.Decimal(
        str(days[-1]["c"])
    )
    assert decimal.Decimal(by_start[july]["base_volume"]) == sum(
        decimal.Decimal(str(day["v"])) for day in days
    )
    assert by_start[july]["source"] == "hyperliquid.rest.agg1d"
    assert months["candles"][-1]["is_final"] is False
    seconds = client.get(
        "/v1/candles",
        params={"instrument_id": "hyperliquid:perp:BTC", "tf": "5s"},
    )
    assert seconds.status_code == 200, seconds.text
    body = seconds.json()
    assert body["candles"] == []
    assert body["complete"] is False
    assert body["source"] == "hyperliquid.trades"


def test_ws_streams_demo_seconds_candles(
    demo_client: testclient.TestClient, contract_validate: Validate
) -> None:
    """A 1s candle subscription streams bars built from DEMO trades."""
    with demo_client.websocket_connect("/v1/stream/market") as ws:
        ws.send_text(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        {
                            "channel": "candles",
                            "instrument_id": "mock:perp:ETHUSD",
                            "timeframe": "1s",
                        }
                    ],
                }
            )
        )
        _receive_until(ws, "subscribed")
        candle = _receive_until(ws, "candle.updated", limit=200)
        contract_validate("market.schema.json", "Candle", candle["payload"])
        assert candle["channel"] == "candles:mock:perp:ETHUSD:1s"
        assert candle["payload"]["source"] == "mock.trades"
