"""System test: the real uvicorn process with background loops.

Starts the execution engine as a separate process (file SQLite, static
DEMO market, background ticker/outbox/reconciler) and drives it over real
HTTP and WebSocket connections.
"""

from __future__ import annotations

import asyncio
import collections.abc
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import httpx
import pytest
import websockets

_TOKEN = "system-token"


def _free_port() -> int:
    """Finds a free TCP port.

    Returns:
        Port number.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(
    tmp_path_factory: pytest.TempPathFactory,
) -> collections.abc.Iterator[str]:
    """Runs the service in a subprocess.

    Args:
        tmp_path_factory: Temporary directory factory.

    Yields:
        Base URL.
    """
    port = _free_port()
    workdir = tmp_path_factory.mktemp("exec")
    env = {
        **os.environ,
        "JOSEPH_ENV": "development",
        "JOSEPH_DATABASE_URL": f"sqlite:///{workdir / 'execution.db'}",
        "JOSEPH_DEV_TOKENS": f"{_TOKEN}:tenant-sys:user-sys",
        "JOSEPH_SERVICE_TOKEN": "system-service",
        "JOSEPH_TICK_INTERVAL_MS": "200",
        "JOSEPH_RECONCILE_INTERVAL_MS": "500",
        "JOSEPH_DEMO_WALK_BPS": "20",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "joseph_execution.bootstrap:create_application",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=pathlib.Path(__file__).resolve().parents[2],
        # A file, not an unread pipe: a full pipe buffer would block the
        # server, and the log is shown if startup fails.
        stdout=(workdir / "server.log").open("wb"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    # Cold CI runners compile bytecode on first import; allow 60 s.
    deadline = time.monotonic() + 60
    started = False
    while time.monotonic() < deadline and process.poll() is None:
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                started = True
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    if not started:
        process.kill()
        output = (workdir / "server.log").read_text()
        pytest.fail(f"service did not start: {output}")
    yield base
    process.terminate()
    process.wait(timeout=10)


def _headers(key: str | None = None) -> dict[str, str]:
    """Builds headers.

    Args:
        key: Idempotency key.

    Returns:
        Headers.
    """
    result = {"Authorization": f"Bearer {_TOKEN}"}
    if key:
        result["Idempotency-Key"] = key
    return result


def _body(**overrides: object) -> dict[str, object]:
    """Builds an intent body for the system tenant.

    Args:
        **overrides: Overrides.

    Returns:
        Body.
    """
    body: dict[str, object] = {
        "account_id": "acct-paper-tenant-sys",
        "instrument_id": "mock:perp:BTCUSD",
        "side": "buy",
        "position_effect": "open",
        "order_type": "market",
        "quantity_base": "0.010",
        "time_in_force": "IOC",
        "reduce_only": False,
        "leverage": "2",
        "mode": "PAPER",
        "source": "MANUAL_TICKET",
    }
    body.update(overrides)
    return body


def test_health_reports_safe_defaults(server: str) -> None:
    """The running service starts with live trading disabled."""
    health = httpx.get(f"{server}/health").json()
    assert health["live_trading_enabled"] is False
    assert health["status"] == "ok"


def test_end_to_end_trading_flow_over_real_sockets(server: str) -> None:
    """Order, stream events, background fills, close and audit."""
    asyncio.run(_flow(server))


async def _flow(server: str) -> None:
    """Drives the running service.

    Args:
        server: Base URL.
    """
    ws_url = server.replace("http", "ws") + f"/v1/stream/user?token={_TOKEN}"
    async with (
        httpx.AsyncClient(base_url=server, timeout=10) as client,
        websockets.connect(ws_url) as ws,
    ):
        await ws.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        {"channel": "orders"},
                        {"channel": "fills"},
                        {"channel": "positions"},
                    ],
                }
            )
        )
        subscribed = json.loads(await ws.recv())
        assert subscribed["type"] == "subscribed"
        preview = (
            await client.post(
                "/v1/orders/preview", json=_body(), headers=_headers()
            )
        ).json()
        assert preview["allowed"] is True
        market = await client.post(
            "/v1/orders/intents",
            json={
                **preview["normalized"],
                "preview_token": preview["preview_token"],
            },
            headers=_headers("sys-market"),
        )
        assert market.status_code == 201
        assert market.json()["orders"][0]["state"] == "FILLED"
        types = set()
        while not {"order.updated", "fill.created"} <= types:
            envelope = json.loads(await asyncio.wait_for(ws.recv(), 5))
            types.add(envelope["type"])
        resting = await client.post(
            "/v1/orders/intents",
            json=_body(
                order_type="limit", limit_price="94990.0", time_in_force="GTC"
            ),
            headers=_headers("sys-limit"),
        )
        order_id = resting.json()["orders"][0]["order_id"]
        deadline = time.monotonic() + 20
        state = resting.json()["orders"][0]["state"]
        while state != "FILLED" and time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            orders = (
                await client.get("/v1/orders", headers=_headers())
            ).json()["orders"]
            state = next(
                o["state"] for o in orders if o["order_id"] == order_id
            )
        assert state in ("FILLED", "ACKNOWLEDGED")
        if state == "ACKNOWLEDGED":
            cancel = await client.post(
                f"/v1/orders/{order_id}/cancel", headers=_headers()
            )
            assert cancel.json()["state"] in ("CANCELED", "FILLED")
        positions = (
            await client.get("/v1/positions", headers=_headers())
        ).json()
        position = positions["positions"][0]
        close = await client.post(
            f"/v1/positions/{position['position_id']}/close",
            json={"fraction": "1", "order_type": "market"},
            headers=_headers("sys-close"),
        )
        assert close.status_code == 201, close.json()
        await asyncio.sleep(0.8)
        after = (await client.get("/v1/positions", headers=_headers())).json()[
            "positions"
        ]
        assert after == []
        audit = (
            await client.get("/v1/audit/events", headers=_headers())
        ).json()
        assert audit["chain_valid"] is True
        assert len(audit["events"]) >= 6
        metrics = await client.get(
            "/metrics", headers={"X-Joseph-Service-Token": "system-service"}
        )
        assert "joseph_reconcile_lag_ms" in metrics.text
