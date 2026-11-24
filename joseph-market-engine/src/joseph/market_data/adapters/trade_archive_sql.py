# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""SQLAlchemy Core ``TradeArchive``: recorded trades survive restarts.

Three tables hold what the engine records from public trades:

* ``trade_seconds`` — complete 1s aggregates (OHLC, volumes, taker buy
  volume, trade count) keyed by ``(instrument_id, start_ms)``;
* ``trade_minutes`` — per-minute roll-ups of those aggregates (base and
  taker buy volume), recomputed for every minute a write touches, so true
  delta for long bars needs no scan of seconds rows;
* ``trade_coverage`` — disjoint whole-second intervals in which every trade
  was recorded, merged on write.

Decimals are stored as exact strings. SQLite (development, tests) and
PostgreSQL work unchanged; writes run in one transaction so coverage never
claims seconds whose aggregates are missing.
"""

from __future__ import annotations

import decimal
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.domain import recorded_trades

METADATA = sa.MetaData()
"""Schema of the recorded-trade archive."""

_S1 = timeframes.Timeframe.S1
_CHUNK = 400
"""Rows per delete/insert statement, bounding each ``IN`` list."""
_ID_LENGTH = 96
"""Column width of a canonical instrument id."""

TRADE_SECONDS = sa.Table(
    "trade_seconds",
    METADATA,
    sa.Column("instrument_id", sa.String(_ID_LENGTH), primary_key=True),
    sa.Column("start_ms", sa.BigInteger, primary_key=True),
    sa.Column("open", sa.String(48), nullable=False),
    sa.Column("high", sa.String(48), nullable=False),
    sa.Column("low", sa.String(48), nullable=False),
    sa.Column("close", sa.String(48), nullable=False),
    sa.Column("base_volume", sa.String(48), nullable=False),
    sa.Column("quote_turnover", sa.String(48)),
    sa.Column("taker_buy_base_volume", sa.String(48)),
    sa.Column("trade_count", sa.Integer),
    sa.Column("event_ts_ms", sa.BigInteger),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("received_at_ms", sa.BigInteger, nullable=False),
)

TRADE_MINUTES = sa.Table(
    "trade_minutes",
    METADATA,
    sa.Column("instrument_id", sa.String(_ID_LENGTH), primary_key=True),
    sa.Column("start_ms", sa.BigInteger, primary_key=True),
    sa.Column("base_volume", sa.String(48), nullable=False),
    sa.Column("taker_buy_base_volume", sa.String(48)),
)

TRADE_COVERAGE = sa.Table(
    "trade_coverage",
    METADATA,
    sa.Column("instrument_id", sa.String(_ID_LENGTH), primary_key=True),
    sa.Column("from_ms", sa.BigInteger, primary_key=True),
    sa.Column("to_ms", sa.BigInteger, nullable=False),
)


def create_schema(engine: sa.Engine) -> None:
    """Creates the archive tables if missing.

    Args:
        engine: SQLAlchemy engine.
    """
    METADATA.create_all(engine)


def _text(value: decimal.Decimal | None) -> str | None:
    """Encodes an optional decimal exactly.

    Args:
        value: Decimal or ``None``.

    Returns:
        Decimal string or ``None``.
    """
    return None if value is None else decimals.format_decimal(value)


def _number(value: object) -> decimal.Decimal | None:
    """Decodes an optional stored decimal.

    Args:
        value: Stored string or ``None``.

    Returns:
        Decimal or ``None``.
    """
    return None if value is None else decimals.to_decimal(value)


def _int(value: Any) -> int:
    """Decodes a stored integer.

    Args:
        value: Column value.

    Returns:
        Integer.
    """
    return int(value)


def _row(instrument: str, bar: market.Candle) -> dict[str, object]:
    """Encodes a 1s aggregate as a table row.

    Args:
        instrument: Canonical instrument id.
        bar: 1s candle.

    Returns:
        Column values.
    """
    return {
        "instrument_id": instrument,
        "start_ms": bar.start_ms,
        "open": decimals.format_decimal(bar.open),
        "high": decimals.format_decimal(bar.high),
        "low": decimals.format_decimal(bar.low),
        "close": decimals.format_decimal(bar.close),
        "base_volume": decimals.format_decimal(bar.base_volume),
        "quote_turnover": _text(bar.quote_turnover),
        "taker_buy_base_volume": _text(bar.taker_buy_base_volume),
        "trade_count": bar.trade_count,
        "event_ts_ms": bar.event_ts_ms,
        # The column holds 64 characters; a longer label is cut, not refused.
        "source": bar.source[:64],
        "received_at_ms": bar.received_at_ms,
    }


def _candle(
    instrument: instruments.InstrumentId, row: sa.RowMapping
) -> market.Candle:
    """Decodes a ``trade_seconds`` row (recorded live, so ``LIVE`` and final).

    Args:
        instrument: Instrument.
        row: Result row.

    Returns:
        Final 1s candle.
    """
    start = int(row["start_ms"])
    return market.Candle(
        instrument_id=instrument,
        timeframe=_S1,
        start_ms=start,
        end_ms=_S1.end_of(start),
        open=decimals.to_decimal(row["open"]),
        high=decimals.to_decimal(row["high"]),
        low=decimals.to_decimal(row["low"]),
        close=decimals.to_decimal(row["close"]),
        base_volume=decimals.to_decimal(row["base_volume"]),
        is_final=True,
        source=str(row["source"]),
        feed_state=quality.FeedState.LIVE,
        received_at_ms=int(row["received_at_ms"]),
        quote_turnover=_number(row["quote_turnover"]),
        trade_count=(
            None if row["trade_count"] is None else int(row["trade_count"])
        ),
        event_ts_ms=(
            None if row["event_ts_ms"] is None else int(row["event_ts_ms"])
        ),
        taker_buy_base_volume=_number(row["taker_buy_base_volume"]),
    )


class SqlTradeArchive:
    """``TradeArchive`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the archive and creates its tables.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine
        create_schema(engine)

    def put(
        self,
        instrument: instruments.InstrumentId,
        seconds: Sequence[market.Candle],
        covered: Sequence[recorded_trades.Interval],
    ) -> None:
        """Stores complete 1s aggregates and merges their coverage.

        Aggregates, the minute roll-ups they touch and the coverage are
        written in one transaction.

        Args:
            instrument: Instrument.
            seconds: Final 1s aggregates with traded volume, upserted by
                start.
            covered: Whole-second intervals in which every trade is
                recorded (united with the stored coverage).
        """
        key = str(instrument)
        rows = [_row(key, bar) for bar in seconds]
        with self.engine.begin() as conn:
            for at in range(0, len(rows), _CHUNK):
                chunk = rows[at : at + _CHUNK]
                conn.execute(
                    TRADE_SECONDS.delete().where(
                        TRADE_SECONDS.c.instrument_id == key,
                        TRADE_SECONDS.c.start_ms.in_(
                            [row["start_ms"] for row in chunk]
                        ),
                    )
                )
                conn.execute(TRADE_SECONDS.insert(), chunk)
            if seconds:
                self._roll_up(conn, key, seconds)
            if covered:
                self._merge_coverage(conn, key, covered)

    def _roll_up(
        self, conn: sa.Connection, key: str, seconds: Sequence[market.Candle]
    ) -> None:
        """Recomputes the minute roll-ups touched by a write.

        Args:
            conn: Open transaction.
            key: Instrument id.
            seconds: Aggregates just written.
        """
        minutes = sorted(
            {
                recorded_trades.floor_to(b.start_ms, recorded_trades.MINUTE_MS)
                for b in seconds
            }
        )
        low, high = minutes[0], minutes[-1] + recorded_trades.MINUTE_MS
        stored = conn.execute(
            sa.select(
                TRADE_SECONDS.c.start_ms,
                TRADE_SECONDS.c.base_volume,
                TRADE_SECONDS.c.taker_buy_base_volume,
            ).where(
                TRADE_SECONDS.c.instrument_id == key,
                TRADE_SECONDS.c.start_ms >= low,
                TRADE_SECONDS.c.start_ms < high,
            )
        ).all()
        # The select spans every minute from the first to the last touched
        # one; only the touched minutes are recomputed.
        wanted = set(minutes)
        volumes: dict[int, decimal.Decimal] = {}
        takers: dict[int, decimal.Decimal | None] = {}
        for start, volume, taker in stored:
            minute = recorded_trades.floor_to(
                _int(start), recorded_trades.MINUTE_MS
            )
            if minute not in wanted:
                continue
            volumes[minute] = volumes.get(
                minute, decimals.ZERO
            ) + decimals.to_decimal(volume)
            # One second without a taker volume leaves the minute's unknown.
            held = takers.get(minute, decimals.ZERO)
            takers[minute] = (
                None
                if held is None or taker is None
                else held + decimals.to_decimal(taker)
            )
        for at in range(0, len(minutes), _CHUNK):
            chunk = minutes[at : at + _CHUNK]
            conn.execute(
                TRADE_MINUTES.delete().where(
                    TRADE_MINUTES.c.instrument_id == key,
                    TRADE_MINUTES.c.start_ms.in_(chunk),
                )
            )
            conn.execute(
                TRADE_MINUTES.insert(),
                [
                    {
                        "instrument_id": key,
                        "start_ms": minute,
                        "base_volume": decimals.format_decimal(volumes[minute]),
                        "taker_buy_base_volume": _text(takers[minute]),
                    }
                    for minute in chunk
                ],
            )

    def _merge_coverage(
        self,
        conn: sa.Connection,
        key: str,
        covered: Sequence[recorded_trades.Interval],
    ) -> None:
        """Unites new coverage with the stored intervals it touches.

        Args:
            conn: Open transaction.
            key: Instrument id.
            covered: New intervals.
        """
        fresh = recorded_trades.merge(covered)
        if not fresh:
            return
        low, high = fresh[0][0], fresh[-1][1]
        table = TRADE_COVERAGE
        touching = conn.execute(
            sa.select(table.c.from_ms, table.c.to_ms).where(
                table.c.instrument_id == key,
                table.c.to_ms >= low,
                table.c.from_ms <= high,
            )
        ).all()
        merged = recorded_trades.merge(
            [*fresh, *((_int(a), _int(b)) for a, b in touching)]
        )
        if touching:
            conn.execute(
                table.delete().where(
                    table.c.instrument_id == key,
                    table.c.from_ms.in_([_int(a) for a, _ in touching]),
                )
            )
        conn.execute(
            table.insert(),
            [
                {"instrument_id": key, "from_ms": a, "to_ms": b}
                for a, b in merged
            ],
        )

    def seconds(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[market.Candle]:
        """Returns stored 1s aggregates starting in ``[start, end)``.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending 1s candles.
        """
        query = (
            sa.select(TRADE_SECONDS)
            .where(
                TRADE_SECONDS.c.instrument_id == str(instrument),
                TRADE_SECONDS.c.start_ms >= start_ms,
                TRADE_SECONDS.c.start_ms < end_ms,
            )
            .order_by(TRADE_SECONDS.c.start_ms)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [_candle(instrument, row) for row in rows]

    def previous(
        self, instrument: instruments.InstrumentId, before_ms: int
    ) -> market.Candle | None:
        """Returns the newest stored 1s aggregate starting before a time.

        Args:
            instrument: Instrument.
            before_ms: Exclusive bound, epoch milliseconds.

        Returns:
            The candle, or ``None`` when nothing is stored before it.
        """
        query = (
            sa.select(TRADE_SECONDS)
            .where(
                TRADE_SECONDS.c.instrument_id == str(instrument),
                TRADE_SECONDS.c.start_ms < before_ms,
            )
            .order_by(TRADE_SECONDS.c.start_ms.desc())
            .limit(1)
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).mappings().first()
        return None if row is None else _candle(instrument, row)

    def coverage(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[recorded_trades.Interval]:
        """Returns coverage intervals overlapping ``[start, end)``.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Disjoint ascending intervals (not clipped to the range).
        """
        table = TRADE_COVERAGE
        query = (
            sa.select(table.c.from_ms, table.c.to_ms)
            .where(
                table.c.instrument_id == str(instrument),
                table.c.to_ms > start_ms,
                table.c.from_ms < end_ms,
            )
            .order_by(table.c.from_ms)
        )
        with self.engine.connect() as conn:
            return [(_int(a), _int(b)) for a, b in conn.execute(query).all()]

    def minute_flows(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[recorded_trades.MinuteFlow]:
        """Returns per-minute roll-ups of minutes starting in the range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.

        Returns:
            Ascending minute totals.
        """
        table = TRADE_MINUTES
        query = (
            sa.select(
                table.c.start_ms,
                table.c.base_volume,
                table.c.taker_buy_base_volume,
            )
            .where(
                table.c.instrument_id == str(instrument),
                table.c.start_ms >= start_ms,
                table.c.start_ms < end_ms,
            )
            .order_by(table.c.start_ms)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).all()
        return [
            recorded_trades.MinuteFlow(
                _int(start), decimals.to_decimal(volume), _number(taker)
            )
            for start, volume, taker in rows
        ]

    def covered_until(self, instrument: instruments.InstrumentId) -> int | None:
        """Returns the end of the newest coverage interval.

        Args:
            instrument: Instrument.

        Returns:
            Exclusive end in epoch milliseconds, or ``None`` without
            coverage.
        """
        table = TRADE_COVERAGE
        query = sa.select(sa.func.max(table.c.to_ms)).where(
            table.c.instrument_id == str(instrument)
        )
        with self.engine.connect() as conn:
            value = conn.execute(query).scalar()
        return None if value is None else _int(value)

    def instruments(self) -> list[instruments.InstrumentId]:
        """Lists instruments with recorded coverage.

        Returns:
            Instruments, sorted by canonical id.
        """
        query = sa.select(TRADE_COVERAGE.c.instrument_id).distinct()
        with self.engine.connect() as conn:
            ids = sorted(str(row[0]) for row in conn.execute(query))
        return [instruments.InstrumentId.parse(value) for value in ids]

    def prune(self, before_ms: int) -> int:
        """Deletes aggregates and coverage before a retention cut-off.

        Coverage that straddles the cut-off is cut at it; a minute roll-up
        is deleted only once its whole minute lies before the cut-off.

        Args:
            before_ms: Cut-off, epoch milliseconds.

        Returns:
            Number of 1s aggregates deleted, as the driver reports it.
        """
        minute_cut = before_ms - recorded_trades.MINUTE_MS
        table = TRADE_COVERAGE
        with self.engine.begin() as conn:
            deleted = conn.execute(
                TRADE_SECONDS.delete().where(
                    TRADE_SECONDS.c.start_ms < before_ms
                )
            ).rowcount
            conn.execute(
                TRADE_MINUTES.delete().where(
                    TRADE_MINUTES.c.start_ms <= minute_cut
                )
            )
            conn.execute(table.delete().where(table.c.to_ms <= before_ms))
            conn.execute(
                table.update()
                .where(table.c.from_ms < before_ms)
                .values(from_ms=before_ms)
            )
        return int(deleted or 0)
