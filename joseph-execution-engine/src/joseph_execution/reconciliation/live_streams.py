# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Private venue streams for live accounts, applied through the OMS.

The supervisor keeps one ``stream_account_events`` task per live account
that has working orders or an open position (a subset of the accounts the
reconciler watches) and applies order and fill events with
``OmsService.apply_venue_events`` of the order management service (OMS),
which is idempotent: duplicates are ignored by fill id, and the REST
snapshot each adapter replays after a reconnect fills any gap. Position
and balance events are only counted; the reconciler compares positions
against venue truth on its own schedule.

A stream that ends or fails is restarted after a delay; an account that
leaves scope (everything flat and finished) has its stream cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Collection

from joseph_execution.domain import accounts
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.oms import service as oms_service
from joseph_execution.ports import repositories
from joseph_execution.ports import services
from joseph_execution.ports import trading

_LOG = logging.getLogger(__name__)


class LiveStreamSupervisor:
    """Supervisor that runs and restarts private streams of live accounts.

    Attributes:
        applied: Order and fill events the OMS reported as changing state,
            summed over the supervisor's lifetime.
    """

    def __init__(
        self,
        uow_factory: repositories.UnitOfWorkFactory,
        resolver: trading.AdapterResolver,
        oms: oms_service.OmsService,
        metrics: services.Metrics,
        *,
        poll_s: float = 10.0,
        restart_s: float = 15.0,
        venues: Collection[str] | None = None,
    ) -> None:
        """Initializes the supervisor.

        Args:
            uow_factory: Unit of work factory.
            resolver: Resolves the LIVE adapter of an account.
            oms: Order management service that applies the events.
            metrics: Metrics sink.
            poll_s: Seconds between scope refreshes.
            restart_s: Seconds before restarting a stream that ended or
                failed.
            venues: Venues whose accounts get a stream (``None`` = all).
                Brokers have their own event pump and are left out.
        """
        self._uow = uow_factory
        self._resolver = resolver
        self._oms = oms
        self._metrics = metrics
        self._poll_s = poll_s
        self._restart_s = restart_s
        self._venues = None if venues is None else frozenset(venues)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.applied = 0

    def scope(self) -> list[accounts.Account]:
        """Lists live accounts with working orders or open positions.

        Open LIVE positions count, and non-terminal LIVE orders among the
        newest 2,000 orders.

        Returns:
            The accounts, sorted by id; paper and disconnected accounts and
            accounts of venues outside ``venues`` are left out.
        """
        with self._uow() as uow:
            ids: set[str] = {
                p.account_id
                for p in uow.positions.list()
                if p.mode is enums.OrderMode.LIVE and p.signed_qty != 0
            }
            ids.update(
                o.account_id
                for o in uow.orders.list(limit=2000)
                if o.mode is enums.OrderMode.LIVE and not o.state.is_terminal
            )
            found = [uow.accounts.get(i) for i in sorted(ids)]
        return [
            a
            for a in found
            if a is not None
            and not a.is_paper
            and a.status is not enums.AccountStatus.DISCONNECTED
            and (self._venues is None or a.venue in self._venues)
        ]

    def running(self) -> list[str]:
        """Lists accounts whose stream task is still running.

        Returns:
            Account ids, sorted.
        """
        return sorted(a for a, t in self._tasks.items() if not t.done())

    async def sync(self) -> None:
        """Starts streams for accounts in scope and stops the others.

        A task that already finished is replaced by a new one.
        """
        wanted = {a.account_id: a for a in self.scope()}
        for account_id in list(self._tasks):
            task = self._tasks[account_id]
            if account_id not in wanted or task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                del self._tasks[account_id]
        for account_id, account in wanted.items():
            if account_id not in self._tasks:
                self._tasks[account_id] = asyncio.create_task(
                    self._pump(account), name=f"live-stream-{account_id}"
                )

    async def run_forever(self) -> None:
        """Refreshes the scope until cancelled."""
        try:
            while True:
                try:
                    await self.sync()
                except Exception:  # noqa: BLE001 - isolation point: logged with traceback; supervision retries on the next poll.
                    _LOG.exception("live stream supervision failed")
                await asyncio.sleep(self._poll_s)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Cancels every stream task."""
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def _pump(self, account: accounts.Account) -> None:
        """Consumes one account's stream until cancelled.

        The stream is restarted ``restart_s`` seconds after it ends or
        fails; failures are logged and counted, never raised.

        Args:
            account: Live account to stream.
        """
        labels = {"venue": account.venue}
        while True:
            adapter = self._resolver.resolve(account, enums.OrderMode.LIVE)
            try:
                async for event in adapter.stream_account_events(
                    account.account_id
                ):
                    await self._apply(account.account_id, event, labels)
            except errors.ExecutionError as err:
                _LOG.warning(
                    "live stream of %s stopped: %s",
                    account.account_id,
                    err.code,
                )
                self._metrics.increment("live_stream_failures_total", labels)
            except Exception:  # noqa: BLE001 - isolation point: logged with traceback; the stream restarts after restart_s.
                _LOG.exception("live stream of %s crashed", account.account_id)
                self._metrics.increment("live_stream_failures_total", labels)
            await asyncio.sleep(self._restart_s)

    async def _apply(
        self,
        account_id: str,
        event: trading.VenueEvent,
        labels: dict[str, str],
    ) -> None:
        """Counts one event and applies it when it carries an order or fill.

        Args:
            account_id: Account the event belongs to.
            event: Venue event.
            labels: Metric labels of the account's venue.
        """
        self._metrics.increment("live_stream_events_total", labels)
        if event.order is None and event.fill is None:
            return
        self.applied += await self._oms.apply_venue_events(account_id, [event])
