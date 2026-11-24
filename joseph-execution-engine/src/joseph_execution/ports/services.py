# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Small infrastructure ports: clock, ids, events, metrics, security."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class Clock(Protocol):
    """A source of the current UTC time (injected so tests can fix it)."""

    def now_ms(self) -> int:
        """Returns the current time in UTC epoch milliseconds."""
        ...


class IdGenerator(Protocol):
    """A generator of unique identifiers."""

    def new_id(self) -> str:
        """Returns a new, unique UUIDv7 (time-ordered)."""
        ...


class EventPublisher(Protocol):
    """A publisher of tenant-scoped user-stream events.

    ``publish`` is synchronous and must not block: the OMS calls it on the
    order path, after committing the change the event reports.
    """

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
            tenant_id: Tenant whose stream receives it; no other tenant
                may see it.
            event_type: Stream type such as ``order.updated``.
            channel: Channel such as ``orders``.
            payload: Contract payload.
            account_id: Related account for subscribers' account filters.
        """
        ...


class Metrics(Protocol):
    """A sink for counters and gauges."""

    def increment(
        self, name: str, labels: Mapping[str, str] | None = None
    ) -> None:
        """Increments a counter.

        Args:
            name: Metric name.
            labels: Label values by label name, if any.
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
            value: New value; replaces the previous one.
            labels: Label values by label name, if any.
        """
        ...


class StepUpVerifier(Protocol):
    """A verifier of step-up (MFA, multi-factor) codes for sensitive actions.

    Arming LIVE trading and granting AUTO_LIVE consent depend on it, so it
    must fail closed.
    """

    def verify(self, user_id: str, code: str) -> bool:
        """Checks a step-up code.

        Args:
            user_id: Acting user.
            code: One-time code.

        Returns:
            True only for a valid code; False otherwise, including when
            verification is not configured.
        """
        ...


class CredentialVault(Protocol):
    """Encrypts and decrypts venue credentials (server side only).

    Plaintext never leaves the engine; only envelopes are persisted.
    """

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
            envelope: Stored envelope from ``seal``.

        Returns:
            Plaintext fields.

        Raises:
            ExecutionError: With ``FORBIDDEN`` when the envelope fails
                authentication (tampered, or sealed under another key).
        """
        ...
