# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Kill switches: an engaged switch blocks new exposure.

While an engaged switch covers an order (``KillSwitch.applies_to``), the
risk check ``kill_switch.engaged`` fails it unless it is risk-reducing
(reduce-only, opposite to the position and no larger than it); risk-reducing
orders pass with a warning. Cancelling open orders and flattening positions
are separate, explicit options; engaging a switch never silently cancels or
flattens anything.
"""

from __future__ import annotations

import dataclasses

from joseph_execution.domain import enums


@dataclasses.dataclass(frozen=True, slots=True)
class KillSwitch:
    """Kill switch state for one scope.

    Attributes:
        tenant_id: Owning tenant.
        scope: GLOBAL (whole tenant), ACCOUNT or STRATEGY.
        target_id: Account id or strategy version/id; ``None`` for GLOBAL.
        engaged: Whether new exposure is blocked.
        cancel_open_orders: Whether engaging requested the cancellation of
            working opening orders (always False when released).
        flatten_positions: Whether engaging requested closing the positions
            in scope (always False when released).
        reason: Operator reason.
        actor: Who changed it.
        changed_at_ms: When it changed, UTC epoch milliseconds.
    """

    tenant_id: str
    scope: enums.KillSwitchScope
    target_id: str | None
    engaged: bool
    cancel_open_orders: bool
    flatten_positions: bool
    reason: str
    actor: str
    changed_at_ms: int

    def applies_to(
        self, account_id: str, strategy_version_id: str | None
    ) -> bool:
        """Checks whether this switch covers an order.

        A released switch covers nothing. GLOBAL covers every order, ACCOUNT
        the orders of ``target_id``, and STRATEGY the orders whose strategy
        version equals ``target_id`` or whose strategy id (the part before
        ``:v``) does, so ``my-strategy`` covers ``my-strategy:v3``. The
        tenant is not compared: callers pass only the tenant's switches.

        Args:
            account_id: Order account.
            strategy_version_id: Order strategy version, if any; orders
                without one are never covered by a STRATEGY switch.

        Returns:
            True when engaged and in scope.
        """
        if not self.engaged:
            return False
        if self.scope is enums.KillSwitchScope.GLOBAL:
            return True
        if self.scope is enums.KillSwitchScope.ACCOUNT:
            return self.target_id == account_id
        if strategy_version_id is None or self.target_id is None:
            return False
        return strategy_version_id == self.target_id or (
            strategy_version_id.split(":v")[0] == self.target_id
        )

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``KillSwitchState``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "scope": self.scope.value,
            "target_id": self.target_id,
            "engaged": self.engaged,
            "cancel_open_orders": self.cancel_open_orders,
            "flatten_positions": self.flatten_positions,
            "reason": self.reason,
            "actor": self.actor,
            "changed_at_ms": self.changed_at_ms,
        }
