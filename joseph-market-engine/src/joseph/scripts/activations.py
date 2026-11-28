# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Live activations of Python Strategy scripts.

An activation pins one strategy (a built-in or plugin catalog class, or an
immutable tenant version) with its inputs to instruments, a timeframe, a
mode and notification channels. A background loop evaluates every active
activation on closed bars through ``ScriptService`` and turns orders the
strategy queued on the newest closed bar into signals:

* an entry (``ctx.entry``) that opens or reverses the simulated position is
  an ``ENTRY_CONFIRMED`` signal with direction, planned entry (the bar
  close), the strategy's stop and target and their reward/risk;
* a close (``ctx.close``), or the closing half of a reversal, is a
  ``POSITION_EXITED`` signal.

Signals are recorded once per deterministic dedupe key (tenant, activation,
source hash, instrument, bar time, kind and direction) and routed through
the alert routing: UI and selected subscriptions per the activation's
channels, and entries of PAPER and SHADOW activations to the execution
engine, whose deployments bind the activation's ``strategy_version_id``.
ALERT_ONLY entries are never forwarded and AUTO_LIVE cannot be activated
here at all. Each instrument keeps a bar-close checkpoint that starts at
activation (or resume) time, so history is never replayed, also not after a
restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import logging
import uuid
from collections.abc import Sequence
from typing import Annotated
from typing import Any
from typing import Literal
from typing import TypeAlias

import pydantic
import sqlalchemy as sa

from joseph.alerts.app import ports as alert_ports
from joseph.alerts.domain import model as deliveries
from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.scripts import models
from joseph.scripts import service as script_service
from joseph.signals.app import service as signal_service
from joseph.signals.domain import signal as signals

_LOG = logging.getLogger(__name__)

ActivationMode: TypeAlias = Literal[
    "ALERT_ONLY", "PAPER", "SHADOW", "AUTO_LIVE"
]
"""Requested modes; AUTO_LIVE is accepted only to be refused explicitly."""

NOTIFY_CHANNELS = frozenset(
    channel.value
    for channel in deliveries.Channel
    if channel is not deliveries.Channel.EXECUTION
)
"""Channels an activation may notify; execution forwarding follows mode."""

_CHANGED = (
    "the strategy's installed code changed since activation; create a new "
    "activation to adopt it"
)
"""Error of an activation whose pinned catalog code is no longer installed."""

METADATA = sa.MetaData()
"""SQL metadata of persisted activations."""

ACTIVATIONS = sa.Table(
    "strategy_activations",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("activation_id", sa.String(64), primary_key=True),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("body_json", sa.Text, nullable=False),
)
"""Tenant-scoped activation bodies; compare-and-swap guards updates."""


class StrategyActivationInput(pydantic.BaseModel):
    """A request to evaluate a Python strategy live on closed bars.

    Attributes:
        script_id: Tenant strategy slug, ``builtin:<id>`` or
            ``plugin:<entry>:<id>``.
        version: Tenant version to pin; the latest when None. Ignored for
            catalog strategies, which pin their installed source hash.
        instrument_ids: Canonical instruments, one through twenty.
        tf: Signal timeframe.
        inputs: Input overrides on top of the declared defaults.
        mode: ALERT_ONLY, PAPER or SHADOW; AUTO_LIVE is refused with
            TRADE_MODE_DISABLED.
        channels: Notification channels (UI and external subscriptions).
        history_bars: Closed bars evaluated per pass, the strategy's warmup.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    script_id: models.ScriptRef
    version: Annotated[int, pydantic.Field(ge=1)] | None = None
    instrument_ids: Annotated[
        list[models.InstrumentIdStr],
        pydantic.Field(min_length=1, max_length=20),
    ]
    tf: timeframes.Timeframe = pydantic.Field(strict=False)
    inputs: models.JsonInputs = pydantic.Field(default_factory=dict)
    mode: ActivationMode = "ALERT_ONLY"
    channels: Annotated[list[str], pydantic.Field(max_length=8)] = (
        pydantic.Field(default_factory=lambda: ["UI"])
    )
    history_bars: Annotated[int, pydantic.Field(ge=50, le=2000)] = 500


class ActivationInstrumentStatus(pydantic.BaseModel):
    """Evaluation progress of one activated instrument.

    Attributes:
        last_processed_ms: Newest bar close already turned into signals or
            skipped; bars at or before it are never signalled.
        last_error: Last evaluation error, None after a clean pass.
    """

    last_processed_ms: int
    last_error: str | None = None


class StrategyActivation(StrategyActivationInput):
    """A persisted activation and its evaluation state.

    Attributes:
        activation_id: Opaque identifier.
        version: Pinned tenant version, or 0 for catalog strategies.
        name: Strategy display title.
        source_hash: Pinned exact source identity.
        strategy_version_id: Identity execution deployments bind:
            ``<script_id>:v<version>`` for tenant versions and
            ``<catalog id>:v<first 12 hash digits>`` for catalog classes.
        state: ACTIVE or PAUSED.
        created_at_ms: Creation time.
        updated_at_ms: Last configuration or state change.
        last_checked_ms: Last evaluation pass, None before one.
        last_error: Activation-wide error (such as a changed built-in or a
            disabled runtime), None when evaluation works.
        emitted: Number of new signals recorded.
        instruments: Progress per instrument id.
    """

    version: int = 0
    activation_id: str
    name: str
    source_hash: str
    strategy_version_id: str
    state: Literal["ACTIVE", "PAUSED"]
    created_at_ms: int
    updated_at_ms: int
    last_checked_ms: int | None = None
    last_error: str | None = None
    emitted: int = 0
    instruments: dict[str, ActivationInstrumentStatus] = pydantic.Field(
        default_factory=dict
    )


class StrategyActivationList(pydantic.BaseModel):
    """Owned activations.

    Attributes:
        activations: Activations, newest first.
        runtime_enabled: Whether tenant source can execute (Docker runner).
        forwarding_enabled: Whether PAPER/SHADOW entries reach the execution
            engine in this deployment.
    """

    activations: list[StrategyActivation]
    runtime_enabled: bool
    forwarding_enabled: bool


def version_id(script: script_service.ResolvedScript) -> str:
    """Builds the strategy version identity execution deployments bind.

    Args:
        script: Resolved strategy.

    Returns:
        ``<script_id>:v<version>`` or ``<catalog id>:v<hash prefix>``.
    """
    if script.entry is None:
        return f"{script.script_id}:v{script.version}"
    return f"{script.script_id}:v{script.source_hash[:12]}"


def _decimal(value: float | None) -> decimal.Decimal | None:
    """Converts a float price to a decimal with its shortest repr.

    Args:
        value: Price or None.

    Returns:
        Decimal, or None.
    """
    return None if value is None else decimal.Decimal(repr(float(value)))


def build_signals(
    activation: StrategyActivation,
    tenant_id: str,
    instrument_id: str,
    result: models.ScriptResult,
    now_ms: int,
) -> list[signals.Signal]:
    """Turns orders queued on the newest closed bar into signals.

    Args:
        activation: Activation that ran.
        tenant_id: Owning tenant.
        instrument_id: Evaluated instrument.
        result: Strategy result over closed bars.
        now_ms: Evaluation time.

    Returns:
        Zero, one or (for a reversal) two signals: an exit before an entry.
    """
    closes = {int(start): end for start, end in result.bar_closes.items()}
    if not closes:
        return []
    last_start = max(closes)
    orders = [
        order
        for order in result.orders
        if order.time_ms == last_start and order.status == "pending"
    ]
    if not orders:
        return []
    order = orders[-1]
    position = result.position_size
    events: list[tuple[str, str]] = []
    if position and (
        order.action == "close" or (order.action == "long") != (position > 0)
    ):
        events.append(("POSITION_EXITED", "LONG" if position > 0 else "SHORT"))
    if order.action != "close" and (
        not position or (order.action == "long") != (position > 0)
    ):
        events.append(
            ("ENTRY_CONFIRMED", "LONG" if order.action == "long" else "SHORT")
        )
    close_ms = closes[last_start]
    found = []
    for kind, direction in events:
        key = "|".join(
            (
                "strategy",
                tenant_id,
                activation.activation_id,
                activation.source_hash,
                instrument_id,
                str(last_start),
                kind,
                direction,
            )
        )
        is_entry = kind == "ENTRY_CONFIRMED"
        entry = _decimal(order.reference) if is_entry else None
        stop = _decimal(order.stop) if is_entry else None
        target = _decimal(order.target) if is_entry else None
        rr = None
        if entry is not None and stop is not None and target is not None:
            rr = (abs(target - entry) / abs(entry - stop)).quantize(
                decimal.Decimal("0.01"), rounding=decimal.ROUND_FLOOR
            )
        comment = order.comment or (
            "strategy entry" if is_entry else "strategy exit"
        )
        message = (
            f"{activation.name} {direction} "
            f"{'entry' if is_entry else 'exit'} on {instrument_id} "
            f"{activation.tf.value}: {comment}"
        )
        found.append(
            signals.Signal(
                signal_id=signals.signal_id_for(key),
                dedupe_key=key,
                tenant_id=tenant_id,
                strategy_id=activation.script_id,
                strategy_version_id=activation.strategy_version_id,
                source_hash=activation.source_hash,
                instrument_id=instrument_id,
                direction=direction,
                timeframe=activation.tf.value,
                kind=kind,
                severity=signals.SEVERITY_BY_KIND[kind],
                mode=activation.mode,
                bar_start_ms=last_start,
                bar_close_ms=close_ms,
                evaluated_at_ms=now_ms,
                data_source=result.feed_state,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                rr=rr,
                message=message[:500],
            )
        )
    return found


class StrategyActivations:
    """Owns persisted activations and the closed-bar evaluation loop.

    Attributes:
        scripts: Script resolution and execution.
        engine: Application SQL engine.
        signals: Canonical signal history.
        subscriptions: Tenant notification destinations.
        outbox: Durable delivery queue.
        forward_execution: Whether PAPER/SHADOW entries are forwarded.
        interval_s: Seconds between evaluation passes.
    """

    def __init__(
        self,
        scripts: script_service.ScriptService,
        engine: sa.Engine,
        *,
        history: signal_service.SignalService,
        subscriptions: alert_ports.SubscriptionRepository,
        outbox: alert_ports.OutboxRepository,
        forward_execution: bool = False,
        interval_s: float = 10.0,
    ) -> None:
        """Connects services and creates the activation table if missing.

        Args:
            scripts: Script resolution and execution.
            engine: Application SQL engine.
            history: Canonical signal history.
            subscriptions: Tenant notification destinations.
            outbox: Durable delivery queue.
            forward_execution: Whether PAPER/SHADOW entries are forwarded.
            interval_s: Seconds between evaluation passes.
        """
        self.scripts = scripts
        self.engine = engine
        self.signals = history
        self.subscriptions = subscriptions
        self.outbox = outbox
        self.forward_execution = forward_execution
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        METADATA.create_all(engine)

    def _now(self) -> int:
        """Reads the market clock.

        Returns:
            UTC epoch milliseconds.
        """
        return self.scripts.market_data.now_ms()

    def _where(self, tenant_id: str, activation_id: str) -> Any:
        """Selects one owned activation row.

        Args:
            tenant_id: Owner.
            activation_id: Activation id.

        Returns:
            SQL predicate.
        """
        return sa.and_(
            ACTIVATIONS.c.tenant_id == tenant_id,
            ACTIVATIONS.c.activation_id == activation_id,
        )

    def get(
        self, principal: identity.Principal, activation_id: str
    ) -> StrategyActivation:
        """Reads one owned activation.

        Args:
            principal: Owner.
            activation_id: Activation id.

        Returns:
            The activation.

        Raises:
            errors.NotFoundError: If the owner has no such activation.
        """
        with self.engine.connect() as connection:
            body = connection.execute(
                sa.select(ACTIVATIONS.c.body_json).where(
                    self._where(principal.tenant_id, activation_id)
                )
            ).scalar_one_or_none()
        if body is None:
            raise errors.NotFoundError(
                f"strategy activation {activation_id} not found"
            )
        return StrategyActivation.model_validate_json(body)

    def list_owned(
        self, principal: identity.Principal
    ) -> StrategyActivationList:
        """Lists the owner's activations, newest first.

        Args:
            principal: Owner.

        Returns:
            Activations with runtime and forwarding availability.
        """
        with self.engine.connect() as connection:
            rows: list[str] = list(
                connection.execute(
                    sa.select(ACTIVATIONS.c.body_json).where(
                        ACTIVATIONS.c.tenant_id == principal.tenant_id
                    )
                ).scalars()
            )
        found = [StrategyActivation.model_validate_json(row) for row in rows]
        found.sort(key=lambda a: (-a.created_at_ms, a.activation_id))
        return StrategyActivationList(
            activations=found,
            runtime_enabled=self.scripts.runner.enabled,
            forwarding_enabled=self.forward_execution,
        )

    def _replace(
        self,
        tenant_id: str,
        activation: StrategyActivation,
        previous: StrategyActivation,
    ) -> bool:
        """Stores a new state only if nobody changed the row meanwhile.

        Args:
            tenant_id: Owner.
            activation: New state.
            previous: State read before the change.

        Returns:
            True when stored, False on a concurrent change or deletion.
        """
        with self.engine.begin() as connection:
            result = connection.execute(
                ACTIVATIONS.update()
                .where(
                    self._where(tenant_id, activation.activation_id),
                    ACTIVATIONS.c.body_json == previous.model_dump_json(),
                )
                .values(
                    state=activation.state,
                    body_json=activation.model_dump_json(),
                )
            )
            return result.rowcount == 1

    def _check(
        self, principal: identity.Principal, request: StrategyActivationInput
    ) -> script_service.ResolvedScript:
        """Validates an activation request and resolves its strategy.

        Args:
            principal: Author.
            request: Requested activation.

        Returns:
            The pinned strategy.

        Raises:
            errors.TradeModeDisabledError: For AUTO_LIVE.
            errors.ValidationFailedError: For non-strategy scripts, unknown
                channels, duplicate instruments or rate series.
            errors.UnsupportedError: If tenant source cannot run here.
        """
        script_service.require_write(principal)
        if request.mode == "AUTO_LIVE":
            raise errors.TradeModeDisabledError(
                "AUTO_LIVE cannot be activated from the market engine. "
                "Create an execution-engine deployment for one account with "
                "limits, then give explicit scoped consent; live trading is "
                "disabled by default."
            )
        unknown = set(request.channels) - NOTIFY_CHANNELS
        if unknown:
            raise errors.ValidationFailedError(
                f"unknown notification channels: {sorted(unknown)}"
            )
        if len(set(request.instrument_ids)) != len(request.instrument_ids):
            raise errors.ValidationFailedError("instruments must be distinct")
        for instrument_id in request.instrument_ids:
            parsed = instruments.InstrumentId.parse(instrument_id)
            if parsed.market_type is instruments.MarketType.RATE:
                raise errors.ValidationFailedError(
                    f"{instrument_id} is a published rate series, not a "
                    "tradable instrument; trade a bond ETF instead"
                )
        script = self.scripts.resolve(
            principal.tenant_id, request.script_id, request.version
        )
        if script.kind != "strategy":
            raise errors.ValidationFailedError(
                "only Strategy scripts can be activated"
            )
        if not self.scripts.runnable(script):
            raise errors.UnsupportedError(
                "tenant strategies need JOSEPH_SCRIPT_RUNTIME=docker and the "
                "runner image; see README"
            )
        return script

    def create(
        self, principal: identity.Principal, request: StrategyActivationInput
    ) -> StrategyActivation:
        """Activates a strategy from the next closed bar on.

        Args:
            principal: Author with strategy:write.
            request: Strategy, instruments, timeframe, inputs, mode and
                channels.

        Returns:
            The ACTIVE activation; bars closed before now never signal.
        """
        script = self._check(principal, request)
        now = self._now()
        activation = StrategyActivation(
            **request.model_dump(exclude={"version"}),
            version=script.version,
            activation_id="act-" + uuid.uuid4().hex[:20],
            name=script.name,
            source_hash=script.source_hash,
            strategy_version_id=version_id(script),
            state="ACTIVE",
            created_at_ms=now,
            updated_at_ms=now,
            instruments={
                key: ActivationInstrumentStatus(last_processed_ms=now)
                for key in request.instrument_ids
            },
        )
        with self.engine.begin() as connection:
            connection.execute(
                ACTIVATIONS.insert().values(
                    tenant_id=principal.tenant_id,
                    activation_id=activation.activation_id,
                    state=activation.state,
                    body_json=activation.model_dump_json(),
                )
            )
        return activation

    def set_state(
        self,
        principal: identity.Principal,
        activation_id: str,
        state: Literal["ACTIVE", "PAUSED"],
    ) -> StrategyActivation:
        """Pauses or resumes an activation.

        Resuming moves every checkpoint to now, so bars that closed while
        paused never signal.

        Args:
            principal: Author with strategy:write.
            activation_id: Activation id.
            state: New state.

        Returns:
            The updated activation.

        Raises:
            errors.ConflictError: If it changed concurrently.
        """
        script_service.require_write(principal)
        previous = self.get(principal, activation_id)
        if previous.state == state:
            return previous
        now = self._now()
        activation = previous.model_copy(deep=True)
        activation.state = state
        activation.updated_at_ms = now
        if state == "ACTIVE":
            script = self._check(
                principal,
                StrategyActivationInput.model_validate(
                    previous.model_dump(include=set(_INPUT_FIELDS))
                    | {"version": previous.version or None}
                ),
            )
            if script.source_hash != previous.source_hash:
                raise errors.ConflictError(_CHANGED)
            activation.last_error = None
            activation.instruments = {
                key: ActivationInstrumentStatus(last_processed_ms=now)
                for key in activation.instrument_ids
            }
        if not self._replace(principal.tenant_id, activation, previous):
            raise errors.ConflictError(
                "activation changed concurrently; reload and retry"
            )
        return activation

    def delete(self, principal: identity.Principal, activation_id: str) -> None:
        """Deletes an activation; its signals stay in the history.

        Args:
            principal: Author with strategy:write.
            activation_id: Activation id.
        """
        script_service.require_write(principal)
        self.get(principal, activation_id)
        with self.engine.begin() as connection:
            connection.execute(
                ACTIVATIONS.delete().where(
                    self._where(principal.tenant_id, activation_id)
                )
            )

    async def evaluate(
        self, tenant_id: str, activation: StrategyActivation
    ) -> list[signals.Signal]:
        """Evaluates one activation once and routes its new signals.

        Args:
            tenant_id: Owner.
            activation: ACTIVE activation as read before this pass.

        Returns:
            Newly recorded signals.
        """
        previous = activation.model_copy(deep=True)
        current = activation.model_copy(deep=True)
        now = self._now()
        current.last_checked_ms = now
        principal = identity.Principal(tenant_id, "strategy-activations")
        created: list[signals.Signal] = []
        try:
            script = self.scripts.resolve(
                tenant_id, current.script_id, current.version or None
            )
            current.last_error = None
            if script.source_hash != current.source_hash:
                current.last_error = _CHANGED
        except errors.JosephError as exc:
            current.last_error = exc.message[:400]
        if current.last_error is not None:
            self._replace(tenant_id, current, previous)
            return created
        for instrument_id in current.instrument_ids:
            status = current.instruments.setdefault(
                instrument_id, ActivationInstrumentStatus(last_processed_ms=now)
            )
            try:
                result = await self.scripts.run(
                    principal,
                    current.script_id,
                    models.ScriptRun(
                        version=current.version or None,
                        instrument_id=instrument_id,
                        tf=current.tf,
                        limit=current.history_bars,
                        inputs=current.inputs,
                    ),
                )
            except errors.JosephError as exc:
                status.last_error = exc.message[:400]
                continue
            status.last_error = None
            closes = [int(end) for end in result.bar_closes.values()]
            newest = max(closes, default=status.last_processed_ms)
            if newest <= status.last_processed_ms:
                continue
            fresh = now - newest <= max(2 * current.tf.duration_ms, 120_000)
            status.last_processed_ms = newest
            if not fresh:
                continue
            for signal in build_signals(
                current, tenant_id, instrument_id, result, now
            ):
                if self.signals.record(signal):
                    created.append(signal)
        current.emitted += len(created)
        if not self._replace(tenant_id, current, previous):
            # Recorded signals are delivered even when the activation was
            # paused meanwhile: they describe bars evaluated while active.
            _LOG.info(
                "activation %s changed during evaluation",
                current.activation_id,
            )
        self._route(tenant_id, current, created, now)
        return created

    def _route(
        self,
        tenant_id: str,
        activation: StrategyActivation,
        found: Sequence[signals.Signal],
        now: int,
    ) -> None:
        """Queues deliveries of newly recorded signals.

        Args:
            tenant_id: Owner.
            activation: Activation that produced them.
            found: New signals.
            now: Routing time.
        """
        if not found:
            return
        subscriptions = self.subscriptions.list_for_tenant(tenant_id)
        for signal in found:
            routed = deliveries.route(
                signal,
                subscriptions,
                frozenset(activation.channels),
                now,
                ui_target=f"tenant:{tenant_id}",
                forward_execution=self.forward_execution,
            )
            for record in routed.deliveries:
                self.outbox.enqueue(record)

    async def run_once(self) -> int:
        """Evaluates every ACTIVE activation once, tenant by tenant.

        Returns:
            Number of new signals.
        """
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    sa.select(
                        ACTIVATIONS.c.tenant_id, ACTIVATIONS.c.body_json
                    ).where(ACTIVATIONS.c.state == "ACTIVE")
                )
            )
        count = 0
        for tenant, body in rows:
            activation = StrategyActivation.model_validate_json(str(body))
            count += len(await self.evaluate(str(tenant), activation))
        return count

    async def _loop(self) -> None:
        """Evaluates activations until the stop event is set."""
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - One failed pass must not end the loop; the next pass retries.
                _LOG.exception("strategy activation pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), self.interval_s)

    def start(self) -> None:
        """Starts the evaluation loop once."""
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stops the evaluation loop before delivery and data sources close."""
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


_INPUT_FIELDS = frozenset(StrategyActivationInput.model_fields) - {"version"}
"""Fields of the request model, used to revalidate on resume."""
