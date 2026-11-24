# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Append-only, hash-chained audit records.

Each tenant has one chain: every record stores the hash of the tenant's
previous record, so editing, removing or reordering a stored record breaks
``verify_chain``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from collections.abc import Sequence

from joseph_execution.domain import ids

GENESIS_HASH = "0" * 64
"""``prev_hash`` of a tenant's first record (64 zeros)."""


def chain_hash(  # noqa: PLR0917 - the hashed record fields in record order; callers pass them positionally.
    prev_hash: str,
    event_id: str,
    tenant_id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    ts_ms: int,
    payload: Mapping[str, object],
) -> str:
    """Computes the chained hash of an audit record.

    The trace id and account id of a record are not hashed.

    Args:
        prev_hash: Hash of the tenant's previous record, or
            ``GENESIS_HASH`` for its first.
        event_id: Record id.
        tenant_id: Tenant whose chain the record extends.
        actor: Who acted.
        action: Dotted action name.
        entity_type: Kind of the audited entity.
        entity_id: Id of the audited entity.
        ts_ms: Record time, UTC epoch milliseconds.
        payload: JSON-compatible payload.

    Returns:
        Hex SHA-256 of ``prev_hash`` followed by the canonical JSON of the
        other fields.
    """
    body = ids.canonical_json(
        {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "ts_ms": ts_ms,
            "payload": payload,
        }
    )
    return ids.sha256_hex(prev_hash + body)


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable audit record.

    Attributes:
        event_id: UUIDv7.
        tenant_id: Tenant whose chain holds the record.
        actor: ``user:<id>``, ``service:<name>``, ``system`` or
            ``system:<component>``.
        action: Action such as ``order.submitted``.
        entity_type: Entity type such as ``order``.
        entity_id: Entity id.
        trace_id: Request trace id (not covered by the hash).
        ts_ms: Record time, UTC epoch milliseconds.
        payload: JSON payload (never contains secrets).
        prev_hash: Hash of the tenant's previous record.
        hash: This record's ``chain_hash``.
        account_id: Related account, if any (not covered by the hash).
    """

    event_id: str
    tenant_id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    trace_id: str
    ts_ms: int
    payload: Mapping[str, object]
    prev_hash: str
    hash: str
    account_id: str | None = None

    def recompute_hash(self) -> str:
        """Recomputes the hash from the stored fields.

        Returns:
            The ``chain_hash`` the record should carry; it differs from
            ``hash`` when a hashed field was altered.
        """
        return chain_hash(
            self.prev_hash,
            self.event_id,
            self.tenant_id,
            self.actor,
            self.action,
            self.entity_type,
            self.entity_id,
            self.ts_ms,
            self.payload,
        )

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``AuditEvent``.

        Returns:
            JSON-compatible dict.
        """
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "account_id": self.account_id,
            "actor": self.actor,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "trace_id": self.trace_id,
            "ts_ms": self.ts_ms,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


def verify_chain(events: Sequence[AuditEvent]) -> bool:
    """Verifies an ordered, complete tenant chain.

    Args:
        events: Every record of one tenant in append order, starting with
            its first.

    Returns:
        True when each record links to its predecessor (the first to
        ``GENESIS_HASH``) and its hash matches its fields; True for an
        empty chain.
    """
    previous = GENESIS_HASH
    for event in events:
        if event.prev_hash != previous:
            return False
        if event.recompute_hash() != event.hash:
            return False
        previous = event.hash
    return True
