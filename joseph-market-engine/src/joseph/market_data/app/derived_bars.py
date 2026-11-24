# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Engine-derived bars layered over a venue adapter (never fabricated).

``DerivedBarsAdapter`` wraps a venue ``MarketDataPort`` and adds two things
the engine derives exactly from venue data:

* **Aggregated timeframes.** A timeframe the venue lacks but a shorter
  native timeframe tiles exactly on the venue's calendar (``1M`` from
  ``1d`` on Hyperliquid and Lighter, ``6h`` from ``2h``, ...) is declared in
  ``VenueCapabilities.aggregated_timeframes``. History folds the native
  child bars of each period (``domain.aggregation``); the live stream seeds
  the current period's children over REST and then folds every update of
  the child stream, so the live month is its closed days plus the current
  day's updates. A period missing a child bar is a gap, not a guess.
* **True delta from recorded trades.** On venues whose klines carry no
  taker volume, final minute-and-above bars whose whole span is covered by
  trades the ``TradeRecorder`` archived get ``taker_buy_base_volume`` with
  ``taker_buy_method = taker`` (``MarketDataCapabilities.trade_delta``). A
  bar that just closed waits briefly for the recorder to prove its last
  second. Every other bar keeps no taker volume.

Everything else is delegated unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.app import ports
from joseph.market_data.app import trade_recorder
from joseph.market_data.domain import aggregation
from joseph.market_data.domain import capabilities as capabilities_lib
from joseph.market_data.domain import health

_RETRY_MISSING_MS = 10_000


def _clip(
    rows: Sequence[market.Candle], start_ms: int, end_ms: int, limit: int | None
) -> list[market.Candle]:
    """Keeps bars starting in ``[start, end)``, newest ``limit``.

    Args:
        rows: Ascending bars.
        start_ms: Inclusive start.
        end_ms: Exclusive end.
        limit: Optional maximum.

    Returns:
        Ascending bars.
    """
    kept = [c for c in rows if start_ms <= c.start_ms < end_ms]
    return kept[-limit:] if limit is not None else kept


class DerivedBarsAdapter:
    """``MarketDataPort`` adding aggregated timeframes and recorded delta."""

    def __init__(
        self,
        inner: ports.MarketDataPort,
        clock: clock_lib.Clock,
        *,
        recorder: trade_recorder.TradeRecorder | None = None,
        delta_wait_s: float = 4.0,
    ) -> None:
        """Initializes the wrapper.

        Args:
            inner: Venue adapter.
            clock: Time source.
            recorder: Optional trade recorder (true delta, notes).
            delta_wait_s: Longest wait for the recorder at a bar close.
        """
        self.inner = inner
        self._clock = clock
        self._recorder = recorder
        self._delta_wait_s = delta_wait_s

    @property
    def venue(self) -> instruments.Venue:
        """Returns the venue served by the wrapped adapter."""
        return self.inner.venue

    # Capabilities --------------------------------------------------------

    @staticmethod
    def derived(
        caps: capabilities_lib.VenueCapabilities,
    ) -> dict[timeframes.Timeframe, timeframes.Timeframe]:
        """Maps each timeframe the engine aggregates to its native child.

        Args:
            caps: Capabilities of the wrapped adapter.

        Returns:
            ``{aggregated timeframe: native child timeframe}``.
        """
        if not caps.market_data.rest_history:
            return {}
        native = [
            tf
            for tf in caps.timeframes
            if tf not in caps.trade_built_timeframes
            and tf not in caps.aggregated_timeframes
        ]
        return aggregation.derived_timeframes(
            sessions.for_venue(caps.venue), native
        )

    def _delta(self, caps: capabilities_lib.VenueCapabilities) -> bool:
        """Checks whether recorded true delta applies to a venue.

        Args:
            caps: Capabilities of the wrapped adapter.

        Returns:
            True when trades are recorded and klines lack taker volume.
        """
        return self._recorder is not None and (
            caps.venue in self._recorder.delta_venues
        )

    def capabilities(self) -> capabilities_lib.VenueCapabilities:
        """Returns the venue's capabilities plus what the engine derives.

        Returns:
            Capabilities.
        """
        caps = self.inner.capabilities()
        derived = self.derived(caps)
        delta = self._delta(caps)
        records = self._recorder is not None and (
            self._recorder.records_venue(caps.venue)
        )
        if not derived and not delta and not records:
            return caps
        notes = list(caps.notes)
        if derived:
            pairs = ", ".join(
                f"{tf.value} from {child.value}"
                for tf, child in derived.items()
            )
            notes.append(
                f"Aggregated by the engine, equal to native bars: {pairs}. "
                "A period missing a child bar is reported as a gap."
            )
        if delta:
            notes.append(
                "1m-and-above bars whose whole span is covered by recorded "
                "trades carry a true delta summed from the trades' aggressor "
                "side (trade_delta); other bars keep no taker volume, so "
                "indicators use their labelled estimate."
            )
        if records and self._recorder is not None:
            notes.append(
                trade_recorder.recording_settings_note(self._recorder.settings)
            )
            notes.extend(self._recorder.history_notes(caps.venue))
        return dataclasses.replace(
            caps,
            timeframes=tuple(
                tf
                for tf in timeframes.Timeframe
                if tf in caps.timeframes or tf in derived
            ),
            aggregated_timeframes=tuple(
                tf
                for tf in timeframes.Timeframe
                if tf in caps.aggregated_timeframes or tf in derived
            ),
            market_data=dataclasses.replace(
                caps.market_data,
                trade_delta=caps.market_data.trade_delta or delta,
            ),
            notes=tuple(notes),
        )

    # Candles -------------------------------------------------------------

    async def _with_delta(
        self,
        rows: Sequence[market.Candle],
        caps: capabilities_lib.VenueCapabilities,
    ) -> list[market.Candle]:
        """Attaches recorded delta, waiting briefly at a fresh bar close.

        Args:
            rows: Bars of one series.
            caps: Capabilities of the wrapped adapter.

        Returns:
            Bars in the same order.
        """
        recorder = self._recorder
        if recorder is None or not rows or not self._delta(caps):
            return list(rows)
        instrument = rows[0].instrument_id
        now = self._clock.now_ms()
        horizon = int(self._delta_wait_s * 2_000)
        fresh = [
            c.end_ms
            for c in rows
            if c.is_final
            and not c.timeframe.is_seconds
            and c.taker_buy_base_volume is None
            and now - c.end_ms < horizon
        ]
        if fresh and recorder.recording(instrument):
            await recorder.wait_recorded(
                instrument, max(fresh), self._delta_wait_s
            )
        return recorder.enrich(rows)

    async def fetch_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Candle]:
        """Fetches native or aggregated bars (see ``MarketDataPort``).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            limit: Optional maximum bars.

        Returns:
            Ascending candles.
        """
        caps = self.inner.capabilities()
        child = self.derived(caps).get(timeframe)
        if child is None:
            rows = await self.inner.fetch_candles(
                instrument, timeframe, start_ms, end_ms, limit
            )
            return await self._with_delta(rows, caps)
        calendar = sessions.for_instrument(instrument)
        first = calendar.floor(timeframe, start_ms)
        last = calendar.floor(timeframe, end_ms - 1)
        if limit is not None:
            first = max(first, calendar.shift(timeframe, last, -(limit - 1)))
        children = await self.inner.fetch_candles(
            instrument, child, first, calendar.end_of(timeframe, last)
        )
        children = await self._with_delta(children, caps)
        parents = aggregation.aggregate(
            children,
            calendar=calendar,
            target=timeframe,
            child=child,
            now_ms=self._clock.now_ms(),
        )
        return _clip(parents, start_ms, end_ms, limit)

    def stream_candles(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Candle]:
        """Streams native or aggregated bars (see ``MarketDataPort``).

        Args:
            instrument: Instrument.
            timeframe: Resolution.
            tracker: Optional health tracker.

        Returns:
            Async iterator of candle updates.

        Raises:
            errors.UnsupportedError: When the venue has no native stream of
                the (child) timeframe.
        """
        caps = self.inner.capabilities()
        child = self.derived(caps).get(timeframe)
        if child is None:
            stream = self.inner.stream_candles(instrument, timeframe, tracker)
            if timeframe.is_seconds or not self._delta(caps):
                return stream
            return self._delta_stream(stream, caps)
        if not caps.market_data.native_ws_candles:
            raise errors.UnsupportedError(
                f"{caps.venue.value} has no native {child.value} candle "
                f"stream to aggregate {timeframe.value} from"
            )
        return self._aggregated_stream(instrument, timeframe, child, tracker)

    async def _delta_stream(
        self,
        stream: AsyncIterator[market.Candle],
        caps: capabilities_lib.VenueCapabilities,
    ) -> AsyncGenerator[market.Candle, None]:
        """Adds recorded delta to finalized bars of a native stream.

        Args:
            stream: Native candle stream.
            caps: Capabilities of the wrapped adapter.

        Yields:
            Candle updates (finals enriched when covered).
        """
        async with trade_recorder.closing(stream):
            async for update in stream:
                if update.is_final and update.taker_buy_base_volume is None:
                    yield (await self._with_delta([update], caps))[0]
                else:
                    yield update

    async def _aggregated_stream(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        child: timeframes.Timeframe,
        tracker: health.FeedHealthTracker | None,
    ) -> AsyncGenerator[market.Candle, None]:
        """Streams aggregated bars from the child stream.

        Args:
            instrument: Instrument.
            timeframe: Aggregated timeframe.
            child: Native child timeframe.
            tracker: Optional health tracker (of the child stream).

        Yields:
            Parent updates (live and finalized).
        """
        caps = self.inner.capabilities()
        calendar = sessions.for_instrument(instrument)
        live = aggregation.LiveAggregator(calendar, timeframe, child)
        now = self._clock.now_ms()
        parent = calendar.floor(timeframe, now)
        seed = await self.inner.fetch_candles(
            instrument, child, parent, calendar.end_of(timeframe, parent)
        )
        for bar in live.update(await self._with_delta(seed, caps), now):
            yield bar
        retried_ms: int | None = None
        stream = self.inner.stream_candles(instrument, child, tracker)
        async with trade_recorder.closing(stream):
            async for update in stream:
                now = self._clock.now_ms()
                missing = live.missing_before(update.start_ms)
                if missing and (
                    retried_ms is None or now - retried_ms >= _RETRY_MISSING_MS
                ):
                    retried_ms = now
                    try:
                        rows = await self.inner.fetch_candles(
                            instrument, child, missing[0], update.start_ms
                        )
                    except errors.JosephError as err:
                        if tracker is not None:
                            tracker.record_error(
                                now, f"child backfill failed: {err.message}"
                            )
                        rows = []
                    for bar in live.update(
                        await self._with_delta(rows, caps), now
                    ):
                        yield bar
                for bar in live.update(
                    await self._with_delta([update], caps), now
                ):
                    yield bar

    # Delegated -----------------------------------------------------------

    async def list_instruments(self) -> list[instruments.Instrument]:
        """See ``MarketDataPort.list_instruments``."""
        return await self.inner.list_instruments()

    async def fetch_snapshot(
        self, instrument: instruments.InstrumentId
    ) -> market.MarketSnapshot:
        """See ``MarketDataPort.fetch_snapshot``."""
        return await self.inner.fetch_snapshot(instrument)

    async def fetch_snapshots(self) -> list[market.MarketSnapshot]:
        """See ``MarketDataPort.fetch_snapshots``."""
        return await self.inner.fetch_snapshots()

    async def fetch_order_book(
        self, instrument: instruments.InstrumentId, depth: int
    ) -> market.OrderBook:
        """See ``MarketDataPort.fetch_order_book``."""
        return await self.inner.fetch_order_book(instrument, depth)

    async def fetch_open_interest(
        self, instrument: instruments.InstrumentId
    ) -> market.OpenInterest:
        """See ``MarketDataPort.fetch_open_interest``."""
        return await self.inner.fetch_open_interest(instrument)

    async def fetch_funding(
        self, instrument: instruments.InstrumentId
    ) -> market.Funding:
        """See ``MarketDataPort.fetch_funding``."""
        return await self.inner.fetch_funding(instrument)

    def stream_trades(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
    ) -> AsyncIterator[market.Trade]:
        """See ``MarketDataPort.stream_trades``."""
        return self.inner.stream_trades(instrument, tracker)

    async def fetch_trades(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> list[market.Trade]:
        """See ``MarketDataPort.fetch_trades``."""
        return await self.inner.fetch_trades(
            instrument, start_ms, end_ms, limit
        )

    async def close(self) -> None:
        """See ``MarketDataPort.close``."""
        await self.inner.close()


def wrap(
    adapters: Mapping[instruments.Venue, ports.MarketDataPort],
    clock: clock_lib.Clock,
    *,
    recorder: trade_recorder.TradeRecorder | None = None,
) -> dict[instruments.Venue, ports.MarketDataPort]:
    """Wraps every crypto venue adapter.

    DEMO, exchange venues (TWSE, TPEx, Nasdaq, NYSE, Cboe) and rate venues
    (Treasury) are left as is: their adapters aggregate on their own session
    calendars (where a minute without trades has no bar, so a missing child
    is not a gap), and they publish no aggressor side, so recorded trades
    must never add a taker delta to their classified bars.

    Args:
        adapters: Raw adapters.
        clock: Time source.
        recorder: Optional trade recorder.

    Returns:
        Adapters in the same order.
    """
    unwrapped = {
        instruments.Venue.MOCK,
        *instruments.EXCHANGE_VENUES,
        *instruments.RATE_VENUES,
    }
    return {
        venue: (
            adapter
            if venue in unwrapped
            else DerivedBarsAdapter(adapter, clock, recorder=recorder)
        )
        for venue, adapter in adapters.items()
    }
