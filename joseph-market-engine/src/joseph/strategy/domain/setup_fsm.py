"""Setup lifecycle state machine with de-duplication.

One ``InstanceState`` exists per ``(tenant, strategy_version, account scope,
venue, instrument, direction)``. Each closed signal bar's ``Evaluation`` is
folded into it by the pure ``step`` function, which returns the new state,
the transitions taken and the signal emissions.

State mapping (``gates`` are the top-level conjuncts of the root ``ALL``):

* WAITING — no open setup (conditions not progressing).
* AOI_TOUCHED — a positive AOI_TOUCH is true but more than one gate is
  still outstanding. Emits WATCHING_AOI.
* CONFIRMING — all gates but one are true (for rules without an AOI_TOUCH
  this alone opens a setup). Emits PRE_SIGNAL.
* READY — every gate true, or no gate FALSE with some UNKNOWN. Transient:
  the same bar resolves READY to FIRED or BLOCKED.
* FIRED — entry confirmed: risk computable, net R:R >= the strategy floor,
  instrument qualified, no cooldown and AOI not already used when
  ``once_per_aoi``. Emits ENTRY_CONFIRMED. Terminal.
* BLOCKED — entry conditions met (or only UNKNOWN) but a blocker applies.
  Emits ENTRY_BLOCKED once per setup; may later become READY again.
* EXPIRED — open longer than ``expire_after_bars`` or conditions lapsed
  back to WAITING. Emits SETUP_EXPIRED. Terminal.
* INVALIDATED — the anchoring AOI stopped being live. Emits
  AOI_INVALIDATED. Terminal.
* CANCELLED — the strategy was paused or replaced. Terminal.

Idempotency: a bar close at or before the last processed close never emits
again. A higher candle revision for the last processed close yields a
``RevisionNotice`` audit record and never rewrites a fired signal.

Expiry and cooldown are counted in signal bars of the instrument's session
calendar (``LifecyclePolicy.calendar``): on an exchange a setup opened at the
last bar of a session that expires after 2 bars lives through the first two
bars of the next session, not two wall-clock bar lengths.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Mapping

from joseph.domain import ids
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.strategy.domain import compiler
from joseph.strategy.domain import errors
from joseph.strategy.domain import evaluator
from joseph.strategy.domain import risk
from joseph.strategy.domain import tristate

T = tristate.TriState


class SetupState(enum.StrEnum):
    """Setup lifecycle states (contract ``SetupState``)."""

    WAITING = "WAITING"
    AOI_TOUCHED = "AOI_TOUCHED"
    CONFIRMING = "CONFIRMING"
    READY = "READY"
    FIRED = "FIRED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


S = SetupState
_OPEN_EXITS = frozenset({S.EXPIRED, S.INVALIDATED, S.CANCELLED})

TRANSITIONS: Mapping[SetupState, frozenset[SetupState]] = {
    S.WAITING: frozenset({S.AOI_TOUCHED, S.CONFIRMING, S.READY, S.CANCELLED}),
    S.AOI_TOUCHED: frozenset({S.CONFIRMING, S.READY} | _OPEN_EXITS),
    S.CONFIRMING: frozenset({S.AOI_TOUCHED, S.READY} | _OPEN_EXITS),
    S.READY: frozenset({S.FIRED, S.BLOCKED}),
    S.BLOCKED: frozenset({S.AOI_TOUCHED, S.CONFIRMING, S.READY} | _OPEN_EXITS),
    S.FIRED: frozenset(),
    S.EXPIRED: frozenset(),
    S.INVALIDATED: frozenset(),
    S.CANCELLED: frozenset(),
}
"""Every legal transition; anything else raises."""

TERMINAL: frozenset[SetupState] = frozenset(
    state for state, targets in TRANSITIONS.items() if not targets
)
"""States with no outgoing transitions."""


def check_transition(source: SetupState, target: SetupState) -> None:
    """Validates a transition against the table.

    Args:
        source: Current state.
        target: Requested state.

    Raises:
        errors.IllegalTransitionError: If the transition is not allowed.
    """
    if target not in TRANSITIONS[source]:
        raise errors.IllegalTransitionError(
            f"illegal setup transition {source.value} -> {target.value}"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class InstanceKey:
    """Scope of one strategy instance.

    Attributes:
        tenant_id: Owning tenant.
        strategy_version_id: Immutable version id.
        account_scope: Account the signals are scoped to (``*`` = any).
        instrument_id: Canonical instrument id.
        direction: ``LONG`` or ``SHORT``.
    """

    tenant_id: str
    strategy_version_id: str
    account_scope: str
    instrument_id: str
    direction: str

    @property
    def venue(self) -> str:
        """Returns the venue part of the instrument id."""
        return self.instrument_id.split(":", 1)[0]

    def as_text(self) -> str:
        """Returns the canonical key text.

        Returns:
            ``tenant|version|scope|venue|instrument|direction``.
        """
        return "|".join(
            (
                self.tenant_id,
                self.strategy_version_id,
                self.account_scope,
                self.venue,
                self.instrument_id,
                self.direction,
            )
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Setup:
    """An open setup.

    Attributes:
        setup_id: Deterministic id.
        state: Current state (never terminal while stored).
        started_at_ms: Close of the bar that opened the setup.
        anchor_aoi_id: AOI anchoring the setup, if any.
        emitted_kinds: Signal kinds already emitted for this setup.
    """

    setup_id: str
    state: SetupState
    started_at_ms: int
    anchor_aoi_id: str | None
    emitted_kinds: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True, slots=True)
class InstanceState:
    """Checkpointable state of one strategy instance.

    Attributes:
        key: Instance scope.
        setup: Open setup, if any.
        last_close_ms: Last processed bar close.
        last_revision: Candle revision of the last processed bar.
        cooldown_until_ms: Entries are blocked before this bar close.
        fired_aoi_ids: AOIs that already produced an entry.
        last_fired_close_ms: Close of the bar that last fired.
    """

    key: InstanceKey
    setup: Setup | None = None
    last_close_ms: int | None = None
    last_revision: int = 0
    cooldown_until_ms: int | None = None
    fired_aoi_ids: frozenset[str] = frozenset()
    last_fired_close_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Serializes for checkpoint storage.

        Returns:
            JSON-compatible dict.
        """
        setup = None
        if self.setup is not None:
            setup = {
                "setup_id": self.setup.setup_id,
                "state": self.setup.state.value,
                "started_at_ms": self.setup.started_at_ms,
                "anchor_aoi_id": self.setup.anchor_aoi_id,
                "emitted_kinds": sorted(self.setup.emitted_kinds),
            }
        return {
            "key": dataclasses.asdict(self.key),
            "setup": setup,
            "last_close_ms": self.last_close_ms,
            "last_revision": self.last_revision,
            "cooldown_until_ms": self.cooldown_until_ms,
            "fired_aoi_ids": sorted(self.fired_aoi_ids),
            "last_fired_close_ms": self.last_fired_close_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> InstanceState:
        """Restores a checkpoint.

        Args:
            data: Output of ``to_dict``.

        Returns:
            The restored state.

        Raises:
            ValueError: If the checkpoint is malformed.
        """
        key_data = data["key"]
        if not isinstance(key_data, Mapping):
            raise ValueError("checkpoint key missing")
        key = InstanceKey(**{k: str(v) for k, v in key_data.items()})
        setup_data = data.get("setup")
        setup = None
        if isinstance(setup_data, Mapping):
            kinds = setup_data.get("emitted_kinds") or []
            anchor = setup_data.get("anchor_aoi_id")
            setup = Setup(
                setup_id=str(setup_data["setup_id"]),
                state=SetupState(str(setup_data["state"])),
                started_at_ms=_as_int(setup_data["started_at_ms"]),
                anchor_aoi_id=None if anchor is None else str(anchor),
                emitted_kinds=frozenset(str(k) for k in _as_list(kinds)),
            )
        return cls(
            key=key,
            setup=setup,
            last_close_ms=_optional_int(data.get("last_close_ms")),
            last_revision=_as_int(data.get("last_revision", 0)),
            cooldown_until_ms=_optional_int(data.get("cooldown_until_ms")),
            fired_aoi_ids=frozenset(
                str(v) for v in _as_list(data.get("fired_aoi_ids") or [])
            ),
            last_fired_close_ms=_optional_int(data.get("last_fired_close_ms")),
        )


def _as_int(value: object) -> int:
    """Coerces a checkpoint integer.

    Args:
        value: Stored value.

    Returns:
        The integer.

    Raises:
        ValueError: If the value is not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected int, got {value!r}")
    return value


def _optional_int(value: object) -> int | None:
    """Coerces an optional checkpoint integer.

    Args:
        value: Stored value or ``None``.

    Returns:
        The integer or ``None``.
    """
    return None if value is None else _as_int(value)


def _as_list(value: object) -> list[object]:
    """Coerces a checkpoint list.

    Args:
        value: Stored value.

    Returns:
        The list.

    Raises:
        ValueError: If the value is not a list.
    """
    if not isinstance(value, list):
        raise ValueError(f"expected list, got {value!r}")
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class LifecyclePolicy:
    """Policy inputs of the state machine, derived from a plan.

    Attributes:
        timeframe: Signal timeframe.
        expire_after_bars: Lifetime of an unfired setup.
        cooldown_bars: Bars of blocked entries after a fire.
        once_per_aoi: Fire at most once per AOI.
        emit: Signal kinds the user receives.
        min_rr: Strategy-level net reward/risk floor.
        has_aoi_touch: Whether setups are anchored by AOI touches.
        calendar: Session calendar of the instance's instrument; expiry
            and cooldown count ``timeframe`` bars on it.
    """

    timeframe: timeframes.Timeframe
    expire_after_bars: int
    cooldown_bars: int
    once_per_aoi: bool
    emit: frozenset[str]
    min_rr: decimal.Decimal
    has_aoi_touch: bool
    calendar: sessions.BarCalendar = sessions.CONTINUOUS

    @classmethod
    def from_plan(
        cls,
        plan: compiler.CompiledStrategy,
        calendar: sessions.BarCalendar = sessions.CONTINUOUS,
    ) -> LifecyclePolicy:
        """Builds the policy from a compiled strategy.

        Args:
            plan: Compiled strategy.
            calendar: Session calendar of the instance's instrument
                (``sessions.for_instrument``).

        Returns:
            The policy.
        """
        definition = plan.definition
        return cls(
            timeframe=definition.signal_timeframe,
            expire_after_bars=definition.setup.expire_after_bars,
            cooldown_bars=definition.notification.cooldown_bars,
            once_per_aoi=definition.notification.once_per_aoi,
            emit=frozenset(definition.notification.emit),
            min_rr=definition.risk.min_rr,
            has_aoi_touch=plan.has_aoi_touch,
            calendar=calendar,
        )

    def bars_after(self, close_ms: int, bars: int) -> int:
        """Returns the close of the signal bar ``bars`` bars after a close.

        On an exchange the close of a session's last bar is not the next
        bar's start, so the count restarts at the next session's open.

        Args:
            close_ms: Close of a signal bar.
            bars: Bars to count on the calendar (0 returns ``close_ms``).

        Returns:
            Time at which ``bars`` more signal bars have closed.
        """
        if bars <= 0:
            return close_ms
        tf = self.timeframe
        following = self.calendar.ceil(tf, close_ms)
        return self.calendar.end_of(
            tf, self.calendar.shift(tf, following, bars - 1)
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Transition:
    """A state change of one setup.

    Attributes:
        setup_id: Setup id.
        source: Previous state.
        target: New state.
        bar_close_ms: Bar close at which it happened.
        reason: Explanation.
    """

    setup_id: str
    source: SetupState
    target: SetupState
    bar_close_ms: int
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class Emission:
    """A signal the instance wants to publish.

    Attributes:
        kind: Contract ``SignalKind``.
        setup_id: Setup the signal belongs to.
        setup_state: State after this bar.
        reason: Explanation.
        risk_status: ``PASS``, ``FAIL``, ``UNKNOWN`` or ``NOT_APPLICABLE``.
        risk_reasons: Blockers or risk notes.
    """

    kind: str
    setup_id: str
    setup_state: SetupState
    reason: str
    risk_status: str
    risk_reasons: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class RevisionNotice:
    """Audit record for a corrected bar that was already processed.

    Attributes:
        bar_close_ms: Close of the corrected bar.
        old_revision: Revision processed earlier.
        new_revision: Revision received now.
    """

    bar_close_ms: int
    old_revision: int
    new_revision: int


@dataclasses.dataclass(frozen=True, slots=True)
class StepOutcome:
    """Result of folding one evaluation into an instance.

    Attributes:
        state: New instance state.
        transitions: Transitions taken, in order.
        emissions: Signals to publish.
        revision: Revision audit notice, if any.
        skipped: Why the bar was ignored or held, if it was.
    """

    state: InstanceState
    transitions: tuple[Transition, ...] = ()
    emissions: tuple[Emission, ...] = ()
    revision: RevisionNotice | None = None
    skipped: str | None = None


class _Step:
    """Mutable working copy used inside ``step``.

    Attributes:
        setup: Open setup being advanced.
        transitions: Transitions recorded so far.
        emissions: Emissions recorded so far.
    """

    def __init__(self, policy: LifecyclePolicy, close_ms: int) -> None:
        """Initializes the working copy.

        Args:
            policy: Lifecycle policy.
            close_ms: Bar close being processed.
        """
        self.policy = policy
        self.close_ms = close_ms
        self.setup: Setup | None = None
        self.transitions: list[Transition] = []
        self.emissions: list[Emission] = []

    def move(self, target: SetupState, reason: str) -> None:
        """Transitions the open setup.

        Args:
            target: New state.
            reason: Explanation.
        """
        setup = self.setup
        if setup is None or setup.state is target:
            return
        check_transition(setup.state, target)
        self.transitions.append(
            Transition(
                setup.setup_id, setup.state, target, self.close_ms, reason
            )
        )
        self.setup = dataclasses.replace(setup, state=target)

    def emit(
        self,
        kind: str,
        reason: str,
        risk_status: str = "NOT_APPLICABLE",
        risk_reasons: tuple[str, ...] = (),
    ) -> None:
        """Emits a signal once per setup and kind, if the user wants it.

        Args:
            kind: Signal kind.
            reason: Explanation.
            risk_status: Risk evaluation status.
            risk_reasons: Risk notes.
        """
        setup = self.setup
        if setup is None or kind in setup.emitted_kinds:
            return
        self.setup = dataclasses.replace(
            setup, emitted_kinds=setup.emitted_kinds | {kind}
        )
        if kind in self.policy.emit:
            self.emissions.append(
                Emission(
                    kind=kind,
                    setup_id=setup.setup_id,
                    setup_state=setup.state,
                    reason=reason,
                    risk_status=risk_status,
                    risk_reasons=risk_reasons,
                )
            )


def _target_phase(
    evaluation: evaluator.Evaluation, policy: LifecyclePolicy
) -> SetupState:
    """Maps an evaluation to the phase it supports.

    Args:
        evaluation: Evaluation of the bar.
        policy: Lifecycle policy.

    Returns:
        WAITING, AOI_TOUCHED, CONFIRMING or READY.
    """
    gates = evaluation.gate_results
    true_count = sum(1 for g in gates if g is T.TRUE)
    if evaluation.result is T.TRUE:
        return S.READY
    if (
        evaluation.result is T.UNKNOWN
        and T.FALSE not in gates
        and true_count >= 1
    ):
        return S.READY
    touched = bool(evaluation.touched)
    if (
        len(gates) >= 2
        and true_count == len(gates) - 1
        and (touched or not policy.has_aoi_touch)
    ):
        return S.CONFIRMING
    if touched:
        return S.AOI_TOUCHED
    return S.WAITING


def _anchor_invalidated(
    evaluation: evaluator.Evaluation, anchor_aoi_id: str
) -> bool:
    """Checks whether the anchoring AOI stopped being live.

    Args:
        evaluation: Evaluation of the bar.
        anchor_aoi_id: Anchor AOI id.

    Returns:
        True when the latest record is no longer live or an
        ``AOI_INVALIDATED`` event names it.
    """
    frame = evaluation.frames[-1]
    for event in frame.events:
        if event.aoi_id == anchor_aoi_id and event.name.value in (
            "AOI_INVALIDATED",
            "AOI_EXPIRED",
        ):
            return True
    for snap in frame.snapshots.values():
        for aoi in snap.aois:
            if aoi.aoi_id == anchor_aoi_id:
                return not aoi.state.is_live
    return False


def _blockers(
    state: InstanceState,
    evaluation: evaluator.Evaluation,
    policy: LifecyclePolicy,
    qualification: tristate.TriState,
) -> tuple[list[str], bool]:
    """Lists reasons an otherwise ready entry may not fire.

    Args:
        state: Instance state before this bar.
        evaluation: Evaluation of the bar.
        policy: Lifecycle policy.
        qualification: Whether the instrument is qualified for the
            strategy's universe.

    Returns:
        ``(blockers, any_unknown)`` where ``any_unknown`` marks blockers
        caused by missing information rather than a failed check.
    """
    blockers: list[str] = []
    unknown = False
    if evaluation.result is not T.TRUE:
        unknown = True
        blockers.append("some conditions are UNKNOWN (missing or stale data)")
    assessment: risk.RiskAssessment = evaluation.risk
    if assessment.rr is None:
        unknown = True
        blockers.append(
            "risk not computable: " + ("; ".join(assessment.reasons) or "n/a")
        )
    elif assessment.rr < policy.min_rr:
        blockers.append(
            f"net R:R {assessment.rr} below the floor {policy.min_rr}"
        )
    if qualification is T.UNKNOWN:
        unknown = True
        blockers.append("instrument qualification unknown")
    elif qualification is T.FALSE:
        blockers.append("instrument not qualified")
    close = evaluation.bar_close_ms
    if state.cooldown_until_ms is not None and close < state.cooldown_until_ms:
        blockers.append(f"cooldown active until {state.cooldown_until_ms}")
    touched = set(evaluation.aoi_ids)
    if policy.once_per_aoi and touched and touched <= state.fired_aoi_ids:
        blockers.append(f"already fired for AOI {sorted(touched)}")
    return blockers, unknown


def _close_setup(
    work: _Step, evaluation: evaluator.Evaluation, target: SetupState
) -> None:
    """Ends the open setup when invalidated, expired or lapsed.

    Args:
        work: Working copy (its setup may be cleared).
        evaluation: Evaluation of the bar.
        target: Phase supported by this bar.
    """
    setup = work.setup
    if setup is None:
        return
    policy = work.policy
    expires_at_ms = policy.bars_after(
        setup.started_at_ms, policy.expire_after_bars
    )
    if setup.anchor_aoi_id and _anchor_invalidated(
        evaluation, setup.anchor_aoi_id
    ):
        work.move(S.INVALIDATED, f"anchor {setup.anchor_aoi_id} invalidated")
        work.emit("AOI_INVALIDATED", f"AOI {setup.anchor_aoi_id} invalidated")
    elif work.close_ms >= expires_at_ms:
        work.move(S.EXPIRED, f"expired after {policy.expire_after_bars} bars")
        work.emit("SETUP_EXPIRED", "setup expired without an entry")
    elif target is S.WAITING:
        work.move(S.EXPIRED, "conditions lapsed")
        work.emit("SETUP_EXPIRED", "setup conditions lapsed")
    else:
        return
    work.setup = None


def precheck(
    state: InstanceState, close_ms: int, candle_revision: int
) -> StepOutcome | None:
    """Detects duplicate, out-of-order and revised bars before evaluation.

    Args:
        state: Current instance state.
        close_ms: Close of the incoming bar.
        candle_revision: Revision of the incoming candle.

    Returns:
        A skip outcome, or ``None`` when the bar is new and must be
        evaluated.
    """
    if state.last_close_ms is None or close_ms > state.last_close_ms:
        return None
    if close_ms < state.last_close_ms:
        return StepOutcome(state=state, skipped="out_of_order")
    if candle_revision > state.last_revision:
        notice = RevisionNotice(close_ms, state.last_revision, candle_revision)
        return StepOutcome(
            state=dataclasses.replace(state, last_revision=candle_revision),
            revision=notice,
            skipped="revision_of_processed_bar",
        )
    return StepOutcome(state=state, skipped="duplicate")


def step(
    state: InstanceState,
    evaluation: evaluator.Evaluation,
    policy: LifecyclePolicy,
    *,
    qualification: tristate.TriState = T.TRUE,
    candle_revision: int = 0,
) -> StepOutcome:
    """Folds one closed-bar evaluation into the instance state.

    Args:
        state: Current instance state.
        evaluation: Evaluation of the latest closed bar.
        policy: Lifecycle policy.
        qualification: Universe qualification of the instrument at this bar.
        candle_revision: Revision of the evaluated candle.

    Returns:
        The outcome; ``skipped`` explains ignored or held bars.
    """
    close = evaluation.bar_close_ms
    skipped = precheck(state, close, candle_revision)
    if skipped is not None:
        return skipped
    base = dataclasses.replace(
        state, last_close_ms=close, last_revision=candle_revision
    )
    if not evaluation.usable:
        return StepOutcome(
            state=base, skipped=f"held: {evaluation.current_issue}"
        )

    work = _Step(policy, close)
    work.setup = state.setup
    target = _target_phase(evaluation, policy)
    _close_setup(work, evaluation, target)
    fired_ids = state.fired_aoi_ids
    anchor = risk.choose_anchor(evaluation.touched)
    anchor_id = None if anchor is None else anchor.aoi.aoi_id
    touched_ids = set(evaluation.aoi_ids)
    already_used = (
        policy.once_per_aoi and bool(touched_ids) and touched_ids <= fired_ids
    )
    if work.setup is None and target is not S.WAITING and not already_used:
        work.setup = Setup(
            setup_id=ids.stable_id(
                "setup", state.key.as_text(), anchor_id or "-", close
            ),
            state=S.WAITING,
            started_at_ms=close,
            anchor_aoi_id=anchor_id,
        )
    new_state = base
    if work.setup is not None:
        new_state = _advance(
            work,
            state,
            base,
            evaluation,
            target=target,
            qualification=qualification,
        )
    open_setup = work.setup
    if open_setup is not None and open_setup.state in TERMINAL:
        open_setup = None
    new_state = dataclasses.replace(new_state, setup=open_setup)
    return StepOutcome(
        state=new_state,
        transitions=tuple(work.transitions),
        emissions=tuple(work.emissions),
    )


def _advance(
    work: _Step,
    state: InstanceState,
    base: InstanceState,
    evaluation: evaluator.Evaluation,
    *,
    target: SetupState,
    qualification: tristate.TriState,
) -> InstanceState:
    """Moves the open setup toward ``target`` and resolves READY.

    Args:
        work: Working copy holding the open setup.
        state: Instance state before this bar.
        base: State with the bar marked processed.
        evaluation: Evaluation of the bar.
        target: Phase supported by this bar.
        qualification: Universe qualification of the instrument.

    Returns:
        Updated instance state (setup is set by the caller).
    """
    if target is S.AOI_TOUCHED:
        work.move(S.AOI_TOUCHED, "AOI touched")
        work.emit("WATCHING_AOI", "price touched a qualifying AOI")
        return base
    if target is S.CONFIRMING:
        work.move(S.CONFIRMING, "one condition outstanding")
        work.emit("PRE_SIGNAL", "all but one entry condition hold")
        return base
    if target is not S.READY:
        return base
    work.move(S.READY, "entry conditions met")
    blockers, unknown = _blockers(state, evaluation, work.policy, qualification)
    if blockers:
        work.move(S.BLOCKED, "; ".join(blockers))
        work.emit(
            "ENTRY_BLOCKED",
            "; ".join(blockers),
            "UNKNOWN" if unknown else "FAIL",
            tuple(blockers),
        )
        return base
    work.move(S.FIRED, "entry confirmed")
    work.emit("ENTRY_CONFIRMED", "entry confirmed", "PASS")
    policy = work.policy
    cooldown = None
    if policy.cooldown_bars > 0:
        cooldown = policy.bars_after(work.close_ms, policy.cooldown_bars)
    return dataclasses.replace(
        base,
        fired_aoi_ids=state.fired_aoi_ids | frozenset(evaluation.aoi_ids),
        cooldown_until_ms=cooldown,
        last_fired_close_ms=work.close_ms,
    )


def cancel(state: InstanceState, bar_close_ms: int, reason: str) -> StepOutcome:
    """Cancels the open setup (strategy paused or replaced).

    Args:
        state: Instance state.
        bar_close_ms: Time of cancellation.
        reason: Explanation.

    Returns:
        Outcome with the CANCELLED transition, if a setup was open.
    """
    setup = state.setup
    if setup is None or setup.state is S.READY:
        return StepOutcome(state=state)
    check_transition(setup.state, S.CANCELLED)
    transition = Transition(
        setup.setup_id, setup.state, S.CANCELLED, bar_close_ms, reason
    )
    return StepOutcome(
        state=dataclasses.replace(state, setup=None), transitions=(transition,)
    )
