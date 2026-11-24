# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in: round trip against a running IB Gateway paper account.

Run with ``JOSEPH_IBKR_PAPER_TESTS=1 JOSEPH_IBKR_PAPER_ACCOUNT=DU...``
(optionally ``JOSEPH_IBKR_HOST``/``JOSEPH_IBKR_PORT``, default
127.0.0.1:4002) and the ``ibkr`` extra installed. Places a 1-share AAPL
limit buy far below the market on the paper account and cancels it.
"""

from __future__ import annotations

import decimal
import os

import pytest

from joseph_execution.adapters import market_static
from joseph_execution.adapters.brokers import ibkr
from joseph_execution.domain import enums
from joseph_execution.domain import ids
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading
from joseph_execution.testing import equities as fixtures

pytestmark = [
    pytest.mark.ibkr_paper,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_IBKR_PAPER_TESTS") != "1",
        reason="set JOSEPH_IBKR_PAPER_TESTS=1 with a running IB Gateway",
    ),
]


async def test_paper_account_round_trip() -> None:
    """A far limit order is acknowledged, found by orderRef and canceled."""
    pytest.importorskip("ib_async")
    account = os.environ["JOSEPH_IBKR_PAPER_ACCOUNT"]
    clock = clock_lib.SystemClock()
    gateway = market_static.StaticMarketDataGateway(clock)
    gateway.add_instrument(fixtures.us())
    adapter = ibkr.IbkrAdapter(
        ibkr.IbkrConfig(
            host=os.environ.get("JOSEPH_IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("JOSEPH_IBKR_PORT", "4002")),
            client_id=int(os.environ.get("JOSEPH_IBKR_CLIENT_ID", "91")),
            ack_timeout_s=10,
        ),
        ibkr.IbAsyncFactory,
        gateway,
        clock,
        {"acct": account}.get,
    )
    try:
        caps = await adapter.capabilities("acct")
        assert caps.status is enums.TradingStatus.AVAILABLE, caps.notes
        snapshot = await adapter.account_snapshot("acct")
        assert snapshot.equity > 0
        cid = ids.client_order_id(ids.uuid7())
        result = await adapter.submit_order(
            trading.VenueOrderRequest(
                account_id="acct",
                instrument_id=fixtures.AAPL,
                client_order_id=cid,
                side=enums.OrderSide.BUY,
                order_type=enums.OrderType.LIMIT,
                quantity_base=decimal.Decimal(1),
                time_in_force=enums.TimeInForce.DAY,
                reduce_only=False,
                limit_price=decimal.Decimal(1),
            )
        )
        assert result.accepted, result.state
        found = await adapter.get_order_by_client_id("acct", cid)
        assert found is not None
        canceled = await adapter.cancel_order("acct", cid)
        assert canceled.status is enums.OrderState.CANCELED
    finally:
        await adapter.close()
