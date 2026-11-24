# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""API test fixtures: the DEMO app, a fixture-backed venue app, tokens."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import testclient

from joseph import bootstrap
from joseph.domain import clock as clock_lib
from joseph.platform import config

_CAPTURE_MS = 1_795_277_530_000
"""Capture time of the venue fixtures; clocks start here so data is fresh."""
_TOKEN = "it-token-tenant-01"
_TOKEN_B = "it-token-tenant-02"


def _settings(**overrides: Any) -> config.Settings:
    """Builds DEMO-only settings on in-memory SQLite with two tenants.

    Args:
        **overrides: ``Settings`` field overrides.

    Returns:
        The settings.
    """
    base = config.Settings(
        dev_tokens=config.parse_dev_tokens(
            f"{_TOKEN}:tenant-01:user-01,{_TOKEN_B}:tenant-02:user-02"
        ),
        database_url="sqlite://",
        demo_only=True,
        poll_interval_s=1.0,
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def tokens() -> tuple[str, str]:
    """Returns bearer tokens of tenant-01 and tenant-02.

    Returns:
        ``(tenant-01 token, tenant-02 token)``.
    """
    return _TOKEN, _TOKEN_B


@pytest.fixture
def demo_container() -> bootstrap.Container:
    """Builds a DEMO-only container.

    Returns:
        The container, not yet started.
    """
    return bootstrap.build_container(_settings())


@pytest.fixture
def demo_client(
    demo_container: bootstrap.Container,
) -> Iterator[testclient.TestClient]:
    """Serves the DEMO app.

    Args:
        demo_container: Container to serve.

    Yields:
        A client for the running app; it stops when the test ends.
    """
    app = bootstrap.create_application(demo_container)
    with testclient.TestClient(app) as client:
        yield client


@pytest.fixture
def venue_client(
    crypto_routes: Callable[[respx.Router], None],
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[tuple[testclient.TestClient, respx.Router]]:
    """Serves an app with every venue backed by captured fixtures.

    Args:
        crypto_routes: Installer of the crypto venue routes.
        exchange_routes: Installer of the exchange and rate venue routes.

    Yields:
        Test client and the respx router (to inject failures).
    """
    with respx.mock(assert_all_called=False) as router:
        crypto_routes(router)
        exchange_routes(router)
        container = bootstrap.build_container(
            _settings(demo_only=False),
            clock=clock_lib.ManualClock(_CAPTURE_MS),
            http_client=httpx.AsyncClient(),
        )
        app = bootstrap.create_application(container)
        with testclient.TestClient(app) as client:
            yield client, router
