# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Lighter public trade archive (AWS Public Blockchain Data, Parquet).

Lighter's REST trade history reaches back only about 53 minutes. The AWS
Public Blockchain Data program publishes every Lighter mainnet trade as one
keyless Parquet file per UTC day (verified live 2026-11-23):

* ``s3://aws-public-blockchain/v1.1/lighter/core/mainnet/trades/
  date=YYYY-MM-DD/trades.parquet``, served anonymously and unsigned at
  ``https://aws-public-blockchain.s3.amazonaws.com/...`` with HTTP range
  support. Days start 2025-01-17; a day appears at about 07:00 UTC the
  next day (HTTP 404 before). The ETag is a multipart one, so no whole-file
  digest is published;
* one file holds every market: 1.6-3.8 M rows and 150-300 MB a day, ZSTD,
  row groups of 1 M rows, rows ordered by ``(timestamp, id)``, 43 columns.
  Only these are read: ``id`` (int64, the REST ``trade_id``, unique and
  increasing), ``timestamp`` (int64 epoch milliseconds, the block time, as
  in REST; ``transaction_time`` is microseconds), ``price`` and ``size``
  (int64 in units of the market's ``supported_price_decimals`` and
  ``supported_size_decimals``; ``usdc_amount`` equals ``price * size``),
  ``is_maker_ask`` and ``trade_type`` (``trade``, ``liquidation``,
  ``deleverage``, ``market-settlement``);
* ``is_maker_ask`` true means the resting maker sold, so the aggressor
  bought, as in REST and WebSocket trades: fills of one taker order share
  one block and sweep the book its way (5,696 of 5,711 multi-fill orders
  in one block, 4,870 of 4,871 moving sweeps in the taker's direction, on
  BTC 2026-11-21; the opposite reading gives 1,676 and 30 of 55);
* ``public_market_index`` is the REST ``market_id`` (internal ``market_id``
  80 is public 4099, DRV). It is null in files up to at least 2026-06,
  when ``market_id`` was the public id, so the key is
  ``coalesce(public_market_index, market_id)``. Row groups whose
  statistics exclude the market are skipped, but rows are time ordered and
  every row group spans all markets, so the cheap market column is read
  first and the other columns only when it holds the market;
* Lighter's candles count only ``trade`` rows (liquidations are excluded
  from volume and prices: 40 of 40 sampled minutes), open at the previous
  close and include that open in high and low, and the ``1d`` candle's
  ``i`` is the id of the day's last trade.

A day is accepted only when its ``trade`` rows reproduce Lighter's own
``1d`` candle: close, last trade id, high and low (with the open), base
volume exactly and quote volume within the candle's float rounding. A
wrong market mapping, a missing row group or wrong decimals fail that
check and nothing is written.

Only the footer and the needed column chunks are fetched, by HTTP range
requests pinned to the footer's ETag (``If-Match``), with bounded
concurrency; row groups are decoded one at a time and nothing is written to
disk. BTC on 2026-11-21 (211 MB, 2.24 M rows) costs about 19 MB.
"""

from __future__ import annotations

import asyncio
import bisect
import dataclasses
import datetime
import decimal
import io
import logging
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import http as http_lib
from joseph.market_data.adapters import lighter
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trade_bars

_LOG = logging.getLogger(__name__)

BASE_URL = "https://aws-public-blockchain.s3.amazonaws.com"
DAY_PATH = "v1.1/lighter/core/mainnet/trades/date={day}/trades.parquet"
SOURCE = "lighter.trades.archive"
DAY_MS = 86_400_000
TAIL_BYTES = 65_536
"""First read of a file: pyarrow reads the last 64 KiB to find the footer."""
PUBLIC_KEY = "public_market_index"
INTERNAL_KEY = "market_id"
TRADE_COLUMNS = (
    "timestamp",
    "id",
    "price",
    "size",
    "is_maker_ask",
    "trade_type",
)
BOOK_TRADE = "trade"
SCHEMA = pa.schema(
    [
        ("timestamp", pa.int64()),
        ("id", pa.int64()),
        ("price", pa.int64()),
        ("size", pa.int64()),
        ("is_maker_ask", pa.bool_()),
    ]
)
"""Columns kept of a market's book trades."""
_MAGIC = b"PAR1"
_MAX_GAP = 65_536
_MISSING_RETRY_S = 1_800.0
_MARKETS_TTL_S = 3_600.0
_EXACT = decimal.Context(prec=60)
_WIDE = pa.decimal128(38, 0)

ArchiveSink = Callable[
    [list[market.Candle], list[recorded_trades.Interval]], None
]
"""Receives archived 1s aggregates and coverage (``TradeArchive.put``)."""


def day_bounds(day: datetime.date) -> recorded_trades.Interval:
    """Returns the ``[start, end)`` of a UTC day.

    Args:
        day: UTC date.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(
        day.year, day.month, day.day, tzinfo=datetime.UTC
    )
    start = int(moment.timestamp()) * 1000
    return start, start + DAY_MS


def day_of(ts_ms: int) -> datetime.date:
    """Returns the UTC day containing a time.

    Args:
        ts_ms: Epoch milliseconds.

    Returns:
        UTC date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).date()


@dataclasses.dataclass(frozen=True, slots=True)
class Market:
    """Lighter market identity and integer scaling.

    Attributes:
        market_id: Public (REST) market id.
        price_decimals: Decimals of the integer ``price`` column.
        size_decimals: Decimals of the integer ``size`` column.
    """

    market_id: int
    price_decimals: int
    size_decimals: int

    def price(self, value: int) -> decimal.Decimal:
        """Scales an integer price.

        Args:
            value: Archive integer.

        Returns:
            Price.
        """
        return decimal.Decimal(value).scaleb(-self.price_decimals, _EXACT)

    def size(self, value: int) -> decimal.Decimal:
        """Scales an integer size.

        Args:
            value: Archive integer.

        Returns:
            Base quantity.
        """
        return decimal.Decimal(value).scaleb(-self.size_decimals, _EXACT)

    def quote(self, value: int | decimal.Decimal) -> decimal.Decimal:
        """Scales an integer ``price * size`` product.

        Args:
            value: Product of archive integers.

        Returns:
            Quote amount.
        """
        places = self.price_decimals + self.size_decimals
        return decimal.Decimal(value).scaleb(-places, _EXACT)


class SparseFile(io.RawIOBase):
    """Read-only view of the fetched byte ranges of a remote file.

    pyarrow reads the footer and column chunks through it. Reads may span
    adjacent buffers; a read of bytes that were not fetched raises
    ``OSError`` (it means a range was computed wrongly).
    """

    def __init__(self, size: int, buffers: Mapping[int, bytes]) -> None:
        """Initializes the view.

        Args:
            size: Size of the whole remote file.
            buffers: Fetched bytes by file offset.
        """
        super().__init__()
        self._size = size
        self._buffers = dict(buffers)
        self._starts = sorted(self._buffers)
        self._pos = 0

    def readable(self) -> bool:
        """Returns True (the view is readable)."""
        return True

    def seekable(self) -> bool:
        """Returns True (the view is seekable)."""
        return True

    def tell(self) -> int:
        """Returns the current offset."""
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Moves the current offset.

        Args:
            offset: Offset relative to ``whence``.
            whence: ``SEEK_SET``, ``SEEK_CUR`` or ``SEEK_END``.

        Returns:
            New offset.
        """
        origin = {io.SEEK_CUR: self._pos, io.SEEK_END: self._size}
        self._pos = origin.get(whence, 0) + offset
        return self._pos

    def read(self, size: int = -1) -> bytes:
        """Reads fetched bytes from the current offset.

        Args:
            size: Bytes wanted (``-1`` reads to the end).

        Returns:
            Bytes, fewer only at the end of the file.

        Raises:
            OSError: When a wanted byte was not fetched.
        """
        end = self._size if size < 0 else min(self._size, self._pos + size)
        parts: list[bytes] = []
        cursor = self._pos
        while cursor < end:
            index = bisect.bisect_right(self._starts, cursor) - 1
            start = self._starts[index] if index >= 0 else 0
            buffer = self._buffers.get(start, b"")
            if index < 0 or cursor >= start + len(buffer):
                raise OSError(f"byte {cursor} of the archive was not fetched")
            stop = min(end, start + len(buffer))
            parts.append(buffer[cursor - start : stop - start])
            cursor = stop
        self._pos = cursor
        return b"".join(parts)


def chunk_range(column: Any) -> tuple[int, int]:
    """Returns the byte range of a column chunk as pyarrow reads it.

    Args:
        column: ``pyarrow.parquet.ColumnChunkMetaData``.

    Returns:
        ``(start, end)`` file offsets.
    """
    start = int(column.data_page_offset)
    dictionary = column.dictionary_page_offset
    if column.has_dictionary_page and dictionary and dictionary < start:
        start = int(dictionary)
    return start, start + int(column.total_compressed_size)


def coalesce(
    ranges: Iterable[tuple[int, int]], max_gap: int = _MAX_GAP
) -> list[tuple[int, int]]:
    """Merges byte ranges that touch or lie close together.

    Args:
        ranges: ``(start, end)`` ranges in any order.
        max_gap: Largest gap fetched to save a request.

    Returns:
        Ascending disjoint ranges.
    """
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if out and start - out[-1][1] <= max_gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class _Stats:
    """Row-group statistics of one integer column (``None`` = unknown).

    Attributes:
        low: Minimum non-null value.
        high: Maximum non-null value.
        nulls: Null count.
    """

    low: int | None
    high: int | None
    nulls: int | None

    def may_equal(self, value: int) -> bool:
        """Checks whether a non-null value of the column may equal one.

        Args:
            value: Value.

        Returns:
            False only when the statistics exclude it.
        """
        if self.low is None or self.high is None:
            return True
        return self.low <= value <= self.high


def _stats(group: Any, names: Sequence[str], name: str) -> _Stats | None:
    """Reads the statistics of a column of a row group.

    Args:
        group: ``pyarrow.parquet.RowGroupMetaData``.
        names: Column names in file order.
        name: Column.

    Returns:
        Statistics, or ``None`` when the file has no such column.
    """
    if name not in names:
        return None
    stats = group.column(names.index(name)).statistics
    if stats is None:
        return _Stats(None, None, None)
    low = high = None
    if stats.has_min_max:
        low, high = int(stats.min), int(stats.max)
    nulls = int(stats.null_count) if stats.has_null_count else None
    return _Stats(low, high, nulls)


def key_columns(
    group: Any, names: Sequence[str], market_id: int
) -> tuple[str, ...]:
    """Chooses the market columns of a row group from its statistics.

    The market key is ``coalesce(public_market_index, market_id)``.

    Args:
        group: ``pyarrow.parquet.RowGroupMetaData``.
        names: Column names in file order.
        market_id: Public market id.

    Returns:
        Columns to read, or ``()`` when the statistics prove that no row
        belongs to the market.

    Raises:
        ValueError: If the file has no ``market_id`` column.
    """
    internal = _stats(group, names, INTERNAL_KEY)
    if internal is None:
        raise ValueError("the archive has no market_id column")
    public = _stats(group, names, PUBLIC_KEY)
    if public is None:
        return (INTERNAL_KEY,) if internal.may_equal(market_id) else ()
    rows = int(group.num_rows)
    has_values = public.nulls is None or public.nulls < rows
    has_nulls = public.nulls is None or public.nulls > 0
    if not (
        (has_values and public.may_equal(market_id))
        or (has_nulls and internal.may_equal(market_id))
    ):
        return ()
    if not has_nulls:
        return (PUBLIC_KEY,)
    if not has_values:
        return (INTERNAL_KEY,)
    return (PUBLIC_KEY, INTERNAL_KEY)


def market_mask(table: Any, market_id: int) -> Any:
    """Selects the rows of one market.

    Args:
        table: ``pyarrow.Table`` with the chosen market columns.
        market_id: Public market id.

    Returns:
        Boolean ``pyarrow`` array (nulls are false).
    """
    keys = [
        pc.cast(table.column(name), pa.int64())
        for name in (PUBLIC_KEY, INTERNAL_KEY)
        if name in table.column_names
    ]
    key = pc.coalesce(*keys) if len(keys) > 1 else keys[0]
    return pc.fill_null(pc.equal(key, market_id), False)


def book_trades(table: Any, mask: Any) -> Any:
    """Keeps the masked ``trade`` rows (the rows Lighter's candles count).

    Args:
        table: ``pyarrow.Table`` with ``TRADE_COLUMNS``.
        mask: Market rows.

    Returns:
        ``pyarrow.Table`` with the ``SCHEMA`` columns.
    """
    book = pc.fill_null(pc.equal(table.column("trade_type"), BOOK_TRADE), False)
    kept = table.filter(pc.and_(mask, book))
    return kept.select(SCHEMA.names).cast(SCHEMA)


@dataclasses.dataclass(frozen=True, slots=True)
class DayTotals:
    """What a day's trades must reproduce (Lighter's ``1d`` candle).

    Attributes:
        open: Candle open (the previous close) or the first trade's price.
        high: Highest price.
        low: Lowest price.
        close: Last price.
        base_volume: Base volume.
        quote_volume: Quote volume.
        last_trade_id: Id of the last trade (``None`` when not published).
    """

    open: decimal.Decimal
    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    base_volume: decimal.Decimal
    quote_volume: decimal.Decimal
    last_trade_id: int | None


def totals(table: Any, scale: Market) -> DayTotals | None:
    """Folds a day's trades into the totals a ``1d`` candle publishes.

    Args:
        table: ``pyarrow.Table`` of ``SCHEMA`` sorted by time and id.
        scale: Market (integer scaling).

    Returns:
        Exact totals, or ``None`` without trades.
    """
    if table.num_rows == 0:
        return None
    price = table.column("price")
    size = table.column("size")
    quote = pc.multiply_checked(price, size)
    return DayTotals(
        open=scale.price(int(price[0].as_py())),
        high=scale.price(int(pc.max(price).as_py())),
        low=scale.price(int(pc.min(price).as_py())),
        close=scale.price(int(price[-1].as_py())),
        base_volume=scale.size(pc.sum(pc.cast(size, _WIDE)).as_py()),
        quote_volume=scale.quote(pc.sum(pc.cast(quote, _WIDE)).as_py()),
        last_trade_id=int(table.column("id")[-1].as_py()),
    )


def matches(found: DayTotals | None, expected: DayTotals | None) -> bool:
    """Checks that a day's trades reproduce Lighter's ``1d`` candle.

    The candle opens at the previous close and includes it in high and
    low, so high and low are compared with the open folded in. Base volume
    and prices must be equal, quote volume may differ by the candle's float
    rounding (``recorded_trades.VOLUME_TOLERANCE``), and the last trade id
    must equal the candle's ``i`` when published. A day without trades
    matches only a missing or zero-volume candle.

    Args:
        found: Totals of the archived trades.
        expected: The ``1d`` candle (``None`` when the venue has none).

    Returns:
        True when they agree.
    """
    if found is None:
        return expected is None or expected.base_volume == decimals.ZERO
    if expected is None:
        return False
    return (
        found.close == expected.close
        and max(found.high, expected.open) == expected.high
        and min(found.low, expected.open) == expected.low
        and found.base_volume == expected.base_volume
        and recorded_trades.volumes_agree(
            found.quote_volume, expected.quote_volume
        )
        and expected.last_trade_id in (None, found.last_trade_id)
    )


def iter_trades(
    table: Any,
    instrument: instruments.InstrumentId,
    scale: Market,
    received_ms: int,
    *,
    batch: int = 2_000,
) -> Iterator[list[market.Trade]]:
    """Converts archive rows into trades, a batch at a time.

    Args:
        table: ``pyarrow.Table`` of ``SCHEMA``.
        instrument: Instrument of every row.
        scale: Market (integer scaling).
        received_ms: Receipt time stamped on the trades.
        batch: Trades per yielded list.

    Yields:
        Trades in table order (``is_maker_ask`` true: the aggressor
        bought).
    """
    for chunk in table.to_batches(max_chunksize=batch):
        rows = chunk.to_pydict()
        yield [
            market.Trade(
                instrument_id=instrument,
                trade_id=str(trade_id),
                venue_event_ts_ms=int(ts),
                received_at_ms=received_ms,
                price=scale.price(int(price)),
                base_quantity=scale.size(int(size)),
                aggressor_side=(
                    market.AggressorSide.BUY
                    if maker_ask
                    else market.AggressorSide.SELL
                ),
                is_aggregated=False,
            )
            for ts, trade_id, price, size, maker_ask in zip(
                rows["timestamp"],
                rows["id"],
                rows["price"],
                rows["size"],
                rows["is_maker_ask"],
                strict=True,
            )
        ]


def _metadata(size: int, buffers: Mapping[int, bytes]) -> Any:
    """Parses the footer of a remote file.

    Args:
        size: File size.
        buffers: Fetched tail (footer included).

    Returns:
        ``pyarrow.parquet.FileMetaData``.
    """
    reader = pq.ParquetFile(SparseFile(size, buffers), pre_buffer=False)
    return reader.metadata


def _read(
    remote: _Remote,
    buffers: Mapping[int, bytes],
    index: int,
    columns: Sequence[str],
) -> Any:
    """Decodes some columns of one row group from fetched chunks.

    Args:
        remote: File.
        buffers: Fetched column chunks.
        index: Row group.
        columns: Columns.

    Returns:
        ``pyarrow.Table``.
    """
    reader = pq.ParquetFile(
        SparseFile(remote.size, buffers),
        metadata=remote.metadata,
        pre_buffer=False,
    )
    return reader.read_row_group(
        index, columns=list(columns), use_threads=False
    )


def _read_mask(
    remote: _Remote,
    buffers: Mapping[int, bytes],
    index: int,
    keys: Sequence[str],
    market_id: int,
) -> Any:
    """Decodes the market columns of a row group into a market mask.

    Args:
        remote: File.
        buffers: Fetched market column chunks.
        index: Row group.
        keys: Market columns.
        market_id: Public market id.

    Returns:
        Boolean ``pyarrow`` array.
    """
    return market_mask(_read(remote, buffers, index, keys), market_id)


def _read_trades(
    remote: _Remote, buffers: Mapping[int, bytes], index: int, mask: Any
) -> Any:
    """Decodes the trade columns of a row group and keeps the book trades.

    Args:
        remote: File.
        buffers: Fetched trade column chunks.
        index: Row group.
        mask: Market rows.

    Returns:
        ``pyarrow.Table`` of ``SCHEMA``.
    """
    return book_trades(_read(remote, buffers, index, TRADE_COLUMNS), mask)


def _sorted(parts: Sequence[Any]) -> Any:
    """Concatenates row-group results in time and trade-id order.

    Args:
        parts: ``pyarrow.Table`` objects of ``SCHEMA``.

    Returns:
        Sorted ``pyarrow.Table``.
    """
    table = pa.concat_tables(parts)
    return table.sort_by([("timestamp", "ascending"), ("id", "ascending")])


def _content_range(header: str | None) -> tuple[int, int]:
    """Parses ``Content-Range: bytes a-b/total``.

    Args:
        header: Header value.

    Returns:
        ``(first byte, total size)``.

    Raises:
        errors.VenueUnavailableError: On a missing or malformed header.
    """
    try:
        span, total = str(header).split(" ", 1)[1].split("/", 1)
        return int(span.split("-", 1)[0]), int(total)
    except (IndexError, ValueError) as err:
        raise errors.VenueUnavailableError(
            f"lighter archive sent a bad Content-Range: {header}"
        ) from err


@dataclasses.dataclass(frozen=True, slots=True)
class _Remote:
    """A remote archive file whose footer is parsed.

    Attributes:
        url: File URL.
        size: File size.
        etag: ETag the column reads are pinned to.
        metadata: ``pyarrow.parquet.FileMetaData``.
        names: Column names in file order.
    """

    url: str
    size: int
    etag: str | None
    metadata: Any
    names: tuple[str, ...]


class LighterArchive:
    """Reads, verifies and archives Lighter's daily trade files remotely.

    Attributes:
        venue: Venue whose trades the archive holds.
        FOLD_TRADES: Trades folded per builder call.
        SINK_BARS: Aggregates handed to the archive per write.
        note: Capability note describing the archive.
        bytes_received: Body bytes received from the archive host.
        requests: Range requests made to the archive host.
    """

    venue = instruments.Venue.LIGHTER
    FOLD_TRADES = 2_000
    SINK_BARS = 5_000
    note = (
        "Older seconds history is imported from the AWS Public Blockchain "
        "Data Lighter trade archive (s3://aws-public-blockchain, keyless; "
        "UTC days, published about seven hours after the day ends) by HTTP "
        "range reads of the needed columns. A day is used only when its "
        "trades fold exactly to Lighter's own 1d candle, which (like every "
        "Lighter candle) excludes liquidation fills."
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
        rest_url: str = lighter.REST_URL,
        max_concurrency: int = 4,
        max_days: int = 1,
        coalesce_gap: int = _MAX_GAP,
        timeout_s: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the reader.

        Args:
            client: Optional HTTP client (tests inject respx-backed ones).
            base_url: Archive host.
            rest_url: Lighter REST base (market ids and ``1d`` candles).
            max_concurrency: Concurrent range requests.
            max_days: Days imported at the same time (bounds memory).
            coalesce_gap: Largest gap between column chunks fetched in one
                request.
            timeout_s: Per-request timeout.
            monotonic: Monotonic clock (seconds) for retry and TTL caches.
        """
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": http_lib.USER_AGENT}
        )
        self._rest = http_lib.VenueHttp(
            instruments.Venue.LIGHTER, rest_url, client=self._client
        )
        self._base = base_url.rstrip("/")
        self._ranges = asyncio.Semaphore(max(1, max_concurrency))
        self._days = asyncio.Semaphore(max(1, max_days))
        self._gap = max(0, coalesce_gap)
        self._missing: dict[datetime.date, float] = {}
        self._markets: dict[str, Market] = {}
        self._markets_at: float | None = None
        self._monotonic = monotonic
        self.bytes_received = 0
        self.requests = 0

    @staticmethod
    def day_of(ts_ms: int) -> datetime.date:
        """Returns the archive (UTC) day containing a time.

        Args:
            ts_ms: Epoch milliseconds.

        Returns:
            UTC date.
        """
        return day_of(ts_ms)

    @staticmethod
    def day_bounds(day: datetime.date) -> recorded_trades.Interval:
        """Returns the ``[start, end)`` of an archive (UTC) day.

        Args:
            day: UTC date.

        Returns:
            Epoch milliseconds.
        """
        return day_bounds(day)

    def url(self, day: datetime.date) -> str:
        """Returns the URL of a day's file.

        Args:
            day: UTC date.

        Returns:
            URL.
        """
        return f"{self._base}/{DAY_PATH.format(day=day.isoformat())}"

    # Market metadata and the verifying candle ----------------------------

    async def market(self, instrument: instruments.InstrumentId) -> Market:
        """Resolves an instrument to its public market id and decimals.

        Args:
            instrument: Lighter instrument.

        Returns:
            Market.

        Raises:
            errors.NotFoundError: When Lighter lists no such perpetual.
            errors.VenueUnavailableError: When ``orderBooks`` fails.
        """
        now = self._monotonic()
        if self._markets_at is None or now - self._markets_at > _MARKETS_TTL_S:
            payload = await self._rest.get("/orderBooks")
            try:
                _, meta = lighter.parse_order_books(payload, 0)
                self._markets = {
                    symbol: Market(
                        int(row["market_id"]),
                        int(row["price_decimals"]),
                        int(row["size_decimals"]),
                    )
                    for symbol, row in meta.items()
                }
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                raise errors.VenueUnavailableError(
                    "lighter orderBooks is unreadable"
                ) from err
            self._markets_at = now
        found = self._markets.get(instrument.venue_market_id)
        if found is None:
            raise errors.NotFoundError(
                f"lighter lists no market {instrument.venue_market_id}"
            )
        return found

    async def expected_totals(
        self,
        instrument: instruments.InstrumentId,
        scale: Market,
        day: datetime.date,
    ) -> DayTotals | None:
        """Fetches Lighter's ``1d`` candle of a day.

        Args:
            instrument: Instrument.
            scale: Market.
            day: UTC date.

        Returns:
            Candle totals, or ``None`` when the venue has no candle that day.

        Raises:
            errors.VenueUnavailableError: When the candle cannot be read.
        """
        start, end = day_bounds(day)
        payload = await self._rest.get(
            "/candles",
            params={
                "market_id": scale.market_id,
                "resolution": "1d",
                "start_timestamp": start,
                "end_timestamp": end,
                "count_back": 1,
            },
        )
        try:
            for row in payload.get("c") or []:
                if int(row["t"]) != start:
                    continue
                candle = lighter.parse_candle(
                    row,
                    instrument,
                    timeframes.Timeframe.D1,
                    price_dec=scale.price_decimals,
                    size_dec=scale.size_decimals,
                    now_ms=start,
                )
                last = row.get("i")
                return DayTotals(
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    base_volume=candle.base_volume,
                    quote_volume=candle.quote_turnover or decimals.ZERO,
                    last_trade_id=None if last is None else int(last),
                )
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            raise errors.VenueUnavailableError(
                f"lighter 1d candle of {day} is unreadable"
            ) from err
        return None

    # Range reads ----------------------------------------------------------

    async def _get(
        self, url: str, span: str, etag: str | None
    ) -> tuple[bytes, int, int, str | None] | None:
        """Performs one range request.

        Args:
            url: File URL.
            span: Range value after ``bytes=`` (``a-b`` or ``-n``).
            etag: ETag the file must still have.

        Returns:
            ``(body, first byte, total size, etag)``, or ``None`` when the
            file does not exist (only asked without ``etag``).

        Raises:
            errors.VenueUnavailableError: On transport errors, a changed
                file or a response that is not a partial one.
        """
        headers = {"Range": f"bytes={span}"}
        if etag is not None:
            headers["If-Match"] = etag
        async with self._ranges:
            try:
                async with self._client.stream(
                    "GET", url, headers=headers
                ) as response:
                    status = response.status_code
                    if etag is None and status in (403, 404):
                        return None
                    if status == 412:
                        raise errors.VenueUnavailableError(
                            "lighter archive file changed while being read"
                        )
                    if status != 206:
                        raise errors.VenueUnavailableError(
                            f"lighter archive answered a range request with "
                            f"HTTP {status}"
                        )
                    body = await response.aread()
            except httpx.HTTPError as err:
                raise errors.VenueUnavailableError(
                    f"lighter archive unreachable: {type(err).__name__}"
                ) from err
        self.requests += 1
        self.bytes_received += len(body)
        first, total = _content_range(response.headers.get("content-range"))
        return body, first, total, response.headers.get("etag")

    async def _span(
        self, remote_url: str, start: int, end: int, etag: str | None
    ) -> bytes:
        """Fetches the bytes ``[start, end)`` of a file.

        Args:
            remote_url: File URL.
            start: First byte.
            end: Exclusive end.
            etag: ETag the file must still have.

        Returns:
            Exactly ``end - start`` bytes.

        Raises:
            errors.VenueUnavailableError: On failures or a short range.
        """
        got = await self._get(remote_url, f"{start}-{end - 1}", etag)
        if got is None or got[1] != start or len(got[0]) != end - start:
            raise errors.VenueUnavailableError(
                "lighter archive returned the wrong byte range"
            )
        return got[0]

    async def _fetch(
        self, remote: _Remote, ranges: Iterable[tuple[int, int]]
    ) -> dict[int, bytes]:
        """Fetches byte ranges (coalesced, concurrently but bounded).

        Args:
            remote: File.
            ranges: ``(start, end)`` ranges.

        Returns:
            Bytes by offset.

        Raises:
            errors.VenueUnavailableError: On the first failed request.
        """
        spans = coalesce(ranges, self._gap)
        bodies = await asyncio.gather(
            *(self._span(remote.url, a, b, remote.etag) for a, b in spans),
            return_exceptions=True,
        )
        out: dict[int, bytes] = {}
        for (start, _), body in zip(spans, bodies, strict=True):
            if isinstance(body, BaseException):
                raise body
            out[start] = body
        return out

    async def open_file(self, day: datetime.date) -> _Remote | None:
        """Reads the footer of a day's file.

        Args:
            day: UTC date.

        Returns:
            Parsed file, or ``None`` when the day is not published.

        Raises:
            errors.VenueUnavailableError: On transport failures.
            ValueError: When the file is not Parquet.
        """
        url = self.url(day)
        got = await self._get(url, f"-{TAIL_BYTES}", None)
        if got is None:
            return None
        tail, first, size, etag = got
        footer = int.from_bytes(tail[-8:-4], "little")
        begin = size - 8 - footer
        if (
            len(tail) < 12
            or tail[-4:] != _MAGIC
            or first + len(tail) != size
            or begin < 4
        ):
            raise ValueError("the archive file is not a Parquet file")
        buffers = {first: tail}
        if begin < first:
            buffers[begin] = await self._span(url, begin, first, etag)
        metadata = await asyncio.to_thread(_metadata, size, buffers)
        names = tuple(metadata.schema.to_arrow_schema().names)
        missing = set(TRADE_COLUMNS) - set(names)
        if missing:
            raise ValueError(f"the archive lacks columns {sorted(missing)}")
        return _Remote(url, size, etag, metadata, names)

    async def _row_group(
        self, remote: _Remote, index: int, market_id: int
    ) -> Any | None:
        """Reads one market's book trades of one row group.

        The market column is read first; the trade columns only when it
        holds the market.

        Args:
            remote: File.
            index: Row group.
            market_id: Public market id.

        Returns:
            ``pyarrow.Table`` of ``SCHEMA``, or ``None`` when the row group
            holds no row of the market.
        """
        group = remote.metadata.row_group(index)
        keys = key_columns(group, remote.names, market_id)
        if not keys:
            return None

        def ranges(columns: Sequence[str]) -> list[tuple[int, int]]:
            """Returns the chunk ranges of columns of this row group.

            Args:
                columns: Column names.

            Returns:
                Byte ranges.
            """
            return [
                chunk_range(group.column(remote.names.index(name)))
                for name in columns
            ]

        buffers = await self._fetch(remote, ranges(keys))
        mask = await asyncio.to_thread(
            _read_mask, remote, buffers, index, keys, market_id
        )
        del buffers
        if not pc.any(mask).as_py():
            return None
        buffers = await self._fetch(remote, ranges(TRADE_COLUMNS))
        return await asyncio.to_thread(
            _read_trades, remote, buffers, index, mask
        )

    async def read_day(self, day: datetime.date, scale: Market) -> Any | None:
        """Reads one market's book trades of a day, row group by row group.

        Args:
            day: UTC date.
            scale: Market.

        Returns:
            ``pyarrow.Table`` of ``SCHEMA`` sorted by time and trade id, or
            ``None`` when the day is not published.

        Raises:
            errors.VenueUnavailableError: On transport failures.
            ValueError: When the file is not the expected Parquet.
        """
        remote = await self.open_file(day)
        if remote is None:
            return None
        parts = [SCHEMA.empty_table()]
        for index in range(remote.metadata.num_row_groups):
            part = await self._row_group(remote, index, scale.market_id)
            if part is not None:
                parts.append(part)
        return await asyncio.to_thread(_sorted, parts)

    # Import ---------------------------------------------------------------

    def _import_table(
        self,
        table: Any,
        instrument: instruments.InstrumentId,
        scale: Market,
        day: datetime.date,
        sink: ArchiveSink,
        *,
        received_ms: int,
        expected: DayTotals | None,
    ) -> None:
        """Verifies a day against its candle, then archives it.

        Args:
            table: ``pyarrow.Table`` of ``SCHEMA``, sorted.
            instrument: Instrument.
            scale: Market.
            day: UTC date.
            sink: Archive writer.
            received_ms: Receipt time.
            expected: The ``1d`` candle.

        Raises:
            ValueError: On nulls, a trade outside the day or totals that do
                not reproduce the candle (nothing is written then).
        """
        start, end = day_bounds(day)
        for name in SCHEMA.names:
            if table.column(name).null_count:
                raise ValueError(f"the archive has null {name} values")
        stamps = table.column("timestamp")
        if table.num_rows and (
            int(pc.min(stamps).as_py()) < start
            or int(pc.max(stamps).as_py()) >= end
        ):
            raise ValueError("an archive trade lies outside its day")
        found = totals(table, scale)
        if not matches(found, expected):
            raise ValueError(
                f"the trades do not match the 1d candle ({found} != {expected})"
            )
        builder = trade_bars.TradeBarBuilder(
            instrument,
            timeframes.Timeframe.S1,
            source=SOURCE,
            retention_bars=600,
            dedupe_capacity=200_000,
        )
        builder.start_segment(start)
        out: list[market.Candle] = []
        for trades in iter_trades(
            table, instrument, scale, received_ms, batch=self.FOLD_TRADES
        ):
            out.extend(
                bar
                for bar in builder.add(trades, received_ms)
                if recorded_trades.archivable(bar)
            )
            if len(out) >= self.SINK_BARS:
                sink(out, [])
                out = []
        out.extend(
            bar
            for bar in builder.finalize_through(end, received_ms)
            if recorded_trades.archivable(bar)
        )
        sink(out, [(start, end)])

    async def import_day(
        self,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: ArchiveSink,
        received_ms: int,
    ) -> bool:
        """Archives one verified day of an instrument's trades.

        Args:
            instrument: Lighter instrument.
            day: UTC date (a complete past day).
            sink: Archive writer.
            received_ms: Receipt time stamped on the aggregates.

        Returns:
            True when imported, False when the day is not published yet.

        Raises:
            errors.NotFoundError: For a market Lighter does not list.
            errors.VenueUnavailableError: On transport, format or
                verification failures (nothing is written then).
        """
        missed = self._missing.get(day)
        if missed is not None and self._monotonic() - missed < _MISSING_RETRY_S:
            return False
        async with self._days:
            scale = await self.market(instrument)
            before = self.bytes_received, self.requests
            try:
                table = await self.read_day(day, scale)
            except (OSError, ValueError, pa.ArrowException) as err:
                raise errors.VenueUnavailableError(
                    f"lighter archive {day} is unreadable: {err}"
                ) from err
            if table is None:
                self._missing[day] = self._monotonic()
                return False
            self._missing.pop(day, None)
            expected = await self.expected_totals(instrument, scale, day)
            try:
                await asyncio.to_thread(
                    self._import_table,
                    table,
                    instrument,
                    scale,
                    day,
                    sink,
                    received_ms=received_ms,
                    expected=expected,
                )
            except (ValueError, pa.ArrowException) as err:
                raise errors.VenueUnavailableError(
                    f"lighter archive {day} rejected: {err}"
                ) from err
        _LOG.info(
            "imported lighter archive %s of %s: %d trades, %d bytes in %d "
            "requests",
            day,
            instrument,
            table.num_rows,
            self.bytes_received - before[0],
            self.requests - before[1],
        )
        return True

    def prune(self, before_ms: int) -> int:
        """Forgets not-yet-published markers of days before a cut-off.

        Nothing is cached on disk.

        Args:
            before_ms: Retention cut-off.

        Returns:
            Number of markers forgotten.
        """
        stale = [d for d in self._missing if day_bounds(d)[1] <= before_ms]
        for day in stale:
            del self._missing[day]
        return len(stale)

    async def close(self) -> None:
        """Closes the HTTP client if owned."""
        if self._owns_client:
            await self._client.aclose()
