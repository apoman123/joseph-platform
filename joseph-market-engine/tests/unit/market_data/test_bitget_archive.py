# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for the Bitget public trade archive client.

The fixtures are a real archive part (XTZUSDT, 2026-11-21 UTC+8, one part)
with its file listing and Bitget's 1D kline of that day, captured live on
2026-11-23.
"""

from __future__ import annotations

import datetime
import decimal
import io
import json
import pathlib
import zipfile
from typing import Any
from typing import TypeAlias

import httpx
import pytest
import respx

from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import market
from joseph.market_data.adapters import bitget_archive
from joseph.market_data.domain import recorded_trades

_D = decimal.Decimal
_ID = instruments.InstrumentId.parse("bitget:perp:XTZUSDT")
_DAY = datetime.date(2026, 11, 21)
_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3]
    / "fixtures"
    / "venues"
    / "bitget"
)
_PART_URL = (
    "https://img.bitgetimg.com/online/trades/UMCBL/XTZUSDT/"
    "XTZUSDT_20261121_001.zip"
)

Written: TypeAlias = list[tuple[int, list[recorded_trades.Interval]]]
"""Archive writes seen by a sink: ``(row count, covered intervals)``."""


def _json(name: str) -> Any:
    """Loads a JSON fixture.

    Args:
        name: File name without extension.

    Returns:
        The parsed JSON.
    """
    return json.loads((_FIXTURES / f"{name}.json").read_text())


def _part() -> bytes:
    """Returns the real archive part.

    Returns:
        The zip bytes of the captured part.
    """
    return (_FIXTURES / "archive_XTZUSDT_20261121_001.zip").read_bytes()


def _zip(text: str) -> bytes:
    """Zips CSV text like Bitget does.

    Args:
        text: CSV content.

    Returns:
        The zip archive bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XTZUSDT_UMCBL_20261121_002.csv", text)
    return buffer.getvalue()


def _parts(root: pathlib.Path) -> list[pathlib.Path]:
    """Lists leftover partial downloads.

    Args:
        root: Cache directory.

    Returns:
        ``.part`` files.
    """
    return list(root.rglob("*.part"))


async def _no_sleep(seconds: float) -> None:
    """Skips the archive client's waits.

    Args:
        seconds: Ignored.
    """
    del seconds


def _archive(root: pathlib.Path, **kwargs: Any) -> bitget_archive.BitgetArchive:
    """Builds an archive client on an injected HTTP client.

    Args:
        root: Cache directory.
        **kwargs: Constructor overrides; ``sleep`` defaults to no waiting.

    Returns:
        The archive client.
    """
    values: dict[str, Any] = {"sleep": _no_sleep}
    values.update(kwargs)
    return bitget_archive.BitgetArchive(
        root,
        client=httpx.AsyncClient(),
        **values,
    )


def _routes(router: respx.Router, part: bytes | None = None) -> None:
    """Serves the captured listing, part and kline.

    Args:
        router: respx router.
        part: Part bytes (the real part by default).
    """
    router.post(bitget_archive.LIST_URL).mock(
        return_value=httpx.Response(200, json=_json("archive_list"))
    )
    router.get(_PART_URL).mock(
        return_value=httpx.Response(200, content=part or _part())
    )
    router.get(bitget_archive.KLINE_URL).mock(
        return_value=httpx.Response(
            200, json=_json("archive_history_candles_1D")
        )
    )


def test_days_are_cut_at_midnight_utc8() -> None:
    """A Bitget day starts at 16:00 UTC the day before; parts are numbered."""
    start, end = bitget_archive.day_bounds(_DAY)
    assert start == 1_795_190_400_000
    assert end - start == bitget_archive.DAY_MS
    assert bitget_archive.day_of(start) == _DAY
    assert bitget_archive.day_of(start - 1) == _DAY - datetime.timedelta(1)
    archive = bitget_archive.BitgetArchive(pathlib.Path("unused"))
    assert archive.day_bounds(_DAY) == (start, end)
    assert archive.day_of(end - 1) == _DAY
    assert bitget_archive.part_number(_PART_URL) == 1
    with pytest.raises(ValueError, match="file name"):
        bitget_archive.part_number("x.zip")


def test_real_part_folds_to_bitgets_own_daily_kline() -> None:
    """The captured day's trades reproduce Bitget's 1D kline exactly."""
    with zipfile.ZipFile(io.BytesIO(_part())) as archive:
        text = archive.read(archive.namelist()[0]).decode()
    trades = list(bitget_archive.parse_rows(text.splitlines(), _ID, 3))
    assert len(trades) == 1_621
    assert trades[0].trade_id == "1507703370942251008"
    assert trades[0].venue_event_ts_ms == 1_795_190_416_000
    assert trades[0].aggressor_side is market.AggressorSide.BUY
    assert {t.aggressor_side for t in trades} == {
        market.AggressorSide.BUY,
        market.AggressorSide.SELL,
    }
    found = bitget_archive.totals(trades)
    row = _json("archive_history_candles_1D")["data"][1]
    expected = bitget_archive.DayTotals(
        _D(row[2]), _D(row[3]), _D(row[4]), _D(row[5]), _D(row[6])
    )
    assert found == expected
    assert bitget_archive.matches(found, expected)
    assert not bitget_archive.matches(None, expected)
    short = bitget_archive.totals(trades[:-1])
    assert not bitget_archive.matches(short, expected)
    assert bitget_archive.totals([]) is None


@pytest.mark.parametrize(
    "text",
    ["a,b\n1,2\n", "trade_id,timestamp,price,side,size(base)\n1,2,3,hold,4\n"],
    ids=["header", "side"],
)
def test_malformed_rows_are_rejected(text: str) -> None:
    """Unknown headers and sides raise."""
    with pytest.raises(ValueError):
        list(bitget_archive.parse_rows(text.splitlines(), _ID, 0))


async def test_import_verifies_the_day_and_archives_it(
    tmp_path: pathlib.Path,
) -> None:
    """A verified day is archived once and its parts are cached."""
    archive = _archive(tmp_path)
    archive.FOLD_TRADES = 300
    archive.SINK_BARS = 100
    written: Written = []

    def sink(
        rows: list[market.Candle], covered: list[recorded_trades.Interval]
    ) -> None:
        """Records archive writes.

        Args:
            rows: Second aggregates written in one chunk.
            covered: Intervals the chunk covers.
        """
        written.append((len(rows), covered))

    with respx.mock() as router:
        _routes(router)
        assert await archive.import_day(_ID, _DAY, sink, 7)
        listed = router.calls.call_count
        assert await archive.day_files("XTZUSDT", _DAY) is not None
        assert router.calls.call_count == listed
    assert written[-1][1] == [bitget_archive.day_bounds(_DAY)]
    assert len(written) > 2
    assert all(covered == [] for _, covered in written[:-1])
    assert sum(n for n, _ in written) > 0
    await archive.close()
    owned = bitget_archive.BitgetArchive(tmp_path)
    await owned.close()


async def test_a_day_that_does_not_match_its_kline_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """A truncated part fails verification; nothing is covered or cached."""
    with zipfile.ZipFile(io.BytesIO(_part())) as source:
        lines = source.read(source.namelist()[0]).decode().splitlines()
    truncated = _zip("\n".join(lines[:-5]) + "\n")
    archive = _archive(tmp_path)
    written: Written = []
    with respx.mock() as router:
        _routes(router, truncated)
        with pytest.raises(errors.VenueUnavailableError, match="1D kline"):
            await archive.import_day(
                _ID, _DAY, lambda rows, covered: written.append((0, covered)), 0
            )
    assert all(covered == [] for _, covered in written)
    assert not archive.day_dir("XTZUSDT", _DAY).exists()


async def test_unlisted_day_is_not_rerequested(tmp_path: pathlib.Path) -> None:
    """A day Bitget has not listed yet returns ``None`` for a while."""
    now = [0.0]
    archive = _archive(tmp_path, monotonic=lambda: now[0])
    with respx.mock() as router:
        listing = router.post(bitget_archive.LIST_URL).mock(
            return_value=httpx.Response(200, json={"code": "200", "data": []})
        )
        assert not await archive.import_day(
            _ID, _DAY, lambda rows, covered: None, 0
        )
        assert await archive.day_files("XTZUSDT", _DAY) is None
        assert listing.call_count == 1


async def test_list_calls_are_spaced_and_validated(
    tmp_path: pathlib.Path,
) -> None:
    """Calls wait out the spacing; odd hosts, codes and days are refused."""
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        """Records a wait without waiting.

        Args:
            seconds: Requested delay in seconds.
        """
        waits.append(seconds)

    archive = _archive(
        tmp_path, sleep=sleep, monotonic=lambda: 10.0, list_interval_s=1.5
    )
    entry = dict(_json("archive_list")["data"][0])
    other_day = {**entry, "dateTime": 1}
    duplicate = {**entry}
    with respx.mock() as router:
        route = router.post(bitget_archive.LIST_URL)
        route.mock(
            return_value=httpx.Response(
                200, json={"code": "200", "data": [entry, duplicate, other_day]}
            )
        )
        assert await archive.list_parts("XTZUSDT", _DAY) == [_PART_URL]
        await archive.list_parts("XTZUSDT", _DAY)
        assert waits == [1.5]
        evil = {**entry, "fileUrl": "https://evil.example/x_001.zip"}
        route.mock(
            return_value=httpx.Response(
                200, json={"code": "200", "data": [evil]}
            )
        )
        with pytest.raises(errors.VenueUnavailableError, match="host"):
            await archive.list_parts("XTZUSDT", _DAY)
        unnumbered = {
            **entry,
            "fileUrl": "https://img.bitgetimg.com/online/trades/x.zip",
        }
        route.mock(
            return_value=httpx.Response(
                200, json={"code": "200", "data": [unnumbered]}
            )
        )
        with pytest.raises(errors.VenueUnavailableError, match="file name"):
            await archive.list_parts("XTZUSDT", _DAY)
        for response, error in (
            (httpx.Response(200, json={"code": "400"}), "rejected"),
            (httpx.Response(429), "rate limited"),
            (httpx.Response(503), "HTTP 503"),
            (httpx.Response(200, content=b"<html>"), "invalid JSON"),
        ):
            route.mock(return_value=response)
            with pytest.raises(errors.VenueUnavailableError, match=error):
                await archive.list_parts("XTZUSDT", _DAY)
        route.mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(errors.VenueUnavailableError, match="unreachable"):
            await archive.list_parts("XTZUSDT", _DAY)


async def test_missing_parts_and_klines_fail_the_day(
    tmp_path: pathlib.Path,
) -> None:
    """A listed part that is gone, or no kline to compare, is an error."""
    archive = _archive(tmp_path)
    with respx.mock() as router:
        _routes(router)
        router.get(_PART_URL).mock(return_value=httpx.Response(403))
        with pytest.raises(errors.VenueUnavailableError, match="HTTP 403"):
            await archive.day_files("XTZUSDT", _DAY)
        router.get(_PART_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(errors.VenueUnavailableError, match="download"):
            await archive.day_files("XTZUSDT", _DAY)
        assert _parts(tmp_path) == []
        router.get(bitget_archive.KLINE_URL).mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        with pytest.raises(errors.VenueUnavailableError, match="no bitget"):
            await archive.expected_totals("XTZUSDT", _DAY)
        router.get(bitget_archive.KLINE_URL).mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(errors.VenueUnavailableError, match="unavailable"):
            await archive.expected_totals("XTZUSDT", _DAY)


async def test_unreadable_or_foreign_parts_are_discarded(
    tmp_path: pathlib.Path,
) -> None:
    """Corrupt zips and trades outside the day reject the import."""
    archive = _archive(tmp_path)
    late = "trade_id,timestamp,price,side,size(base)\n1,1795276800000,1,buy,1\n"
    for payload in (b"not a zip", _zip(late)):
        with respx.mock() as router:
            _routes(router, payload)
            with pytest.raises(errors.VenueUnavailableError, match="rejected"):
                await archive.import_day(
                    _ID, _DAY, lambda rows, covered: None, 0
                )
        assert not archive.day_dir("XTZUSDT", _DAY).exists()


def test_files_without_one_csv_and_trades_outside_the_day_raise(
    tmp_path: pathlib.Path,
) -> None:
    """A part must hold one CSV; folding checks the day's bounds."""
    path = tmp_path / "x_001.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.txt", "x")
    with pytest.raises(ValueError, match="CSV"):
        list(bitget_archive.BitgetArchive._lines([path]))
    start, _ = bitget_archive.day_bounds(_DAY)
    outside = tmp_path / "y_001.zip"
    with zipfile.ZipFile(outside, "w") as archive:
        archive.writestr(
            "y.csv",
            "trade_id,timestamp,price,side,size(base)\n"
            f"1,{start - 1},1,buy,1\n",
        )
    client = bitget_archive.BitgetArchive(tmp_path)
    expected = bitget_archive.DayTotals(_D(1), _D(1), _D(1), _D(1), _D(1))
    with pytest.raises(ValueError, match="outside"):
        client._import_files(
            [outside],
            _ID,
            _DAY,
            lambda rows, covered: None,
            0,
            expected=expected,
        )


def test_prune_deletes_days_before_the_cutoff(tmp_path: pathlib.Path) -> None:
    """Retention removes cached days wholly before the cut-off."""
    assert bitget_archive.BitgetArchive(tmp_path / "none").prune(0) == 0
    archive = bitget_archive.BitgetArchive(tmp_path)
    for day in (_DAY, _DAY - datetime.timedelta(days=1)):
        folder = archive.day_dir("XTZUSDT", day)
        folder.mkdir(parents=True)
        (folder / "p_001.zip").write_bytes(b"z")
    (tmp_path / "XTZUSDT" / "notes").mkdir()
    assert archive.prune(bitget_archive.day_bounds(_DAY)[0]) == 1
    assert archive.day_dir("XTZUSDT", _DAY).exists()
    assert not archive.day_dir(
        "XTZUSDT", _DAY - datetime.timedelta(days=1)
    ).exists()
