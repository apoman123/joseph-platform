# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Alert routing, quiet hours, delivery records and message formatting.

Pure domain logic: deciding *who* receives a signal on *which* channel and
*when* to retry. Network delivery lives in ``alerts.adapters``.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import zoneinfo
from collections.abc import Iterable
from collections.abc import Mapping

from joseph.domain import ids
from joseph.signals.domain import signal as signal_domain


class Channel(enum.StrEnum):
    """A delivery channel (values are stored and sent on the wire)."""

    UI = "UI"
    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"
    WEB_PUSH = "WEB_PUSH"
    LINE = "LINE"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"
    EXECUTION = "EXECUTION"


PUSH_CHANNELS: frozenset[Channel] = frozenset(
    {
        Channel.TELEGRAM,
        Channel.WHATSAPP,
        Channel.WEB_PUSH,
        Channel.LINE,
        Channel.EMAIL,
    }
)
"""Channels silenced during quiet hours."""

FORWARDED_MODES: frozenset[str] = frozenset({"PAPER", "SHADOW", "AUTO_LIVE"})
"""Strategy modes whose entries are forwarded to the execution engine."""


class DeliveryStatus(enum.StrEnum):
    """The state of an outbox delivery."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    DEAD = "DEAD"


class DeliveryError(Exception):
    """A channel failed to deliver.

    Attributes:
        retryable: Whether retrying may succeed.
        retry_after_ms: Delay in ms the channel asked for (``Retry-After``),
            or ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after_ms: int | None = None,
    ) -> None:
        """Initializes the error.

        Args:
            message: Redacted explanation (never contains secrets).
            retryable: Whether retrying may succeed.
            retry_after_ms: Requested delay in ms before retrying, or
                ``None`` to use the backoff.
        """
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms


@dataclasses.dataclass(frozen=True, slots=True)
class QuietHours:
    """A daily window in which push notifications are held back.

    Attributes:
        start: Local start time, inclusive.
        end: Local end time, exclusive (may be earlier than ``start`` to
            span midnight; equal to ``start`` means never quiet).
        tz: IANA time zone, e.g. ``Asia/Taipei``.
        bypass_severities: Severities delivered even when quiet.
    """

    start: datetime.time
    end: datetime.time
    tz: str = "UTC"
    bypass_severities: frozenset[str] = frozenset({"CRITICAL"})

    def is_quiet(self, ts_ms: int) -> bool:
        """Checks whether a moment falls inside the window.

        Args:
            ts_ms: UTC epoch milliseconds.

        Returns:
            True during quiet hours.
        """
        local = datetime.datetime.fromtimestamp(
            ts_ms / 1000, tz=zoneinfo.ZoneInfo(self.tz)
        ).time()
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


@dataclasses.dataclass(frozen=True, slots=True)
class WebPushRegistration:
    """Private browser registration used for encrypted Web Push delivery.

    Attributes:
        endpoint: HTTPS capability URL of an approved browser push service.
        p256dh: Base64url uncompressed P-256 public key, 65 decoded bytes.
        auth: Base64url authentication secret, 16 decoded bytes.
    """

    endpoint: str = dataclasses.field(repr=False)
    p256dh: str = dataclasses.field(repr=False)
    auth: str = dataclasses.field(repr=False)

    def to_payload(self) -> dict[str, object]:
        """Builds the private SDK payload, never an API response.

        Returns:
            Subscription endpoint and encryption keys for the push SDK.
        """
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Subscription:
    """A user's request to receive signals on a channel.

    Attributes:
        subscription_id: Identifier.
        tenant_id: Owning tenant.
        channel: Delivery channel.
        target: Chat id, E.164 WhatsApp phone number, device identifier,
            LINE user id, e-mail address or webhook URL.
        kinds: Signal kinds to receive (empty = all).
        min_severity: Lowest severity delivered (``INFO``, ``WARNING``,
            ``ENTRY`` or ``CRITICAL``).
        strategy_ids: Strategies to receive (empty = all).
        enabled: Whether active.
        quiet_hours: Optional quiet-hours window (push channels only).
        web_push: Optional private browser registration, stored only on
            WEB_PUSH subscriptions and omitted from API responses.
    """

    subscription_id: str
    tenant_id: str
    channel: Channel
    target: str
    kinds: frozenset[str] = frozenset()
    min_severity: str = "INFO"
    strategy_ids: frozenset[str] = frozenset()
    enabled: bool = True
    quiet_hours: QuietHours | None = None
    web_push: WebPushRegistration | None = dataclasses.field(
        default=None, repr=False
    )

    def wants(self, signal: signal_domain.Signal) -> bool:
        """Checks the subscription filters.

        Args:
            signal: Signal.

        Returns:
            True when the subscription is enabled, belongs to the signal's
            tenant, its kind and strategy filters admit the signal and the
            signal's severity is at least ``min_severity``.
        """
        if not self.enabled or signal.tenant_id != self.tenant_id:
            return False
        if self.kinds and signal.kind not in self.kinds:
            return False
        if self.strategy_ids and signal.strategy_id not in self.strategy_ids:
            return False
        return signal_domain.severity_rank(signal.severity) >= (
            signal_domain.severity_rank(self.min_severity)
        )


def current_kinds(kinds: frozenset[str]) -> frozenset[str]:
    """Migrates a stored kinds filter to the current signal kinds.

    Kinds of the removed declarative strategies (and any other unknown
    kind) are dropped. A filter that listed only such kinds would turn
    into "every kind" by accident, so it becomes the explicit list of
    every current kind instead; it keeps receiving whatever strategies and
    alerts emit now.

    Args:
        kinds: Stored filter; empty means every kind.

    Returns:
        The filter restricted to current kinds; empty stays empty.
    """
    kept = kinds & signal_domain.SIGNAL_KINDS
    if kinds and not kept:
        return signal_domain.SIGNAL_KINDS
    return kept


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """One durable delivery attempt sequence in the outbox.

    Attributes:
        delivery_id: Identifier derived from the idempotency key.
        idempotency_key: ``signal_id:channel:target``.
        tenant_id: Owning tenant.
        channel: Channel.
        target: Recipient.
        payload: Contract ``Signal`` dict.
        created_at_ms: Enqueue time in UTC epoch ms.
        status: Delivery status.
        attempts: Attempts made.
        next_attempt_ms: Earliest next attempt in UTC epoch ms.
        last_error: Redacted last error, or ``None``.
        delivered_at_ms: Delivery time in UTC epoch ms, or ``None``.
    """

    delivery_id: str
    idempotency_key: str
    tenant_id: str
    channel: Channel
    target: str
    payload: Mapping[str, object]
    created_at_ms: int
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    next_attempt_ms: int = 0
    last_error: str | None = None
    delivered_at_ms: int | None = None


def idempotency_key(signal_id: str, channel: Channel, target: str) -> str:
    """Builds the delivery idempotency key.

    Args:
        signal_id: Signal id.
        channel: Channel.
        target: Recipient.

    Returns:
        ``signal_id:channel:target``.
    """
    return f"{signal_id}:{channel.value}:{target}"


def new_delivery(
    signal: signal_domain.Signal, channel: Channel, target: str, now_ms: int
) -> DeliveryRecord:
    """Creates a pending delivery for a signal.

    Args:
        signal: Signal.
        channel: Channel.
        target: Recipient.
        now_ms: Enqueue time in UTC epoch ms; the record is due at once.

    Returns:
        The record.
    """
    key = idempotency_key(signal.signal_id, channel, target)
    return DeliveryRecord(
        delivery_id=ids.stable_id("dlv", key),
        idempotency_key=key,
        tenant_id=signal.tenant_id,
        channel=channel,
        target=target,
        payload=signal.to_contract(),
        created_at_ms=now_ms,
        next_attempt_ms=now_ms,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RouteResult:
    """The routing decision for one signal.

    Attributes:
        deliveries: Deliveries to enqueue.
        suppressed: ``(subscription_id, reason)`` pairs held back.
    """

    deliveries: tuple[DeliveryRecord, ...]
    suppressed: tuple[tuple[str, str], ...]


def route(
    signal: signal_domain.Signal,
    subscriptions: Iterable[Subscription],
    strategy_channels: frozenset[str],
    now_ms: int,
    *,
    ui_target: str | None = None,
    forward_execution: bool = False,
) -> RouteResult:
    """Decides deliveries for a newly recorded signal.

    UI and execution deliveries are implicit: UI and EXECUTION
    subscriptions are ignored. A subscription that matches the signal is
    suppressed when the strategy does not allow its channel, or during its
    quiet hours on a push channel unless the severity bypasses them.
    Subscriptions are visited in id order so the result is deterministic.

    Args:
        signal: Signal.
        subscriptions: Candidate subscriptions.
        strategy_channels: Channel names the strategy allows.
        now_ms: Current time in UTC epoch ms (quiet hours).
        ui_target: Target for the implicit UI delivery; ``None`` disables
            it.
        forward_execution: Whether ``ENTRY_CONFIRMED`` signals of
            PAPER/SHADOW/AUTO_LIVE strategies go to the execution engine.

    Returns:
        Deliveries and suppressions.
    """
    deliveries: list[DeliveryRecord] = []
    suppressed: list[tuple[str, str]] = []
    if ui_target is not None and Channel.UI.value in strategy_channels:
        deliveries.append(new_delivery(signal, Channel.UI, ui_target, now_ms))
    for sub in sorted(subscriptions, key=lambda s: s.subscription_id):
        if sub.channel in (Channel.UI, Channel.EXECUTION):
            continue
        if not sub.wants(signal):
            continue
        if sub.channel.value not in strategy_channels:
            suppressed.append(
                (sub.subscription_id, "channel not enabled for this strategy")
            )
            continue
        quiet = sub.quiet_hours
        if (
            sub.channel in PUSH_CHANNELS
            and quiet is not None
            and quiet.is_quiet(now_ms)
            and signal.severity not in quiet.bypass_severities
        ):
            suppressed.append((sub.subscription_id, "quiet hours"))
            continue
        deliveries.append(new_delivery(signal, sub.channel, sub.target, now_ms))
    if (
        forward_execution
        and signal.kind == "ENTRY_CONFIRMED"
        and signal.mode in FORWARDED_MODES
    ):
        deliveries.append(
            new_delivery(signal, Channel.EXECUTION, "execution-engine", now_ms)
        )
    return RouteResult(
        deliveries=tuple(deliveries), suppressed=tuple(suppressed)
    )


def backoff_ms(
    attempts: int, base_ms: int = 2_000, cap_ms: int = 300_000
) -> int:
    """Computes the exponential backoff before a delivery's next attempt.

    Args:
        attempts: Attempts already made (>= 1).
        base_ms: Delay in ms after the first failure; it doubles per
            further attempt.
        cap_ms: Maximum delay in ms.

    Returns:
        Delay in ms before the next attempt.
    """
    exponent: int = max(0, attempts - 1)
    delay: int = base_ms * (2**exponent)
    return min(cap_ms, delay)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Removes secrets from a message.

    Args:
        text: Message that may contain secrets (URLs, headers).
        secrets: Secret values.

    Returns:
        Message with every secret replaced by ``***``.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


# Message titles by signal kind; other kinds print their raw name.
_TITLES = {
    "ENTRY_CONFIRMED": "Entry confirmed",
    "POSITION_EXITED": "Position exited",
}


def format_text(payload: Mapping[str, object]) -> str:
    """Formats a signal for chat and e-mail channels.

    Includes the exact venue instrument, strategy version and levels so the
    message can be acted on and traced.

    Args:
        payload: Contract ``Signal`` dict.

    Returns:
        Plain text, cut to 4,000 characters (Telegram allows 4,096).
    """
    kind = str(payload.get("kind", ""))
    if kind == "NOTIFICATION_TEST":
        return (
            "Joseph test alert\nYour notification connection is working. "
            "No trade was placed."
        )
    if kind == "CUSTOM_ALERT":
        return (
            f"Python alert: {payload.get('instrument_id')} "
            f"{payload.get('timeframe')}\n"
            f"{payload.get('message', '')}\n"
            f"Script {payload.get('strategy_version_id')} | "
            f"Data {payload.get('data_source')} | "
            f"Bar close {payload.get('bar_close_ms')}"
        )[:4000]
    close_ms = payload.get("bar_close_ms")
    when = ""
    if isinstance(close_ms, int):
        when = datetime.datetime.fromtimestamp(
            close_ms / 1000, tz=datetime.UTC
        ).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"{_TITLES.get(kind, kind)}: {payload.get('direction')} "
        f"{payload.get('instrument_id')} {payload.get('timeframe')}",
        f"Strategy {payload.get('strategy_version_id')} "
        f"({payload.get('mode')})",
    ]
    if kind == "ENTRY_CONFIRMED":
        lines.append(
            f"Entry {payload.get('entry_price')}  "
            f"Stop {payload.get('stop_price')}  "
            f"Target {payload.get('target_price')}  R:R {payload.get('rr')}"
        )
    message = payload.get("message")
    if message:
        lines.append(str(message))
    lines.append(f"Bar close {when}  Data {payload.get('data_source')}")
    return "\n".join(lines)[:4000]
