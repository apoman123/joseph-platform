"""Perpetual qualification policy (V5 §5), pure and deterministic.

Hard gates use **strict** comparators: a value equal to its threshold fails.

========================  ===================  ===============  ======
Gate                      Crypto / RWA         Equity perp      Strict
========================  ===================  ===============  ======
24h quote turnover        > USD 50,000,000     > USD 30,000,000  ``>``
Open interest (USD)       > USD 10,000,000     > USD 10,000,000  ``>``
24h turnover / OI         > 0.20               > 0.20            ``>``
Best bid/ask spread       < 0.10 %             < 0.10 %          ``<``
USD 50K worst-side slip   < 0.25 %             < 0.25 %          ``<``
========================  ===================  ===============  ======

Funding is normalized to 8 hours (``rate * 100 * 8 / interval_hours``);
``|x| >= 0.10`` is ``WATCH`` and ``|x| >= 0.30`` is ``HIGH_RISK``.

Status (default parity policy, configurable):

* ``REJECTED`` if any known gate fails, or funding is ``HIGH_RISK`` while
  ``reject_high_funding_risk`` is set;
* otherwise ``INSUFFICIENT_DATA`` if any gate input or the funding rate is
  missing or stale (never treated as zero or as a pass);
* otherwise ``QUALIFIED`` (``WATCH`` funding adds a risk tag only).

The heat score orders results and never overrides a gate.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
import math

from joseph.domain import decimals
from joseph.domain import instruments

POLICY_VERSION = "joseph-perp-qualification/2026-11-v1"


class Status(enum.StrEnum):
    """Qualification status."""

    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FundingRisk(enum.StrEnum):
    """Funding risk classification."""

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    HIGH_RISK = "HIGH_RISK"
    UNKNOWN = "UNKNOWN"


class Gate(enum.StrEnum):
    """Hard gate names (contract ``GateName``)."""

    TURNOVER_24H = "TURNOVER_24H"
    OPEN_INTEREST_USD = "OPEN_INTEREST_USD"
    TURNOVER_OI_RATIO = "TURNOVER_OI_RATIO"
    SPREAD_PCT = "SPREAD_PCT"
    SLIPPAGE_50K_PCT = "SLIPPAGE_50K_PCT"


_D = decimal.Decimal


_TURNOVER_CRYPTO_USD = _D(50_000_000)
_TURNOVER_EQUITY_USD = _D(30_000_000)
_OPEN_INTEREST_USD = _D(10_000_000)
_TURNOVER_OI_RATIO = _D("0.20")
_SPREAD_PCT = _D("0.10")
_SLIPPAGE_PCT = _D("0.25")
_FUNDING_WATCH_PCT = _D("0.10")
_FUNDING_HIGH_RISK_PCT = _D("0.30")
_SLIPPAGE_NOTIONAL_USD = _D(50_000)


@dataclasses.dataclass(frozen=True, slots=True)
class Thresholds:
    """Gate thresholds for one asset class.

    Attributes:
        turnover_24h_usd: Minimum (exclusive) 24h quote turnover.
        open_interest_usd: Minimum (exclusive) open interest.
        turnover_oi_ratio: Minimum (exclusive) turnover/OI.
        spread_pct: Maximum (exclusive) spread in percent.
        slippage_pct: Maximum (exclusive) 50K slippage in percent.
    """

    turnover_24h_usd: decimal.Decimal
    open_interest_usd: decimal.Decimal = _OPEN_INTEREST_USD
    turnover_oi_ratio: decimal.Decimal = _TURNOVER_OI_RATIO
    spread_pct: decimal.Decimal = _SPREAD_PCT
    slippage_pct: decimal.Decimal = _SLIPPAGE_PCT


CRYPTO_THRESHOLDS = Thresholds(turnover_24h_usd=_TURNOVER_CRYPTO_USD)
EQUITY_THRESHOLDS = Thresholds(turnover_24h_usd=_TURNOVER_EQUITY_USD)


@dataclasses.dataclass(frozen=True, slots=True)
class QualificationPolicy:
    """Versioned, configurable qualification policy.

    Attributes:
        version: Policy version stamped on every record.
        crypto: Thresholds for crypto, RWA and unknown asset classes.
        equity: Thresholds for equity perpetuals.
        funding_watch_pct: ``|8h funding|`` at or above which is WATCH.
        funding_high_risk_pct: ``|8h funding|`` at or above which is
            HIGH_RISK.
        reject_high_funding_risk: Whether HIGH_RISK funding rejects.
        max_data_age_ms: Inputs older than this count as missing.
        slippage_notional_usd: Book-walk notional.
    """

    version: str = POLICY_VERSION
    crypto: Thresholds = CRYPTO_THRESHOLDS
    equity: Thresholds = EQUITY_THRESHOLDS
    funding_watch_pct: decimal.Decimal = _FUNDING_WATCH_PCT
    funding_high_risk_pct: decimal.Decimal = _FUNDING_HIGH_RISK_PCT
    reject_high_funding_risk: bool = True
    max_data_age_ms: int = 300_000
    slippage_notional_usd: decimal.Decimal = _SLIPPAGE_NOTIONAL_USD

    def thresholds(self, asset_class: instruments.AssetClass) -> Thresholds:
        """Selects thresholds for an asset class.

        Args:
            asset_class: Instrument asset class.

        Returns:
            Thresholds (UNKNOWN uses the stricter crypto set).
        """
        if asset_class is instruments.AssetClass.EQUITY:
            return self.equity
        return self.crypto


@dataclasses.dataclass(frozen=True, slots=True)
class Observation:
    """A gate input with its observation time.

    Attributes:
        value: Observed value, or ``None`` when unavailable.
        as_of_ms: Observation time, or ``None`` when unavailable.
    """

    value: decimal.Decimal | None
    as_of_ms: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class QualificationInput:
    """Everything needed to qualify one exact venue instrument.

    Attributes:
        instrument_id: Instrument.
        display_symbol: Display symbol.
        asset_class: Asset class.
        turnover_24h_usd: 24h quote turnover.
        open_interest_usd: Open interest notional.
        spread_pct: Best bid/ask spread in percent.
        slippage_pct: Worst-side 50K slippage in percent.
        funding_8h_pct: 8h-normalized funding in percent.
        funding_interval_hours: Funding interval.
        trending: Whether a trending source lists the base asset.
        tradable: False when the venue status is not TRADING.
    """

    instrument_id: instruments.InstrumentId
    display_symbol: str
    asset_class: instruments.AssetClass
    turnover_24h_usd: Observation
    open_interest_usd: Observation
    spread_pct: Observation
    slippage_pct: Observation
    funding_8h_pct: Observation
    funding_interval_hours: float | None = None
    trending: bool = False
    tradable: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of one gate (contract ``GateResult``).

    Attributes:
        gate: Gate name.
        observed: Observed value or ``None``.
        threshold: Threshold.
        comparator: ``"gt"`` or ``"lt"`` (strict).
        passed: True/False, or ``None`` when data is missing or stale.
        unit: ``USD``, ``ratio`` or ``pct``.
        as_of_ms: Observation time.
        reason: Human-readable explanation.
    """

    gate: Gate
    observed: decimal.Decimal | None
    threshold: decimal.Decimal
    comparator: str
    passed: bool | None
    unit: str
    as_of_ms: int | None
    reason: str

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "gate": self.gate.value,
            "observed": decimals.format_optional(self.observed),
            "threshold": decimals.format_decimal(self.threshold),
            "comparator": self.comparator,
            "passed": self.passed,
            "unit": self.unit,
            "as_of_ms": self.as_of_ms,
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class QualificationRecord:
    """Qualification of one instrument (contract ``QualificationRecord``).

    Attributes:
        instrument_id: Instrument.
        display_symbol: Display symbol.
        status: Final status.
        risk_tags: ``WATCH`` / ``HIGH_RISK`` tags.
        gates: Gate results.
        funding_8h_pct: Normalized funding.
        funding_interval_hours: Funding interval.
        funding_risk: Funding classification.
        asset_class: Asset class.
        heat_score: Ordering score (never a gate).
        policy_version: Policy version.
        as_of_ms: Evaluation time.
        reasons: Plain-language reasons.
        rank: 1-based rank within its report.
    """

    instrument_id: instruments.InstrumentId
    display_symbol: str
    status: Status
    risk_tags: tuple[str, ...]
    gates: tuple[GateResult, ...]
    funding_8h_pct: decimal.Decimal | None
    funding_interval_hours: float | None
    funding_risk: FundingRisk
    asset_class: instruments.AssetClass
    heat_score: float | None
    policy_version: str
    as_of_ms: int
    reasons: tuple[str, ...]
    rank: int | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "instrument_id": str(self.instrument_id),
            "venue": self.instrument_id.venue.value,
            "display_symbol": self.display_symbol,
            "status": self.status.value,
            "risk_tags": list(self.risk_tags),
            "gates": [gate.to_contract() for gate in self.gates],
            "funding": {
                "normalized_8h_pct": decimals.format_optional(
                    self.funding_8h_pct
                ),
                "interval_hours": self.funding_interval_hours,
                "risk": self.funding_risk.value,
            },
            "asset_class": self.asset_class.value,
            "heat_score": self.heat_score,
            "rank": self.rank,
            "policy_version": self.policy_version,
            "as_of_ms": self.as_of_ms,
            "reasons": list(self.reasons),
        }


def normalized_8h_pct(
    rate: decimal.Decimal, interval_hours: float
) -> decimal.Decimal:
    """Normalizes a per-interval funding rate to percent per 8 hours.

    Args:
        rate: Rate per interval as a decimal fraction.
        interval_hours: Interval length in hours (> 0).

    Returns:
        ``rate * 100 * 8 / interval_hours``.

    Raises:
        ValueError: If the interval is not positive.
    """
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    return rate * decimals.HUNDRED * _D(8) / decimals.to_decimal(interval_hours)


def classify_funding(
    value_8h_pct: decimal.Decimal | None, policy: QualificationPolicy
) -> FundingRisk:
    """Classifies normalized funding risk (inclusive thresholds).

    Args:
        value_8h_pct: Normalized funding in percent, or ``None``.
        policy: Policy.

    Returns:
        Funding risk.
    """
    if value_8h_pct is None:
        return FundingRisk.UNKNOWN
    magnitude = abs(value_8h_pct)
    if magnitude >= policy.funding_high_risk_pct:
        return FundingRisk.HIGH_RISK
    if magnitude >= policy.funding_watch_pct:
        return FundingRisk.WATCH
    return FundingRisk.NORMAL


def _fresh(
    observation: Observation, now_ms: int, policy: QualificationPolicy
) -> decimal.Decimal | None:
    """Returns the value if present and fresh.

    Args:
        observation: Gate input.
        now_ms: Evaluation time.
        policy: Policy (age budget).

    Returns:
        Value or ``None``.
    """
    if observation.value is None:
        return None
    if observation.as_of_ms is not None and (
        now_ms - observation.as_of_ms > policy.max_data_age_ms
    ):
        return None
    return observation.value


def _show(value: decimal.Decimal) -> str:
    """Formats a value for reasons (at most 6 decimal places).

    Args:
        value: Value.

    Returns:
        Display text.
    """
    return decimals.format_decimal(value.quantize(_D("0.000001")))


def _gate(
    gate: Gate,
    observation: Observation,
    *,
    threshold: decimal.Decimal,
    comparator: str,
    unit: str,
    now_ms: int,
    policy: QualificationPolicy,
) -> GateResult:
    """Evaluates one strict gate.

    Args:
        gate: Gate name.
        observation: Input.
        threshold: Threshold.
        comparator: ``"gt"`` or ``"lt"``.
        unit: Unit label.
        now_ms: Evaluation time.
        policy: Policy.

    Returns:
        Gate result.
    """
    value = _fresh(observation, now_ms, policy)
    symbol = ">" if comparator == "gt" else "<"
    if value is None:
        stale = observation.value is not None
        return GateResult(
            gate,
            observation.value,
            threshold,
            comparator,
            None,
            unit,
            observation.as_of_ms,
            f"{gate.value} {'is stale' if stale else 'is unavailable'}",
        )
    passed = value > threshold if comparator == "gt" else value < threshold
    verdict = "passes" if passed else "fails"
    return GateResult(
        gate,
        value,
        threshold,
        comparator,
        passed,
        unit,
        observation.as_of_ms,
        f"{gate.value} {_show(value)} {verdict} "
        f"{symbol} {decimals.format_decimal(threshold)}",
    )


def heat_score(item: QualificationInput) -> float | None:
    """Computes the ordering score.

    ASSUMPTION (original heat formula unavailable): ``10 * log10(turnover)``
    plus up to 20 points for turnover/OI (capped at 5x) plus 5 when
    trending. Used only for ordering; it never changes a status.

    Args:
        item: Qualification input.

    Returns:
        Score, or ``None`` without turnover.
    """
    turnover = item.turnover_24h_usd.value
    if turnover is None or turnover <= decimals.ZERO:
        return None
    score = 10 * math.log10(float(turnover))
    oi = item.open_interest_usd.value
    if oi is not None and oi > decimals.ZERO:
        score += 4 * min(float(turnover / oi), 5.0)
    if item.trending:
        score += 5
    return round(score, 2)


def evaluate(
    item: QualificationInput, policy: QualificationPolicy, now_ms: int
) -> QualificationRecord:
    """Qualifies one instrument.

    Args:
        item: Inputs.
        policy: Policy.
        now_ms: Evaluation time.

    Returns:
        Qualification record.
    """
    limits = policy.thresholds(item.asset_class)
    turnover = _fresh(item.turnover_24h_usd, now_ms, policy)
    oi = _fresh(item.open_interest_usd, now_ms, policy)
    ratio = Observation(
        None
        if turnover is None or oi is None or oi <= decimals.ZERO
        else turnover / oi,
        max(
            (
                t
                for t in (
                    item.turnover_24h_usd.as_of_ms,
                    item.open_interest_usd.as_of_ms,
                )
                if t is not None
            ),
            default=None,
        ),
    )

    def check(
        gate: Gate,
        observation: Observation,
        threshold: decimal.Decimal,
        comparator: str,
        unit: str,
    ) -> GateResult:
        """Evaluates one gate with this call's time and policy.

        Args:
            gate: Gate name.
            observation: Input.
            threshold: Threshold.
            comparator: ``"gt"`` or ``"lt"``.
            unit: Unit label.

        Returns:
            Gate result.
        """
        return _gate(
            gate,
            observation,
            threshold=threshold,
            comparator=comparator,
            unit=unit,
            now_ms=now_ms,
            policy=policy,
        )

    gates = (
        check(
            Gate.TURNOVER_24H,
            item.turnover_24h_usd,
            limits.turnover_24h_usd,
            "gt",
            "USD",
        ),
        check(
            Gate.OPEN_INTEREST_USD,
            item.open_interest_usd,
            limits.open_interest_usd,
            "gt",
            "USD",
        ),
        check(
            Gate.TURNOVER_OI_RATIO,
            ratio,
            limits.turnover_oi_ratio,
            "gt",
            "ratio",
        ),
        check(Gate.SPREAD_PCT, item.spread_pct, limits.spread_pct, "lt", "pct"),
        check(
            Gate.SLIPPAGE_50K_PCT,
            item.slippage_pct,
            limits.slippage_pct,
            "lt",
            "pct",
        ),
    )
    funding = _fresh(item.funding_8h_pct, now_ms, policy)
    risk = classify_funding(funding, policy)
    reasons: list[str] = [g.reason for g in gates if g.passed is not True]
    tags: list[str] = []
    if risk is FundingRisk.WATCH:
        tags.append("WATCH")
    if risk is FundingRisk.HIGH_RISK:
        tags.append("HIGH_RISK")
    failed = any(g.passed is False for g in gates)
    missing = any(g.passed is None for g in gates)
    missing = missing or risk is FundingRisk.UNKNOWN
    if not item.tradable:
        failed = True
        reasons.append("instrument is not trading")
    if risk is FundingRisk.HIGH_RISK and policy.reject_high_funding_risk:
        failed = True
        reasons.append("funding is HIGH_RISK under the default policy")
    if risk is FundingRisk.UNKNOWN:
        reasons.append("funding is unavailable; risk classification unknown")
    if failed:
        status = Status.REJECTED
    elif missing:
        status = Status.INSUFFICIENT_DATA
    else:
        status = Status.QUALIFIED
    return QualificationRecord(
        instrument_id=item.instrument_id,
        display_symbol=item.display_symbol,
        status=status,
        risk_tags=tuple(tags),
        gates=gates,
        funding_8h_pct=funding,
        funding_interval_hours=item.funding_interval_hours,
        funding_risk=risk,
        asset_class=item.asset_class,
        heat_score=heat_score(item),
        policy_version=policy.version,
        as_of_ms=now_ms,
        reasons=tuple(reasons),
    )


_STATUS_ORDER = {
    Status.QUALIFIED: 0,
    Status.INSUFFICIENT_DATA: 1,
    Status.REJECTED: 2,
}


def rank(records: list[QualificationRecord]) -> list[QualificationRecord]:
    """Orders records (qualified first, then heat) and assigns ranks.

    Args:
        records: Records.

    Returns:
        New ranked records.
    """
    ordered = sorted(
        records,
        key=lambda r: (
            _STATUS_ORDER[r.status],
            -(r.heat_score or 0.0),
            str(r.instrument_id),
        ),
    )
    return [dataclasses.replace(r, rank=i + 1) for i, r in enumerate(ordered)]
