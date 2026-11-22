"""Signal records: immutable lifecycle notifications of strategy setups.

A fired signal is never rewritten. Its ``dedupe_key`` is unique per tenant,
so replaying the same bars cannot create a second record, and its id is
derived from that key so batch, replay and live runs agree.
"""

from __future__ import annotations

import dataclasses
import decimal
import json
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import decimals
from joseph.domain import ids
from joseph.domain import structures

SEVERITY_BY_KIND: Mapping[str, str] = {
    "WATCHING_AOI": "INFO",
    "PRE_SIGNAL": "INFO",
    "ENTRY_CONFIRMED": "ENTRY",
    "ENTRY_BLOCKED": "WARNING",
    "AOI_INVALIDATED": "WARNING",
    "SETUP_EXPIRED": "INFO",
    "QUALIFICATION_CHANGED": "INFO",
    "POSITION_EXITED": "INFO",
}
"""Contract severity of each signal kind."""

_SEVERITY_RANK: Mapping[str, int] = {
    "INFO": 0,
    "WARNING": 1,
    "ENTRY": 2,
    "CRITICAL": 3,
}


def severity_rank(severity: str) -> int:
    """Orders severities for minimum-severity routing.

    Args:
        severity: Contract severity.

    Returns:
        0 (INFO) to 3 (CRITICAL).
    """
    return _SEVERITY_RANK.get(severity, 0)


def dedupe_key(
    *,
    tenant_id: str,
    strategy_version_id: str,
    account_scope: str,
    instrument_id: str,
    direction: str,
    setup_id: str,
    kind: str,
) -> str:
    """Builds the signal de-duplication key.

    Args:
        tenant_id: Owning tenant.
        strategy_version_id: Immutable version id.
        account_scope: Account scope (``*`` = any account).
        instrument_id: Canonical instrument id.
        direction: ``LONG`` or ``SHORT``.
        setup_id: Setup id.
        kind: Signal kind.

    Returns:
        Pipe-joined key.
    """
    return "|".join(
        (
            tenant_id,
            strategy_version_id,
            account_scope,
            instrument_id,
            direction,
            setup_id,
            kind,
        )
    )


def signal_id_for(key: str) -> str:
    """Derives a deterministic signal id from a dedupe key.

    Args:
        key: Dedupe key.

    Returns:
        ``sig-`` plus 32 hex characters.
    """
    return "sig-" + ids.sha256_hex(key.encode("utf-8"))[:32]


def _strip(
    mapping: Mapping[str, object], drop: frozenset[str]
) -> dict[str, object]:
    """Copies a mapping without provenance-only keys.

    Args:
        mapping: Source mapping.
        drop: Keys to remove.

    Returns:
        Filtered copy.
    """
    return {k: v for k, v in mapping.items() if k not in drop}


_CANDLE_PROVENANCE = frozenset(
    {"received_at_ms", "feed_state", "source", "event_ts_ms"}
)


def inputs_snapshot_hash(frames: Sequence[structures.BarFrame]) -> str:
    """Hashes the market inputs of an evaluation.

    Provenance (receipt time, feed state, source) is excluded, so live and
    replayed copies of the same confirmed bars hash identically.

    Args:
        frames: Evaluated frames, oldest first.

    Returns:
        ``sha256:<hex>``.
    """
    payload: list[dict[str, object]] = []
    for frame in frames:
        snapshots = []
        for key in sorted(frame.snapshots, key=lambda k: (k[0], k[1].value)):
            snapshot = frame.snapshots[key].to_contract()
            snapshots.append(_strip(snapshot, frozenset({"data_quality"})))
        payload.append(
            {
                "candle": _strip(
                    frame.candle.to_contract(), _CANDLE_PROVENANCE
                ),
                "events": [
                    event.to_contract()
                    for event in sorted(frame.events, key=_event_id)
                ],
                "snapshots": snapshots,
            }
        )
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + ids.sha256_hex(text.encode("utf-8"))


def _event_id(event: structures.StructureEvent) -> str:
    """Sort key for events.

    Args:
        event: Structure event.

    Returns:
        The event id.
    """
    return event.event_id


@dataclasses.dataclass(frozen=True, slots=True)
class Signal:
    """One lifecycle notification (contract ``Signal``).

    Attributes:
        signal_id: Deterministic id derived from ``dedupe_key``.
        dedupe_key: Unique key per tenant.
        tenant_id: Owning tenant.
        strategy_id: Strategy slug.
        strategy_version_id: Immutable version id.
        setup_id: Setup id.
        instrument_id: Canonical instrument id.
        direction: ``LONG`` or ``SHORT``.
        timeframe: Signal timeframe.
        kind: Signal kind.
        setup_state: Setup state after the bar.
        severity: Severity.
        bar_start_ms: Evaluated bar start.
        bar_close_ms: Evaluated bar close.
        evaluated_at_ms: Evaluation time.
        inputs_snapshot_hash: Hash of the evaluated inputs.
        rule_version: Definition hash.
        evaluator_version: Evaluator semantics version.
        indicator_versions: Plugin versions read.
        reason_tree: Contract ``ReasonNode`` dict.
        aoi_ids: Touched AOIs.
        range_ids: Bound dealing ranges.
        entry_price: Projected entry.
        stop_price: Projected stop.
        target_price: Projected target.
        rr: Net reward/risk.
        risk_status: ``PASS``, ``FAIL``, ``UNKNOWN`` or ``NOT_APPLICABLE``.
        risk_reasons: Risk notes or blockers.
        mode: Strategy activation mode.
        data_source: Provenance of the evaluated bar.
        revision: Revision counter (0 for originals).
        message: Human-readable summary.
        bar_base_volume: Base volume of the evaluated bar.
        bar_quote_volume: Venue-published quote turnover of the evaluated
            bar (``None`` when the venue publishes none; never estimated).
    """

    signal_id: str
    dedupe_key: str
    tenant_id: str
    strategy_id: str
    strategy_version_id: str
    setup_id: str | None
    instrument_id: str
    direction: str
    timeframe: str
    kind: str
    setup_state: str
    severity: str
    bar_start_ms: int
    bar_close_ms: int
    evaluated_at_ms: int
    inputs_snapshot_hash: str
    rule_version: str
    evaluator_version: str
    indicator_versions: Mapping[str, str]
    reason_tree: Mapping[str, object]
    aoi_ids: tuple[str, ...]
    range_ids: tuple[str, ...]
    entry_price: decimal.Decimal | None
    stop_price: decimal.Decimal | None
    target_price: decimal.Decimal | None
    rr: decimal.Decimal | None
    risk_status: str
    risk_reasons: tuple[str, ...]
    mode: str
    data_source: str
    revision: int = 0
    message: str = ""
    bar_base_volume: decimal.Decimal | None = None
    bar_quote_volume: decimal.Decimal | None = None

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``Signal`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "signal_id": self.signal_id,
            "dedupe_key": self.dedupe_key,
            "tenant_id": self.tenant_id,
            "strategy_id": self.strategy_id,
            "strategy_version_id": self.strategy_version_id,
            "setup_id": self.setup_id,
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "kind": self.kind,
            "setup_state": self.setup_state,
            "severity": self.severity,
            "bar_start_ms": self.bar_start_ms,
            "bar_close_ms": self.bar_close_ms,
            "evaluated_at_ms": self.evaluated_at_ms,
            "inputs_snapshot_hash": self.inputs_snapshot_hash,
            "rule_version": self.rule_version,
            "evaluator_version": self.evaluator_version,
            "indicator_versions": dict(self.indicator_versions),
            "reason_tree": dict(self.reason_tree),
            "aoi_ids": list(self.aoi_ids),
            "range_ids": list(self.range_ids),
            "entry_price": decimals.format_optional(self.entry_price),
            "stop_price": decimals.format_optional(self.stop_price),
            "target_price": decimals.format_optional(self.target_price),
            "rr": decimals.format_optional(self.rr),
            "risk_evaluation": {
                "status": self.risk_status,
                "reasons": list(self.risk_reasons),
            },
            "mode": self.mode,
            "data_source": self.data_source,
            "revision": self.revision,
            "message": self.message,
            "bar_base_volume": decimals.format_optional(self.bar_base_volume),
            "bar_quote_volume": decimals.format_optional(self.bar_quote_volume),
        }

    @classmethod
    def from_contract(cls, data: Mapping[str, object]) -> Signal:
        """Restores a signal from its contract dict.

        Args:
            data: Contract ``Signal`` object.

        Returns:
            The signal.

        Raises:
            ValueError: If required fields are malformed.
        """
        risk_eval = data.get("risk_evaluation")
        if not isinstance(risk_eval, Mapping):
            raise ValueError("risk_evaluation missing")
        reason_tree = data.get("reason_tree")
        versions = data.get("indicator_versions") or {}
        if not isinstance(reason_tree, Mapping) or not isinstance(
            versions, Mapping
        ):
            raise ValueError("reason_tree or indicator_versions malformed")
        setup_id = data.get("setup_id")
        return cls(
            signal_id=str(data["signal_id"]),
            dedupe_key=str(data["dedupe_key"]),
            tenant_id=str(data["tenant_id"]),
            strategy_id=str(data["strategy_id"]),
            strategy_version_id=str(data["strategy_version_id"]),
            setup_id=None if setup_id is None else str(setup_id),
            instrument_id=str(data["instrument_id"]),
            direction=str(data["direction"]),
            timeframe=str(data["timeframe"]),
            kind=str(data["kind"]),
            setup_state=str(data["setup_state"]),
            severity=str(data["severity"]),
            bar_start_ms=_int(data["bar_start_ms"]),
            bar_close_ms=_int(data["bar_close_ms"]),
            evaluated_at_ms=_int(data["evaluated_at_ms"]),
            inputs_snapshot_hash=str(data["inputs_snapshot_hash"]),
            rule_version=str(data["rule_version"]),
            evaluator_version=str(data["evaluator_version"]),
            indicator_versions={str(k): str(v) for k, v in versions.items()},
            reason_tree=dict(reason_tree),
            aoi_ids=tuple(str(v) for v in _seq(data.get("aoi_ids"))),
            range_ids=tuple(str(v) for v in _seq(data.get("range_ids"))),
            entry_price=decimals.to_optional_decimal(data.get("entry_price")),
            stop_price=decimals.to_optional_decimal(data.get("stop_price")),
            target_price=decimals.to_optional_decimal(data.get("target_price")),
            rr=decimals.to_optional_decimal(data.get("rr")),
            risk_status=str(risk_eval["status"]),
            risk_reasons=tuple(str(v) for v in _seq(risk_eval.get("reasons"))),
            mode=str(data["mode"]),
            data_source=str(data["data_source"]),
            revision=_int(data.get("revision", 0)),
            message=str(data.get("message", "")),
            bar_base_volume=decimals.to_optional_decimal(
                data.get("bar_base_volume")
            ),
            bar_quote_volume=decimals.to_optional_decimal(
                data.get("bar_quote_volume")
            ),
        )


def _int(value: object) -> int:
    """Coerces a stored integer.

    Args:
        value: Stored value.

    Returns:
        The integer.

    Raises:
        ValueError: If not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected int, got {value!r}")
    return value


def _seq(value: object) -> list[object]:
    """Coerces a stored list (``None`` becomes empty).

    Args:
        value: Stored value.

    Returns:
        The list.

    Raises:
        ValueError: If not a list.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected list, got {value!r}")
    return value
