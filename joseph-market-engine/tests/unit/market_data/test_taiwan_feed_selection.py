# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Check that Taiwan market data activates only with a Fugle key."""

from __future__ import annotations

import pytest

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import twse
from joseph.platform import config


def test_market_settings_reject_removed_data_sources() -> None:
    """Reject old Taiwan and paid US feeds at the market configuration edge."""
    settings = config.Settings.from_env({"JOSEPH_TW_DATA_SOURCE": "twse"})
    assert settings.tw_data_source == "twse"
    with pytest.raises(ValueError, match="JOSEPH_TW_DATA_SOURCE"):
        config.Settings.from_env({"JOSEPH_TW_DATA_SOURCE": "sinopac"})
    with pytest.raises(ValueError, match="JOSEPH_ALPACA_FEED"):
        config.Settings.from_env({"JOSEPH_ALPACA_FEED": "sip"})
    fugle = config.Settings.from_env(
        {"JOSEPH_TW_DATA_SOURCE": "fugle", "JOSEPH_FUGLE_API_KEY": "test-key"}
    )
    assert fugle.tw_data_source == "fugle"
    assert fugle.fugle_api_key == "test-key"
    assert "test-key" not in repr(fugle)


async def test_fugle_selection_without_key_disables_taiwan_data() -> None:
    """Keep Taiwan feeds absent until a free-tier key is configured."""
    settings = config.Settings(
        database_url="sqlite://",
        tw_data_source="fugle",
        enabled_venues=(instruments.Venue.TWSE,),
    )
    container = bootstrap.build_container(settings)
    assert instruments.Venue.TWSE not in container.adapters
    await container.aclose()
    with pytest.raises(ValueError, match="JOSEPH_TW_DATA_SOURCE"):
        bootstrap.build_container(
            config.Settings(database_url="sqlite://", tw_data_source="sinopac")
        )


async def test_legacy_official_selection_does_not_start_taiwan_feeds() -> None:
    """Ignore the removed official-only Taiwan data-feed setting."""
    settings = config.Settings(
        database_url="sqlite://",
        enabled_venues=(instruments.Venue.TWSE, instruments.Venue.TPEX),
        strategy_activations_enabled=False,
        trade_recording=False,
        tw_data_source="twse",
    )
    container = bootstrap.build_container(
        settings, clock=clock_lib.ManualClock(1_795_395_600_000)
    )
    assert not container.adapters
    await container.aclose()


async def test_fugle_selection_wires_both_taiwan_venues() -> None:
    """Select free Fugle data for both venues without changing trading."""
    settings = config.Settings(
        database_url="sqlite://",
        enabled_venues=(instruments.Venue.TWSE, instruments.Venue.TPEX),
        strategy_activations_enabled=False,
        trade_recording=False,
        tw_data_source="fugle",
        fugle_api_key="free-key",
    )
    container = bootstrap.build_container(
        settings, clock=clock_lib.ManualClock(1_795_395_600_000)
    )
    for adapter in container.adapters.values():
        assert isinstance(adapter, twse.TaiwanExchangeAdapter)
        assert adapter._fugle is not None
        assert adapter._sinopac is None
        caps = adapter.capabilities()
        assert caps.requires_credentials
        assert caps.credentials_configured
        assert caps.verified_at_ms is None
        assert timeframes.Timeframe.M1 in caps.timeframes
        assert timeframes.Timeframe.S1 not in caps.timeframes
    await container.aclose()
