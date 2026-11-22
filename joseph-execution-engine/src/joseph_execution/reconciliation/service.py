"""Periodic reconciliation against venue truth.

Each pass (1) resolves due outbox items, including UNKNOWN_RECONCILING
orders, (2) pulls recent fills so missed stream events are applied, and
(3) compares positions; a mismatch marks the position unknown, which the
Risk Gateway treats as a block on new exposure until it matches again.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence

from joseph_execution.audit import service as audit_service
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import positions
from joseph_execution.oms import service as oms_service
from joseph_execution.ports import repositories
from joseph_execution.ports import services
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Summary of one pass.

    Attributes:
        outbox_items: Outbox items processed.
        fills_applied: Missed fills applied.
        mismatches: Positions flagged unknown.
        cleared: Positions that matched again.
        lag_ms: Time since the previous pass.
    """

    outbox_items: int
    fills_applied: int
    mismatches: int
    cleared: int
    lag_ms: int


class Reconciler:
    """Runs reconciliation passes for simulated and connected accounts."""

    def __init__(  # noqa: PLR0917
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        resolver: trading.AdapterResolver,
        oms: oms_service.OmsService,
        audit_log: audit_service.AuditLog,
        clock: services.Clock,
        metrics: services.Metrics,
    ) -> None:
        """Initializes the reconciler.

        Args:
            uow_factory: Unit of work factory.
            resolver: Adapter resolver.
            oms: OMS.
            audit_log: Audit log.
            clock: Time source.
            metrics: Metrics sink.
        """
        self._uow = uow_factory
        self._resolver = resolver
        self._oms = oms
        self._audit = audit_log
        self._clock = clock
        self._metrics = metrics
        self._last_run_ms: int | None = None

    async def run_once(self) -> ReconcileReport:
        """Runs one reconciliation pass.

        Returns:
            Report.
        """
        now = self._clock.now_ms()
        lag = 0 if self._last_run_ms is None else now - self._last_run_ms
        self._last_run_ms = now
        processed = await self._oms.dispatch_pending()
        fills_applied = mismatches = cleared = 0
        with self._uow() as uow:
            scopes: set[tuple[str, enums.OrderMode]] = set()
            for pos in uow.positions.list(include_flat=True):
                scopes.add((pos.account_id, pos.mode))
            for order in uow.orders.list(limit=2000):
                if not order.state.is_terminal:
                    scopes.add((order.account_id, order.mode))
            accounts_by_id = {
                account_id: uow.accounts.get(account_id)
                for account_id, _ in scopes
            }
        for account_id, mode in sorted(scopes):
            account = accounts_by_id.get(account_id)
            if account is None or mode is enums.OrderMode.LIVE:
                continue
            adapter = self._resolver.resolve(account, mode)
            try:
                fills = await adapter.list_recent_fills(account_id, 0)
                fills_applied += await self._oms.apply_venue_fills(
                    account_id, fills
                )
                venue_positions = await adapter.list_positions(account_id)
            except errors.ExecutionError as err:
                _LOG.warning("reconcile of %s failed: %s", account_id, err.code)
                continue
            found, fixed = self._compare(account_id, mode, venue_positions)
            mismatches += found
            cleared += fixed
        self._metrics.gauge("reconcile_lag_ms", float(lag))
        if mismatches:
            self._metrics.increment("reconcile_mismatch_total")
        return ReconcileReport(
            processed, fills_applied, mismatches, cleared, lag
        )

    def _compare(
        self,
        account_id: str,
        mode: enums.OrderMode,
        venue_positions: Sequence[trading.VenuePosition],
    ) -> tuple[int, int]:
        """Compares local and venue positions and flags differences.

        Args:
            account_id: Account.
            mode: Mode.
            venue_positions: Venue positions.

        Returns:
            ``(mismatches, cleared)``.
        """
        venue = {p.instrument_id: p.signed_qty for p in venue_positions}
        mismatches = cleared = 0
        now = self._clock.now_ms()
        with self._uow() as uow:
            local = [
                p
                for p in uow.positions.list(
                    account_id=account_id, include_flat=True
                )
                if p.mode is mode
            ]
            seen = set()
            for pos in local:
                seen.add(pos.instrument_id)
                expected = venue.get(pos.instrument_id, 0)
                matches = pos.signed_qty == expected
                if not matches and not pos.unknown:
                    pos.unknown = True
                    pos.updated_at_ms = now
                    uow.positions.save(pos)
                    mismatches += 1
                    self._audit.record(
                        uow,
                        tenant_id=pos.tenant_id,
                        actor="system:reconciler",
                        action="position.mismatch",
                        entity_type="position",
                        entity_id=pos.position_id,
                        account_id=account_id,
                        payload={
                            "local": str(pos.signed_qty),
                            "venue": str(expected),
                        },
                    )
                elif matches and pos.unknown:
                    pos.unknown = False
                    pos.updated_at_ms = now
                    uow.positions.save(pos)
                    cleared += 1
            for instrument_id, qty in venue.items():
                if instrument_id in seen or qty == 0:
                    continue
                account = uow.accounts.get(account_id)
                if account is None:
                    continue
                ghost = positions.Position(
                    position_id=positions.position_id(
                        account_id, instrument_id, mode.value
                    ),
                    tenant_id=account.tenant_id,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    mode=mode,
                    updated_at_ms=now,
                    unknown=True,
                )
                uow.positions.save(ghost)
                mismatches += 1
                self._audit.record(
                    uow,
                    tenant_id=account.tenant_id,
                    actor="system:reconciler",
                    action="position.unexpected",
                    entity_type="position",
                    entity_id=ghost.position_id,
                    account_id=account_id,
                    payload={"venue": str(qty)},
                )
        return mismatches, cleared
