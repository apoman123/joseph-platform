# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Deterministic synthetic DEMO venue (``mock``).

Prices are a pure function of ``(instrument, time)``: layered value noise
(multi-day trend, 6h swings, 45m waves), per-step jitter and occasional
decaying jumps that create sharp moves, price gaps and wicks. Any range
therefore reproduces exactly, and indicators see realistic price action.
Every value is labeled ``DEMO``; the venue is never tradable live.

Each bar samples the price path in sub-steps: one minute long for
timeframes up to 1h, and ``minutes // 60`` minutes above (so 60 to 119
sub-steps; calendar months use their real length). Bars up to 1h are
therefore mutually consistent; higher timeframes are consistent with each
other at their own sampling step. Every bar carries a plausible taker buy volume
(the buy share leans with each step's price move), so true delta works.

Public trades are synthesized per minute so that they aggregate exactly to
that minute's 1m bar: the first trade opens it at ``:00.000``, interior
trades touch its high and low, quantities sum to its volume and buyer-
aggressor quantities to its taker buy volume. Seconds bars are folded from
these trades with the same builder (and rules) as real venues.

``mock:stock:ACME`` is a DEMO stock on the NYSE calendar (``XNYS``): its bars
start at the 09:30 New York open, stop at the close (early closes
included), skip weekends and holidays, and aggregate in-session minutes
only; daily bars are sessions and weekly/monthly bars their sessions. Its
trades exist only in session and its buy volume is labeled ``classified``
like a stock exchange's. It has no funding and no open interest, and its
24h snapshot fields describe the latest completed session.
"""

from __future__ import annotations

import asyncio
import collections
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
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.domain import books
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_MINUTE_MS = 60_000
_MASK = (1 << 64) - 1
_SOURCE = "mock"
_TRADE_SOURCE = "mock.trades"
# Most recent minutes of trades generated per call (a page cap): 6 hours.
_MAX_TRADE_MINUTES = 360
# Minutes of synthesized trades kept in the per-adapter LRU cache.
_TRADE_CACHE = 4_096


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
        book_depth_base: Typical base quantity at the best book level.
        market_type: Product type (``perp`` or ``stock``).
        asset_class: Asset class.
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
    market_type: instruments.MarketType = instruments.MarketType.PERP
    asset_class: instruments.AssetClass = instruments.AssetClass.CRYPTO

    @property
    def is_stock(self) -> bool:
        """Whether this is the exchange-listed DEMO stock."""
        return self.market_type is instruments.MarketType.STOCK

    def instrument_id(self) -> instruments.InstrumentId:
        """Returns the canonical id (``mock:{market_type}:{symbol}``)."""
        return instruments.InstrumentId(
            instruments.Venue.MOCK, self.market_type, self.symbol
        )


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
    MockSpec(
        "ACME",
        "ACME",
        decimal.Decimal(150),
        decimal.Decimal("0.01"),
        decimal.Decimal(1),
        6000.0,
        decimal.Decimal("2"),
        decimal.Decimal(0),
        decimal.Decimal(0),
        decimal.Decimal(1500),
        market_type=instruments.MarketType.STOCK,
        asset_class=instruments.AssetClass.EQUITY,
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
    # A jump starts on about a third of days, at a seeded minute, and decays
    # with an 1800-minute (30-hour) time constant, so the jumps of the two
    # previous days still contribute.
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


def _split_lots(
    lots: int, count: int, seed: int, salt: int, minute: int
) -> list[int]:
    """Splits ``lots`` into ``count`` positive integer parts.

    Args:
        lots: Total lots (``>= count``).
        count: Number of parts.
        seed: Instrument seed.
        salt: Stream discriminator.
        minute: Minute index.

    Returns:
        Parts summing exactly to ``lots``.
    """
    if count == 0:
        return []
    weights = [
        0.3 + abs(_unit(seed, salt, minute * 97 + k)) for k in range(count)
    ]
    spare = lots - count
    total = sum(weights)
    parts = [1 + int(spare * w / total) for w in weights]
    parts[-1] += lots - sum(parts)
    return parts


def _buy_count(
    trade_count: int, lots: int, taker_lots: int, share: float
) -> int:
    """Chooses how many of a minute's trades are buyer-aggressed.

    Args:
        trade_count: Trades in the minute.
        lots: Total lots.
        taker_lots: Buyer-aggressor lots.
        share: Desired buy fraction of trades.

    Returns:
        Number of buy trades, clamped so that every trade on either side can
        hold at least one lot.
    """
    sell_lots = lots - taker_lots
    low = max(trade_count - sell_lots, 1 if taker_lots > 0 else 0)
    high = min(taker_lots, trade_count - (1 if sell_lots > 0 else 0))
    return max(low, min(high, round(trade_count * share)))


def _sample_span(
    spec: MockSpec,
    seed: int,
    span: tuple[float, float],
    *,
    step: int,
    wick: float,
    totals: tuple[float, float, float, float, float],
) -> tuple[float, float, float, float, float]:
    """Folds the sub-steps of one in-session span into running totals.

    Args:
        spec: Instrument parameters.
        seed: Instrument seed.
        span: ``(first, stop)`` minute positions.
        step: Minutes per sub-step.
        wick: Largest wick per sub-step, as a fraction of the price.
        totals: ``(volume, taker, high, low, close)`` so far.

    Returns:
        Updated ``(volume, taker, high, low, close)``.
    """
    volume, taker, high, low, close = totals
    position, stop = span
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
        # Volume grows with the size of the move, and the taker buy share
        # leans with its direction (fully at a 0.2% move), so delta tracks
        # price.
        step_volume = (
            spec.volume_per_minute
            * step
            * fraction
            * (0.5 + abs(_unit(seed, 10, index)))
            * (1 + 300 * abs(p1 - p0) / p0)
        )
        lean = max(-1.0, min(1.0, (p1 - p0) / (p0 * 0.002)))
        share = 0.5 + 0.3 * lean + 0.15 * _unit(seed, 12, index)
        volume += step_volume
        taker += step_volume * min(0.95, max(0.05, share))
        close = p1
        position = nxt
    return volume, taker, high, low, close


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
        self._trade_cache: collections.OrderedDict[
            tuple[str, int], tuple[market.Trade, ...]
        ] = collections.OrderedDict()

    @property
    def venue(self) -> instruments.Venue:
        """The venue, always ``Venue.MOCK``."""
        return instruments.Venue.MOCK

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns the DEMO capabilities.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=instruments.Venue.MOCK,
            display_name="Demo venue",
            market_types=(
                instruments.MarketType.PERP,
                instruments.MarketType.STOCK,
            ),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True,
                native_ws_candles=True,
                native_ws_trades=True,
                book_snapshot=True,
                open_interest=True,
                funding=True,
                trades_history=True,
                taker_volume=True,
                classified_delta=True,
            ),
            timeframes=tuple(timeframes.Timeframe),
            trade_built_timeframes=timeframes.SECONDS,
            notes=(
                "Synthetic DEMO data; never tradable live.",
                "Deterministic: identical requests return identical bars.",
                "Synthetic trades aggregate exactly to the 1m bars; seconds "
                "bars are built from them like on real venues.",
                "mock:stock:ACME follows the XNYS session calendar; it has "
                "no funding or open interest and classified buy volume.",
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
        if (
            instrument.venue is not instruments.Venue.MOCK
            or spec is None
            or instrument.market_type is not spec.market_type
        ):
            raise errors.NotFoundError(f"unknown instrument {instrument}")
        return spec

    async def list_instruments(self) -> list[instruments.Instrument]:
        """Lists the three DEMO perpetuals and the DEMO stock.

        Returns:
            Instruments.
        """
        now = self._clock.now_ms()
        return [
            instruments.Instrument(
                key=instruments.InstrumentKey(
                    instrument_id=spec.instrument_id(),
                    canonical_base=spec.base,
                    quote="USD",
                    settlement="USD",
                ),
                display_symbol=(
                    f"{spec.base} DEMO"
                    if spec.is_stock
                    else f"{spec.base}-USD DEMO"
                ),
                tick_size=spec.tick,
                lot_size=spec.lot,
                min_qty=spec.lot,
                min_notional=None if spec.is_stock else decimal.Decimal(5),
                contract_multiplier=decimals.ONE,
                linear=True,
                status=instruments.InstrumentStatus.TRADING,
                asset_class=spec.asset_class,
                source=_SOURCE,
                as_of_ms=now,
                funding_interval_hours=None if spec.is_stock else 8.0,
                max_leverage=None if spec.is_stock else decimal.Decimal(20),
                listing_exchange="DEMO" if spec.is_stock else None,
            )
            for spec in SPECS
        ]

    def _segments(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[tuple[int, int]]:
        """Returns the in-session parts of ``[start_ms, end_ms)``.

        Args:
            instrument: Mock instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending ``(start, end)`` intervals (the whole range for a
            continuous market).
        """
        calendar = sessions.for_instrument(instrument)
        if calendar.continuous:
            return [(start_ms, end_ms)]
        return [
            (max(s.open_ms, start_ms), min(s.close_ms, end_ms))
            for s in calendar.sessions(start_ms, end_ms)
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
            Candle, or ``None`` when the bar has not started (or, for a
            seconds bar without trades, before a later trade fills it).
        """
        spec = self._spec(instrument)
        now = self._clock.now_ms() if now_ms is None else now_ms
        if start_ms > now:
            return None
        if timeframe.is_seconds:
            calendar = sessions.for_instrument(instrument)
            bars = self._seconds_bars(
                instrument,
                timeframe,
                start_ms,
                calendar.end_of(timeframe, start_ms),
                now,
            )
            return bars[0] if bars else None
        return self._kline(spec, instrument, timeframe, start_ms, now)

    def _kline(
        self,
        spec: MockSpec,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        now: int,
    ) -> market.Candle:
        """Builds a minute-or-longer bar by sampling the price path.

        Sub-steps are one minute long up to 1h bars and ``minutes // 60``
        minutes above. Only in-session minutes are sampled, so an exchange
        bar spanning several sessions (``1d`` and above) never sees
        overnight prices.

        Args:
            spec: Instrument parameters.
            instrument: Mock instrument.
            timeframe: Minute or longer resolution.
            start_ms: Aligned bar start.
            now: Current time in epoch milliseconds (bars are partial up to
                it).

        Returns:
            Candle with a taker buy volume.
        """
        seed = self._seeds[spec.symbol]
        end_ms = sessions.for_instrument(instrument).end_of(timeframe, start_ms)
        segments = self._segments(instrument, start_ms, end_ms)
        minutes = sum(b - a for a, b in segments) // _MINUTE_MS
        step = max(1, minutes // 60)
        open_ = _price(spec, seed, segments[0][0] / _MINUTE_MS)
        high = low = open_
        close = open_
        volume = 0.0
        taker = 0.0
        wick = 0.0004 * math.sqrt(step)
        for segment_start, segment_end in segments:
            volume, taker, high, low, close = _sample_span(
                spec,
                seed,
                (
                    segment_start / _MINUTE_MS,
                    min(segment_end, now) / _MINUTE_MS,
                ),
                step=step,
                wick=wick,
                totals=(volume, taker, high, low, close),
            )
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
            taker_buy_base_volume=decimals.floor_to_step(
                decimals.to_decimal(round(taker, 6)), spec.lot
            ),
            taker_buy_method=(
                market.BuyVolumeMethod.CLASSIFIED if spec.is_stock else None
            ),
        )

    def _minute_trades(
        self, instrument: instruments.InstrumentId, minute: int
    ) -> tuple[market.Trade, ...]:
        """Synthesizes the trades of one minute, consistent with its 1m bar.

        Args:
            instrument: Mock instrument.
            minute: Minutes since the epoch.

        Returns:
            Ascending trades (``received_at_ms`` equals the trade time).
        """
        key = (instrument.venue_market_id, minute)
        cached = self._trade_cache.get(key)
        if cached is not None:
            self._trade_cache.move_to_end(key)
            return cached
        spec = self._spec(instrument)
        seed = self._seeds[spec.symbol]
        start = minute * _MINUTE_MS
        candle = self._kline(
            spec, instrument, timeframes.Timeframe.M1, start, start + _MINUTE_MS
        )
        lots = int(candle.base_volume / spec.lot)
        taker = candle.taker_buy_base_volume or decimals.ZERO
        taker_lots = int(taker / spec.lot)
        count = min(6 + int(abs(_unit(seed, 13, minute)) * 40), lots)
        if count <= 0:
            self._trade_cache[key] = ()
            return ()
        # One trade per equal slot of the minute, the first at :00.000 so that
        # it opens the bar.
        width = _MINUTE_MS // count
        offsets = [0] + [
            k * width + int(abs(_unit(seed, 14, minute * 97 + k)) * (width - 1))
            for k in range(1, count)
        ]
        prices = [
            min(
                candle.high,
                max(
                    candle.low,
                    self._round_price(
                        spec, _price(spec, seed, minute + off / _MINUTE_MS)
                    ),
                ),
            )
            for off in offsets
        ]
        prices[0] = candle.open
        prices[-1] = candle.close if count > 1 else candle.open
        # Two distinct interior trades print the bar's high and low.
        if count >= 4:
            i_high = 1 + min(
                count - 3, int(abs(_unit(seed, 17, minute)) * (count - 2))
            )
            i_low = 1 + min(
                count - 3, int(abs(_unit(seed, 18, minute)) * (count - 2))
            )
            if i_low == i_high:
                i_low = i_high + 1 if i_high + 1 <= count - 2 else i_high - 1
            prices[i_high], prices[i_low] = candle.high, candle.low
        share = taker_lots / lots
        buys = _buy_count(count, lots, taker_lots, share)

        def buy_rank(k: int) -> float:
            """Orders a trade for the buy side, likeliest buyer first.

            Upticks rank first, so the aggressor sides look like the tick
            rule would classify them.

            Args:
                k: Trade index within the minute.

            Returns:
                Negated buy score (ascending order puts upticks first).
            """
            uptick = 0.6 if k and prices[k] > prices[k - 1] else 0.0
            return -(_unit(seed, 16, minute * 97 + k) + uptick)

        rank = sorted(range(count), key=buy_rank)
        buy_set = set(rank[:buys])
        buy_parts = iter(_split_lots(taker_lots, buys, seed, 15, minute))
        sell_parts = iter(
            _split_lots(lots - taker_lots, count - buys, seed, 19, minute)
        )
        trades = []
        for k in range(count):
            is_buy = k in buy_set
            parts = buy_parts if is_buy else sell_parts
            ts = start + offsets[k]
            trades.append(
                market.Trade(
                    instrument_id=instrument,
                    trade_id=f"{minute}-{k}",
                    venue_event_ts_ms=ts,
                    received_at_ms=ts,
                    price=prices[k],
                    base_quantity=next(parts) * spec.lot,
                    aggressor_side=(
                        market.AggressorSide.BUY
                        if is_buy
                        else market.AggressorSide.SELL
                    ),
                    is_aggregated=False,
                )
            )
        result = tuple(trades)
        self._trade_cache[key] = result
        while len(self._trade_cache) > _TRADE_CACHE:
            self._trade_cache.popitem(last=False)
        return result

    def _trades_between(
        self, instrument: instruments.InstrumentId, start_ms: int, end_ms: int
    ) -> list[market.Trade]:
        """Returns synthetic trades with time in [start, end).

        At most the newest ``_MAX_TRADE_MINUTES`` minutes are generated.

        Args:
            instrument: Mock instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending trades.
        """
        if end_ms <= start_ms:
            return []
        last = (end_ms - 1) // _MINUTE_MS
        first = max(start_ms // _MINUTE_MS, last - _MAX_TRADE_MINUTES + 1)
        # Exchange instruments trade only in session minutes.
        minutes: list[int] = []
        for segment_start, segment_end in self._segments(
            instrument, first * _MINUTE_MS, (last + 1) * _MINUTE_MS
        ):
            # Ceiling division keeps a minute the segment ends inside.
            minutes.extend(
                range(
                    segment_start // _MINUTE_MS, -(-segment_end // _MINUTE_MS)
                )
            )
        trades: list[market.Trade] = []
        for minute in minutes:
            trades.extend(
                trade
                for trade in self._minute_trades(instrument, minute)
                if start_ms <= trade.venue_event_ts_ms < end_ms
            )
        return trades

    def _seconds_bars(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        first_ms: int,
        stop_ms: int,
        now: int,
    ) -> list[market.Candle]:
        """Folds synthetic trades into seconds bars starting in [first, stop).

        Every minute opens with a trade at ``:00.000``, so the minute that
        contains ``first_ms`` seeds the previous close; the next minute's
        opening trade lets the last bars of the range be filled.

        Args:
            instrument: Mock instrument.
            timeframe: Seconds resolution.
            first_ms: First bar start.
            stop_ms: Exclusive end of bar starts.
            now: Current time (trades after it have not happened).

        Returns:
            Ascending DEMO seconds bars.
        """
        if stop_ms <= first_ms:
            return []
        last_minute = (stop_ms - 1) // _MINUTE_MS
        cutoff = min(now + 1, (last_minute + 1) * _MINUTE_MS + 1)
        origin = max(
            first_ms - first_ms % _MINUTE_MS,
            (last_minute - _MAX_TRADE_MINUTES + 1) * _MINUTE_MS,
        )
        if cutoff <= origin:
            return []
        trades = self._trades_between(instrument, origin, cutoff)
        calendar = sessions.for_instrument(instrument)
        builder = trade_bars.TradeBarBuilder(
            instrument,
            timeframe,
            source=_TRADE_SOURCE,
            feed_state=quality.FeedState.DEMO,
            retention_bars=calendar.bars_between(timeframe, origin, cutoff) + 1,
            dedupe_capacity=len(trades) + 1,
            buy_volume_method=self._buy_method(instrument),
        )
        builder.start_segment(origin)
        out = {c.start_ms: c for c in builder.add(trades, now)}
        for candle in builder.finalize_through(min(stop_ms, now), now):
            out[candle.start_ms] = candle
        return [out[s] for s in sorted(out) if first_ms <= s < stop_ms]

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Generates candles while allowing concurrent API requests to run.

        Long session-history previews must not monopolize the event loop while
        users search instruments or open another chart.

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
        calendar = sessions.for_instrument(instrument)
        first = calendar.ceil(timeframe, start_ms)
        if first >= end_ms or first > now:
            return []
        last = calendar.floor(timeframe, min(end_ms - 1, now))
        if limit is not None:
            first = max(first, calendar.shift(timeframe, last, -(limit - 1)))
        if timeframe.is_seconds:
            rows = self._seconds_bars(
                instrument,
                timeframe,
                first,
                calendar.end_of(timeframe, last),
                now,
            )
            if limit is None:
                return rows
            return rows[-limit:] if limit > 0 else []
        rows = []
        cursor = first
        while cursor <= last:
            candle = self.bar(instrument, timeframe, cursor, now)
            if candle is not None:
                rows.append(candle)
            if len(rows) % 16 == 0:
                await asyncio.sleep(0)
            cursor = calendar.shift(timeframe, cursor, 1)
        return rows

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """Returns synthetic trades that have happened in [start, end).

        Args:
            instrument: Mock instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum number of most recent trades.

        Returns:
            Ascending DEMO trades.
        """
        self._spec(instrument)
        now = self._clock.now_ms()
        trades = self._trades_between(
            instrument, start_ms, min(end_ms, now + 1)
        )
        return trades_lib.keep_latest(trades, limit)

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """Streams synthetic trades as the clock passes them.

        Args:
            instrument: Mock instrument.
            tracker: Optional health tracker.

        Returns:
            Async iterator of DEMO trades.
        """
        self._spec(instrument)
        return self._trade_stream(instrument, tracker)

    async def _trade_stream(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncIterator[market.Trade]:
        """Yields trades from the subscription time on.

        Args:
            instrument: Mock instrument.
            tracker: Optional health tracker.

        Yields:
            Trades in time order.
        """
        cursor = self._clock.now_ms() + 1
        while True:
            now = self._clock.now_ms()
            for trade in self._trades_between(instrument, cursor, now + 1):
                yield trade
            cursor = max(cursor, now + 1)
            if tracker is not None:
                tracker.record_message(now)
            await self._sleep(self._interval)

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

        Perpetuals take the last 24 hourly bars; the DEMO stock's 24h fields
        describe its latest completed session instead.

        Args:
            instrument: Mock instrument.

        Returns:
            Snapshot.
        """
        spec = self._spec(instrument)
        now = self._clock.now_ms()
        last = self._last_price(spec, now)
        if spec.is_stock:
            return await self._stock_snapshot(instrument, spec, now)
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

    def _quotes(
        self, spec: MockSpec, last: decimal.Decimal
    ) -> tuple[decimal.Decimal, decimal.Decimal]:
        """Returns the DEMO best bid and ask around a price.

        Args:
            spec: Instrument parameters.
            last: Reference price.

        Returns:
            ``(bid, ask)``.
        """
        half = last * spec.spread_bps / decimal.Decimal(20000)
        bid = decimals.floor_to_step(last - half, spec.tick)
        ask = bid + max(spec.tick, decimals.round_to_step(half * 2, spec.tick))
        return bid, ask

    async def _stock_snapshot(
        self,
        instrument: instruments.InstrumentId,
        spec: MockSpec,
        now: int,
    ) -> market.MarketSnapshot:
        """Builds the DEMO stock snapshot (no funding, no open interest).

        The 24h fields describe the latest completed session; between
        sessions the last price is that session's close.

        Args:
            instrument: Mock stock.
            spec: Instrument parameters.
            now: Current time.

        Returns:
            Snapshot.
        """
        calendar = sessions.for_instrument(instrument)
        day = timeframes.Timeframe.D1
        closed = calendar.last_closed_start(day, now)
        rows: list[market.Candle] = []
        if closed is not None:
            rows = await self.fetch_candles(
                instrument, day, closed, calendar.end_of(day, closed)
            )
        session = rows[-1] if rows else None
        if calendar.is_open(now):
            last = self._last_price(spec, now)
        elif session is not None:
            last = session.close
        else:
            last = self._last_price(spec, now)
        bid, ask = self._quotes(spec, last)
        turnover: decimal.Decimal | None = None
        change_pct: decimal.Decimal | None = None
        if session is not None:
            turnover = decimals.round_to_step(
                session.base_volume * session.close, decimal.Decimal("0.01")
            )
            change_pct = (
                (session.close - session.open) / session.open * decimals.HUNDRED
            )
        return market.MarketSnapshot(
            instrument_id=instrument,
            feed_state=quality.FeedState.DEMO,
            source=_SOURCE,
            as_of_ms=now,
            last=last,
            mark=last,
            best_bid=bid,
            best_ask=ask,
            turnover_24h_quote=turnover,
            volume_24h_base=None if session is None else session.base_volume,
            change_24h_pct=change_pct,
        )

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """Builds snapshots for all DEMO instruments.

        Returns:
            Snapshots.
        """
        rows = []
        for spec in SPECS:
            rows.append(await self.fetch_snapshot(spec.instrument_id()))
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
            depth: Levels per side (at least one is built).

        Returns:
            Order book.
        """
        snapshot = await self.fetch_snapshot(instrument)
        spec = self._spec(instrument)
        seed = self._seeds[spec.symbol]
        if snapshot.best_bid is None:
            raise AssertionError("DEMO snapshots always quote a best bid")
        if snapshot.best_ask is None:
            raise AssertionError("DEMO snapshots always quote a best ask")
        # Levels sit 0.5 bp of the bid apart, at least one tick.
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

        Raises:
            errors.NotFoundError: If not a mock instrument.
            errors.UnsupportedError: For the DEMO stock.
        """
        spec = self._spec(instrument)
        if spec.is_stock:
            raise errors.UnsupportedError(
                f"{instrument} is a stock: stocks have no open interest"
            )
        snapshot = await self.fetch_snapshot(instrument)
        if snapshot.open_interest is None:
            raise AssertionError("DEMO perpetual snapshots carry open interest")
        return snapshot.open_interest

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """Returns DEMO funding.

        Args:
            instrument: Mock instrument.

        Returns:
            Predicted funding for the next 8h boundary.

        Raises:
            errors.NotFoundError: If not a mock instrument.
            errors.UnsupportedError: For the DEMO stock.
        """
        spec = self._spec(instrument)
        if spec.is_stock:
            raise errors.UnsupportedError(
                f"{instrument} is a stock: stocks have no funding rate"
            )
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
        if timeframe.is_seconds:
            async for candle in self._stream_seconds(
                instrument, timeframe, tracker
            ):
                yield candle
            return
        calendar = sessions.for_instrument(instrument)
        current = calendar.floor(timeframe, self._clock.now_ms())
        while True:
            now = self._clock.now_ms()
            boundary = calendar.floor(timeframe, now)
            while current < boundary:
                closed = self.bar(instrument, timeframe, current, now)
                if closed is not None:
                    yield closed
                current = calendar.shift(timeframe, current, 1)
            live = self.bar(instrument, timeframe, current, now)
            if tracker is not None:
                tracker.record_message(now)
            if live is not None:
                yield live
            await self._sleep(self._interval)

    async def _stream_seconds(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncIterator[market.Candle]:
        """Streams seconds bars folded from synthetic trades on the clock.

        Args:
            instrument: Mock instrument.
            timeframe: Seconds resolution.
            tracker: Optional health tracker.

        Yields:
            Live and final seconds bars, like a venue trade stream.
        """
        now = self._clock.now_ms()
        cursor = now - now % _MINUTE_MS
        builder = trade_bars.TradeBarBuilder(
            instrument,
            timeframe,
            source=_TRADE_SOURCE,
            feed_state=quality.FeedState.DEMO,
            buy_volume_method=self._buy_method(instrument),
        )
        builder.start_segment(cursor)
        while True:
            now = self._clock.now_ms()
            trades = self._trades_between(instrument, cursor, now + 1)
            cursor = max(cursor, now + 1)
            for candle in builder.add(trades, now):
                yield candle
            for candle in builder.finalize_through(now, now):
                yield candle
            if tracker is not None:
                tracker.record_message(now)
            await self._sleep(self._interval)

    def _buy_method(
        self, instrument: instruments.InstrumentId
    ) -> market.BuyVolumeMethod | None:
        """Returns the buy-volume label of an instrument's bars.

        Args:
            instrument: Mock instrument.

        Returns:
            ``classified`` for the DEMO stock, else ``None`` (taker flag).
        """
        if self._spec(instrument).is_stock:
            return market.BuyVolumeMethod.CLASSIFIED
        return None

    async def close(self) -> None:
        """Does nothing: the DEMO venue holds no resources."""
