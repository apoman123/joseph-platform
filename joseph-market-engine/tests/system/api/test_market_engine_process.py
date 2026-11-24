# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System test: the market engine as a real process on a real socket.

Starts ``uvicorn joseph.bootstrap:create_application --factory`` in DEMO
mode, then exercises REST and the WebSocket stream over TCP exactly as the
terminal does.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from collections.abc import Iterator
from typing import TypeAlias

import httpx
import pytest
import websockets

Validate: TypeAlias = Callable[[str, str, object], None]
_PROJECT = pathlib.Path(__file__).resolve().parents[3]


def _free_port() -> int:
    """Finds a free TCP port on the loopback interface.

    Returns:
        A port that was free when checked; another process may take it.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Runs the engine in a subprocess.

    The server's output goes to ``server.log`` in a temporary directory; the
    handle is passed to the child and not closed by the test.

    Args:
        tmp_path_factory: Temporary directory factory.

    Yields:
        Base URL of the healthy server, which is stopped afterwards.

    Raises:
        RuntimeError: If the server does not become healthy.
    """
    port = _free_port()
    workdir = tmp_path_factory.mktemp("system")
    db_path = workdir / "market.db"
    log_path = workdir / "server.log"
    env = {
        **os.environ,
        "JOSEPH_DEMO_ONLY": "true",
        "JOSEPH_DEV_TOKENS": "sys-token:tenant-01:user-01",
        "JOSEPH_DATABASE_URL": f"sqlite:///{db_path}",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "joseph.bootstrap:create_application",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=_PROJECT,
        env=env,
        # A file, not an unread pipe: a full pipe buffer would block the
        # server, and the log is shown if startup fails.
        stdout=log_path.open("wb"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        # Cold CI runners compile bytecode on first import; allow 60 s.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError(
                "market engine did not start:\n" + log_path.read_text()
            )
        if process.poll() is not None:
            raise RuntimeError(
                "market engine exited early:\n" + log_path.read_text()
            )
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_rest_endpoints_over_tcp(
    server: str, contract_validate: Validate
) -> None:
    """Health, venues, candles, snapshot and qualification respond."""
    with httpx.Client(base_url=server, timeout=10) as client:
        assert client.get("/health").json()["status"] == "ok"
        contract_validate(
            "market.schema.json",
            "VenueStatusList",
            client.get("/v1/venues").json(),
        )
        batch = client.get(
            "/v1/candles",
            params={
                "instrument_id": "mock:perp:BTCUSD",
                "tf": "5m",
                "limit": 100,
            },
        )
        contract_validate("market.schema.json", "CandleBatch", batch.json())
        assert len(batch.json()["candles"]) == 100
        qualification = client.get("/v1/market/qualification").json()
        contract_validate(
            "qualification.schema.json", "QualificationReport", qualification
        )
        error = client.get(
            "/v1/candles", params={"instrument_id": "nope", "tf": "5m"}
        )
        assert error.status_code == 422
        assert error.headers["x-trace-id"] == error.json()["trace_id"]


def test_websocket_stream_over_tcp(
    server: str, contract_validate: Validate
) -> None:
    """A real WebSocket client receives sequenced candle envelopes."""

    async def scenario() -> list[dict[str, object]]:
        """Subscribes and collects envelopes until two candle updates arrive.

        Returns:
            Every envelope received, in order.
        """
        url = server.replace("http", "ws") + "/v1/stream/market?token=sys-token"
        async with websockets.connect(url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "channels": [
                            {
                                "channel": "candles",
                                "instrument_id": "mock:perp:ETHUSD",
                                "timeframe": "1m",
                            },
                            {"channel": "signals"},
                        ],
                    }
                )
            )
            got: list[dict[str, object]] = []
            while len([e for e in got if e["type"] == "candle.updated"]) < 2:
                got.append(json.loads(await asyncio.wait_for(ws.recv(), 10)))
            return got

    envelopes = asyncio.run(scenario())
    subscribed = next(e for e in envelopes if e["type"] == "subscribed")
    assert subscribed["payload"] == {
        "channels": ["candles:mock:perp:ETHUSD:1m", "signals"]
    }
    candles = [e for e in envelopes if e["type"] == "candle.updated"]
    for envelope in candles:
        contract_validate("stream.schema.json", "StreamEnvelope", envelope)
    sequences = [int(str(e["sequence"])) for e in candles]
    assert sequences == sorted(sequences)
