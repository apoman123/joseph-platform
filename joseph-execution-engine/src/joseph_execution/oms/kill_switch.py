# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Kill switch service.

Engaging blocks new opening orders immediately (the Risk Gateway reads the
switch on every intent). Cancelling working orders and flattening positions
are separate explicit options executed through the normal OMS path, so they
remain audited and reduce-only.
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
    """Engages, releases and lists kill switches."""

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
            publisher: Stream publisher.
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
            Switches.
        """
        with self._uow() as uow:
            return uow.kill_switches.list(principal.tenant_id)

    async def set(  # noqa: PLR0917
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

        Args:
            principal: Caller.
            scope: Scope.
            target_id: Account or strategy for scoped switches.
            engage: Engage (True) or release (False).
            cancel_open_orders: Cancel working opening orders on engage.
            flatten_positions: Close positions in scope on engage.
            reason: Operator reason.

        Returns:
            New state.

        Raises:
            ExecutionError: ``VALIDATION_FAILED`` or ``NOT_FOUND``.
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
            in_scope_orders = (
                [
                    o
                    for o in uow.orders.list(tenant_id=principal.tenant_id)
                    if o.state.is_working
                    and not o.reduce_only
                    and switch.applies_to(o.account_id, o.strategy_version_id)
                ]
                if engage and cancel_open_orders
                else []
            )
            in_scope_positions = (
                [
                    p
                    for p in uow.positions.list(tenant_id=principal.tenant_id)
                    if self._position_in_scope(switch, p.account_id)
                ]
                if engage and flatten_positions
                else []
            )
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

    @staticmethod
    def _position_in_scope(
        switch: kill_switch.KillSwitch, account_id: str
    ) -> bool:
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
