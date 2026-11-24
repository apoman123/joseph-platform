# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the data.binance.vision aggTrades archive client."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import hashlib
import io
import pathlib
import zipfile

import httpx
import pytest
import respx

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import binance_vision
from joseph.market_data.domain import recorded_trades
from joseph.market_data.domain import trade_bars
from joseph.market_data.domain import trades as trades_lib

_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("binance:perp:BTCDOMUSDT")
_DAY = datetime.date(2026, 11, 21)
_HOST = "https://data.binance.vision"
_PATH = (
    "/data/futures/um/daily/aggTrades/BTCDOMUSDT/"
    "BTCDOMUSDT-aggTrades-2026-11-21.zip"
)
_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "fixtures"
    / "venues"
    / "binance"
)


def _csv() -> str:
    """Returns the captured head of a real daily file (with header).

    Returns:
        CSV text.
    """
    return (_FIXTURE / "vision_aggtrades_head.csv").read_text()


def _zip(text: str, name: str = "BTCDOMUSDT-aggTrades-2026-11-21.csv") -> bytes:
    """Zips CSV text like Binance does.

    Args:
        text: CSV content.
        name: Member name.

    Returns:
        Zip bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _parts(root: pathlib.Path) -> list[pathlib.Path]:
    """Lists leftover partial downloads.

    Args:
        root: Cache directory.

    Returns:
        ``.part`` files.
    """
    return list(root.rglob("*.part"))


def _checksum(payload: bytes) -> str:
    """Builds a ``.CHECKSUM`` body.

    Args:
        payload: File bytes.

    Returns:
        ``<sha256>  <name>``.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return f"{digest}  BTCDOMUSDT-aggTrades-2026-11-21.zip\n"


def test_parse_rows_with_header_follows_buyer_maker_semantics() -> None:
    """``is_buyer_maker=true`` is a SELL aggressor; ids and times kept."""
    trades = list(binance_vision.parse_rows(_csv().splitlines(), _ID, 5))
    assert len(trades) == 40
    first = trades[0]
    assert first.trade_id == "51387216"
    assert first.venue_event_ts_ms == 1_795_219_200_379
    assert (first.price, first.base_quantity) == (_D("5841.1"), _D("0.013"))
    assert first.aggressor_side is market.AggressorSide.BUY
    assert trades[3].aggressor_side is market.AggressorSide.SELL
    assert all(t.is_aggregated and t.received_at_ms == 5 for t in trades)


def test_parse_rows_without_header_and_with_microseconds() -> None:
    """Old header-less files parse positionally; microseconds are scaled."""
    lines = [
        "7,100.5,2,10,11,1795219200379123,True",
        "",
        "8,100.6,1,12,12,1795219200380,FALSE",
    ]
    trades = list(binance_vision.parse_rows(lines, _ID, 0))
    assert [t.venue_event_ts_ms for t in trades] == [
        1_795_219_200_379,
        1_795_219_200_380,
    ]
    assert [t.aggressor_side for t in trades] == [
        market.AggressorSide.SELL,
        market.AggressorSide.BUY,
    ]


@pytest.mark.parametrize(
    "lines",
    [
        ["agg_trade_id,price,quantity"],
        ["1,2,3"],
        ["1,2,3,4,5,6,maybe"],
    ],
    ids=["header", "short", "flag"],
)
def test_parse_rows_rejects_malformed_input(lines: list[str]) -> None:
    """Unexpected headers, short rows and bad flags raise."""
    with pytest.raises(ValueError):
        list(binance_vision.parse_rows(lines, _ID, 0))


def test_parse_checksum() -> None:
    """The digest is the first token; malformed files raise."""
    real = (_FIXTURE / "vision_aggtrades.zip.CHECKSUM").read_text()
    assert binance_vision.parse_checksum(real) == (
        "86964c96459ba7aa9d66ea7d852555d5f1f75463a26df9ee83eb1cde61bdb4c9"
    )
    for bad in ("", "nothex  name", "ab" * 31):
        with pytest.raises(ValueError):
            binance_vision.parse_checksum(bad)


def test_day_seconds_match_the_live_builder() -> None:
    """Streamed archive aggregates equal bars built from the same trades."""
    lines = _csv().splitlines()
    chunks = list(
        binance_vision.day_seconds(lines, _ID, _DAY, received_ms=9, batch=5)
    )
    assert len(chunks) > 1
    bars = [bar for chunk in chunks for bar in chunk]
    trades = list(binance_vision.parse_rows(lines, _ID, 9))
    start = binance_vision.day_start_ms(_DAY)
    coverage = trades_lib.TradeCoverage(
        tuple(trades), start, start + binance_vision.DAY_MS
    )
    expected = [
        b
        for b in trade_bars.bars_from_coverage(
            coverage,
            _ID,
            bars[0].timeframe,
            source=binance_vision.SOURCE,
            now_ms=9,
        )
        if recorded_trades.archivable(b)
    ]
    assert [(b.start_ms, b.close, b.base_volume) for b in bars] == [
        (b.start_ms, b.close, b.base_volume) for b in expected
    ]
    assert sum(b.base_volume for b in bars) == sum(
        t.base_quantity for t in trades
    )
    assert sum(b.taker_buy_base_volume or 0 for b in bars) == sum(
        t.base_quantity
        for t in trades
        if t.aggressor_side is market.AggressorSide.BUY
    )
    assert all(b.source == "binance.trades.vision" for b in bars)


def test_day_seconds_rejects_trades_of_another_day() -> None:
    """A row outside the file's day fails the import."""
    lines = ["1,10,1,1,1,1795305600000,false"]
    with pytest.raises(ValueError, match="outside its day"):
        list(binance_vision.day_seconds(lines, _ID, _DAY, received_ms=0))


def test_dates_and_names() -> None:
    """Day boundaries and file names follow the archive layout."""
    assert binance_vision.day_start_ms(_DAY) == 1_795_219_200_000
    assert binance_vision.utc_day(1_795_219_200_000 - 1) == datetime.date(
        2026, 11, 20
    )
    assert (
        binance_vision.file_name("BTCUSDT", _DAY)
        == "BTCUSDT-aggTrades-2026-11-21.zip"
    )


async def test_download_is_verified_cached_and_imported(
    tmp_path: pathlib.Path,
) -> None:
    """A verified day is downloaded once, cached and archived in chunks."""
    payload = _zip(_csv())
    vision = binance_vision.BinanceVision(tmp_path, client=httpx.AsyncClient())
    with respx.mock(assert_all_called=True) as router:
        zip_route = router.get(f"{_HOST}{_PATH}").mock(
            return_value=httpx.Response(200, content=payload)
        )
        router.get(f"{_HOST}{_PATH}.CHECKSUM").mock(
            return_value=httpx.Response(200, text=_checksum(payload))
        )
        path = await vision.daily_zip("BTCDOMUSDT", _DAY)
        again = await vision.daily_zip("BTCDOMUSDT", _DAY)
        received: list[tuple[int, list[recorded_trades.Interval]]] = []

        def sink(
            rows: list[market.Candle], covered: list[recorded_trades.Interval]
        ) -> None:
            """Records archive writes.

            Args:
                rows: Aggregates.
                covered: Coverage.
            """
            received.append((len(rows), covered))

        assert await vision.import_day(_ID, _DAY, sink, 1)
    assert path == again == vision.cached_path("BTCDOMUSDT", _DAY)
    assert path is not None and path.read_bytes() == payload
    assert zip_route.call_count == 1
    start = binance_vision.day_start_ms(_DAY)
    assert received[-1] == (0, [(start, start + binance_vision.DAY_MS)])
    assert sum(n for n, _ in received) > 0
    await vision.close()


async def test_checksum_mismatch_is_rejected_and_not_cached(
    tmp_path: pathlib.Path,
) -> None:
    """A corrupted download raises and leaves no cached file."""
    payload = _zip(_csv())
    vision = binance_vision.BinanceVision(tmp_path, client=httpx.AsyncClient())
    with respx.mock() as router:
        router.get(f"{_HOST}{_PATH}").mock(
            return_value=httpx.Response(200, content=payload + b"x")
        )
        router.get(f"{_HOST}{_PATH}.CHECKSUM").mock(
            return_value=httpx.Response(200, text=_checksum(payload))
        )
        with pytest.raises(binance_vision.ChecksumMismatchError):
            await vision.daily_zip("BTCDOMUSDT", _DAY)
        router.get(f"{_HOST}{_PATH}.CHECKSUM").mock(
            return_value=httpx.Response(200, text="garbage")
        )
        with pytest.raises(binance_vision.ChecksumMismatchError):
            await vision.daily_zip("BTCDOMUSDT", _DAY)
    assert not vision.cached_path("BTCDOMUSDT", _DAY).exists()
    assert _parts(tmp_path) == []


async def test_unpublished_day_returns_none_and_is_not_rerequested(
    tmp_path: pathlib.Path,
) -> None:
    """Today's file does not exist yet: ``None``, remembered for a while."""
    now = [100.0]
    vision = binance_vision.BinanceVision(
        tmp_path, client=httpx.AsyncClient(), monotonic=lambda: now[0]
    )
    with respx.mock() as router:
        checksum = router.get(f"{_HOST}{_PATH}.CHECKSUM").mock(
            return_value=httpx.Response(404)
        )
        assert await vision.daily_zip("BTCDOMUSDT", _DAY) is None
        assert await vision.daily_zip("BTCDOMUSDT", _DAY) is None
        assert checksum.call_count == 1
        now[0] += 3_600
        payload = _zip(_csv())
        checksum.mock(return_value=httpx.Response(200, text=_checksum(payload)))
        router.get(f"{_HOST}{_PATH}").mock(return_value=httpx.Response(404))
        assert await vision.daily_zip("BTCDOMUSDT", _DAY) is None
        assert (
            await vision.import_day(_ID, _DAY, lambda rows, covered: None, 0)
            is False
        )


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.ConnectError("down")],
    ids=["http-500", "transport"],
)
async def test_transport_failures_raise_venue_unavailable(
    tmp_path: pathlib.Path, response: object
) -> None:
    """HTTP and transport failures map to ``VenueUnavailableError``."""
    vision = binance_vision.BinanceVision(tmp_path, client=httpx.AsyncClient())
    with respx.mock() as router:
        route = router.get(f"{_HOST}{_PATH}.CHECKSUM")
        if isinstance(response, httpx.Response):
            route.mock(return_value=response)
        else:
            route.mock(side_effect=response)
        with pytest.raises(errors.VenueUnavailableError):
            await vision.daily_zip("BTCDOMUSDT", _DAY)
        route.mock(return_value=httpx.Response(200, text=_checksum(b"x")))
        zip_route = router.get(f"{_HOST}{_PATH}")
        if isinstance(response, httpx.Response):
            zip_route.mock(return_value=response)
        else:
            zip_route.mock(side_effect=response)
        with pytest.raises(errors.VenueUnavailableError):
            await vision.daily_zip("BTCDOMUSDT", _DAY)
    assert _parts(tmp_path) == []


async def test_unreadable_archive_is_discarded(tmp_path: pathlib.Path) -> None:
    """A cached file that fails to parse is deleted and reported."""
    vision = binance_vision.BinanceVision(tmp_path, client=httpx.AsyncClient())
    target = vision.cached_path("BTCDOMUSDT", _DAY)
    target.parent.mkdir(parents=True)
    target.write_bytes(_zip("1,2,3\n"))
    with pytest.raises(errors.VenueUnavailableError, match="unreadable"):
        await vision.import_day(_ID, _DAY, lambda rows, covered: None, 0)
    assert not target.exists()
    target.write_bytes(_zip("x", name="a.txt"))
    with pytest.raises(errors.VenueUnavailableError):
        await vision.import_day(_ID, _DAY, lambda rows, covered: None, 0)


async def test_downloads_are_bounded_and_deduplicated(
    tmp_path: pathlib.Path,
) -> None:
    """At most ``max_concurrency`` downloads run; one per day at a time."""
    payload = _zip(_csv())
    active = [0, 0]

    async def slow(request: httpx.Request) -> httpx.Response:
        """Serves a checksum slowly while counting concurrency.

        Args:
            request: Request.

        Returns:
            Response.
        """
        active[0] += 1
        active[1] = max(active[1], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".CHECKSUM")
        return httpx.Response(
            200, text=f"{hashlib.sha256(payload).hexdigest()}  {name}"
        )

    vision = binance_vision.BinanceVision(
        tmp_path, client=httpx.AsyncClient(), max_concurrency=2
    )
    days = [_DAY - datetime.timedelta(days=i) for i in range(5)]
    with respx.mock() as router:
        router.get(url__regex=r".*\.CHECKSUM$").mock(side_effect=slow)
        downloads = router.get(url__regex=r".*\.zip$").mock(
            return_value=httpx.Response(200, content=payload)
        )
        paths = await asyncio.gather(
            *(vision.daily_zip("BTCDOMUSDT", d) for d in [*days, days[0]])
        )
    assert all(p is not None for p in paths)
    assert active[1] == 2
    assert downloads.call_count == 5


def test_prune_deletes_cached_days_before_the_cutoff(
    tmp_path: pathlib.Path,
) -> None:
    """Retention removes cached files of days wholly before the cut-off."""
    vision = binance_vision.BinanceVision(tmp_path)
    assert vision.prune(0) == 0
    for day in (_DAY, _DAY - datetime.timedelta(days=1)):
        path = vision.cached_path("BTCDOMUSDT", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"z")
    (tmp_path / "BTCDOMUSDT" / "BTCDOMUSDT-aggTrades-bad.zip").write_bytes(b"")
    deleted = vision.prune(binance_vision.day_start_ms(_DAY))
    assert deleted == 1
    assert vision.cached_path("BTCDOMUSDT", _DAY).exists()
