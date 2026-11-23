"""Trade recorder: archives venues' public trades with explicit coverage.

Seconds history and true delta on minute-and-above bars both need the
venue's public trades, which most venues keep only briefly (Binance 48 h of
REST history, Bitget 90 days, Lighter about 53 minutes, MEXC and Hyperliquid
none). The recorder keeps them in a ``TradeArchive`` as complete 1s
aggregates plus coverage intervals, so history accumulates across restarts:

* **Live recording** runs per instrument while the engine uses it: every
  candle a feed session produces ``touch``es its instrument (a lease), and
  ``always`` instruments record permanently. It consumes the venue's
  coverage-tagged trade stream (``stream_trade_events``): a segment starts
  at a proven venue time (a REST backfill) or at the first trade received,
  and a second is complete once delivery is proven past it (any frame,
  minus a grace). Outages that cannot be backfilled end the segment, so the
  archive never bridges them.
* **Backfill** runs when recording starts and, on startup, for every
  archived instrument: the gap since the newest coverage is walked back
  from now over the venue's REST trade history with a request budget.
  Complete past days come from official day archives where one exists
  (Binance ``data.binance.vision``, sha256 verified; Bitget's public trade
  archive, verified against its own 1D kline). What the budget or the
  venue's window cannot reach stays uncovered and is reported as gaps by
  candle batches.
* **Retention** prunes aggregates and coverage older than
  ``retention_ms`` (``JOSEPH_TRADE_BAR_RETENTION_DAYS``).

The recorder is also the ``RecordedTrades`` read side of the service:
archived seconds bars (importing archive days on demand) and
true delta for fully covered bars.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import logging
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol
from typing import runtime_checkable

from joseph.domain import clock as clock_lib
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import ws
from joseph.market_data.app import ports
from joseph.market_data.app import trade_archive
from joseph.market_data.domain import capabilities
from joseph.market_data.domain import health
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_LOG = logging.getLogger(__name__)
_S1 = timeframes.Timeframe.S1
_SECOND = recorded_trades.SECOND_MS
_DAY_MS = 86_400_000
_PUBLISH_LAG_MS = 1_000
_WAIT_STEP_S = 0.05
_RETRY_FAILED_MS = 1_800_000

Sleep = Callable[[float], Awaitable[None]]
Sink = Callable[[list[market.Candle], list[recorded_trades.Interval]], None]


@contextlib.asynccontextmanager
async def closing[T](
    stream: AsyncIterator[T],
) -> AsyncGenerator[AsyncIterator[T], None]:
    """Closes an async iterator (usually an async generator) on exit.

    Args:
        stream: Async iterator.

    Yields:
        The same iterator.
    """
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


@runtime_checkable
class TradeEventPort(Protocol):
    """Adapter surface the recorder needs (``BaseVenueAdapter`` has it)."""

    def capabilities(self) -> capabilities.VenueCapabilities:
        """Returns the venue capabilities."""
        ...

    def stream_trade_events(
        self,
        instrument: instruments.InstrumentId,
        tracker: health.FeedHealthTracker | None = None,
        *,
        prime_from_ms: int | None = None,
    ) -> AsyncIterator[ws.TradeStreamEvent]:
        """Streams coverage-tagged trade events.

        Args:
            instrument: Instrument.
            tracker: Optional health tracker.
            prime_from_ms: Optional first-connection backfill start.
        """
        ...

    async def fetch_trade_coverage(
        self,
        instrument: instruments.InstrumentId,
        start_ms: int,
        end_ms: int,
        max_pages: int | None = None,
    ) -> trades_lib.TradeCoverage:
        """Fetches the newest complete trades of a range.

        Args:
            instrument: Instrument.
            start_ms: Inclusive start.
            end_ms: Exclusive end.
            max_pages: Request budget.
        """
        ...


class DayArchive(Protocol):
    """Official per-day trade archive of one venue.

    Implemented by ``BinanceVision`` (UTC days) and ``BitgetArchive``
    (days cut at 00:00 UTC+8).

    Attributes:
        venue: Venue whose trades the archive holds.
        note: Capability note describing the archive.
    """

    venue: instruments.Venue
    note: str

    def day_of(self, ts_ms: int) -> datetime.date:
        """Returns the archive day containing a time.

        Args:
            ts_ms: Epoch milliseconds.
        """
        ...

    def day_bounds(self, day: datetime.date) -> recorded_trades.Interval:
        """Returns the ``[start, end)`` epoch milliseconds of a day.

        Args:
            day: Archive day.
        """
        ...

    async def import_day(
        self,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: Sink,
        received_ms: int,
    ) -> bool:
        """Archives one complete past day of trades.

        Args:
            instrument: Instrument.
            day: UTC date.
            sink: Archive writer.
            received_ms: Receipt time.
        """
        ...

    def prune(self, before_ms: int) -> int:
        """Deletes cached days before a cut-off.

        Args:
            before_ms: Cut-off.
        """
        ...

    async def close(self) -> None:
        """Releases network resources."""
        ...


@dataclasses.dataclass(frozen=True, slots=True)
class RecorderSettings:
    """Recording policy.

    Attributes:
        retention_ms: Archived history kept (older data is pruned).
        max_instruments: Instruments recorded live at the same time.
        backfill_max_calls: REST trade-history calls per backfill (each up
            to the adapter's page budget).
        lease_ms: How long a touch keeps an instrument recording.
        flush_interval_ms: Archive write interval while recording.
        grace_ms: Delivery grace before a second counts as complete.
        retry_s: Delay before restarting a failed trade stream.
        supervise_s: Interval of lease expiry and retention checks.
        prune_interval_ms: Interval between retention prunes.
        archive_backfill_days: Past days imported from an official day
            archive when recording starts.
        archive_request_days: Past days imported on demand per request.
        always: Instruments recorded permanently.
    """

    retention_ms: int = 30 * _DAY_MS
    max_instruments: int = 16
    backfill_max_calls: int = 20
    lease_ms: int = 300_000
    flush_interval_ms: int = 2_000
    grace_ms: int = 1_500
    retry_s: float = 30.0
    supervise_s: float = 30.0
    prune_interval_ms: int = 3_600_000
    archive_backfill_days: int = 1
    archive_request_days: int = 2
    always: tuple[instruments.InstrumentId, ...] = ()


@dataclasses.dataclass
class _Recording:
    """Live recording state of one instrument.

    Attributes:
        instrument: Instrument.
        tracker: Health of the recording stream (gaps, reconnects).
        lease_until_ms: Recording stops after this unless touched again.
        task: Recording task.
        builder: 1s builder of the current coverage segment.
        segment_from: Whole-second start of the current segment.
        proven: Whole-second end up to which delivery is proven.
        pending: Complete 1s aggregates not yet written.
        flushed_ms: Clock time of the last archive write.
    """

    instrument: instruments.InstrumentId
    tracker: health.FeedHealthTracker
    lease_until_ms: int = 0
    task: asyncio.Task[None] | None = None
    builder: trade_bars.TradeBarBuilder | None = None
    segment_from: int | None = None
    proven: int | None = None
    pending: dict[int, market.Candle] = dataclasses.field(default_factory=dict)
    flushed_ms: int = 0


class TradeRecorder:
    """Records public trades into a ``TradeArchive`` (see module docs).

    Attributes:
        archive: Archive written and read.
        reader: Read side (seconds bars and true delta).
        settings: Recording policy.
    """

    def __init__(
        self,
        adapters: Mapping[instruments.Venue, ports.MarketDataPort],
        archive: trade_archive.TradeArchive,
        clock: clock_lib.Clock,
        settings: RecorderSettings | None = None,
        *,
        day_archives: Sequence[DayArchive] = (),
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Initializes a stopped recorder.

        Args:
            adapters: Venue adapters (raw, not wrapped).
            archive: Archive.
            clock: Time source.
            settings: Recording policy.
            day_archives: Official day archives (one per venue).
            sleep: Sleep function (injectable for tests).
        """
        self.archive = archive
        self.settings = settings or RecorderSettings()
        self._clock = clock
        self._sleep = sleep
        self._day_archives = {a.venue: a for a in day_archives}
        self._ports: dict[instruments.Venue, TradeEventPort] = {
            venue: adapter
            for venue, adapter in adapters.items()
            if venue is not instruments.Venue.MOCK
            and isinstance(adapter, TradeEventPort)
            and adapter.capabilities().market_data.native_ws_trades
        }
        self.reader = trade_archive.ArchiveReader(
            archive,
            clock,
            delta_venues=[
                venue
                for venue, port in self._ports.items()
                if not port.capabilities().market_data.taker_volume
            ],
        )
        self._recordings: dict[instruments.InstrumentId, _Recording] = {}
        self._failed_days: dict[
            tuple[instruments.InstrumentId, datetime.date], int
        ] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._started = False
        self._last_prune_ms: int | None = None

    # Lifecycle -----------------------------------------------------------

    @property
    def delta_venues(self) -> frozenset[instruments.Venue]:
        """Returns venues whose 1m+ bars get recorded true delta."""
        return self.reader.delta_venues

    def recordable(self, instrument: instruments.InstrumentId) -> bool:
        """Checks whether an instrument's venue streams recordable trades.

        Args:
            instrument: Instrument.

        Returns:
            True when its venue has a native trade stream.
        """
        return instrument.venue in self._ports

    def records_venue(self, venue: instruments.Venue) -> bool:
        """Checks whether a venue's trades can be recorded.

        Args:
            venue: Venue.

        Returns:
            True when its adapter streams coverage-tagged trades.
        """
        return venue in self._ports

    def history_notes(self, venue: instruments.Venue) -> tuple[str, ...]:
        """Describes where a venue's recorded history comes from.

        Args:
            venue: Venue.

        Returns:
            Capability notes (empty when the venue is not recorded).
        """
        port = self._ports.get(venue)
        if port is None:
            return ()
        notes: list[str] = []
        if port.capabilities().market_data.trades_history:
            notes.append(
                "Recording backfills the gap since the last recorded trade "
                "over the REST trade history, within a request budget "
                f"({self.settings.backfill_max_calls} calls)."
            )
        else:
            notes.append(
                "The venue publishes no trade history, so seconds bars and "
                "true delta exist only where trades were recorded live."
            )
        archive = self._day_archives.get(venue)
        if archive is not None:
            notes.append(archive.note)
        return tuple(notes)

    def start(self) -> None:
        """Starts supervision: startup backfill, retention, always-on."""
        if self._started:
            return
        self._started = True
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        """Stops every recording (flushing it) and supervision."""
        self._started = False
        tasks = [r.task for r in self._recordings.values() if r.task]
        if self._supervisor is not None:
            tasks.append(self._supervisor)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for recording in self._recordings.values():
            self._close_segment(recording)
        self._recordings.clear()
        self._supervisor = None
        for archive in self._day_archives.values():
            await archive.close()

    def touch(self, instrument: instruments.InstrumentId) -> bool:
        """Starts or extends recording of an instrument in use.

        Args:
            instrument: Instrument.

        Returns:
            True when the instrument is being recorded.
        """
        if not self._started or not self.recordable(instrument):
            return False
        now = self._clock.now_ms()
        recording = self._recordings.get(instrument)
        if recording is None:
            if len(self._recordings) >= self.settings.max_instruments:
                return False
            recording = _Recording(
                instrument=instrument,
                tracker=health.FeedHealthTracker(instrument.venue),
            )
            self._recordings[instrument] = recording
            recording.task = asyncio.create_task(self._run(recording))
        recording.lease_until_ms = max(
            recording.lease_until_ms, now + self.settings.lease_ms
        )
        return True

    def recording(self, instrument: instruments.InstrumentId) -> bool:
        """Checks whether an instrument is being recorded live.

        Args:
            instrument: Instrument.

        Returns:
            True while its recording runs.
        """
        return instrument in self._recordings

    def health(self) -> list[health.FeedHealth]:
        """Returns the health of every live recording.

        Returns:
            Health snapshots (gaps count unrecoverable outages).
        """
        now = self._clock.now_ms()
        return [r.tracker.snapshot(now) for r in self._recordings.values()]

    async def _supervise(self) -> None:
        """Runs startup work, then expires leases and prunes periodically."""
        self.prune_now()
        for instrument in self.settings.always:
            if self.touch(instrument):
                self._recordings[instrument].lease_until_ms = 2**62
        for instrument in self.archive.instruments():
            if instrument not in self._recordings and self.recordable(
                instrument
            ):
                await self._safe_backfill(instrument)
        while True:
            await self._sleep(self.settings.supervise_s)
            self.expire()
            now = self._clock.now_ms()
            if (
                self._last_prune_ms is None
                or now - self._last_prune_ms >= self.settings.prune_interval_ms
            ):
                self.prune_now()

    def expire(self) -> list[instruments.InstrumentId]:
        """Stops recordings whose lease ran out.

        Returns:
            Instruments that stopped recording.
        """
        now = self._clock.now_ms()
        stopped = [
            r.instrument
            for r in self._recordings.values()
            if r.lease_until_ms < now
        ]
        for instrument in stopped:
            recording = self._recordings.pop(instrument)
            if recording.task is not None:
                recording.task.cancel()
            self._close_segment(recording)
        return stopped

    def prune_now(self) -> int:
        """Applies the retention to the archive and the day-archive cache.

        Returns:
            Number of archived 1s aggregates deleted.
        """
        now = self._clock.now_ms()
        cutoff = now - self.settings.retention_ms
        deleted = self.archive.prune(cutoff)
        for archive in self._day_archives.values():
            archive.prune(cutoff)
        self._last_prune_ms = now
        if deleted:
            _LOG.info("pruned %d archived 1s aggregates", deleted)
        return deleted

    # Live recording ------------------------------------------------------

    async def _run(self, recording: _Recording) -> None:
        """Records an instrument until cancelled, restarting failed streams.

        Args:
            recording: Recording state.
        """
        port = self._ports[recording.instrument.venue]
        while True:
            try:
                prime = await self._safe_backfill(recording.instrument)
                events = port.stream_trade_events(
                    recording.instrument,
                    recording.tracker,
                    prime_from_ms=prime,
                )
                async with closing(events):
                    async for event in events:
                        self.on_event(recording, event)
            except asyncio.CancelledError:
                self._close_segment(recording)
                raise
            except errors.JosephError as err:
                recording.tracker.set_unavailable(
                    self._clock.now_ms(), err.message
                )
                _LOG.warning(
                    "trade recording of %s stopped: %s",
                    recording.instrument,
                    err.message,
                )
            self._close_segment(recording)
            await self._sleep(self.settings.retry_s)

    def on_event(
        self, recording: _Recording, event: ws.TradeStreamEvent
    ) -> None:
        """Applies one trade-stream event to a recording.

        Args:
            recording: Recording state.
            event: Coverage start, trades or a delivery proof.
        """
        if isinstance(event, ws.CoverageStart):
            self._close_segment(recording)
            recording.builder = self._builder(recording.instrument)
            recording.builder.start_segment(event.complete_from_ms)
            if event.complete_from_ms is not None:
                start = recorded_trades.ceil_to(event.complete_from_ms, _SECOND)
                recording.segment_from = recording.proven = start
        elif isinstance(event, ws.TradesReceived):
            if recording.builder is None:
                recording.builder = self._builder(recording.instrument)
                recording.builder.start_segment(None)
            if recording.segment_from is None and event.trades:
                first = event.trades[0].venue_event_ts_ms
                start = recorded_trades.ceil_to(first + 1, _SECOND)
                recording.segment_from = recording.proven = start
            self._keep(
                recording,
                recording.builder.add(event.trades, event.received_ms),
            )
        elif recording.builder is not None:
            through = event.received_ms - self.settings.grace_ms
            self._keep(
                recording,
                recording.builder.finalize_through(through, event.received_ms),
            )
            if recording.segment_from is not None:
                recording.proven = max(
                    recording.proven or recording.segment_from,
                    recorded_trades.floor_to(through, _SECOND),
                )
        now = self._clock.now_ms()
        if now - recording.flushed_ms >= self.settings.flush_interval_ms:
            self._flush(recording)

    def _builder(
        self, instrument: instruments.InstrumentId
    ) -> trade_bars.TradeBarBuilder:
        """Creates the 1s builder of a new coverage segment.

        Args:
            instrument: Instrument.

        Returns:
            Builder.
        """
        return trade_bars.TradeBarBuilder(
            instrument,
            _S1,
            source=f"{instrument.venue.value}.trades",
            retention_bars=600,
        )

    @staticmethod
    def _keep(recording: _Recording, bars: Sequence[market.Candle]) -> None:
        """Collects complete 1s aggregates for the next write.

        Args:
            recording: Recording state.
            bars: Bars changed by the builder.
        """
        for bar in bars:
            if recorded_trades.archivable(bar):
                recording.pending[bar.start_ms] = bar

    def _flush(self, recording: _Recording) -> None:
        """Writes proven-complete aggregates and coverage to the archive.

        Args:
            recording: Recording state.
        """
        recording.flushed_ms = self._clock.now_ms()
        start, end = recording.segment_from, recording.proven
        if start is None or end is None or end <= start:
            return
        ready = sorted(s for s in recording.pending if s < end)
        rows = [recording.pending.pop(s) for s in ready]
        self.archive.put(recording.instrument, rows, [(start, end)])

    def _close_segment(self, recording: _Recording) -> None:
        """Flushes and forgets the current coverage segment.

        Args:
            recording: Recording state.
        """
        self._flush(recording)
        recording.builder = None
        recording.segment_from = recording.proven = None
        recording.pending.clear()

    def proven_until(self, instrument: instruments.InstrumentId) -> int | None:
        """Returns how far the live recording has proven delivery.

        Args:
            instrument: Instrument.

        Returns:
            Whole-second venue time, or ``None`` when not recording.
        """
        recording = self._recordings.get(instrument)
        return None if recording is None else recording.proven

    async def wait_recorded(
        self,
        instrument: instruments.InstrumentId,
        end_ms: int,
        timeout_s: float,
    ) -> bool:
        """Waits until the live recording has archived trades up to a time.

        Args:
            instrument: Instrument.
            end_ms: Venue time that must be proven complete.
            timeout_s: Longest wait.

        Returns:
            True when archived through ``end_ms`` (False when not recording
            or it did not get there in time).
        """
        steps = max(1, int(timeout_s / _WAIT_STEP_S))
        for _ in range(steps + 1):
            recording = self._recordings.get(instrument)
            if recording is None or recording.segment_from is None:
                return False
            if recording.segment_from >= end_ms:
                return False
            if recording.proven is not None and recording.proven >= end_ms:
                self._flush(recording)
                return True
            await self._sleep(_WAIT_STEP_S)
        return False

    # Backfill ------------------------------------------------------------

    def _sink(self, instrument: instruments.InstrumentId) -> Sink:
        """Returns an archive writer bound to one instrument.

        Args:
            instrument: Instrument.

        Returns:
            Writer.
        """

        def sink(
            rows: list[market.Candle], covered: list[recorded_trades.Interval]
        ) -> None:
            """Writes aggregates and coverage.

            Args:
                rows: 1s aggregates.
                covered: Coverage intervals.
            """
            self.archive.put(instrument, rows, covered)

        return sink

    async def _safe_backfill(
        self, instrument: instruments.InstrumentId
    ) -> int | None:
        """Runs a backfill, recording but tolerating failures.

        Args:
            instrument: Instrument.

        Returns:
            Venue time to prime the live stream from, or ``None``.
        """
        try:
            return await self.backfill(instrument)
        except errors.JosephError as err:
            _LOG.warning("backfill of %s failed: %s", instrument, err.message)
            return None

    async def backfill(
        self, instrument: instruments.InstrumentId
    ) -> int | None:
        """Fills the gap since the newest coverage, newest trades first.

        Args:
            instrument: Instrument.

        Returns:
            The newest coverage end (a prime point for the live stream) when
            the venue has a REST trade history, else ``None``.
        """
        port = self._ports[instrument.venue]
        now = self._clock.now_ms()
        floor_ms = now - self.settings.retention_ms
        last = self.archive.covered_until(instrument)
        target = floor_ms if last is None else max(floor_ms, last)
        if instrument.venue in self._day_archives:
            await self._import_days(
                instrument,
                target,
                now,
                self.settings.archive_backfill_days,
            )
            newest = self.archive.covered_until(instrument)
            if newest is not None:
                target = max(target, newest)
        if not port.capabilities().market_data.trades_history:
            return None
        cursor = recorded_trades.floor_to(now - _PUBLISH_LAG_MS, _SECOND)
        calls = 0
        while cursor > target and calls < self.settings.backfill_max_calls:
            coverage = await port.fetch_trade_coverage(
                instrument, target, cursor
            )
            calls += 1
            rows, window = recorded_trades.archivable_seconds(
                coverage,
                instrument,
                source=f"{instrument.venue.value}.trades.rest",
                now_ms=self._clock.now_ms(),
            )
            if window is not None:
                self.archive.put(instrument, rows, [window])
            following = recorded_trades.ceil_to(coverage.from_ms, _SECOND)
            if following >= cursor:
                break
            cursor = following
        if cursor > target:
            _LOG.info(
                "backfill of %s stopped %d s short (venue window or budget)",
                instrument,
                (cursor - target) // 1000,
            )
        return self.archive.covered_until(instrument)

    async def _import_days(
        self,
        instrument: instruments.InstrumentId,
        from_ms: int,
        now_ms: int,
        max_days: int,
        until_ms: int | None = None,
    ) -> int:
        """Imports uncovered complete past days from the day archive.

        Newest days first; a day that is not published yet (today's file
        appears the next day) is skipped, an older missing day stops the
        walk.

        Args:
            instrument: Instrument.
            from_ms: Oldest time of interest.
            now_ms: Current time.
            max_days: Most days to import.
            until_ms: Newest time of interest (defaults to now).

        Returns:
            Number of days imported.
        """
        archive = self._day_archives.get(instrument.venue)
        if archive is None or max_days <= 0:
            return 0
        one = datetime.timedelta(days=1)
        oldest = max(from_ms, now_ms - self.settings.retention_ms)
        today = archive.day_of(now_ms)
        newest_day = archive.day_of(min(until_ms or now_ms, now_ms))
        day = min(newest_day, today - one)
        imported = 0
        while archive.day_bounds(day)[1] > oldest and imported < max_days:
            start, end = archive.day_bounds(day)
            coverage = self.archive.coverage(instrument, start, end)
            failed = self._failed_days.get((instrument, day))
            recent_failure = (
                failed is not None and now_ms - failed < _RETRY_FAILED_MS
            )
            if not recorded_trades.covering(coverage, start, end) and (
                not recent_failure
            ):
                try:
                    found = await archive.import_day(
                        instrument, day, self._sink(instrument), now_ms
                    )
                except errors.JosephError as err:
                    self._failed_days[(instrument, day)] = now_ms
                    _LOG.warning(
                        "archive day %s of %s failed: %s",
                        day,
                        instrument,
                        err.message,
                    )
                    found = True
                else:
                    imported += int(found)
                if not found and day < today - one:
                    break
            day -= one
        return imported

    # Read side (RecordedTrades) -----------------------------------------

    async def seconds_bars(
        self,
        instrument: instruments.InstrumentId,
        timeframe: timeframes.Timeframe,
        start_ms: int,
        end_ms: int,
    ) -> list[market.Candle]:
        """Returns archived seconds bars, importing archive days on demand.

        Args:
            instrument: Instrument.
            timeframe: Seconds timeframe.
            start_ms: Inclusive start.
            end_ms: Exclusive end.

        Returns:
            Ascending final candles of complete buckets.
        """
        if end_ms <= start_ms:
            return []
        now = self._clock.now_ms()
        if instrument.venue in self._day_archives:
            await self._import_days(
                instrument,
                start_ms,
                now,
                self.settings.archive_request_days,
                until_ms=end_ms - 1,
            )
        return self.reader.seconds_bars_now(
            instrument, timeframe, start_ms, end_ms
        )

    def enrich(self, rows: Sequence[market.Candle]) -> list[market.Candle]:
        """See ``trade_archive.RecordedTrades.enrich``."""
        return self.reader.enrich(rows)


def recording_settings_note(settings: RecorderSettings) -> str:
    """Describes the recording policy for capability notes.

    Args:
        settings: Recording policy.

    Returns:
        One sentence.
    """
    days = settings.retention_ms // _DAY_MS
    return (
        "Public trades are recorded while the engine uses an instrument and "
        f"kept {days} days, so seconds history and true delta accumulate "
        "across restarts; uncovered spans are reported as gaps."
    )
