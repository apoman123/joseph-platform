# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in: login and place_order in Shioaji simulation mode.

Run with ``JOSEPH_SINOPAC_SIM_TESTS=1``, ``JOSEPH_SINOPAC_API_KEY``,
``JOSEPH_SINOPAC_SECRET_KEY`` and ``JOSEPH_SINOPAC_ACCOUNT`` (e.g.
``9A95-0000001``) with the ``sinopac`` extra installed, Mon-Fri
08:00-20:00 Taipei. It logs in with ``simulation=True`` and places one
board-lot limit order at the limit-down price, which also performs
SinoPac's required simulation-mode test (login + place_order).
"""

from __future__ import annotations

import asyncio
import contextlib
import decimal
import os

import pytest

from joseph_execution.adapters.brokers import sinopac
from joseph_execution.domain import enums
from joseph_execution.domain import errors
from joseph_execution.domain import ids
from joseph_execution.platform import clock as clock_lib
from joseph_execution.ports import trading

pytestmark = [
    pytest.mark.sinopac_sim,
    pytest.mark.skipif(
        os.environ.get("JOSEPH_SINOPAC_SIM_TESTS") != "1",
        reason="set JOSEPH_SINOPAC_SIM_TESTS=1 with Shioaji credentials",
    ),
]


async def test_simulation_login_and_place_order() -> None:
    """Simulation login, place_order and lookup succeed; a cancel is sent."""
    pytest.importorskip("shioaji")
    config = sinopac.SinopacConfig(
        api_key=os.environ["JOSEPH_SINOPAC_API_KEY"],
        secret_key=os.environ["JOSEPH_SINOPAC_SECRET_KEY"],
        ca_path=os.environ.get("JOSEPH_SINOPAC_CA_PATH"),
        ca_password=os.environ.get("JOSEPH_SINOPAC_CA_PASSWORD"),
        person_id=os.environ.get("JOSEPH_SINOPAC_PERSON_ID"),
        simulation=True,
    )
    code = os.environ.get("JOSEPH_SINOPAC_TEST_CODE", "2890")
    adapter = sinopac.SinopacAdapter(
        config,
        sinopac.ShioajiFactory,
        clock_lib.SystemClock(),
        {"acct": os.environ["JOSEPH_SINOPAC_ACCOUNT"]}.get,
    )
    try:
        caps = await adapter.capabilities("acct")
        assert caps.status is enums.TradingStatus.AVAILABLE, caps.notes
        api = adapter._api
        assert api is not None
        contract = api.contracts.stocks[code]
        # Shioaji quotes prices as floats; repr() keeps the shortest exact text.
        price = decimal.Decimal(repr(float(contract.limit_down)))
        cid = ids.client_order_id(ids.uuid7())
        result = await adapter.submit_order(
            trading.VenueOrderRequest(
                account_id="acct",
                instrument_id=f"twse:stock:{code}",
                client_order_id=cid,
                side=enums.OrderSide.BUY,
                order_type=enums.OrderType.LIMIT,
                quantity_base=decimal.Decimal(1000),
                time_in_force=enums.TimeInForce.DAY,
                reduce_only=False,
                limit_price=price,
            )
        )
        assert result.accepted, result.state
        await asyncio.sleep(2)
        found = await adapter.get_order_by_client_id("acct", cid)
        assert found is not None
        # The cancel report may arrive later through the order callback.
        with contextlib.suppress(errors.AdapterTimeoutError):
            await adapter.cancel_order("acct", cid)
        state = None
        for _ in range(10):
            await asyncio.sleep(1)
            try:
                state = await adapter.get_order_by_client_id("acct", cid)
            except errors.AdapterTimeoutError:
                continue
            if state is not None and state.status is enums.OrderState.CANCELED:
                break
        assert state is not None
    finally:
        await adapter.close()
