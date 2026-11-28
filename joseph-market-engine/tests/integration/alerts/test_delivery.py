# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for alert delivery over mocked HTTP/SMTP and outboxes."""

from __future__ import annotations

import asyncio
import datetime
import email.message
import hashlib
import hmac
import json
import logging
import typing
import uuid
from collections.abc import Mapping

import httpx
import pytest
import respx
import sqlalchemy as sa

from joseph.alerts.adapters import channels
from joseph.alerts.adapters import memory
from joseph.alerts.adapters import sql
from joseph.alerts.app import dispatcher
from joseph.alerts.app import ports
from joseph.alerts.domain import model
from joseph.domain import clock
from joseph.signals.domain import signal as signal_domain

C = model.Channel
BOT_TOKEN = "123456:SECRET-BOT-TOKEN"
LINE_TOKEN = "line-SECRET-access-token"
SERVICE_TOKEN = "svc-SECRET-token"
HOOK_SECRET = "hook-SECRET"


def _signal(mode: str = "PAPER") -> signal_domain.Signal:
    """Builds a tenant-a ENTRY_CONFIRMED signal for the BTCUSDT perpetual.

    Args:
        mode: Strategy mode, such as ``PAPER`` or ``ALERT_ONLY``.

    Returns:
        The signal; equal inputs give the same ``signal_id``.
    """
    key = "tenant-a|trend:v1|binance:perp:BTCUSDT|LONG|ENTRY_CONFIRMED"
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(key),
        dedupe_key=key,
        tenant_id="tenant-a",
        strategy_id="trend",
        strategy_version_id="trend:v1",
        source_hash="sha256:b",
        instrument_id="binance:perp:BTCUSDT",
        direction="LONG",
        timeframe="5m",
        kind="ENTRY_CONFIRMED",
        severity="ENTRY",
        mode=mode,
        bar_start_ms=1_795_132_800_000,
        bar_close_ms=1_795_133_100_000,
        evaluated_at_ms=1_795_133_100_100,
        data_source="LIVE",
    )


@pytest.fixture(params=["memory", "sql"])
def outbox(
    request: pytest.FixtureRequest, engine: sa.Engine
) -> ports.OutboxRepository:
    """Provides each outbox implementation.

    Args:
        request: Parametrization: ``memory`` or ``sql``.
        engine: SQLite engine; the SQL outbox creates its schema there.

    Returns:
        An empty outbox.
    """
    if request.param == "memory":
        return memory.InMemoryOutbox()
    sql.create_schema(engine)
    return sql.SqlOutbox(engine)


def _subscriptions() -> memory.InMemorySubscriptionRepository:
    """Builds Telegram, LINE, webhook and e-mail subscriptions.

    Returns:
        A repository holding one tenant-a subscription per channel.
    """
    repo = memory.InMemorySubscriptionRepository()
    for channel, target in (
        (C.TELEGRAM, "chat-42"),
        (C.LINE, "U123"),
        (C.WEBHOOK, "https://hooks.example.com/j"),
        (C.EMAIL, "trader@example.com"),
    ):
        repo.upsert(
            model.Subscription(
                subscription_id=f"sub-{channel.value}",
                tenant_id="tenant-a",
                channel=channel,
                target=target,
            )
        )
    return repo


def _all_channels(tenant_id: str, version_id: str) -> frozenset[str]:
    """Allows every channel.

    Args:
        tenant_id: Tenant (unused).
        version_id: Version (unused).

    Returns:
        The names of every channel except EXECUTION.
    """
    del tenant_id, version_id
    return frozenset({"UI", "TELEGRAM", "LINE", "EMAIL", "WEBHOOK"})


class _FakeSmtp:
    """SMTP stand-in recording calls.

    The records live on the class because ``EmailSender`` builds a new
    instance per message; tests reset them before use.

    Attributes:
        sent: Messages sent.
        calls: Method calls in order.
    """

    sent: typing.ClassVar[list[email.message.EmailMessage]] = []
    calls: typing.ClassVar[list[str]] = []

    def __enter__(self) -> _FakeSmtp:
        """Enters the context.

        Returns:
            This instance.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Exits the context.

        Args:
            *args: Exception info (ignored).
        """

    def starttls(self) -> None:
        """Records STARTTLS."""
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        """Records LOGIN.

        Args:
            user: User name, recorded.
            password: Password, never recorded.
        """
        self.calls.append(f"login:{user}")
        del password

    def send_message(self, message: email.message.EmailMessage) -> None:
        """Records a message.

        Args:
            message: E-mail message.
        """
        self.sent.append(message)


async def test_end_to_end_delivery_on_every_channel(
    outbox: ports.OutboxRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """A twice-published signal reaches each of six channels once, correctly."""
    ui_events: list[tuple[str, Mapping[str, object]]] = []

    async def ui_sink(tenant_id: str, payload: Mapping[str, object]) -> None:
        """Records UI pushes in ``ui_events``.

        Args:
            tenant_id: Tenant pushed to.
            payload: Signal payload.
        """
        ui_events.append((tenant_id, payload))

    _FakeSmtp.sent, _FakeSmtp.calls = [], []
    route = dispatcher.AlertDispatcher(
        outbox,
        _subscriptions(),
        manual_clock,
        strategy_channels=_all_channels,
        forward_execution=True,
    )
    await route.publish(_signal())
    await route.publish(_signal())
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=True) as mock:
            telegram = mock.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            ).mock(return_value=httpx.Response(200, json={"ok": True}))
            line = mock.post("https://api.line.me/v2/bot/message/push").mock(
                return_value=httpx.Response(409, json={})
            )
            hook = mock.post("https://hooks.example.com/j").mock(
                return_value=httpx.Response(204)
            )
            forward = mock.post("http://exec.local/internal/v1/signals").mock(
                return_value=httpx.Response(202)
            )
            worker = dispatcher.OutboxWorker(
                outbox,
                {
                    C.UI: channels.UiSender(ui_sink),
                    C.TELEGRAM: channels.TelegramSender(client, BOT_TOKEN),
                    C.LINE: channels.LineSender(client, LINE_TOKEN),
                    C.WEBHOOK: channels.WebhookSender(
                        client, HOOK_SECRET, now_ms=lambda: 1234
                    ),
                    C.EXECUTION: channels.ExecutionForwarder(
                        client, "http://exec.local/", SERVICE_TOKEN
                    ),
                    C.EMAIL: channels.EmailSender(
                        _FakeSmtp,  # type: ignore[arg-type]  # fake has only the SMTP calls EmailSender makes
                        "joseph@example.com",
                        username="u",
                        password="pw-SECRET",
                    ),
                },
                manual_clock,
            )
            stats = await worker.run_once()
            assert stats == dispatcher.DeliveryStats(delivered=6)
            assert (await worker.run_once()).delivered == 0

    body = json.loads(telegram.calls[0].request.content)
    assert body["chat_id"] == "chat-42"
    assert "binance:perp:BTCUSDT" in body["text"]
    line_request = line.calls[0].request
    assert line_request.headers["Authorization"] == f"Bearer {LINE_TOKEN}"
    retry_key = line_request.headers["X-Line-Retry-Key"]
    assert str(uuid.UUID(retry_key)) == retry_key
    hook_request = hook.calls[0].request
    expected = hmac.new(
        HOOK_SECRET.encode(), b"1234." + hook_request.content, hashlib.sha256
    ).hexdigest()
    assert hook_request.headers["X-Joseph-Signature"] == f"sha256={expected}"
    assert hook_request.headers["X-Joseph-Timestamp"] == "1234"
    forward_request = forward.calls[0].request
    assert forward_request.headers["X-Joseph-Service-Token"] == SERVICE_TOKEN
    assert forward_request.headers["Idempotency-Key"] == _signal().signal_id
    assert json.loads(forward_request.content)["kind"] == "ENTRY_CONFIRMED"
    assert ui_events[0][0] == "tenant-a"
    assert _FakeSmtp.calls == ["starttls", "login:u"]
    assert _FakeSmtp.sent[0]["To"] == "trader@example.com"


async def test_retries_backoff_and_dead_letter(
    outbox: ports.OutboxRepository,
    manual_clock: clock.ManualClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retryable failures back off; persistent ones dead-letter."""
    record = model.new_delivery(
        _signal(), C.TELEGRAM, "chat-1", manual_clock.now_ms()
    )
    assert outbox.enqueue(record)
    assert not outbox.enqueue(record)
    caplog.set_level(logging.WARNING)
    async with httpx.AsyncClient() as client:
        with respx.mock() as mock:
            mock.post(url__regex=r".*/sendMessage").mock(
                side_effect=httpx.ConnectError(
                    f"cannot reach https://api.telegram.org/bot{BOT_TOKEN}"
                )
            )
            worker = dispatcher.OutboxWorker(
                outbox,
                {C.TELEGRAM: channels.TelegramSender(client, BOT_TOKEN)},
                manual_clock,
                max_attempts=3,
                base_backoff_ms=1000,
            )
            assert (await worker.run_once()).retried == 1
            stored = outbox.get(record.delivery_id)
            assert stored is not None and stored.attempts == 1
            assert stored.next_attempt_ms == manual_clock.now_ms() + 1000
            assert BOT_TOKEN not in (stored.last_error or "")
            assert (await worker.run_once()).retried == 0
            manual_clock.advance(1000)
            assert (await worker.run_once()).retried == 1
            manual_clock.advance(2000)
            assert (await worker.run_once()).dead == 1
    dead = outbox.dead_letters(10)
    assert [d.delivery_id for d in dead] == [record.delivery_id]
    assert BOT_TOKEN not in caplog.text
    assert "***" in caplog.text


@pytest.mark.parametrize(
    "body",
    ["<html>bad gateway</html>", "", '{"ok": false}', "[true]"],
    ids=["html", "empty", "not-ok", "json-list"],
)
async def test_telegram_reply_without_ok_json_is_a_delivery_error(
    body: str, manual_clock: clock.ManualClock
) -> None:
    """A 200 that is not JSON fails like a reply without ``"ok": true``."""
    record = model.new_delivery(
        _signal(), C.TELEGRAM, "chat-1", manual_clock.now_ms()
    )
    async with httpx.AsyncClient() as client:
        with respx.mock() as mock:
            mock.post(url__regex=r".*/sendMessage").mock(
                return_value=httpx.Response(200, text=body)
            )
            sender = channels.TelegramSender(client, BOT_TOKEN)
            with pytest.raises(model.DeliveryError) as raised:
                await sender.send(record)
    assert not raised.value.retryable
    assert BOT_TOKEN not in str(raised.value)


async def test_rate_limit_honors_retry_after(
    outbox: ports.OutboxRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """HTTP 429 schedules the retry after Retry-After."""
    record = model.new_delivery(_signal(), C.LINE, "U1", manual_clock.now_ms())
    outbox.enqueue(record)
    async with httpx.AsyncClient() as client:
        with respx.mock() as mock:
            mock.post("https://api.line.me/v2/bot/message/push").mock(
                return_value=httpx.Response(
                    429, headers={"Retry-After": "30"}, text="slow down"
                )
            )
            worker = dispatcher.OutboxWorker(
                outbox,
                {C.LINE: channels.LineSender(client, LINE_TOKEN)},
                manual_clock,
            )
            await worker.run_once()
    stored = outbox.get(record.delivery_id)
    assert stored is not None
    assert stored.status is model.DeliveryStatus.PENDING
    assert stored.next_attempt_ms == manual_clock.now_ms() + 30_000


async def test_client_errors_dead_letter_immediately(
    outbox: ports.OutboxRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """Client errors, unsafe URLs and missing senders dead-letter at once."""
    bad = model.new_delivery(_signal(), C.EXECUTION, "x", manual_clock.now_ms())
    insecure = model.new_delivery(
        _signal(),
        C.WEBHOOK,
        "http://169.254.169.254/latest",
        manual_clock.now_ms(),
    )
    unconfigured = model.new_delivery(
        _signal(), C.EMAIL, "a@b.c", manual_clock.now_ms()
    )
    for record in (bad, insecure, unconfigured):
        outbox.enqueue(record)
    async with httpx.AsyncClient() as client:
        with respx.mock() as mock:
            mock.post("http://exec.local/internal/v1/signals").mock(
                return_value=httpx.Response(401, json={"code": "nope"})
            )
            worker = dispatcher.OutboxWorker(
                outbox,
                {
                    C.EXECUTION: channels.ExecutionForwarder(
                        client, "http://exec.local", SERVICE_TOKEN
                    ),
                    C.WEBHOOK: channels.WebhookSender(client, HOOK_SECRET),
                },
                manual_clock,
            )
            stats = await worker.run_once()
    assert stats.dead == 3
    dead = {d.delivery_id: d for d in outbox.dead_letters(10)}
    assert "https" in (dead[insecure.delivery_id].last_error or "")
    assert "no sender" in (dead[unconfigured.delivery_id].last_error or "")


async def test_localhost_webhook_allowed_in_development(
    manual_clock: clock.ManualClock,
) -> None:
    """Plain-text localhost webhooks require an explicit opt-in."""
    record = model.new_delivery(
        _signal(), C.WEBHOOK, "http://localhost:9/h", manual_clock.now_ms()
    )
    async with httpx.AsyncClient() as client:
        with respx.mock() as mock:
            mock.post("http://localhost:9/h").mock(
                return_value=httpx.Response(200)
            )
            await channels.WebhookSender(
                client, HOOK_SECRET, allow_insecure_localhost=True
            ).send(record)
            with pytest.raises(model.DeliveryError):
                await channels.WebhookSender(client, HOOK_SECRET).send(record)


async def test_quiet_hours_and_channel_policy_in_dispatch(
    manual_clock: clock.ManualClock,
) -> None:
    """Quiet subscribers and disallowed channels get nothing queued."""
    subs = memory.InMemorySubscriptionRepository()
    subs.upsert(
        model.Subscription(
            subscription_id="q",
            tenant_id="tenant-a",
            channel=C.TELEGRAM,
            target="c",
            quiet_hours=model.QuietHours(
                start=datetime.time(0, 0), end=datetime.time(23, 59)
            ),
        )
    )
    box = memory.InMemoryOutbox()
    result = dispatcher.AlertDispatcher(
        box,
        subs,
        manual_clock,
        strategy_channels=_all_channels,
    ).route(_signal(mode="ALERT_ONLY"))
    assert [d.channel for d in result.deliveries] == [C.UI]
    assert result.suppressed == (("q", "quiet hours"),)
    assert [r.channel for r in box.all()] == [C.UI]


class _Boom:
    """Publisher that always fails, as a broken downstream would."""

    async def publish(self, signal: signal_domain.Signal) -> None:
        """Fails without publishing.

        Args:
            signal: Signal to publish.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("down")


async def test_fanout_isolates_failing_publishers(
    manual_clock: clock.ManualClock,
) -> None:
    """A failing downstream publisher does not block the others."""
    seen: list[str] = []

    class _Ok:
        """Publisher that records ids in ``seen``."""

        async def publish(self, signal: signal_domain.Signal) -> None:
            """Records the signal id.

            Args:
                signal: Signal to publish.
            """
            seen.append(signal.signal_id)

    await dispatcher.FanoutPublisher([_Boom(), _Ok()]).publish(_signal())
    assert seen == [_signal().signal_id]
    del manual_clock


async def test_worker_loop_starts_and_stops(
    manual_clock: clock.ManualClock,
) -> None:
    """The background loop delivers and stops cleanly."""
    box = memory.InMemoryOutbox()
    delivered: list[str] = []

    async def sink(tenant_id: str, payload: Mapping[str, object]) -> None:
        """Records the tenant of each delivery in ``delivered``.

        Args:
            tenant_id: Tenant delivered to.
            payload: Signal payload (unused).
        """
        delivered.append(tenant_id)
        del payload

    box.enqueue(
        model.new_delivery(
            _signal(), C.UI, "tenant:tenant-a", manual_clock.now_ms()
        )
    )
    worker = dispatcher.OutboxWorker(
        box, {C.UI: channels.UiSender(sink)}, manual_clock
    )
    worker.start(interval_s=0.01)
    for _ in range(100):
        if delivered:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    assert delivered == ["tenant-a"]


def test_senders_never_expose_secrets_in_repr() -> None:
    """Sender representations redact secrets."""
    client = httpx.AsyncClient()
    texts = [
        repr(channels.TelegramSender(client, BOT_TOKEN)),
        repr(channels.LineSender(client, LINE_TOKEN)),
        repr(channels.WebhookSender(client, HOOK_SECRET)),
        repr(channels.ExecutionForwarder(client, "http://x", SERVICE_TOKEN)),
        repr(
            channels.EmailSender(
                _FakeSmtp,  # type: ignore[arg-type]  # fake has only the SMTP calls EmailSender makes
                "a@b.c",
                password="pw-SECRET",
            )
        ),
    ]
    assert all("SECRET" not in text for text in texts)


def test_sql_subscriptions_round_trip(engine: sa.Engine) -> None:
    """Subscriptions with quiet hours persist once per id, per tenant."""
    sql.create_schema(engine)
    repo = sql.SqlSubscriptionRepository(engine)
    sub = model.Subscription(
        subscription_id="s1",
        tenant_id="tenant-a",
        channel=C.LINE,
        target="U1",
        kinds=frozenset({"ENTRY_CONFIRMED"}),
        quiet_hours=model.QuietHours(
            datetime.time(22, 0), datetime.time(7, 0), "Asia/Taipei"
        ),
    )
    repo.upsert(sub)
    repo.upsert(sub)
    assert repo.list_for_tenant("tenant-a") == [sub]
    assert repo.list_for_tenant("tenant-b") == []


def _stored_subscription(sub_id: str, kinds: list[str]) -> dict[str, object]:
    """Builds a subscription body as an older engine stored it.

    Args:
        sub_id: Subscription id (also the storage key).
        kinds: Stored kinds filter.

    Returns:
        The stored JSON object.
    """
    return {
        "subscription_id": sub_id,
        "tenant_id": "tenant-a",
        "channel": "TELEGRAM",
        "target": "123456789",
        "kinds": kinds,
        "min_severity": "INFO",
        "strategy_ids": [],
        "enabled": True,
        "quiet_hours": None,
        "web_push": None,
    }


def _legacy_payload(kind: str) -> dict[str, object]:
    """Builds a pending payload in the declarative-strategy contract shape.

    Args:
        kind: Signal kind.

    Returns:
        The current test signal's contract with the removed fields added.
    """
    body = _signal().to_contract()
    del body["source_hash"], body["condition"]
    body.update(
        kind=kind,
        rule_version="sha256:b",
        setup_id="s1",
        setup_state="FIRED",
        reason_tree={"op": "ALL", "result": "TRUE"},
        aoi_ids=["zone-1"],
        risk_evaluation={"status": "PASS", "reasons": []},
    )
    return body


def test_sql_migration_of_retired_kinds(engine: sa.Engine) -> None:
    """Stored kinds filters and pending payloads lose the retired kinds.

    A filter of only retired kinds becomes every current kind; pending
    deliveries of a retired kind are dead-lettered and other legacy
    payloads take the current contract shape.
    """
    sql.METADATA.create_all(engine)
    stored = {
        "sub-all": [],
        "sub-mixed": ["ENTRY_BLOCKED", "ENTRY_CONFIRMED", "PRE_SIGNAL"],
        "sub-retired": ["SETUP_EXPIRED", "WATCHING_AOI"],
        "sub-current": ["CUSTOM_ALERT"],
    }
    legacy = {
        "dlv-entry": ("PENDING", _legacy_payload("ENTRY_CONFIRMED")),
        "dlv-blocked": ("PENDING", _legacy_payload("ENTRY_BLOCKED")),
        "dlv-done": ("DELIVERED", _legacy_payload("PRE_SIGNAL")),
    }
    with engine.begin() as conn:
        for sub_id, kinds in stored.items():
            conn.execute(
                sql.SUBSCRIPTIONS.insert().values(
                    subscription_id=sub_id,
                    tenant_id="tenant-a",
                    body_json=json.dumps(_stored_subscription(sub_id, kinds)),
                )
            )
        for delivery_id, (status, payload) in legacy.items():
            conn.execute(
                sql.OUTBOX.insert().values(
                    delivery_id=delivery_id,
                    idempotency_key=delivery_id,
                    tenant_id="tenant-a",
                    channel="WEBHOOK",
                    target="https://hooks.example.com/joseph",
                    payload_json=json.dumps(payload, sort_keys=True),
                    status=status,
                    attempts=0,
                    next_attempt_ms=0,
                    created_at_ms=0,
                )
            )
    assert sql.migrate_legacy_kinds(engine) == 4
    assert sql.migrate_legacy_kinds(engine) == 0
    sql.create_schema(engine)
    subs = {
        s.subscription_id: s.kinds
        for s in sql.SqlSubscriptionRepository(engine).list_for_tenant(
            "tenant-a"
        )
    }
    assert subs == {
        "sub-all": frozenset(),
        "sub-mixed": frozenset({"ENTRY_CONFIRMED"}),
        "sub-retired": signal_domain.SIGNAL_KINDS,
        "sub-current": frozenset({"CUSTOM_ALERT"}),
    }
    outbox = sql.SqlOutbox(engine)
    entry = outbox.get("dlv-entry")
    blocked = outbox.get("dlv-blocked")
    done = outbox.get("dlv-done")
    assert entry is not None and blocked is not None and done is not None
    assert entry.status is model.DeliveryStatus.PENDING
    assert dict(entry.payload) == _signal().to_contract()
    assert blocked.status is model.DeliveryStatus.DEAD
    assert blocked.last_error == (
        "signal kind retired with the declarative strategies"
    )
    assert done.payload == legacy["dlv-done"][1]
