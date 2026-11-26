# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tests feed connectivity reporting independently of optional metrics."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from unittest import mock as mock_lib

import pytest

from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import instruments
from joseph.market_data.adapters import mock


@pytest.fixture
def probe_module() -> types.ModuleType:
    """Loads the standalone probe script without changing package paths.

    Returns:
        Module containing the connectivity probe under test.
    """
    script = (
        pathlib.Path(__file__).resolve().parents[3] / "scripts/probe_venues.py"
    )
    spec = importlib.util.spec_from_file_location("probe_venues_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("unavailable", [False, True])
async def test_probe_distinguishes_optional_metrics_from_outages(
    monkeypatch: pytest.MonkeyPatch,
    unavailable: bool,
    probe_module: types.ModuleType,
) -> None:
    """Reports unsupported metrics without hiding actual venue failures.

    Args:
        monkeypatch: Scoped patch helper for the adapter factory.
        unavailable: Whether open interest fails with an actual outage.
        probe_module: Loaded standalone probe script.

    Returns:
        None. Checks connectivity and WebSocket evidence in the probe result.
    """
    probe_venues = probe_module
    adapter = mock.MockAdapter(clock.SystemClock())
    exception = (
        errors.VenueUnavailableError("service offline")
        if unavailable
        else errors.UnsupportedError("optional metric unavailable")
    )
    monkeypatch.setattr(
        adapter,
        "fetch_open_interest",
        mock_lib.AsyncMock(side_effect=exception),
    )
    monkeypatch.setattr(
        adapter,
        "fetch_funding",
        mock_lib.AsyncMock(side_effect=errors.UnsupportedError("no funding")),
    )
    monkeypatch.setattr(
        probe_venues.factory,
        "build_adapters",
        lambda *args, **kwargs: {instruments.Venue.MOCK: adapter},
    )
    monkeypatch.setitem(
        probe_venues._BTC, instruments.Venue.MOCK, "mock:perp:BTCUSD"
    )
    result = await probe_venues._probe(instruments.Venue.MOCK)
    assert result["ok"] is not unavailable
    if unavailable:
        assert "service offline" in str(result["error"])
    else:
        assert (
            result["open_interest_unavailable"] == "optional metric unavailable"
        )
        assert result["funding_unavailable"] == "no funding"
        assert "ws_first_candle_ms" in result
