# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Normalize historical constituents from matched provider daily price bases.

Fugle documents adjusted-close / raw-close as the available adjustment factor:
https://developer.fugle.tw/docs/data/http-api/historical/candles/
This never infers corporate actions from adjacent price jumps. Apply before
aggregation so a weekly or intraday bar cannot straddle incompatible bases.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime
import decimal
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes

REFRESH_MS = 900_000
"""UTC cache epoch, in milliseconds, shared with chart-history invalidation."""
_MAX_FACTORS = 8192
_D1 = timeframes.Timeframe.D1
FactorLoader: TypeAlias = Callable[
    [int, int], Awaitable[Mapping[int, decimal.Decimal]]
]
"""Loads confirmed daily factors for an inclusive/exclusive UTC date range."""


def _today_start(calendar: sessions.BarCalendar, now_ms: int) -> int:
    """Find midnight of today's exchange date, including before the open.

    Args:
        calendar: Instrument's exchange or continuous-market calendar.
        now_ms: Current UTC epoch milliseconds.

    Returns:
        UTC epoch milliseconds of exchange-local midnight. The previous
        session remains historical on weekends and before today's open.
    """
    zone = (
        calendar.tz
        if isinstance(calendar, sessions.ExchangeCalendar)
        else datetime.UTC
    )
    day = datetime.datetime.fromtimestamp(now_ms / 1000, zone).date()
    return int(
        datetime.datetime.combine(day, datetime.time(0), zone).timestamp()
        * 1000
    )


def daily_factors(
    raw: Sequence[market.Candle], adjusted: Sequence[market.Candle]
) -> dict[int, decimal.Decimal]:
    """Derive factors from the same instrument, session and provider closes.

    Args:
        raw: Native daily candles, with positive finite close prices.
        adjusted: Provider-adjusted daily candles for the identical sessions.

    Returns:
        Daily session start milliseconds mapped to adjusted/raw close ratios.

    Raises:
        errors.VenueUnavailableError: If observations disagree on identity,
            sessions or contain unusable prices. Missing evidence is not unity.
    """
    originals = {bar.start_ms: bar for bar in raw}
    restored = {bar.start_ms: bar for bar in adjusted}
    identities = {bar.instrument_id for bar in raw}
    identities.update(bar.instrument_id for bar in adjusted)
    if (
        originals.keys() != restored.keys()
        or len(originals) != len(raw)
        or len(restored) != len(adjusted)
        or len(identities) > 1
    ):
        raise errors.VenueUnavailableError("Adjustment daily sessions differ")
    factors = {}
    for start, bar in originals.items():
        other = restored[start]
        if (
            bar.instrument_id != other.instrument_id
            or bar.timeframe is not _D1
            or other.timeframe is not _D1
            or not bar.close.is_finite()
            or not other.close.is_finite()
            or bar.close <= 0
            or other.close <= 0
        ):
            raise errors.VenueUnavailableError(
                "Invalid daily adjustment evidence"
            )
        factors[start] = other.close / bar.close
    return factors


class DailyPriceNormalizer:  # noqa: DOC302 - Document constructor mutation.
    """Cache daily factors and adjust historical OHLC without altering the tape.

    Turnover, trade counts, aggressor classification and current-session
    prices retain their reported values. Stock split volume rebasing is opt-in.
    Factors expire on a UTC epoch; a lock
    per instrument coalesces concurrent timeframe loads. The factor LRU is
    bounded to 8192 instrument/session pairs.

    Attributes:
        _clock: UTC receipt clock used for sessions and factor expiration.
        _epoch: Integer UTC fifteen-minute epoch, initially -1 before use.
        _factors: Ordered instrument/session-to-Decimal ratio cache; session
            starts are UTC milliseconds and ratios are positive and finite.
        _locks: Instrument-to-asyncio.Lock map coalescing concurrent requests
            for the same provider listing during this source's lifetime.
    """

    def __init__(  # noqa: DOC303 - Document None after mutation.
        self, clock: clock_lib.Clock
    ) -> None:
        """Initialize an empty provider-local cache without starting I/O.

        Args:
            clock: UTC receipt clock determining the current session and epoch.

        Returns:
            None; factor fetches occur only when historical bars require them.
        """
        self._clock = clock
        self._epoch = -1
        self._factors: collections.OrderedDict[
            tuple[instruments.InstrumentId, int], decimal.Decimal
        ] = collections.OrderedDict()
        self._locks: dict[instruments.InstrumentId, asyncio.Lock] = {}

    async def adjust(
        self,
        bars: Sequence[market.Candle],
        load: FactorLoader,
        *,
        split_volume: bool = False,
    ) -> list[market.Candle]:
        """Normalize past-session constituents, failing on missing evidence.

        Args:
            bars: Native constituents of exactly one exchange instrument.
            load: Async provider callback accepting inclusive start and
                exclusive end UTC milliseconds, returning daily factors.
            split_volume: Optional inverse-price share scaling for a provider
                whose split-adjusted bars also adjust volume; defaults to false.

        Returns:
            New candles with normalized OHLC and an ``.adjusted`` source suffix;
            current-session candles keep native OHLC. Quantities stay raw unless
            split_volume matches them to the provider's split-adjusted shares.

        Raises:
            errors.VenueUnavailableError: If a historical session has no valid
                factor or the epoch changes while evidence is being loaded.
            ValueError: If bars mix instruments or are coarser than one day.
        """
        if not bars:
            return []
        instrument = bars[0].instrument_id
        if any(
            bar.instrument_id != instrument
            or bar.timeframe.duration_ms > _D1.duration_ms
            for bar in bars
        ):
            raise ValueError(
                "Adjust one instrument's constituents before aggregation"
            )
        calendar = sessions.for_instrument(instrument)
        starts = {calendar.floor(_D1, bar.start_ms) for bar in bars}
        selected = await self.ratios(instrument, starts, load)
        current = _today_start(calendar, self._clock.now_ms())
        result = []
        for bar in bars:
            factor = selected.get(
                calendar.floor(_D1, bar.start_ms), decimal.Decimal(1)
            )
            volume = bar.base_volume
            buy_volume = bar.taker_buy_base_volume
            if split_volume:
                volume /= factor
                if buy_volume is not None:
                    buy_volume /= factor
            result.append(
                dataclasses.replace(
                    bar,
                    open=bar.open * factor,
                    high=bar.high * factor,
                    low=bar.low * factor,
                    close=bar.close * factor,
                    base_volume=volume,
                    taker_buy_base_volume=buy_volume,
                    source=f"{bar.source}.adjusted"
                    if calendar.floor(_D1, bar.start_ms) < current
                    else bar.source,
                )
            )
        return result

    async def ratios(
        self,
        instrument: instruments.InstrumentId,
        starts: set[int],
        load: FactorLoader,
    ) -> dict[int, decimal.Decimal]:
        """Return confirmed past-session ratios and current-session unity.

        Args:
            instrument: Exact exchange instrument whose prices are normalized.
            starts: Daily session start times in UTC milliseconds.
            load: Async provider callback reading matched daily price evidence.

        Returns:
            Session starts mapped to positive price factors; current-session
            prices use unity, without making an extra data request.

        Raises:
            errors.VenueUnavailableError: If any past-session factor is missing,
                invalid or became obsolete during a cache-epoch transition.
        """
        calendar = sessions.for_instrument(instrument)
        current = _today_start(calendar, self._clock.now_ms())
        needed = {start for start in starts if start < current}
        lock = self._locks.setdefault(instrument, asyncio.Lock())
        async with lock:
            epoch = self._clock.now_ms() // REFRESH_MS
            if self._epoch != epoch:
                self._factors.clear()
                self._epoch = epoch
            missing = {
                start
                for start in needed
                if (instrument, start) not in self._factors
            }
            if missing:
                factors = await load(
                    min(missing), calendar.end_of(_D1, max(missing))
                )
                if self._clock.now_ms() // REFRESH_MS != epoch:
                    raise errors.VenueUnavailableError(
                        "Adjustment epoch changed; retry history"
                    )
                if any(
                    start not in factors
                    or not factors[start].is_finite()
                    or factors[start] <= 0
                    for start in missing
                ):
                    raise errors.VenueUnavailableError(
                        "Missing confirmed daily adjustment factor"
                    )
                for start, factor in factors.items():
                    self._factors[instrument, start] = factor
            selected = {
                start: self._factors[instrument, start] for start in needed
            }
            for start in needed:
                self._factors.move_to_end((instrument, start))
            while len(self._factors) > _MAX_FACTORS:
                self._factors.popitem(last=False)
        return {
            start: selected.get(start, decimal.Decimal(1)) for start in starts
        }
