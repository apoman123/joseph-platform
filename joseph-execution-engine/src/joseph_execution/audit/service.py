# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Audit log service: appends hash-chained records inside transactions."""

from __future__ import annotations

from collections.abc import Mapping

from joseph_execution.domain import audit
from joseph_execution.domain import tracing
from joseph_execution.ports import repositories
from joseph_execution.ports import services

# Case-insensitive substrings: a payload key containing any of them has its
# value redacted before the record is hashed and stored.
_SECRET_KEYS = (
    "secret",
    "password",
    "private_key",
    "api_key",
    "token",
    "seed",
    "mnemonic",
    "passphrase",
    "signature",
)


def _scrub(payload: Mapping[str, object]) -> dict[str, object]:
    """Redacts credential-like values in an audit payload.

    Nested mappings are scrubbed recursively; values inside lists are kept
    as they are.

    Args:
        payload: Raw payload (not mutated).

    Returns:
        A copy whose values under secret-like keys (see ``_SECRET_KEYS``)
        are replaced by ``"[REDACTED]"``.
    """
    clean: dict[str, object] = {}
    for key, value in payload.items():
        if any(marker in key.lower() for marker in _SECRET_KEYS):
            clean[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            clean[key] = _scrub(value)
        else:
            clean[key] = value
    return clean


class AuditLog:
    """Audit writer that appends records in the caller's transaction.

    Each record extends its tenant's hash chain, so it commits or rolls back
    together with the change it audits.
    """

    def __init__(
        self, clock: services.Clock, ids: services.IdGenerator
    ) -> None:
        """Initializes the log.

        Args:
            clock: Source of record timestamps.
            ids: Generator of record ids.
        """
        self._clock = clock
        self._ids = ids

    def record(
        self,
        uow: repositories.UnitOfWork,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Mapping[str, object],
        account_id: str | None = None,
    ) -> audit.AuditEvent:
        """Appends one record to the tenant's chain.

        Args:
            uow: Active unit of work of the audited change; the record is
                written in the same transaction.
            tenant_id: Tenant whose chain the record extends.
            actor: Who acted, such as ``user:<id>`` or ``system:oms``.
            action: Dotted action name, such as ``order.submitted``.
            entity_type: Kind of the audited entity, such as ``order``.
            entity_id: Id of the audited entity.
            payload: JSON-compatible details; values under secret-like keys
                are redacted before hashing.
            account_id: Related account, if any.

        Returns:
            The appended record, chained to the tenant's previous hash.
        """
        now = self._clock.now_ms()
        event_id = self._ids.new_id()
        clean = _scrub(payload)
        prev = uow.audit.last_hash(tenant_id)
        digest = audit.chain_hash(
            prev,
            event_id,
            tenant_id,
            actor,
            action,
            entity_type,
            entity_id,
            now,
            clean,
        )
        event = audit.AuditEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            trace_id=tracing.current_trace_id(),
            ts_ms=now,
            payload=clean,
            prev_hash=prev,
            hash=digest,
            account_id=account_id,
        )
        uow.audit.append(event)
        return event

    @staticmethod  # noqa: GPY035 - public API: api/routers/risk.py calls AuditLog.verify on the class.
    def verify(uow: repositories.UnitOfWork, tenant_id: str) -> bool:
        """Verifies a tenant's whole chain.

        Args:
            uow: Unit of work to read the chain from.
            tenant_id: Tenant.

        Returns:
            True when every link and hash is intact (also for an empty
            chain), False otherwise.
        """
        return audit.verify_chain(uow.audit.chain(tenant_id))
