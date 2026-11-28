# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in indicator scripts served through the script run routes."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator

import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.domain import clock
from joseph.platform import config

HEADERS = {"Authorization": "Bearer study-a"}
"""Ordinary authenticated user."""

DEFAULT_STUDIES = (
    "builtin:ema",
    "builtin:volume_delta",
    "builtin:pivot_points",
)
"""Studies of a new chart."""


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Serves a DEMO engine with the isolated runtime disabled.

    Yields:
        Started application client.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        script_runtime="disabled",
        dev_tokens=config.parse_dev_tokens("study-a:tenant-a:user-a"),
    )
    container = bootstrap.build_container(
        settings, clock=clock.ManualClock(1_795_277_530_000)
    )
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection


@pytest.mark.parametrize(
    "script_id",
    [
        "builtin:sma",
        "builtin:ema",
        "builtin:bollinger_bands",
        "builtin:rsi",
        "builtin:atr",
        "builtin:volume_delta",
        "builtin:pivot_points",
        "builtin:anchored_vwap",
        "builtin:regression_channel",
    ],
)
def test_every_builtin_runs_on_demo_candles(
    client: testclient.TestClient, script_id: str
) -> None:
    """Each built-in indicator returns a validated result on real routes.

    Args:
        client: Application client.
        script_id: Built-in catalog id.
    """
    response = client.post(
        f"/v1/scripts/{script_id}/run",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "5m",
            "limit": 600,
            "include_open": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "indicator"
    assert body["plots"] or body["visuals"]


def test_pivot_points_draw_labelled_levels(
    client: testclient.TestClient,
) -> None:
    """Pivot points draw seven labelled lines per period instead of AOIs.

    Args:
        client: Application client.
    """
    body = client.post(
        "/v1/scripts/builtin:pivot_points/run",
        headers=HEADERS,
        json={"instrument_id": "mock:perp:BTCUSD", "tf": "1h", "limit": 400},
    ).json()
    lines = [row for row in body["visuals"] if row["kind"] == "line"]
    labels = [row for row in body["visuals"] if row["kind"] == "label"]
    assert lines
    assert len(lines) % 7 == 0
    assert len(labels) == len(lines)


def test_volume_delta_labels_its_method(
    client: testclient.TestClient,
) -> None:
    """Volume delta publishes a histogram and names its method.

    Args:
        client: Application client.
    """
    body = client.post(
        "/v1/scripts/builtin:volume_delta/run",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "5m",
            "limit": 300,
            "window_from_ms": 1_795_270_000_000,
        },
    ).json()
    assert body["overlay"] is False
    assert body["plot_styles"]["Delta %"]["style"] == "histogram"
    assert body["plot_styles"]["Coverage %"]["display"] == "data_window"
    table = next(row for row in body["visuals"] if row["kind"] == "table")
    assert table["data"]["rows"][0][0] == "Method"


def test_batched_default_studies_are_fast(
    client: testclient.TestClient,
) -> None:
    """The default chart studies compute in one batch well under a second.

    Args:
        client: Application client.
    """
    request = {
        "instrument_id": "mock:perp:BTCUSD",
        "tf": "5m",
        "limit": 2000,
        "include_open": True,
        "runs": [{"script_id": script_id} for script_id in DEFAULT_STUDIES],
    }
    client.post("/v1/scripts/run-batch", headers=HEADERS, json=request)
    started = time.perf_counter()
    response = client.post(
        "/v1/scripts/run-batch",
        headers=HEADERS,
        json={**request, "inputs": {"length": 21}},
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [row["script_id"] for row in results] == list(DEFAULT_STUDIES)
    assert all(row["error"] is None for row in results), results
    assert results[0]["result"]["bars_count"] == 2000
    assert elapsed < 1.0


def test_batch_isolates_failing_studies(client: testclient.TestClient) -> None:
    """One failing study reports its error without failing the batch.

    Args:
        client: Application client.
    """
    response = client.post(
        "/v1/scripts/run-batch",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "1d",
            "limit": 50,
            "runs": [
                {"script_id": "builtin:ema"},
                {
                    "script_id": "builtin:pivot_points",
                    "inputs": {"period": "1d"},
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    first, second = response.json()["results"]
    assert first["error"] is None
    assert second["result"] is None
    assert "shorter than the 1d period" in second["error"]
