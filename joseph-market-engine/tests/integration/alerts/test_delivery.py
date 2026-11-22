"""Alert delivery over mocked HTTP/SMTP with the durable outbox."""

from __future__ import annotations

import asyncio
import datetime
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
    """Builds an entry signal.

    Args:
        mode: Strategy mode.

    Returns:
        Signal.
    """
    key = "tenant-a|long-a:v1|*|binance:perp:BTCUSDT|LONG|s1|ENTRY_CONFIRMED"
    return signal_domain.Signal(
        signal_id=signal_domain.signal_id_for(key),
        dedupe_key=key,
        tenant_id="tenant-a",
        strategy_id="long-a",
        strategy_version_id="long-a:v1",
        setup_id="s1",
        instrument_id="binance:perp:BTCUSDT",
        direction="LONG",
        timeframe="5m",
        kind="ENTRY_CONFIRMED",
        setup_state="FIRED",
        severity="ENTRY",
        bar_start_ms=1_795_132_800_000,
        bar_close_ms=1_795_133_100_000,
        evaluated_at_ms=1_795_133_100_100,
        inputs_snapshot_hash="sha256:a",
        rule_version="sha256:b",
        evaluator_version="1.0.0",
        indicator_versions={"fvg_ifvg": "1.0.0"},
        reason_tree={"op": "ALL", "result": "TRUE"},
        aoi_ids=("fvg-1",),
        range_ids=(),
        entry_price=None,
        stop_price=None,
        target_price=None,
        rr=None,
        risk_status="PASS",
        risk_reasons=(),
        mode=mode,
        data_source="LIVE",
    )


@pytest.fixture(params=["memory", "sql"])
def outbox(
    request: pytest.FixtureRequest, engine: sa.Engine
) -> ports.OutboxRepository:
    """Provides each outbox implementation.

    Args:
        request: Parametrization.
        engine: SQLite engine.

    Returns:
        Outbox.
    """
    if request.param == "memory":
        return memory.InMemoryOutbox()
    sql.create_schema(engine)
    return sql.SqlOutbox(engine)


def _subscriptions() -> memory.InMemorySubscriptionRepository:
    """Builds Telegram, LINE, webhook and e-mail subscriptions.

    Returns:
        Repository.
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
        All channel names.
    """
    del tenant_id, version_id
    return frozenset({"UI", "TELEGRAM", "LINE", "EMAIL", "WEBHOOK"})


class _FakeSmtp:
    """SMTP stand-in recording calls.

    Attributes:
        sent: Messages sent.
        calls: Method calls in order.
    """

    sent: typing.ClassVar[list[object]] = []
    calls: typing.ClassVar[list[str]] = []

    def __enter__(self) -> _FakeSmtp:
        """Enters the context.

        Returns:
            Self.
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
            user: User.
            password: Password.
        """
        self.calls.append(f"login:{user}")
        del password

    def send_message(self, message: object) -> None:
        """Records a message.

        Args:
            message: E-mail message.
        """
        self.sent.append(message)


async def test_end_to_end_delivery_on_every_channel(
    outbox: ports.OutboxRepository,
    manual_clock: clock.ManualClock,
) -> None:
    """One signal fans out to six channels with correct payloads."""
    ui_events: list[tuple[str, Mapping[str, object]]] = []

    async def ui_sink(tenant_id: str, payload: Mapping[str, object]) -> None:
        """Records UI pushes.

        Args:
            tenant_id: Tenant.
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
                        _FakeSmtp,
                        "joseph@example.com",
                        username="u",
                        password="pw-SECRET",
                    ),  # type: ignore[arg-type]
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
    assert _FakeSmtp.sent[0]["To"] == "trader@example.com"  # type: ignore[index]


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
    """Non-retryable responses and unsafe URLs are not retried."""
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


async def test_fanout_isolates_failing_publishers(
    manual_clock: clock.ManualClock,
) -> None:
    """A failing downstream publisher does not block the others."""
    seen: list[str] = []

    class _Boom:
        """Publisher that always fails."""

        async def publish(self, signal: signal_domain.Signal) -> None:
            """Raises.

            Args:
                signal: Signal.

            Raises:
                RuntimeError: Always.
            """
            raise RuntimeError("down")

    class _Ok:
        """Publisher that records ids."""

        async def publish(self, signal: signal_domain.Signal) -> None:
            """Records the signal id.

            Args:
                signal: Signal.
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
        """Records deliveries.

        Args:
            tenant_id: Tenant.
            payload: Payload.
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
                _FakeSmtp,
                "a@b.c",  # type: ignore[arg-type]
                password="pw-SECRET",
            )
        ),
    ]
    assert all("SECRET" not in text for text in texts)


def test_sql_subscriptions_round_trip(engine: sa.Engine) -> None:
    """Subscriptions with quiet hours persist."""
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
