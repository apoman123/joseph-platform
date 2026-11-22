"""Deterministic synthetic DEMO venue (``mock``).

Prices are a pure function of ``(instrument, time)``: layered value noise
(multi-day trend, 6h swings, 45m waves), per-step jitter and occasional
decaying jumps that create displacement legs, fair-value gaps and sweeps.
Any range therefore reproduces exactly, and the indicator engine finds
realistic structure. Every value is labeled ``DEMO``; the venue is never
tradable live.

Each bar aggregates 60 sub-steps (1 minute for timeframes up to 1h, coarser
above), so bars up to 1h are mutually consistent; higher timeframes are
consistent with each other at their own sampling step.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import hashlib
import math
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.domain import books
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health

_MINUTE_MS = 60_000
_MASK = (1 << 64) - 1
_SOURCE = "mock"


@dataclasses.dataclass(frozen=True, slots=True)
class MockSpec:
    """Synthetic instrument parameters.

    Attributes:
        symbol: Venue market id.
        base: Base asset.
        price: Reference price.
        tick: Tick size.
        lot: Lot size.
        volume_per_minute: Typical base volume per minute.
        spread_bps: Quoted spread in basis points.
        funding_rate: Funding rate per 8h interval.
        open_interest_base: Open interest in base units.
        book_depth_base: Base quantity at the best level.
    """

    symbol: str
    base: str
    price: decimal.Decimal
    tick: decimal.Decimal
    lot: decimal.Decimal
    volume_per_minute: float
    spread_bps: decimal.Decimal
    funding_rate: decimal.Decimal
    open_interest_base: decimal.Decimal
    book_depth_base: decimal.Decimal


SPECS: tuple[MockSpec, ...] = (
    MockSpec(
        "BTCUSD",
        "BTC",
        decimal.Decimal(95000),
        decimal.Decimal("0.1"),
        decimal.Decimal("0.001"),
        40.0,
        decimal.Decimal("0.2"),
        decimal.Decimal("0.0001"),
        decimal.Decimal(30000),
        decimal.Decimal("2.5"),
    ),
    MockSpec(
        "ETHUSD",
        "ETH",
        decimal.Decimal(3400),
        decimal.Decimal("0.01"),
        decimal.Decimal("0.01"),
        600.0,
        decimal.Decimal("0.6"),
        decimal.Decimal("0.0013"),
        decimal.Decimal(450000),
        decimal.Decimal("40"),
    ),
    MockSpec(
        "SOLUSD",
        "SOL",
        decimal.Decimal(180),
        decimal.Decimal("0.001"),
        decimal.Decimal("0.1"),
        9000.0,
        decimal.Decimal("14"),
        decimal.Decimal("0.00005"),
        decimal.Decimal(2_000_000),
        decimal.Decimal("60"),
    ),
)


def _seed(symbol: str) -> int:
    """Derives a 64-bit seed from a symbol.

    Args:
        symbol: Venue market id.

    Returns:
        Seed.
    """
    return int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:8], "big")


def _mix(value: int) -> int:
    """SplitMix64 finalizer.

    Args:
        value: Input integer.

    Returns:
        Well-mixed 64-bit integer.
    """
    value = (value + 0x9E3779B97F4A7C15) & _MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
    return value ^ (value >> 31)


def _unit(seed: int, salt: int, index: int) -> float:
    """Returns a deterministic pseudo-random value in [-1, 1).

    Args:
        seed: Instrument seed.
        salt: Stream discriminator.
        index: Position in the stream.

    Returns:
        Pseudo-random value.
    """
    mixed = _mix(seed ^ _mix(salt * 0x100000001B3 + (index & _MASK)))
    return (mixed >> 11) / float(1 << 52) - 1.0


def _noise(seed: int, salt: int, position: float) -> float:
    """Smooth value noise in [-1, 1].

    Args:
        seed: Instrument seed.
        salt: Layer discriminator.
        position: Continuous position (knot units).

    Returns:
        Interpolated noise.
    """
    knot = math.floor(position)
    frac = position - knot
    eased = frac * frac * (3 - 2 * frac)
    left = _unit(seed, salt, knot)
    right = _unit(seed, salt, knot + 1)
    return left + (right - left) * eased


def log_return(seed: int, minute: float) -> float:
    """Returns the log-price offset at a (fractional) minute index.

    Args:
        seed: Instrument seed.
        minute: Minutes since the epoch.

    Returns:
        Log offset relative to the reference price.
    """
    value = (
        0.09 * _noise(seed, 1, minute / 4320.0)
        + 0.025 * _noise(seed, 2, minute / 360.0)
        + 0.006 * _noise(seed, 3, minute / 45.0)
        + 0.0009 * _unit(seed, 4, int(minute))
    )
    day = int(minute // 1440)
    for knot in (day - 2, day - 1, day):
        if _unit(seed, 5, knot) > 0.35:
            onset = knot * 1440 + int((_unit(seed, 6, knot) + 1) * 700)
            if minute >= onset:
                size = 0.018 * _unit(seed, 7, knot)
                value += size * math.exp(-(minute - onset) / 1800.0)
    return value


def _price(spec: MockSpec, seed: int, minute: float) -> float:
    """Returns the synthetic price at a minute index.

    Args:
        spec: Instrument parameters.
        seed: Instrument seed.
        minute: Minutes since the epoch.

    Returns:
        Price as a float (rounded later).
    """
    return float(spec.price) * math.exp(log_return(seed, minute))


class MockAdapter:
    """``MarketDataPort`` serving deterministic DEMO data."""

    def __init__(
        self,
        clock: clock_lib.Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stream_interval_s: float = 1.0,
    ) -> None:
        """Initializes the adapter.

        Args:
            clock: Time source.
            sleep: Sleep function (injectable for tests).
            stream_interval_s: Seconds between live updates.
        """
        self._clock = clock
        self._sleep = sleep
        self._interval = stream_interval_s
        self._specs = {spec.symbol: spec for spec in SPECS}
        self._seeds = {spec.symbol: _seed(spec.symbol) for spec in SPECS}

    @property
    def venue(self) -> instruments.Venue:
        """Returns ``Venue.MOCK``."""
        return instruments.Venue.MOCK

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns the DEMO capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=instruments.Venue.MOCK,
            display_name="Demo venue",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
            ),
            timeframes=tuple(timeframes.Timeframe),
            notes=(
                "Synthetic DEMO data; never tradable live.",
                "Deterministic: identical requests return identical bars.",
            ),
            docs_url="https://example.invalid/joseph/demo-venue",
            verified_at_ms=None,
        )

    def _spec(self, instrument: instruments.InstrumentId) -> MockSpec:
        """Resolves a mock instrument.

        Args:
            instrument: Canonical id.

        Returns:
            Parameters.

        Raises:
            errors.NotFoundError: If not a mock instrument.
        """
        spec = self._specs.get(instrument.venue_market_id)
        if instrument.venue is not instruments.Venue.MOCK or spec is None:
            raise errors.NotFoundError(f"unknown instrument {instrument}")
        return spec

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Lists the three DEMO perpetuals.

        Returns:
            Instruments.
        """
        now = self._clock.now_ms()
        return [
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=instruments.InstrumentId(
                        instruments.Venue.MOCK,
                        instruments.MarketType.PERP,
                        spec.symbol,
                    ),
                    canonical_base=spec.base,
                    quote="USD",
                    settlement="USD",
                ),
                display_symbol=f"{spec.base}-USD DEMO",
                tick_size=spec.tick,
                lot_size=spec.lot,
                min_qty=spec.lot,
                min_notional=decimal.Decimal(5),
                contract_multiplier=decimals.ONE,
                linear=True,
                status=instruments.InstrumentStatus.TRADING,
                asset_class=instruments.AssetClass.CRYPTO,
                source=_SOURCE,
                as_of_ms=now,
                funding_interval_hours=8.0,
                max_leverage=decimal.Decimal(20),
            )
            for spec in SPECS
        ]

    def _round_price(self, spec: MockSpec, value: float) -> decimal.Decimal:
        """Rounds a synthetic price to the tick.

        Args:
            spec: Instrument parameters.
            value: Float price.

        Returns:
            Tick-aligned price.
        """
        return decimals.round_to_step(decimals.to_decimal(value), spec.tick)

    def bar(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        now_ms: int | None = None,
    ) -> market.Candle | None:
        """Builds one bar, partial when ``now_ms`` falls inside it.

        Args:
            instrument: Mock instrument.
            timeframe: Resolution.
            start_ms: Aligned bar start.
            now_ms: Current time; defaults to the clock.

        Returns:
            Candle, or ``None`` when the bar has not started.
        """
        spec = self._spec(instrument)
        seed = self._seeds[spec.symbol]
        now = self._clock.now_ms() if now_ms is None else now_ms
        if start_ms > now:
            return None
        end_ms = timeframe.end_of(start_ms)
        minutes = timeframe.duration_ms // _MINUTE_MS
        step = max(1, minutes // 60)
        first = start_ms / _MINUTE_MS
        open_ = _price(spec, seed, first)
        high = low = open_
        close = open_
        volume = 0.0
        position = first
        stop = min(end_ms, now) / _MINUTE_MS
        wick = 0.0004 * math.sqrt(step)
        while position < stop:
            nxt = min(position + step, stop)
            p0 = _price(spec, seed, position)
            p1 = _price(spec, seed, nxt)
            index = int(position)
            up = abs(_unit(seed, 8, index)) * wick
            down = abs(_unit(seed, 9, index)) * wick
            high = max(high, max(p0, p1) * (1 + up))
            low = min(low, min(p0, p1) * (1 - down))
            fraction = (nxt - position) / step
            volume += (
                spec.volume_per_minute
                * step
                * fraction
                * (0.5 + abs(_unit(seed, 10, index)))
                * (1 + 300 * abs(p1 - p0) / p0)
            )
            close = p1
            position = nxt
        final = end_ms <= now
        open_d = self._round_price(spec, open_)
        close_d = self._round_price(spec, close)
        return market.Candle(
            instrument_id=instrument,
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            open=open_d,
            high=max(self._round_price(spec, high), open_d, close_d),
            low=min(self._round_price(spec, low), open_d, close_d),
            close=close_d,
            base_volume=decimals.floor_to_step(
                decimals.to_decimal(round(volume, 6)), spec.lot
            ),
            is_final=final,
            source=_SOURCE,
            feed_state=quality.FeedState.DEMO,
            received_at_ms=now,
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Generates candles for the range.

        Args:
            instrument: Mock instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars (newest kept).

        Returns:
            Ascending DEMO candles.
        """
        self._spec(instrument)
        now = self._clock.now_ms()
        duration = timeframe.duration_ms
        last = timeframe.floor(min(end_ms - 1, now))
        first = timeframe.floor(start_ms)
        if first < start_ms:
            first += duration
        if limit is not None:
            first = max(first, last - (limit - 1) * duration)
        rows = []
        cursor = first
        while cursor <= last:
            candle = self.bar(instrument, timeframe, cursor, now)
            if candle is not None:
                rows.append(candle)
            cursor += duration
        return rows

    def _last_price(self, spec: MockSpec, now_ms: int) -> decimal.Decimal:
        """Returns the synthetic last price.

        Args:
            spec: Instrument parameters.
            now_ms: Time.

        Returns:
            Price.
        """
        seed = self._seeds[spec.symbol]
        return self._round_price(spec, _price(spec, seed, now_ms / _MINUTE_MS))

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """Builds a DEMO snapshot with 24h stats from synthetic bars.

        Args:
            instrument: Mock instrument.

        Returns:
            Snapshot.
        """
        spec = self._spec(instrument)
        now = self._clock.now_ms()
        last = self._last_price(spec, now)
        hours = await self.fetch_candles(
            instrument, timeframes.Timeframe.H1, now - 86_400_000, now + 1
        )
        turnover = sum((c.base_volume * c.close for c in hours), decimals.ZERO)
        volume = sum((c.base_volume for c in hours), decimals.ZERO)
        first_open = hours[0].open if hours else last
        half = last * spec.spread_bps / decimal.Decimal(20000)
        bid = decimals.floor_to_step(last - half, spec.tick)
        ask = bid + max(spec.tick, decimals.round_to_step(half * 2, spec.tick))
        mark = last
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.DEMO,
            source=_SOURCE,
            as_of_ms=now,
            last=last,
            mark=mark,
            index=last,
            best_bid=bid,
            best_ask=ask,
            turnover_24h_quote=decimals.round_to_step(
                turnover, decimal.Decimal("0.01")
            ),
            volume_24h_base=volume,
            change_24h_pct=(last - first_open) / first_open * decimals.HUNDRED,
            funding=self._funding(instrument, spec, now),
            open_interest=market.OpenInterest(
                instrument_id=instrument,
                base_quantity=spec.open_interest_base,
                notional_usd=spec.open_interest_base * mark,
                mark_price=mark,
                source=_SOURCE,
                as_of_ms=now,
            ),
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Builds snapshots for all DEMO instruments.

        Returns:
            Snapshots.
        """
        rows = []
        for spec in SPECS:
            rows.append(
                await self.fetch_snapshot(
                    instruments.InstrumentId(
                        instruments.Venue.MOCK,
                        instruments.MarketType.PERP,
                        spec.symbol,
                    )
                )
            )
        return rows

    def _funding(
        self, instrument: instruments.InstrumentId, spec: MockSpec, now_ms: int
    ) -> market.Funding:
        """Builds the DEMO funding observation.

        Args:
            instrument: Mock instrument.
            spec: Instrument parameters.
            now_ms: Time.

        Returns:
            Funding.
        """
        period = 8 * 3_600_000
        next_ms = (now_ms // period + 1) * period
        return market.Funding(
            instrument_id=instrument,
            rate=spec.funding_rate,
            interval_hours=8.0,
            kind=market.FundingKind.PREDICTED,
            effective_time_ms=next_ms,
            next_funding_ms=next_ms,
            source=_SOURCE,
            as_of_ms=now_ms,
        )

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """Builds a deterministic DEMO book around the last price.

        Args:
            instrument: Mock instrument.
            depth: Levels per side.

        Returns:
            Order book.
        """
        snapshot = await self.fetch_snapshot(instrument)
        spec = self._spec(instrument)
        seed = self._seeds[spec.symbol]
        assert snapshot.best_bid is not None
        assert snapshot.best_ask is not None
        step = max(
            spec.tick,
            decimals.round_to_step(
                snapshot.best_bid * decimal.Decimal("0.00005"), spec.tick
            ),
        )
        bids, asks = [], []
        for level in range(max(1, depth)):
            scale = decimal.Decimal(1 + level) ** 1 * decimal.Decimal(
                str(round(0.6 + abs(_unit(seed, 11, level)), 4))
            )
            quantity = (
                decimals.floor_to_step(spec.book_depth_base * scale, spec.lot)
                or spec.lot
            )
            bids.append((snapshot.best_bid - step * level, quantity))
            asks.append((snapshot.best_ask + step * level, quantity))
        return books.snapshot_book(
            instrument,
            bids,
            asks,
            ts_ms=snapshot.as_of_ms,
            received_ms=snapshot.as_of_ms,
            source=_SOURCE,
        )

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """Returns DEMO open interest.

        Args:
            instrument: Mock instrument.

        Returns:
            Open interest.
        """
        snapshot = await self.fetch_snapshot(instrument)
        assert snapshot.open_interest is not None
        return snapshot.open_interest

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Returns DEMO funding.

        Args:
            instrument: Mock instrument.

        Returns:
            Funding.
        """
        spec = self._spec(instrument)
        return self._funding(instrument, spec, self._clock.now_ms())

    async def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams the live DEMO bar and finalizes it on the clock.

        Args:
            instrument: Mock instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Yields:
            Partial updates and then the final bar at each boundary.
        """
        self._spec(instrument)
        current = timeframe.floor(self._clock.now_ms())
        while True:
            now = self._clock.now_ms()
            boundary = timeframe.floor(now)
            while current < boundary:
                closed = self.bar(instrument, timeframe, current, now)
                if closed is not None:
                    yield closed
                current += timeframe.duration_ms
            live = self.bar(instrument, timeframe, current, now)
            if tracker is not None:
                tracker.record_message(now)
            if live is not None:
                yield live
            await self._sleep(self._interval)

    async def close(self) -> None:
        """No resources to release."""
