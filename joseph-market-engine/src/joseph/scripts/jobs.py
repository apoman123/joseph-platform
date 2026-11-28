# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Durable market scans and closed-bar Python alerts.

Durable market scans and closed-bar Python alerts with bounded execution.
Scans and alerts run built-in and plugin scripts in process and tenant
source in the isolated runtime. A scan composes up to eleven filters with
AND; missing data is reported as unavailable, never as a match.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import decimal
import json
import uuid
from typing import Any

import sqlalchemy as sa

from joseph.alerts.app import ports
from joseph.alerts.domain import model as deliveries
from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.app import universe
from joseph.scripts import catalog
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

_BATCH = 25
"""Instruments loaded and evaluated together."""
_BOOK_DEPTH = 100
"""Order-book levels per side loaded for the order_book metric."""
_DERIVATIVES = frozenset(
    {instruments.MarketType.PERP, instruments.MarketType.FUTURE}
)
"""Market types with open interest and funding."""


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
        self._fetches = asyncio.Semaphore(8)
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

    def _resolve_filters(
        self,
        principal: identity.Principal,
        script_id: str | None,
        request: models.ScanInput,
    ) -> list[service.ResolvedScript]:
        """Resolves and checks every filter of a scan request.

        Args:
            principal: Authenticated author.
            script_id: Path filter, or None for a conditions-only scan.
            request: Scan request with optional further conditions.

        Returns:
            Resolved filters in evaluation order, with effective inputs.

        Raises:
            errors.ValidationFailedError: Without filters, for a non-filter
                script, or for inputs outside a catalog filter's declaration.
            errors.UnsupportedError: For tenant filters while the isolated
                runtime is disabled.
        """
        conditions = list(request.conditions)
        if script_id is not None:
            conditions.insert(
                0,
                models.ScanCondition(
                    script_id=script_id,
                    version=request.version,
                    inputs=request.inputs,
                ),
            )
        elif request.version is not None or request.inputs:
            raise errors.ValidationFailedError(
                "version and inputs belong to a path filter; put them in "
                "conditions"
            )
        if not conditions:
            raise errors.ValidationFailedError("add at least one filter")
        resolved = []
        for condition in conditions:
            script = self.scripts.resolve(
                principal.tenant_id, condition.script_id, condition.version
            )
            if script.kind != "filter":
                raise errors.ValidationFailedError(
                    f"{condition.script_id} is not a Filter script"
                )
            if not self.scripts.runnable(script):
                raise errors.UnsupportedError(
                    "scanning a tenant Filter script requires the enabled "
                    "Docker runtime; built-in filters run without it"
                )
            check_inputs(script, condition.inputs)
            resolved.append(
                dataclasses.replace(
                    script, defaults={**script.defaults, **condition.inputs}
                )
            )
        return resolved

    def submit_scan(
        self,
        principal: identity.Principal,
        script_id: str | None,
        request: models.ScanInput,
    ) -> models.ScanJob:
        """Pins every filter and starts an asynchronous real-market scan.

        Args:
            principal: Authenticated author.
            script_id: Path filter (tenant slug or catalog id), or None
                when the request lists its filters as conditions.
            request: Market selection, explicit symbol bound, optional
                history and further AND conditions.

        Returns:
            Pending persisted job; polling exposes progress and source failures.

        Raises:
            errors.ConflictError: If four scans are already running.
        """
        service.require_write(principal)
        filters = self._resolve_filters(principal, script_id, request)
        if len(self._tasks) >= 4:
            raise errors.ConflictError(
                "four scans are already running; wait or cancel one"
            )
        pins = [
            models.ScanConditionPin(
                script_id=script.script_id,
                version=script.version,
                source_hash=script.source_hash,
                title=script.name,
                inputs=script.defaults,
            )
            for script in filters
        ]
        job = models.ScanJob(
            job_id=uuid.uuid4().hex,
            script_id=pins[0].script_id,
            version=pins[0].version,
            source_hash=pins[0].source_hash,
            status="pending",
            created_at_ms=self.scripts.market_data.now_ms(),
            conditions=pins,
        )
        self._insert(principal, job, "scan")
        task = asyncio.create_task(self._scan(principal, job, request, filters))
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
            errors.NotFoundError: If the owned scan does not exist.
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

    async def _universe(
        self, request: models.ScanInput
    ) -> tuple[list[instruments.Instrument], int]:
        """Selects the instruments to scan, in scan order.

        Args:
            request: Market selection, order and bound.

        Returns:
            At most ``max_symbols`` instruments and the eligible count.
        """
        rows = await self.universe.select(
            venue=request.venue,
            market_type=request.market_type,
            query=request.query,
        )
        if request.order_by == "turnover":
            quotes, _ = await self.universe.quotes(rows, budget_s=30)

            def turnover(row: instruments.Instrument) -> decimal.Decimal:
                """Reads a row's 24h turnover for ordering.

                Args:
                    row: Instrument.

                Returns:
                    Turnover, or -1 without a quote so it sorts last.
                """
                quote = quotes.get(str(row.instrument_id))
                value = None if quote is None else quote.turnover_24h_quote
                return decimal.Decimal(-1) if value is None else value

            rows = sorted(rows, key=turnover, reverse=True)
        return rows[: request.max_symbols], len(rows)

    async def _candidate(
        self,
        row: instruments.Instrument,
        quote: market.MarketSnapshot,
        request: models.ScanInput,
        metrics: frozenset[str],
    ) -> dict[str, Any]:
        """Collects one instrument's snapshot, metrics and closed history.

        Args:
            row: Instrument metadata.
            quote: Its latest snapshot.
            request: History depth and resolution.
            metrics: Extra data to load (FilterMetric names).

        Returns:
            Worker symbol record with instrument_id, instrument, snapshot and
            bars.

        Raises:
            errors.MissingDataError: If requested history has no closed bar.
        """
        market_data = self.scripts.market_data
        key = str(row.instrument_id)
        async with self._fetches:
            now = market_data.now_ms()
            snapshot = quote.to_contract(now, 60000)
            snapshot["evaluated_at_ms"] = now
            derivative = row.instrument_id.market_type in _DERIVATIVES
            if "order_book" in metrics:
                try:
                    book = await market_data.get_order_book(key, _BOOK_DEPTH)
                    snapshot["order_book"] = book.to_contract()
                except errors.JosephError:
                    snapshot["order_book"] = None
            adapter = market_data.adapter(row.instrument_id.venue)
            if (
                "open_interest" in metrics
                and derivative
                and quote.open_interest is None
            ):
                with contextlib.suppress(errors.JosephError):
                    interest = await adapter.fetch_open_interest(
                        row.instrument_id
                    )
                    snapshot["open_interest"] = interest.to_contract()
            if "funding" in metrics and derivative and quote.funding is None:
                with contextlib.suppress(errors.JosephError):
                    funding = await adapter.fetch_funding(row.instrument_id)
                    snapshot["funding"] = funding.to_contract()
            bars: list[dict[str, Any]] = []
            if request.history_bars:
                batch = await market_data.get_batch(
                    key, request.tf, limit=request.history_bars
                )
                bars = [
                    service.bar_record(bar)
                    for bar in batch.candles
                    if bar.is_final
                ]
                if not bars:
                    raise errors.MissingDataError(
                        "no closed history for this filter"
                    )
        return {
            "instrument_id": key,
            "instrument": row.to_contract(),
            "snapshot": json.loads(json.dumps(snapshot)),
            "bars": bars,
        }

    async def _decide(
        self,
        principal: identity.Principal,
        filters: list[service.ResolvedScript],
        symbols: list[dict[str, Any]],
        timeframe: str,
    ) -> dict[str, tuple[str, str]]:
        """Evaluates every filter on a batch, combining them with AND.

        A symbol is excluded as soon as one filter returns False; a filter
        that reports missing data leaves it unavailable unless a later
        filter excludes it; a filter failure is an error.

        Args:
            principal: Job owner.
            filters: Resolved filters with effective inputs.
            symbols: Worker symbol records.
            timeframe: Scan resolution.

        Returns:
            Per instrument: ("match", ""), ("excluded", ""),
            ("unavailable", reason) or ("error", message).

        Raises:
            errors.ValidationFailedError: If a filter answers for symbols it
                was not asked about or omits one.
        """
        outcome: dict[str, tuple[str, str]] = {
            row["instrument_id"]: ("match", "") for row in symbols
        }
        for script in filters:
            pending = [
                row
                for row in symbols
                if outcome[row["instrument_id"]][0] in {"match", "unavailable"}
            ]
            if not pending:
                break
            output = await self.scripts.execute(
                principal.tenant_id,
                script,
                {
                    "symbols": pending,
                    "inputs": script.defaults,
                    "timeframe": timeframe,
                },
                cache=False,
            )
            expected = {row["instrument_id"] for row in pending}
            for decision in output.get("results", []):
                key = decision.get("instrument_id")
                if key not in expected or not isinstance(
                    decision.get("matched"), bool
                ):
                    raise errors.ValidationFailedError(
                        "filter output does not match the requested universe"
                    )
                expected.remove(key)
                message = str(decision.get("error") or "")[:400]
                if decision.get("unavailable"):
                    previous = outcome[key][1]
                    reason = f"{script.name}: {message}"
                    outcome[key] = (
                        "unavailable",
                        f"{previous}; {reason}" if previous else reason,
                    )
                elif message:
                    outcome[key] = ("error", f"{script.name}: {message}")
                elif not decision["matched"]:
                    outcome[key] = ("excluded", "")
            if expected:
                raise errors.ValidationFailedError(
                    "filter output omitted requested symbols"
                )
        return outcome

    async def _scan(
        self,
        principal: identity.Principal,
        job: models.ScanJob,
        request: models.ScanInput,
        filters: list[service.ResolvedScript],
    ) -> None:
        """Evaluates market symbols in bounded batches.

        Args:
            principal: Authenticated job owner.
            job: Pending persisted scan with pinned filters.
            request: User-selected real market universe and bounds.
            filters: Resolved filters with effective inputs.
        """
        previous = job.model_copy(deep=True)
        metrics = frozenset(request.metrics).union(
            *(script.entry.metrics for script in filters if script.entry)
        )
        try:
            rows, job.universe_count = await self._universe(request)
            job.total = len(rows)
            job.status = "running"
            if not self._replace(principal, job, previous):
                return
            for offset in range(0, len(rows), _BATCH):
                previous = job.model_copy(deep=True)
                group = rows[offset : offset + _BATCH]
                await self._scan_batch(
                    principal, job, request, filters, group, metrics
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
                job.error = (
                    exc.message
                    if isinstance(exc, errors.JosephError)
                    else str(exc)
                )[:400]
                self._replace(principal, job, persisted)

    async def _scan_batch(  # noqa: PLR0917 - One batch step shares the job's whole evaluation context.
        self,
        principal: identity.Principal,
        job: models.ScanJob,
        request: models.ScanInput,
        filters: list[service.ResolvedScript],
        group: list[instruments.Instrument],
        metrics: frozenset[str],
    ) -> None:
        """Loads one batch's data, evaluates it and records the outcomes.

        Args:
            principal: Job owner.
            job: Scan state updated in place.
            request: Scan request.
            filters: Resolved filters.
            group: Batch instruments.
            metrics: Extra data to load.
        """
        quotes, failures = await self.universe.quotes(group)
        loaded = await asyncio.gather(
            *(
                self._candidate(
                    row, quotes[str(row.instrument_id)], request, metrics
                )
                for row in group
                if str(row.instrument_id) in quotes
            ),
            return_exceptions=True,
        )
        symbols = []
        quoted = [row for row in group if str(row.instrument_id) in quotes]
        for row in group:
            key = str(row.instrument_id)
            if key not in quotes:
                job.unavailable.append(
                    {
                        "instrument_id": key,
                        "error": failures.get(key, "quote unavailable"),
                    }
                )
        for row, item in zip(quoted, loaded, strict=True):
            if isinstance(item, errors.JosephError):
                job.unavailable.append(
                    {
                        "instrument_id": str(row.instrument_id),
                        "error": item.message,
                    }
                )
            elif isinstance(item, BaseException):
                raise item
            else:
                symbols.append(item)
        if not symbols:
            return
        outcome = await self._decide(
            principal, filters, symbols, request.tf.value
        )
        for record in symbols:
            key = record["instrument_id"]
            state, message = outcome[key]
            if state == "match":
                job.matches.append(
                    {
                        "instrument_id": key,
                        "snapshot": _summary(record["snapshot"]),
                    }
                )
            elif state == "unavailable":
                job.unavailable.append({"instrument_id": key, "error": message})
            elif state == "error":
                job.errors.append({"instrument_id": key, "error": message})

    def save_alert(
        self,
        principal: identity.Principal,
        request: models.AlertJobInput,
        job_id: str | None = None,
    ) -> models.AlertJob:
        """Creates or replaces an explicit pinned future-bar alert subscription.

        Args:
            principal: Authenticated author.
            request: Pinned source (tenant version, or version 0 for a
                built-in or plugin script), symbol, inputs and selected
                destinations.
            job_id: Optional owned job to replace; omitted allocates a new job.

        Returns:
            Persisted alert state; changing a configuration resets its
            checkpoint.

        Raises:
            errors.ValidationFailedError: For a wrong version pin or role,
                undeclared inputs or destinations the tenant does not own.
            errors.UnsupportedError: When enabling tenant source while the
                isolated runtime is disabled.
            errors.ConflictError: If the alert was edited concurrently.
        """
        service.require_write(principal)
        catalog_script = catalog.is_catalog_id(request.script_id)
        if catalog_script != (request.version == 0):
            raise errors.ValidationFailedError(
                "pin version 0 for built-in and plugin scripts and a "
                "positive version for your own scripts"
            )
        script = self.scripts.resolve(
            principal.tenant_id, request.script_id, request.version or None
        )
        if script.kind not in {"alert", "indicator", "strategy"}:
            raise errors.ValidationFailedError(
                "this script role cannot publish alert conditions"
            )
        check_inputs(script, request.inputs)
        if request.enabled and not self.scripts.runnable(script):
            raise errors.UnsupportedError(
                "enable the Docker script runtime before enabling alerts on "
                "your own scripts; built-in alerts run without it"
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
            source_hash=script.source_hash,
            title=script.name,
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
        """
        service.require_write(principal)
        self.get(principal, job_id, "alert")
        with self.engine.begin() as connection:
            connection.execute(
                JOBS.delete().where(
                    self._condition(principal.tenant_id, job_id)
                )
            )

    def _signal(
        self,
        principal: identity.Principal,
        job: models.AlertJob,
        event: models.ScriptAlertEvent,
        close_ms: int,
        result: models.ScriptResult,
        *,
        key: str,
    ) -> signals.Signal:
        """Builds the ALERT_ONLY custom-alert signal of one event.

        Args:
            principal: Job owner.
            job: Alert job.
            event: Matched condition event.
            close_ms: Close of the event's bar.
            result: Script run that produced the event.
            key: Idempotency key.

        Returns:
            The signal.
        """
        return signals.Signal(
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
            evaluated_at_ms=job.last_checked_ms or close_ms,
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

    async def evaluate_alert(
        self, principal: identity.Principal, job: models.AlertJob
    ) -> None:
        """Records future-bar Python conditions.

        Records future-bar Python conditions and queues idempotent deliveries.
        Built-in and plugin conditions run in process; tenant source runs in
        the isolated runtime. A catalog script whose installed source no
        longer matches the pinned hash is reported, not evaluated.

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
        catalog_script = catalog.is_catalog_id(job.script_id)
        try:
            script = self.scripts.resolve(
                principal.tenant_id, job.script_id, job.version or None
            )
            if job.source_hash not in {None, script.source_hash}:
                job.last_error = (
                    "the installed script changed since this alert was "
                    "saved; save the alert again to accept the new version"
                )
                self._replace(principal, job, previous)
                return
            result = await self.scripts.run(
                principal,
                job.script_id,
                models.ScriptRun(
                    version=job.version or None,
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
            pin = f"0:{result.source_hash}" if catalog_script else job.version
            for event in result.alerts:
                close_ms = closes.get(event.time_ms)
                if close_ms is None or close_ms <= job.last_processed_ms:
                    continue
                key = (
                    f"python|{principal.tenant_id}|{job.job_id}|"
                    f"{pin}|{event.name}|{event.time_ms}"
                )
                signal = self._signal(
                    principal, job, event, close_ms, result, key=key
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
        """Starts the alert scheduler.

        Built-in and plugin alerts need no isolated runtime, so the
        scheduler always runs; jobs on tenant source report the disabled
        runtime as their error.

        Returns:
            None after allocating one idempotent scheduler task.
        """
        if self._loop_task is None:
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


def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drops bulky loaded metrics from a persisted match snapshot.

    Args:
        snapshot: Snapshot record evaluated by the filters.

    Returns:
        The snapshot contract without the order book.
    """
    return {
        key: value for key, value in snapshot.items() if key != "order_book"
    }


def check_inputs(
    script: service.ResolvedScript, inputs: dict[str, Any]
) -> None:
    """Checks overrides against a catalog script's declared inputs.

    Tenant scripts read their inputs with ``ctx.input`` bounds at run time,
    so only catalog declarations are checked here.

    Args:
        script: Resolved script.
        inputs: Input overrides.

    Raises:
        errors.ValidationFailedError: For an undeclared input, a value of
            another JSON type, a number outside its bounds or a value outside
            its options.
    """
    if script.entry is None:
        return
    declared = script.entry.inputs
    for name, value in inputs.items():
        spec = declared.get(name)
        if spec is None:
            raise errors.ValidationFailedError(
                f"{script.script_id} has no input {name}"
            )
        default = spec["default"]
        numeric = isinstance(value, (int, float)) and not isinstance(
            value, bool
        )
        if isinstance(default, bool) != isinstance(value, bool) or (
            isinstance(default, (int, float))
            and not isinstance(default, bool)
            and not numeric
        ):
            raise errors.ValidationFailedError(
                f"{name} must be a {type(default).__name__}"
            )
        if numeric and (
            (spec["minimum"] is not None and value < spec["minimum"])
            or (spec["maximum"] is not None and value > spec["maximum"])
        ):
            raise errors.ValidationFailedError(
                f"{name} must be between {spec['minimum']} and "
                f"{spec['maximum']}"
            )
        if spec["options"] is not None and value not in spec["options"]:
            raise errors.ValidationFailedError(
                f"{name} must be one of {', '.join(map(str, spec['options']))}"
            )
