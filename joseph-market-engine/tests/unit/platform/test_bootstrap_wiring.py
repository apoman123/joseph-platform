# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for composition-root helpers: senders, channels, plugins."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import httpx
import pytest

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


class _Repository:
    """Strategy repository stand-in.

    Attributes:
        answer: Version returned (or exception raised) by ``get``.
    """

    def __init__(self, answer: Any) -> None:
        """Initializes the stub.

        Args:
            answer: Version to return, or an exception to raise.
        """
        self.answer = answer

    def get(self, tenant_id: str, strategy_id: str, number: int) -> Any:
        """Returns the scripted version.

        Args:
            tenant_id: Tenant (unused).
            strategy_id: Strategy (unused).
            number: Version number (unused).

        Returns:
            The scripted version.

        Raises:
            Exception: ``answer``, when it is an exception.
        """
        del tenant_id, strategy_id, number
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_channel_policy_defaults_to_the_ui() -> None:
    """Unknown or malformed versions notify the UI only."""
    missing = bootstrap._channel_policy(_Repository(None))  # type: ignore[arg-type]  # a stub stands in for the strategy repository
    assert missing("t1", "s1:v1") == frozenset({"UI"})
    broken = bootstrap._channel_policy(_Repository(LookupError("x")))  # type: ignore[arg-type]  # a stub stands in for the strategy repository
    assert broken("t1", "s1:v2") == frozenset({"UI"})
    assert broken("t1", "no-version") == frozenset({"UI"})


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


async def test_signal_stack_uses_discovered_plugins_and_settings() -> None:
    """Discovery settings shape the registry and the rule vocabulary."""
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        indicator_plugin_packages="none",
        indicator_plugins_disabled=("rsi",),
    )
    container = bootstrap.build_container(
        settings, clock=clock_lib.ManualClock(1_795_132_800_000)
    )
    try:
        stack = container.signal_stack
        assert stack is not None
        vocabulary = stack.versions.vocabulary
        assert "rsi" in vocabulary.order
        assert "rsi" not in vocabulary.usable
        assert "ema" in vocabulary.usable
        assert vocabulary.kind_plugin["PIVOT_S1"] == "pivot_points"
        assert "rsi" not in stack.plugins.ids()
    finally:
        await container.aclose()
