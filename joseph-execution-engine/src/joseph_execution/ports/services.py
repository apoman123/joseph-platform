# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Small infrastructure ports: clock, ids, events, metrics, security."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class Clock(Protocol):
    """Current UTC time source."""

    def now_ms(self) -> int:
        """Returns UTC epoch milliseconds."""
        ...


class IdGenerator(Protocol):
    """Generates unique identifiers."""

    def new_id(self) -> str:
        """Returns a new UUIDv7."""
        ...


class EventPublisher(Protocol):
    """Publishes tenant-scoped user-stream events."""

    def publish(
        self,
        tenant_id: str,
        event_type: str,
        channel: str,
        payload: Mapping[str, object],
        account_id: str | None = None,
    ) -> None:
        """Publishes an event.

        Args:
            tenant_id: Tenant whose stream receives it.
            event_type: Stream type such as ``order.updated``.
            channel: Channel such as ``orders``.
            payload: Contract payload.
            account_id: Related account for channel filters.
        """
        ...


class Metrics(Protocol):
    """Counter and gauge sink."""

    def increment(
        self, name: str, labels: Mapping[str, str] | None = None
    ) -> None:
        """Increments a counter.

        Args:
            name: Metric name.
            labels: Label values.
        """
        ...

    def gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Sets a gauge.

        Args:
            name: Metric name.
            value: Value.
            labels: Label values.
        """
        ...


class StepUpVerifier(Protocol):
    """Verifies a step-up (MFA) code for sensitive actions."""

    def verify(self, user_id: str, code: str) -> bool:
        """Checks a step-up code.

        Args:
            user_id: Acting user.
            code: One-time code.

        Returns:
            True when valid.
        """
        ...


class CredentialVault(Protocol):
    """Encrypts and decrypts venue credentials (server side only)."""

    def seal(self, secret: Mapping[str, str]) -> Mapping[str, str]:
        """Encrypts a credential payload.

        Args:
            secret: Plaintext fields.

        Returns:
            Envelope fields safe to persist.
        """
        ...

    def open(self, envelope: Mapping[str, str]) -> dict[str, str]:
        """Decrypts an envelope.

        Args:
            envelope: Stored envelope.

        Returns:
            Plaintext fields.
        """
        ...
