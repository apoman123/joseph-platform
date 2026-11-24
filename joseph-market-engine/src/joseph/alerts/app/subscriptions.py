# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tenant-scoped management of alert subscriptions."""

from __future__ import annotations

import datetime
import re
import zoneinfo
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence

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
_USER_CHANNELS = ("TELEGRAM", "LINE", "EMAIL", "WEBHOOK")


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
        "LINE": bool(_LINE.match(target)),
        "EMAIL": bool(_EMAIL.match(target)),
        "WEBHOOK": target.startswith("https://"),
    }[channel]
    if not valid:
        hints = {
            "TELEGRAM": "a numeric chat id or @channel name",
            "LINE": "a LINE user/group id (U, C or R followed by 32 hex)",
            "EMAIL": "an e-mail address",
            "WEBHOOK": "an https:// URL",
        }
        raise errors.ValidationFailedError(
            f"{channel} target must be {hints[channel]}"
        )


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
    ) -> None:
        """Initializes the service.

        Args:
            repository: Subscription store.
            configured_channels: Names of the channels the server has
                credentials for, reported to clients as
                ``configured_channels``.
        """
        self._repository = repository
        self._configured = sorted(set(configured_channels))

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
        self._repository.upsert(subscription)
        return to_contract(subscription)
