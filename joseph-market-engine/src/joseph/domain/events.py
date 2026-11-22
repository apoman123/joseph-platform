"""Versioned domain-event envelope for cross-context integration.

Every published event carries a schema version and an idempotency key so
consumers can deduplicate redeliveries from the at-least-once outbox.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from joseph.domain import ids


@dataclasses.dataclass(frozen=True, slots=True)
class DomainEvent:
    """Immutable integration event.

    Attributes:
        event_type: Dotted type such as ``signal.created``.
        payload: JSON-compatible body (contract object).
        idempotency_key: Stable key identifying the logical occurrence.
        ts_ms: Event time in UTC epoch milliseconds.
        schema_version: Payload schema version.
        event_id: Unique delivery-independent identifier (UUIDv7).
        correlation_id: Optional id linking related events and requests.
    """

    event_type: str
    payload: Mapping[str, object]
    idempotency_key: str
    ts_ms: int
    schema_version: int = 1
    event_id: str = dataclasses.field(default_factory=ids.uuid7)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        """Validates mandatory envelope fields.

        Raises:
            ValueError: If the type, idempotency key or version is missing.
        """
        if not self.event_type:
            raise ValueError("event_type is required")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
