# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in live test of SinoPac Shioaji market data (needs a SinoPac account).

Run with ``JOSEPH_SINOPAC_DATA_TESTS=1 uv run --extra sinopac pytest
tests/network/test_live_sinopac.py -m sinopac_data`` and
``JOSEPH_SINOPAC_API_KEY[_FILE]`` / ``JOSEPH_SINOPAC_SECRET_KEY[_FILE]``
set. Market data needs no CA certificate. The test reads the last settled
session of 2330 (one kbars query and one ticks query: a few MB of the
daily data allowance) outside trading hours only, so it never spends the
trading-hours query budget, and checks what the source relies on: kbar
labels are minute ends on the XTAI grid, ``ts`` is Taipei wall-clock time,
volumes are lots, and ``tick_type`` is 0, 1 or 2.
"""

from __future__ import annotations

import importlib.util
import os

import httpx
import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import sinopac
from joseph.market_data.adapters import twse
from joseph.platform import config

pytestmark = [
    pytest.mark.sinopac_data,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_SINOPAC_DATA_TESTS") != "1",
        reason="set JOSEPH_SINOPAC_DATA_TESTS=1 and SinoPac keys to run",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("shioaji") is None,
        reason="needs the 'sinopac' extra (shioaji)",
    ),
]

_ID = instruments.InstrumentId.parse("twse:stock:2330")


async def test_live_shioaji_history_matches_the_assumptions() -> None:
    """Kbars and ticks of a settled session match the adapter's assumptions."""
    settings = config.Settings.from_env()
    if not (settings.sinopac_api_key and settings.sinopac_secret_key):
        pytest.skip("JOSEPH_SINOPAC_API_KEY/SECRET_KEY are not set")
    clock = clock_lib.SystemClock()
    now = clock.now_ms()
    if sinopac.in_trading_hours(now):
        pytest.skip("runs outside trading hours (keeps the query budget)")
    adapter = factory.build_adapters(
        [instruments.Venue.TWSE],
        clock,
        client=httpx.AsyncClient(timeout=30),
        sinopac_config=sinopac.SinopacConfig(
            api_key=settings.sinopac_api_key,
            secret_key=settings.sinopac_secret_key,
        ),
    )[instruments.Venue.TWSE]
    assert isinstance(adapter, twse.TaiwanExchangeAdapter)
    try:
        calendar = sessions.XTAI
        latest = calendar.session_at_or_before(now)
        if now < latest.close_ms + sinopac.DAY_SETTLE_MS:
            latest = calendar.previous_session(latest)
        got = await adapter.fetch_candle_coverage(
            _ID, timeframes.Timeframe.M1, latest.open_ms, latest.close_ms
        )
        assert got.covered == ((latest.open_ms, latest.close_ms),)
        assert len(got.candles) > 200  # 2330 trades nearly every minute.
        assert got.candles[0].start_ms == latest.open_ms
        assert got.candles[-1].start_ms == latest.close_ms - 60_000
        assert all(c.base_volume % 1000 == 0 for c in got.candles)
        seconds = await adapter.fetch_candle_coverage(
            _ID,
            timeframes.Timeframe.S1,
            latest.open_ms,
            latest.open_ms + 60_000,
        )
        assert seconds.candles and seconds.covered
        trades = await adapter.fetch_trades(
            _ID, latest.open_ms, latest.open_ms + 60_000
        )
        sides = {t.aggressor_side.value for t in trades}
        assert sides <= {"buy", "sell", "unknown"}
        caps = adapter.capabilities()
        assert caps.notes[-1].startswith("Shioaji status: available")
    finally:
        await adapter.close()
