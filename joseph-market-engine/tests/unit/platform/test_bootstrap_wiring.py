# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for composition-root helpers: senders, logging, plugins."""

from __future__ import annotations

import dataclasses
import logging

import httpx
import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.alerts.domain import model as alert_model
from joseph.domain import clock as clock_lib
from joseph.platform import config
from joseph.platform import redaction
from joseph.platform import streaming


async def test_every_configured_channel_gets_a_sender() -> None:
    """Configured credentials enable their channel; others stay absent."""
    settings = config.Settings(
        telegram_bot_token="tg-token-1",
        line_channel_token="line-token-1",
        webhook_secret="hook-secret-1",
        smtp_host="smtp.example",
        smtp_from="alerts@example.com",
        service_token="svc-token-1",
    )
    hub = streaming.StreamHub(clock_lib.ManualClock(0))
    async with httpx.AsyncClient() as client:
        senders = bootstrap._senders(settings, hub, client)
    assert set(senders) == {
        alert_model.Channel.UI,
        alert_model.Channel.TELEGRAM,
        alert_model.Channel.LINE,
        alert_model.Channel.WEBHOOK,
        alert_model.Channel.EMAIL,
        alert_model.Channel.EXECUTION,
    }
    async with httpx.AsyncClient() as client:
        plain = bootstrap._senders(config.Settings(), hub, client)
    assert set(plain) == {alert_model.Channel.UI}


def test_logging_setup_redacts_every_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_logging adds the redacting filter once per handler."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    redaction.configure_logging(logging.WARNING)
    redaction.configure_logging(logging.WARNING)
    assert len(root.handlers) == 1
    filters = root.handlers[0].filters
    assert sum(isinstance(f, redaction.RedactingFilter) for f in filters) == 1
    record = logging.LogRecord(
        "x", logging.ERROR, __file__, 1, "token=%s", ("abc123",), None
    )
    record.exc_text = "Traceback: password=hunter22"
    assert redaction.RedactingFilter().filter(record)
    assert record.getMessage() == "token=***"
    assert record.exc_text == "Traceback: password=***"


def test_script_catalog_follows_plugin_settings() -> None:
    """Script plugin settings shape the catalog the API serves."""
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        strategy_activations_enabled=False,
        script_plugin_packages="none",
        script_plugins_disabled=("builtin:rsi",),
        dev_tokens=config.parse_dev_tokens("wiring:tenant:user"),
    )
    container = bootstrap.build_container(
        settings, clock=clock_lib.ManualClock(1_795_132_800_000)
    )
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as client:
        listing = client.get(
            "/v1/scripts/catalog",
            params={"kind": "indicator"},
            headers={"Authorization": "Bearer wiring"},
        ).json()["scripts"]
    enabled = {row["script_id"]: row["enabled"] for row in listing}
    assert enabled["builtin:rsi"] is False
    assert enabled["builtin:ema"] is True
