# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Kill switch service.

Engaging a switch blocks new exposure from the next intent on: the OMS
(order management system) puts the tenant's switches into the risk context
of every intent, and the Risk Gateway check ``kill_switch.engaged`` fails
every order in scope that is not risk-reducing. Cancelling working orders
and flattening positions are separate, explicit options of engaging. They
run through the normal OMS path, so they stay audited, and flattening
orders are reduce-only.
"""

from __future__ import annotations

import logging

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import decimals
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import identity
from joseph_execution.domain import kill_switch
from joseph_execution.oms import service as oms_service
from joseph_execution.ports import repositories
from joseph_execution.ports import services

_LOG = logging.getLogger(__name__)


class KillSwitchService:
    """Engages, releases and lists the kill switches of tenants."""

    def __init__(
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        oms: oms_service.OmsService,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
        publisher: services.EventPublisher,
    ) -> None:
        """Initializes the service.

        Args:
            uow_factory: Unit of work factory.
            oms: OMS for cancels and closes.
            audit_log: Audit log.
            clock: Time source.
            publisher: Stream publisher of ``risk.kill_switch`` events.
        """
        self._uow = uow_factory
        self._oms = oms
        self._audit = audit_log
        self._clock = clock
        self._publisher = publisher

    def list(
        self, principal: identity.Principal
    ) -> list[kill_switch.KillSwitch]:
        """Lists the tenant's switches.

        Args:
            principal: Caller.

        Returns:
            Every stored switch of the caller's tenant, engaged or released.
        """
        with self._uow() as uow:
            return uow.kill_switches.list(principal.tenant_id)

    async def set(  # noqa: PLR0917 - the API router and tests pass the kill switch body fields positionally.
        self,
        principal: identity.Principal,
        scope: enums.KillSwitchScope,
        target_id: str | None,
        engage: bool,
        cancel_open_orders: bool,
        flatten_positions: bool,
        reason: str,
    ) -> kill_switch.KillSwitch:
        """Engages or releases a switch.

        The new state replaces the stored switch of the same scope and
        target, and is audited (``kill_switch.engaged`` or
        ``kill_switch.released``) in the same transaction. After the
        commit it is published, then the optional actions run: each
        working, non-reduce-only order in scope is cancelled, and for each
        open position in scope a full close (fraction 1) is submitted as a
        reduce-only MARKET intent with source EMERGENCY, through the risk
        checks like any order. A failing cancel or close is logged and
        skipped; it does not undo the switch or stop the other actions.

        Args:
            principal: Caller.
            scope: Scope.
            target_id: Account id (ACCOUNT) or strategy version or strategy
                id (STRATEGY); ignored for GLOBAL.
            engage: Engage (True) or release (False).
            cancel_open_orders: Cancel working opening orders on engage;
                ignored on release.
            flatten_positions: Close positions in scope on engage (GLOBAL
                and ACCOUNT switches only); ignored on release.
            reason: Operator reason.

        Returns:
            New state.

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` when a scoped switch has no
                target, or ``NOT_FOUND`` when an ACCOUNT target is not an
                account of the caller's tenant.
        """
        if scope is enums.KillSwitchScope.GLOBAL:
            target_id = None
        elif not target_id:
            raise errors.ExecutionError(
                "VALIDATION_FAILED", f"{scope.value} scope needs target_id"
            )
        now = self._clock.now_ms()
        switch = kill_switch.KillSwitch(
            tenant_id=principal.tenant_id,
            scope=scope,
            target_id=target_id,
            engaged=engage,
            cancel_open_orders=engage and cancel_open_orders,
            flatten_positions=engage and flatten_positions,
            reason=reason,
            actor=principal.actor,
            changed_at_ms=now,
        )
        with self._uow() as uow:
            if scope is enums.KillSwitchScope.ACCOUNT:
                account = uow.accounts.get(target_id or "")
                if account is None or account.tenant_id != (
                    principal.tenant_id
                ):
                    raise errors.ExecutionError(
                        "NOT_FOUND", "account not found"
                    )
            uow.kill_switches.upsert(switch)
            self._audit.record(
                uow,
                tenant_id=principal.tenant_id,
                actor=principal.actor,
                action="kill_switch.engaged"
                if engage
                else "kill_switch.released",
                entity_type="kill_switch",
                entity_id=f"{scope.value}:{target_id or '*'}",
                account_id=target_id
                if scope is enums.KillSwitchScope.ACCOUNT
                else None,
                payload=switch.to_contract(),
            )
            if engage and cancel_open_orders:
                in_scope_orders = [
                    o
                    for o in uow.orders.list(tenant_id=principal.tenant_id)
                    if o.state.is_working
                    and not o.reduce_only
                    and switch.applies_to(o.account_id, o.strategy_version_id)
                ]
            else:
                in_scope_orders = []
            if engage and flatten_positions:
                in_scope_positions = [
                    p
                    for p in uow.positions.list(tenant_id=principal.tenant_id)
                    if _position_in_scope(switch, p.account_id)
                ]
            else:
                in_scope_positions = []
        self._publisher.publish(
            principal.tenant_id,
            "risk.kill_switch",
            "risk",
            switch.to_contract(),
        )
        for order in in_scope_orders:
            try:
                await self._oms.cancel_order(principal, order.order_id)
            except errors.ExecutionError as err:
                _LOG.warning(
                    "kill switch cancel of %s failed: %s",
                    order.order_id,
                    err.code,
                )
        for pos in in_scope_positions:
            try:
                request = await self._oms.close_request(
                    principal,
                    pos.position_id,
                    decimals.ONE,
                    enums.OrderType.MARKET,
                    None,
                    source=enums.IntentSource.EMERGENCY,
                )
                await self._oms.submit_intent(
                    principal,
                    request,
                    f"kill-switch:{now}:{pos.position_id}",
                )
            except errors.ExecutionError as err:
                _LOG.warning(
                    "kill switch flatten of %s failed: %s",
                    pos.position_id,
                    err.code,
                )
        return switch


def _position_in_scope(switch: kill_switch.KillSwitch, account_id: str) -> bool:
    """Checks whether a position's account is covered by a switch.

    Strategy switches never flatten whole positions because positions
    are netted across strategies.

    Args:
        switch: Switch.
        account_id: Position account.

    Returns:
        True when the position should be flattened.
    """
    if switch.scope is enums.KillSwitchScope.GLOBAL:
        return True
    if switch.scope is enums.KillSwitchScope.ACCOUNT:
        return switch.target_id == account_id
    return False
