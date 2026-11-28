# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""System tests for anchored built-in studies of the market engine.

The anchored VWAP and regression channel drawings run the built-in
``joseph_script`` indicators from their anchor bar. A run reads at most
5,000 bars from ``from_ms`` (the newest are kept), so an anchor further
back is reported by the script as outside the loaded bars instead of being
drawn from the wrong bar. Runs end at a fixed ``to_ms`` on the 24x7 DEMO
perpetual, which makes the bar counts exact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import httpx

from tests import conftest

MOCK_BTC = "mock:perp:BTCUSD"
_MINUTE_MS = 60_000
AT_MS = conftest.ny_ms(2026, 11, 18, 12)
"""A fixed end time on a 1m bar boundary."""
RUN_LIMIT = 5_000
"""Most bars one script run reads."""

_Validate: TypeAlias = Callable[[str, str, object], None]


def _run(
    market: httpx.Client,
    script_id: str,
    bars_back: int,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Runs a study anchored ``bars_back`` 1m bars before ``AT_MS``.

    Args:
        market: Market engine client.
        script_id: Built-in script id.
        bars_back: Closed bars between the anchor and ``AT_MS``.
        inputs: Inputs besides ``anchor_ms``.

    Returns:
        The study's batch entry.
    """
    anchor = AT_MS - bars_back * _MINUTE_MS
    response = market.post(
        "/v1/scripts/run-batch",
        json={
            "instrument_id": MOCK_BTC,
            "tf": "1m",
            "from_ms": anchor,
            "to_ms": AT_MS,
            "limit": RUN_LIMIT,
            "runs": [
                {
                    "script_id": script_id,
                    "inputs": {**inputs, "anchor_ms": anchor},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    (entry,) = response.json()["results"]
    return dict(entry)


def _tables(result: dict[str, Any]) -> list[str]:
    """Reads the first cell of every table a result draws.

    Args:
        result: Script result.

    Returns:
        Table texts.
    """
    return [
        str(visual["data"]["rows"][0][0])
        for visual in result["visuals"]
        if visual["kind"] == "table"
    ]


def test_anchored_vwap_starts_at_its_anchor_bar(
    market: httpx.Client, validate: _Validate
) -> None:
    """The VWAP counts bars from the anchor bar through the end.

    Args:
        market: Market engine client.
        validate: Contract validator.
    """
    entry = _run(market, "builtin:anchored_vwap", 300, {"anchor": "time"})
    validate("python-scripts.schema.json", "ScriptBatchEntry", entry)
    result = entry["result"]
    bars = next(plot for plot in result["plots"] if plot["name"] == "Bars")
    counted = [point["value"] for point in bars["points"]]
    assert counted[0] == 1
    assert counted[-1] == 300
    assert _tables(result) == []


def test_anchor_beyond_the_run_limit_is_reported(
    market: httpx.Client,
) -> None:
    """An anchor before the loaded bars draws nothing and says so.

    Args:
        market: Market engine client.
    """
    for script_id, inputs in (
        ("builtin:anchored_vwap", {"anchor": "time"}),
        ("builtin:regression_channel", {}),
    ):
        entry = _run(market, script_id, RUN_LIMIT + 10, inputs)
        result = entry["result"]
        assert _tables(result) == ["Anchor is outside the loaded bars"]
        assert not [row for row in result["visuals"] if row["kind"] == "line"]
