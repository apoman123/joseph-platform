# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Pydantic request bodies mirroring ``execution.schema.json``."""

from __future__ import annotations

from typing import Annotated
from typing import Literal

import pydantic

DecimalStr = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=64
    ),
]
PositiveStr = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=64
    ),
]
InstrumentIdStr = Annotated[
    str,
    pydantic.StringConstraints(
        pattern=r"^(binance|hyperliquid|lighter|bitget|mexc|twse|tpex|nasdaq"
        r"|nyse|cboe|treasury|mock):(perp|spot|future|stock|etf|rate)"
        r":[A-Za-z0-9._/-]{1,64}$"
    ),
]


class _Body(pydantic.BaseModel):
    """Base model rejecting unknown fields."""

    model_config = pydantic.ConfigDict(extra="forbid")


class StopLossBody(_Body):
    """Stop-loss leg."""

    trigger: DecimalStr
    basis: Literal["mark", "last"] = "mark"


class TakeProfitBody(_Body):
    """Take-profit leg."""

    trigger: DecimalStr
    fraction: PositiveStr = "1"


class BracketBody(_Body):
    """Bracket."""

    stop_loss: StopLossBody | None = None
    take_profit: list[TakeProfitBody] = pydantic.Field(
        default_factory=list, max_length=4
    )


class IntentBody(_Body):
    """Contract ``OrderIntentRequest`` (user-submitted sources only)."""

    account_id: str = pydantic.Field(min_length=1, max_length=128)
    instrument_id: InstrumentIdStr
    side: Literal["buy", "sell"]
    position_effect: Literal["open", "close", "reduce"]
    order_type: Literal["market", "limit", "stop_market", "stop_limit"]
    quantity_base: PositiveStr
    limit_price: DecimalStr | None = None
    trigger_price: DecimalStr | None = None
    trigger_basis: Literal["mark", "last"] | None = None
    time_in_force: Literal["GTC", "IOC", "FOK", "POST_ONLY", "DAY"]
    reduce_only: bool
    margin_mode: Literal["isolated", "cross"] = "isolated"
    leverage: PositiveStr | None = None
    bracket: BracketBody | None = None
    mode: Literal["PAPER", "SHADOW", "LIVE"]
    source: Literal["MANUAL_TICKET", "CHART", "QUICK_TRADE"]
    strategy_version_id: str | None = None
    signal_id: str | None = None
    expires_at_ms: int | None = None
    preview_token: str | None = pydantic.Field(default=None, max_length=256)
    outside_rth: bool = False


class AmendBody(_Body):
    """Contract ``AmendRequest``."""

    limit_price: DecimalStr | None = None
    trigger_price: DecimalStr | None = None
    quantity_base: PositiveStr | None = None
    expected_version: int | None = pydantic.Field(default=None, ge=0)


class CloseBody(_Body):
    """Contract ``ClosePositionRequest``."""

    fraction: PositiveStr
    order_type: Literal["market", "limit"]
    limit_price: DecimalStr | None = None
    preview_token: str | None = pydantic.Field(default=None, max_length=256)


class KillSwitchBody(_Body):
    """Contract ``KillSwitchRequest``."""

    scope: Literal["GLOBAL", "ACCOUNT", "STRATEGY"]
    target_id: str | None = None
    engage: bool
    cancel_open_orders: bool
    flatten_positions: bool
    reason: str = pydantic.Field(min_length=1, max_length=500)


class LimitsBody(_Body):
    """Risk limit update."""

    account_id: str
    limits: dict[str, str]


class DeploymentLimitsBody(_Body):
    """Contract ``DeploymentLimits``."""

    max_notional: PositiveStr
    max_open_positions: int = pydantic.Field(ge=0)
    max_daily_loss: PositiveStr
    allowed_instruments: list[InstrumentIdStr]
    max_leverage: PositiveStr


class SizingBody(_Body):
    """Contract ``SizingModel``."""

    model: Literal["RISK_PCT", "FIXED_QTY"]
    risk_pct: PositiveStr | None = None
    fixed_qty: PositiveStr | None = None


class DeploymentBody(_Body):
    """Contract ``DeploymentRequest``."""

    strategy_version_id: str = pydantic.Field(min_length=3, max_length=128)
    account_id: str
    mode: Literal["PAPER", "SHADOW", "AUTO_LIVE"]
    limits: DeploymentLimitsBody
    sizing: SizingBody


class LiveConsentBody(_Body):
    """AUTO_LIVE consent request."""

    step_up_code: str = pydantic.Field(min_length=6, max_length=8)
    acknowledge_risk: bool
    duration_ms: int = pydantic.Field(default=86_400_000, gt=0)


class AccountLiveConsentBody(_Body):
    """Contract ``AccountLiveConsentRequest`` (account LIVE arming).

    The step-up code has the same shape as AUTO_LIVE deployment consent;
    the service enforces the acknowledgement and the configured maximum
    duration (and audits refusals).
    """

    step_up_code: str = pydantic.Field(min_length=6, max_length=8)
    acknowledge_risk: bool
    duration_ms: int | None = pydantic.Field(default=None, gt=0)


class ConnectBody(_Body):
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
    label: str = pydantic.Field(default="", max_length=120)
    credentials: dict[str, str]
    withdrawal_permission_disabled: bool

    def __repr__(self) -> str:
        """Returns a representation without credential values."""
        return f"ConnectBody(venue={self.venue!r}, credentials=[REDACTED])"
