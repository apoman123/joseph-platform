# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Catalog routes and in-process built-in script runs without Docker."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.domain import clock
from joseph.platform import config

HEADERS = {"Authorization": "Bearer catalog-a"}
"""Ordinary authenticated user."""


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
        strategy_activations_enabled=False,
        script_runtime="disabled",
        dev_tokens=config.parse_dev_tokens("catalog-a:tenant-a:user-a"),
    )
    container = bootstrap.build_container(
        settings, clock=clock.ManualClock(1_795_277_530_000)
    )
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection


def test_catalog_lists_builtins_with_public_source(
    client: testclient.TestClient,
) -> None:
    """Built-ins are listed with inputs; single reads include source."""
    listing = client.get(
        "/v1/scripts/catalog", params={"kind": "indicator"}, headers=HEADERS
    )
    assert listing.status_code == 200
    scripts = {row["script_id"]: row for row in listing.json()["scripts"]}
    assert scripts["builtin:ema"]["inputs"]["length"]["default"] == 9
    assert scripts["builtin:ema"]["source_available"] is True
    assert "source" not in scripts["builtin:ema"] or (
        scripts["builtin:ema"]["source"] is None
    )
    single = client.get("/v1/scripts/catalog/builtin:ema", headers=HEADERS)
    assert "class ExponentialMovingAverage" in single.json()["source"]
    assert (
        client.get(
            "/v1/scripts/catalog/builtin:nope", headers=HEADERS
        ).status_code
        == 404
    )


def test_builtin_runs_in_process_without_docker(
    client: testclient.TestClient,
) -> None:
    """A built-in indicator runs although tenant source execution is off."""
    response = client.post(
        "/v1/scripts/builtin:ema/run",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "1m",
            "limit": 50,
            "inputs": {"length": 5},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["script_id"] == "builtin:ema"
    assert body["version"] == 0
    assert body["kind"] == "indicator"
    points = body["plots"][0]["points"]
    assert len(points) == body["bars_count"]
    assert all(point["value"] is not None for point in points)
    again = client.post(
        "/v1/scripts/builtin:ema/run",
        headers=HEADERS,
        json={
            "instrument_id": "mock:perp:BTCUSD",
            "tf": "1m",
            "limit": 50,
            "inputs": {"length": 5},
        },
    )
    assert again.json()["plots"] == body["plots"]
