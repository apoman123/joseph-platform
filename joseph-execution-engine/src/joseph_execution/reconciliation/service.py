# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Periodic reconciliation against venue truth.

Each pass (1) runs the due outbox items through the order management
service (OMS), which resolves UNKNOWN_RECONCILING orders, (2) pulls recent
fills so missed stream events are applied, and (3) compares positions; a
mismatch marks the position unknown, which the Risk Gateway treats as a
block on new exposure until it matches again. LIVE accounts are reconciled
against their venue adapter the same way (an adapter without credentials
refuses and the account is skipped for that pass).
Holdings the adapter reports as unmanaged (``VenuePosition.managed`` is
False, e.g. emerging-board stocks in a SinoPac account) are not compared:
Joseph can neither trade nor match them, so they must not block the
account. Each is logged once with the adapter's note and counted.
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
    """Summary of one reconciliation pass.

    Attributes:
        outbox_items: Due outbox items handed to the OMS.
        fills_applied: Venue fills that were new to the OMS.
        mismatches: Positions newly flagged unknown, including venue
            holdings with no local position.
        cleared: Flagged positions that match the venue again.
        lag_ms: Milliseconds since the previous pass (0 on the first).
        unmanaged: Venue holdings skipped as not managed by Joseph.
    """

    outbox_items: int
    fills_applied: int
    mismatches: int
    cleared: int
    lag_ms: int
    unmanaged: int = 0


class Reconciler:
    """Runs reconciliation passes for simulated and connected accounts."""

    def __init__(  # noqa: PLR0917 - six injected collaborators; bootstrap passes them positionally.
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
            resolver: Resolves the adapter of an account and mode.
            oms: Order management service that applies venue truth.
            audit_log: Audit log for mismatch records.
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
        self._noted: set[tuple[str, str]] = set()

    async def run_once(self) -> ReconcileReport:
        """Runs one reconciliation pass.

        Every account with a position (flat included) or a non-terminal
        order in the newest 2,000 orders is reconciled once per order mode.
        An account whose adapter raises ``ExecutionError`` is logged and
        skipped for this pass.

        Returns:
            Counts of the pass.
        """
        now = self._clock.now_ms()
        lag = 0 if self._last_run_ms is None else now - self._last_run_ms
        self._last_run_ms = now
        processed = await self._oms.dispatch_pending()
        fills_applied = mismatches = cleared = unmanaged = 0
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
            if account is None:
                continue
            adapter = self._resolver.resolve(account, mode)
            try:
                # since_ms=0 asks for every fill the venue still lists; the
                # OMS ignores fills it already recorded.
                fills = await adapter.list_recent_fills(account_id, 0)
                fills_applied += await self._oms.apply_venue_fills(
                    account_id, fills
                )
                venue_positions = await adapter.list_positions(account_id)
            except errors.ExecutionError as err:
                _LOG.warning("reconcile of %s failed: %s", account_id, err.code)
                continue
            managed = [p for p in venue_positions if p.managed]
            unmanaged += self._note_unmanaged(account_id, venue_positions)
            found, fixed = self._compare(account_id, mode, managed)
            mismatches += found
            cleared += fixed
        self._metrics.gauge("reconcile_lag_ms", float(lag))
        if mismatches:
            self._metrics.increment("reconcile_mismatch_total")
        return ReconcileReport(
            processed, fills_applied, mismatches, cleared, lag, unmanaged
        )

    def _note_unmanaged(
        self, account_id: str, venue_positions: Sequence[trading.VenuePosition]
    ) -> int:
        """Logs each unmanaged holding once per process with its note.

        Args:
            account_id: Account the positions belong to.
            venue_positions: Positions reported by the venue.

        Returns:
            Number of unmanaged holdings in ``venue_positions``.
        """
        count = 0
        for position in venue_positions:
            if position.managed:
                continue
            count += 1
            key = (account_id, position.instrument_id)
            if key not in self._noted:
                self._noted.add(key)
                _LOG.info(
                    "account %s: holding %s skipped: %s",
                    account_id,
                    position.instrument_id,
                    position.unmanaged_note,
                )
        return count

    def _compare(
        self,
        account_id: str,
        mode: enums.OrderMode,
        venue_positions: Sequence[trading.VenuePosition],
    ) -> tuple[int, int]:
        """Compares local and venue positions and flags differences.

        A local position whose signed quantity differs from the venue is
        marked unknown and audited (``position.mismatch``); a venue holding
        with no local position is saved as a new unknown position and
        audited (``position.unexpected``). An unknown position that matches
        again is cleared.

        Args:
            account_id: Account to compare.
            mode: Order mode of the local positions to compare.
            venue_positions: Managed positions reported by the venue.

        Returns:
            ``(mismatches, cleared)``: positions newly flagged unknown, and
            flagged positions cleared.
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
