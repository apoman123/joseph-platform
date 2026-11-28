# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Integration tests for the alert subscription API."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable
from collections.abc import Iterator
from unittest import mock

import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.alerts.adapters import channels
from joseph.platform import config


def test_alert_environment_file_keeps_secrets_server_side(
    tmp_path: pathlib.Path,
) -> None:
    """Loads alert settings and secret files without importing execution keys.

    Args:
        tmp_path: Temporary directory contained in the test workspace.

    Returns:
        None after checking precedence, redaction and the credential boundary.
    """
    token_file = tmp_path / "meta-token"
    token_file.write_text("private-meta-secret", encoding="utf-8")
    environment = tmp_path / "market.env"
    environment.write_text(
        f"JOSEPH_TELEGRAM_BOT_TOKEN=private-bot-secret\nJOSEPH_WHATSAPP_ACCESS_TOKEN_FILE={token_file}\nJOSEPH_WHATSAPP_PHONE_NUMBER_ID=123\nJOSEPH_WHATSAPP_TEMPLATE=joseph_alert\nJOSEPH_SERVICE_TOKEN=do-not-import\n",
        encoding="utf-8",
    )
    source = {"JOSEPH_MARKET_ENV_FILE": str(environment)}
    settings = config.Settings.from_env(source)
    assert settings.telegram_bot_token == "private-bot-secret"
    assert settings.whatsapp_access_token == "private-meta-secret"
    assert settings.service_token is None
    assert "private-meta-secret" not in repr(settings)
    assert "private-bot-secret" not in repr(settings)
    assert "JOSEPH_TELEGRAM_BOT_TOKEN" not in config.data_feed_environment(
        source
    )
    assert (
        config.Settings.from_env(
            {**source, "JOSEPH_TELEGRAM_BOT_TOKEN": ""}
        ).telegram_bot_token
        is None
    )


def test_notification_lifecycle_api_is_tenant_scoped(
    client: testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Exercises create, collision, test, status and delete through HTTP.

    Args:
        client: Full application with isolated tenants and Telegram configured.
        monkeypatch: Fixture replacing external provider I/O only.
        contract_validate: Shared JSON Schema validator.

    Returns:
        None after verifying no cross-tenant read or write is possible.
    """
    monkeypatch.setattr(channels.TelegramSender, "send", mock.AsyncMock())
    for auth, target in ((_AUTH_A, "123"), (_AUTH_B, "456")):
        assert (
            client.put(
                "/v1/alerts/subscriptions/phone",
                json=_body(target=target),
                headers=auth,
            ).status_code
            == 200
        )
    assert (
        client.get("/v1/alerts/subscriptions", headers=_AUTH_A).json()[
            "subscriptions"
        ][0]["target"]
        == "123"
    )
    queued = client.post("/v1/alerts/subscriptions/phone/test", headers=_AUTH_A)
    assert queued.status_code == 202
    contract_validate("signal.schema.json", "AlertDelivery", queued.json())
    path = "/v1/alerts/deliveries/" + queued.json()["delivery_id"]
    assert client.get(path, headers=_AUTH_A).status_code == 200
    assert client.get(path, headers=_AUTH_B).status_code == 404
    assert client.get(path).status_code == 401
    assert (
        client.delete(
            "/v1/alerts/subscriptions/phone", headers=_AUTH_B
        ).status_code
        == 204
    )
    assert (
        len(
            client.get("/v1/alerts/subscriptions", headers=_AUTH_A).json()[
                "subscriptions"
            ]
        )
        == 1
    )
    assert (
        client.delete(
            "/v1/alerts/subscriptions/phone", headers=_AUTH_A
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/v1/alerts/subscriptions/phone/test", headers=_AUTH_A
        ).status_code
        == 404
    )


_AUTH_A = {"Authorization": "Bearer token-a"}
_AUTH_B = {"Authorization": "Bearer token-b"}


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Runs the full app on the DEMO venue with two tenants.

    Yields:
        A client for the running app; ``token-a`` and ``token-b``
        authenticate tenant-a and tenant-b.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        strategy_activations_enabled=False,
        telegram_bot_token="123:secret-bot-token",
        dev_tokens=config.parse_dev_tokens(
            "token-a:tenant-a:user-a,token-b:tenant-b:user-b"
        ),
    )
    app = bootstrap.create_application(bootstrap.build_container(settings))
    with testclient.TestClient(app) as test_client:
        yield test_client


def _body(**overrides: object) -> dict[str, object]:
    """Builds a Telegram subscription body.

    Args:
        **overrides: Contract fields to replace.

    Returns:
        Contract ``AlertSubscription`` with Taipei overnight quiet hours.
    """
    body: dict[str, object] = {
        "subscription_id": "phone",
        "channel": "TELEGRAM",
        "target": "123456789",
        "kinds": ["ENTRY_CONFIRMED"],
        "min_severity": "ENTRY",
        "strategy_ids": [],
        "enabled": True,
        "quiet_hours": {
            "start": "23:00",
            "end": "07:00",
            "tz": "Asia/Taipei",
            "bypass_severities": ["CRITICAL"],
        },
    }
    body.update(overrides)
    return body


def test_upsert_and_list_conform_to_contract(
    client: testclient.TestClient,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Upserts and lists match the contract, name channels, hide the token."""
    stored = client.put(
        "/v1/alerts/subscriptions/phone", json=_body(), headers=_AUTH_A
    )
    assert stored.status_code == 200, stored.text
    contract_validate("signal.schema.json", "AlertSubscription", stored.json())
    listed = client.get("/v1/alerts/subscriptions", headers=_AUTH_A).json()
    contract_validate("signal.schema.json", "AlertSubscriptionList", listed)
    assert [s["subscription_id"] for s in listed["subscriptions"]] == ["phone"]
    assert "TELEGRAM" in listed["configured_channels"]
    assert "secret-bot-token" not in str(listed)


@pytest.mark.parametrize(
    ("channel", "target"),
    [
        ("TELEGRAM", "not-a-chat"),
        ("EMAIL", "nobody"),
        ("WEBHOOK", "http://plain-http.example.com"),
        ("LINE", "U123"),
        ("SMS", "123"),
    ],
)
def test_bad_targets_are_rejected(
    client: testclient.TestClient, channel: str, target: str
) -> None:
    """Each channel validates its target format."""
    response = client.put(
        "/v1/alerts/subscriptions/bad",
        json=_body(subscription_id="bad", channel=channel, target=target),
        headers=_AUTH_A,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_unknown_time_zone_is_rejected(client: testclient.TestClient) -> None:
    """Quiet hours require an IANA time zone."""
    body = _body(
        quiet_hours={"start": "23:00", "end": "07:00", "tz": "Mars/Olympus"}
    )
    response = client.put(
        "/v1/alerts/subscriptions/phone", json=body, headers=_AUTH_A
    )
    assert response.status_code == 422


def test_subscriptions_are_tenant_scoped(
    client: testclient.TestClient,
) -> None:
    """Tenant B never sees tenant A's subscriptions; auth is required."""
    client.put("/v1/alerts/subscriptions/phone", json=_body(), headers=_AUTH_A)
    listed_b = client.get("/v1/alerts/subscriptions", headers=_AUTH_B).json()
    assert listed_b["subscriptions"] == []
    assert client.get("/v1/alerts/subscriptions").status_code == 401
