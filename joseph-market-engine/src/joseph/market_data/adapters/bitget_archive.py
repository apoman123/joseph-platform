# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Bitget public trade archive (``www.bitget.com/data-download``), USDT-M.

Bitget publishes every USDT-M futures trade as free downloads that need no
login, cookie or API key (verified live 2026-11-23):

* the official download page lists a day's files with ``POST
  https://www.bitget.com/v1/statistics/public/download/getPublicDataV2``
  and ``{"displaySymbol": [SYMBOL], "businessLine": 2, "businessType": 2,
  "dateType": 1, "beginTimeStr": day, "endTimeStr": day}`` (futures,
  trades, daily). The endpoint is the page's own, not part of Bitget's API
  documentation; it answers HTTP 429 after a few quick calls, so calls are
  spaced, and it may list a file twice;
* a day is split into numbered zip parts on ``img.bitgetimg.com`` (one for
  a quiet contract, 40 for BTCUSDT on 2026-11-21), each holding one CSV
  ``trade_id,timestamp,price,side,volume(quote),size(base)``. Days are cut
  at 00:00 UTC+8 and appear about a day later;
* rows equal ``fills-history`` rows of the same trade id (same taker
  ``side``, lowercase, and base-coin ``size``); timestamps are truncated to
  whole seconds (``1795190416000`` against REST ``1795190416083``), which
  keeps 1s buckets exact, and trades of one second follow trade-id order;
* no checksum is published (``.CHECKSUM`` returns 403). Zip CRCs catch
  corrupted bytes, and a day is accepted only if its trades fold to exactly
  Bitget's own UTC+8 ``1D`` kline: high, low, close, base and quote volume
  (the kline opens at the previous close, so its open is not compared). A
  missing or truncated part fails that check and nothing is covered.

Parts are cached per day; concurrency is bounded and a day that is not
listed yet is not re-requested for a while.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import datetime
import decimal
import io
import json
import logging
import pathlib
import re
import time
import zipfile
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Sequence
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

LIST_URL = (
    "https://www.bitget.com/v1/statistics/public/download/getPublicDataV2"
)
KLINE_URL = "https://api.bitget.com/api/v2/mix/market/history-candles"
FILE_HOST = "img.bitgetimg.com"
SOURCE = "bitget.trades.archive"
DAY_MS = 86_400_000
UTC8 = datetime.timezone(datetime.timedelta(hours=8))
_PART = re.compile(r"_(\d+)\.zip$")
_SIDES = {"buy": market.AggressorSide.BUY, "sell": market.AggressorSide.SELL}
_COLUMNS = ("trade_id", "timestamp", "price", "side", "size(base)")
"""Columns read; quote volume is recomputed from price and size."""
_MANIFEST = "parts.json"
_MISSING_RETRY_S = 1_800.0
"""Seconds before a day that was not listed yet is listed again."""
_CHUNK_BYTES = 1 << 16

ArchiveSink: TypeAlias = Callable[
    [list[market.Candle], list[recorded_trades.Interval]], None
]
"""Receives archived 1s aggregates and coverage (``TradeArchive.put``)."""
Sleep: TypeAlias = Callable[[float], Awaitable[None]]
"""Waits a number of seconds (``asyncio.sleep``)."""


def day_bounds(day: datetime.date) -> recorded_trades.Interval:
    """Returns the ``[start, end)`` of a Bitget (UTC+8) day.

    Args:
        day: Date in UTC+8.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(day.year, day.month, day.day, tzinfo=UTC8)
    start = int(moment.timestamp()) * 1000
    return start, start + DAY_MS


def day_of(ts_ms: int) -> datetime.date:
    """Returns the Bitget (UTC+8) day containing a time.

    Args:
        ts_ms: Epoch milliseconds.

    Returns:
        Date in UTC+8.
    """
    return datetime.datetime.fromtimestamp(ts_ms / 1000, UTC8).date()


def part_number(url: str) -> int:
    """Returns the part number of an archive file name.

    Args:
        url: File URL or name ending in ``_NNN.zip``.

    Returns:
        Part number.

    Raises:
        ValueError: If the name has no part number.
    """
    match = _PART.search(url)
    if match is None:
        raise ValueError(f"unexpected archive file name: {url}")
    return int(match.group(1))


def parse_rows(
    lines: Iterable[str],
    instrument: instruments.InstrumentId,
    received_ms: int,
) -> Iterator[market.Trade]:
    """Parses archive CSV lines (with header) into trades.

    Args:
        lines: CSV text lines.
        instrument: Instrument of every row.
        received_ms: Receipt time stamped on the trades.

    Yields:
        Trades in file order (``side`` is the taker side).

    Raises:
        ValueError: On a missing column or a malformed row.
    """
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or set(_COLUMNS) - set(reader.fieldnames):
        raise ValueError(
            f"unexpected Bitget archive header: {reader.fieldnames}"
        )
    for row in reader:
        side = _SIDES.get(str(row["side"]).strip().lower())
        if side is None:
            raise ValueError(f"bad side in Bitget archive row: {row}")
        yield market.Trade(
            instrument_id=instrument,
            trade_id=str(row["trade_id"]),
            venue_event_ts_ms=int(row["timestamp"]),
            received_at_ms=received_ms,
            price=decimals.to_decimal(row["price"]),
            base_quantity=decimals.to_decimal(row["size(base)"]),
            aggressor_side=side,
            is_aggregated=False,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DayTotals:
    """Totals a day's trades must reproduce (Bitget's UTC+8 ``1D`` kline).

    Attributes:
        high: Highest price.
        low: Lowest price.
        close: Last price.
        base_volume: Base volume.
        quote_volume: Quote volume.
    """

    high: decimal.Decimal
    low: decimal.Decimal
    close: decimal.Decimal
    base_volume: decimal.Decimal
    quote_volume: decimal.Decimal


def totals(trades: Iterable[market.Trade]) -> DayTotals | None:
    """Folds trades into the totals a ``1D`` kline publishes.

    Args:
        trades: Trades of one day (any order).

    Returns:
        Totals, or ``None`` without trades.
    """
    high = low = close = None
    last: tuple[int, int] | None = None
    volume = quote = decimals.ZERO
    for trade in trades:
        price = trade.price
        high = price if high is None else max(high, price)
        low = price if low is None else min(low, price)
        key = (trade.venue_event_ts_ms, int(trade.trade_id))
        if last is None or key > last:
            last, close = key, price
        volume += trade.base_quantity
        quote += price * trade.base_quantity
    if high is None or low is None or close is None:
        return None
    return DayTotals(high, low, close, volume, quote)


def matches(found: DayTotals | None, expected: DayTotals) -> bool:
    """Checks that a day's trades reproduce its kline.

    High, low, close and base volume must be equal; quote volume may differ
    by the kline's rounding (``recorded_trades.VOLUME_TOLERANCE``). The open
    is not compared: the kline opens at the previous day's close.

    Args:
        found: Totals of the archived trades.
        expected: The ``1D`` kline.

    Returns:
        True when they agree.
    """
    return (
        found is not None
        and (found.high, found.low, found.close, found.base_volume)
        == (expected.high, expected.low, expected.close, expected.base_volume)
        and recorded_trades.volumes_agree(
            found.quote_volume, expected.quote_volume
        )
    )


def _read_manifest(folder: pathlib.Path) -> list[pathlib.Path] | None:
    """Returns the parts of a completely downloaded day.

    Args:
        folder: Day directory.

    Returns:
        Part paths, or ``None`` when the day is not complete on disk.
    """
    manifest = folder / _MANIFEST
    if not manifest.exists():
        return None
    return [folder / str(name) for name in json.loads(manifest.read_text())]


def _write_manifest(folder: pathlib.Path, names: Sequence[str]) -> None:
    """Marks a day as completely downloaded.

    Args:
        folder: Day directory.
        names: Part file names in order.
    """
    (folder / _MANIFEST).write_text(json.dumps(list(names)))


class BitgetArchive:
    """Downloads, verifies and archives Bitget's daily trade files.

    Attributes:
        venue: Venue whose trades the archive holds.
        FOLD_TRADES: Trades folded per builder call.
        SINK_BARS: Aggregates handed to the archive per write.
        note: Capability note describing the archive.
        cache_dir: Directory of downloaded parts (``{SYMBOL}/{day}/``).
    """

    venue = instruments.Venue.BITGET
    FOLD_TRADES = 2_000
    SINK_BARS = 5_000
    note = (
        "Older seconds history is imported from Bitget's public trade "
        "archive (www.bitget.com/data-download, no key; days cut at 00:00 "
        "UTC+8, about a day late). No checksum is published, so a day is "
        "used only when its trades fold exactly to Bitget's own 1D kline."
    )

    def __init__(
        self,
        cache_dir: pathlib.Path | str,
        *,
        client: httpx.AsyncClient | None = None,
        max_concurrency: int = 2,
        timeout_s: float = 120.0,
        list_interval_s: float = 1.5,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Initializes the downloader.

        Args:
            cache_dir: Cache directory (created on demand).
            client: Optional HTTP client (tests inject respx-backed ones).
            max_concurrency: Concurrent day downloads (at least 1).
            timeout_s: Per-request timeout in seconds of the client created
                when ``client`` is not given.
            list_interval_s: Seconds between file-list calls, which the
                endpoint rate-limits.
            monotonic: Monotonic clock (seconds).
            sleep: Sleep function (injectable for tests).
        """
        self.cache_dir = pathlib.Path(cache_dir)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": http_lib.USER_AGENT}
        )
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._list_lock = asyncio.Lock()
        self._list_interval_s = list_interval_s
        self._last_list_s: float | None = None
        self._locks: dict[tuple[str, datetime.date], asyncio.Lock] = {}
        self._missing: dict[tuple[str, datetime.date], float] = {}
        self._monotonic = monotonic
        self._sleep = sleep

    @staticmethod  # noqa: GPY035 - implements DayArchive.day_of, which trade_recorder calls on the instance
    def day_of(ts_ms: int) -> datetime.date:
        """Returns the archive (UTC+8) day containing a time.

        Args:
            ts_ms: Epoch milliseconds.

        Returns:
            Date in UTC+8.
        """
        return day_of(ts_ms)

    @staticmethod  # noqa: GPY035 - implements DayArchive.day_bounds, which trade_recorder calls on the instance
    def day_bounds(day: datetime.date) -> recorded_trades.Interval:
        """Returns the ``[start, end)`` of an archive (UTC+8) day.

        Args:
            day: Date in UTC+8.

        Returns:
            Epoch milliseconds.
        """
        return day_bounds(day)

    def day_dir(self, symbol: str, day: datetime.date) -> pathlib.Path:
        """Returns the cache directory of one day.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.

        Returns:
            Path (may not exist yet).
        """
        return self.cache_dir / symbol / day.isoformat()

    async def _post_list(self, symbol: str, day: datetime.date) -> object:
        """Calls the file-list endpoint, spacing calls.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.

        Returns:
            Parsed JSON body.

        Raises:
            errors.RateLimitedError: On HTTP 429.
            errors.VenueUnavailableError: On other failures.
        """
        async with self._list_lock:
            if self._last_list_s is not None:
                wait = self._list_interval_s - (
                    self._monotonic() - self._last_list_s
                )
                if wait > 0:
                    await self._sleep(wait)
            body = {
                "displaySymbol": [symbol],
                "businessLine": 2,
                "businessType": 2,
                "dateType": 1,
                "beginTimeStr": day.isoformat(),
                "endTimeStr": day.isoformat(),
            }
            try:
                response = await self._client.post(LIST_URL, json=body)
            except httpx.HTTPError as err:
                raise errors.VenueUnavailableError(
                    f"bitget archive unreachable: {type(err).__name__}"
                ) from err
            finally:
                self._last_list_s = self._monotonic()
        if response.status_code == 429:
            raise errors.RateLimitedError("bitget archive list rate limited")
        if response.status_code >= 400:
            raise errors.VenueUnavailableError(
                f"bitget archive list returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as err:
            raise errors.VenueUnavailableError(
                "bitget archive list returned invalid JSON"
            ) from err

    async def list_parts(
        self, symbol: str, day: datetime.date
    ) -> list[str] | None:
        """Lists the part URLs of a day, ordered by part number.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.

        Returns:
            URLs, or ``None`` when the day is not listed (yet).

        Raises:
            errors.VenueUnavailableError: On failures (``RateLimitedError``
                on HTTP 429) or unexpected files.
        """
        payload = await self._post_list(symbol, day)
        if not isinstance(payload, dict) or str(payload.get("code")) != "200":
            raise errors.VenueUnavailableError(
                "bitget archive list rejected the request"
            )
        start = day_bounds(day)[0]
        # A set, because the endpoint may list a file twice.
        urls: set[str] = set()
        for entry in payload.get("data") or []:
            if int(entry.get("dateTime", -1)) != start:
                continue
            url = httpx.URL(str(entry["fileUrl"]))
            # Only Bitget's file host is trusted for downloads.
            if url.scheme != "https" or url.host != FILE_HOST:
                raise errors.VenueUnavailableError(
                    f"bitget archive listed an unexpected file host {url.host}"
                )
            urls.add(str(url))
        try:
            return sorted(urls, key=part_number) or None
        except ValueError as err:
            raise errors.VenueUnavailableError(str(err)) from err

    async def _download(self, url: str, target: pathlib.Path) -> None:
        """Streams one part to disk.

        Args:
            url: Part URL.
            target: Destination.

        Raises:
            errors.VenueUnavailableError: When the part is missing or the
                transfer fails.
        """
        part = target.with_suffix(".part")
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise errors.VenueUnavailableError(
                        f"bitget archive part {target.name} returned HTTP "
                        f"{response.status_code}"
                    )
                with part.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        handle.write(chunk)
        except httpx.HTTPError as err:
            _remove(part)
            raise errors.VenueUnavailableError(
                f"bitget archive download failed: {type(err).__name__}"
            ) from err
        except errors.VenueUnavailableError:
            _remove(part)
            raise
        _rename(part, target)

    async def day_files(
        self, symbol: str, day: datetime.date
    ) -> list[pathlib.Path] | None:
        """Returns a day's cached parts, downloading them once.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.

        Returns:
            Part files in order, or ``None`` when the day is not listed (a
            day found unlisted is not listed again for 30 minutes).

        Raises:
            errors.VenueUnavailableError: On list or download failures.
        """
        key = (symbol, day)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            folder = self.day_dir(symbol, day)
            cached = _read_manifest(folder)
            if cached is not None:
                return cached
            missed = self._missing.get(key)
            if missed is not None and (
                self._monotonic() - missed < _MISSING_RETRY_S
            ):
                return None
            async with self._semaphore:
                urls = await self.list_parts(symbol, day)
                if urls is None:
                    self._missing[key] = self._monotonic()
                    return None
                _make_dir(folder)
                names = [httpx.URL(url).path.rsplit("/", 1)[-1] for url in urls]
                for url, name in zip(urls, names, strict=True):
                    await self._download(url, folder / name)
                _write_manifest(folder, names)
                self._missing.pop(key, None)
                _LOG.info(
                    "cached %d bitget archive parts of %s", len(names), day
                )
                return [folder / name for name in names]

    async def expected_totals(
        self, symbol: str, day: datetime.date
    ) -> DayTotals:
        """Fetches Bitget's UTC+8 ``1D`` kline of a day.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.

        Returns:
            Totals the day's trades must reproduce.

        Raises:
            errors.VenueUnavailableError: When no kline can be fetched.
        """
        start, end = day_bounds(day)
        try:
            response = await self._client.get(
                KLINE_URL,
                params={
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    # ``1D`` (not ``1Dutc``) bars span UTC+8 days, as the
                    # archive's days do.
                    "granularity": "1D",
                    "endTime": end,
                    "limit": 2,
                },
            )
            rows = response.json().get("data") or []
        except (httpx.HTTPError, ValueError, AttributeError) as err:
            raise errors.VenueUnavailableError(
                f"bitget 1D kline unavailable: {type(err).__name__}"
            ) from err
        for row in rows:
            if int(row[0]) == start:
                return DayTotals(
                    high=decimals.to_decimal(row[2]),
                    low=decimals.to_decimal(row[3]),
                    close=decimals.to_decimal(row[4]),
                    base_volume=decimals.to_decimal(row[5]),
                    quote_volume=decimals.to_decimal(row[6]),
                )
        raise errors.VenueUnavailableError(
            f"no bitget 1D kline of {day} to verify the archive"
        )

    @staticmethod  # noqa: GPY035 - unit tests call BitgetArchive._lines on the class
    def _lines(paths: Sequence[pathlib.Path]) -> Iterator[str]:
        """Streams the CSV lines of every part in order.

        Args:
            paths: Part files.

        Yields:
            Lines; each part starts with its header line.

        Raises:
            ValueError: If a part does not hold exactly one CSV.
        """
        for path in paths:
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith(".csv")]
                if len(names) != 1:
                    raise ValueError(
                        f"{path.name} holds {len(names)} CSV files"
                    )
                with archive.open(names[0]) as raw:
                    yield from io.TextIOWrapper(
                        raw, encoding="utf-8", newline=""
                    )

    @classmethod
    def _trades(  # noqa: GPY036 - live network tests call BitgetArchive._trades on the class
        cls,
        paths: Sequence[pathlib.Path],
        instrument: instruments.InstrumentId,
        received_ms: int,
    ) -> Iterator[market.Trade]:
        """Parses every part of a day.

        Args:
            paths: Part files.
            instrument: Instrument.
            received_ms: Receipt time.

        Yields:
            Trades in file order.
        """
        # Every part repeats the CSV header, so each is parsed on its own.
        for path in paths:
            yield from parse_rows(cls._lines([path]), instrument, received_ms)

    def _import_files(
        self,
        paths: Sequence[pathlib.Path],
        instrument: instruments.InstrumentId,
        day: datetime.date,
        sink: ArchiveSink,
        received_ms: int,
        *,
        expected: DayTotals,
    ) -> None:
        """Verifies a day against its kline, then archives it.

        Args:
            paths: Part files.
            instrument: Instrument.
            day: UTC+8 date.
            sink: Archive writer.
            received_ms: Receipt time.
            expected: The day's ``1D`` kline.

        Raises:
            ValueError: When the trades do not reproduce the kline or a
                trade lies outside the day.
        """
        start, end = day_bounds(day)
        found = totals(self._trades(paths, instrument, received_ms))
        if not matches(found, expected):
            raise ValueError(
                f"bitget archive {day} does not match its 1D kline "
                f"({found} != {expected})"
            )
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
        for trade in self._trades(paths, instrument, received_ms):
            if not start <= trade.venue_event_ts_ms < end:
                raise ValueError("bitget archive trade outside its day")
            pending.append(trade)
            if len(pending) >= self.FOLD_TRADES:
                out.extend(
                    b
                    for b in builder.add(pending, received_ms)
                    if recorded_trades.archivable(b)
                )
                pending = []
            # Coverage goes with the last write only, so a day that fails
            # halfway is never reported as covered.
            if len(out) >= self.SINK_BARS:
                sink(out, [])
                out = []
        for bars in (
            builder.add(pending, received_ms),
            builder.finalize_through(end, received_ms),
        ):
            out.extend(b for b in bars if recorded_trades.archivable(b))
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
            instrument: Bitget instrument.
            day: UTC+8 date (a complete past day).
            sink: Archive writer.
            received_ms: Receipt time stamped on the aggregates.

        Returns:
            True when imported, False when the day is not listed yet.

        Raises:
            errors.VenueUnavailableError: On list, download or verification
                failures (a day whose files fail to parse or verify is
                discarded).
        """
        symbol = instrument.venue_market_id
        paths = await self.day_files(symbol, day)
        if paths is None:
            return False
        expected = await self.expected_totals(symbol, day)
        try:
            await asyncio.to_thread(
                self._import_files,
                paths,
                instrument,
                day,
                sink,
                received_ms,
                expected=expected,
            )
        except (ValueError, KeyError, zipfile.BadZipFile, OSError) as err:
            self.discard(symbol, day)
            raise errors.VenueUnavailableError(
                f"bitget archive {day} rejected: {err}"
            ) from err
        return True

    def discard(self, symbol: str, day: datetime.date) -> None:
        """Deletes a day's cached parts.

        Args:
            symbol: Bitget symbol.
            day: UTC+8 date.
        """
        folder = self.day_dir(symbol, day)
        if folder.exists():
            for path in folder.iterdir():
                path.unlink(missing_ok=True)
            folder.rmdir()

    def prune(self, before_ms: int) -> int:
        """Deletes cached days entirely before a cut-off.

        Args:
            before_ms: Retention cut-off; days that end at or before it are
                deleted.

        Returns:
            Number of days deleted.
        """
        if not self.cache_dir.exists():
            return 0
        deleted = 0
        for folder in self.cache_dir.glob("*/*"):
            try:
                day = datetime.date.fromisoformat(folder.name)
            except ValueError:
                continue
            if folder.is_dir() and day_bounds(day)[1] <= before_ms:
                self.discard(folder.parent.name, day)
                deleted += 1
        return deleted

    async def close(self) -> None:
        """Closes the HTTP client if owned."""
        if self._owns_client:
            await self._client.aclose()


def _make_dir(folder: pathlib.Path) -> None:
    """Creates a directory and its parents.

    Args:
        folder: Directory.
    """
    folder.mkdir(parents=True, exist_ok=True)


def _rename(source: pathlib.Path, target: pathlib.Path) -> None:
    """Moves a finished download into place.

    Args:
        source: Partial file.
        target: Final path.
    """
    source.replace(target)


def _remove(path: pathlib.Path) -> None:
    """Deletes a file if it exists.

    Args:
        path: File.
    """
    path.unlink(missing_ok=True)
