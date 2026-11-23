"""Unit tests: the market-data service serves recorded trades."""

from __future__ import annotations

import decimal

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import quality
from joseph.domain import timeframes
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service
from joseph.market_data.app import trade_archive
from joseph.market_data.domain import capabilities

_TF = timeframes.Timeframe
_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("mexc:perp:BTC_USDT")
_T0 = 1_795_349_040_000
"""A minute boundary (2026-11-22)."""


def _bar(
    start: int, tf: timeframes.Timeframe, volume: str = "1", final: bool = True
) -> market.Candle:
    """Builds a bar without taker volume.

    Args:
        start: Bar start.
        tf: Timeframe.
        volume: Base volume.
        final: Finality.

    Returns:
        Candle.
    """
    return market.Candle(
        instrument_id=_ID,
        timeframe=tf,
        start_ms=start,
        end_ms=tf.end_of(start),
        open=_D(10),
        high=_D(10),
        low=_D(10),
        close=_D(10),
        base_volume=_D(volume),
        is_final=final,
        source="mexc.rest",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=start,
    )


class StreamOnlyAdapter:
    """MEXC-like double: klines over REST, trades only from the stream."""

    venue = instruments.Venue.MEXC

    def __init__(self, minutes: list[market.Candle]) -> None:
        """Initializes the double.

        Args:
            minutes: 1m bars served over REST.
        """
        self.minutes = minutes
        self.fetches: list[timeframes.Timeframe] = []

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns capabilities without a trade history.

        Returns:
            Capabilities.
        """
        return capabilities.VenueCapabilities(
            venue=self.venue,
            display_name="fake",
            market_types=(instruments.MarketType.PERP,),
            market_data=capabilities.MarketDataCapabilities(
                rest_history=True, native_ws_trades=True
            ),
            timeframes=(_TF.S1, _TF.M1),
            trade_built_timeframes=(_TF.S1,),
            notes=(),
            docs_url="x",
        )

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Serves 1m bars (seconds have no REST history here).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Start.
            end_ms: End.
            limit: Limit.

        Returns:
            Bars.
        """
        del instrument, limit
        self.fetches.append(timeframe)
        return [c for c in self.minutes if start_ms <= c.start_ms < end_ms]


def _service(
    clock: clock_lib.Clock,
    adapter: StreamOnlyAdapter,
    archive: trade_archive.MemoryTradeArchive,
) -> tuple[service.MarketDataService, candle_store.CandleStore]:
    """Builds a service with a recorded-trade reader.

    Args:
        clock: Clock.
        adapter: Venue adapter.
        archive: Archive.

    Returns:
        Service and its store.
    """
    adapters = {adapter.venue: adapter}
    store = candle_store.CandleStore()
    router = feed_router.FeedRouter(adapters, store, clock)  # type: ignore[arg-type]
    reader = trade_archive.ArchiveReader(
        archive, clock, delta_venues=[instruments.Venue.MEXC]
    )
    svc = service.MarketDataService(
        adapters,  # type: ignore[arg-type]
        registry.InstrumentRegistry(adapters),  # type: ignore[arg-type]
        store,
        router,
        clock,
        recorded=reader,
    )
    return svc, store


async def test_archived_seconds_survive_a_restart_behind_live_bars() -> None:
    """After a restart, older recorded seconds fill in behind live bars."""
    clock = clock_lib.ManualClock(_T0 + 10_000)
    archive = trade_archive.MemoryTradeArchive()
    archive.put(
        _ID,
        [_bar(_T0, _TF.S1), _bar(_T0 + 2_000, _TF.S1)],
        [(_T0, _T0 + 5_000)],
    )
    svc, store = _service(clock, StreamOnlyAdapter([]), archive)
    store.upsert(_bar(_T0 + 8_000, _TF.S1))
    batch = await svc.get_batch(_ID, _TF.S1, from_ms=_T0, to_ms=_T0 + 9_000)
    starts = [c.start_ms - _T0 for c in batch.candles]
    assert starts == [0, 1_000, 2_000, 3_000, 4_000, 8_000]
    assert batch.gaps == ((_T0 + 5_000, _T0 + 8_000),)


async def test_minute_bars_carry_recorded_delta_when_covered() -> None:
    """A fully covered 1m bar gets true delta; the next one does not."""
    clock = clock_lib.ManualClock(_T0 + 180_000)
    archive = trade_archive.MemoryTradeArchive()
    second = market.Candle(
        instrument_id=_ID,
        timeframe=_TF.S1,
        start_ms=_T0 + 3_000,
        end_ms=_T0 + 4_000,
        open=_D(10),
        high=_D(10),
        low=_D(10),
        close=_D(10),
        base_volume=_D(1),
        is_final=True,
        source="mexc.trades",
        feed_state=quality.FeedState.LIVE,
        received_at_ms=0,
        taker_buy_base_volume=_D("0.25"),
    )
    archive.put(_ID, [second], [(_T0, _T0 + 90_000)])
    minutes = [_bar(_T0, _TF.M1), _bar(_T0 + 60_000, _TF.M1, "2")]
    svc, _ = _service(clock, StreamOnlyAdapter(minutes), archive)
    batch = await svc.get_batch(_ID, _TF.M1, from_ms=_T0, to_ms=_T0 + 120_000)
    assert [c.taker_buy_base_volume for c in batch.candles] == [
        _D("0.25"),
        None,
    ]
    closed = await svc.get_candles(_ID, _TF.M1, _T0, _T0 + 120_000)
    assert closed[0].taker_buy_method is market.BuyVolumeMethod.TAKER
