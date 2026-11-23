"""System tests: securities paper trading against the real market engine.

The DEMO stock ``mock:stock:ACME`` follows XNYS. Session rules are tested
on execution engines whose clock is pinned to known New York times in the
past (``conftest.ClockedEngine``): the engine asks the market engine for
the XNYS sessions around its pinned time and gets marks stamped with the
wall clock, which are never older than a pinned past time. So the result
does not depend on whether the exchange happens to be open while the suite
runs. Clock-independent rules run on the main (wall-clock) instance.

There is no DEMO Taiwan listing in the market engine (TWSE data needs the
network), so TWSE tick-ladder and price-limit refusals are covered by the
execution engine's own tests (``tests/integration/test_equity_flows.py::
test_securities_rules_reject_orders`` and ``tests/unit/
test_equity_risk.py``); here the TWD paper account is checked for
existence and routing.
"""

from __future__ import annotations

import decimal
import time
import uuid
from collections.abc import Callable

import httpx
import pytest

from tests import conftest

ACME = "mock:stock:ACME"
MOCK_BTC = "mock:perp:BTCUSD"
USD_PAPER = "acct-paper-tenant-a"
TWD_PAPER = "acct-twd-paper-tenant-a"
WEDNESDAY_10_00 = conftest.ny_ms(2026, 11, 18, 10)
"""In session (Wed 18 Nov 2026, 15:00 UTC); the close is 21:00 UTC."""
WEDNESDAY_CLOSE = conftest.ny_ms(2026, 11, 18, 16)
THURSDAY_OPEN = conftest.ny_ms(2026, 11, 19, 9, 30)
"""The T+1 settlement time of a Wednesday fill."""

Validate = Callable[[str, str, object], None]


def _intent(**overrides: object) -> dict[str, object]:
    """Builds a paper ACME limit DAY buy of 10 shares.

    Args:
        **overrides: Fields to replace.

    Returns:
        Contract ``OrderIntentRequest``.
    """
    body: dict[str, object] = {
        "account_id": USD_PAPER,
        "instrument_id": ACME,
        "side": "buy",
        "position_effect": "open",
        "order_type": "limit",
        "quantity_base": "10",
        "limit_price": None,
        "time_in_force": "DAY",
        "reduce_only": False,
        "leverage": "1",
        "mode": "PAPER",
        "source": "MANUAL_TICKET",
    }
    body.update(overrides)
    return body


def _preview(
    client: httpx.Client, validate: Validate, body: dict[str, object]
) -> dict[str, object]:
    """Previews an intent and validates the contract.

    Args:
        client: Execution engine client.
        validate: Contract validator.
        body: Intent request.

    Returns:
        Contract ``OrderPreview``.
    """
    response = client.post("/v1/orders/preview", json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    validate("execution.schema.json", "OrderPreview", preview)
    return preview


def _submit(
    client: httpx.Client, validate: Validate, body: dict[str, object]
) -> dict[str, object]:
    """Previews and submits an intent.

    Args:
        client: Execution engine client.
        validate: Contract validator.
        body: Intent request.

    Returns:
        Contract ``SubmitIntentResponse``.
    """
    preview = _preview(client, validate, body)
    response = client.post(
        "/v1/orders/intents",
        json={**body, "preview_token": preview["preview_token"]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code in (200, 201), response.text
    result = response.json()
    validate("execution.schema.json", "SubmitIntentResponse", result)
    return result


def _check(preview: dict[str, object], name: str) -> dict[str, object]:
    """Returns one risk check of a preview or submit result.

    Args:
        preview: Contract ``OrderPreview`` or ``SubmitIntentResponse``.
        name: Check name.

    Returns:
        Contract ``RiskCheckResult``.
    """
    risk = preview["risk"]
    assert isinstance(risk, dict)
    return next(c for c in risk["checks"] if c["check"] == name)


def _resting_buy(client: httpx.Client, validate: Validate) -> str:
    """Returns a buy limit 5% under the ACME mark (inside the 10% band).

    Args:
        client: Execution engine client.
        validate: Contract validator.

    Returns:
        Tick-aligned price.
    """
    probe = _preview(client, validate, _intent(order_type="market"))
    mark = decimal.Decimal(str(probe["mark_price"]))
    price = mark * decimal.Decimal("0.95")
    return str(price.quantize(decimal.Decimal("0.01")))


def _order(client: httpx.Client, order_id: str) -> dict[str, object]:
    """Finds one order of the USD paper account.

    Args:
        client: Execution engine client.
        order_id: Order.

    Returns:
        Contract ``Order``.
    """
    orders = client.get("/v1/orders", params={"account_id": USD_PAPER}).json()
    return next(o for o in orders["orders"] if o["order_id"] == order_id)


def _wait_state(
    client: httpx.Client, order_id: str, states: set[str], timeout_s: float = 15
) -> str:
    """Polls an order until it reaches one of ``states``.

    Args:
        client: Execution engine client.
        order_id: Order.
        states: Wanted states.
        timeout_s: Maximum wait.

    Returns:
        The last observed state.
    """
    deadline = time.monotonic() + timeout_s
    state = ""
    while time.monotonic() < deadline:
        state = str(_order(client, order_id)["state"])
        if state in states:
            break
        time.sleep(0.25)
    return state


def _summary(
    client: httpx.Client, validate: Validate, account_id: str = USD_PAPER
) -> dict[str, object]:
    """Fetches and validates an account summary.

    Args:
        client: Execution engine client.
        validate: Contract validator.
        account_id: Account.

    Returns:
        Contract ``AccountSummary``.
    """
    summary = client.get(f"/v1/accounts/{account_id}/summary").json()
    validate("execution.schema.json", "AccountSummary", summary)
    return summary


# ---------------------------------------------------------------------------
# Clock-independent rules (main instance, wall clock)
# ---------------------------------------------------------------------------


def test_twd_paper_account_is_provisioned(
    execution: httpx.Client, validate: Validate
) -> None:
    """Each tenant also gets a TWD paper account for TWSE securities."""
    accounts = execution.get("/v1/accounts").json()
    validate("execution.schema.json", "AccountList", accounts)
    by_id = {a["account_id"]: a for a in accounts["accounts"]}
    twd = by_id[TWD_PAPER]
    assert twd["currency"] == "TWD"
    assert twd["venue"] == "mock"
    assert "LIVE" not in twd["modes"]
    assert by_id[USD_PAPER]["currency"] == "USD"
    summary = _summary(execution, validate, TWD_PAPER)
    assert decimal.Decimal(str(summary["settled_cash"])) == 3_000_000
    assert summary["capabilities"]["cash_account"] is True
    assert summary["capabilities"]["currencies"] == ["TWD"]


def test_paper_accounts_route_by_currency(
    execution: httpx.Client, validate: Validate
) -> None:
    """USD stocks need the USD account and TWSE listings the TWD one."""
    twd = _preview(
        execution, validate, _intent(account_id=TWD_PAPER, limit_price="150")
    )
    routing = _check(twd, "routing.venue")
    assert (routing["outcome"], routing["code"]) == ("FAIL", "UNSUPPORTED")
    assert "TWD securities only" in str(routing["message"])
    twse = _preview(
        execution,
        validate,
        _intent(instrument_id="twse:stock:2330", limit_price="600"),
    )
    routing = _check(twse, "routing.venue")
    assert (routing["outcome"], routing["code"]) == ("FAIL", "UNSUPPORTED")
    assert "Use the TWD paper account" in str(routing["message"])
    assert twd["allowed"] is False and twse["allowed"] is False


def test_day_orders_are_refused_on_24x7_markets(
    execution: httpx.Client, validate: Validate
) -> None:
    """DAY and outside-RTH need an exchange; the DEMO perp has none."""
    for overrides in ({"time_in_force": "DAY"}, {"outside_rth": True}):
        body = _intent(
            instrument_id=MOCK_BTC,
            order_type="market",
            quantity_base="0.010",
            **({"time_in_force": "GTC"} | overrides),
        )
        preview = _preview(execution, validate, body)
        assert preview["allowed"] is False
        session = _check(preview, "market.session")
        assert session["outcome"] == "FAIL"
        assert session["code"] == "UNSUPPORTED_ORDER_TYPE"
    result = _submit(
        execution,
        validate,
        _intent(
            instrument_id=MOCK_BTC,
            order_type="market",
            quantity_base="0.010",
            time_in_force="DAY",
        ),
    )
    assert result["risk"]["decision"] == "REJECT"
    assert result["orders"] == []


def test_paper_account_cannot_flag_outside_rth(
    execution: httpx.Client, validate: Validate
) -> None:
    """The paper simulator offers no extended hours, so ACME refuses it."""
    preview = _preview(
        execution,
        validate,
        _intent(
            limit_price=_resting_buy(execution, validate), outside_rth=True
        ),
    )
    session = _check(preview, "market.session")
    assert (session["outcome"], session["code"]) == (
        "FAIL",
        "UNSUPPORTED_ORDER_TYPE",
    )
    assert preview["normalized"]["outside_rth"] is True


# ---------------------------------------------------------------------------
# Session rules at pinned times (clocked instances)
# ---------------------------------------------------------------------------


def test_day_limit_rests_in_session_and_expires_at_the_close(
    clocked: conftest.ClockedEngine, validate: Validate
) -> None:
    """A Wednesday DAY limit rests until 16:00 ET and then expires."""
    client = clocked.client
    clocked.set_clock(WEDNESDAY_10_00)
    body = _intent(limit_price=_resting_buy(client, validate))
    preview = _preview(client, validate, body)
    session = _check(preview, "market.session")
    assert session["outcome"] == "PASS"
    assert session["message"] == "Market open until 2026-11-18 21:00 UTC."
    assert preview["allowed"] is True
    result = _submit(client, validate, body)
    assert result["risk"]["decision"] == "ALLOW"
    order_id = str(result["orders"][0]["order_id"])
    assert _wait_state(client, order_id, {"ACKNOWLEDGED"}) == "ACKNOWLEDGED"
    clocked.set_clock(WEDNESDAY_CLOSE - 60_000)
    time.sleep(1.5)
    assert _order(client, order_id)["state"] == "ACKNOWLEDGED"
    clocked.set_clock(WEDNESDAY_CLOSE)
    assert _wait_state(client, order_id, {"EXPIRED"}) == "EXPIRED"
    orders = client.get("/v1/orders", params={"account_id": USD_PAPER}).json()
    validate("execution.schema.json", "OrderList", orders)


def test_in_session_fill_settles_t_plus_one(
    clocked: conftest.ClockedEngine, validate: Validate
) -> None:
    """A Wednesday buy fills at once and its cash settles at Thursday's open."""
    client = clocked.client
    clocked.set_clock(WEDNESDAY_10_00)
    before = _summary(client, validate)
    settled_before = decimal.Decimal(str(before["settled_cash"]))
    unsettled_before = decimal.Decimal(str(before["unsettled_cash"]))
    result = _submit(
        client,
        validate,
        _intent(order_type="market", time_in_force="IOC"),
    )
    assert result["risk"]["decision"] == "ALLOW"
    order_id = str(result["orders"][0]["order_id"])
    assert _wait_state(client, order_id, {"FILLED"}) == "FILLED"
    fills = client.get("/v1/fills", params={"account_id": USD_PAPER}).json()
    validate("execution.schema.json", "FillList", fills)
    (fill,) = [f for f in fills["fills"] if f["order_id"] == order_id]
    assert fill["ts_ms"] == WEDNESDAY_10_00
    cost = decimal.Decimal(fill["price"]) * decimal.Decimal(
        fill["quantity_base"]
    ) + decimal.Decimal(fill["fee"])
    filled = _summary(client, validate)
    assert decimal.Decimal(str(filled["settled_cash"])) == settled_before
    assert (
        decimal.Decimal(str(filled["unsettled_cash"]))
        == unsettled_before - cost
    )
    position = next(
        p for p in filled["positions"] if p["instrument_id"] == ACME
    )
    assert decimal.Decimal(position["quantity_base"]) >= 10
    clocked.set_clock(THURSDAY_OPEN - 60_000)
    pending = _summary(client, validate)
    assert decimal.Decimal(str(pending["settled_cash"])) == settled_before
    clocked.set_clock(THURSDAY_OPEN)
    settled = _summary(client, validate)
    assert (
        decimal.Decimal(str(settled["settled_cash"])) == settled_before - cost
    )
    assert decimal.Decimal(str(settled["unsettled_cash"])) == unsettled_before


@pytest.mark.parametrize(
    ("now_ms", "next_open"),
    [
        (conftest.ny_ms(2026, 11, 17, 18), "2026-11-18 14:30 UTC"),
        (conftest.ny_ms(2026, 11, 14, 12), "2026-11-16 14:30 UTC"),
        (conftest.ny_ms(2026, 9, 7, 11), "2026-09-08 13:30 UTC"),
    ],
    ids=["weeknight", "weekend", "labor-day"],
)
def test_closed_market_refuses_market_and_ioc_orders(
    clocked: conftest.ClockedEngine,
    validate: Validate,
    now_ms: int,
    next_open: str,
) -> None:
    """Closed: market orders and IOC are refused; a DAY limit rests."""
    client = clocked.client
    clocked.set_clock(now_ms)
    market_order = _submit(
        client, validate, _intent(order_type="market", time_in_force="IOC")
    )
    assert market_order["risk"]["decision"] == "REJECT"
    assert market_order["orders"] == []
    session = _check(market_order, "market.session")
    assert (session["outcome"], session["code"]) == (
        "FAIL",
        "VENUE_UNAVAILABLE",
    )
    assert f"until the next open ({next_open})" in str(session["message"])
    price = _resting_buy(client, validate)
    ioc = _preview(
        client, validate, _intent(limit_price=price, time_in_force="IOC")
    )
    assert ioc["allowed"] is False
    session = _check(ioc, "market.session")
    assert (session["outcome"], session["code"]) == (
        "FAIL",
        "UNSUPPORTED_ORDER_TYPE",
    )
    resting = _submit(client, validate, _intent(limit_price=price))
    assert resting["risk"]["decision"] == "ALLOW"
    session = _check(resting, "market.session")
    assert session["outcome"] == "WARN"
    assert f"rests until the next open ({next_open})" in str(session["message"])
    order_id = str(resting["orders"][0]["order_id"])
    assert _wait_state(client, order_id, {"ACKNOWLEDGED"}) == "ACKNOWLEDGED"
    client.post(f"/v1/orders/{order_id}/cancel", json={})


def test_queue_policy_queues_a_closed_market_day_order_for_the_open(
    clocked_queue: conftest.ClockedEngine, validate: Validate
) -> None:
    """With ``queue``, a weekend market DAY order fills at Monday's open."""
    client = clocked_queue.client
    clocked_queue.set_clock(conftest.ny_ms(2026, 11, 14, 12))
    ioc = _preview(
        client, validate, _intent(order_type="market", time_in_force="IOC")
    )
    session = _check(ioc, "market.session")
    assert (session["outcome"], session["code"]) == (
        "FAIL",
        "UNSUPPORTED_ORDER_TYPE",
    )
    assert "submit the market order as DAY" in str(session["message"])
    queued = _submit(client, validate, _intent(order_type="market"))
    assert queued["risk"]["decision"] == "ALLOW"
    session = _check(queued, "market.session")
    assert session["outcome"] == "WARN"
    assert "queues as DAY for the next open (2026-11-16 14:30 UTC)" in str(
        session["message"]
    )
    order_id = str(queued["orders"][0]["order_id"])
    assert _wait_state(client, order_id, {"ACKNOWLEDGED"}) == "ACKNOWLEDGED"
    time.sleep(1.5)
    assert _order(client, order_id)["state"] == "ACKNOWLEDGED"
    monday_open = conftest.ny_ms(2026, 11, 16, 9, 30)
    clocked_queue.set_clock(monday_open)
    assert _wait_state(client, order_id, {"FILLED"}) == "FILLED"
    fills = client.get("/v1/fills", params={"account_id": USD_PAPER}).json()
    validate("execution.schema.json", "FillList", fills)
    (fill,) = [f for f in fills["fills"] if f["order_id"] == order_id]
    assert fill["ts_ms"] == monday_open
