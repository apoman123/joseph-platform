# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Regression test: a reader must never block a writer's commit (WAL)."""

from __future__ import annotations

import pathlib
import time

import sqlalchemy as sa

from joseph_execution.adapters.persistence import sql


def test_open_reader_does_not_block_commit(tmp_path: pathlib.Path) -> None:
    """A commit finishes within a second during an open read; WAL is on."""
    engine = sql.create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE probe (value INTEGER)")
        conn.exec_driver_sql("INSERT INTO probe VALUES (1)")
    reader = engine.connect()
    reader.exec_driver_sql("BEGIN")
    assert reader.execute(sa.text("SELECT count(*) FROM probe")).scalar() == 1
    started = time.monotonic()
    with engine.begin() as writer:
        writer.exec_driver_sql("INSERT INTO probe VALUES (2)")
    assert time.monotonic() - started < 1.0
    reader.close()
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert mode == "wal"
    engine.dispose()
