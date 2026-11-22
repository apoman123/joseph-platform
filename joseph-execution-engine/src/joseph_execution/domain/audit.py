"""Append-only, hash-chained audit records."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from collections.abc import Sequence

from joseph_execution.domain import ids

GENESIS_HASH = "0" * 64


def chain_hash(  # noqa: PLR0917
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

    Args:
        prev_hash: Hash of the previous record of the tenant.
        event_id: Record id.
        tenant_id: Tenant.
        actor: Actor.
        action: Action name.
        entity_type: Entity type.
        entity_id: Entity id.
        ts_ms: Timestamp.
        payload: JSON payload.

    Returns:
        Hex SHA-256 over ``prev_hash`` + canonical record JSON.
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
        tenant_id: Tenant.
        actor: ``user:<id>``, ``service:<name>`` or ``system``.
        action: Action such as ``order.submitted``.
        entity_type: Entity type such as ``order``.
        entity_id: Entity id.
        trace_id: Request trace id.
        ts_ms: Time.
        payload: JSON payload (never contains secrets).
        prev_hash: Previous record hash of the tenant chain.
        hash: This record's chained hash.
        account_id: Related account, if any.
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
            Expected hash.
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
        events: Records of one tenant in append order.

    Returns:
        True when every link and hash is intact.
    """
    previous = GENESIS_HASH
    for event in events:
        if event.prev_hash != previous:
            return False
        if event.recompute_hash() != event.hash:
            return False
        previous = event.hash
    return True
