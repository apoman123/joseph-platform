# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in live smoke of the keyless heatmap sources.

Run with ``JOSEPH_NETWORK_TESTS=1 uv run pytest tests/network -m network``.
Each source is read once (about ten small public requests in total).
"""

from __future__ import annotations

import os

import httpx
import pytest

from joseph import bootstrap
from joseph.domain import clock as clock_lib

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_NETWORK_TESTS") != "1",
        reason="set JOSEPH_NETWORK_TESTS=1 to hit live APIs",
    ),
]


async def test_live_heatmap_sources_parse() -> None:
    """Reads every keyless source once and checks plausible sizes."""
    async with httpx.AsyncClient(timeout=30) as client:
        sources = bootstrap.heatmap_sources(clock_lib.SystemClock(), client, 30)
        assert sources.us is not None
        assert sources.taiwan is not None
        assert sources.coins is not None
        nasdaq = await sources.us.screener("nasdaq")
        assert len(nasdaq) > 2000
        assert sum(1 for row in nasdaq if row.market_cap) > 1000
        assert 495 <= len(await sources.us.sp500()) <= 510
        assert 95 <= len(await sources.us.nasdaq100()) <= 110
        twse = await sources.taiwan.companies("twse")
        assert len(twse) > 800 and all(row.code for row in twse)
        assert sum(1 for row in twse if row.industry) > 0.95 * len(twse)
        daily = await sources.taiwan.daily("tpex")
        assert any(row.close for row in daily)
        coins = await sources.coins.markets()
        assert coins[0].market_cap and coins[0].changes
