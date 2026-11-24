# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests for the anchored-study cap of the market engine.

The system-test market engine runs with ``JOSEPH_MAX_ANCHORED_BARS`` at
``conftest.MAX_ANCHORED_BARS`` (2,000, the smallest accepted value), so an
anchor just beyond it is cheap to request. Snapshots are evaluated at a
fixed ``at_ms`` on the 24x7 DEMO perpetual, which makes the closed-bar
count exact.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from tests import conftest

MOCK_BTC = "mock:perp:BTCUSD"
_MINUTE_MS = 60_000
AT_MS = conftest.ny_ms(2026, 11, 18, 12)
"""A fixed evaluation time on a 1m bar boundary."""
CAP = conftest.MAX_ANCHORED_BARS
PLUGINS: dict[str, dict[str, str]] = {
    "anchored_vwap": {"anchor": "time"},
    "regression_channel": {},
}
"""Anchored plugins and the settings that make ``anchor_ms`` their anchor."""

Validate = Callable[[str, str, object], None]


def _anchored(
    market: httpx.Client, plugin: str, bars_back: int, at_ms: int
) -> httpx.Response:
    """Requests one anchored study anchored ``bars_back`` 1m bars ago.

    Args:
        market: Market engine client.
        plugin: Anchored plugin id.
        bars_back: Closed bars between the anchor and ``AT_MS``.
        at_ms: Evaluation time.

    Returns:
        Response of ``/v1/indicators/snapshot``.
    """
    settings = {**PLUGINS[plugin], "anchor_ms": AT_MS - bars_back * _MINUTE_MS}
    return market.get(
        "/v1/indicators/snapshot",
        params={
            "instrument_id": MOCK_BTC,
            "tf": "1m",
            "set": plugin,
            "at_ms": at_ms,
            "settings": json.dumps({plugin: settings}),
        },
    )


@pytest.mark.parametrize("plugin", sorted(PLUGINS))
def test_anchor_beyond_the_cap_is_refused_with_its_bar_count(
    market: httpx.Client, validate: Validate, plugin: str
) -> None:
    """2,500 closed bars back is a 422 that names the count and the cap."""
    response = _anchored(market, plugin, 2_500, AT_MS + 30_000)
    assert response.status_code == 422, response.text
    body = response.json()
    validate("common.schema.json", "ErrorResponse", body)
    assert body["code"] == "VALIDATION_FAILED"
    assert "the anchor is 2500 closed 1m bars back" in body["message"]
    assert f"at most {CAP} bars" in body["message"]


@pytest.mark.parametrize("at_offset_ms", [0, 30_000], ids=["boundary", "mid"])
def test_cap_counts_every_closed_bar(
    market: httpx.Client, validate: Validate, at_offset_ms: int
) -> None:
    """Exactly the cap is computed; one bar more is refused, at any time."""
    at_ms = AT_MS + at_offset_ms
    allowed = _anchored(market, "anchored_vwap", CAP, at_ms)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    validate("indicators.schema.json", "IndicatorSnapshotList", body)
    (snapshot,) = body["snapshots"]
    assert snapshot["bar_start_ms"] == AT_MS - _MINUTE_MS
    assert snapshot["values"]["anchor_in_window"] is True
    assert snapshot["values"]["bars"] == CAP
    refused = _anchored(market, "anchored_vwap", CAP + 1, at_ms)
    assert refused.status_code == 422, refused.text
    assert f"the anchor is {CAP + 1} closed" in refused.json()["message"]
