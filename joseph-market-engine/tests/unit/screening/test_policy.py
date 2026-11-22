"""Unit and property tests for the qualification policy."""

from __future__ import annotations

import dataclasses
import decimal

import hypothesis
import pytest
from hypothesis import strategies

from joseph.domain import instruments
from joseph.screening.domain import policy

_D = decimal.Decimal
_NOW = 1_795_278_000_000
_ID = instruments.InstrumentId.parse("binance:perp:BTCUSDT")
_POLICY = policy.QualificationPolicy()


def _obs(value: str | None, age_ms: int = 0) -> policy.Observation:
    """Builds an observation.

    Args:
        value: Decimal text or ``None``.
        age_ms: Age relative to ``_NOW``.

    Returns:
        Observation.
    """
    return policy.Observation(
        None if value is None else _D(value), _NOW - age_ms
    )


def _input(**overrides: object) -> policy.QualificationInput:
    """Builds a passing input with overrides.

    Args:
        **overrides: Field replacements.

    Returns:
        Input.
    """
    base = policy.QualificationInput(
        instrument_id=_ID,
        display_symbol="BTCUSDT",
        asset_class=instruments.AssetClass.CRYPTO,
        turnover_24h_usd=_obs("1000000000"),
        open_interest_usd=_obs("2000000000"),
        spread_pct=_obs("0.01"),
        slippage_pct=_obs("0.05"),
        funding_8h_pct=_obs("0.01"),
        funding_interval_hours=8.0,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_all_gates_passing_is_qualified() -> None:
    """Five passing gates and normal funding qualify."""
    record = policy.evaluate(_input(), _POLICY, _NOW)
    assert record.status is policy.Status.QUALIFIED
    assert all(g.passed for g in record.gates)
    assert record.policy_version == policy.POLICY_VERSION


@pytest.mark.parametrize(
    ("field", "value", "gate"),
    [
        ("turnover_24h_usd", "50000000", policy.Gate.TURNOVER_24H),
        ("open_interest_usd", "10000000", policy.Gate.OPEN_INTEREST_USD),
        ("spread_pct", "0.10", policy.Gate.SPREAD_PCT),
        ("slippage_pct", "0.25", policy.Gate.SLIPPAGE_50K_PCT),
    ],
)
def test_value_equal_to_threshold_fails(
    field: str, value: str, gate: policy.Gate
) -> None:
    """Strict comparators: equality fails."""
    record = policy.evaluate(_input(**{field: _obs(value)}), _POLICY, _NOW)
    result = next(g for g in record.gates if g.gate is gate)
    assert result.passed is False
    assert record.status is policy.Status.REJECTED


def test_turnover_oi_ratio_equal_to_threshold_fails() -> None:
    """Turnover/OI of exactly 0.20 fails."""
    record = policy.evaluate(
        _input(
            turnover_24h_usd=_obs("200000000"),
            open_interest_usd=_obs("1000000000"),
        ),
        _POLICY,
        _NOW,
    )
    ratio = next(
        g for g in record.gates if g.gate is policy.Gate.TURNOVER_OI_RATIO
    )
    assert ratio.passed is False


def test_equity_uses_lower_turnover_threshold() -> None:
    """Equity perpetuals need > 30M, crypto > 50M."""
    item = _input(
        turnover_24h_usd=_obs("40000000"), open_interest_usd=_obs("15000000")
    )
    assert policy.evaluate(item, _POLICY, _NOW).status is (
        policy.Status.REJECTED
    )
    equity = dataclasses.replace(
        item, asset_class=instruments.AssetClass.EQUITY
    )
    assert policy.evaluate(equity, _POLICY, _NOW).status is (
        policy.Status.QUALIFIED
    )


def test_unknown_asset_class_uses_stricter_crypto_thresholds() -> None:
    """UNKNOWN never benefits from the equity threshold."""
    item = _input(
        asset_class=instruments.AssetClass.UNKNOWN,
        turnover_24h_usd=_obs("40000000"),
        open_interest_usd=_obs("15000000"),
    )
    assert policy.evaluate(item, _POLICY, _NOW).status is (
        policy.Status.REJECTED
    )


def test_missing_data_is_insufficient_never_pass() -> None:
    """Missing inputs yield INSUFFICIENT_DATA with passed=None."""
    record = policy.evaluate(_input(slippage_pct=_obs(None)), _POLICY, _NOW)
    assert record.status is policy.Status.INSUFFICIENT_DATA
    gate = next(
        g for g in record.gates if g.gate is policy.Gate.SLIPPAGE_50K_PCT
    )
    assert gate.passed is None
    assert "unavailable" in gate.reason


def test_stale_data_is_insufficient() -> None:
    """Inputs older than the policy budget count as missing."""
    record = policy.evaluate(
        _input(spread_pct=_obs("0.01", age_ms=600_000)), _POLICY, _NOW
    )
    assert record.status is policy.Status.INSUFFICIENT_DATA
    assert any("stale" in r for r in record.reasons)


def test_known_failure_beats_missing_data() -> None:
    """A definite failure rejects even when other data is missing."""
    record = policy.evaluate(
        _input(spread_pct=_obs("0.5"), slippage_pct=_obs(None)), _POLICY, _NOW
    )
    assert record.status is policy.Status.REJECTED


def test_missing_funding_is_insufficient() -> None:
    """Unknown funding cannot be confirmed low risk."""
    record = policy.evaluate(_input(funding_8h_pct=_obs(None)), _POLICY, _NOW)
    assert record.status is policy.Status.INSUFFICIENT_DATA
    assert record.funding_risk is policy.FundingRisk.UNKNOWN


@pytest.mark.parametrize(
    ("value", "risk"),
    [
        ("0.0999", policy.FundingRisk.NORMAL),
        ("0.10", policy.FundingRisk.WATCH),
        ("-0.2999", policy.FundingRisk.WATCH),
        ("0.30", policy.FundingRisk.HIGH_RISK),
        ("-0.5", policy.FundingRisk.HIGH_RISK),
    ],
)
def test_funding_classification_is_inclusive(
    value: str, risk: policy.FundingRisk
) -> None:
    """WATCH at >= 0.10 and HIGH_RISK at >= 0.30 (absolute)."""
    assert policy.classify_funding(_D(value), _POLICY) is risk


def test_watch_funding_stays_qualified_with_tag() -> None:
    """WATCH is a tag, not a rejection."""
    record = policy.evaluate(_input(funding_8h_pct=_obs("0.15")), _POLICY, _NOW)
    assert record.status is policy.Status.QUALIFIED
    assert record.risk_tags == ("WATCH",)


def test_high_risk_funding_rejects_by_default_and_is_configurable() -> None:
    """HIGH_RISK rejects under the default policy only."""
    item = _input(funding_8h_pct=_obs("0.35"))
    assert policy.evaluate(item, _POLICY, _NOW).status is (
        policy.Status.REJECTED
    )
    lenient = dataclasses.replace(_POLICY, reject_high_funding_risk=False)
    record = policy.evaluate(item, lenient, _NOW)
    assert record.status is policy.Status.QUALIFIED
    assert record.risk_tags == ("HIGH_RISK",)


def test_normalized_funding_uses_interval() -> None:
    """Rate * 100 * 8 / interval_hours."""
    assert policy.normalized_8h_pct(_D("0.0001"), 1.0) == _D("0.08")
    assert policy.normalized_8h_pct(_D("0.0001"), 8.0) == _D("0.01")
    assert policy.normalized_8h_pct(_D("0.0004"), 4.0) == _D("0.08")
    with pytest.raises(ValueError):
        policy.normalized_8h_pct(_D("0.0001"), 0)


def test_not_trading_is_rejected() -> None:
    """Halted instruments never qualify."""
    record = policy.evaluate(_input(tradable=False), _POLICY, _NOW)
    assert record.status is policy.Status.REJECTED


def test_heat_never_overrides_gates() -> None:
    """Trending boosts heat but not status."""
    failing = _input(spread_pct=_obs("1"), trending=True)
    record = policy.evaluate(failing, _POLICY, _NOW)
    assert record.status is policy.Status.REJECTED
    plain = policy.evaluate(
        dataclasses.replace(failing, trending=False), _POLICY, _NOW
    )
    assert record.heat_score is not None and plain.heat_score is not None
    assert record.heat_score == pytest.approx(plain.heat_score + 5)


def test_rank_orders_qualified_first_then_heat() -> None:
    """Qualified records rank above others regardless of heat."""
    strong = policy.evaluate(
        _input(
            spread_pct=_obs("1"),
            turnover_24h_usd=_obs("9e10".replace("9e10", "90000000000")),
        ),
        _POLICY,
        _NOW,
    )
    ok = policy.evaluate(_input(), _POLICY, _NOW)
    ranked = policy.rank([strong, ok])
    assert [r.status for r in ranked] == [
        policy.Status.QUALIFIED,
        policy.Status.REJECTED,
    ]
    assert [r.rank for r in ranked] == [1, 2]


def test_record_contract(contract_validate: object) -> None:
    """Records validate against the JSON Schema."""
    record = policy.rank([policy.evaluate(_input(), _POLICY, _NOW)])[0]
    contract_validate(  # type: ignore[operator]
        "qualification.schema.json",
        "QualificationRecord",
        record.to_contract(),
    )


_DECIMALS = strategies.decimals(
    min_value=_D("0"),
    max_value=_D("1e11"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


@hypothesis.given(_DECIMALS)
def test_turnover_gate_is_strictly_greater(value: decimal.Decimal) -> None:
    """Passed == value > threshold for every value."""
    record = policy.evaluate(
        _input(turnover_24h_usd=policy.Observation(value, _NOW)), _POLICY, _NOW
    )
    gate = record.gates[0]
    assert gate.passed is (value > _D(50_000_000))


@hypothesis.given(
    strategies.decimals(
        min_value=_D("0"),
        max_value=_D("5"),
        allow_nan=False,
        allow_infinity=False,
        places=6,
    )
)
def test_spread_gate_is_strictly_less(value: decimal.Decimal) -> None:
    """Passed == value < threshold for every value."""
    record = policy.evaluate(
        _input(spread_pct=policy.Observation(value, _NOW)), _POLICY, _NOW
    )
    gate = next(g for g in record.gates if g.gate is policy.Gate.SPREAD_PCT)
    assert gate.passed is (value < _D("0.10"))


@hypothesis.given(
    strategies.lists(
        strategies.one_of(strategies.none(), _DECIMALS), min_size=5, max_size=5
    )
)
def test_status_is_consistent_with_gates(
    values: list[decimal.Decimal | None],
) -> None:
    """QUALIFIED iff every gate passes; missing never yields QUALIFIED."""
    names = [
        "turnover_24h_usd",
        "open_interest_usd",
        "spread_pct",
        "slippage_pct",
        "funding_8h_pct",
    ]
    item = _input(
        **{
            name: policy.Observation(value, _NOW)
            for name, value in zip(names, values, strict=True)
        }
    )
    record = policy.evaluate(item, _POLICY, _NOW)
    if record.status is policy.Status.QUALIFIED:
        assert all(g.passed is True for g in record.gates)
        assert None not in values
    if any(g.passed is False for g in record.gates):
        assert record.status is policy.Status.REJECTED
