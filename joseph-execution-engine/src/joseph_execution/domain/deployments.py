# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Strategy deployments and scoped AUTO_LIVE consent.

A deployment binds one immutable strategy version to one account. AUTO_LIVE
stays PENDING_CONSENT until a scoped, expiring consent exists; consent is
bound by hash to the account, venue, strategy version, allowed instruments,
notional and leverage caps and expiry, so changing any of them (or
deploying a new version) invalidates it. All times are UTC epoch
milliseconds.
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
class DeploymentLimits:
    """Per-deployment limits, applied on top of the account's limits.

    Where both set a cap, the stricter one applies to the deployment's
    orders.

    Attributes:
        max_notional: Maximum notional per order, in the account currency.
        max_open_positions: Maximum open positions of the account when an
            order of the deployment opens a new one.
        max_daily_loss: Cap on the account's realized loss since 00:00 UTC
            (a positive amount); reaching it pauses new exposure.
        allowed_instruments: Instrument allow-list. Empty means any for
            PAPER/SHADOW; AUTO_LIVE trades listed instruments only, and
            its consent requires a non-empty list.
        max_leverage: Leverage cap.
    """

    max_notional: decimal.Decimal
    max_open_positions: int
    max_daily_loss: decimal.Decimal
    allowed_instruments: tuple[str, ...]
    max_leverage: decimal.Decimal

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``DeploymentLimits``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "max_notional": decimals.fmt(self.max_notional),
            "max_open_positions": self.max_open_positions,
            "max_daily_loss": decimals.fmt(self.max_daily_loss),
            "allowed_instruments": list(self.allowed_instruments),
            "max_leverage": decimals.fmt(self.max_leverage),
        }

    @classmethod
    def from_contract(cls, body: Mapping[str, object]) -> DeploymentLimits:
        """Parses contract limits.

        Args:
            body: Contract ``DeploymentLimits``; a missing or empty
                ``allowed_instruments`` gives an empty allow-list.

        Returns:
            Limits.

        Raises:
            KeyError: If a required field is missing.
            ValueError: If a number is invalid.
        """
        instruments = body.get("allowed_instruments") or []
        return cls(
            max_notional=decimals.to_decimal(body["max_notional"]),
            max_open_positions=int(str(body["max_open_positions"])),
            max_daily_loss=decimals.to_decimal(body["max_daily_loss"]),
            allowed_instruments=tuple(
                str(x)
                for x in instruments  # type: ignore[attr-defined]  # untyped JSON; the contract makes it a list of ids.
            ),
            max_leverage=decimals.to_decimal(body["max_leverage"]),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SizingModel:
    """How strategy signals are converted into quantities.

    Attributes:
        model: ``RISK_PCT`` or ``FIXED_QTY``.
        risk_pct: Percent of equity risked between entry and stop, in
            ``(0, 5]``; required for ``RISK_PCT``.
        fixed_qty: Fixed base quantity, positive; required for
            ``FIXED_QTY``.
    """

    model: str
    risk_pct: decimal.Decimal | None = None
    fixed_qty: decimal.Decimal | None = None

    def __post_init__(self) -> None:
        """Validates the sizing model.

        Raises:
            ExecutionError: With ``VALIDATION_FAILED`` for an unknown model
                or when the model's own field is missing or out of range.
        """
        if self.model == "RISK_PCT":
            if self.risk_pct is None or not (
                decimals.ZERO < self.risk_pct <= decimal.Decimal(5)
            ):
                raise errors.ExecutionError(
                    "VALIDATION_FAILED", "risk_pct must be in (0, 5]"
                )
        elif self.model == "FIXED_QTY":
            if self.fixed_qty is None or self.fixed_qty <= decimals.ZERO:
                raise errors.ExecutionError(
                    "VALIDATION_FAILED", "fixed_qty must be positive"
                )
        else:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"unknown sizing model {self.model}"
            )

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``SizingModel``.

        Returns:
            JSON-compatible dict.
        """
        body: dict[str, object] = {"model": self.model}
        if self.risk_pct is not None:
            body["risk_pct"] = decimals.fmt(self.risk_pct)
        if self.fixed_qty is not None:
            body["fixed_qty"] = decimals.fmt(self.fixed_qty)
        return body

    @classmethod
    def from_contract(cls, body: Mapping[str, object]) -> SizingModel:
        """Parses a contract sizing model.

        Args:
            body: Contract ``SizingModel``.

        Returns:
            Sizing model.

        Raises:
            KeyError: If ``model`` is missing.
            ValueError: If a number is invalid.
            ExecutionError: With ``VALIDATION_FAILED`` from validation.
        """
        return cls(
            model=str(body["model"]),
            risk_pct=decimals.to_optional(body.get("risk_pct")),
            fixed_qty=decimals.to_optional(body.get("fixed_qty")),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class LiveConsent:
    """Explicit, expiring user consent for AUTO_LIVE.

    Attributes:
        consented_at_ms: When consent was given.
        expires_at_ms: When it lapses; it is expired from this instant on.
        scope_hash: ``consent_scope_hash`` of the consented scope.
        actor: Consenting user, as an audit actor label.
    """

    consented_at_ms: int
    expires_at_ms: int
    scope_hash: str
    actor: str

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``LiveConsent``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "consented_at_ms": self.consented_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "scope_hash": self.scope_hash,
            "actor": self.actor,
        }


def consent_scope_hash(
    account_id: str,
    venue: str,
    strategy_version_id: str,
    limits: DeploymentLimits,
    expires_at_ms: int,
) -> str:
    """Hashes the scope that AUTO_LIVE consent covers.

    Args:
        account_id: Account.
        venue: Account venue.
        strategy_version_id: Exact immutable strategy version.
        limits: Deployment limits; the allowed instruments (in any order),
            ``max_notional`` and ``max_leverage`` are bound, the loss and
            position limits are not.
        expires_at_ms: Consent expiry.

    Returns:
        ``sha256:<hex>`` scope hash.
    """
    scope = {
        "account_id": account_id,
        "venue": venue,
        "strategy_version_id": strategy_version_id,
        "allowed_instruments": sorted(limits.allowed_instruments),
        "max_notional": decimals.fmt(limits.max_notional),
        "max_leverage": decimals.fmt(limits.max_leverage),
        "expires_at_ms": expires_at_ms,
    }
    return "sha256:" + ids.sha256_hex(ids.canonical_json(scope))


@dataclasses.dataclass(frozen=True, slots=True)
class Deployment:
    """A binding of one strategy version to one account.

    Attributes:
        deployment_id: Identifier.
        tenant_id: Owning tenant.
        strategy_version_id: Immutable strategy version.
        account_id: Executing account.
        mode: PAPER, SHADOW or AUTO_LIVE.
        state: Lifecycle state.
        limits: Deployment limits.
        sizing: Sizing model.
        created_at_ms: Creation time.
        updated_at_ms: Last change.
        live_consent: Consent for AUTO_LIVE, if given (dropped when the
            deployment is disabled).
    """

    deployment_id: str
    tenant_id: str
    strategy_version_id: str
    account_id: str
    mode: enums.DeploymentMode
    state: enums.DeploymentState
    limits: DeploymentLimits
    sizing: SizingModel
    created_at_ms: int
    updated_at_ms: int
    live_consent: LiveConsent | None = None

    def consent_valid(self, venue: str, now_ms: int) -> bool:
        """Checks that AUTO_LIVE consent exists, is unexpired and in scope.

        Args:
            venue: Venue of the deployment's account, as it is now.
            now_ms: Current time.

        Returns:
            True only when consent exists, ``now_ms`` is before its expiry
            and its scope hash matches the deployment's current account,
            venue, strategy version and limits.
        """
        consent = self.live_consent
        if consent is None or now_ms >= consent.expires_at_ms:
            return False
        expected = consent_scope_hash(
            self.account_id,
            venue,
            self.strategy_version_id,
            self.limits,
            consent.expires_at_ms,
        )
        return expected == consent.scope_hash

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Deployment``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "deployment_id": self.deployment_id,
            "tenant_id": self.tenant_id,
            "strategy_version_id": self.strategy_version_id,
            "account_id": self.account_id,
            "mode": self.mode.value,
            "state": self.state.value,
            "limits": self.limits.to_contract(),
            "sizing": self.sizing.to_contract(),
            "live_consent": None
            if self.live_consent is None
            else self.live_consent.to_contract(),
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }
