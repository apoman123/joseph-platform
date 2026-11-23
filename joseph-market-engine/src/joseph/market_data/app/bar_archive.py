"""Archive of recorded sparse bars and their coverage (port).

Venues with sparse bars (``ports.sparse_bars``) prove which spans they
delivered completely: inside such *coverage* a minute without a bar had no
trades. Where the bars themselves cannot be fetched again (TWSE and TPEx
intraday bars recorded from MIS snapshots) or cost a metered quota to fetch
again (SinoPac Shioaji kbars), the adapter keeps them with their coverage in
a ``BarArchive`` so both survive restarts.

Coverage is only ever stored together with the bars it proves, in one
write: coverage without its bars would turn lost bars into "quiet"
minutes. Each provider (the source family of the bars, e.g. ``twse.mis`` or
``sinopac``) keeps its own bars and coverage, so a reader never mixes
sources inside one bar. ``adapters.bar_archive_sql`` implements the port
with SQLAlchemy in the trade recorder's database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.domain import candles as candle_rules


class BarArchive(Protocol):
    """Durable store of final one-minute bars and their coverage."""

    def put(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        bars: Sequence[market.Candle],
        covered: Sequence[candle_rules.Span],
    ) -> None:
        """Stores final 1m bars and unites their coverage, atomically.

        Args:
            instrument: Instrument.
            provider: Source family of the bars.
            bars: Final 1m bars (upserted by start).
            covered: Spans in which every bar of the provider is stored.
        """
        ...

    def minutes(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns stored 1m bars starting in ``[start, end)``.

        Args:
            instrument: Instrument.
            provider: Source family.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending final candles.
        """
        ...

    def coverage(
        self,
        instrument: instruments.InstrumentId,
        provider: str,
        start_ms: int,
        end_ms: int,
    ) -> list[candle_rules.Span]:
        """Returns stored coverage overlapping ``[start, end)``.

        Args:
            instrument: Instrument.
            provider: Source family.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Disjoint ascending spans clipped to the range.
        """
        ...

    def prune(self, before_ms: int) -> int:
        """Drops bars and coverage older than a time.

        Args:
            before_ms: Bars starting before this time are deleted and
                coverage is cut at it.

        Returns:
            Bars deleted.
        """
        ...
