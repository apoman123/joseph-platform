# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authoring, library pin resolution.

Authoring, library pin resolution and closed-candle Python script execution.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
from collections.abc import Mapping
from typing import Any

import pydantic

from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import http
from joseph.market_data.app import service as market_service
from joseph.scripts import accounting
from joseph.scripts import catalog as catalog_lib
from joseph.scripts import models
from joseph.scripts import repository
from joseph.scripts import runner
from joseph.scripts import worker

_RESULT_CACHE_SIZE = 512
"""Cached catalog-script results keyed by exact inputs and bars."""


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedScript:
    """A runnable script: a tenant version or a trusted catalog class.

    Attributes:
        script_id: Tenant slug or catalog id.
        version: Tenant version, or 0 for catalog scripts.
        kind: Script role.
        name: Display title.
        overlay: Whether plots share the price pane.
        defaults: Default input values.
        requests: Declared additional series.
        source_hash: Exact source/configuration identity.
        entry: Catalog entry for built-in and plugin scripts, else None.
        tenant: Tenant version for tenant source, else None.
        periods: Calendar periods whose per-bar bounds the host computes.
    """

    script_id: str
    version: int
    kind: str
    name: str
    overlay: bool
    defaults: dict[str, Any]
    requests: dict[str, models.SeriesRequest]
    source_hash: str
    entry: catalog_lib.CatalogEntry | None
    tenant: models.ScriptVersion | None
    periods: tuple[str, ...] = ()


def auto_intrabar_tf(
    chart: timeframes.Timeframe,
) -> timeframes.Timeframe | None:
    """Picks the lower timeframe of ``Request(tf="auto", intrabars=True)``.

    Args:
        chart: Chart resolution.

    Returns:
        1s on seconds charts, 1m intraday, 5m on daily and 1h on weekly and
        monthly charts; None on 1s and 1m charts, which have no practical
        lower timeframe.
    """
    if chart in {timeframes.Timeframe.S1, timeframes.Timeframe.M1}:
        return None
    if chart.is_seconds:
        return timeframes.Timeframe.S1
    if chart.duration_ms < timeframes.Timeframe.D1.duration_ms:
        return timeframes.Timeframe.M1
    if chart is timeframes.Timeframe.D1:
        return timeframes.Timeframe.M5
    return timeframes.Timeframe.H1


def auto_period_tf(chart: timeframes.Timeframe) -> timeframes.Timeframe | None:
    """Picks the period of an ordinary ``auto`` request.

    Args:
        chart: Chart resolution.

    Returns:
        The day below daily charts, the week below weekly and the month
        below monthly charts; None on monthly charts.
    """
    for period in (
        timeframes.Timeframe.D1,
        timeframes.Timeframe.W1,
        timeframes.Timeframe.MN1,
    ):
        if chart.duration_ms < period.duration_ms:
            return period
    return None


def request_tf(
    declared: models.SeriesRequest,
    chart: timeframes.Timeframe,
    inputs: Mapping[str, Any],
) -> timeframes.Timeframe | None:
    """Resolves the resolution a declared request reads for one run.

    Args:
        declared: Declared request.
        chart: Chart resolution.
        inputs: Effective run inputs; ``tf_input`` names one of them.

    Returns:
        The resolution, or None when an intrabar request has no lower
        timeframe below the chart.

    Raises:
        errors.ValidationFailedError: If the selected input is not a
            resolution.
    """
    chosen: object = declared.tf
    if declared.tf_input:
        value = inputs.get(declared.tf_input, "auto")
        chosen = "auto" if value in ("", None) else value
    if chosen == "auto":
        if declared.intrabars:
            return auto_intrabar_tf(chart)
        return auto_period_tf(chart)
    try:
        resolved = timeframes.parse(str(chosen))
    except ValueError:
        raise errors.ValidationFailedError(
            f"{declared.tf_input or 'tf'} is not a supported resolution"
        ) from None
    if declared.intrabars and resolved.duration_ms >= chart.duration_ms:
        return None
    return resolved


def period_bounds(
    instrument_id: str,
    bars: list[dict[str, Any]],
    periods: tuple[str, ...],
) -> dict[str, list[list[int]]]:
    """Computes the calendar period around every bar for ``ctx.period``.

    Args:
        instrument_id: Canonical instrument whose session calendar applies.
        bars: Source bars in order.
        periods: Declared periods ("1d", "1w", "1M").

    Returns:
        Per period, one [start, next start] pair per bar in UTC
        milliseconds.

    Raises:
        errors.ValidationFailedError: If a bar lies outside the calendar.
    """
    calendar = sessions.for_instrument(
        instruments.InstrumentId.parse(instrument_id)
    )
    table: dict[str, list[list[int]]] = {}
    for name in periods:
        tf = timeframes.parse(name)
        following: dict[int, int] = {}
        rows: list[list[int]] = []
        try:
            for bar in bars:
                start = calendar.floor(tf, int(bar["time_ms"]))
                if start not in following:
                    following[start] = calendar.shift(tf, start, 1)
                rows.append([start, following[start]])
        except ValueError as exc:
            raise errors.ValidationFailedError(str(exc)) from None
        table[name] = rows
    return table


def bar_record(bar: market.Candle) -> dict[str, Any]:
    """Converts a candle to the JSON bar shape scripts read.

    Args:
        bar: Source candle.

    Returns:
        time_ms/end_ms/is_final/OHLCV plus optional provider fields, which
        stay None when the source did not publish them.
    """
    return {
        "time_ms": bar.start_ms,
        "end_ms": bar.end_ms,
        "is_final": bar.is_final,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.base_volume),
        "quote_turnover": None
        if bar.quote_turnover is None
        else float(bar.quote_turnover),
        "trade_count": bar.trade_count,
        "taker_buy_volume": None
        if bar.taker_buy_base_volume is None
        else float(bar.taker_buy_base_volume),
        "taker_buy_method": None
        if bar.taker_buy_method is None
        else str(bar.taker_buy_method.value),
    }


def require_write(principal: identity.Principal) -> None:
    """Applies the same authoring permission policy.

    Applies the same authoring permission policy as existing strategy versions.

    Args:
        principal: Authenticated caller, optionally carrying explicit scopes.

    Returns:
        None if unscoped development identity or strategy:write is granted.

    Raises:
        errors.ForbiddenError: If explicit scopes do not permit authoring.
    """
    if principal.scopes and not principal.has_scope("strategy:write"):
        raise errors.ForbiddenError("strategy:write scope required")


class ScriptService:
    """Coordinates private script versions and an isolated simulation runtime.

    Attributes:
        repository: Tenant-scoped immutable source repository.
        runner: Network-isolated user-code executor.
        market_data: Authoritative backend candle service, including provenance.
        catalog: Built-in and installed plugin scripts, run in process.
    """

    def __init__(
        self,
        store: repository.ScriptRepository,
        runtime: runner.DockerRunner,
        market_data: market_service.MarketDataService,
        catalog: catalog_lib.ScriptCatalog | None = None,
    ) -> None:
        """Connects authoring, data and execution.

        Connects authoring, data and execution without giving scripts
        credentials.

        Args:
            store: Tenant-isolated immutable script source store.
            runtime: Explicitly configured isolated runtime.
            market_data: Backend data source used to fetch real closed bars.
            catalog: Built-in and plugin scripts; the built-ins only when
                omitted.
        """
        self.repository = store
        self.runner = runtime
        self.market_data = market_data
        self.catalog = catalog or catalog_lib.build(
            catalog_lib.CatalogConfig(packages=catalog_lib.NO_PACKAGES)
        )
        self._slots = asyncio.Semaphore(4)
        self._results: collections.OrderedDict[str, dict[str, Any]] = (
            collections.OrderedDict()
        )

    def resolve(
        self, tenant_id: str, script_id: str, version: int | None = None
    ) -> ResolvedScript:
        """Finds a runnable tenant version or catalog script.

        Args:
            tenant_id: Owner of tenant scripts.
            script_id: Tenant slug or catalog id.
            version: Tenant version, latest when None; ignored for catalog
                scripts.

        Returns:
            The resolved script.

        Raises:
            errors.NotFoundError: If no such script or version exists.
            errors.UnsupportedError: If a catalog script is disabled.
        """
        if catalog_lib.is_catalog_id(script_id):
            entry = self.catalog.get(script_id)
            return ResolvedScript(
                script_id=entry.script_id,
                version=0,
                kind=entry.kind,
                name=entry.title,
                overlay=entry.overlay,
                defaults=entry.defaults,
                requests={
                    alias: models.SeriesRequest.model_validate(
                        spec, strict=False
                    )
                    for alias, spec in entry.requests.items()
                },
                source_hash=entry.source_hash,
                entry=entry,
                tenant=None,
                periods=entry.periods,
            )
        script = self.repository.get(tenant_id, script_id, version)
        return ResolvedScript(
            script_id=script.script_id,
            version=script.version,
            kind=script.kind,
            name=script.name,
            overlay=script.overlay,
            defaults=dict(script.defaults),
            requests=dict(script.requests),
            source_hash=script.source_hash,
            entry=None,
            tenant=script,
        )

    def runnable(self, script: ResolvedScript) -> bool:
        """Tells whether a script can execute in this deployment.

        Args:
            script: Resolved script.

        Returns:
            True for catalog scripts, and for tenant source when the isolated
            runtime is enabled.
        """
        return script.entry is not None or self.runner.enabled

    async def execute(
        self,
        tenant_id: str,
        script: ResolvedScript,
        data: Mapping[str, Any],
        *,
        cache: bool = True,
    ) -> dict[str, Any]:
        """Executes a resolved script on prepared bars and inputs.

        Catalog classes run in process on a worker thread (they are trusted
        platform or operator-installed code); tenant source runs only in the
        isolated runner. Catalog results are cached by exact input, so polls
        over unchanged bars cost nothing.

        Args:
            tenant_id: Owner of tenant library pins.
            script: Resolved script.
            data: Bundle fields besides the source: bars, inputs, kind,
                instrument and simulation settings (or ``symbols`` for scans).
            cache: Whether a catalog result may be served from and stored in
                the result cache; scans of changing quotes pass False.

        Returns:
            The raw worker result mapping.

        Raises:
            errors.NotFoundError: If tenant source is missing.
            errors.UnsupportedError: If tenant source must run while the
                isolated runtime is disabled.
            errors.ValidationFailedError: If the script fails.
        """
        if script.entry is None:
            if script.tenant is None:
                raise errors.NotFoundError("script source is unavailable")
            if not self.runner.enabled:
                raise errors.UnsupportedError(
                    "enable JOSEPH_SCRIPT_RUNTIME=docker and build the "
                    "runner image; see README"
                )
            bundle = self.bundle(tenant_id, script.tenant)
            bundle.update(data)
            return await self.runner.run(bundle)
        payload = {**data, "kind": script.kind}
        if not cache:
            return await self._run_class(script, payload)
        try:
            key = script.source_hash + json.dumps(
                payload, sort_keys=True, allow_nan=False, default=str
            )
        except ValueError:
            raise errors.ValidationFailedError(
                "script input is not finite JSON"
            ) from None
        cached = self._results.get(key)
        if cached is not None:
            self._results.move_to_end(key)
            return cached
        result = await self._run_class(script, payload)
        self._results[key] = result
        while len(self._results) > _RESULT_CACHE_SIZE:
            self._results.popitem(last=False)
        return result

    async def _run_class(
        self, script: ResolvedScript, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Runs a catalog class on a worker thread within the slot bound.

        Args:
            script: Resolved catalog script.
            payload: Bundle with the script kind.

        Returns:
            The raw worker result mapping.

        Raises:
            errors.ValidationFailedError: If the script fails.
        """
        if script.entry is None:
            raise errors.ValidationFailedError("script is not in the catalog")
        async with self._slots:
            try:
                return await asyncio.to_thread(
                    worker.run_class, script.entry.cls, payload
                )
            except Exception as exc:  # noqa: BLE001 - Script failures become bounded validation errors.
                raise errors.ValidationFailedError(
                    f"{script.script_id} failed: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                ) from None

    async def _request_bars(
        self,
        declared: models.SeriesRequest,
        request: models.ScriptRun,
        bars: list[dict[str, Any]],
        inputs: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Fetches the closed bars of one declared request.

        Intrabar requests cover only the newest chart bars that need them:
        from ``window_from_ms`` when given, skipping bars that already
        carry the ``unless`` field, at most ``limit`` lower bars.

        Args:
            declared: Declared request.
            request: Run request.
            bars: Chart bars of the run.
            inputs: Effective run inputs.

        Returns:
            Closed bars in order; empty when nothing is needed.
        """
        tf = request_tf(declared, request.tf, inputs)
        if tf is None:
            return []
        from_ms = None
        skip = bool(declared.unless) and (
            not declared.unless_input
            or inputs.get(declared.unless_input) is True
        )
        if declared.intrabars:
            needed = [
                bar
                for bar in bars
                if (
                    request.window_from_ms is None
                    or bar["end_ms"] > request.window_from_ms
                )
                and (not skip or bar.get(declared.unless) is None)
            ]
            if not needed:
                return []
            from_ms = int(needed[0]["time_ms"])
        with http.background():
            extra = await self.market_data.get_batch(
                declared.instrument_id or request.instrument_id,
                tf,
                from_ms=from_ms,
                to_ms=int(bars[-1]["end_ms"]),
                limit=declared.limit,
            )
        return [bar_record(bar) for bar in extra.candles if bar.is_final]

    def bundle(
        self,
        tenant_id: str,
        script: models.ScriptInput,
        *,
        seen: frozenset[tuple[str, int]] = frozenset(),
        budget: list[int] | None = None,
    ) -> dict[str, Any]:
        """Resolves a pinned library graph.

        Resolves a pinned library graph without executing source on the server.

        Args:
            tenant_id: Authenticated owner of every referenced library.
            script: Script source with alias-to-immutable-version dependencies.
            seen: Ancestor identities for depth/cycle checks, initially empty.
            budget: Optional shared graph node/byte counters, initially empty.

        Returns:
            Private recursively resolved source bundle for the runner only.

        Raises:
            errors.ValidationFailedError: For cycles, excessive depth or imports
                of an indicator/strategy instead of a library.
            errors.NotFoundError: If an owned dependency version is absent.
        """
        if budget is None:
            budget = [0, 0]
        budget[0] += 1
        budget[1] += len(script.source.encode())
        if budget[0] > 64 or budget[1] > 1_000_000:
            raise errors.ValidationFailedError(
                "resolved library graph exceeds its source budget"
            )
        if len(seen) >= 16:
            raise errors.ValidationFailedError(
                "library import depth exceeds sixteen"
            )
        libraries = {}
        for alias, reference in script.libraries.items():
            key = (reference.script_id, reference.version)
            if key in seen or reference.script_id == script.script_id:
                raise errors.ValidationFailedError(
                    "circular library dependency"
                )
            library = self.repository.get(
                tenant_id, reference.script_id, reference.version
            )
            if library.kind != "library":
                raise errors.ValidationFailedError(
                    "only library scripts can be imported"
                )
            libraries[alias] = self.bundle(
                tenant_id, library, seen=seen | {key}, budget=budget
            )
        data = script.model_dump(exclude={"expected_version"})
        data["libraries"] = libraries
        return data

    def validate(
        self, principal: identity.Principal, script: models.ScriptInput
    ) -> dict[str, object]:
        """Checks Python structure and owned pinned dependencies.

        Checks Python structure and owned pinned dependencies without execution.

        Args:
            principal: Authenticated tenant owner.
            script: Source and declaration to validate.

        Returns:
            Validity, declared kind and explicit non-execution status.

        Raises:
            errors.ValidationFailedError: If source or its dependency graph
                fails.
            errors.NotFoundError: If a pinned tenant-owned library is absent.
        """
        models.validate_source(script)
        self.bundle(principal.tenant_id, script)
        return {"valid": True, "kind": script.kind, "executed": False}

    def save(
        self,
        principal: identity.Principal,
        script: models.ScriptInput,
        *,
        create: bool,
    ) -> models.ScriptVersion:
        """Validates and atomically saves an immutable version.

        Validates and atomically saves an immutable version for an authorized
        user.

        Args:
            principal: Authenticated caller with authoring permission.
            script: Source, kind, dependency pins and optimistic edit version.
            create: True for a new script, False for an existing version update.

        Returns:
            Newly saved version with content provenance.

        Raises:
            errors.ForbiddenError: If authoring permission is absent.
            errors.ValidationFailedError: If source/dependencies/kind are
                invalid.
            errors.NotFoundError: If the script or a pinned library is absent.
            errors.ConflictError: If an edit is stale or the slug already
                exists.
        """
        require_write(principal)
        self.validate(principal, script)
        return self.repository.save(principal.tenant_id, script, create=create)

    async def fetch(
        self, request: models.ScriptRun
    ) -> market_service.CandleBatchResult:
        """Fetches the chart candles of a run request.

        Args:
            request: Instrument, resolution and range.

        Returns:
            The candle batch. Venue requests it causes yield the rate limit
            to waiting chart batches.
        """
        with http.background():
            return await self.market_data.get_batch(
                request.instrument_id,
                request.tf,
                from_ms=request.from_ms,
                to_ms=request.to_ms,
                limit=request.limit,
            )

    async def run_batch(
        self, principal: identity.Principal, request: models.ScriptBatchRun
    ) -> models.ScriptBatchResult:
        """Runs several studies over one fetch of the same chart candles.

        Args:
            principal: Authenticated tenant owner.
            request: Shared chart range plus one entry per study.

        Returns:
            One result or error per entry, in request order; one study's
            failure never fails the others.

        Raises:
            errors.JosephError: If the shared candles cannot be fetched.
        """
        source = await self.fetch(request)
        entries = []
        for item in request.runs:
            shared = {
                name: getattr(request, name)
                for name in models.ScriptRun.model_fields
            }
            single = models.ScriptRun(
                **shared
                | {
                    "version": item.version,
                    "inputs": {**request.inputs, **item.inputs},
                }
            )
            try:
                result = await self.run(
                    principal, item.script_id, single, source=source
                )
            except errors.JosephError as exc:
                entries.append(
                    models.ScriptBatchEntry(
                        script_id=item.script_id, error=str(exc)[:500]
                    )
                )
            else:
                entries.append(
                    models.ScriptBatchEntry(
                        script_id=item.script_id, result=result
                    )
                )
        return models.ScriptBatchResult(results=entries)

    async def run(
        self,
        principal: identity.Principal,
        script_id: str,
        request: models.ScriptRun,
        *,
        source: market_service.CandleBatchResult | None = None,
    ) -> models.ScriptResult:
        """Runs an indicator or simulates a strategy.

        Runs an indicator or simulates a strategy using only closed source bars.

        Args:
            principal: Authenticated tenant owner.
            script_id: Owned script slug.
            request: Version, instrument, resolution, inputs and simulation
                costs.
            source: Candles already fetched for this request (batched
                runs); fetched here when None.

        Returns:
            Validated plot series, provenance and optional simulated
            performance.

        Raises:
            errors.NotFoundError: If an owned version or instrument is absent.
            errors.UnsupportedError: If a library is run standalone or runtime
                off.
            errors.MissingDataError: If no closed source candles are available.
            errors.ValidationFailedError: On invalid data, user-code failure or
                resource limits; malformed worker output is never trusted.
        """
        script = self.resolve(principal.tenant_id, script_id, request.version)
        if script.kind == "library":
            raise errors.UnsupportedError(
                "libraries export reusable code; run an importing "
                "indicator or strategy"
            )
        if not self.runnable(script):
            raise errors.UnsupportedError(
                "enable JOSEPH_SCRIPT_RUNTIME=docker and build the "
                "runner image; see README"
            )
        if request.include_open and script.kind != "indicator":
            raise errors.ValidationFailedError(
                "provisional bars are supported only for indicators"
            )
        batch = source or await self.fetch(request)
        bars = [
            bar_record(bar)
            for bar in batch.candles
            if (bar.is_final or request.include_open)
            and (request.to_ms is None or bar.end_ms <= request.to_ms)
        ]
        if not bars:
            raise errors.MissingDataError(
                "no closed candles are available for this script run"
            )
        instrument = await self.market_data.registry.get(
            instruments.InstrumentId.parse(request.instrument_id)
        )
        data: dict[str, Any] = {
            "kind": script.kind,
            "instrument": instrument.to_contract(),
        }
        if script.kind == "filter":
            snapshot = await self.market_data.get_snapshot(
                instrument.instrument_id
            )
            data["snapshot"] = snapshot.to_contract(
                self.market_data.now_ms(), 60000
            )
        inputs = {**script.defaults, **request.inputs}
        additional = {}
        for alias, declared in script.requests.items():
            additional[alias] = await self._request_bars(
                declared, request, bars, inputs
            )
        if script.periods:
            data["periods"] = period_bounds(
                request.instrument_id, bars, script.periods
            )
        data.update(
            {
                "bars": bars,
                "instrument_id": request.instrument_id,
                "timeframe": request.tf.value,
                "extra_series": additional,
                "inputs": inputs,
                "initial_equity": request.initial_equity,
                "commission_bps": request.commission_bps,
                "slippage_bps": request.slippage_bps,
                "overlay": script.overlay,
            }
        )
        result = await self.execute(principal.tenant_id, script, data)
        if script.kind != "strategy" and (
            result.get("fills") or result.get("orders")
        ):
            raise errors.ValidationFailedError(
                "only strategies can publish simulated fills"
            )
        starts = {bar["time_ms"] for bar in bars}
        if any(
            event.get("time_ms") not in starts
            for event in result.get("alerts", [])
        ) or any(
            fill.get("time_ms") not in starts
            for fill in (*result.get("fills", []), *result.get("orders", []))
        ):
            raise errors.ValidationFailedError(
                "script events must reference actual evaluated source bars"
            )
        metadata: Mapping[str, object] = {
            "script_id": script.script_id,
            "version": script.version,
            "kind": script.kind,
            "source_hash": script.source_hash,
            "instrument_id": request.instrument_id,
            "tf": request.tf.value,
            "bars_count": len(bars),
            "bar_closes": {
                str(int(bar["time_ms"])): int(bar["end_ms"])
                for bar in bars
                if bar["is_final"]
            },
            "feed_state": batch.feed_state.value,
            "data_source": batch.source,
            "complete": batch.complete,
            "overlay": script.overlay,
        }
        try:
            output = models.ScriptResult.model_validate({**result, **metadata})
            return accounting.reconcile(output, bars, request)
        except pydantic.ValidationError:
            raise errors.ValidationFailedError(
                "script output does not match the plot/simulation contract"
            ) from None
