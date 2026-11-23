"""Integration tests: qualification service over captured venue data."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.adapters import mock
from joseph.screening.app import service
from joseph.screening.domain import policy

Validate = Callable[[str, str, object], None]


def _service(
    venues: list[instruments.Venue], clock: clock_lib.Clock, **kwargs: object
) -> service.QualificationService:
    """Builds a service over fixture-backed adapters.

    Args:
        venues: Venues.
        clock: Clock.
        **kwargs: Service options.

    Returns:
        Service.
    """
    adapters = factory.build_adapters(
        venues,
        clock,
        client=httpx.AsyncClient(),
        alpaca_config=alpaca.AlpacaConfig(key_id="test", secret_key="test"),
    )
    return service.QualificationService(adapters, clock, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "venue",
    # Every real venue with recorded fixtures; rate series are never screened.
    [
        v
        for v in instruments.Venue
        if v is not instruments.Venue.MOCK and v not in instruments.RATE_VENUES
    ],
    ids=lambda v: v.value,
)
async def test_each_venue_produces_contract_valid_reports(
    venue: instruments.Venue,
    venue_api: respx.Router,
    capture_clock: clock_lib.ManualClock,
    contract_validate: Validate,
) -> None:
    """Every venue yields a valid report with evidence per gate."""
    del venue_api
    report = await _service([venue], capture_clock).report(venue)
    contract_validate(
        "qualification.schema.json", "QualificationReport", report.to_contract()
    )
    assert report.records
    for record in report.records:
        assert record.instrument_id.venue is venue
        # Listed securities have no open-interest or funding gates.
        listed = venue in instruments.EXCHANGE_VENUES
        assert len(record.gates) == (3 if listed else 5)
        if record.status is policy.Status.QUALIFIED:
            assert all(g.passed for g in record.gates)
            assert record.funding_risk is not policy.FundingRisk.HIGH_RISK
        if record.status is policy.Status.INSUFFICIENT_DATA:
            assert record.reasons


async def test_binance_preselection_is_usdt_and_top_n(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Binance candidates are USDT-quoted and limited to top N."""
    del venue_api
    report = await _service(
        [instruments.Venue.BINANCE], capture_clock, top_n=2
    ).report(instruments.Venue.BINANCE)
    assert len(report.records) <= 2
    assert all(
        r.instrument_id.venue_market_id.endswith("USDT") for r in report.records
    )


async def test_btc_qualifies_on_binance_fixture(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Captured BTCUSDT data passes every hard gate."""
    del venue_api
    report = await _service([instruments.Venue.BINANCE], capture_clock).report(
        instruments.Venue.BINANCE
    )
    btc = next(
        r
        for r in report.records
        if str(r.instrument_id) == "binance:perp:BTCUSDT"
    )
    assert btc.status is policy.Status.QUALIFIED, btc.reasons


async def test_unreachable_venue_is_skipped_in_global_report(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """A failing venue does not block the global report."""
    venue_api.route(host="api.bitget.com").mock(
        return_value=httpx.Response(503)
    )
    clock = capture_clock
    adapters = factory.build_adapters(
        [instruments.Venue.BITGET], clock, client=httpx.AsyncClient()
    )
    adapters[instruments.Venue.MOCK] = mock.MockAdapter(clock)
    svc = service.QualificationService(adapters, clock)
    report = await svc.report()
    assert {r.instrument_id.venue for r in report.records} == {
        instruments.Venue.MOCK
    }
    assert [r.rank for r in report.records] == list(
        range(1, len(report.records) + 1)
    )


async def test_reports_are_cached_until_refresh(
    venue_api: respx.Router, capture_clock: clock_lib.ManualClock
) -> None:
    """Scans are cached; refresh forces a new scan."""
    svc = _service([instruments.Venue.HYPERLIQUID], capture_clock)
    await svc.report(instruments.Venue.HYPERLIQUID)
    calls = venue_api.calls.call_count
    await svc.report(instruments.Venue.HYPERLIQUID)
    assert venue_api.calls.call_count == calls
    await svc.report(instruments.Venue.HYPERLIQUID, refresh=True)
    assert venue_api.calls.call_count > calls
