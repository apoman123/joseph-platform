# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Encrypted push, WhatsApp and tenant-safe notification lifecycle tests."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from collections.abc import Mapping
from unittest import mock

import httpx
import pytest
import requests
import respx
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from joseph.alerts.adapters import memory
from joseph.alerts.adapters import mobile
from joseph.alerts.adapters import sql
from joseph.alerts.app import dispatcher
from joseph.alerts.app import subscriptions
from joseph.alerts.domain import model
from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import identity


def _encode(value: bytes) -> str:
    """Encodes raw key material for Web Push.

    Args:
        value: Raw P-256 or authentication bytes.

    Returns:
        Unpadded base64url text.
    """
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _keys() -> tuple[str, str]:
    """Generates a fresh P-256 pair for real SDK encryption tests.

    Returns:
        Raw private scalar and uncompressed public point, both base64url.
    """
    private = ec.generate_private_key(ec.SECP256R1())
    return (
        _encode(private.private_numbers().private_value.to_bytes(32, "big")),
        _encode(
            private.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        ),
    )


def _principal(tenant: str = "a") -> identity.Principal:
    """Builds an authenticated tenant principal for service tests.

    Args:
        tenant: Tenant identifier, default a.

    Returns:
        Principal with strategy read and write scopes.
    """
    return identity.Principal(
        tenant_id=tenant,
        user_id="user",
        scopes=frozenset({"strategy:read", "strategy:write"}),
    )


def _push_body(
    endpoint: str = "https://fcm.googleapis.com/fcm/send/private-device",
) -> dict[str, object]:
    """Creates a valid browser registration with fresh encryption keys.

    Args:
        endpoint: HTTPS browser push endpoint on a trusted service.

    Returns:
        Subscription input with private PushSubscription data.
    """
    return {
        "channel": "WEB_PUSH",
        "target": "device-"
        + hashlib.sha256(endpoint.encode()).hexdigest()[:32],
        "web_push": {
            "endpoint": endpoint,
            "keys": {
                "p256dh": _keys()[1],
                "auth": _encode(b"test-auth-16byte"),
            },
        },
    }


def _record(channel: model.Channel, target: str) -> model.DeliveryRecord:
    """Builds a due synthetic test delivery with no executable trading signal.

    Args:
        channel: Destination channel.
        target: Validated channel recipient.

    Returns:
        Pending tenant-a delivery due at time zero.
    """
    return model.DeliveryRecord(
        "test-id",
        "test-id",
        "a",
        channel,
        target,
        {"kind": "NOTIFICATION_TEST"},
        0,
    )


@pytest.mark.parametrize("status", [200, 400, 401, 429, 503])
@pytest.mark.asyncio
async def test_whatsapp_uses_template_and_redacts_errors(status: int) -> None:
    """Uses official templates and bounded retries without leaking tokens.

    Args:
        status: Simulated Meta HTTP status.

    Returns:
        None after checking provider payload and error retryability.
    """
    token = "private-meta-access-token"
    with respx.mock as router:
        route = router.post(
            "https://graph.facebook.com/v27.0/123/messages"
        ).mock(
            return_value=httpx.Response(
                status,
                json={"messages": [{"id": "wamid.example"}], "secret": token},
                headers={"Retry-After": "2"},
            )
        )
        async with httpx.AsyncClient() as client:
            sender = mobile.WhatsAppSender(client, token, "123", "joseph_alert")
            record = _record(model.Channel.WHATSAPP, "+14155552671")
            if status == 200:
                await sender.send(record)
            else:
                with pytest.raises(model.DeliveryError) as exc:
                    await sender.send(record)
                assert token not in str(exc.value)
                assert exc.value.retryable == (status in (429, 503))
                assert exc.value.retry_after_ms == 2000
        request = route.calls[0].request
        body = json.loads(request.content)
        assert request.headers["Authorization"] == f"Bearer {token}"
        assert body["to"] == "14155552671"
        assert body["type"] == "template"
        text = body["template"]["components"][0]["parameters"][0]["text"]
        assert "No trade was placed" in text
        assert "\n" not in text


@pytest.mark.asyncio
async def test_web_push_encrypts_real_payload_and_disables_expired_device() -> (
    None
):
    """Exercises SDK AES128GCM/VAPID and expires a device after HTTP 410."""
    repo = memory.InMemorySubscriptionRepository()
    body = _push_body()
    service = subscriptions.SubscriptionService(repo, ["WEB_PUSH"])
    service.upsert(_principal(), "phone", body)
    private, public = _keys()
    sender = mobile.WebPushSender(
        repo, private, public, "mailto:ops@example.com"
    )
    captured: list[bytes] = []
    status = 201

    def post(
        session: requests.Session, endpoint: str, **kwargs: object
    ) -> requests.Response:
        """Captures an encrypted SDK request without reaching a real device.

        Args:
            session: Isolated session whose redirect/proxy policy is checked.
            endpoint: Private endpoint used only for this mocked request.
            **kwargs: Requests data, headers and bounded timeout.

        Returns:
            Push service response with the controlled HTTP status.
        """
        assert session.max_redirects == 0
        assert not session.trust_env
        assert endpoint.startswith("https://fcm.googleapis.com/")
        assert kwargs["timeout"] == 8
        headers = kwargs["headers"]
        assert isinstance(headers, Mapping)
        assert headers["content-encoding"] == "aes128gcm"
        assert str(headers["Authorization"]).startswith("vapid ")
        data = kwargs["data"]
        assert isinstance(data, bytes)
        assert len(data) < 4096
        captured.append(data)
        response = requests.Response()
        response.status_code = status
        return response

    with mock.patch.object(requests.Session, "post", post):
        record = dataclasses.replace(
            _record(model.Channel.WEB_PUSH, str(body["target"])),
            payload={
                "kind": "ENTRY_CONFIRMED",
                "instrument_id": "台積電" * 2000,
            },
        )
        await sender.send(record)
        assert 2000 < len(captured[0]) < 4096
        assert ("台積電" * 5).encode() not in captured[0]
        status = 410
        with pytest.raises(model.DeliveryError, match="410") as exc:
            await sender.send(
                _record(model.Channel.WEB_PUSH, str(body["target"]))
            )
        assert not exc.value.retryable
        assert not repo.list_for_tenant("a")[0].enabled


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/x",
        "https://localhost/x",
        "https://fcm.googleapis.com.evil.example/x",
        "https://fcm.googleapis.com@127.0.0.1/x",
        "https://fcm.googleapis.com:8443/x",
    ],
)
def test_push_rejects_untrusted_destinations(endpoint: str) -> None:
    """Rejects endpoints that could redirect encrypted pushes to local services.

    Args:
        endpoint: Untrusted browser registration destination.

    Returns:
        None after verifying a redacted validation failure.
    """
    with pytest.raises(errors.ValidationFailedError, match="invalid Web Push"):
        subscriptions.from_contract("a", "phone", _push_body(endpoint))


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_tenant_collision_private_registration_and_delete(
    backend: str, engine: sa.Engine
) -> None:
    """Keeps identical public ids isolated and never returns browser secrets.

    Args:
        backend: Repository implementation to exercise.
        engine: Shared SQLite engine for durable storage tests.

    Returns:
        None after checking create, replace, restart and tenant-safe deletion.
    """
    sql.create_schema(engine)
    repo = (
        sql.SqlSubscriptionRepository(engine)
        if backend == "sql"
        else memory.InMemorySubscriptionRepository()
    )
    service = subscriptions.SubscriptionService(repo, ["WEB_PUSH"])
    body = _push_body()
    public = service.upsert(_principal(), "phone", body)
    assert "web_push" not in public
    service.upsert(
        _principal("b"), "phone", {"channel": "TELEGRAM", "target": "123"}
    )
    public["enabled"] = False
    service.upsert(_principal(), "phone", public)
    assert repo.list_for_tenant("a")[0].web_push is not None
    assert "private-device" not in str(service.list(_principal()))
    if backend == "sql":
        repo = sql.SqlSubscriptionRepository(engine)
        assert repo.list_for_tenant("a")[0].web_push is not None
    service.delete(_principal("b"), "phone")
    assert len(repo.list_for_tenant("a")) == 1
    with pytest.raises(errors.NotFoundError):
        service.delete(_principal("b"), "phone")


@pytest.mark.asyncio
async def test_test_delivery_survives_restart_and_never_reaches_execution(
    engine: sa.Engine,
) -> None:
    """Queues only the selected channel and polls tenant-safe durable status.

    Args:
        engine: Shared SQLite engine used to recreate outbox adapters.

    Returns:
        None after successful delivery through the normal outbox worker.
    """
    sql.create_schema(engine)
    repo = sql.SqlSubscriptionRepository(engine)
    outbox = sql.SqlOutbox(engine)
    service = subscriptions.SubscriptionService(
        repo, ["TELEGRAM"], outbox=outbox
    )
    service.upsert(
        _principal(), "phone", {"channel": "TELEGRAM", "target": "123"}
    )
    queued = service.test(_principal(), "phone")
    delivery_id = str(queued["delivery_id"])
    with pytest.raises(errors.NotFoundError):
        service.delivery(_principal("b"), delivery_id)
    record = sql.SqlOutbox(engine).get(delivery_id)
    assert record is not None
    assert record.channel is model.Channel.TELEGRAM
    assert record.payload == {"kind": "NOTIFICATION_TEST"}
    sender = mock.Mock(channel=model.Channel.TELEGRAM)
    sender.send = mock.AsyncMock()
    worker = dispatcher.OutboxWorker(
        sql.SqlOutbox(engine),
        {model.Channel.TELEGRAM: sender},
        clock.ManualClock(record.created_at_ms),
    )
    await worker.run_once()
    assert service.delivery(_principal(), delivery_id)["status"] == "DELIVERED"
    sender.send.assert_awaited_once()
    service.upsert(
        _principal(),
        "phone",
        {"channel": "TELEGRAM", "target": "123", "enabled": False},
    )
    with pytest.raises(errors.UnsupportedError):
        service.test(_principal(), "phone")


def test_push_key_mismatch_fails_without_revealing_private_key() -> None:
    """Rejects mismatched VAPID keys with a configuration-only error."""
    private, _ = _keys()
    with pytest.raises(ValueError) as exc:
        mobile.WebPushSender(
            memory.InMemorySubscriptionRepository(),
            private,
            _keys()[1],
            "mailto:ops@example.com",
        )
    assert private not in str(exc.value)


def test_legacy_unscoped_subscription_is_preserved_for_its_owner(
    engine: sa.Engine,
) -> None:
    """Updates pre-upgrade rows while allowing another tenant the same name.

    Args:
        engine: Shared SQLite database containing an old-format public key.

    Returns:
        None after checking compatible updates and isolated deletion.
    """
    sql.create_schema(engine)
    with engine.begin() as connection:
        connection.execute(
            sql.SUBSCRIPTIONS.insert().values(
                subscription_id="phone",
                tenant_id="a",
                body_json=json.dumps(
                    {
                        "subscription_id": "phone",
                        "tenant_id": "a",
                        "channel": "TELEGRAM",
                        "target": "123",
                    }
                ),
            )
        )
    repo = sql.SqlSubscriptionRepository(engine)
    service = subscriptions.SubscriptionService(repo, ["TELEGRAM"])
    service.upsert(
        _principal("b"), "phone", {"channel": "TELEGRAM", "target": "456"}
    )
    assert repo.list_for_tenant("a")[0].target == "123"
    service.upsert(
        _principal(), "phone", {"channel": "TELEGRAM", "target": "789"}
    )
    assert len(repo.list_for_tenant("a")) == 1
    assert repo.list_for_tenant("a")[0].target == "789"
    service.delete(_principal(), "phone")
    assert repo.list_for_tenant("b")[0].target == "456"


@pytest.mark.parametrize(
    "status,retryable", [(401, False), (429, True), (503, True)]
)
@pytest.mark.asyncio
async def test_push_retryability(status: int, retryable: bool) -> None:
    """Classifies push failures without exposing the private endpoint.

    Args:
        status: Simulated push service HTTP status.
        retryable: Whether the existing outbox should retry the delivery.

    Returns:
        None after checking the redacted error and retained device state.
    """
    repo = memory.InMemorySubscriptionRepository()
    body = _push_body()
    subscriptions.SubscriptionService(repo, ["WEB_PUSH"]).upsert(
        _principal(), "phone", body
    )
    sender = mobile.WebPushSender(repo, *_keys(), "mailto:tests@example.com")
    with (
        mock.patch.object(sender, "_send", return_value=status),
        pytest.raises(model.DeliveryError) as exc,
    ):
        await sender.send(_record(model.Channel.WEB_PUSH, str(body["target"])))
    assert exc.value.retryable is retryable
    assert "private-device" not in str(exc.value)
    assert repo.list_for_tenant("a")[0].enabled


@pytest.mark.asyncio
async def test_push_redirects_are_permanent_failures() -> None:
    """Prevents redirects from becoming retries or reaching untrusted hosts."""
    repo = memory.InMemorySubscriptionRepository()
    body = _push_body()
    subscriptions.SubscriptionService(repo, ["WEB_PUSH"]).upsert(
        _principal(), "phone", body
    )
    sender = mobile.WebPushSender(repo, *_keys(), "mailto:tests@example.com")
    with (
        mock.patch.object(
            sender,
            "_send",
            side_effect=requests.TooManyRedirects("private endpoint"),
        ),
        pytest.raises(model.DeliveryError) as exc,
    ):
        await sender.send(_record(model.Channel.WEB_PUSH, str(body["target"])))
    assert not exc.value.retryable
    assert "private endpoint" not in str(exc.value)
