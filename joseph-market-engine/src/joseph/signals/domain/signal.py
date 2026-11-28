# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal records: immutable notifications of strategies and alerts.

Strategy activations emit ``ENTRY_CONFIRMED`` and ``POSITION_EXITED``;
alert jobs emit ``CUSTOM_ALERT``. A fired signal is never rewritten (the
signal store only normalizes bodies stored by the removed declarative
strategies). Its ``dedupe_key`` is unique per tenant, so replaying the same
bars cannot create a second record, and its id is derived from that key so
batch, replay and live runs agree.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Mapping

from joseph.domain import decimals
from joseph.domain import ids

SEVERITY_BY_KIND: Mapping[str, str] = {
    "ENTRY_CONFIRMED": "ENTRY",
    "POSITION_EXITED": "INFO",
    "CUSTOM_ALERT": "INFO",
}
"""Signal kinds and their default severity.

Strategy activations emit ``ENTRY_CONFIRMED`` and ``POSITION_EXITED``;
alert jobs emit ``CUSTOM_ALERT`` with the severity their condition chose.
"""

SIGNAL_KINDS: frozenset[str] = frozenset(SEVERITY_BY_KIND)
"""Every signal kind of the contract."""

RETIRED_KINDS: frozenset[str] = frozenset(
    {
        "WATCHING_AOI",
        "PRE_SIGNAL",
        "ENTRY_BLOCKED",
        "AOI_INVALIDATED",
        "SETUP_EXPIRED",
        "QUALIFICATION_CHANGED",
    }
)
"""Kinds of the removed declarative strategies; only old rows carry them."""

# Order used by minimum-severity routing; unknown severities rank as INFO.
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
        0 (INFO) to 3 (CRITICAL); 0 for an unknown severity.
    """
    return _SEVERITY_RANK.get(severity, 0)


def signal_id_for(key: str) -> str:
    """Derives a deterministic signal id from a dedupe key.

    Args:
        key: Dedupe key.

    Returns:
        ``sig-`` plus 32 hex characters.
    """
    return "sig-" + ids.sha256_hex(key.encode("utf-8"))[:32]


@dataclasses.dataclass(frozen=True, slots=True)
class Signal:
    """One notification of a strategy or alert script (contract ``Signal``).

    Attributes:
        signal_id: Deterministic id derived from ``dedupe_key``.
        dedupe_key: Unique key per tenant.
        tenant_id: Owning tenant.
        strategy_id: Script id of the strategy or alert.
        strategy_version_id: Immutable script version id.
        source_hash: Hash of the script source that ran.
        instrument_id: Canonical instrument id.
        direction: ``LONG`` or ``SHORT``; ``NONE`` for alerts.
        timeframe: Signal timeframe.
        kind: Signal kind (a key of ``SEVERITY_BY_KIND``).
        severity: ``INFO``, ``WARNING``, ``ENTRY`` or ``CRITICAL``.
        mode: Activation mode; ``ALERT_ONLY`` for alerts.
        bar_start_ms: Evaluated bar start in UTC epoch ms.
        bar_close_ms: Evaluated bar close in UTC epoch ms.
        evaluated_at_ms: Evaluation time in UTC epoch ms.
        data_source: Feed state of the evaluated bars.
        entry_price: Planned entry (the signal bar's close), or ``None``.
        stop_price: Strategy stop, or ``None``.
        target_price: Strategy target, or ``None``.
        rr: Reward/risk of entry, stop and target, or ``None``.
        message: Human-readable summary.
        condition: Name of the alert condition that matched, or ``None``
            for strategy signals.
    """

    signal_id: str
    dedupe_key: str
    tenant_id: str
    strategy_id: str
    strategy_version_id: str
    source_hash: str
    instrument_id: str
    direction: str
    timeframe: str
    kind: str
    severity: str
    mode: str
    bar_start_ms: int
    bar_close_ms: int
    evaluated_at_ms: int
    data_source: str
    entry_price: decimal.Decimal | None = None
    stop_price: decimal.Decimal | None = None
    target_price: decimal.Decimal | None = None
    rr: decimal.Decimal | None = None
    message: str = ""
    condition: str | None = None

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
            "source_hash": self.source_hash,
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "kind": self.kind,
            "severity": self.severity,
            "mode": self.mode,
            "bar_start_ms": self.bar_start_ms,
            "bar_close_ms": self.bar_close_ms,
            "evaluated_at_ms": self.evaluated_at_ms,
            "data_source": self.data_source,
            "entry_price": decimals.format_optional(self.entry_price),
            "stop_price": decimals.format_optional(self.stop_price),
            "target_price": decimals.format_optional(self.target_price),
            "rr": decimals.format_optional(self.rr),
            "message": self.message,
            "condition": self.condition,
        }

    @classmethod
    def from_contract(cls, data: Mapping[str, object]) -> Signal:
        """Restores a signal from its contract dict.

        Bodies stored before the declarative strategies were removed are
        read too: their ``rule_version`` becomes ``source_hash``, the
        condition name comes from the alert's reason tree and the other
        fields of that era are ignored.

        Args:
            data: Contract ``Signal`` object.

        Returns:
            The signal.

        Raises:
            ValueError: If required fields are malformed.
            KeyError: If a required field is missing.
        """
        source_hash = data.get("source_hash", data.get("rule_version"))
        if source_hash is None:
            raise KeyError("source_hash")
        condition = data.get("condition", _legacy_condition(data))
        return cls(
            signal_id=str(data["signal_id"]),
            dedupe_key=str(data["dedupe_key"]),
            tenant_id=str(data["tenant_id"]),
            strategy_id=str(data["strategy_id"]),
            strategy_version_id=str(data["strategy_version_id"]),
            source_hash=str(source_hash),
            instrument_id=str(data["instrument_id"]),
            direction=str(data["direction"]),
            timeframe=str(data["timeframe"]),
            kind=str(data["kind"]),
            severity=str(data["severity"]),
            mode=str(data["mode"]),
            bar_start_ms=_int(data["bar_start_ms"]),
            bar_close_ms=_int(data["bar_close_ms"]),
            evaluated_at_ms=_int(data["evaluated_at_ms"]),
            data_source=str(data["data_source"]),
            entry_price=decimals.to_optional_decimal(data.get("entry_price")),
            stop_price=decimals.to_optional_decimal(data.get("stop_price")),
            target_price=decimals.to_optional_decimal(data.get("target_price")),
            rr=decimals.to_optional_decimal(data.get("rr")),
            message=str(data.get("message", "")),
            condition=None if condition is None else str(condition),
        )


def is_legacy_body(data: Mapping[str, object]) -> bool:
    """Checks whether a stored body predates the current contract.

    Args:
        data: Stored contract ``Signal`` object.

    Returns:
        True when it lacks ``source_hash`` (it was written with the
        declarative-strategy fields).
    """
    return "source_hash" not in data


def _legacy_condition(data: Mapping[str, object]) -> object:
    """Reads the condition name of an alert stored with a reason tree.

    Args:
        data: Stored contract ``Signal`` object.

    Returns:
        The reason tree's ``node_id`` for ``CUSTOM_ALERT`` bodies, else
        ``None``.
    """
    tree = data.get("reason_tree")
    if data.get("kind") != "CUSTOM_ALERT" or not isinstance(tree, Mapping):
        return None
    return tree.get("node_id")


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
