# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Liquidity qualification filter: the screening policy's test vectors.

The vectors are those of the removed ``joseph.screening`` policy tests
(strict gates, asset-class thresholds, missing and stale data, funding,
listed securities and DEMO stocks), restated as snapshot and instrument
contracts so the built-in Filter keeps the same decisions.
"""

from __future__ import annotations

import decimal
from typing import Any

import hypothesis
import pytest
from hypothesis import strategies

import joseph_script
from joseph.scripts import builtin
from joseph.scripts import worker
from joseph.scripts.builtin.filters import liquidity_qualification as policy

_D = decimal.Decimal
_NOW = 1_795_278_000_000
_QUALIFIED = policy.QUALIFIED
_REJECTED = policy.REJECTED
_MISSING = policy.INSUFFICIENT_DATA


def _book(
    spread_pct: str = "0.01", slippage_pct: str = "0.05"
) -> dict[str, Any]:
    """Builds a two-level book with a given spread and USD 50K slippage.

    Around a mid of 100 the best levels hold USD 1 each; the second level
    on each side holds two million at the slippage price, so the
    walk's average price lands (almost exactly) at that slippage.

    Args:
        spread_pct: Best bid/ask spread in percent of the mid.
        slippage_pct: Target worst-side slippage in percent.

    Returns:
        Contract order book, fresh at ``_NOW``.
    """
    half = _D(spread_pct) / 2
    slip = _D(slippage_pct)
    return {
        "bids": [
            [str(100 - half), str(_D(1) / (100 - half))],
            [str(100 - slip), str(_D(2000000) / (100 - slip))],
        ],
        "asks": [
            [str(100 + half), str(_D(1) / (100 + half))],
            [str(100 + slip), str(_D(2000000) / (100 + slip))],
        ],
        "received_at_ms": _NOW,
        "stale": False,
    }


def _perp(
    *,
    turnover: str | None = "1000000000",
    open_interest: str | None = "2000000000",
    spread: str | None = "0.01",
    slippage: str | None = "0.05",
    funding_8h: str | None = "0.01",
    age_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Builds a BTCUSDT perpetual snapshot that passes unless overridden.

    Args:
        turnover: 24h quote turnover in USD, None when missing.
        open_interest: Open interest in USD, None when missing.
        spread: Spread in percent, None without a book.
        slippage: USD 50K slippage in percent, None without depth.
        funding_8h: 8h funding in percent (8h interval), None when missing.
        age_ms: Observation ages by input name (turnover, open_interest,
            book, funding).

    Returns:
        Snapshot contract with a loaded order book.
    """
    ages = age_ms or {}
    snapshot: dict[str, Any] = {
        "turnover_24h_quote": turnover,
        "as_of_ms": _NOW - ages.get("turnover", 0),
        "best_bid": None,
        "best_ask": None,
        "open_interest": None,
        "funding": None,
        "order_book": None,
    }
    if open_interest is not None:
        snapshot["open_interest"] = {
            "notional_usd": open_interest,
            "as_of_ms": _NOW - ages.get("open_interest", 0),
        }
    if funding_8h is not None:
        snapshot["funding"] = {
            "rate": str(_D(funding_8h) / 100),
            "interval_hours": 8.0,
            "as_of_ms": _NOW - ages.get("funding", 0),
        }
    if spread is not None:
        book = _book(spread, slippage or "0.05")
        book["received_at_ms"] = _NOW - ages.get("book", 0)
        if slippage is None:
            book["bids"] = book["bids"][:1]
            book["asks"] = book["asks"][:1]
        snapshot["order_book"] = book
    return snapshot


def _instrument(
    instrument_id: str = "binance:perp:BTCUSDT",
    asset_class: str = "CRYPTO",
    status: str = "TRADING",
    quote: str = "USDT",
) -> dict[str, Any]:
    """Builds an instrument contract.

    Args:
        instrument_id: Canonical id.
        asset_class: Asset class.
        status: Venue status.
        quote: Quote asset.

    Returns:
        Instrument fields the filter reads.
    """
    return {
        "instrument_id": instrument_id,
        "asset_class": asset_class,
        "status": status,
        "key": {"quote": quote},
    }


def _status(
    snapshot: dict[str, Any],
    instrument: dict[str, Any] | None = None,
    **options: Any,
) -> str:
    """Qualifies a snapshot.

    Args:
        snapshot: Snapshot contract.
        instrument: Instrument contract; BTCUSDT when omitted.
        **options: max_age_ms / reject_high_funding_risk overrides.

    Returns:
        QUALIFIED, REJECTED or INSUFFICIENT_DATA.
    """
    status, _ = policy.qualify(
        snapshot, instrument or _instrument(), now_ms=_NOW, **options
    )
    return status


def test_all_gates_passing_is_qualified() -> None:
    """Five passing gates and normal funding qualify."""
    assert _status(_perp()) == _QUALIFIED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("turnover", "50000000"),
        ("open_interest", "10000000"),
        ("spread", "0.10"),
    ],
)
def test_value_equal_to_threshold_fails(field: str, value: str) -> None:
    """A value equal to its gate threshold fails the gate.

    Args:
        field: Gate input.
        value: Its threshold.
    """
    snapshot = _perp(
        turnover=value if field == "turnover" else "1000000000",
        open_interest=value if field == "open_interest" else "2000000000",
        spread=value if field == "spread" else "0.01",
    )
    status, reasons = policy.qualify(snapshot, _instrument(), now_ms=_NOW)
    assert status == _REJECTED
    assert any("fails" in reason for reason in reasons)


def test_slippage_equal_to_threshold_fails() -> None:
    """Exactly 0.25 % slippage fails; just below passes."""
    assert policy.gate(
        "SLIPPAGE_50K_PCT",
        observation=(_D("0.25"), _NOW),
        threshold=_D("0.25"),
        comparator="lt",
        now_ms=_NOW,
        max_age_ms=300_000,
    ) == (False, "SLIPPAGE_50K_PCT 0.25 fails < 0.25")
    assert _status(_perp(slippage="0.24")) == _QUALIFIED
    assert _status(_perp(slippage="0.26")) == _REJECTED


def test_turnover_oi_ratio_equal_to_threshold_fails() -> None:
    """Turnover/OI of exactly 0.20 fails."""
    _, reasons = policy.qualify(
        _perp(turnover="200000000", open_interest="1000000000"),
        _instrument(),
        now_ms=_NOW,
    )
    assert reasons == ["TURNOVER_OI_RATIO 0.2 fails > 0.2"]


def test_equity_perp_uses_lower_turnover_threshold() -> None:
    """Equity perpetuals need > 30M, crypto > 50M."""
    item = _perp(turnover="40000000", open_interest="15000000")
    assert _status(item) == _REJECTED
    assert _status(item, _instrument(asset_class="EQUITY")) == _QUALIFIED


def test_unknown_asset_class_uses_stricter_crypto_thresholds() -> None:
    """UNKNOWN never benefits from the equity threshold."""
    item = _perp(turnover="40000000", open_interest="15000000")
    assert _status(item, _instrument(asset_class="UNKNOWN")) == _REJECTED


def test_missing_data_is_insufficient_never_pass() -> None:
    """Missing slippage depth is insufficient data, not a pass."""
    status, reasons = policy.qualify(
        _perp(slippage=None), _instrument(), now_ms=_NOW
    )
    assert status == _MISSING
    assert reasons == ["SLIPPAGE_50K_PCT is unavailable"]


def test_stale_data_is_insufficient() -> None:
    """Inputs older than the age budget count as missing."""
    status, reasons = policy.qualify(
        _perp(age_ms={"book": 600_000}), _instrument(), now_ms=_NOW
    )
    assert status == _MISSING
    assert any("stale" in reason for reason in reasons)
    assert (
        _status(_perp(age_ms={"book": 600_000}), max_age_ms=900_000)
        == _QUALIFIED
    )


def test_known_failure_beats_missing_data() -> None:
    """A definite failure rejects even when other data is missing."""
    assert _status(_perp(spread="0.5", slippage=None)) == _REJECTED


def test_missing_funding_is_insufficient() -> None:
    """Unknown funding cannot be confirmed low risk."""
    status, reasons = policy.qualify(
        _perp(funding_8h=None), _instrument(), now_ms=_NOW
    )
    assert status == _MISSING
    assert "funding is unavailable" in reasons[-1]


@pytest.mark.parametrize(
    ("value", "status"),
    [
        ("0.0999", _QUALIFIED),
        ("0.10", _QUALIFIED),
        ("-0.2999", _QUALIFIED),
        ("0.30", _REJECTED),
        ("-0.5", _REJECTED),
    ],
)
def test_funding_watch_passes_and_high_risk_rejects(
    value: str, status: str
) -> None:
    """Watch funding (>= 0.10) qualifies; high risk (>= 0.30) rejects.

    Args:
        value: 8h funding in percent.
        status: Expected decision.
    """
    assert _status(_perp(funding_8h=value)) == status


def test_high_risk_funding_rejection_is_configurable() -> None:
    """HIGH_RISK rejects only while reject_high_funding_risk is set."""
    item = _perp(funding_8h="0.35")
    assert _status(item, reject_high_funding_risk=False) == _QUALIFIED


def test_normalized_funding_uses_interval() -> None:
    """Funding normalizes as rate * 100 * 8 / interval hours."""
    assert policy.normalized_funding(
        {"rate": "0.0001", "interval_hours": 1.0}
    ) == _D("0.08")
    assert policy.normalized_funding(
        {"rate": "0.0004", "interval_hours": 4}
    ) == _D("0.08")
    assert policy.normalized_funding(
        {"rate": "0.0001", "interval_hours": 0}
    ) is (None)


def test_not_trading_is_rejected() -> None:
    """Halted instruments never qualify."""
    assert _status(_perp(), _instrument(status="HALTED")) == _REJECTED


def _listed(
    instrument_id: str,
    asset_class: str,
    turnover: str,
    spread: str = "0.02",
    slippage: str = "0.05",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Builds a listed security without open interest or funding.

    Args:
        instrument_id: Listed security.
        asset_class: EQUITY or BOND.
        turnover: Session turnover in the listing currency.
        spread: Spread in percent.
        slippage: Slippage in percent.

    Returns:
        Snapshot and instrument contracts.
    """
    snapshot = _perp(
        turnover=turnover,
        open_interest=None,
        spread=spread,
        slippage=slippage,
        funding_8h=None,
    )
    return snapshot, _instrument(instrument_id, asset_class, quote="USD")


def test_us_equity_qualifies_without_funding_or_open_interest() -> None:
    """A liquid stock qualifies; funding and OI never apply."""
    snapshot, instrument = _listed("nasdaq:stock:AAPL", "EQUITY", "9e9")
    assert policy.qualify(snapshot, instrument, now_ms=_NOW) == (
        _QUALIFIED,
        [],
    )


def test_us_equity_turnover_threshold_is_strict() -> None:
    """USD 20M of session turnover exactly fails the equity gate."""
    assert _status(*_listed("nasdaq:stock:AAPL", "EQUITY", "20000000")) == (
        _REJECTED
    )


def test_bond_etfs_have_their_own_thresholds() -> None:
    """A bond ETF with USD 15M turnover qualifies (equities need 20M)."""
    assert _status(*_listed("nasdaq:etf:TLT", "BOND", "15000000")) == (
        _QUALIFIED
    )
    assert _status(*_listed("nasdaq:etf:TLT", "EQUITY", "15000000")) == (
        _REJECTED
    )


def test_twse_thresholds_are_in_twd_and_tick_aware() -> None:
    """TWSE gates use TWD amounts and allow a one-tick 0.5 % spread."""
    assert (
        _status(
            *_listed(
                "twse:stock:2330",
                "EQUITY",
                "25000000000",
                spread="0.5",
                slippage="0.6",
            )
        )
        == _QUALIFIED
    )
    limits = policy.limits_for("twse:stock:2330", "BOND")
    assert limits["turnover"] == _D(100_000_000)
    assert limits["notional"] == _D(1_500_000)
    assert limits["currency"] == "TWD"


def test_tpex_thresholds_are_in_twd_with_twse_values() -> None:
    """TPEx stocks and bond ETFs use TWSE's TWD values."""
    assert (
        _status(*_listed("tpex:stock:6488", "EQUITY", "5218083500", "0.11"))
        == _QUALIFIED
    )
    assert _status(*_listed("tpex:etf:00679B", "BOND", "150000000")) == (
        _QUALIFIED
    )
    assert policy.limits_for("tpex:stock:6488", "EQUITY") == policy.limits_for(
        "twse:stock:2330", "EQUITY"
    )


def test_missing_listed_inputs_are_insufficient_data_not_funding() -> None:
    """A listed stock without book depth is missing slippage only."""
    snapshot, instrument = _listed("nasdaq:stock:AAPL", "EQUITY", "9e9")
    snapshot["order_book"]["bids"] = snapshot["order_book"]["bids"][:1]
    status, reasons = policy.qualify(snapshot, instrument, now_ms=_NOW)
    assert status == _MISSING
    assert reasons == ["SLIPPAGE_50K_PCT is unavailable"]


def test_rates_are_never_screened_and_listing_is_detected() -> None:
    """Rate series and non-USDT Binance pairs are ineligible."""
    assert not policy.eligible(_instrument("treasury:rate:UST10Y"))
    assert policy.eligible(_instrument("nasdaq:stock:AAPL", quote="USD"))
    assert not policy.eligible(_instrument("binance:spot:ETHBTC", quote="BTC"))
    assert policy.is_listed("nasdaq:etf:TLT")
    assert policy.is_listed("mock:stock:ACME")
    assert not policy.is_listed("binance:perp:BTCUSDT")


def test_filter_raises_unavailable_and_rejects_ineligible() -> None:
    """The Filter class maps the three outcomes to the scanner's."""
    cls = builtin.script_class("filters.liquidity_qualification")
    assert issubclass(cls, joseph_script.Filter)
    defaults = {name: spec.default for name, spec in cls.INPUTS.items()}

    def run(snapshot: dict[str, Any], instrument: dict[str, Any]) -> bool:
        """Runs the filter class.

        Args:
            snapshot: Snapshot contract.
            instrument: Instrument contract.

        Returns:
            The decision.
        """
        result = worker.run_class(
            cls,
            {
                "kind": "filter",
                "bars": [],
                "snapshot": {"evaluated_at_ms": _NOW, **snapshot},
                "instrument": instrument,
                "instrument_id": instrument["instrument_id"],
                "inputs": defaults,
            },
        )
        return bool(result["matched"])

    assert run(_perp(), _instrument())
    assert not run(_perp(spread="1"), _instrument())
    assert not run(_perp(), _instrument("treasury:rate:UST10Y"))
    with pytest.raises(joseph_script.Unavailable, match="SLIPPAGE"):
        run(_perp(slippage=None), _instrument())


_DECIMALS = strategies.decimals(
    min_value=_D("0"),
    max_value=_D("1e11"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


@hypothesis.given(_DECIMALS)
def test_turnover_gate_is_strictly_greater(value: decimal.Decimal) -> None:
    """The turnover gate passes exactly when the value exceeds USD 50M.

    Args:
        value: Observed turnover.
    """
    passed, _ = policy.gate(
        "TURNOVER_24H",
        observation=(value, _NOW),
        threshold=policy.CRYPTO["turnover"],
        comparator="gt",
        now_ms=_NOW,
        max_age_ms=300_000,
    )
    assert passed is (value > _D(50_000_000))


@hypothesis.given(
    strategies.lists(
        strategies.one_of(strategies.none(), _DECIMALS), min_size=4, max_size=4
    )
)
def test_status_is_consistent_with_gates(
    values: list[decimal.Decimal | None],
) -> None:
    """QUALIFIED only with every input present; a failure always rejects.

    Args:
        values: Turnover, open interest, spread and funding (None missing).
    """
    turnover, interest, spread, funding = (
        None if value is None else str(value) for value in values
    )
    snapshot = _perp(
        turnover=turnover,
        open_interest=interest,
        spread=None if spread is None else str(_D(spread) / 10**9),
        funding_8h=None if funding is None else str(_D(funding) / 10**12),
    )
    status, reasons = policy.qualify(snapshot, _instrument(), now_ms=_NOW)
    if status == _QUALIFIED:
        assert None not in values and not reasons
    if any("fails" in reason for reason in reasons):
        assert status == _REJECTED
