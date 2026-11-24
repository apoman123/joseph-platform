# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Cross-service system tests.

Signal → deployment → paper order, streams, security boundaries and a
latency smoke check.
"""

from __future__ import annotations

import asyncio
import decimal
import json
import pathlib
import statistics
import time
import uuid
from collections.abc import Callable

import httpx
import pytest
import websockets

from tests import conftest

MOCK_BTC = "mock:perp:BTCUSD"
PAPER = "acct-paper-tenant-a"
_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
)


def _signal(
    strategy_version_id: str, entry: str, stop: str, target: str
) -> dict[str, object]:
    """Builds a contract Signal for the DEMO instrument.

    Args:
        strategy_version_id: Version the deployment is bound to.
        entry: Entry price.
        stop: Stop price.
        target: Target price.

    Returns:
        Signal payload.
    """
    body = json.loads(
        (_FIXTURES / "signal.fixture.json").read_text(encoding="utf-8")
    )
    signal = dict(body["valid"][0])
    signal.update(
        {
            "signal_id": str(uuid.uuid4()),
            "tenant_id": "tenant-a",
            "strategy_id": strategy_version_id.split(":", maxsplit=1)[0],
            "strategy_version_id": strategy_version_id,
            "instrument_id": MOCK_BTC,
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "mode": "PAPER",
            "data_source": "DEMO",
            "bar_close_ms": int(time.time() * 1000),
            "evaluated_at_ms": int(time.time() * 1000),
        }
    )
    signal["dedupe_key"] = f"system|{signal['signal_id']}"
    return signal


def test_signal_creates_exactly_one_paper_order(
    services: dict[str, str],
    execution: httpx.Client,
    validate: Callable[[str, str, object], None],
) -> None:
    """A PAPER deployment turns one signal into one sized order, once."""
    version_id = "system-forward:v1"
    deployment = execution.post(
        "/v1/deployments",
        json={
            "strategy_version_id": version_id,
            "account_id": PAPER,
            "mode": "PAPER",
            "limits": {
                "max_notional": "20000",
                "max_open_positions": 3,
                "max_daily_loss": "1000",
                "allowed_instruments": [MOCK_BTC],
                "max_leverage": "3",
            },
            "sizing": {"model": "RISK_PCT", "risk_pct": "0.5"},
        },
    )
    assert deployment.status_code in (200, 201), deployment.text
    validate("execution.schema.json", "Deployment", deployment.json())
    # Price the signal around the live DEMO mark: the synthetic price walks
    # with time, and fixed prices would put the stop on the wrong side.
    probe = execution.post(
        "/v1/orders/preview",
        json={
            "account_id": PAPER,
            "instrument_id": MOCK_BTC,
            "side": "buy",
            "position_effect": "open",
            "order_type": "market",
            "quantity_base": "0.001",
            "time_in_force": "IOC",
            "reduce_only": False,
            "mode": "PAPER",
            "source": "MANUAL_TICKET",
        },
    ).json()
    mark = decimal.Decimal(probe["mark_price"])
    tick = decimal.Decimal("0.1")
    signal = _signal(
        version_id,
        str(mark.quantize(tick)),
        str((mark * decimal.Decimal("0.99")).quantize(tick)),
        str((mark * decimal.Decimal("1.02")).quantize(tick)),
    )
    headers = {"X-Joseph-Service-Token": conftest.SERVICE_TOKEN}
    first = httpx.post(
        f"{services['execution']}/internal/v1/signals",
        json=signal,
        headers=headers,
        timeout=30,
    )
    assert first.status_code in (200, 201, 202), first.text
    results = first.json()["results"]
    assert [r.get("decision") for r in results] == ["ALLOW"], results
    again = httpx.post(
        f"{services['execution']}/internal/v1/signals",
        json=signal,
        headers=headers,
        timeout=30,
    )
    assert again.status_code in (200, 201, 202)
    orders = execution.get("/v1/orders", params={"account_id": PAPER}).json()
    linked = [
        o for o in orders["orders"] if o.get("signal_id") == signal["signal_id"]
    ]
    assert len({o["intent_id"] for o in linked}) == 1
    primary = [o for o in linked if o["role"] == "PRIMARY"]
    assert len(primary) == 1
    assert primary[0]["strategy_version_id"] == version_id
    assert {o["role"] for o in linked} <= {
        "PRIMARY",
        "STOP_LOSS",
        "TAKE_PROFIT",
    }


def test_internal_endpoint_requires_service_token(
    services: dict[str, str],
) -> None:
    """Forged signals without the service token are rejected."""
    response = httpx.post(
        f"{services['execution']}/internal/v1/signals",
        json=_signal("x:v1", "1", "0.5", "2"),
        timeout=10,
    )
    assert response.status_code in (401, 403)


def test_every_private_endpoint_requires_auth(
    anonymous: dict[str, httpx.Client],
) -> None:
    """Unauthenticated calls get 401 with a contract error body."""
    probes = [
        ("execution", "GET", "/v1/accounts"),
        ("execution", "GET", "/v1/orders"),
        ("execution", "POST", "/v1/orders/preview"),
        ("execution", "POST", "/v1/orders/intents"),
        ("execution", "GET", "/v1/audit/events"),
        ("execution", "POST", "/v1/risk/kill-switch"),
        ("market", "GET", "/v1/strategies"),
        ("market", "POST", "/v1/strategies"),
        ("market", "GET", "/v1/signals"),
    ]
    for service, method, path in probes:
        response = anonymous[service].request(method, path, json={})
        assert response.status_code == 401, (service, path)
        assert response.json()["code"] == "UNAUTHENTICATED"


async def _first_message(
    url: str, subscribe: dict[str, object], wanted: str, timeout_s: float = 20
) -> dict[str, object]:
    """Connects, subscribes and returns the first envelope of a type.

    Args:
        url: WebSocket URL.
        subscribe: Client message.
        wanted: Envelope type to wait for.
        timeout_s: Maximum wait.

    Returns:
        The envelope.
    """
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(subscribe))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            envelope = json.loads(raw)
            if envelope["type"] == wanted:
                return envelope
    raise AssertionError(f"no {wanted} envelope received")


async def test_market_stream_delivers_candles(
    services: dict[str, str], validate: Callable[[str, str, object], None]
) -> None:
    """The market WebSocket streams contract candle envelopes."""
    url = services["market"].replace("http", "ws") + "/v1/stream/market"
    envelope = await _first_message(
        url,
        {
            "action": "subscribe",
            "channels": [
                {
                    "channel": "candles",
                    "instrument_id": MOCK_BTC,
                    "timeframe": "1m",
                }
            ],
        },
        "candle.updated",
        timeout_s=90,
    )
    validate("stream.schema.json", "StreamEnvelope", envelope)
    validate("market.schema.json", "Candle", envelope["payload"])


async def test_user_stream_requires_token_and_streams_orders(
    services: dict[str, str],
    execution: httpx.Client,
) -> None:
    """The user stream rejects anonymous clients and streams order events."""
    base = services["execution"].replace("http", "ws") + "/v1/stream/user"
    with pytest.raises(Exception):  # noqa: B017 - any rejection.
        async with websockets.connect(base) as ws:
            await ws.send(
                json.dumps(
                    {"action": "subscribe", "channels": [{"channel": "orders"}]}
                )
            )
            await asyncio.wait_for(ws.recv(), timeout=5)
            await asyncio.wait_for(ws.recv(), timeout=5)
    url = f"{base}?token={conftest.TOKEN_A}"
    task = asyncio.create_task(
        _first_message(
            url,
            {
                "action": "subscribe",
                "channels": [{"channel": "orders"}],
            },
            "order.updated",
        )
    )
    await asyncio.sleep(1.0)
    probe = execution.post(
        "/v1/orders/preview",
        json={
            "account_id": PAPER,
            "instrument_id": MOCK_BTC,
            "side": "sell",
            "position_effect": "open",
            "order_type": "market",
            "quantity_base": "0.001",
            "time_in_force": "IOC",
            "reduce_only": False,
            "mode": "PAPER",
            "source": "MANUAL_TICKET",
        },
    ).json()
    resting = str(
        (
            decimal.Decimal(probe["mark_price"]) * decimal.Decimal("1.07")
        ).quantize(decimal.Decimal("0.1"))
    )
    body = {
        "account_id": PAPER,
        "instrument_id": MOCK_BTC,
        "side": "sell",
        "position_effect": "open",
        "order_type": "limit",
        "quantity_base": "0.001",
        "limit_price": resting,
        "time_in_force": "GTC",
        "reduce_only": False,
        "mode": "PAPER",
        "source": "MANUAL_TICKET",
    }
    preview = execution.post("/v1/orders/preview", json=body).json()
    submit = execution.post(
        "/v1/orders/intents",
        json={**body, "preview_token": preview["preview_token"]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    envelope = await task
    assert envelope["payload"]["account_id"] == PAPER
    execution.post(
        f"/v1/orders/{submit['orders'][0]['order_id']}/cancel", json={}
    )


@pytest.mark.load
def test_candle_endpoint_latency_smoke(market: httpx.Client) -> None:
    """200 sequential candle requests keep p95 under one second."""
    latencies = []
    for _ in range(200):
        started = time.perf_counter()
        response = market.get(
            "/v1/candles",
            params={"instrument_id": MOCK_BTC, "tf": "5m", "limit": 200},
        )
        latencies.append(time.perf_counter() - started)
        assert response.status_code == 200
    p95 = statistics.quantiles(latencies, n=20)[18]
    print(
        f"candles p50={statistics.median(latencies) * 1000:.1f}ms "
        f"p95={p95 * 1000:.1f}ms"
    )
    assert p95 < 1.0
