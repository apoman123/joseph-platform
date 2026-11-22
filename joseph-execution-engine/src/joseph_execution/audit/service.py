"""Audit log service: appends hash-chained records inside transactions."""

from __future__ import annotations

from collections.abc import Mapping

from joseph_execution.domain import audit
from joseph_execution.domain import tracing
from joseph_execution.ports import repositories
from joseph_execution.ports import services

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
    """Removes credential-like keys from an audit payload.

    Args:
        payload: Raw payload.

    Returns:
        Payload with secret-like values replaced by ``"[REDACTED]"``.
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
    """Writes audit records atomically with the audited change."""

    def __init__(
        self, clock: services.Clock, ids: services.IdGenerator
    ) -> None:
        """Initializes the log.

        Args:
            clock: Time source.
            ids: Id generator.
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
        """Appends one record to the tenant chain.

        Args:
            uow: Active unit of work (same transaction as the change).
            tenant_id: Tenant.
            actor: Actor label.
            action: Action name.
            entity_type: Entity type.
            entity_id: Entity id.
            payload: JSON payload; secret-like keys are redacted.
            account_id: Related account.

        Returns:
            The appended record.
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

    @staticmethod
    def verify(uow: repositories.UnitOfWork, tenant_id: str) -> bool:
        """Verifies a tenant's whole chain.

        Args:
            uow: Unit of work.
            tenant_id: Tenant.

        Returns:
            True when intact.
        """
        return audit.verify_chain(uow.audit.chain(tenant_id))
