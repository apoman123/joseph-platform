# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Heatmap routes over recorded public sources, validated by the contracts."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Callable

import httpx
import jsonschema
import respx
from fastapi import testclient

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.platform import config

_SCHEMA = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[4]
        / "joseph-terminal/packages/contracts/schemas"
        / "python-scripts.schema.json"
    ).read_text(encoding="utf-8")
)


def _validate(definition: str, instance: object) -> None:
    """Validates a body against a generated heatmap contract definition.

    Args:
        definition: ``$defs`` entry such as ``HeatmapSnapshot``.
        instance: Response body.
    """
    jsonschema.Draft202012Validator(
        {**_SCHEMA, "$ref": f"#/$defs/{definition}"}
    ).validate(instance)


def test_heatmap_routes_follow_the_contract(
    heatmap_routes: Callable[[respx.Router], None],
    tokens: tuple[str, str],
) -> None:
    """Serves the catalogue and data sets, rejecting bad parameters.

    Args:
        heatmap_routes: Installer of the heatmap routes.
        tokens: Bearer tokens.
    """
    settings = dataclasses.replace(
        config.Settings(),
        dev_tokens=config.parse_dev_tokens(f"{tokens[0]}:tenant-01:user-01"),
        database_url="sqlite://",
        demo_only=True,
    )
    with respx.mock(assert_all_called=False) as router:
        heatmap_routes(router)
        clock = clock_lib.ManualClock(1_795_277_530_000)
        container = bootstrap.build_container(settings, clock=clock)
        container.heatmap_sources = bootstrap.heatmap_sources(
            clock, httpx.AsyncClient(), 5
        )
        headers = {"Authorization": f"Bearer {tokens[0]}"}
        app = bootstrap.create_application(container)
        with testclient.TestClient(app) as client:
            catalog = client.get("/v1/heatmap/catalog", headers=headers)
            assert catalog.status_code == 200
            _validate("HeatmapCatalog", catalog.json())
            assert catalog.json()["defaults"]["stocks"] == "us_sp500"
            stocks = client.get("/v1/heatmap", headers=headers).json()
            _validate("HeatmapSnapshot", stocks)
            assert stocks["dataset"] == "us_sp500"
            assert len(stocks["tiles"]) == 13
            assert stocks["tiles"][0]["key"] == "AAPL"
            taiwan = client.get(
                "/v1/heatmap",
                headers=headers,
                params={"type": "stocks", "dataset": "tw_twse", "limit": 3},
            ).json()
            _validate("HeatmapSnapshot", taiwan)
            assert [tile["key"] for tile in taiwan["tiles"]] == [
                "2330",
                "2454",
                "2317",
            ]
            coins = client.get(
                "/v1/heatmap", headers=headers, params={"type": "crypto"}
            ).json()
            _validate("HeatmapSnapshot", coins)
            for params in (
                {"type": "crypto", "dataset": "us_sp500"},
                {"type": "bonds"},
                {"limit": 0},
                {"dataset": "Bad Id"},
            ):
                response = client.get(
                    "/v1/heatmap", headers=headers, params=params
                )
                assert response.status_code == 422, params
            assert client.get("/v1/heatmap").status_code == 401
            assert client.get(
                "/v1/heatmap/metadata", headers=headers
            ).status_code in {
                404,
                405,
                422,
            }


def test_crypto_tiles_open_enabled_venue_charts(
    venue_client: tuple[testclient.TestClient, respx.Router],
    heatmap_routes: Callable[[respx.Router], None],
    tokens: tuple[str, str],
) -> None:
    """Maps coins to the Binance perpetual of the configured venues.

    Args:
        venue_client: App with fixture-backed crypto venues.
        heatmap_routes: Installer of the heatmap routes.
        tokens: Bearer tokens.
    """
    client, router = venue_client
    heatmap_routes(router)
    headers = {"Authorization": f"Bearer {tokens[0]}"}
    for _ in range(20):
        body = client.get(
            "/v1/heatmap", headers=headers, params={"type": "crypto"}
        ).json()
        if not body["pending"]:
            break
    tiles = {tile["key"]: tile for tile in body["tiles"]}
    assert tiles["bitcoin"]["instrument_id"] == "binance:perp:BTCUSDT"
    assert tiles["bitcoin"]["chart_unavailable_reason"] is None
    missing = [tile for tile in tiles.values() if tile["instrument_id"] is None]
    assert all(
        tile["chart_unavailable_reason"] == "No chart on configured venues"
        for tile in missing
    )
