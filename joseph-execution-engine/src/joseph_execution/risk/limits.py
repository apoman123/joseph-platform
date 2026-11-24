# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Risk limits: user values clamped by platform hard caps.

Money limits (notional and daily loss) are sized in USD and scaled into the
account currency by ``money_factor``. The other limits carry their unit in
their name (``_pct`` percent, ``_ms`` milliseconds) or are counts and
multiples.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.domain import decimals
from joseph_execution.domain import errors
from joseph_execution.domain import risk

POLICY_VERSION = "risk-policy-2026.11.1"
"""Policy id recorded on every decision (2026.11.1 added LIVE arming)."""

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

    The defaults are the USD-sized values of an account without stored
    limits. Deployment limits can only tighten them.

    Attributes:
        max_account_notional: Cap on the gross notional of the account's
            positions plus the order (account currency).
        max_position_notional: Cap on the position's current notional plus
            the order (account currency).
        max_open_positions: Concurrent non-flat positions.
        max_daily_loss: Realized loss since 00:00 UTC (a positive amount in
            the account currency) at which new exposure pauses.
        max_leverage: Leverage cap.
        max_spread_pct: Maximum bid/ask spread in percent of the mid.
        max_slippage_pct: Maximum estimated market-order slippage in percent
            of the best price.
        max_price_deviation_pct: Maximum distance of a limit or trigger
            price from the reference price, in percent.
        max_quote_age_ms: Quote freshness budget.
        max_book_age_ms: Order book freshness budget.
        max_signal_age_ms: Strategy signal age budget.
        reject_spike_threshold: Venue rejects inside ``reject_window_ms``
            at which trading pauses.
        reject_window_ms: Window of the reject spike detector.
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
            Field name to its value as a plain decimal or integer string.
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


# Hard caps (USD-sized) that no stored or user-supplied limit can exceed.
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


# Limits in money terms, scaled by money_factor for non-USD accounts.
_MONEY_FIELDS = (
    "max_account_notional",
    "max_position_notional",
    "max_daily_loss",
)


def money_factor(
    currency: str,
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR,
) -> decimal.Decimal:
    """Returns the multiplier of USD-sized money limits for a currency.

    This is the only place account currencies scale limits.

    Args:
        currency: Account currency.
        twd_limit_factor: Factor of TWD accounts (``JOSEPH_TWD_LIMIT_FACTOR``,
            default ``risk.DEFAULT_TWD_LIMIT_FACTOR``).

    Returns:
        ``twd_limit_factor`` for TWD, else 1.
    """
    return twd_limit_factor if currency == "TWD" else decimals.ONE


def in_currency(
    base: RiskLimits,
    currency: str,
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR,
) -> RiskLimits:
    """Expresses USD-sized money limits in an account currency.

    Args:
        base: Limits sized in USD.
        currency: Account currency.
        twd_limit_factor: Factor of TWD accounts.

    Returns:
        Limits with notional and loss limits scaled by ``money_factor``;
        ``base`` itself when the factor is 1.
    """
    factor = money_factor(currency, twd_limit_factor)
    if factor == decimals.ONE:
        return base
    scaled = {name: getattr(base, name) * factor for name in _MONEY_FIELDS}
    return dataclasses.replace(base, **scaled)


def hard_limits(
    currency: str = "USD",
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR,
) -> RiskLimits:
    """Returns the platform hard caps in an account currency.

    Args:
        currency: Account currency.
        twd_limit_factor: Factor of TWD accounts.

    Returns:
        Hard caps.
    """
    return in_currency(PLATFORM_HARD_LIMITS, currency, twd_limit_factor)


def from_mapping(  # noqa: GPY013 - the ValueError is caught below and re-raised as ExecutionError.
    values: Mapping[str, str] | None,
    currency: str = "USD",
    twd_limit_factor: decimal.Decimal = risk.DEFAULT_TWD_LIMIT_FACTOR,
) -> RiskLimits:
    """Builds limits from stored values, clamped to platform hard caps.

    Unknown keys are rejected so typos cannot silently disable a limit.
    A value above its hard cap is lowered to the cap, not rejected; keys
    that are not given keep their (currency-scaled) defaults.

    Args:
        values: Stored or user-supplied values as strings; ``None`` or
            empty for the defaults.
        currency: Account currency of money limits (defaults and hard caps
            scale with ``money_factor``).
        twd_limit_factor: Factor of TWD accounts.

    Returns:
        Effective limits.

    Raises:
        ExecutionError: ``VALIDATION_FAILED`` for unknown keys, unparsable
            values or negative values.
    """
    base = in_currency(RiskLimits(), currency, twd_limit_factor)
    caps = hard_limits(currency, twd_limit_factor)
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
    return dataclasses.replace(base, **updates)  # type: ignore[arg-type]  # each value has its field's type (int or Decimal), parsed above
