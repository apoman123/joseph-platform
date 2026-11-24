# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Regression test: an open reader must never block a writer's commit.

With SQLite's default rollback journal, one connection's read transaction
blocks another connection's commit; commits run on the event loop, so that
freezes the whole engine. ``create_engine`` therefore enables WAL.
"""

from __future__ import annotations

import pathlib
import time

import sqlalchemy as sa

from joseph.platform import db


def test_open_reader_does_not_block_commit(tmp_path: pathlib.Path) -> None:
    """A commit completes promptly while another connection is reading."""
    engine = db.create_engine(f"sqlite:///{tmp_path / 'market.db'}")
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
