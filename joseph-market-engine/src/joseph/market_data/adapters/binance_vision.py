# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Binance public data archive (``data.binance.vision``): USDⓈ-M aggTrades.

Binance publishes every USDⓈ-M futures aggregate trade as free, keyless
downloads (verified live 2026-11-23):

* daily ``data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-
  YYYY-MM-DD.zip`` (monthly files under ``monthly/`` with ``YYYY-MM``),
  each with a ``.CHECKSUM`` file beside it holding ``<sha256>  <name>``;
* one CSV per zip with the columns ``agg_trade_id, price, quantity,
  first_trade_id, last_trade_id, transact_time, is_buyer_maker``. Files
  from late 2022 on start with that header line; older files have none, so
  the header is optional. ``transact_time`` is epoch milliseconds (spot
  files switched to microseconds in 2025, so 16-digit values are scaled);
* ``is_buyer_maker`` equals the REST/stream ``m`` flag (row-for-row equal
  to ``/fapi/v1/aggTrades`` for the same ids), so ``true`` means the
  aggressor sold. A day's rows sum exactly to that day's 1d kline (open,
  high, low, close, volume, quote volume and taker buy volume);
* a day's file appears the next day (2026-11-21's at 07:24 UTC on
  2026-11-22; 2026-11-22's was still missing at 03:45 UTC on 2026-11-23),
  so today's and, until the morning UTC, yesterday's trades come only from
  the REST window (48 hours) or the live stream.

Downloads stream to disk (no whole file in memory) while hashing, are
verified against the checksum, and are cached per day; concurrency is
bounded and a missing day is not re-requested for 30 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import datetime
import hashlib
import io
import logging
import pathlib
import time
import zipfile
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from typing import TypeAlias

import httpx

from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import http as http_lib
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trade_bars

_LOG = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision"
DAILY_PATH = (
    "data/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
)
SOURCE = "binance.trades.vision"
DAY_MS = 86_400_000
_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
_MICROS_ABOVE = 10**14
"""Times above this are microseconds (milliseconds have 13 digits)."""
_CHUNK_BYTES = 1 << 16
"""Download chunk size (64 KiB)."""
_MISSING_RETRY_S = 1_800.0
"""Seconds before a day found unpublished is requested again."""
_BOOLS = {"true": True, "false": False}


class ChecksumMismatchError(errors.VenueUnavailableError):
    """A downloaded archive does not match its published sha256."""


def _remove(path: pathlib.Path) -> None:
    """Deletes a file if it exists.

    Args:
        path: File.
    """
    path.unlink(missing_ok=True)


def day_start_ms(day: datetime.date) -> int:
    """Returns 00:00 UTC of a day in epoch milliseconds.

    Args:
        day: UTC date.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(
        day.year, day.month, day.day, tzinfo=datetime.UTC
    )
    return int(moment.timestamp()) * 1000


def utc_day(ts_ms: int) -> datetime.date:
    """Returns the UTC date of a time.

    Args:
        ts_ms: Epoch milliseconds.

    Returns:
        UTC date.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).date()


def file_name(symbol: str, day: datetime.date) -> str:
    """Returns the archive file name of a symbol and day.

    Args:
        symbol: Binance symbol such as ``BTCUSDT``.
        day: UTC date.

    Returns:
        ``{SYMBOL}-aggTrades-YYYY-MM-DD.zip``.
    """
    return f"{symbol}-aggTrades-{day.isoformat()}.zip"


def parse_checksum(text: str) -> str:
    """Extracts the sha256 digest from a ``.CHECKSUM`` file.

    Args:
        text: File content (``<hex digest>  <file name>``).

    Returns:
        Lower-case hex digest.

    Raises:
        ValueError: If no 64-digit hex digest is present.
    """
    digest = text.strip().split()[0].lower() if text.strip() else ""
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("malformed CHECKSUM file")
    return digest


def parse_rows(
    lines: Iterable[str],
    instrument: instruments.InstrumentId,
    received_ms: int,
) -> Iterator[market.Trade]:
    """Parses aggTrades CSV lines (header optional) into trades.

    Args:
        lines: CSV text lines.
        instrument: Instrument of every row.
        received_ms: Receipt time stamped on the trades, epoch
            milliseconds.

    Yields:
        Trades in file order.

    Raises:
        ValueError: On a malformed row.
    """
    columns: dict[str, int] | None = None
    for row in csv.reader(lines):
        if not row:
            continue
        if columns is None:
            # Only files from late 2022 on start with a header line.
            if not row[0].strip().isdigit():
                columns = {name.strip(): i for i, name in enumerate(row)}
                if set(_COLUMNS) - set(columns):
                    raise ValueError(f"unexpected aggTrades header: {row}")
                continue
            columns = {name: i for i, name in enumerate(_COLUMNS)}
        if len(row) < len(columns):
            raise ValueError(f"short aggTrades row: {row}")
        ts = int(row[columns["transact_time"]])
        maker = _BOOLS.get(row[columns["is_buyer_maker"]].strip().lower())
        if maker is None:
            raise ValueError(f"bad is_buyer_maker: {row}")
        yield market.Trade(
            instrument_id=instrument,
            trade_id=row[columns["agg_trade_id"]],
            venue_event_ts_ms=ts // 1000 if ts > _MICROS_ABOVE else ts,
            received_at_ms=received_ms,
            price=decimals.to_decimal(row[columns["price"]]),
            base_quantity=decimals.to_decimal(row[columns["quantity"]]),
            aggressor_side=(
                market.AggressorSide.SELL if maker else market.AggressorSide.BUY
            ),
            is_aggregated=True,
        )


def day_seconds(
    lines: Iterable[str],
    instrument: instruments.InstrumentId,
    day: datetime.date,
    *,
    received_ms: int,
    batch: int = 5_000,
) -> Iterator[list[market.Candle]]:
    """Folds one day's aggTrades into complete 1s aggregates, streamed.

    The live seconds builder is used, so archive bars follow exactly the
    rules of stream-built bars. The day is complete, so every second of it
    is covered; flat seconds are not yielded (coverage proves them).

    Args:
        lines: CSV lines of the day's file.
        instrument: Instrument.
        day: UTC date of the file.
        received_ms: Receipt time stamped on the bars, epoch milliseconds.
        batch: Bars at which a chunk is yielded (a chunk may hold more;
            the last may hold fewer).

    Yields:
        Chunks of final 1s aggregates with traded volume, ascending.

    Raises:
        ValueError: On a malformed row or a trade outside the day.
    """
    start = day_start_ms(day)
    end = start + DAY_MS
    builder = trade_bars.TradeBarBuilder(
        instrument,
        timeframes.Timeframe.S1,
        source=SOURCE,
        retention_bars=600,
        dedupe_capacity=200_000,
    )
    builder.start_segment(start)
    pending: list[market.Trade] = []
    out: list[market.Candle] = []

    def keep(bars: list[market.Candle]) -> None:
        """Collects archivable bars.

        Args:
            bars: Changed bars from the builder.
        """
        out.extend(b for b in bars if recorded_trades.archivable(b))

    for trade in parse_rows(lines, instrument, received_ms):
        if not start <= trade.venue_event_ts_ms < end:
            raise ValueError("archive trade outside its day")
        pending.append(trade)
        if len(pending) >= min(2_000, batch):
            keep(builder.add(pending, received_ms))
            pending = []
        if len(out) >= batch:
            yield out
            out = []
    keep(builder.add(pending, received_ms))
    keep(builder.finalize_through(end, received_ms))
    if out:
        yield out


ArchiveSink: TypeAlias = Callable[
    [list[market.Candle], list[recorded_trades.Interval]], None
]
"""Receives archived 1s aggregates and coverage (``TradeArchive.put``)."""


class BinanceVision:
    """Downloads, verifies and caches daily USDⓈ-M aggTrades archives.

    Attributes:
        venue: Venue whose trades the archive holds.
        note: Capability note describing the archive.
        cache_dir: Directory of verified zips (``{SYMBOL}/{file}``).
    """

    venue = instruments.Venue.BINANCE
    note = (
        "Older seconds history is imported from the official "
        "data.binance.vision daily aggTrades archive (sha256 verified); a "
        "day's file appears the next day, so the newest day comes from the "
        "REST window and live recording."
    )

    def __init__(
        self,
        cache_dir: pathlib.Path | str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
        max_concurrency: int = 2,
        timeout_s: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the downloader.

        Args:
            cache_dir: Cache directory (created on demand).
            client: Optional HTTP client (tests inject respx-backed ones);
                ``close`` leaves an injected client open.
            base_url: Archive host.
            max_concurrency: Concurrent downloads (at least 1).
            timeout_s: Per-request timeout in seconds of the client created
                here (unused with ``client``).
            monotonic: Monotonic clock (seconds) for the missing-day cache.
        """
        self.cache_dir = pathlib.Path(cache_dir)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": http_lib.USER_AGENT}
        )
        self._base = base_url.rstrip("/")
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._locks: dict[tuple[str, datetime.date], asyncio.Lock] = {}
        self._missing: dict[tuple[str, datetime.date], float] = {}
        self._monotonic = monotonic

    def _now_s(self) -> float:
        """Returns the monotonic clock in seconds."""
        return self._monotonic()

    @staticmethod  # noqa: GPY035 - implements DayArchive.day_of; trade_recorder calls it on the instance
    def day_of(ts_ms: int) -> datetime.date:
        """Returns the archive (UTC) day containing a time.

        Args:
            ts_ms: Epoch milliseconds.

        Returns:
            UTC date.
        """
        return utc_day(ts_ms)

    @staticmethod  # noqa: GPY035 - implements DayArchive.day_bounds; trade_recorder calls it on the instance
    def day_bounds(day: datetime.date) -> recorded_trades.Interval:
        """Returns the ``[start, end)`` of an archive (UTC) day.

        Args:
            day: UTC date.

        Returns:
            Epoch milliseconds.
        """
        start = day_start_ms(day)
        return start, start + DAY_MS

    def url(self, symbol: str, day: datetime.date) -> str:
        """Returns the download URL of a daily file.

        Args:
            symbol: Binance symbol.
            day: UTC date.

        Returns:
            URL.
        """
        path = DAILY_PATH.format(symbol=symbol, day=day.isoformat())
        return f"{self._base}/{path}"

    def cached_path(self, symbol: str, day: datetime.date) -> pathlib.Path:
        """Returns where a verified daily file is cached.

        Args:
            symbol: Binance symbol.
            day: UTC date.

        Returns:
            Path (may not exist yet).
        """
        return self.cache_dir / symbol / file_name(symbol, day)

    async def _get_text(self, url: str) -> str | None:
        """Fetches a small text file.

        Args:
            url: URL.

        Returns:
            Body, or ``None`` when the file does not exist (HTTP 403 or
            404).

        Raises:
            errors.VenueUnavailableError: On transport or other HTTP errors.
        """
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as err:
            raise errors.VenueUnavailableError(
                f"binance archive unreachable: {type(err).__name__}"
            ) from err
        if response.status_code in (403, 404):
            return None
        if response.status_code >= 400:
            raise errors.VenueUnavailableError(
                f"binance archive returned HTTP {response.status_code}"
            )
        return response.text

    async def _download(self, url: str, target: pathlib.Path) -> str | None:
        """Streams a file to disk, returning its sha256.

        Args:
            url: URL.
            target: Destination (``.part`` file).

        Returns:
            Hex digest, or ``None`` when the file does not exist (HTTP 403
            or 404).

        Raises:
            errors.VenueUnavailableError: On transport or other HTTP errors
                (a partly written ``target`` is removed after a transport
                error).
        """
        digest = hashlib.sha256()
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code in (403, 404):
                    return None
                if response.status_code >= 400:
                    raise errors.VenueUnavailableError(
                        f"binance archive returned HTTP {response.status_code}"
                    )
                with target.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        digest.update(chunk)
                        handle.write(chunk)
        except httpx.HTTPError as err:
            _remove(target)
            raise errors.VenueUnavailableError(
                f"binance archive download failed: {type(err).__name__}"
            ) from err
        return digest.hexdigest()

    async def daily_zip(
        self, symbol: str, day: datetime.date
    ) -> pathlib.Path | None:
        """Returns the verified daily archive, downloading it once.

        A day found unpublished is not requested again for
        ``_MISSING_RETRY_S`` (30 minutes); ``None`` is returned meanwhile.

        Args:
            symbol: Binance symbol.
            day: UTC date.

        Returns:
            Path of the cached zip, or ``None`` when Binance has not
            published it (yet).

        Raises:
            ChecksumMismatchError: If the checksum file is unreadable or the
                download does not match it.
            errors.VenueUnavailableError: On transport or HTTP errors.
        """
        key = (symbol, day)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            final = self.cached_path(symbol, day)
            if final.exists():
                return final
            missed = self._missing.get(key)
            if missed is not None and self._now_s() - missed < _MISSING_RETRY_S:
                return None
            async with self._semaphore:
                url = self.url(symbol, day)
                text = await self._get_text(f"{url}.CHECKSUM")
                if text is None:
                    self._missing[key] = self._now_s()
                    return None
                try:
                    expected = parse_checksum(text)
                except ValueError as err:
                    raise ChecksumMismatchError(
                        f"unreadable checksum for {final.name}"
                    ) from err
                final.parent.mkdir(parents=True, exist_ok=True)
                part = final.with_suffix(".part")
                actual = await self._download(url, part)
                if actual is None:
                    self._missing[key] = self._now_s()
                    return None
                if actual != expected:
                    _remove(part)
                    raise ChecksumMismatchError(
                        f"{final.name} sha256 {actual} != published {expected}"
                    )
                part.replace(final)
                self._missing.pop(key, None)
                _LOG.info("cached binance archive %s", final.name)
                return final

    @staticmethod  # noqa: GPY035 - tests/network call open_lines on an instance
    @contextlib.contextmanager
    def open_lines(path: pathlib.Path) -> Iterator[Iterator[str]]:
        """Opens the CSV inside a cached zip as a line stream.

        Args:
            path: Cached zip.

        Yields:
            Text lines, read incrementally.

        Raises:
            ValueError: If the zip does not hold exactly one CSV.
        """
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{path.name} holds {len(names)} CSV files")
            with archive.open(names[0]) as raw:
                yield iter(io.TextIOWrapper(raw, encoding="utf-8", newline=""))

    def _import_file(
        self,
        path: pathlib.Path,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: ArchiveSink,
        received_ms: int,
    ) -> None:
        """Parses a cached day and hands its aggregates to ``sink``.

        Coverage of the whole day is handed over only after every
        aggregate, so an interrupted import never claims missing seconds.

        Args:
            path: Cached zip.
            instrument: Instrument.
            day: UTC date.
            sink: Archive writer.
            received_ms: Receipt time stamped on the aggregates, epoch
                milliseconds.
        """
        with self.open_lines(path) as lines:
            for chunk in day_seconds(
                lines, instrument, day, received_ms=received_ms
            ):
                sink(chunk, [])
        start = day_start_ms(day)
        sink([], [(start, start + DAY_MS)])

    async def import_day(
        self,
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: ArchiveSink,
        received_ms: int,
    ) -> bool:
        """Archives one day of an instrument's trades from the archive.

        Parsing runs in a worker thread so the event loop stays free;
        ``sink`` is therefore called from that thread.

        Args:
            instrument: Binance instrument.
            day: UTC date (a complete past day).
            sink: Archive writer (``TradeArchive.put`` of the instrument),
                called from the worker thread.
            received_ms: Receipt time stamped on the aggregates, epoch
                milliseconds.

        Returns:
            True when imported, False when the day is not published.

        Raises:
            errors.VenueUnavailableError: On download, checksum or parse
                failures (a file that fails to parse is discarded).
        """
        symbol = instrument.venue_market_id
        path = await self.daily_zip(symbol, day)
        if path is None:
            return False
        try:
            await asyncio.to_thread(
                self._import_file, path, instrument, day, sink, received_ms
            )
        except (ValueError, zipfile.BadZipFile, OSError) as err:
            self.discard(symbol, day)
            raise errors.VenueUnavailableError(
                f"binance archive {path.name} is unreadable: {err}"
            ) from err
        return True

    def discard(self, symbol: str, day: datetime.date) -> None:
        """Deletes a cached file (after it failed to parse).

        Args:
            symbol: Binance symbol.
            day: UTC date.
        """
        self.cached_path(symbol, day).unlink(missing_ok=True)

    def prune(self, before_ms: int) -> int:
        """Deletes cached files of days entirely before a cut-off.

        Args:
            before_ms: Retention cut-off, epoch milliseconds.

        Returns:
            Number of files deleted.
        """
        if not self.cache_dir.exists():
            return 0
        deleted = 0
        for path in self.cache_dir.glob("*/*-aggTrades-*.zip"):
            stamp = path.stem.rsplit("-aggTrades-", 1)[-1]
            try:
                day = datetime.date.fromisoformat(stamp)
            except ValueError:
                continue
            if day_start_ms(day) + DAY_MS <= before_ms:
                path.unlink(missing_ok=True)
                deleted += 1
        return deleted

    async def close(self) -> None:
        """Closes the HTTP client if owned."""
        if self._owns_client:
            await self._client.aclose()
