# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for alert routing, quiet hours, backoff and formatting."""

from __future__ import annotations

import dataclasses
import datetime
import decimal

import pytest

from joseph.alerts.domain import model
from joseph.signals.domain import signal as signal_domain

C = model.Channel


def _signal(
    kind: str = "ENTRY_CONFIRMED",
    mode: str = "PAPER",
    tenant: str = "t1",
    strategy_id: str = "ema-trend-long",
) -> signal_domain.Signal:
    """Builds a minimal LONG signal for the BTCUSDT perpetual.

    Args:
        kind: Signal kind; a key of ``SEVERITY_BY_KIND``.
        mode: Strategy mode, such as ``PAPER`` or ``ALERT_ONLY``.
        tenant: Owning tenant id.
        strategy_id: Strategy slug; the version id is ``{strategy_id}:v1``.

    Returns:
        A live-data signal with entry, stop and target.
    """
    key = f"{tenant}|{strategy_id}:v1|binance:perp:BTCUSDT|LONG|{kind}"
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(key),
        dedupe_key=key,
        tenant_id=tenant,
        strategy_id=strategy_id,
        strategy_version_id=f"{strategy_id}:v1",
        source_hash="sha256:y",
        instrument_id="binance:perp:BTCUSDT",
        direction="LONG",
        timeframe="5m",
        kind=kind,
        severity=signal_domain.SEVERITY_BY_KIND[kind],
        mode=mode,
        bar_start_ms=1_795_132_800_000,
        bar_close_ms=1_795_133_100_000,
        evaluated_at_ms=1_795_133_100_100,
        data_source="LIVE",
        entry_price=decimal.Decimal("94790"),
        stop_price=decimal.Decimal("94430"),
        target_price=decimal.Decimal("95870"),
        rr=decimal.Decimal("3"),
        message="EMA trend LONG entry",
    )


def _sub(channel: model.Channel, **kwargs: object) -> model.Subscription:
    """Builds an enabled tenant ``t1`` subscription with no filters.

    Args:
        channel: Delivery channel; it also names the id and target.
        **kwargs: ``Subscription`` field overrides.

    Returns:
        The subscription.
    """
    fields: dict[str, object] = {
        "subscription_id": f"sub-{channel.value}",
        "tenant_id": "t1",
        "channel": channel,
        "target": f"target-{channel.value}",
    }
    fields.update(kwargs)
    return model.Subscription(**fields)  # type: ignore[arg-type]  # overrides are typed object; each test passes the field's real type


_NOW = 1_795_133_100_200
_ALL = frozenset({"UI", "TELEGRAM", "LINE", "EMAIL", "WEBHOOK"})


@pytest.mark.parametrize("channel", [C.WHATSAPP, C.WEB_PUSH])
def test_mobile_channels_preserve_policy_filters_and_quiet_hours(
    channel: model.Channel,
) -> None:
    """Applies existing policy, filters and quiet hours to both mobile channels.

    Args:
        channel: Newly supported WhatsApp or standard Web Push channel.

    Returns:
        None after checking opt-in routing and critical severity bypass.
    """
    sub = _sub(channel)
    assert not model.route(_signal(), [sub], frozenset({"UI"}), _NOW).deliveries
    allowed = frozenset({channel.value})
    assert len(model.route(_signal(), [sub], allowed, _NOW).deliveries) == 1
    filtered = dataclasses.replace(sub, strategy_ids=frozenset({"other"}))
    assert not model.route(_signal(), [filtered], allowed, _NOW).deliveries
    quiet = model.QuietHours(datetime.time(0), datetime.time(23, 59), "UTC")
    held = dataclasses.replace(sub, quiet_hours=quiet)
    assert not model.route(_signal(), [held], allowed, _NOW).deliveries
    critical = dataclasses.replace(_signal(), severity="CRITICAL")
    assert len(model.route(critical, [held], allowed, _NOW).deliveries) == 1


def test_routes_matching_subscriptions_and_ui() -> None:
    """Matching subscriptions and the UI receive the signal, UI first."""
    result = model.route(
        _signal(),
        [_sub(C.TELEGRAM), _sub(C.LINE)],
        _ALL,
        _NOW,
        ui_target="tenant:t1",
    )
    channels = [d.channel for d in result.deliveries]
    assert channels == [C.UI, C.LINE, C.TELEGRAM]
    assert all(
        d.payload["signal_id"] == _signal().signal_id for d in result.deliveries
    )


def test_strategy_channel_policy_is_enforced() -> None:
    """Channels not enabled by the strategy are suppressed."""
    result = model.route(_signal(), [_sub(C.TELEGRAM)], frozenset({"UI"}), _NOW)
    assert result.deliveries == ()
    assert result.suppressed[0][1] == "channel not enabled for this strategy"


@pytest.mark.parametrize(
    ("sub_kwargs", "delivered"),
    [
        ({"kinds": frozenset({"POSITION_EXITED"})}, False),
        ({"min_severity": "CRITICAL"}, False),
        ({"strategy_ids": frozenset({"other"})}, False),
        ({"enabled": False}, False),
        ({"tenant_id": "t2"}, False),
        (
            {"kinds": frozenset({"ENTRY_CONFIRMED"}), "min_severity": "ENTRY"},
            True,
        ),
    ],
)
def test_subscription_filters(
    sub_kwargs: dict[str, object], delivered: bool
) -> None:
    """Kind, severity, strategy, enabled and tenant filters decide delivery."""
    result = model.route(_signal(), [_sub(C.EMAIL, **sub_kwargs)], _ALL, _NOW)
    assert bool(result.deliveries) is delivered


def test_quiet_hours_spanning_midnight_in_local_time() -> None:
    """Quiet hours use the local zone, wrap past midnight, spare webhooks."""
    quiet = model.QuietHours(
        start=datetime.time(22, 0), end=datetime.time(7, 0), tz="Asia/Taipei"
    )
    # 2026-11-20 16:00 UTC == 2026-11-21 00:00 Asia/Taipei (quiet).
    midnight_taipei = int(
        datetime.datetime(2026, 11, 20, 16, 0, tzinfo=datetime.UTC).timestamp()
        * 1000
    )
    noon_taipei = midnight_taipei + 12 * 3_600_000
    assert quiet.is_quiet(midnight_taipei)
    assert not quiet.is_quiet(noon_taipei)
    sub = _sub(C.TELEGRAM, quiet_hours=quiet)
    held = model.route(_signal(), [sub], _ALL, midnight_taipei)
    assert held.deliveries == () and held.suppressed[0][1] == "quiet hours"
    sent = model.route(_signal(), [sub], _ALL, noon_taipei)
    assert len(sent.deliveries) == 1
    webhook = model.route(
        _signal(),
        [_sub(C.WEBHOOK, quiet_hours=quiet)],
        _ALL,
        midnight_taipei,
    )
    assert len(webhook.deliveries) == 1


def _forwarded(sig: signal_domain.Signal) -> bool:
    """Checks whether routing forwards a signal to execution.

    Args:
        sig: Signal to route with execution forwarding on.

    Returns:
        True when routing creates an EXECUTION delivery.
    """
    result = model.route(sig, [], _ALL, _NOW, forward_execution=True)
    return any(d.channel is C.EXECUTION for d in result.deliveries)


def test_execution_forwarding_only_for_tradable_entries() -> None:
    """Only ENTRY_CONFIRMED signals of tradable modes go to execution."""
    assert _forwarded(_signal(mode="PAPER"))
    assert _forwarded(_signal(mode="SHADOW"))
    assert not _forwarded(_signal(mode="ALERT_ONLY"))
    assert not _forwarded(_signal(kind="POSITION_EXITED"))


def test_idempotency_key_and_delivery_id_are_stable() -> None:
    """Re-routing the same signal yields the same delivery ids."""
    first = model.route(_signal(), [_sub(C.LINE)], _ALL, _NOW)
    second = model.route(_signal(), [_sub(C.LINE)], _ALL, _NOW + 5)
    assert [d.delivery_id for d in first.deliveries] == [
        d.delivery_id for d in second.deliveries
    ]
    assert first.deliveries[0].idempotency_key.endswith(":LINE:target-LINE")


def test_backoff_and_redaction() -> None:
    """Backoff doubles up to a cap; secrets are removed from text."""
    assert [model.backoff_ms(n, 1000, 5000) for n in (1, 2, 3, 4)] == [
        1000,
        2000,
        4000,
        5000,
    ]
    assert model.redact("url /botSECRET/send", ["SECRET", ""]) == (
        "url /bot***/send"
    )


def test_format_text_carries_trace_fields() -> None:
    """Message text names the instrument, version, levels and UTC time."""
    text = model.format_text(_signal().to_contract())
    assert text.startswith("Entry confirmed: LONG binance:perp:BTCUSDT 5m")
    assert "ema-trend-long:v1" in text
    assert "Stop 94430" in text and "R:R 3" in text
    assert "EMA trend LONG entry" in text
    assert "UTC" in text
    exit_text = model.format_text(_signal(kind="POSITION_EXITED").to_contract())
    assert exit_text.startswith("Position exited: LONG")
    assert "Stop" not in exit_text


@pytest.mark.parametrize(
    ("stored", "migrated"),
    [
        (frozenset(), frozenset()),
        (frozenset({"ENTRY_CONFIRMED"}), frozenset({"ENTRY_CONFIRMED"})),
        (
            frozenset({"ENTRY_CONFIRMED", "ENTRY_BLOCKED", "PRE_SIGNAL"}),
            frozenset({"ENTRY_CONFIRMED"}),
        ),
        (
            frozenset({"WATCHING_AOI", "SETUP_EXPIRED"}),
            signal_domain.SIGNAL_KINDS,
        ),
    ],
)
def test_current_kinds_migrates_retired_kinds(
    stored: frozenset[str], migrated: frozenset[str]
) -> None:
    """Retired kinds are dropped; a filter of only retired kinds keeps all.

    Args:
        stored: Kinds filter stored before the migration.
        migrated: Expected filter afterwards.
    """
    assert model.current_kinds(stored) == migrated
