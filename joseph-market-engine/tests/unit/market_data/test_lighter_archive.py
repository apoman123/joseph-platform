"""Unit tests for the Lighter trade archive (remote Parquet range reads).

Every Parquet file here is a tiny synthetic one written by the test itself,
laid out like the real archive (time-ordered rows of all markets, the
internal ``market_id`` and the public ``public_market_index``), and served
through respx by a double that honors ``Range`` and ``If-Match``.
"""

from __future__ import annotations

import datetime
import decimal
import io
from collections.abc import Sequence

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import respx

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.domain import timeframes
from joseph.market_data.adapters import lighter
from joseph.market_data.adapters import lighter_archive
from joseph.market_data.app import trade_archive
from joseph.market_data.domain import aggregation
from joseph.market_data.domain import recorded_trades

_D = decimal.Decimal
_DAY = datetime.date(2026, 11, 21)
_START, _END = lighter_archive.day_bounds(_DAY)
_BTC = instruments.InstrumentId.parse("lighter:perp:BTC")
_DRV = instruments.InstrumentId.parse("lighter:perp:DRV")
_ETAG = '"etag-1"'
_BOOKS_URL = f"{lighter.REST_URL}/orderBooks"
_CANDLES_URL = f"{lighter.REST_URL}/candles"

Row = tuple[int, int, int, int | None, int, int, bool | None, str]
"""``(id, ms into the day, market_id, public index, price, size,
is_maker_ask, trade_type)``."""

_OTHERS = [
    (1, 100, 5, 5, 100, 1, True, "trade"),
    (2, 200, 6, 6, 100, 1, False, "trade"),
]
_MIXED = [
    (10, 1_000, 1, 1, 600_000, 100, True, "trade"),
    (11, 1_500, 1, 1, 600_010, 200, False, "trade"),
    (12, 1_500, 1, 1, 590_000, 1_000, False, "liquidation"),
    (13, 5_000, 1, 1, 600_000, 7, True, "deleverage"),
    (14, 7_200, 1, None, 600_020, 300, True, "trade"),
    (15, 8_000, 80, 4099, 33_000, 40, True, "trade"),
    (16, 8_000, 1, 4099, 33_000, 40, False, "trade"),
    (17, 86_399_000, 1, 1, 600_005, 50, False, "trade"),
]
_QUIET = [
    (20, 86_399_500, 0, 0, 30_000, 1, True, "trade"),
    (21, 86_399_600, 2, 2, 30_000, 1, True, "trade"),
]
_CANDLE = {
    "t": _START,
    "o": 59999.0,
    "h": 60002.0,
    "l": 59999.0,
    "c": 60000.5,
    "v": 0.0065,
    "V": 390.00825,
    "i": 17,
}
_BOOKS = {
    "code": 200,
    "order_books": [
        {
            "symbol": symbol,
            "market_id": market_id,
            "market_type": "perp",
            "supported_size_decimals": size_dec,
            "supported_price_decimals": price_dec,
            "min_base_amount": "0.1",
            "status": "active",
        }
        for symbol, market_id, price_dec, size_dec in (
            ("BTC", 1, 1, 5),
            ("DRV", 4099, 5, 1),
            ("QUIET", 9, 2, 2),
        )
    ],
}

Written = list[tuple[list[market.Candle], list[recorded_trades.Interval]]]


def _table(rows: Sequence[Row], drop: Sequence[str] = ()) -> pa.Table:
    """Builds archive rows in the real file's column order.

    Args:
        rows: Rows.
        drop: Columns left out.

    Returns:
        Table.
    """
    columns: dict[str, tuple[pa.DataType, list[object]]] = {
        "id": (pa.int64(), [r[0] for r in rows]),
        "size": (pa.int64(), [r[5] for r in rows]),
        "price": (pa.int64(), [r[4] for r in rows]),
        "usdc_amount": (pa.int64(), [r[4] * r[5] for r in rows]),
        "timestamp": (pa.int64(), [_START + r[1] for r in rows]),
        "tx_hash": (pa.string(), [f"0x{r[0]:064x}" for r in rows]),
        "market_id": (pa.int16(), [r[2] for r in rows]),
        "trade_type": (pa.string(), [r[7] for r in rows]),
        "is_maker_ask": (pa.bool_(), [r[6] for r in rows]),
        "public_market_index": (pa.int64(), [r[3] for r in rows]),
    }
    kept = {k: v for k, v in columns.items() if k not in drop}
    schema = pa.schema([(k, t) for k, (t, _) in kept.items()])
    return pa.table({k: v for k, (_, v) in kept.items()}, schema=schema)


def _parquet(
    groups: Sequence[Sequence[Row]],
    *,
    drop: Sequence[str] = (),
    statistics: bool = True,
    padding: int = 0,
) -> bytes:
    """Writes a Parquet file with one row group per ``groups`` entry.

    Args:
        groups: Rows of each row group.
        drop: Columns left out.
        statistics: Whether column statistics are written.
        padding: Bytes of key-value metadata (to grow the footer).

    Returns:
        File bytes.
    """
    tables = [_table(rows, drop) for rows in groups]
    schema = tables[0].schema
    if padding:
        schema = schema.with_metadata({b"pad": b"x" * padding})
    buffer = io.BytesIO()
    with pq.ParquetWriter(
        buffer, schema, compression="zstd", write_statistics=statistics
    ) as writer:
        for table in tables:
            writer.write_table(table.cast(schema))
    return buffer.getvalue()


class _Server:
    """S3 double: range requests, ETag pinning and request recording.

    Attributes:
        data: File bytes.
        etag: Current ETag.
        ranges: Served ``(start, end)`` ranges in request order.
        if_match: ``If-Match`` header of each request.
    """

    def __init__(self, data: bytes) -> None:
        """Initializes the double.

        Args:
            data: File bytes.
        """
        self.data = data
        self.etag = _ETAG
        self.ranges: list[tuple[int, int]] = []
        self.if_match: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serves one range request.

        Args:
            request: Request.

        Returns:
            HTTP 206 with the range, or 412 for a stale ``If-Match``.
        """
        pinned = request.headers.get("if-match")
        self.if_match.append(pinned)
        if pinned is not None and pinned != self.etag:
            return httpx.Response(412)
        spec = request.headers["range"].removeprefix("bytes=")
        size = len(self.data)
        if spec.startswith("-"):
            start, end = max(0, size - int(spec[1:])), size
        else:
            first, last = spec.split("-")
            start, end = int(first), int(last) + 1
        self.ranges.append((start, end))
        return httpx.Response(
            206,
            content=self.data[start:end],
            headers={
                "Content-Range": f"bytes {start}-{end - 1}/{size}",
                "ETag": self.etag,
            },
        )


def _archive(**kwargs: object) -> lighter_archive.LighterArchive:
    """Builds an archive client on an injected HTTP client.

    Args:
        **kwargs: Constructor overrides.

    Returns:
        Client.
    """
    values: dict[str, object] = {"coalesce_gap": 0}
    values.update(kwargs)
    return lighter_archive.LighterArchive(
        client=httpx.AsyncClient(),
        **values,  # type: ignore[arg-type]
    )


def _routes(
    router: respx.Router,
    archive: lighter_archive.LighterArchive,
    server: _Server,
    candles: Sequence[dict[str, object]] = (_CANDLE,),
) -> None:
    """Serves the file, the market list and the 1d candle.

    Args:
        router: respx router.
        archive: Client (for the day URL).
        server: File double.
        candles: Candle rows.
    """
    router.get(archive.url(_DAY)).mock(side_effect=server)
    router.get(_BOOKS_URL).mock(return_value=httpx.Response(200, json=_BOOKS))
    router.get(_CANDLES_URL).mock(
        return_value=httpx.Response(
            200, json={"code": 200, "r": "1d", "c": list(candles)}
        )
    )


def _sink(written: Written) -> lighter_archive.ArchiveSink:
    """Returns a sink that records writes.

    Args:
        written: Destination list.

    Returns:
        Sink.
    """

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Records one write.

        Args:
            rows: Aggregates.
            covered: Coverage.
        """
        written.append((rows, covered))

    return sink


def _chunks(
    data: bytes, group: int, names: Sequence[str]
) -> list[tuple[int, int]]:
    """Returns the byte ranges of some column chunks of a file.

    Args:
        data: File bytes.
        group: Row group.
        names: Columns.

    Returns:
        Ranges.
    """
    meta = pq.read_metadata(io.BytesIO(data))
    order = meta.schema.to_arrow_schema().names
    return [
        lighter_archive.chunk_range(
            meta.row_group(group).column(order.index(n))
        )
        for n in names
    ]


def _touched(fetched: Sequence[tuple[int, int]], span: tuple[int, int]) -> bool:
    """Checks whether any fetched range overlaps a span.

    Args:
        fetched: Fetched ranges.
        span: Span.

    Returns:
        True on overlap.
    """
    return any(a < span[1] and span[0] < b for a, b in fetched)


def test_days_are_utc_and_files_are_dated() -> None:
    """Archive days are UTC dates; each has one file URL."""
    assert _START == 1_795_219_200_000
    assert _END - _START == lighter_archive.DAY_MS
    assert lighter_archive.day_of(_START) == _DAY
    assert lighter_archive.day_of(_START - 1) == _DAY - datetime.timedelta(1)
    archive = lighter_archive.LighterArchive(client=httpx.AsyncClient())
    assert archive.day_bounds(_DAY) == (_START, _END)
    assert archive.day_of(_END - 1) == _DAY
    assert archive.url(_DAY) == (
        "https://aws-public-blockchain.s3.amazonaws.com/v1.1/lighter/core/"
        "mainnet/trades/date=2026-11-21/trades.parquet"
    )


async def test_import_reads_only_needed_chunks_and_archives_the_day() -> None:
    """Statistics and the market probe skip row groups; the day is archived.

    Liquidation and deleverage rows are left out (Lighter's candles do not
    count them), a null public index falls back to ``market_id``, and
    ``is_maker_ask`` true makes the aggressor a buyer.
    """
    data = _parquet([_OTHERS, _MIXED, _QUIET])
    server = _Server(data)
    archive = _archive()
    archive.FOLD_TRADES = 1
    archive.SINK_BARS = 1
    store = trade_archive.MemoryTradeArchive()
    written: Written = []

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Writes into the store and records the write.

        Args:
            rows: Aggregates.
            covered: Coverage.
        """
        written.append((rows, covered))
        store.put(_BTC, rows, covered)

    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, server)
        assert await archive.import_day(_BTC, _DAY, sink, 7)
    assert store.coverage(_BTC, 0, _END + 1) == [(_START, _END)]
    assert all(covered == [] for _, covered in written[:-1])
    seconds = store.seconds(_BTC, _START, _END)
    assert [s.start_ms - _START for s in seconds] == [1_000, 7_000, 86_399_000]
    assert {s.source for s in seconds} == {lighter_archive.SOURCE}
    day = aggregation.fold(
        seconds,
        timeframe=timeframes.Timeframe.D1,
        start_ms=_START,
        end_ms=_END,
        is_final=True,
        source="x",
    )
    assert (day.open, day.high, day.low, day.close) == (
        _D("60000.0"),
        _D("60002.0"),
        _D("60000.0"),
        _D("60000.5"),
    )
    assert day.base_volume == _D("0.00650")
    assert day.quote_turnover == _D("390.008250")
    assert day.taker_buy_base_volume == _D("0.00400")
    assert day.trade_count == 4
    column_reads = server.ranges[1:]
    assert server.if_match[0] is None
    assert set(server.if_match[1:]) == {_ETAG}
    assert archive.requests == len(server.ranges)
    assert archive.bytes_received == sum(b - a for a, b in server.ranges)
    every = _table(_MIXED).column_names
    assert not any(_touched(column_reads, c) for c in _chunks(data, 0, every))
    for unused in _chunks(data, 1, ["tx_hash", "usdc_amount"]):
        assert not _touched(column_reads, unused)
    assert all(
        _touched(column_reads, c)
        for c in _chunks(data, 1, lighter_archive.TRADE_COLUMNS)
    )
    (probe,) = _chunks(data, 2, ["public_market_index"])
    assert _touched(column_reads, probe)
    for skipped in _chunks(data, 2, ["market_id", "price", "id"]):
        assert not _touched(column_reads, skipped)
    await archive.close()


async def test_public_index_maps_markets() -> None:
    """The public index identifies a market even where ``market_id`` differs."""
    archive = _archive()
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(_parquet([_OTHERS, _MIXED, _QUIET])))
        drv = await archive.read_day(_DAY, await archive.market(_DRV))
        btc = await archive.read_day(_DAY, await archive.market(_BTC))
    assert drv is not None and btc is not None
    assert drv.column("id").to_pylist() == [15, 16]
    assert btc.column("id").to_pylist() == [10, 11, 14, 17]
    assert btc.schema == lighter_archive.SCHEMA


@pytest.mark.parametrize(
    ("case", "change"),
    [
        ("volume", {"v": 0.0064}),
        ("close", {"c": 60000.4}),
        ("high", {"h": 60003.0}),
        ("last trade", {"i": 16}),
        ("liquidations counted", {"v": 0.0165}),
    ],
)
async def test_a_day_that_does_not_match_its_candle_writes_nothing(
    case: str, change: dict[str, object]
) -> None:
    """Any disagreement with the 1d candle rejects the day before writing."""
    archive = _archive()
    written: Written = []
    with respx.mock(assert_all_called=False) as router:
        _routes(
            router,
            archive,
            _Server(_parquet([_MIXED])),
            candles=[{**_CANDLE, **change}],
        )
        with pytest.raises(errors.VenueUnavailableError, match="1d candle"):
            await archive.import_day(_BTC, _DAY, _sink(written), 0)
    assert written == [], case


def test_matches_folds_the_open_into_high_and_low() -> None:
    """The candle opens at the previous close; empty days need no volume."""
    found = lighter_archive.DayTotals(
        _D(10), _D(12), _D(9), _D(11), _D(5), _D(55), 7
    )
    gap_up = lighter_archive.DayTotals(
        _D(8), _D(12), _D(8), _D(11), _D(5), _D("55.00001"), None
    )
    assert lighter_archive.matches(found, found)
    assert lighter_archive.matches(found, gap_up)
    assert not lighter_archive.matches(
        found,
        lighter_archive.DayTotals(
            _D(8), _D(12), _D(9), _D(11), _D(5), _D(55), 7
        ),
    )
    assert not lighter_archive.matches(found, None)
    assert lighter_archive.matches(None, None)
    zero = lighter_archive.DayTotals(
        _D(1), _D(1), _D(1), _D(1), _D(0), _D(0), 3
    )
    assert lighter_archive.matches(None, zero)
    assert not lighter_archive.matches(None, found)
    empty = lighter_archive.SCHEMA.empty_table()
    assert (
        lighter_archive.totals(empty, lighter_archive.Market(1, 1, 5)) is None
    )


def test_rows_become_trades_with_the_taker_side() -> None:
    """``is_maker_ask`` true is a taker buy; integers scale by decimals."""
    scale = lighter_archive.Market(1, 1, 5)
    table = pa.table(
        {
            "timestamp": [_START, _START + 1],
            "id": [5, 6],
            "price": [600_001, 599_999],
            "size": [123, 1],
            "is_maker_ask": [True, False],
        },
        schema=lighter_archive.SCHEMA,
    )
    batches = list(lighter_archive.iter_trades(table, _BTC, scale, 9, batch=1))
    assert [len(b) for b in batches] == [1, 1]
    buy, sell = (b[0] for b in batches)
    assert buy.aggressor_side is market.AggressorSide.BUY
    assert sell.aggressor_side is market.AggressorSide.SELL
    assert (buy.trade_id, buy.price, buy.base_quantity) == (
        "5",
        _D("60000.1"),
        _D("0.00123"),
    )
    assert (buy.venue_event_ts_ms, buy.received_at_ms) == (_START, 9)
    assert not buy.is_aggregated
    assert scale.quote(600_001 * 123) == _D("73.800123")


async def test_unpublished_day_is_not_rerequested_until_later() -> None:
    """HTTP 404 means not yet published; it is retried after a while."""
    now = [0.0]
    archive = _archive(monotonic=lambda: now[0])
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(b""))
        route = router.get(archive.url(_DAY)).mock(
            return_value=httpx.Response(404)
        )
        assert not await archive.import_day(_BTC, _DAY, _sink([]), 0)
        assert not await archive.import_day(_BTC, _DAY, _sink([]), 0)
        assert route.call_count == 1
        now[0] = 2_000.0
        assert not await archive.import_day(_BTC, _DAY, _sink([]), 0)
        assert route.call_count == 2
    assert archive.prune(_START) == 0
    assert archive.prune(_END) == 1


async def test_empty_day_is_covered_when_the_venue_has_no_candle() -> None:
    """A market without trades that day is covered with no aggregates."""
    archive = _archive()
    written: Written = []
    quiet = instruments.InstrumentId.parse("lighter:perp:QUIET")
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(_parquet([_MIXED])), candles=[])
        assert await archive.import_day(quiet, _DAY, _sink(written), 0)
    assert written == [([], [(_START, _END)])]


@pytest.mark.parametrize(
    ("groups", "drop", "statistics", "expected"),
    [
        ([_MIXED], (), True, ("public_market_index", "market_id")),
        ([_OTHERS], (), True, ()),
        ([_QUIET], (), True, ("public_market_index",)),
        ([[(1, 1, 1, None, 1, 1, True, "trade")]], (), True, ("market_id",)),
        ([_OTHERS], (), False, ("public_market_index", "market_id")),
        ([_MIXED], ("public_market_index",), True, ("market_id",)),
        ([_OTHERS], ("public_market_index",), True, ()),
    ],
    ids=[
        "mixed",
        "excluded",
        "public",
        "null-public",
        "no-stats",
        "old",
        "old-excluded",
    ],
)
def test_statistics_choose_the_market_columns(
    groups: list[list[Row]],
    drop: tuple[str, ...],
    statistics: bool,
    expected: tuple[str, ...],
) -> None:
    """The key is coalesce(public index, market id); stats prune it."""
    meta = pq.read_metadata(
        io.BytesIO(_parquet(groups, drop=drop, statistics=statistics))
    )
    names = meta.schema.to_arrow_schema().names
    chosen = lighter_archive.key_columns(meta.row_group(0), names, 1)
    assert chosen == expected


def test_a_file_without_market_ids_is_rejected() -> None:
    """Without ``market_id`` the market cannot be identified."""
    meta = pq.read_metadata(io.BytesIO(_parquet([_MIXED], drop=["market_id"])))
    names = meta.schema.to_arrow_schema().names
    with pytest.raises(ValueError, match="market_id"):
        lighter_archive.key_columns(meta.row_group(0), names, 1)


async def test_old_files_without_a_public_index_use_market_id() -> None:
    """Files of the old layout are read through ``market_id`` alone."""
    data = _parquet([_MIXED], drop=["public_market_index"])
    archive = _archive()
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(data))
        table = await archive.read_day(_DAY, lighter_archive.Market(1, 1, 5))
    assert table is not None
    assert table.column("id").to_pylist() == [10, 11, 14, 16, 17]


async def test_a_large_footer_is_completed_with_a_second_read() -> None:
    """A footer longer than the tail read is fetched in a second range."""
    data = _parquet([_MIXED], padding=100_000)
    server = _Server(data)
    archive = _archive()
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, server)
        remote = await archive.open_file(_DAY)
    assert remote is not None
    footer = int.from_bytes(data[-8:-4], "little")
    assert footer + 8 > lighter_archive.TAIL_BYTES
    size = len(data)
    assert server.ranges[:2] == [
        (size - lighter_archive.TAIL_BYTES, size),
        (size - 8 - footer, size - lighter_archive.TAIL_BYTES),
    ]
    assert remote.metadata.num_rows == len(_MIXED)


def _replies(
    first: httpx.Response, *rest: httpx.Response
) -> list[httpx.Response]:
    """Lists scripted responses.

    Args:
        first: First response.
        *rest: Following responses.

    Returns:
        Responses.
    """
    return [first, *rest]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (httpx.Response(200, content=b"whole file"), "HTTP 200"),
        (httpx.Response(500), "HTTP 500"),
        (
            httpx.Response(206, content=b"x", headers={"Content-Range": "?"}),
            "Content-Range",
        ),
        (
            httpx.Response(
                206,
                content=b"not parquet at all",
                headers={"Content-Range": "bytes 0-17/18"},
            ),
            "not a Parquet file",
        ),
        (
            httpx.Response(
                206,
                content=b"PAR1"
                + bytes(4)
                + (1_000).to_bytes(4, "little")
                + b"PAR1",
                headers={"Content-Range": "bytes 0-15/16"},
            ),
            "not a Parquet file",
        ),
    ],
    ids=[
        "ignored-range",
        "server-error",
        "content-range",
        "not-parquet",
        "footer-too-long",
    ],
)
async def test_bad_footer_replies_fail_the_day(
    reply: httpx.Response, message: str
) -> None:
    """Replies that are not the requested range of a Parquet file fail."""
    archive = _archive()
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(b""))
        router.get(archive.url(_DAY)).mock(return_value=reply)
        with pytest.raises(errors.VenueUnavailableError, match=message):
            await archive.import_day(_BTC, _DAY, _sink([]), 0)


async def test_transport_errors_changes_and_short_ranges_fail_the_day() -> None:
    """Unreachable hosts, a changed file and short bodies are errors."""
    data = _parquet([_MIXED])
    archive = _archive()
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(data))
        route = router.get(archive.url(_DAY))
        route.mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(errors.VenueUnavailableError, match="unreachable"):
            await archive.import_day(_BTC, _DAY, _sink([]), 0)
        changed = _Server(data)

        def change_after_footer(request: httpx.Request) -> httpx.Response:
            """Serves the footer, then reports a new ETag.

            Args:
                request: Request.

            Returns:
                Response.
            """
            response = changed(request)
            changed.etag = '"etag-2"'
            return response

        route.mock(side_effect=change_after_footer)
        with pytest.raises(errors.VenueUnavailableError, match="changed"):
            await archive.import_day(_BTC, _DAY, _sink([]), 0)
        short = _Server(data)

        def truncate_columns(request: httpx.Request) -> httpx.Response:
            """Serves the footer whole and column ranges one byte short.

            Args:
                request: Request.

            Returns:
                Response.
            """
            response = short(request)
            if len(short.ranges) == 1:
                return response
            start, end = short.ranges[-1]
            return httpx.Response(
                206,
                content=data[start : end - 1],
                headers={
                    "Content-Range": f"bytes {start}-{end - 2}/{len(data)}"
                },
            )

        route.mock(side_effect=truncate_columns)
        with pytest.raises(errors.VenueUnavailableError, match="byte range"):
            await archive.import_day(_BTC, _DAY, _sink([]), 0)
    await archive.close()


@pytest.mark.parametrize(
    ("rows", "drop", "message"),
    [
        (_MIXED, ("is_maker_ask",), "lacks columns"),
        ([(10, 1_000, 1, 1, 600_000, 100, None, "trade")], (), "null"),
        ([(10, 86_400_000, 1, 1, 600_000, 100, True, "trade")], (), "outside"),
    ],
    ids=["missing-column", "null-side", "next-day"],
)
async def test_malformed_files_are_rejected(
    rows: list[Row], drop: tuple[str, ...], message: str
) -> None:
    """Missing columns, null fields and foreign days reject the import."""
    archive = _archive()
    written: Written = []
    with respx.mock(assert_all_called=False) as router:
        _routes(router, archive, _Server(_parquet([rows], drop=drop)))
        with pytest.raises(errors.VenueUnavailableError, match=message):
            await archive.import_day(_BTC, _DAY, _sink(written), 0)
    assert written == []


async def test_market_list_is_cached_and_validated() -> None:
    """Markets are listed once per TTL; unknown or malformed ones fail."""
    now = [0.0]
    archive = _archive(monotonic=lambda: now[0])
    with respx.mock(assert_all_called=False) as router:
        books = router.get(_BOOKS_URL).mock(
            return_value=httpx.Response(200, json=_BOOKS)
        )
        assert await archive.market(_BTC) == lighter_archive.Market(1, 1, 5)
        assert await archive.market(_DRV) == lighter_archive.Market(4099, 5, 1)
        assert books.call_count == 1
        with pytest.raises(errors.NotFoundError, match="NOPE"):
            await archive.market(
                instruments.InstrumentId.parse("lighter:perp:NOPE")
            )
        now[0] = 4_000.0
        books.mock(
            return_value=httpx.Response(
                200,
                json={
                    "order_books": [{"symbol": "BTC", "market_type": "perp"}]
                },
            )
        )
        with pytest.raises(errors.VenueUnavailableError, match="orderBooks"):
            await archive.market(_BTC)
        assert books.call_count == 2


async def test_candles_are_read_for_the_day_only() -> None:
    """Only the candle starting at the day counts; malformed ones fail."""
    archive = _archive()
    scale = lighter_archive.Market(1, 1, 5)
    with respx.mock(assert_all_called=False) as router:
        candles = router.get(_CANDLES_URL)
        candles.mock(
            return_value=httpx.Response(
                200, json={"c": [{**_CANDLE, "t": _START - 86_400_000}]}
            )
        )
        assert await archive.expected_totals(_BTC, scale, _DAY) is None
        candles.mock(
            return_value=httpx.Response(200, json={"c": [dict(_CANDLE)]})
        )
        expected = await archive.expected_totals(_BTC, scale, _DAY)
        assert expected == lighter_archive.DayTotals(
            _D("59999.0"),
            _D("60002.0"),
            _D("59999.0"),
            _D("60000.5"),
            _D("0.00650"),
            _D("390.008250"),
            17,
        )
        without_id = {k: v for k, v in _CANDLE.items() if k != "i"}
        candles.mock(return_value=httpx.Response(200, json={"c": [without_id]}))
        found = await archive.expected_totals(_BTC, scale, _DAY)
        assert found is not None and found.last_trade_id is None
        candles.mock(
            return_value=httpx.Response(200, json={"c": [{"t": _START}]})
        )
        with pytest.raises(errors.VenueUnavailableError, match="unreadable"):
            await archive.expected_totals(_BTC, scale, _DAY)


def test_sparse_file_serves_fetched_ranges_only() -> None:
    """Reads may span adjacent buffers; unfetched bytes raise."""
    view = lighter_archive.SparseFile(13, {0: b"abc", 3: b"def", 10: b"xyz"})
    assert view.readable() and view.seekable()
    assert view.read(6) == b"abcdef"
    assert view.tell() == 6
    assert view.seek(-3, io.SEEK_END) == 10
    assert view.read() == b"xyz"
    assert view.read(5) == b""
    assert view.seek(-12, io.SEEK_CUR) == 1
    assert view.read(2) == b"bc"
    view.seek(5)
    with pytest.raises(OSError, match="byte 6"):
        view.read(3)
    empty = lighter_archive.SparseFile(4, {2: b"zz"})
    with pytest.raises(OSError, match="byte 0"):
        empty.read(1)


def test_coalesce_merges_close_ranges() -> None:
    """Touching ranges always merge; others merge within the gap."""
    ranges = [(10, 20), (0, 5), (20, 30), (100, 110)]
    assert lighter_archive.coalesce(ranges, 0) == [(0, 5), (10, 30), (100, 110)]
    assert lighter_archive.coalesce(ranges, 70) == [(0, 110)]


async def test_close_only_closes_an_owned_client() -> None:
    """An injected client stays open; an owned one is closed."""
    client = httpx.AsyncClient()
    await lighter_archive.LighterArchive(client=client).close()
    assert not client.is_closed
    await lighter_archive.LighterArchive().close()
    await client.aclose()
