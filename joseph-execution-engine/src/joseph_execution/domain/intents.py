# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Order intents: the single entry point of every order path.

Manual tickets, chart trades, strategy signals and emergency closes all
produce an immutable ``OrderIntent`` that must pass the Risk Gateway before
the OMS may create venue orders.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import ids


@dataclasses.dataclass(frozen=True, slots=True)
class StopLossSpec:
    """A protective stop-loss leg.

    Attributes:
        trigger: Trigger price.
        basis: Price the trigger is compared with (mark or last).
    """

    trigger: decimal.Decimal
    basis: enums.PriceBasis = enums.PriceBasis.MARK


@dataclasses.dataclass(frozen=True, slots=True)
class TakeProfitSpec:
    """A take-profit leg.

    Attributes:
        trigger: Limit price of the take-profit order.
        fraction: Fraction of the filled quantity (0 < f <= 1); the legs of
            one bracket add up to at most 1.
    """

    trigger: decimal.Decimal
    fraction: decimal.Decimal = decimals.ONE


@dataclasses.dataclass(frozen=True, slots=True)
class Bracket:
    """Optional protective orders attached to an opening intent.

    Attributes:
        stop_loss: Stop-loss leg, if any.
        take_profit: Take-profit legs.
    """

    stop_loss: StopLossSpec | None = None
    take_profit: tuple[TakeProfitSpec, ...] = ()

    @property
    def empty(self) -> bool:
        """Whether no protective leg is requested."""
        return self.stop_loss is None and not self.take_profit


@dataclasses.dataclass(frozen=True, slots=True)
class OrderIntentRequest:
    """A client-submitted (or strategy-generated) order draft.

    Construction validates the structural invariants (``__post_init__``);
    the Risk Gateway checks everything that depends on state or market
    data.

    Attributes:
        account_id: Target account.
        instrument_id: Canonical instrument id.
        side: Buy or sell.
        position_effect: Open, close or reduce.
        order_type: Market, limit, stop market or stop limit.
        quantity_base: Base-asset quantity (positive).
        time_in_force: Time in force.
        reduce_only: Venue reduce-only flag; required for close and
            reduce intents.
        mode: PAPER, SHADOW or LIVE.
        source: Origin of the intent.
        limit_price: Limit price; set exactly for limit and stop-limit
            orders.
        trigger_price: Trigger price; set exactly for stop-market and
            stop-limit orders.
        trigger_basis: Trigger basis for stop orders.
        margin_mode: Margin mode.
        leverage: Requested leverage (positive; 1 means unlevered).
        bracket: Protective legs.
        strategy_version_id: Strategy version; required for STRATEGY
            intents.
        signal_id: Originating signal; required for STRATEGY intents.
        expires_at_ms: Optional intent expiry, UTC epoch milliseconds.
        preview_token: Token from a preview binding these parameters.
        outside_rth: Allow execution outside regular trading hours
            (Interactive Brokers ``outsideRth``; securities only).
    """

    account_id: str
    instrument_id: str
    side: enums.OrderSide
    position_effect: enums.PositionEffect
    order_type: enums.OrderType
    quantity_base: decimal.Decimal
    time_in_force: enums.TimeInForce
    reduce_only: bool
    mode: enums.OrderMode
    source: enums.IntentSource
    limit_price: decimal.Decimal | None = None
    trigger_price: decimal.Decimal | None = None
    trigger_basis: enums.PriceBasis | None = None
    margin_mode: enums.MarginMode = enums.MarginMode.ISOLATED
    leverage: decimal.Decimal = decimals.ONE
    bracket: Bracket = dataclasses.field(default_factory=Bracket)
    strategy_version_id: str | None = None
    signal_id: str | None = None
    expires_at_ms: int | None = None
    preview_token: str | None = None
    outside_rth: bool = False

    def __post_init__(self) -> None:
        """Validates structural invariants.

        Raises:
            ExecutionError: With ``VALIDATION_FAILED`` on inconsistent input;
                ``details["problems"]`` lists every problem found.
        """
        problems: list[str] = []
        if self.quantity_base <= decimals.ZERO:
            problems.append("quantity_base must be positive")
        needs_limit = self.order_type in (
            enums.OrderType.LIMIT,
            enums.OrderType.STOP_LIMIT,
        )
        needs_trigger = self.order_type in (
            enums.OrderType.STOP_MARKET,
            enums.OrderType.STOP_LIMIT,
        )
        if needs_limit and self.limit_price is None:
            problems.append("limit_price is required for limit orders")
        if not needs_limit and self.limit_price is not None:
            problems.append("limit_price is only valid for limit orders")
        if needs_trigger and self.trigger_price is None:
            problems.append("trigger_price is required for stop orders")
        if not needs_trigger and self.trigger_price is not None:
            problems.append("trigger_price is only valid for stop orders")
        for price in (self.limit_price, self.trigger_price):
            if price is not None and price <= decimals.ZERO:
                problems.append("prices must be positive")
        if self.leverage <= decimals.ZERO:
            problems.append("leverage must be positive")
        if (
            self.position_effect is not enums.PositionEffect.OPEN
            and not self.reduce_only
        ):
            problems.append("close/reduce intents must be reduce_only")
        if self.source is enums.IntentSource.STRATEGY and (
            not self.strategy_version_id or not self.signal_id
        ):
            problems.append(
                "STRATEGY intents need strategy_version_id and signal_id"
            )
        fractions = sum(
            (leg.fraction for leg in self.bracket.take_profit), decimals.ZERO
        )
        if fractions > decimals.ONE:
            problems.append("take_profit fractions exceed 1")
        if problems:
            raise errors.ExecutionError(
                "VALIDATION_FAILED",
                "; ".join(problems),
                details={"problems": problems},
            )

    @property
    def is_risk_reducing(self) -> bool:
        """Whether the intent can only reduce exposure (reduce-only)."""
        return self.reduce_only

    @property
    def price_hint(self) -> decimal.Decimal | None:
        """The limit price, else the trigger price; ``None`` for market."""
        return self.limit_price or self.trigger_price

    def body_hash(self) -> str:
        """Hashes the request without the preview token.

        Used to detect an idempotency key reused with a different body; the
        preview token is not part of the hashed body.

        Returns:
            Hex SHA-256 of the canonical request.
        """
        body = self.to_contract()
        body.pop("preview_token", None)
        return ids.sha256_hex(ids.canonical_json(body))

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``OrderIntentRequest``.

        Returns:
            JSON-compatible dict.
        """
        bracket: dict[str, object] | None = None
        if not self.bracket.empty:
            stop = self.bracket.stop_loss
            stop_body: dict[str, object] | None = None
            if stop is not None:
                stop_body = {
                    "trigger": decimals.fmt(stop.trigger),
                    "basis": stop.basis.value,
                }
            bracket = {
                "stop_loss": stop_body,
                "take_profit": [
                    {
                        "trigger": decimals.fmt(leg.trigger),
                        "fraction": decimals.fmt(leg.fraction),
                    }
                    for leg in self.bracket.take_profit
                ],
            }
        body: dict[str, object] = {
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "position_effect": self.position_effect.value,
            "order_type": self.order_type.value,
            "quantity_base": decimals.fmt(self.quantity_base),
            "limit_price": decimals.fmt_optional(self.limit_price),
            "trigger_price": decimals.fmt_optional(self.trigger_price),
            "trigger_basis": None
            if self.trigger_basis is None
            else self.trigger_basis.value,
            "time_in_force": self.time_in_force.value,
            "reduce_only": self.reduce_only,
            "margin_mode": self.margin_mode.value,
            "leverage": decimals.fmt(self.leverage),
            "bracket": bracket,
            "mode": self.mode.value,
            "source": self.source.value,
            "strategy_version_id": self.strategy_version_id,
            "signal_id": self.signal_id,
            "expires_at_ms": self.expires_at_ms,
            "preview_token": self.preview_token,
        }
        if self.outside_rth:
            # Emitted only when set so body hashes and preview tokens of
            # requests without the flag are unchanged.
            body["outside_rth"] = True
        return body

    @classmethod
    def from_contract(cls, body: Mapping[str, object]) -> OrderIntentRequest:
        """Builds a request from a contract-shaped dict.

        Optional fields fall back to the defaults of the dataclass.

        Args:
            body: Contract ``OrderIntentRequest`` object.

        Returns:
            The domain request.

        Raises:
            ExecutionError: With ``VALIDATION_FAILED`` when a field is
                missing or invalid, or the request breaks an invariant.
        """
        try:
            raw_bracket = body.get("bracket")
            bracket = Bracket()
            if isinstance(raw_bracket, Mapping):
                raw_stop = raw_bracket.get("stop_loss")
                stop = None
                if isinstance(raw_stop, Mapping):
                    stop = StopLossSpec(
                        trigger=decimals.to_decimal(raw_stop["trigger"]),
                        basis=enums.PriceBasis(
                            str(raw_stop.get("basis", "mark"))
                        ),
                    )
                legs_value = raw_bracket.get("take_profit")
                legs_raw: list[Mapping[str, object]] = (
                    legs_value if isinstance(legs_value, list) else []
                )
                legs = tuple(
                    TakeProfitSpec(
                        trigger=decimals.to_decimal(leg["trigger"]),
                        fraction=decimals.to_decimal(leg.get("fraction", "1")),
                    )
                    for leg in legs_raw
                )
                bracket = Bracket(stop_loss=stop, take_profit=legs)
            basis = body.get("trigger_basis")
            leverage = body.get("leverage")
            margin = body.get("margin_mode")
            expires = body.get("expires_at_ms")
            return cls(
                account_id=str(body["account_id"]),
                instrument_id=str(body["instrument_id"]),
                side=enums.OrderSide(str(body["side"])),
                position_effect=enums.PositionEffect(
                    str(body["position_effect"])
                ),
                order_type=enums.OrderType(str(body["order_type"])),
                quantity_base=decimals.to_decimal(body["quantity_base"]),
                time_in_force=enums.TimeInForce(str(body["time_in_force"])),
                reduce_only=bool(body["reduce_only"]),
                mode=enums.OrderMode(str(body["mode"])),
                source=enums.IntentSource(str(body["source"])),
                limit_price=decimals.to_optional(body.get("limit_price")),
                trigger_price=decimals.to_optional(body.get("trigger_price")),
                trigger_basis=None
                if basis is None
                else enums.PriceBasis(str(basis)),
                margin_mode=enums.MarginMode(str(margin))
                if margin
                else enums.MarginMode.ISOLATED,
                leverage=decimals.to_decimal(leverage)
                if leverage
                else decimals.ONE,
                bracket=bracket,
                strategy_version_id=_opt_str(body.get("strategy_version_id")),
                signal_id=_opt_str(body.get("signal_id")),
                expires_at_ms=int(str(expires))
                if expires is not None
                else None,
                preview_token=_opt_str(body.get("preview_token")),
                outside_rth=bool(body.get("outside_rth", False)),
            )
        except (KeyError, ValueError, TypeError) as err:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"invalid order intent: {err}"
            ) from err


def _opt_str(value: object) -> str | None:
    """Converts an optional value to ``str``.

    Args:
        value: Value or ``None``.

    Returns:
        ``str(value)``, or ``None`` when ``value`` is ``None``.
    """
    return None if value is None else str(value)


@dataclasses.dataclass(frozen=True, slots=True)
class OrderIntent:
    """An immutable, server-side order intent.

    Attributes:
        intent_id: UUIDv7 generated by the server.
        tenant_id: Owning tenant.
        user_id: Acting user (or service principal).
        idempotency_key: Client-supplied idempotency key, unique per
            tenant.
        client_order_id: Venue client order id of the primary order.
        created_at_ms: Creation time, UTC epoch milliseconds.
        request: The validated request.
        deployment_id: Deployment for STRATEGY intents, if any; not part of
            the contract form.
    """

    intent_id: str
    tenant_id: str
    user_id: str
    idempotency_key: str
    client_order_id: str
    created_at_ms: int
    request: OrderIntentRequest
    deployment_id: str | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``OrderIntent``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "intent_id": self.intent_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "idempotency_key": self.idempotency_key,
            "client_order_id": self.client_order_id,
            "created_at_ms": self.created_at_ms,
            "request": self.request.to_contract(),
        }
