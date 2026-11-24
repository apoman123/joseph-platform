# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in testnet round trips: place and cancel a far-from-market order.

Run with ``JOSEPH_SANDBOX_TESTS=1`` and the venue's test-environment keys
(the variables are listed in ``joseph-execution-engine/README.md``), e.g.
``JOSEPH_SANDBOX_TESTS=1 JOSEPH_SANDBOX_BINANCE_API_KEY=…
JOSEPH_SANDBOX_BINANCE_API_SECRET=… uv run pytest -m sandbox tests/sandbox
-k binance``.

Each test negotiates capabilities (must be AVAILABLE), places a post-only
GTC buy at about 70% of the market so it rests, reads it back by client
order id, cancels it and waits for the venue to confirm. MEXC has no
sandbox, so its test always skips.
"""

from __future__ import annotations

import asyncio
import dataclasses
import decimal
import json
import os
import uuid
from collections.abc import Awaitable
from collections.abc import Callable

import httpx
import pytest

from joseph_execution.adapters.venues import binance
from joseph_execution.adapters.venues import bitget
from joseph_execution.adapters.venues import catalog
from joseph_execution.adapters.venues import credentials
from joseph_execution.adapters.venues import directory
from joseph_execution.adapters.venues import hyperliquid
from joseph_execution.adapters.venues import lighter
from joseph_execution.adapters.venues import live
from joseph_execution.domain import enums
from joseph_execution.domain import ids
from joseph_execution.platform import clock as clock_lib
from joseph_execution.platform import config
from joseph_execution.ports import trading

D = decimal.Decimal
S = enums.OrderState
pytestmark = pytest.mark.sandbox


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One venue's sandbox configuration.

    Attributes:
        venue: Venue code.
        fields: Credential field to environment variable suffix.
        symbol: Default symbol.
        quantity: Default base quantity.
        reference: Fetches a reference price for a symbol.
        decimals: Price decimals used for the far price.
    """

    venue: str
    fields: dict[str, str]
    symbol: str
    quantity: str
    reference: Callable[[str], Awaitable[decimal.Decimal]]
    decimals: int = 0


async def _get_json(url: str, **kwargs: object) -> object:
    """Fetches JSON from a public endpoint.

    Args:
        url: URL.
        **kwargs: ``httpx`` request options.

    Returns:
        Parsed JSON.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        reply = await client.request(
            str(kwargs.pop("method", "GET")),
            url,
            **kwargs,  # type: ignore[arg-type]
        )
        reply.raise_for_status()
        return json.loads(reply.text, parse_float=decimal.Decimal)


async def _binance_mark(symbol: str) -> decimal.Decimal:
    """Reads the testnet mark price.

    Args:
        symbol: Symbol.

    Returns:
        Mark.
    """
    data = await _get_json(
        f"{binance.TESTNET_REST_URL}/fapi/v1/premiumIndex?symbol={symbol}"
    )
    return D(str(data["markPrice"]))  # type: ignore[index]


async def _bitget_last(symbol: str) -> decimal.Decimal:
    """Reads the futures ticker.

    Args:
        symbol: Symbol.

    Returns:
        Last price.
    """
    data = await _get_json(
        f"{bitget.REST_URL}/api/v2/mix/market/ticker"
        f"?symbol={symbol}&productType=USDT-FUTURES"
    )
    return D(str(data["data"][0]["lastPr"]))  # type: ignore[index]


async def _hyperliquid_mid(symbol: str) -> decimal.Decimal:
    """Reads the testnet mid.

    Args:
        symbol: Coin.

    Returns:
        Mid.
    """
    data = await _get_json(
        f"{hyperliquid.TESTNET_REST_URL}/info",
        method="POST",
        json={"type": "allMids"},
    )
    return D(str(data[symbol]))  # type: ignore[index]


async def _lighter_last(symbol: str) -> decimal.Decimal:
    """Reads the testnet last trade price.

    Args:
        symbol: Symbol.

    Returns:
        Last trade price.
    """
    books = await _get_json(f"{lighter.TESTNET_REST_URL}/api/v1/orderBooks")
    market_id = next(
        b["market_id"]
        for b in books["order_books"]  # type: ignore[index]
        if b["symbol"] == symbol
    )
    details = await _get_json(
        f"{lighter.TESTNET_REST_URL}/api/v1/orderBookDetails?market_id={market_id}"
    )
    return D(str(details["order_book_details"][0]["last_trade_price"]))  # type: ignore[index]


CASES = {
    "binance": Case(
        "binance",
        {"api_key": "API_KEY", "api_secret": "API_SECRET"},
        "BTCUSDT",
        "0.002",
        _binance_mark,
    ),
    "bitget": Case(
        "bitget",
        {
            "api_key": "API_KEY",
            "api_secret": "API_SECRET",
            "passphrase": "PASSPHRASE",
        },
        "BTCUSDT",
        "0.001",
        _bitget_last,
    ),
    "hyperliquid": Case(
        "hyperliquid",
        {
            "agent_private_key": "AGENT_PRIVATE_KEY",
            "account_address": "ACCOUNT_ADDRESS",
        },
        "BTC",
        "0.001",
        _hyperliquid_mid,
    ),
    "lighter": Case(
        "lighter",
        {
            "api_private_key": "API_PRIVATE_KEY",
            "account_index": "ACCOUNT_INDEX",
            "api_key_index": "API_KEY_INDEX",
        },
        "BTC",
        "0.001",
        _lighter_last,
        decimals=1,
    ),
}


def _env(case: Case) -> dict[str, str]:
    """Reads a venue's sandbox credentials or skips.

    Args:
        case: Venue case.

    Returns:
        Credential fields (with ``environment=testnet``).
    """
    if os.environ.get("JOSEPH_SANDBOX_TESTS") != "1":
        pytest.skip("set JOSEPH_SANDBOX_TESTS=1 to run testnet round trips")
    prefix = f"JOSEPH_SANDBOX_{case.venue.upper()}_"
    fields = {k: os.environ.get(prefix + v, "") for k, v in case.fields.items()}
    missing = [prefix + case.fields[k] for k, v in fields.items() if not v]
    if missing:
        pytest.skip(f"missing {', '.join(missing)}")
    return {**fields, "environment": "testnet"}


class _Known:
    """Directory that knows the single test order."""

    def __init__(self) -> None:
        """Initializes the directory."""
        self.orders: dict[str, directory.KnownOrder] = {}

    def find(
        self, account_id: str, client_order_id: str
    ) -> directory.KnownOrder | None:
        """Finds the test order.

        Args:
            account_id: Account.
            client_order_id: Client id.

        Returns:
            Order or ``None``.
        """
        del account_id
        return self.orders.get(client_order_id)

    def recent(
        self, account_id: str, since_ms: int
    ) -> list[directory.KnownOrder]:
        """Lists the test order.

        Args:
            account_id: Account.
            since_ms: Lower bound.

        Returns:
            Orders.
        """
        del account_id, since_ms
        return list(self.orders.values())


async def _wait_for(
    adapter: trading.TradingAdapter,
    cid: str,
    done: Callable[[trading.VenueOrderState | None], bool],
) -> trading.VenueOrderState | None:
    """Polls the venue until a condition holds (venues confirm async).

    Args:
        adapter: Adapter.
        cid: Client id.
        done: Condition.

    Returns:
        Last state.
    """
    state = None
    for _ in range(20):
        state = await adapter.get_order_by_client_id("sandbox", cid)
        if done(state):
            return state
        await asyncio.sleep(1)
    return state


@pytest.mark.parametrize("venue", sorted(CASES))
async def test_testnet_place_and_cancel_far_limit(venue: str) -> None:
    """A far post-only limit rests, is found by client id and cancels."""
    case = CASES[venue]
    fields = _env(case)
    known = _Known()
    adapter = catalog.build_live_adapters(
        credentials.StaticCredentials({"sandbox": fields}),
        clock_lib.SystemClock(),
        live.VenueOptions(live_trading_enabled=True, timeout_s=15.0),
        directory=known,
        urls=config.parse_venue_urls(os.environ.get("JOSEPH_VENUE_URLS", "")),
    )[venue]
    caps = await adapter.capabilities("sandbox")
    assert caps.status is enums.TradingStatus.AVAILABLE, caps.notes
    symbol = os.environ.get(
        f"JOSEPH_SANDBOX_{venue.upper()}_SYMBOL", case.symbol
    )
    reference = await case.reference(symbol)
    price = (reference * D("0.7")).quantize(
        D(1).scaleb(-case.decimals), rounding=decimal.ROUND_DOWN
    )
    cid = ids.client_order_id(str(uuid.uuid4()))
    instrument = f"{venue}:perp:{symbol}"
    known.orders[cid] = directory.KnownOrder(
        cid, instrument, enums.OrderMode.LIVE, 0, False
    )
    request = trading.VenueOrderRequest(
        account_id="sandbox",
        instrument_id=instrument,
        client_order_id=cid,
        side=enums.OrderSide.BUY,
        order_type=enums.OrderType.LIMIT,
        quantity_base=D(
            os.environ.get(f"JOSEPH_SANDBOX_{venue.upper()}_QTY", case.quantity)
        ),
        time_in_force=enums.TimeInForce.POST_ONLY,
        reduce_only=False,
        limit_price=price,
        leverage=D(2),
        margin_mode=enums.MarginMode.CROSS,
    )
    result = await adapter.submit_order(request)
    assert result.accepted, result.state.reason
    resting = await _wait_for(
        adapter, cid, lambda s: s is not None and s.status is S.ACKNOWLEDGED
    )
    assert resting is not None and resting.status is S.ACKNOWLEDGED
    await adapter.cancel_order("sandbox", cid)
    final = await _wait_for(
        adapter, cid, lambda s: s is not None and s.status is S.CANCELED
    )
    assert final is not None and final.status is S.CANCELED
    closer = getattr(adapter, "aclose", None)
    if closer is not None:
        await closer()


def test_mexc_has_no_sandbox() -> None:
    """MEXC offers no test environment; mainnet probes are not automated."""
    pytest.skip("MEXC futures has no sandbox (mexc.com/mexc-api FAQ)")
