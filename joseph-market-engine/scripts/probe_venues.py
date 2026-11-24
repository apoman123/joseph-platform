# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Live connectivity probe for every venue adapter (evidence, not tests).

Usage::

    uv run python scripts/probe_venues.py [--json]

For each venue it lists instruments, the BTC contract metadata, the latest
candles, snapshot, open interest, funding, top-of-book spread and the
latency of the first native WebSocket candle. Only public endpoints are
called; nothing is traded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx

from joseph.domain import clock as clock_lib
from joseph.domain import decimals
from joseph.domain import errors
from joseph.domain import instruments
from joseph.domain import timeframes
from joseph.market_data.adapters import factory

_BTC = {
    instruments.Venue.BINANCE: "binance:perp:BTCUSDT",
    instruments.Venue.HYPERLIQUID: "hyperliquid:perp:BTC",
    instruments.Venue.LIGHTER: "lighter:perp:BTC",
    instruments.Venue.BITGET: "bitget:perp:BTCUSDT",
    instruments.Venue.MEXC: "mexc:perp:BTC_USDT",
}


async def _probe(venue: instruments.Venue) -> dict[str, object]:
    """Probes one venue.

    Args:
        venue: Venue.

    Returns:
        Result summary.
    """
    clock = clock_lib.SystemClock()
    adapter = factory.build_adapters(
        [venue], clock, client=httpx.AsyncClient(timeout=20)
    )[venue]
    instrument = instruments.InstrumentId.parse(_BTC[venue])
    result: dict[str, object] = {"venue": venue.value}
    started = time.perf_counter()
    try:
        listed = await adapter.list_instruments()
        result["instruments"] = len(listed)
        btc = next(i for i in listed if i.instrument_id == instrument)
        result["btc"] = {
            "tick": decimals.format_decimal(btc.tick_size),
            "lot": decimals.format_decimal(btc.lot_size),
            "min_notional": decimals.format_optional(btc.min_notional),
            "funding_h": btc.funding_interval_hours,
            "status": btc.status.value,
        }
        now = clock.now_ms()
        candles = await adapter.fetch_candles(
            instrument, timeframes.Timeframe.M5, now - 1_800_000, now + 1
        )
        last = candles[-1]
        result["last_candle"] = {
            "start_ms": last.start_ms,
            "close": str(last.close),
            "is_final": last.is_final,
            "previous_final": candles[-2].is_final
            if len(candles) > 1
            else None,
        }
        snapshot = await adapter.fetch_snapshot(instrument)
        result["snapshot"] = {
            "mark": decimals.format_optional(snapshot.mark),
            "turnover_24h": decimals.format_optional(
                snapshot.turnover_24h_quote
            ),
        }
        oi = await adapter.fetch_open_interest(instrument)
        result["open_interest_usd"] = decimals.format_decimal(
            oi.notional_usd.quantize(decimals.ONE)
        )
        funding = await adapter.fetch_funding(instrument)
        result["funding"] = {
            "rate": decimals.format_decimal(funding.rate),
            "interval_h": funding.interval_hours,
            "kind": funding.kind.value,
        }
        book = await adapter.fetch_order_book(instrument, 20)
        if book.best_bid is not None and book.best_ask is not None:
            mid = (book.best_bid + book.best_ask) / 2
            result["spread_pct"] = decimals.format_decimal(
                ((book.best_ask - book.best_bid) / mid * 100).quantize(
                    decimals.to_decimal("0.0001")
                )
            )
        result["rest_ms"] = round((time.perf_counter() - started) * 1000)
        caps = adapter.capabilities()
        result["native_ws_candles"] = caps.market_data.native_ws_candles
        if caps.market_data.native_ws_candles:
            ws_start = time.perf_counter()
            stream = adapter.stream_candles(instrument, timeframes.Timeframe.M1)
            first = await asyncio.wait_for(anext(stream), 30)
            await stream.aclose()  # type: ignore[attr-defined]
            result["ws_first_candle_ms"] = round(
                (time.perf_counter() - ws_start) * 1000
            )
            result["ws_candle_start_ms"] = first.start_ms
        result["ok"] = True
    except (errors.JosephError, StopIteration, TimeoutError) as err:
        result["ok"] = False
        result["error"] = f"{type(err).__name__}: {err}"
    finally:
        await adapter.close()
    return result


async def _main(as_json: bool) -> int:
    """Probes every venue concurrently and prints the results.

    Args:
        as_json: Emit JSON instead of a text summary.

    Returns:
        Process exit code (non-zero if any venue failed).
    """
    results = await asyncio.gather(*(_probe(v) for v in _BTC))
    if as_json:
        print(json.dumps(results, indent=2))  # noqa: T201
    else:
        for row in results:
            print(json.dumps(row))  # noqa: T201
    return 0 if all(r.get("ok") for r in results) else 1


def main() -> None:
    """Parses arguments and runs the probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.json)))


if __name__ == "__main__":
    main()
