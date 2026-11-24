# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Fixtures serving captured venue responses through respx."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator

import pytest
import respx

from joseph.domain import clock as clock_lib

_CAPTURE_MS = 1_795_277_530_000
"""A time just after the crypto venue captures."""


@pytest.fixture
def venue_api(
    crypto_history_routes: Callable[[respx.Router], None],
    exchange_routes: Callable[[respx.Router], None],
) -> Iterator[respx.Router]:
    """Serves captured fixtures for every venue host.

    Args:
        crypto_history_routes: Installer of the crypto venue routes,
            monthly bars and trade history included.
        exchange_routes: Installer of the exchange and rate venue routes.

    Yields:
        Active respx router.
    """
    with respx.mock(assert_all_called=False) as router:
        crypto_history_routes(router)
        exchange_routes(router)
        yield router


@pytest.fixture
def capture_clock() -> clock_lib.ManualClock:
    """Returns a clock set to the fixture capture time.

    Returns:
        A manual clock at ``_CAPTURE_MS``.
    """
    return clock_lib.ManualClock(_CAPTURE_MS)
