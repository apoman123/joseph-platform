"""Risk limits: user values clamped by platform hard caps."""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.domain import decimals
from joseph_execution.domain import errors

POLICY_VERSION = "risk-policy-2026.11.1"
"""Policy id recorded on every decision (2026.11.1: account LIVE arming)."""

_D = decimal.Decimal
_DEFAULT_MAX_ACCOUNT_NOTIONAL = decimal.Decimal(1_000_000)
_DEFAULT_MAX_POSITION_NOTIONAL = decimal.Decimal(250_000)
_DEFAULT_MAX_DAILY_LOSS = decimal.Decimal(5_000)
_DEFAULT_MAX_LEVERAGE = decimal.Decimal(20)
_DEFAULT_MAX_SPREAD_PCT = decimal.Decimal("0.5")
_DEFAULT_MAX_SLIPPAGE_PCT = decimal.Decimal("0.5")
_DEFAULT_MAX_PRICE_DEVIATION_PCT = decimal.Decimal(10)


@dataclasses.dataclass(frozen=True, slots=True)
class RiskLimits:
    """Per-account limits enforced for manual and automated orders.

    Attributes:
        max_account_notional: Gross notional across positions and the order.
        max_position_notional: Notional of the resulting position.
        max_open_positions: Concurrent open positions.
        max_daily_loss: Daily realized loss that pauses new exposure.
        max_leverage: Leverage cap.
        max_spread_pct: Maximum bid/ask spread (percent).
        max_slippage_pct: Maximum estimated market-order slippage.
        max_price_deviation_pct: Limit price band around the mark.
        max_quote_age_ms: Quote freshness budget.
        max_book_age_ms: Book freshness budget.
        max_signal_age_ms: Strategy signal age budget.
        reject_spike_threshold: Rejects per window that pause trading.
        reject_window_ms: Window for the reject spike detector.
    """

    max_account_notional: decimal.Decimal = _DEFAULT_MAX_ACCOUNT_NOTIONAL
    max_position_notional: decimal.Decimal = _DEFAULT_MAX_POSITION_NOTIONAL
    max_open_positions: int = 10
    max_daily_loss: decimal.Decimal = _DEFAULT_MAX_DAILY_LOSS
    max_leverage: decimal.Decimal = _DEFAULT_MAX_LEVERAGE
    max_spread_pct: decimal.Decimal = _DEFAULT_MAX_SPREAD_PCT
    max_slippage_pct: decimal.Decimal = _DEFAULT_MAX_SLIPPAGE_PCT
    max_price_deviation_pct: decimal.Decimal = _DEFAULT_MAX_PRICE_DEVIATION_PCT
    max_quote_age_ms: int = 15_000
    max_book_age_ms: int = 15_000
    max_signal_age_ms: int = 120_000
    reject_spike_threshold: int = 5
    reject_window_ms: int = 60_000

    def to_dict(self) -> dict[str, str]:
        """Serializes limits as strings.

        Returns:
            Field name to decimal/int string.
        """
        result: dict[str, str] = {}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            result[field.name] = (
                decimals.fmt(value)
                if isinstance(value, decimal.Decimal)
                else str(value)
            )
        return result


PLATFORM_HARD_LIMITS = RiskLimits(
    max_account_notional=_D(5_000_000),
    max_position_notional=_D(1_000_000),
    max_open_positions=50,
    max_daily_loss=_D(100_000),
    max_leverage=_D(50),
    max_spread_pct=_D(2),
    max_slippage_pct=_D(2),
    max_price_deviation_pct=_D(25),
    max_quote_age_ms=60_000,
    max_book_age_ms=60_000,
    max_signal_age_ms=600_000,
    reject_spike_threshold=50,
    reject_window_ms=3_600_000,
)


CURRENCY_SCALE: Mapping[str, decimal.Decimal] = {"TWD": _D(30)}
"""Approximate units per USD of non-USD account currencies.

Notional and loss limits are expressed in the account's own currency; the
USD-sized defaults and platform hard caps are scaled by this factor for
TWD accounts (TWSE, TPEx), so one board lot of a large-cap is not blocked by a
USD-sized cap. It is a sizing convention, not an exchange rate.
"""

_MONEY_FIELDS = (
    "max_account_notional",
    "max_position_notional",
    "max_daily_loss",
)


def in_currency(base: RiskLimits, currency: str) -> RiskLimits:
    """Expresses USD-sized money limits in an account currency.

    Args:
        base: Limits sized in USD.
        currency: Account currency.

    Returns:
        Limits with notional and loss limits scaled.
    """
    scale = CURRENCY_SCALE.get(currency)
    if scale is None:
        return base
    scaled = {name: getattr(base, name) * scale for name in _MONEY_FIELDS}
    return dataclasses.replace(base, **scaled)


def hard_limits(currency: str = "USD") -> RiskLimits:
    """Returns the platform hard caps in an account currency.

    Args:
        currency: Account currency.

    Returns:
        Hard caps.
    """
    return in_currency(PLATFORM_HARD_LIMITS, currency)


def from_mapping(
    values: Mapping[str, str] | None, currency: str = "USD"
) -> RiskLimits:
    """Builds limits from stored values, clamped to platform hard caps.

    Unknown keys are rejected so typos cannot silently disable a limit.

    Args:
        values: Stored or user-supplied values; ``None`` for defaults.
        currency: Account currency of money limits (defaults and hard caps
            scale with ``CURRENCY_SCALE``).

    Returns:
        Effective limits.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for unknown keys or bad values.
    """
    base = in_currency(RiskLimits(), currency)
    caps = hard_limits(currency)
    if not values:
        return base
    known = {f.name: f for f in dataclasses.fields(RiskLimits)}
    updates: dict[str, object] = {}
    for key, raw in values.items():
        if key not in known:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"unknown risk limit {key!r}"
            )
        default = getattr(base, key)
        hard = getattr(caps, key)
        try:
            if isinstance(default, int):
                value: object = min(int(raw), hard)
                if int(raw) < 0:
                    raise ValueError("negative")
            else:
                parsed = decimals.to_decimal(raw)
                if parsed < decimals.ZERO:
                    raise ValueError("negative")
                value = min(parsed, hard)
        except ValueError as err:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"invalid value for {key}: {raw!r}"
            ) from err
        updates[key] = value
    return dataclasses.replace(base, **updates)  # type: ignore[arg-type]
