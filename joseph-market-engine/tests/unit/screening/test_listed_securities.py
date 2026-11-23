"""Qualification of exchange-listed equities and bond ETFs; rates excluded."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import zoneinfo
from collections.abc import Callable

import pytest

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import mock
from joseph.screening.app import service
from joseph.screening.domain import policy

_D = decimal.Decimal
_NOW = 1_795_278_000_000
_POLICY = policy.QualificationPolicy()
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_TLT = instruments.InstrumentId.parse("nasdaq:etf:TLT")
_TSMC = instruments.InstrumentId.parse("twse:stock:2330")
_GW = instruments.InstrumentId.parse("tpex:stock:6488")
_TPEX_BOND = instruments.InstrumentId.parse("tpex:etf:00679B")
_GATES_WITHOUT_OI = [
    policy.Gate.TURNOVER_24H,
    policy.Gate.SPREAD_PCT,
    policy.Gate.SLIPPAGE_50K_PCT,
]
Validate = Callable[[str, str, object], None]


def _listed(
    instrument_id: instruments.InstrumentId,
    asset_class: instruments.AssetClass,
    turnover: str,
    spread: str = "0.02",
    slippage: str = "0.05",
) -> policy.QualificationInput:
    """Builds an input without open interest or funding.

    Args:
        instrument_id: Listed security.
        asset_class: Asset class.
        turnover: Session turnover in the listing currency.
        spread: Spread in percent.
        slippage: Slippage in percent.

    Returns:
        Input.
    """
    return policy.QualificationInput(
        instrument_id=instrument_id,
        display_symbol=instrument_id.venue_market_id,
        asset_class=asset_class,
        turnover_24h_usd=policy.Observation(_D(turnover), _NOW),
        open_interest_usd=policy.Observation(None),
        spread_pct=policy.Observation(_D(spread), _NOW),
        slippage_pct=policy.Observation(_D(slippage), _NOW),
        funding_8h_pct=policy.Observation(None),
    )


def test_us_equity_qualifies_without_funding_or_open_interest(
    contract_validate: Validate,
) -> None:
    """A liquid stock qualifies; funding and OI never apply."""
    record = policy.evaluate(
        _listed(_AAPL, instruments.AssetClass.EQUITY, "9000000000"),
        _POLICY,
        _NOW,
    )
    assert record.status is policy.Status.QUALIFIED
    assert [g.gate for g in record.gates] == _GATES_WITHOUT_OI
    assert record.funding_risk is policy.FundingRisk.NOT_APPLICABLE
    assert record.reasons == ()
    contract = record.to_contract()
    assert contract["funding"] == {
        "normalized_8h_pct": None,
        "interval_hours": None,
        "risk": "NOT_APPLICABLE",
    }
    contract_validate(
        "qualification.schema.json", "QualificationRecord", contract
    )


def test_us_equity_turnover_threshold_is_strict() -> None:
    """USD 20M of session turnover exactly fails the equity gate."""
    record = policy.evaluate(
        _listed(_AAPL, instruments.AssetClass.EQUITY, "20000000"), _POLICY, _NOW
    )
    assert record.status is policy.Status.REJECTED
    assert record.gates[0].threshold == _D(20_000_000)


def test_bond_etfs_have_their_own_thresholds() -> None:
    """A bond ETF with USD 15M turnover qualifies (equities need 20M)."""
    bond = policy.evaluate(
        _listed(_TLT, instruments.AssetClass.BOND, "15000000"), _POLICY, _NOW
    )
    assert bond.status is policy.Status.QUALIFIED
    equity = policy.evaluate(
        _listed(_TLT, instruments.AssetClass.EQUITY, "15000000"), _POLICY, _NOW
    )
    assert equity.status is policy.Status.REJECTED


def test_twse_thresholds_are_in_twd_and_tick_aware(
    contract_validate: Validate,
) -> None:
    """TWSE gates use TWD amounts and allow a one-tick 0.5 % spread."""
    record = policy.evaluate(
        _listed(
            _TSMC,
            instruments.AssetClass.EQUITY,
            "25000000000",
            spread="0.5",
            slippage="0.6",
        ),
        _POLICY,
        _NOW,
    )
    assert record.status is policy.Status.QUALIFIED
    turnover = record.gates[0]
    assert turnover.unit == "TWD"
    assert turnover.threshold == _D(300_000_000)
    contract_validate(
        "qualification.schema.json", "QualificationRecord", record.to_contract()
    )
    bond = _POLICY.thresholds(instruments.AssetClass.BOND, _TSMC)
    assert bond.turnover_24h == _D(100_000_000)
    assert _POLICY.slippage_notional(bond) == _D(1_500_000)


def test_tpex_thresholds_are_in_twd_with_twse_values(
    contract_validate: Validate,
) -> None:
    """TPEx stocks and bond ETFs use their own TWD sets (TWSE's values)."""
    record = policy.evaluate(
        _listed(
            _GW, instruments.AssetClass.EQUITY, "5218083500", spread="0.11"
        ),
        _POLICY,
        _NOW,
    )
    assert record.status is policy.Status.QUALIFIED
    assert record.gates[0].unit == "TWD"
    assert record.gates[0].threshold == _D(300_000_000)
    contract_validate(
        "qualification.schema.json", "QualificationRecord", record.to_contract()
    )
    bond = policy.evaluate(
        _listed(_TPEX_BOND, instruments.AssetClass.BOND, "150000000"),
        _POLICY,
        _NOW,
    )
    assert bond.status is policy.Status.QUALIFIED
    limits = _POLICY.thresholds(instruments.AssetClass.BOND, _TPEX_BOND)
    assert limits is _POLICY.tpex_bond and limits.currency == "TWD"
    assert _POLICY.slippage_notional(limits) == _D(1_500_000)
    tuned = dataclasses.replace(
        _POLICY,
        tpex_equity=dataclasses.replace(
            _POLICY.tpex_equity, turnover_24h=_D(6_000_000_000)
        ),
    )
    assert (
        policy.evaluate(
            _listed(_GW, instruments.AssetClass.EQUITY, "5218083500"),
            tuned,
            _NOW,
        ).status
        is policy.Status.REJECTED
    )
    assert tuned.thresholds(instruments.AssetClass.EQUITY, _TSMC) is (
        _POLICY.tw_equity
    )


def test_missing_listed_inputs_are_insufficient_data_not_funding() -> None:
    """A listed stock without a book is INSUFFICIENT_DATA for slippage only."""
    item = dataclasses.replace(
        _listed(_AAPL, instruments.AssetClass.EQUITY, "9000000000"),
        slippage_pct=policy.Observation(None),
    )
    record = policy.evaluate(item, _POLICY, _NOW)
    assert record.status is policy.Status.INSUFFICIENT_DATA
    assert not any("funding" in reason for reason in record.reasons)


def test_rates_are_never_screened_and_listing_is_detected() -> None:
    """Rate series are excluded; stocks and ETFs count as listed."""
    assert not policy.is_screened(
        instruments.InstrumentId.parse("treasury:rate:UST10Y")
    )
    assert policy.is_screened(_AAPL)
    assert policy.is_listed(_TLT)
    assert policy.is_listed(instruments.InstrumentId.parse("mock:stock:ACME"))
    assert not policy.is_listed(
        instruments.InstrumentId.parse("binance:perp:BTCUSDT")
    )


async def test_demo_stock_is_screened_with_equity_thresholds(
    contract_validate: Validate,
) -> None:
    """The DEMO venue screens ACME without asking for funding or OI."""
    clock = clock_lib.ManualClock(_ny(2026, 11, 30, 12))
    adapter = _NoDerivativesMock(clock)
    qualification = service.QualificationService(
        {instruments.Venue.MOCK: adapter},
        clock,  # type: ignore[dict-item]
    )
    report = await qualification.report(instruments.Venue.MOCK)
    contract_validate(
        "qualification.schema.json", "QualificationReport", report.to_contract()
    )
    acme = next(
        r for r in report.records if str(r.instrument_id) == "mock:stock:ACME"
    )
    assert [g.gate for g in acme.gates] == _GATES_WITHOUT_OI
    assert acme.status is policy.Status.QUALIFIED, acme.reasons
    assert adapter.derivative_calls == 0


async def test_rate_venue_is_refused_and_skipped() -> None:
    """Asking treasury explicitly fails; the global report skips it."""
    clock = clock_lib.ManualClock(_NOW)
    qualification = service.QualificationService(
        {
            instruments.Venue.MOCK: mock.MockAdapter(clock),
            instruments.Venue.TREASURY: mock.MockAdapter(clock),
        },  # type: ignore[dict-item]
        clock,
    )
    with pytest.raises(errors.UnsupportedError, match="rate series"):
        await qualification.report(instruments.Venue.TREASURY)
    report = await qualification.report()
    assert {r.instrument_id.venue for r in report.records} == {
        instruments.Venue.MOCK
    }


def _ny(year: int, month: int, day: int, hour: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.

    Returns:
        Epoch milliseconds.
    """
    zone = zoneinfo.ZoneInfo("America/New_York")
    moment = datetime.datetime(year, month, day, hour, tzinfo=zone)
    return int(moment.timestamp() * 1000)


class _NoDerivativesMock(mock.MockAdapter):
    """DEMO adapter counting open-interest and funding requests."""

    def __init__(self, clock: clock_lib.Clock) -> None:
        """Initializes the adapter.

        Args:
            clock: Time source.
        """
        super().__init__(clock)
        self.derivative_calls = 0

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Counts the call, then delegates.

        Args:
            instrument: Instrument.

        Returns:
            Open interest.
        """
        if instrument.market_type is instruments.MarketType.STOCK:
            self.derivative_calls += 1
        return await super().fetch_open_interest(instrument)

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Counts the call, then delegates.

        Args:
            instrument: Instrument.

        Returns:
            Funding.
        """
        if instrument.market_type is instruments.MarketType.STOCK:
            self.derivative_calls += 1
        return await super().fetch_funding(instrument)
