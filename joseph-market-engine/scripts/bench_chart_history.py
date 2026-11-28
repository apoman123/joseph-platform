# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Chart-history latency benchmark over simulated slow venues (no network).

Usage, from the ``joseph-market-engine`` directory::

    uv run python scripts/bench_chart_history.py [--latency-ms 150] [--json]
        [--skip-twse]

The real Alpaca (Nasdaq) and TWSE adapters run with their production rate
limits against an in-process HTTP transport that answers every request
after ``--latency-ms`` and synthesizes full session data (every session
minute of AAPL traded; every TWSE session day of 2330 traded). The DEMO
venue runs as in ``JOSEPH_DEMO_ONLY=true``. Each scenario prints its wall
time and the venue requests it sent:

* ``cold``: first ``/v1/candles``-equivalent batch (500 bars) of a series;
* ``warm``: the same batch again right after;
* ``concurrent``: four identical cold batches at once;
* ``with_studies``: a cold chart batch while a Delta pane (10,000 1m
  intrabars) and an EMA window (1,000 bars) load at the same moment;
* ``epoch``: the batch again after the fifteen-minute adjustment epoch
  boundary passed.

This measures engine behavior (request count, pacing, coalescing and
caching), not a venue's real latency; compare runs on the same machine.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import dataclasses
import datetime
import functools
import json
import pathlib
import time
import zoneinfo
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.market_data.adapters import alpaca
from joseph.market_data.adapters import factory
from joseph.market_data.app import candle_store
from joseph.market_data.app import feed_router
from joseph.market_data.app import registry
from joseph.market_data.app import service as service_lib

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "venues"
_NY = zoneinfo.ZoneInfo("America/New_York")
_NOW_MS = int(
    datetime.datetime(2026, 11, 25, 14, 0, tzinfo=_NY).timestamp() * 1000
)
"""Wednesday 2026-11-25 14:00 New York: the US session is open."""
_AAPL = instruments.InstrumentId.parse("nasdaq:stock:AAPL")
_TSMC = instruments.InstrumentId.parse("twse:stock:2330")
_DEMO = instruments.InstrumentId.parse("mock:perp:BTCUSD")
_PAGE = 10_000
_KEYS = alpaca.AlpacaConfig(key_id="bench", secret_key="bench")  # noqa: S106 - fake key of the simulated venue
_TWSE_ROW = (
    "1,000",
    "1,000,000",
    "1,000.00",
    "1,010.00",
    "990.00",
    "1,005.00",
    "+5.00",
    "100",
    "",
)
"""Shares, value, OHLC, change, trades and note of a synthetic day."""


class _Venues:
    """Synthetic Alpaca, Nasdaq Trader and TWSE endpoints with latency.

    Attributes:
        latency_s: Delay before every response, in seconds.
        requests: Requests served per ``host path`` key.
    """

    def __init__(self, latency_s: float) -> None:
        """Initializes the endpoints.

        Args:
            latency_s: Delay before every response, in seconds.
        """
        self.latency_s = latency_s
        self.requests: collections.Counter[str] = collections.Counter()
        self._minutes: dict[tuple[int, int], list[dict[str, Any]]] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answers one request after the configured latency.

        Args:
            request: Request to a simulated host.

        Returns:
            The synthesized response.
        """
        await asyncio.sleep(self.latency_s)
        host, path = request.url.host, request.url.path
        self.requests[f"{host} {path.rsplit('/', 1)[-1]}"] += 1
        if host == "www.nasdaqtrader.com":
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200, text=(_FIXTURES / "nasdaqtrader" / name).read_text("utf-8")
            )
        if host == "data.alpaca.markets":
            return self._alpaca(request)
        if host == "openapi.twse.com.tw":
            return self._twse_openapi(path)
        if host == "www.twse.com.tw":
            return self._twse_www(request)
        return httpx.Response(200, json={"msgArray": []})

    def _alpaca(self, request: httpx.Request) -> httpx.Response:
        """Serves AAPL bars (every session minute traded) and empty tapes.

        Args:
            request: Alpaca data request.

        Returns:
            One page of bars, or an empty trades/quotes page.
        """
        params = request.url.params
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind != "bars":
            return httpx.Response(200, json={kind: [], "next_page_token": None})
        last = alpaca.rfc3339_ms(params["end"])
        if params["timeframe"] == "1Day":
            first = datetime.date.fromisoformat(params["start"][:10])
            rows = [
                _bar(
                    int(
                        datetime.datetime.combine(
                            s.day, datetime.time(0), tzinfo=_NY
                        ).timestamp()
                        * 1000
                    )
                )
                for s in sessions.XNYS.sessions(
                    int(
                        datetime.datetime.combine(
                            first, datetime.time(0), tzinfo=_NY
                        ).timestamp()
                        * 1000
                    ),
                    last + 1,
                )
            ]
        else:
            rows = self._session_minutes(
                alpaca.rfc3339_ms(params["start"]), last
            )
        offset = int(params.get("page_token") or 0)
        more = offset + _PAGE < len(rows)
        return httpx.Response(
            200,
            json={
                "bars": rows[offset : offset + _PAGE],
                "next_page_token": str(offset + _PAGE) if more else None,
                "symbol": "AAPL",
            },
        )

    def _session_minutes(self, start: int, last: int) -> list[dict[str, Any]]:
        """Lists one bar per minute 04:00-20:00 New York in ``[start, last]``.

        Alpaca's minute bars include the extended hours, which the adapter
        then leaves out of session bars.

        Args:
            start: Inclusive start, epoch milliseconds.
            last: Inclusive end, epoch milliseconds.

        Returns:
            Alpaca REST bars (cached per window).
        """
        key = (start, last)
        if key not in self._minutes:
            rows = []
            for day in sessions.XNYS.sessions(start - 86_400_000, last + 1):
                midnight = datetime.datetime.combine(
                    day.day, datetime.time(0), tzinfo=_NY
                )
                first = int(midnight.timestamp() * 1000) + 4 * 3_600_000
                minute = max(first, -(-start // 60_000) * 60_000)
                while minute < min(first + 16 * 3_600_000, last + 1):
                    rows.append(_bar(minute))
                    minute += 60_000
            self._minutes[key] = rows
        return self._minutes[key]

    def _twse_openapi(self, path: str) -> httpx.Response:
        """Serves the captured TWSE OpenAPI listings.

        Args:
            path: Request path.

        Returns:
            The fixture for a known path, else 404.
        """
        names = {
            "/v1/exchangeReport/STOCK_DAY_ALL": "stock_day_all",
            "/v1/opendata/t187ap03_L": "t187ap03_L",
            "/v1/opendata/t187ap47_L": "t187ap47_L",
            "/v1/exchangeReport/TWT88U": "twt88u",
            "/v1/exchangeReport/TWTAWU": "twtawu",
            "/v1/holidaySchedule/holidaySchedule": "holiday_openapi",
        }
        name = names.get(path)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(
            200, text=(_FIXTURES / "twse" / f"{name}.json").read_text("utf-8")
        )

    def _twse_www(self, request: httpx.Request) -> httpx.Response:
        """Serves synthesized STOCK_DAY months (every session day traded).

        Args:
            request: Request to www.twse.com.tw.

        Returns:
            One month of daily rows, or TWSE's no-data answer.
        """
        params = request.url.params
        if not request.url.path.endswith("/STOCK_DAY"):
            return httpx.Response(200, json={"stat": "no data"})
        date = params["date"]
        year, month = int(date[:4]), int(date[4:6])
        first = datetime.date(year, month, 1)
        following = datetime.date(year + month // 12, month % 12 + 1, 1)
        rows = []
        day = first
        while day < following:
            if sessions.XTAI.session(day) is not None:
                roc = f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"
                rows.append([roc, *_TWSE_ROW])
            day += datetime.timedelta(days=1)
        return httpx.Response(
            200,
            json={
                "stat": "OK",
                "date": date,
                "title": f"{year - 1911}年{month:02d}月 2330 各日成交資訊",
                "fields": [
                    "日期",
                    "成交股數",
                    "成交金額",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                    "漲跌價差",
                    "成交筆數",
                    "註記",
                ],
                "data": rows,
            },
        )


def _bar(ts_ms: int) -> dict[str, Any]:
    """Builds an Alpaca REST bar.

    Args:
        ts_ms: Bar time, epoch milliseconds.

    Returns:
        The bar in Alpaca's REST shape.
    """
    return {
        "t": alpaca.rfc3339(ts_ms).replace(".000Z", "Z"),
        "o": 100,
        "h": 101,
        "l": 99,
        "c": 100.5,
        "v": 1000,
        "n": 10,
        "vw": 100.2,
    }


@dataclasses.dataclass
class _Rig:
    """One market-data service over freshly built adapters.

    Attributes:
        service: The service under test.
        clock: Manual clock of the adapters and the service.
        venues: Simulated endpoints (request counts).
    """

    service: service_lib.MarketDataService
    clock: clock_lib.ManualClock
    venues: _Venues


def _rig(venue: instruments.Venue, latency_s: float) -> _Rig:
    """Builds a service for one venue with production rate limits.

    Args:
        venue: ``nasdaq``, ``twse`` or ``mock``.
        latency_s: Simulated per-request latency, in seconds.

    Returns:
        The rig.
    """
    clock = clock_lib.ManualClock(_NOW_MS)
    venues = _Venues(latency_s)
    client = httpx.AsyncClient(transport=httpx.MockTransport(venues))
    adapters = factory.build_adapters(
        [venue], clock, client=client, alpaca_config=_KEYS
    )
    store = candle_store.CandleStore()
    svc = service_lib.MarketDataService(
        adapters,
        registry.InstrumentRegistry(adapters),
        store,
        feed_router.FeedRouter(adapters, store, clock),
        clock,
    )
    return _Rig(svc, clock, venues)


async def _timed(
    rig: _Rig, work: Callable[[], Awaitable[object]]
) -> dict[str, object]:
    """Times one scenario and counts the venue requests it sent.

    Args:
        rig: Rig the scenario runs on.
        work: Scenario.

    Returns:
        ``seconds`` and ``requests`` of the scenario.
    """
    before = collections.Counter(rig.venues.requests)
    started = time.perf_counter()
    await work()
    elapsed = time.perf_counter() - started
    sent = rig.venues.requests - before
    return {"seconds": round(elapsed, 3), "requests": sum(sent.values())}


async def _scenarios(
    venue: instruments.Venue,
    instrument: instruments.InstrumentId,
    timeframe: timeframes.Timeframe,
    latency_s: float,
) -> dict[str, object]:
    """Runs every scenario for one chart series.

    Args:
        venue: Venue of the instrument.
        instrument: Charted instrument.
        timeframe: Chart resolution.
        latency_s: Simulated per-request latency, in seconds.

    Returns:
        Results per scenario name.
    """
    results: dict[str, object] = {}

    def batch(rig: _Rig) -> Awaitable[object]:
        """Starts the terminal's first chart request.

        Args:
            rig: Rig to query.

        Returns:
            Awaitable batch.
        """
        return rig.service.get_batch(instrument, timeframe, limit=500)

    rig = _rig(venue, latency_s)
    await rig.service.registry.search(
        instrument.venue_market_id, venue=venue, limit=1
    )
    results["cold"] = await _timed(rig, lambda: batch(rig))
    results["warm"] = await _timed(rig, lambda: batch(rig))
    rig.clock.advance(service_lib.ADJUSTMENT_REFRESH_MS)
    results["epoch"] = await _timed(rig, lambda: batch(rig))

    rig = _rig(venue, latency_s)
    await rig.service.registry.search(
        instrument.venue_market_id, venue=venue, limit=1
    )
    results["concurrent"] = await _timed(
        rig, lambda: asyncio.gather(*(batch(rig) for _ in range(4)))
    )

    rig = _rig(venue, latency_s)
    await rig.service.registry.search(
        instrument.venue_market_id, venue=venue, limit=1
    )
    if timeframe.duration_ms < timeframes.Timeframe.D1.duration_ms:
        now = rig.clock.now_ms()
        minute = timeframes.Timeframe.M1
        calendar = sessions.for_instrument(instrument)
        delta_from = sessions.lookback_start(calendar, minute, now, 10_000)
        ema_from = sessions.lookback_start(calendar, timeframe, now, 1_000)
        chart_started: dict[str, float] = {}

        async def chart() -> None:
            """Times only the chart batch among the study loads."""
            started = time.perf_counter()
            await batch(rig)
            chart_started["seconds"] = time.perf_counter() - started

        studies = _timed(
            rig,
            lambda: asyncio.gather(
                rig.service.get_candles(instrument, minute, delta_from, now),
                rig.service.get_candles(instrument, timeframe, ema_from, now),
                chart(),
            ),
        )
        total = await studies
        results["with_studies"] = {
            **total,
            "chart_seconds": round(chart_started["seconds"], 3),
        }
    return results


async def _batches(
    svc: service_lib.MarketDataService, tf: timeframes.Timeframe, count: int
) -> list[service_lib.CandleBatchResult]:
    """Requests identical DEMO chart batches at once.

    Args:
        svc: Service to query.
        tf: Chart resolution.
        count: Number of batches.

    Returns:
        The batches.
    """
    return await asyncio.gather(
        *(svc.get_batch(_DEMO, tf, limit=500) for _ in range(count))
    )


async def _demo() -> dict[str, object]:
    """Times DEMO-venue batches (``JOSEPH_DEMO_ONLY=true``).

    Returns:
        Cold, warm and four-concurrent timings for 1m, 1h and 1d.
    """
    results: dict[str, object] = {}
    for tf in (
        timeframes.Timeframe.M1,
        timeframes.Timeframe.H1,
        timeframes.Timeframe.D1,
    ):
        first = _rig(instruments.Venue.MOCK, 0)
        one = functools.partial(_batches, first.service, tf, 1)
        cold = await _timed(first, one)
        warm = await _timed(first, one)
        other = _rig(instruments.Venue.MOCK, 0)
        four = functools.partial(_batches, other.service, tf, 4)
        concurrent = await _timed(other, four)
        results[tf.value] = {
            "cold": cold["seconds"],
            "warm": warm["seconds"],
            "concurrent": concurrent["seconds"],
        }
    return results


async def _main(latency_s: float, skip_twse: bool) -> dict[str, object]:
    """Runs the benchmark.

    Args:
        latency_s: Simulated per-request latency, in seconds.
        skip_twse: Leave out the TWSE daily chart (it paces at 2 s).

    Returns:
        Results per chart.
    """
    report: dict[str, object] = {"latency_ms": round(latency_s * 1000)}
    for tf in (
        timeframes.Timeframe.M15,
        timeframes.Timeframe.H1,
        timeframes.Timeframe.H4,
    ):
        report[f"alpaca {tf.value}"] = await _scenarios(
            instruments.Venue.NASDAQ, _AAPL, tf, latency_s
        )
    if not skip_twse:
        report["twse 1d"] = await _scenarios(
            instruments.Venue.TWSE, _TSMC, timeframes.Timeframe.D1, latency_s
        )
    report["demo"] = await _demo()
    return report


def main() -> None:
    """Parses arguments, runs the benchmark and prints the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--latency-ms", type=float, default=150.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-twse", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(_main(args.latency_ms / 1000, args.skip_twse))
    if args.json:
        print(json.dumps(report, indent=2))  # noqa: T201 - CLI output
        return
    for name, value in report.items():
        print(f"{name}: {json.dumps(value)}")  # noqa: T201 - CLI output


if __name__ == "__main__":
    main()
