"""Fixtures for integration tests: in-memory SQLite and a manual clock."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import pool

from joseph.domain import clock


@pytest.fixture
def engine() -> sa.Engine:
    """Provides a fresh in-memory SQLite engine shared across threads.

    Returns:
        Engine.
    """
    return sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=pool.StaticPool,
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def manual_clock() -> clock.ManualClock:
    """Provides a controllable clock.

    Returns:
        Clock at 2026-11-20 00:00 UTC.
    """
    return clock.ManualClock(1_795_132_800_000)
