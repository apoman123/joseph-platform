# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Pydantic request bodies mirroring ``execution.schema.json``.

Field names are the contract's wire names. Decimal values travel as strings
so that no binary float rounding happens on the way in.

FastAPI publishes each model's class docstring as its description in the
generated OpenAPI document (``joseph-terminal/packages/contracts/openapi``,
checked in CI by ``scripts/export_openapi.py --check``), so changing a
docstring means regenerating that document (``make openapi``). The fields
are documented by comments for that reason.
"""

from __future__ import annotations

from typing import Annotated
from typing import Literal
from typing import TypeAlias

import pydantic

DecimalStr: TypeAlias = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=64
    ),
]
"""Plain decimal string, optionally negative, without exponent."""

PositiveStr: TypeAlias = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=64
    ),
]
"""Unsigned plain decimal string (the pattern also admits ``0``)."""

InstrumentIdStr: TypeAlias = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^(bybit|okx|coinbase|kraken|deriv|binance|hyperliquid|lighter|bitget|mexc|twse|tpex|nasdaq"
        r"|nyse|cboe|treasury|mock):(perp|spot|future|stock|etf|rate)"
        r":[A-Za-z0-9._/-]{1,64}$"
    ),
]
"""Canonical instrument id ``venue:kind:symbol``."""


class _Body(pydantic.BaseModel):
    """Base model rejecting unknown fields."""

    model_config = pydantic.ConfigDict(extra="forbid")


class StopLossBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Stop-loss leg."""

    trigger: DecimalStr
    # Price compared with the trigger.
    basis: Literal["mark", "last"] = "mark"


class TakeProfitBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Take-profit leg."""

    # Limit price of the take-profit order.
    trigger: DecimalStr
    # Share of the filled quantity, 0 < f <= 1; the legs of one bracket add
    # up to at most 1.
    fraction: PositiveStr = "1"


class BracketBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Bracket."""

    stop_loss: StopLossBody | None = None
    take_profit: list[TakeProfitBody] = pydantic.Field(
        default_factory=list, max_length=4
    )


class IntentBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``OrderIntentRequest`` (user-submitted sources only)."""

    account_id: str = pydantic.Field(min_length=1, max_length=128)
    instrument_id: InstrumentIdStr
    side: Literal["buy", "sell"]
    position_effect: Literal["open", "close", "reduce"]
    order_type: Literal["market", "limit", "stop_market", "stop_limit"]
    # Base-asset quantity; the domain rejects zero.
    quantity_base: PositiveStr
    # Given exactly for limit and stop-limit orders.
    limit_price: DecimalStr | None = None
    # Given exactly for stop-market and stop-limit orders.
    trigger_price: DecimalStr | None = None
    trigger_basis: Literal["mark", "last"] | None = None
    time_in_force: Literal["GTC", "IOC", "FOK", "POST_ONLY", "DAY"]
    # Required for close and reduce intents.
    reduce_only: bool
    margin_mode: Literal["isolated", "cross"] = "isolated"
    # None means 1 (unlevered).
    leverage: PositiveStr | None = None
    bracket: BracketBody | None = None
    mode: Literal["PAPER", "SHADOW", "LIVE"]
    # STRATEGY and EMERGENCY intents never come through this body.
    source: Literal["MANUAL_TICKET", "CHART", "QUICK_TRADE"]
    strategy_version_id: str | None = None
    signal_id: str | None = None
    # Optional intent expiry, UTC epoch milliseconds.
    expires_at_ms: int | None = None
    # Token from /v1/orders/preview binding these parameters; manual LIVE
    # orders need one.
    preview_token: str | None = pydantic.Field(default=None, max_length=256)
    # Allow execution outside regular trading hours (securities only).
    outside_rth: bool = False


class AmendBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``AmendRequest``."""

    # None keeps the current value of each of the three amendable fields;
    # a new quantity must exceed the filled quantity.
    limit_price: DecimalStr | None = None
    trigger_price: DecimalStr | None = None
    quantity_base: PositiveStr | None = None
    # Order version the client last saw; a different current version fails
    # with CONFLICT. None skips the check.
    expected_version: int | None = pydantic.Field(default=None, ge=0)


class CloseBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``ClosePositionRequest``."""

    # Share of the position to close, 0 < f <= 1.
    fraction: PositiveStr
    order_type: Literal["market", "limit"]
    limit_price: DecimalStr | None = None
    preview_token: str | None = pydantic.Field(default=None, max_length=256)


class KillSwitchBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``KillSwitchRequest``."""

    scope: Literal["GLOBAL", "ACCOUNT", "STRATEGY"]
    # Account id (ACCOUNT) or strategy version or strategy id (STRATEGY);
    # ignored for GLOBAL.
    target_id: str | None = None
    # True engages, False releases.
    engage: bool
    # On engage, cancel working opening orders in scope.
    cancel_open_orders: bool
    # On engage, close the positions in scope (GLOBAL and ACCOUNT only).
    flatten_positions: bool
    # Operator reason, audited.
    reason: str = pydantic.Field(min_length=1, max_length=500)


class LimitsBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Risk limit update."""

    account_id: str
    # RiskLimits field name to value string. Unknown names are rejected,
    # values above the hard caps are lowered to the caps, and limits not
    # given return to their defaults.
    limits: dict[str, str]


class DeploymentLimitsBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``DeploymentLimits``."""

    max_notional: PositiveStr
    max_open_positions: int = pydantic.Field(ge=0)
    max_daily_loss: PositiveStr
    allowed_instruments: list[InstrumentIdStr]
    max_leverage: PositiveStr


class SizingBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``SizingModel``."""

    model: Literal["RISK_PCT", "FIXED_QTY"]
    risk_pct: PositiveStr | None = None
    fixed_qty: PositiveStr | None = None


class DeploymentBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``DeploymentRequest``."""

    strategy_version_id: str = pydantic.Field(min_length=3, max_length=128)
    account_id: str
    mode: Literal["PAPER", "SHADOW", "AUTO_LIVE"]
    limits: DeploymentLimitsBody
    sizing: SizingBody


class LiveConsentBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """AUTO_LIVE consent request."""

    # TOTP (time-based one-time password) step-up code.
    step_up_code: str = pydantic.Field(min_length=6, max_length=8)
    # Must be true.
    acknowledge_risk: bool
    # Consent lifetime (default 24 hours; the service allows at most 7 days).
    duration_ms: int = pydantic.Field(default=86_400_000, gt=0)


class AccountLiveConsentBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Contract ``AccountLiveConsentRequest`` (account LIVE arming).

    The step-up code has the same shape as AUTO_LIVE deployment consent;
    the service enforces the acknowledgement and the configured maximum
    duration (and audits refusals).
    """

    step_up_code: str = pydantic.Field(min_length=6, max_length=8)
    acknowledge_risk: bool
    # Arming lifetime; None uses the configured maximum
    # (JOSEPH_LIVE_ARM_MAX_MS).
    duration_ms: int | None = pydantic.Field(default=None, gt=0)


class ConnectBody(_Body):  # noqa: GPY014 - the docstring is published as this model's OpenAPI description, a generated contract; fields are documented in comments.
    """Venue connection request (credentials are never echoed)."""

    venue: Literal[
        "binance",
        "hyperliquid",
        "lighter",
        "bitget",
        "mexc",
        "ibkr",
        "sinopac",
        "cathay",
    ]
    # Empty uses the venue as the label.
    label: str = pydantic.Field(default="", max_length=120)
    # Sealed by the vault; broker connections take only "account" (the
    # broker account number).
    credentials: dict[str, str]
    # The user confirms the key cannot withdraw; False is refused.
    withdrawal_permission_disabled: bool

    def __repr__(self) -> str:
        """Returns a representation without credential values."""
        return f"ConnectBody(venue={self.venue!r}, credentials=[REDACTED])"
