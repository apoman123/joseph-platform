# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Durable market scans and closed-bar Python alerts.

Durable market scans and closed-bar Python alerts with bounded execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import sqlalchemy as sa

from joseph.alerts.app import ports
from joseph.alerts.domain import model as deliveries
from joseph.domain import errors
from joseph.domain import identity
from joseph.market_data.app import universe
from joseph.scripts import models
from joseph.scripts import service
from joseph.signals.app import service as signal_service
from joseph.signals.domain import signal as signals

METADATA = sa.MetaData()
"""SQL metadata for persisted Python jobs."""
JOBS = sa.Table(
    "python_script_jobs",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("job_id", sa.String(64), primary_key=True),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("body_json", sa.Text, nullable=False),
)
("Tenant-scoped job bodies; compare-and-swap prevents stale background writes.")


class ScriptJobs:
    """Owns scan tasks and restart-safe alert evaluations.

    Attributes:
        scripts: Immutable script authoring and isolated execution service.
        universe: Real instrument metadata and bounded quote cache.
        engine: Application-owned SQL engine.
        signals: Canonical alert history store.
        subscriptions: Existing user-controlled delivery destinations.
        outbox: Idempotent durable notification queue.
    """

    def __init__(
        self,
        scripts: service.ScriptService,
        source: universe.UniverseService,
        engine: sa.Engine,
        *,
        history: signal_service.SignalService,
        subscriptions: ports.SubscriptionRepository,
        outbox: ports.OutboxRepository,
    ) -> None:
        """Connects existing services and marks abandoned scans interrupted.

        Args:
            scripts: Owned script versions and container runner.
            source: Actual configured market sources.
            engine: Application-owned SQL database.
            history: Canonical tenant-scoped signal history.
            subscriptions: Existing notification destination store.
            outbox: Idempotent durable delivery queue.
        """
        self.scripts = scripts
        self.universe = source
        self.engine = engine
        self.signals = history
        self.subscriptions = subscriptions
        self.outbox = outbox
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        METADATA.create_all(engine)
        with engine.begin() as connection:
            for row in connection.execute(
                sa.select(JOBS).where(JOBS.c.kind == "scan")
            ).mappings():
                job = models.ScanJob.model_validate_json(row["body_json"])
                if job.status in {"pending", "running"}:
                    job.status = "failed"
                    job.error = (
                        "server restarted before this scan "
                        "completed; submit a new scan"
                    )
                    connection.execute(
                        JOBS.update()
                        .where(
                            JOBS.c.tenant_id == row["tenant_id"],
                            JOBS.c.job_id == job.job_id,
                        )
                        .values(body_json=job.model_dump_json())
                    )

    def _condition(self, tenant: str, job_id: str) -> Any:
        """Builds a scoped SQL identity predicate.

        Args:
            tenant: Authenticated owner identifier.
            job_id: Opaque job identifier.

        Returns:
            SQL conjunction that cannot select another tenant's job.
        """
        return sa.and_(JOBS.c.tenant_id == tenant, JOBS.c.job_id == job_id)

    def get(
        self, principal: identity.Principal, job_id: str, kind: str
    ) -> models.ScanJob | models.AlertJob:
        """Reads an owned scan or alert job with an explicit category.

        Args:
            principal: Authenticated owner.
            job_id: Opaque job identifier.
            kind: scan or alert category.

        Returns:
            Validated persisted job state.

        Raises:
            errors.NotFoundError: If the owned job/category does not exist.
        """
        with self.engine.connect() as connection:
            value = connection.execute(
                sa.select(JOBS.c.body_json).where(
                    self._condition(principal.tenant_id, job_id),
                    JOBS.c.kind == kind,
                )
            ).scalar_one_or_none()
        if value is None:
            raise errors.NotFoundError("owned script job not found")
        model = models.ScanJob if kind == "scan" else models.AlertJob
        return model.model_validate_json(value)

    def list_alerts(
        self, principal: identity.Principal
    ) -> list[models.AlertJob]:
        """Lists persistent Python alert configurations for one owner.

        Args:
            principal: Authenticated tenant owner.

        Returns:
            Owned jobs, including disabled jobs and visible evaluation errors.
        """
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.select(JOBS.c.body_json).where(
                    JOBS.c.tenant_id == principal.tenant_id,
                    JOBS.c.kind == "alert",
                )
            )
            return [models.AlertJob.model_validate_json(row[0]) for row in rows]

    def _insert(
        self,
        principal: identity.Principal,
        job: models.ScanJob | models.AlertJob,
        kind: str,
    ) -> None:
        """Persists a newly allocated owned job.

        Args:
            principal: Authenticated tenant owner.
            job: Validated new job state.
            kind: scan or alert category.

        Returns:
            None after atomically inserting the job.
        """
        with self.engine.begin() as connection:
            connection.execute(
                JOBS.insert().values(
                    tenant_id=principal.tenant_id,
                    job_id=job.job_id,
                    kind=kind,
                    body_json=job.model_dump_json(),
                )
            )

    def _replace(
        self,
        principal: identity.Principal,
        job: models.ScanJob | models.AlertJob,
        previous: models.ScanJob | models.AlertJob,
    ) -> bool:
        """Updates state only if no foreground edit changed it while work ran.

        Args:
            principal: Authenticated tenant owner.
            job: Proposed complete replacement state.
            previous: Exact state read before asynchronous work began.

        Returns:
            True after replacement, False if cancelled or edited concurrently.
        """
        with self.engine.begin() as connection:
            result = connection.execute(
                JOBS.update()
                .where(
                    self._condition(principal.tenant_id, job.job_id),
                    JOBS.c.body_json == previous.model_dump_json(),
                )
                .values(body_json=job.model_dump_json())
            )
            return result.rowcount == 1

    def submit_scan(
        self,
        principal: identity.Principal,
        script_id: str,
        request: models.ScanInput,
    ) -> models.ScanJob:
        """Pins a filter version and starts an asynchronous real-market scan.

        Args:
            principal: Authenticated author.
            script_id: Owned filter script slug.
            request: Market selection, explicit symbol bound and optional
                history.

        Returns:
            Pending persisted job; polling exposes progress and source failures.

        Raises:
            errors.JosephError: For missing scripts, wrong role or disabled
                runtime.
        """
        service.require_write(principal)
        script = self.scripts.repository.get(
            principal.tenant_id, script_id, request.version
        )
        if script.kind != "filter" or not self.scripts.runner.enabled:
            raise errors.UnsupportedError(
                "scanning requires a Filter script and an"
                " enabled Docker runtime"
            )
        if len(self._tasks) >= 4:
            raise errors.ConflictError(
                "four scans are already running; wait or cancel one"
            )
        job = models.ScanJob(
            job_id=uuid.uuid4().hex,
            script_id=script_id,
            version=script.version,
            source_hash=script.source_hash,
            status="pending",
            created_at_ms=self.scripts.market_data.now_ms(),
        )
        self._insert(principal, job, "scan")
        task = asyncio.create_task(self._scan(principal, job, request))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        return job

    def cancel_scan(
        self, principal: identity.Principal, job_id: str
    ) -> models.ScanJob:
        """Cancels an owned scan while preserving completed matches and errors.

        Args:
            principal: Authenticated author.
            job_id: Owned scan identifier.

        Returns:
            Cancelled or already terminal job state.

        Raises:
            errors.JosephError: For insufficient authoring access or absent job.
        """
        service.require_write(principal)
        job = self.get(principal, job_id, "scan")
        if not isinstance(job, models.ScanJob):
            raise errors.NotFoundError("scan job not found")
        previous = job.model_copy(deep=True)
        if job.status in {"pending", "running"}:
            job.status = "cancelled"
            self._replace(principal, job, previous)
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
        return job

    async def _scan(  # noqa: PLR0912, PLR0915, GPY013 - One bounded scan persists progress and converts source errors into job state.
        self,
        principal: identity.Principal,
        job: models.ScanJob,
        request: models.ScanInput,
    ) -> None:
        """Evaluates market symbols in bounded batches.

        Args:
            principal: Authenticated job owner.
            job: Pending persisted immutable-version scan.
            request: User-selected real market universe and input overrides.

        Returns:
            None after persisting completion, cancellation or an actionable
            error.
        """
        previous = job.model_copy(deep=True)
        try:
            rows = await self.universe.select(
                venue=request.venue,
                market_type=request.market_type,
                query=request.query,
            )
            job.universe_count = len(rows)
            rows = rows[: request.max_symbols]
            job.total = len(rows)
            job.status = "running"
            if not self._replace(principal, job, previous):
                return
            script = self.scripts.repository.get(
                principal.tenant_id, job.script_id, job.version
            )
            source = self.scripts.bundle(principal.tenant_id, script)
            for offset in range(0, len(rows), 25):
                previous = job.model_copy(deep=True)
                group = rows[offset : offset + 25]
                quotes, failures = await self.universe.quotes(group)
                symbols = []
                for row in group:
                    key = str(row.instrument_id)
                    if key not in quotes:
                        job.errors.append(
                            {
                                "instrument_id": key,
                                "error": failures.get(key, "quote unavailable"),
                            }
                        )
                        continue
                    bars = []
                    try:
                        if request.history_bars:
                            batch = await self.scripts.market_data.get_batch(
                                key, request.tf, limit=request.history_bars
                            )
                            bars = [
                                {
                                    "time_ms": bar.start_ms,
                                    "end_ms": bar.end_ms,
                                    "open": float(bar.open),
                                    "high": float(bar.high),
                                    "low": float(bar.low),
                                    "close": float(bar.close),
                                    "volume": float(bar.base_volume),
                                }
                                for bar in batch.candles
                                if bar.is_final
                            ]
                            if not bars:
                                raise errors.MissingDataError(
                                    "no closed history for filter"
                                )
                        symbols.append(
                            {
                                "instrument_id": key,
                                "instrument": row.to_contract(),
                                "snapshot": json.loads(
                                    json.dumps(
                                        quotes[key].to_contract(
                                            self.scripts.market_data.now_ms(),
                                            60000,
                                        )
                                    )
                                ),
                                "bars": bars,
                            }
                        )
                    except errors.JosephError as exc:
                        job.errors.append(
                            {"instrument_id": key, "error": exc.message}
                        )
                if symbols:
                    output = await self.scripts.runner.run(
                        {
                            **source,
                            "symbols": symbols,
                            "inputs": {**script.defaults, **request.inputs},
                            "timeframe": request.tf.value,
                        }
                    )
                    expected = {row["instrument_id"] for row in symbols}
                    for decision in output.get("results", []):
                        key = decision.get("instrument_id")
                        if key not in expected or not isinstance(
                            decision.get("matched"), bool
                        ):
                            raise errors.ValidationFailedError(
                                "filter output does not match the "
                                "requested universe"
                            )
                        expected.remove(key)
                        if decision.get("error"):
                            job.errors.append(
                                {
                                    "instrument_id": key,
                                    "error": str(decision["error"])[:400],
                                }
                            )
                        elif decision["matched"]:
                            job.matches.append(
                                {
                                    "instrument_id": key,
                                    "snapshot": json.loads(
                                        json.dumps(
                                            quotes[key].to_contract(
                                                self.scripts.market_data.now_ms(),
                                                60000,
                                            )
                                        )
                                    ),
                                }
                            )
                    if expected:
                        raise errors.ValidationFailedError(
                            "filter output omitted requested symbols"
                        )
                job.scanned += len(group)
                if not self._replace(principal, job, previous):
                    return
            previous = job.model_copy(deep=True)
            job.status = "complete"
            self._replace(principal, job, previous)
        except asyncio.CancelledError:
            raise
        except (errors.JosephError, ValueError, KeyError, TypeError) as exc:
            persisted = self.get(principal, job.job_id, "scan")
            if not isinstance(persisted, models.ScanJob):
                return
            if persisted.status not in {"cancelled", "complete"}:
                job.status = "failed"
                job.error = str(exc)[:400]
                self._replace(principal, job, persisted)

    def save_alert(
        self,
        principal: identity.Principal,
        request: models.AlertJobInput,
        job_id: str | None = None,
    ) -> models.AlertJob:
        """Creates or replaces an explicit pinned future-bar alert subscription.

        Args:
            principal: Authenticated author.
            request: Pinned source, symbol, conditions and selected
                destinations.
            job_id: Optional owned job to replace; omitted allocates a new job.

        Returns:
            Persisted alert state; changing a configuration resets its
            checkpoint.

        Raises:
            errors.JosephError: For missing source/destinations, access or
                runtime.
        """
        service.require_write(principal)
        script = self.scripts.repository.get(
            principal.tenant_id, request.script_id, request.version
        )
        if script.kind not in {"alert", "indicator", "strategy"}:
            raise errors.ValidationFailedError(
                "this script role cannot publish alert conditions"
            )
        if request.enabled and not self.scripts.runner.enabled:
            raise errors.UnsupportedError(
                "enable the Docker script runtime before enabling alerts"
            )
        owned = {
            sub.subscription_id
            for sub in self.subscriptions.list_for_tenant(principal.tenant_id)
            if sub.channel
            not in {deliveries.Channel.EXECUTION, deliveries.Channel.UI}
        }
        if not set(request.subscription_ids) <= owned:
            raise errors.ValidationFailedError(
                "select only owned external notification subscriptions"
            )
        now = self.scripts.market_data.now_ms()
        previous = self.get(principal, job_id, "alert") if job_id else None
        job = models.AlertJob(
            **request.model_dump(),
            job_id=job_id or uuid.uuid4().hex,
            created_at_ms=previous.created_at_ms if previous else now,
            last_processed_ms=now,
        )
        if previous is None:
            self._insert(principal, job, "alert")
        elif not self._replace(principal, job, previous):
            raise errors.ConflictError(
                "alert was edited concurrently; reload before saving"
            )
        return job

    def delete_alert(self, principal: identity.Principal, job_id: str) -> None:
        """Removes an owned condition job without deleting its alert history.

        Args:
            principal: Authenticated author.
            job_id: Owned persistent alert identifier.

        Returns:
            None after deleting the configuration.

        Raises:
            errors.JosephError: For missing jobs or insufficient authoring
                access.
        """
        service.require_write(principal)
        self.get(principal, job_id, "alert")
        with self.engine.begin() as connection:
            connection.execute(
                JOBS.delete().where(
                    self._condition(principal.tenant_id, job_id)
                )
            )

    async def evaluate_alert(
        self, principal: identity.Principal, job: models.AlertJob
    ) -> None:
        """Records future-bar Python conditions.

        Records future-bar Python conditions and queues idempotent deliveries.

        Args:
            principal: Internal identity scoped to the persisted job's tenant.
            job: Enabled pinned alert configuration read before this evaluation.

        Returns:
            None after updating diagnostics and the successfully queued
            checkpoint.
            Preview runs never call this method. No execution delivery is
            allowed.
        """
        previous = job.model_copy(deep=True)
        job.last_checked_ms = self.scripts.market_data.now_ms()
        try:
            result = await self.scripts.run(
                principal,
                job.script_id,
                models.ScriptRun(
                    version=job.version,
                    instrument_id=job.instrument_id,
                    tf=job.tf,
                    limit=job.history_bars,
                    inputs=job.inputs,
                ),
            )
            current = self.get(principal, job.job_id, "alert")
            if current.model_dump_json() != previous.model_dump_json():
                return
            closes = {
                int(start): end for start, end in result.bar_closes.items()
            }
            selected = [
                sub
                for sub in self.subscriptions.list_for_tenant(
                    principal.tenant_id
                )
                if sub.subscription_id in job.subscription_ids
            ]
            for event in result.alerts:
                close_ms = closes.get(event.time_ms)
                if close_ms is None or close_ms <= job.last_processed_ms:
                    continue
                key = (
                    f"python|{principal.tenant_id}|{job.job_id}|"
                    f"{job.version}|{event.name}|{event.time_ms}"
                )
                signal = signals.Signal(
                    signal_id=signals.signal_id_for(key),
                    dedupe_key=key,
                    tenant_id=principal.tenant_id,
                    strategy_id=job.script_id,
                    strategy_version_id=f"{job.script_id}:v{job.version}",
                    setup_id=None,
                    instrument_id=job.instrument_id,
                    direction="NONE",
                    timeframe=job.tf.value,
                    kind="CUSTOM_ALERT",
                    setup_state="WAITING",
                    severity=event.severity,
                    bar_start_ms=event.time_ms,
                    bar_close_ms=close_ms,
                    evaluated_at_ms=job.last_checked_ms,
                    inputs_snapshot_hash=result.source_hash,
                    rule_version=result.source_hash,
                    evaluator_version="python-1",
                    indicator_versions={},
                    reason_tree={
                        "node_id": event.name,
                        "node_path": f"/python/{event.name}",
                        "op": "PYTHON",
                        "result": "TRUE",
                        "detail": event.message,
                        "evidence": {"provider": result.data_source},
                        "children": [],
                    },
                    aoi_ids=(),
                    group_ids=(),
                    entry_price=None,
                    stop_price=None,
                    target_price=None,
                    rr=None,
                    risk_status="NOT_APPLICABLE",
                    risk_reasons=(),
                    mode="ALERT_ONLY",
                    data_source=result.feed_state,
                    message=event.message,
                )
                job.emitted += int(self.signals.record(signal))
                routed = deliveries.route(
                    signal,
                    selected,
                    frozenset(
                        channel.value
                        for channel in deliveries.Channel
                        if channel is not deliveries.Channel.EXECUTION
                    ),
                    job.last_checked_ms,
                    ui_target=f"tenant:{principal.tenant_id}",
                    forward_execution=False,
                )
                for record in routed.deliveries:
                    self.outbox.enqueue(record)
            job.last_processed_ms = max(
                [job.last_processed_ms, *closes.values()]
            )
            job.last_error = None
        except errors.JosephError as exc:
            job.last_error = exc.message[:400]
        self._replace(principal, job, previous)

    async def run_once(self) -> None:
        """Evaluates enabled alert jobs once, preserving tenant isolation.

        Returns:
            None after each enabled owned job has been evaluated sequentially.
        """
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    sa.select(JOBS.c.tenant_id, JOBS.c.body_json).where(
                        JOBS.c.kind == "alert"
                    )
                )
            )
        for tenant, body in rows:
            job = models.AlertJob.model_validate_json(str(body))
            if job.enabled:
                await self.evaluate_alert(
                    identity.Principal(str(tenant), "python-alert-worker"), job
                )

    async def _loop(self) -> None:
        """Polls future closed bars until shutdown interrupts the interval.

        Returns:
            None when the stop event is set.
        """
        while not self._stopping.is_set():
            await self.run_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), 10)

    def start(self) -> None:
        """Starts the alert scheduler when the isolated runtime is enabled.

        Returns:
            None after allocating one idempotent scheduler task.
        """
        if self.scripts.runner.enabled and self._loop_task is None:
            self._stopping.clear()
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stops script producers before the outbox, adapters.

        Stops script producers before the outbox, adapters and database close.

        Returns:
            None after the scheduler and active scan tasks finish cancellation.
        """
        self._stopping.set()
        tasks = list(self._tasks.values())
        if self._loop_task is not None:
            tasks.append(self._loop_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None
