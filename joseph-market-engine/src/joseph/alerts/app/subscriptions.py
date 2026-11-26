# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tenant-scoped management of alert subscriptions."""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import re
import time
import urllib.parse
import uuid
import zoneinfo
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence

from cryptography.hazmat.primitives.asymmetric import ec

from joseph.alerts.app import ports
from joseph.alerts.domain import model
from joseph.domain import errors
from joseph.domain import identity

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A numeric chat id (negative for groups) or a public @username.
_TELEGRAM = re.compile(r"^-?[0-9]{1,20}$|^@[A-Za-z0-9_]{5,32}$")
# LINE ids: U (user), C (group chat) or R (room) and 32 hex digits.
_LINE = re.compile(r"^[UCR][0-9a-f]{32}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SEVERITIES = ("INFO", "WARNING", "ENTRY", "CRITICAL")
# UI and EXECUTION deliveries are implicit (routing adds them), so users can
# only subscribe to these.
_USER_CHANNELS = (
    "TELEGRAM",
    "WHATSAPP",
    "WEB_PUSH",
    "LINE",
    "EMAIL",
    "WEBHOOK",
)


def _validate_target(channel: str, target: str) -> None:
    """Checks that a target is plausible for its channel.

    Args:
        channel: Channel name, one of ``_USER_CHANNELS``.
        target: Delivery target.

    Raises:
        ValidationFailedError: If the target is malformed.
    """
    valid = {
        "TELEGRAM": bool(_TELEGRAM.match(target)),
        "WHATSAPP": bool(re.fullmatch(r"\+[1-9][0-9]{6,14}", target)),
        "WEB_PUSH": bool(re.fullmatch(r"device-[0-9a-f]{32}", target)),
        "LINE": bool(_LINE.match(target)),
        "EMAIL": bool(_EMAIL.match(target)),
        "WEBHOOK": target.startswith("https://"),
    }[channel]
    if not valid:
        hints = {
            "TELEGRAM": "a numeric chat id or @channel name",
            "WHATSAPP": "an international number such as +14155552671",
            "WEB_PUSH": "a registered device identifier",
            "LINE": "a LINE user/group id (U, C or R followed by 32 hex)",
            "EMAIL": "an e-mail address",
            "WEBHOOK": "an https:// URL",
        }
        raise errors.ValidationFailedError(
            f"{channel} target must be {hints[channel]}"
        )


def _push_registration(
    value: object, target: str
) -> model.WebPushRegistration | None:
    """Validates a private browser push registration without logging its keys.

    Args:
        value: Optional PushSubscription JSON with endpoint and keys.
        target: Public device identifier derived from the endpoint SHA-256.

    Returns:
        Validated registration, or None when omitted during an update.

    Raises:
        errors.ValidationFailedError: If the host, keys or id is invalid.
    """
    if value is None:
        return None
    try:
        if not isinstance(value, Mapping):
            raise errors.ValidationFailedError(
                "invalid Web Push registration or device identifier"
            )
        endpoint = str(value["endpoint"])
        url = urllib.parse.urlsplit(endpoint)
        host = url.hostname or ""
        allowed = host in {
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
            "web.push.apple.com",
        } or any(
            host.endswith(suffix)
            for suffix in (
                ".push.services.mozilla.com",
                ".push.apple.com",
                ".notify.windows.com",
            )
        )
        if (
            not allowed
            or url.scheme != "https"
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.fragment
            or len(endpoint) > 2048
        ):
            raise errors.ValidationFailedError(
                "invalid Web Push registration or device identifier"
            )
        keys = value["keys"]
        if not isinstance(keys, Mapping):
            raise errors.ValidationFailedError(
                "invalid Web Push registration or device identifier"
            )
        public = str(keys["p256dh"])
        auth = str(keys["auth"])
        public_bytes = base64.b64decode(
            public + "=" * (-len(public) % 4), altchars=b"-_", validate=True
        )
        auth_bytes = base64.b64decode(
            auth + "=" * (-len(auth) % 4), altchars=b"-_", validate=True
        )
        if (
            len(public_bytes) != 65
            or public_bytes[0] != 4
            or len(auth_bytes) != 16
        ):
            raise errors.ValidationFailedError(
                "invalid Web Push registration or device identifier"
            )
        if (
            target
            != "device-" + hashlib.sha256(endpoint.encode()).hexdigest()[:32]
        ):
            raise errors.ValidationFailedError(
                "invalid Web Push registration or device identifier"
            )
        ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), public_bytes
        )
        return model.WebPushRegistration(endpoint, public, auth)
    except (KeyError, ValueError, TypeError) as exc:
        raise errors.ValidationFailedError(
            "invalid Web Push registration or device identifier"
        ) from exc


def _strings(value: object) -> frozenset[str]:
    """Converts a JSON array of strings to a frozenset.

    Args:
        value: Candidate list.

    Returns:
        Strings (empty for non-lists).
    """
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(item) for item in value)


def _parse_time(text: str) -> datetime.time:
    """Parses an ISO time such as ``22:00`` (seconds are accepted too).

    Args:
        text: Time text.

    Returns:
        Time.

    Raises:
        ValidationFailedError: If malformed.
    """
    try:
        return datetime.time.fromisoformat(text)
    except ValueError as err:
        raise errors.ValidationFailedError(f"invalid time {text!r}") from err


def from_contract(
    tenant_id: str, subscription_id: str, body: Mapping[str, object]
) -> model.Subscription:
    """Builds a domain subscription from a contract body.

    Missing optional fields take their defaults (``min_severity`` INFO,
    ``enabled``, CRITICAL bypassing quiet hours); a ``quiet_hours`` value
    that is not an object means no quiet hours.

    Args:
        tenant_id: Owning tenant (from the authenticated principal).
        subscription_id: Path identifier: lower-case letters, digits and
            hyphens, 1 to 63 characters, not starting with a hyphen.
        body: Contract ``AlertSubscription``.

    Returns:
        Domain subscription.

    Raises:
        ValidationFailedError: If any field is invalid.
    """
    if not _ID.match(subscription_id):
        raise errors.ValidationFailedError("invalid subscription_id")
    if body.get("subscription_id", subscription_id) != subscription_id:
        raise errors.ValidationFailedError(
            "subscription_id in the body does not match the path"
        )
    channel = str(body.get("channel", ""))
    if channel not in _USER_CHANNELS:
        raise errors.ValidationFailedError(
            f"channel must be one of {', '.join(_USER_CHANNELS)}"
        )
    target = str(body.get("target", "")).strip()
    _validate_target(channel, target)
    severity = str(body.get("min_severity", "INFO"))
    if severity not in _SEVERITIES:
        raise errors.ValidationFailedError("invalid min_severity")
    quiet = body.get("quiet_hours")
    quiet_hours = None
    if isinstance(quiet, Mapping):
        tz = str(quiet.get("tz", "UTC"))
        try:
            zoneinfo.ZoneInfo(tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as err:
            raise errors.ValidationFailedError(
                f"unknown time zone {tz!r}"
            ) from err
        bypass = quiet.get("bypass_severities", ["CRITICAL"])
        quiet_hours = model.QuietHours(
            start=_parse_time(str(quiet.get("start", ""))),
            end=_parse_time(str(quiet.get("end", ""))),
            tz=tz,
            bypass_severities=_strings(bypass),
        )
    kinds = body.get("kinds", [])
    strategies = body.get("strategy_ids", [])
    return model.Subscription(
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        channel=model.Channel(channel),
        target=target,
        kinds=_strings(kinds),
        min_severity=severity,
        strategy_ids=_strings(strategies),
        enabled=bool(body.get("enabled", True)),
        quiet_hours=quiet_hours,
        web_push=_push_registration(body.get("web_push"), target)
        if channel == "WEB_PUSH"
        else None,
    )


def _quiet_contract(
    quiet: model.QuietHours | None,
) -> dict[str, object] | None:
    """Serializes quiet hours to the contract shape.

    Args:
        quiet: Quiet hours, or ``None``.

    Returns:
        The contract ``quiet_hours`` object (times as ``HH:MM``), or
        ``None`` without quiet hours.
    """
    if quiet is None:
        return None
    return {
        "start": quiet.start.strftime("%H:%M"),
        "end": quiet.end.strftime("%H:%M"),
        "tz": quiet.tz,
        "bypass_severities": sorted(quiet.bypass_severities),
    }


def to_contract(subscription: model.Subscription) -> dict[str, object]:
    """Serializes a subscription to the contract shape.

    Args:
        subscription: Domain subscription.

    Returns:
        Contract ``AlertSubscription`` (sets as sorted lists).
    """
    return {
        "subscription_id": subscription.subscription_id,
        "channel": subscription.channel.value,
        "target": subscription.target,
        "kinds": sorted(subscription.kinds),
        "min_severity": subscription.min_severity,
        "strategy_ids": sorted(subscription.strategy_ids),
        "enabled": subscription.enabled,
        "quiet_hours": _quiet_contract(subscription.quiet_hours),
    }


class SubscriptionService:
    """The alerts API's tenant-scoped view of subscriptions."""

    def __init__(
        self,
        repository: ports.SubscriptionRepository,
        configured_channels: Collection[str],
        *,
        outbox: ports.OutboxRepository | None = None,
        web_push_public_key: str | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            repository: Subscription store.
            configured_channels: Names of the channels the server has
                credentials for, reported to clients as
                ``configured_channels``.
            outbox: Optional durable queue for explicit test deliveries.
            web_push_public_key: Optional public VAPID application server key.
        """
        self._repository = repository
        self._configured = sorted(set(configured_channels))
        self._outbox = outbox
        self._push_key = web_push_public_key

    def list(self, principal: identity.Principal) -> dict[str, object]:
        """Returns the caller's subscriptions on user channels.

        Args:
            principal: Caller.

        Returns:
            Contract ``AlertSubscriptionList``; UI and EXECUTION
            subscriptions are left out.
        """
        rows: Sequence[model.Subscription] = self._repository.list_for_tenant(
            principal.tenant_id
        )
        return {
            "subscriptions": [
                to_contract(s)
                for s in rows
                if s.channel.value in _USER_CHANNELS
            ],
            "configured_channels": self._configured,
            "web_push_public_key": self._push_key,
        }

    def upsert(
        self,
        principal: identity.Principal,
        subscription_id: str,
        body: Mapping[str, object],
    ) -> dict[str, object]:
        """Creates or replaces one of the caller's subscriptions.

        Args:
            principal: Caller; the subscription belongs to its tenant.
            subscription_id: Identifier from the path.
            body: Contract ``AlertSubscription``.

        Returns:
            Stored contract ``AlertSubscription``.

        Raises:
            errors.ValidationFailedError: If the body or id is invalid.
        """
        subscription = from_contract(principal.tenant_id, subscription_id, body)
        if (
            subscription.channel is model.Channel.WEB_PUSH
            and subscription.web_push is None
        ):
            old = next(
                (
                    row
                    for row in self._repository.list_for_tenant(
                        principal.tenant_id
                    )
                    if row.subscription_id == subscription_id
                    and row.channel is model.Channel.WEB_PUSH
                    and row.target == subscription.target
                ),
                None,
            )
            if old is None or old.web_push is None:
                raise errors.ValidationFailedError(
                    "register this device before saving push notifications"
                )
            subscription = dataclasses.replace(
                subscription, web_push=old.web_push
            )
        self._repository.upsert(subscription)
        return to_contract(subscription)

    def delete(
        self, principal: identity.Principal, subscription_id: str
    ) -> None:
        """Removes an owned destination, preventing future alert routing.

        Args:
            principal: Authenticated caller and tenant.
            subscription_id: Public destination identifier.

        Returns:
            None after deleting the subscription.

        Raises:
            errors.NotFoundError: If the destination is absent or not owned.
        """
        if not self._repository.delete(principal.tenant_id, subscription_id):
            raise errors.NotFoundError("notification subscription not found")

    def test(
        self, principal: identity.Principal, subscription_id: str
    ) -> dict[str, object]:
        """Queues a test notification without producing a trading signal.

        Args:
            principal: Authenticated caller and tenant.
            subscription_id: Enabled, configured destination identifier.

        Returns:
            Public delivery status for polling provider acceptance.

        Raises:
            errors.NotFoundError: If no owned subscription exists.
            errors.UnsupportedError: If disabled or credentials are absent.
        """
        subscription = next(
            (
                row
                for row in self._repository.list_for_tenant(principal.tenant_id)
                if row.subscription_id == subscription_id
            ),
            None,
        )
        if subscription is None:
            raise errors.NotFoundError("notification subscription not found")
        if (
            not subscription.enabled
            or subscription.channel.value not in _USER_CHANNELS
            or subscription.channel.value not in self._configured
            or self._outbox is None
        ):
            raise errors.UnsupportedError(
                "enable this destination and configure server credentials "
                "before sending a test"
            )
        now = int(time.time() * 1000)
        delivery_id = "test-" + uuid.uuid4().hex
        self._outbox.enqueue(
            model.DeliveryRecord(
                delivery_id=delivery_id,
                idempotency_key=delivery_id,
                tenant_id=principal.tenant_id,
                channel=subscription.channel,
                target=subscription.target,
                payload={"kind": "NOTIFICATION_TEST"},
                created_at_ms=now,
                next_attempt_ms=now,
            )
        )
        return self.delivery(principal, delivery_id)

    def delivery(
        self, principal: identity.Principal, delivery_id: str
    ) -> dict[str, object]:
        """Returns tenant-scoped delivery metadata without device keys.

        Args:
            principal: Authenticated caller and tenant.
            delivery_id: Opaque delivery identifier returned by a test request.

        Returns:
            Identifier, channel, status, attempts and redacted last error.

        Raises:
            errors.NotFoundError: If absent or owned by another tenant.
        """
        row = self._outbox.get(delivery_id) if self._outbox else None
        if row is None or row.tenant_id != principal.tenant_id:
            raise errors.NotFoundError("notification delivery not found")
        return {
            "delivery_id": row.delivery_id,
            "channel": row.channel.value,
            "status": row.status.value,
            "attempts": row.attempts,
            "last_error": row.last_error,
        }
