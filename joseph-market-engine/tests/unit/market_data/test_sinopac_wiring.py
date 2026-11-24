# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests: Shioaji settings, secrets, wiring and the bar archive."""

from __future__ import annotations

import dataclasses
import decimal
import logging
import pathlib

import httpx
import pytest

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.adapters import bar_archive_sql
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import sinopac
from joseph.market_data.adapters import twse
from joseph.market_data.testing import fake_shioaji
from joseph.platform import config
from joseph.platform import db
from joseph.platform import redaction

_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("twse:stock:2330")
_M1 = timeframes.Timeframe.M1
_T0 = 1_795_395_600_000  # 2026-11-23 09:00 Taipei.


def test_settings_read_the_source_and_secret_files(
    tmp_path: pathlib.Path,
) -> None:
    """Keys come from the variable or its _FILE; repr never shows them."""
    secret = tmp_path / "secret"
    secret.write_text("file-secret-value\n", encoding="utf-8")
    settings = config.Settings.from_env(
        {
            "JOSEPH_TW_DATA_SOURCE": " SinoPac ",
            "JOSEPH_SINOPAC_API_KEY": "env-key-value",
            "JOSEPH_SINOPAC_SECRET_KEY_FILE": str(secret),
            "JOSEPH_SINOPAC_MAX_SYMBOLS": "500",
            "JOSEPH_SINOPAC_INTRADAY_TICKS_CALLS": "99",
            "JOSEPH_SINOPAC_INTRADAY_KBARS_CALLS": "-1",
            "JOSEPH_SINOPAC_SECONDS_HISTORY_DAYS": "3",
        }
    )
    assert settings.tw_data_source == "sinopac"
    assert settings.sinopac_api_key == "env-key-value"
    assert settings.sinopac_secret_key == "file-secret-value"
    assert (
        settings.sinopac_max_symbols,
        settings.sinopac_intraday_ticks_calls,
        settings.sinopac_intraday_kbars_calls,
        settings.sinopac_seconds_history_days,
    ) == (100, 10, 0, 3)
    text = repr(settings)
    assert "env-key-value" not in text and "file-secret-value" not in text
    assert "tw_data_source='sinopac'" in text
    assert redaction.redact("login with file-secret-value") == "login with ***"
    defaults = config.Settings.from_env({})
    assert defaults.tw_data_source == "twse"
    assert defaults.sinopac_api_key is None
    assert bootstrap.sinopac_config(defaults) is None
    built = bootstrap.sinopac_config(settings)
    assert built is not None and built.configured
    assert built.seconds_history_days == 3
    assert "env-key-value" not in repr(built)


def test_bad_source_or_unreadable_secret_file_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """Unknown sources and unreadable files fail without leaking values."""
    with pytest.raises(ValueError, match="twse or sinopac"):
        config.Settings.from_env({"JOSEPH_TW_DATA_SOURCE": "yahoo"})
    missing = tmp_path / "nope"
    with pytest.raises(ValueError, match="JOSEPH_SINOPAC_API_KEY_FILE"):
        config.Settings.from_env({"JOSEPH_SINOPAC_API_KEY_FILE": str(missing)})
    assert config.secret_value({"X": "  "}, "X") is None
    redaction.register_secret("ab")  # Too short to redact safely.
    assert redaction.redact("ab cd") == "ab cd"


def test_secrets_never_reach_log_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logged secret is replaced by the redacting filter."""
    config.secret_value({"K": "very-private-key"}, "K")
    logger = logging.getLogger("test.sinopac.redaction")
    handler = logging.Handler()
    handler.addFilter(redaction.RedactingFilter())
    records: list[str] = []
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]  # replace emit to capture the filtered messages
    logger.addHandler(handler)
    try:
        logger.warning("key is %s", "very-private-key")
    finally:
        logger.removeHandler(handler)
    assert records == ["key is ***"]


async def test_factory_shares_one_source_between_twse_and_tpex() -> None:
    """Both venues use one Shioaji login; no config keeps them official."""
    fake = fake_shioaji.FakeShioaji()
    made = fake_shioaji.FakeShioajiFactory(fake)
    adapters = factory.build_adapters(
        [instruments.Venue.TWSE, instruments.Venue.TPEX],
        clock_lib.ManualClock(_T0),
        client=httpx.AsyncClient(),
        sinopac_config=sinopac.SinopacConfig(
            api_key="abcd1234", secret_key="efgh5678"
        ),
        sinopac_factory=lambda: made,
    )
    first = adapters[instruments.Venue.TWSE]
    second = adapters[instruments.Venue.TPEX]
    assert isinstance(first, twse.TaiwanExchangeAdapter)
    assert isinstance(second, twse.TaiwanExchangeAdapter)
    assert first._sinopac is second._sinopac is not None
    assert timeframes.Timeframe.S1 in first.capabilities().timeframes
    assert timeframes.Timeframe.S1 in second.capabilities().timeframes
    for adapter in adapters.values():
        await adapter.close()
    assert first._sinopac._users == 0
    plain = factory.build_adapters(
        [instruments.Venue.TWSE],
        clock_lib.ManualClock(_T0),
        client=httpx.AsyncClient(),
    )[instruments.Venue.TWSE]
    assert isinstance(plain, twse.TaiwanExchangeAdapter)
    assert plain._sinopac is None
    assert timeframes.Timeframe.S1 not in plain.capabilities().timeframes
    await plain.close()


async def test_container_without_the_sdk_keeps_official_data(
    tmp_path: pathlib.Path,
) -> None:
    """Without the sinopac extra the venue says so and stays official."""
    settings = config.Settings(
        database_url="sqlite://",
        enabled_venues=(instruments.Venue.TWSE, instruments.Venue.TPEX),
        pipeline_enabled=False,
        trade_recording=False,
        trade_archive_url=f"sqlite:///{tmp_path / 'archive.db'}",
        tw_data_source="sinopac",
        sinopac_api_key="abcd1234",
        sinopac_secret_key="efgh5678",
    )
    container = bootstrap.build_container(
        settings, clock=clock_lib.ManualClock(_T0)
    )
    caps = container.adapters[instruments.Venue.TWSE].capabilities()
    assert timeframes.Timeframe.S1 not in caps.timeframes
    assert "shioaji is not installed" in caps.notes[-1]
    adapter = container.adapters[instruments.Venue.TWSE]
    assert isinstance(adapter, twse.TaiwanExchangeAdapter)
    assert isinstance(adapter._archive, bar_archive_sql.SqlBarArchive)
    await container.aclose()
    official = bootstrap.build_container(
        dataclasses.replace(settings, tw_data_source="twse"),
        clock=clock_lib.ManualClock(_T0),
    )
    adapter = official.adapters[instruments.Venue.TWSE]
    assert isinstance(adapter, twse.TaiwanExchangeAdapter)
    assert adapter._sinopac is None
    await official.aclose()


def _bar(
    start: int, *, final: bool = True, taker: bool = True
) -> market.Candle:
    """Builds a one-minute bar of 2330.

    Args:
        start: Bar start in epoch milliseconds.
        final: Whether the bar is final.
        taker: Whether it carries classified buy volume.

    Returns:
        A kbar-sourced bar of three trades.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=_M1,
        start_ms=start,
        end_ms=start + 60_000,
        open=_D("100.5"),
        high=_D(101),
        low=_D(100),
        close=_D("100.5"),
        base_volume=_D(3000),
        is_final=final,
        source="sinopac.kbars",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=7,
        quote_turnover=_D(301_500),
        trade_count=3,
        event_ts_ms=start + 59_000,
        taker_buy_base_volume=_D(1000) if taker else None,
        taker_buy_method=market.BuyVolumeMethod.CLASSIFIED if taker else None,
    )


def test_bar_archive_round_trips_bars_and_merges_coverage() -> None:
    """Bars come back exactly; coverage merges per provider; prune cuts."""
    archive = bar_archive_sql.SqlBarArchive(db.create_engine("sqlite://"))
    archive.put(
        _ID,
        "sinopac",
        [
            _bar(_T0),
            _bar(_T0 + 60_000, final=False),
            _bar(_T0 + 120_000, taker=False),
        ],
        [(_T0, _T0 + 60_000)],
    )
    archive.put(_ID, "sinopac", [_bar(_T0)], [(_T0 + 60_000, _T0 + 180_000)])
    archive.put(_ID, "twse.mis", [], [(_T0, _T0 + 600_000)])
    archive.put(_ID, "sinopac", [], [])
    stored = archive.minutes(_ID, "sinopac", 0, 2**62)
    assert stored == [_bar(_T0), _bar(_T0 + 120_000, taker=False)]
    assert archive.coverage(_ID, "sinopac", 0, 2**62) == [(_T0, _T0 + 180_000)]
    assert archive.coverage(_ID, "sinopac", _T0 + 30_000, _T0 + 90_000) == [
        (_T0 + 30_000, _T0 + 90_000)
    ]
    assert archive.minutes(_ID, "twse.mis", 0, 2**62) == []
    assert archive.prune(_T0 + 60_000) == 1
    assert archive.coverage(_ID, "sinopac", 0, 2**62) == [
        (_T0 + 60_000, _T0 + 180_000)
    ]
    assert [b.start_ms for b in archive.minutes(_ID, "sinopac", 0, 2**62)] == [
        _T0 + 120_000
    ]


def test_unconfigured_source_refuses_every_call() -> None:
    """A source without keys is permanently unavailable."""
    source = sinopac.SinopacSource(
        sinopac.SinopacConfig(), clock_lib.ManualClock(_T0)
    )
    with pytest.raises(errors.VenueUnavailableError, match="not set"):
        source._require()
