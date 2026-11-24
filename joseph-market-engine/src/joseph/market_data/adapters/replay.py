# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Replay source reading recorded candles from JSONL files.

Layout: ``<root>/<venue>/<market_type>/<venue_market_id>/<timeframe>.jsonl``
where each line is a contract ``Candle``. Replayed candles always carry
``feed_state=REPLAY`` and are never tradable; missing files mean
``MissingDataError`` rather than synthetic substitutes.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.domain import candles as candle_rules
from joseph.market_data.domain import codec


class ReplaySource:
    """Serves recorded candles for any instrument present on disk."""

    def __init__(
        self,
        root: str | pathlib.Path,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        speed_s: float = 0.0,
    ) -> None:
        """Initializes the source.

        Args:
            root: Directory containing recordings.
            sleep: Sleep function awaited after each replayed bar
                (injectable for tests).
            speed_s: Seconds to wait after each replayed bar.
        """
        self._root = pathlib.Path(root)
        self._sleep = sleep
        self._speed_s = speed_s
        self._cache: dict[tuple[str, str], list[market.Candle]] = {}

    def _path(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> pathlib.Path:
        """Returns the recording path.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Returns:
            File path (may not exist).
        """
        return (
            self._root
            / instrument.venue.value
            / instrument.market_type.value
            / instrument.venue_market_id
            / f"{timeframe.value}.jsonl"
        )

    def has(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> bool:
        """Checks whether a recording exists.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Returns:
            True when recorded.
        """
        return self._path(instrument, timeframe).is_file()

    def _load(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> list[market.Candle]:
        """Loads and caches a recording.

        A recording is read once per source; later changes to the file are
        not seen.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Returns:
            Ascending candles.

        Raises:
            errors.MissingDataError: If no recording exists.
        """
        key = (str(instrument), timeframe.value)
        if key not in self._cache:
            path = self._path(instrument, timeframe)
            if not path.is_file():
                raise errors.MissingDataError(
                    f"no replay recording for {instrument} {timeframe.value}"
                )
            rows = [
                codec.candle_from_contract(
                    json.loads(line),
                    feed_state=quality.FeedState.REPLAY,
                    source="replay",
                )
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self._cache[key] = candle_rules.sort_unique(rows)
        return self._cache[key]

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Returns recorded candles in [start, end).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start, epoch milliseconds.
            end_ms: Exclusive end, epoch milliseconds.
            limit: Optional maximum (newest kept).

        Returns:
            Ascending candles.

        Raises:
            errors.MissingDataError: If no recording exists.
        """
        rows = [
            c
            for c in self._load(instrument, timeframe)
            if start_ms <= c.start_ms < end_ms
        ]
        return rows[-limit:] if limit is not None else rows

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
    ) -> AsyncIterator[market.Candle]:
        """Replays the recording bar by bar.

        Args:
            instrument: Instrument.
            timeframe: Resolution.

        Yields:
            Recorded candles in order.

        Raises:
            errors.MissingDataError: If no recording exists (on the first
                iteration).
        """
        for candle in self._load(instrument, timeframe):
            yield candle
            await self._sleep(self._speed_s)
