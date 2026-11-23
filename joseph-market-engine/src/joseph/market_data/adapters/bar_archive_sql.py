"""SQLAlchemy Core ``BarArchive``: recorded sparse bars survive restarts.

Two tables, in the trade recorder's database (``JOSEPH_TRADE_ARCHIVE_URL``,
else ``JOSEPH_DATABASE_URL``):

* ``sparse_bars`` — final one-minute bars keyed by ``(instrument_id,
  provider, start_ms)``;
* ``sparse_bar_coverage`` — disjoint spans per ``(instrument_id,
  provider)`` in which every bar of that provider is stored, merged on
  write.

Bars and the coverage they prove are written in one transaction, so stored
coverage never claims a minute whose bar is missing. Decimals are stored as
exact strings; SQLite and PostgreSQL work unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from joseph.domain import decimals
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules

METADATA = sa.MetaData()
"""Schema of the sparse-bar archive."""

_M1 = timeframes.Timeframe.M1
_CHUNK = 400
_ID_LENGTH = 96

SPARSE_BARS = sa.Table(
    "sparse_bars",
    METADATA,
    sa.Column("instrument_id", sa.String(_ID_LENGTH), primary_key=True),
    sa.Column("provider", sa.String(32), primary_key=True),
    sa.Column("start_ms", sa.BigInteger, primary_key=True),
    sa.Column("open", sa.String(48), nullable=False),
    sa.Column("high", sa.String(48), nullable=False),
    sa.Column("low", sa.String(48), nullable=False),
    sa.Column("close", sa.String(48), nullable=False),
    sa.Column("base_volume", sa.String(48), nullable=False),
    sa.Column("quote_turnover", sa.String(48)),
    sa.Column("taker_buy_base_volume", sa.String(48)),
    sa.Column("taker_buy_method", sa.String(16)),
    sa.Column("trade_count", sa.Integer),
    sa.Column("event_ts_ms", sa.BigInteger),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("received_at_ms", sa.BigInteger, nullable=False),
)

SPARSE_COVERAGE = sa.Table(
    "sparse_bar_coverage",
    METADATA,
    sa.Column("instrument_id", sa.String(_ID_LENGTH), primary_key=True),
    sa.Column("provider", sa.String(32), primary_key=True),
    sa.Column("from_ms", sa.BigInteger, primary_key=True),
    sa.Column("to_ms", sa.BigInteger, nullable=False),
)


def _text(value: Any) -> str | None:
    """Encodes an optional decimal exactly.

    Args:
        value: Decimal or ``None``.

    Returns:
        Decimal string or ``None``.
    """
    return None if value is None else decimals.format_decimal(value)


def _int(value: Any) -> int:
    """Decodes a stored integer.

    Args:
        value: Column value.

    Returns:
        Integer.
    """
    return int(value)


def _row(key: str, provider: str, bar: market.Candle) -> dict[str, object]:
    """Encodes a one-minute bar as a table row.

    Args:
        key: Instrument id.
        provider: Source family.
        bar: Final 1m candle.

    Returns:
        Column values.
    """
    method = bar.taker_buy_method
    return {
        "instrument_id": key,
        "provider": provider,
        "start_ms": bar.start_ms,
        "open": decimals.format_decimal(bar.open),
        "high": decimals.format_decimal(bar.high),
        "low": decimals.format_decimal(bar.low),
        "close": decimals.format_decimal(bar.close),
        "base_volume": decimals.format_decimal(bar.base_volume),
        "quote_turnover": _text(bar.quote_turnover),
        "taker_buy_base_volume": _text(bar.taker_buy_base_volume),
        "taker_buy_method": None if method is None else method.value,
        "trade_count": bar.trade_count,
        "event_ts_ms": bar.event_ts_ms,
        "source": bar.source[:64],
        "received_at_ms": bar.received_at_ms,
    }


def _candle(
    instrument: instruments.InstrumentId, row: sa.RowMapping
) -> market.Candle:
    """Decodes a ``sparse_bars`` row.

    Args:
        instrument: Instrument.
        row: Result row.

    Returns:
        Final 1m candle.
    """
    start = int(row["start_ms"])
    method = row["taker_buy_method"]
    taker = row["taker_buy_base_volume"]
    turnover = row["quote_turnover"]
    return market.Candle(
        instrument_id=instrument,
        timeframe=_M1,
        start_ms=start,
        end_ms=sessions.for_instrument(instrument).end_of(_M1, start),
        open=decimals.to_decimal(row["open"]),
        high=decimals.to_decimal(row["high"]),
        low=decimals.to_decimal(row["low"]),
        close=decimals.to_decimal(row["close"]),
        base_volume=decimals.to_decimal(row["base_volume"]),
        is_final=True,
        source=str(row["source"]),
        feed_state=quality.FeedState.LIVE,
        received_at_ms=int(row["received_at_ms"]),
        quote_turnover=(
            None if turnover is None else decimals.to_decimal(turnover)
        ),
        trade_count=(
            None if row["trade_count"] is None else int(row["trade_count"])
        ),
        event_ts_ms=(
            None if row["event_ts_ms"] is None else int(row["event_ts_ms"])
        ),
        taker_buy_base_volume=(
            None if taker is None else decimals.to_decimal(taker)
        ),
        taker_buy_method=(
            None
            if method is None or taker is None
            else market.BuyVolumeMethod(str(method))
        ),
    )


class SqlBarArchive:
    """``BarArchive`` backed by SQLAlchemy Core.

    Attributes:
        engine: SQLAlchemy engine.
    """

    def __init__(self, engine: sa.Engine) -> None:
        """Initializes the archive and creates its tables.

        Args:
            engine: SQLAlchemy engine.
        """
        self.engine = engine
        METADATA.create_all(engine)

    def put(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        bars: Sequence[market.Candle],
        covered: Sequence[candle_rules.Span],
    ) -> None:
        """See ``BarArchive.put``."""
        key = str(instrument)
        rows = [
            _row(key, provider, bar)
            for bar in bars
            if bar.is_final and bar.timeframe is _M1
        ]
        with self.engine.begin() as conn:
            table = SPARSE_BARS
            for at in range(0, len(rows), _CHUNK):
                chunk = rows[at : at + _CHUNK]
                conn.execute(
                    table.delete().where(
                        table.c.instrument_id == key,
                        table.c.provider == provider,
                        table.c.start_ms.in_(
                            [row["start_ms"] for row in chunk]
                        ),
                    )
                )
                conn.execute(table.insert(), chunk)
            self._merge_coverage(conn, key, provider, covered)

    @staticmethod
    def _merge_coverage(
        conn: sa.Connection,
        key: str,
        provider: str,
        covered: Sequence[candle_rules.Span],
    ) -> None:
        """Unites new coverage with the stored spans it touches.

        Args:
            conn: Open transaction.
            key: Instrument id.
            provider: Source family.
            covered: New spans.
        """
        fresh = candle_rules.merge_spans(covered)
        if not fresh:
            return
        table = SPARSE_COVERAGE
        touching = conn.execute(
            sa.select(table.c.from_ms, table.c.to_ms).where(
                table.c.instrument_id == key,
                table.c.provider == provider,
                table.c.to_ms >= fresh[0][0],
                table.c.from_ms <= fresh[-1][1],
            )
        ).all()
        merged = candle_rules.merge_spans(
            [*fresh, *((_int(a), _int(b)) for a, b in touching)]
        )
        if touching:
            conn.execute(
                table.delete().where(
                    table.c.instrument_id == key,
                    table.c.provider == provider,
                    table.c.from_ms.in_([_int(a) for a, _ in touching]),
                )
            )
        conn.execute(
            table.insert(),
            [
                {
                    "instrument_id": key,
                    "provider": provider,
                    "from_ms": low,
                    "to_ms": high,
                }
                for low, high in merged
            ],
        )

    def minutes(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """See ``BarArchive.minutes``."""
        table = SPARSE_BARS
        query = (
            sa.select(table)
            .where(
                table.c.instrument_id == str(instrument),
                table.c.provider == provider,
                table.c.start_ms >= start_ms,
                table.c.start_ms < end_ms,
            )
            .order_by(table.c.start_ms)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [_candle(instrument, row) for row in rows]

    def coverage(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        start_ms: int,
        end_ms: int,
    ) -> list[candle_rules.Span]:
        """See ``BarArchive.coverage``."""
        table = SPARSE_COVERAGE
        query = (
            sa.select(table.c.from_ms, table.c.to_ms)
            .where(
                table.c.instrument_id == str(instrument),
                table.c.provider == provider,
                table.c.to_ms > start_ms,
                table.c.from_ms < end_ms,
            )
            .order_by(table.c.from_ms)
        )
        with self.engine.connect() as conn:
            spans = [(_int(a), _int(b)) for a, b in conn.execute(query).all()]
        return candle_rules.clip_spans(spans, start_ms, end_ms)

    def prune(self, before_ms: int) -> int:
        """See ``BarArchive.prune``."""
        table = SPARSE_COVERAGE
        with self.engine.begin() as conn:
            deleted = conn.execute(
                SPARSE_BARS.delete().where(SPARSE_BARS.c.start_ms < before_ms)
            ).rowcount
            conn.execute(table.delete().where(table.c.to_ms <= before_ms))
            conn.execute(
                table.update()
                .where(table.c.from_ms < before_ms)
                .values(from_ms=before_ms)
            )
        return int(deleted or 0)
