"""Integration tests for the alert subscription API."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from collections.abc import Iterator

import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.platform import config

_A = {"Authorization": "Bearer token-a"}
_B = {"Authorization": "Bearer token-b"}


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Runs the full app on the DEMO venue with two tenants.

    Yields:
        Test client.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
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
        **overrides: Fields to replace.

    Returns:
        Contract ``AlertSubscription``.
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
    """Stored subscriptions round-trip and report configured channels."""
    stored = client.put(
        "/v1/alerts/subscriptions/phone", json=_body(), headers=_A
    )
    assert stored.status_code == 200, stored.text
    contract_validate("signal.schema.json", "AlertSubscription", stored.json())
    listed = client.get("/v1/alerts/subscriptions", headers=_A).json()
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
        headers=_A,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_unknown_time_zone_is_rejected(client: testclient.TestClient) -> None:
    """Quiet hours require an IANA time zone."""
    body = _body(
        quiet_hours={"start": "23:00", "end": "07:00", "tz": "Mars/Olympus"}
    )
    response = client.put(
        "/v1/alerts/subscriptions/phone", json=body, headers=_A
    )
    assert response.status_code == 422


def test_subscriptions_are_tenant_scoped(
    client: testclient.TestClient,
) -> None:
    """Tenant B never sees tenant A's subscriptions; auth is required."""
    client.put("/v1/alerts/subscriptions/phone", json=_body(), headers=_A)
    listed_b = client.get("/v1/alerts/subscriptions", headers=_B).json()
    assert listed_b["subscriptions"] == []
    assert client.get("/v1/alerts/subscriptions").status_code == 401
