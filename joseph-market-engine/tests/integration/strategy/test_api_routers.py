# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""HTTP tests of the strategy, signal and backtest routers."""

from __future__ import annotations

import copy
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence

import fastapi
import pytest
from fastapi import testclient

from joseph.api.routers import backtests
from joseph.api.routers import signals as signals_router
from joseph.api.routers import strategies
from joseph.backtest import runner
from joseph.backtest import service as backtest_service
from joseph.domain import clock
from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import structures
from joseph.domain import timeframes
from joseph.signals.adapters import memory as signal_memory
from joseph.signals.app import service as signal_service
from joseph.strategy.adapters import memory
from joseph.strategy.app import version_service
from joseph.strategy.domain import compiler
from joseph.strategy.testing import api as api_testing
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios

TOKENS = {
    "token-a": identity.Principal("tenant-a", "alice"),
    "token-b": identity.Principal("tenant-b", "bob"),
}


def _principal(request: fastapi.Request) -> identity.Principal:
    """Authenticates a request against ``TOKENS``.

    Args:
        request: Incoming request with an ``Authorization`` header.

    Returns:
        Principal for the bearer token.

    Raises:
        errors.UnauthenticatedError: Without a known token.
    """
    header = request.headers.get("Authorization", "")
    principal = TOKENS.get(header.removeprefix("Bearer "))
    if principal is None:
        raise errors.UnauthenticatedError("missing or invalid token")
    return principal


class _Provider:
    """Frame provider returning the Long A scenario for any request."""

    async def frames(
        self,
        instrument_id: instruments.InstrumentId,
        signal_tf: timeframes.Timeframe,
        requirements: compiler.EngineRequirements,
        from_ms: int,
        to_ms: int,
    ) -> Sequence[structures.BarFrame]:
        """Returns the Long A scenario frames.

        Args:
            instrument_id: Instrument (ignored).
            signal_tf: Signal timeframe (ignored).
            requirements: Engine requirements (ignored).
            from_ms: Window start (ignored).
            to_ms: Window end (ignored).

        Returns:
            The scenario frames.
        """
        del instrument_id, signal_tf, requirements, from_ms, to_ms
        return scenarios.long_a_frames()


@pytest.fixture
def client(manual_clock: clock.ManualClock) -> Iterator[testclient.TestClient]:
    """Builds an app with the strategy, signal and backtest routers.

    Args:
        manual_clock: Clock of the version service.

    Yields:
        A client for the running app, with in-memory stores.
    """
    versions = version_service.StrategyVersionService(
        memory.InMemoryStrategyRepository(), manual_clock
    )
    sig_service = signal_service.SignalService(
        signal_memory.InMemorySignalRepository()
    )
    app = fastapi.FastAPI()
    api_testing.install_error_handler(app)
    app.include_router(
        strategies.build_router(versions=versions, get_principal=_principal)
    )
    app.include_router(
        signals_router.build_router(
            signals=sig_service, get_principal=_principal
        )
    )
    app.include_router(
        backtests.build_router(
            backtests=backtest_service.BacktestService(
                versions, runner.BacktestRunner(_Provider())
            ),
            get_principal=_principal,
        )
    )
    with testclient.TestClient(app) as test_client:
        yield test_client


def _auth(token: str = "token-a") -> dict[str, str]:
    """Builds an auth header.

    Args:
        token: Bearer token; a key of ``TOKENS`` authenticates.

    Returns:
        The request headers.
    """
    return {"Authorization": f"Bearer {token}"}


def _long_a() -> dict[str, object]:
    """Returns the Long A fixture payload.

    Returns:
        A fresh copy of the JSON-compatible definition.
    """
    valid = fixtures.load_fixture("strategy")["valid"]
    assert isinstance(valid, list)
    return copy.deepcopy(valid[0])


def test_authoring_flow(
    client: testclient.TestClient,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """HTTP authoring creates, lists, versions, validates, activates, pauses."""
    created = client.post("/v1/strategies", json=_long_a(), headers=_auth())
    assert created.status_code == 201
    contract_validate("strategy.schema.json", "StrategyVersion", created.json())
    assert created.json()["strategy_version_id"] == "discount-ote-reversal:v1"
    listed = client.get("/v1/strategies", headers=_auth()).json()
    contract_validate("strategy.schema.json", "StrategyVersionList", listed)
    edited = _long_a()
    edited["name"] = "Long A edited"
    v2 = client.post(
        "/v1/strategies/discount-ote-reversal/versions",
        json=edited,
        headers=_auth(),
    )
    assert v2.status_code == 201 and v2.json()["version"] == 2
    history = client.get(
        "/v1/strategies/discount-ote-reversal", headers=_auth()
    ).json()["versions"]
    assert [v["version"] for v in history] == [2, 1]
    check = client.post(
        "/v1/strategies/validate", json=_long_a(), headers=_auth()
    ).json()
    contract_validate("strategy.schema.json", "ValidationResult", check)
    assert check["valid"] is True
    revalidated = client.post(
        "/v1/strategies/discount-ote-reversal/validate",
        json={"version": 1},
        headers=_auth(),
    )
    assert revalidated.json()["valid"] is True
    activated = client.post(
        "/v1/strategies/discount-ote-reversal/activate",
        json={"version": 2, "mode": "PAPER"},
        headers=_auth(),
    )
    assert activated.status_code == 200
    assert activated.json()["state"] == "ACTIVE"
    assert activated.json()["activation"]["mode"] == "PAPER"
    paused = client.post(
        "/v1/strategies/discount-ote-reversal/pause", headers=_auth()
    )
    assert paused.json()["state"] == "PAUSED"


def test_auto_live_is_refused_with_explanation(
    client: testclient.TestClient,
) -> None:
    """AUTO_LIVE activation returns TRADE_MODE_DISABLED."""
    client.post("/v1/strategies", json=_long_a(), headers=_auth())
    response = client.post(
        "/v1/strategies/discount-ote-reversal/activate",
        json={"version": 1, "mode": "AUTO_LIVE"},
        headers=_auth(),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TRADE_MODE_DISABLED"


def test_tenant_isolation_and_auth(client: testclient.TestClient) -> None:
    """Other tenants get 404; anonymous callers get 401."""
    client.post("/v1/strategies", json=_long_a(), headers=_auth())
    other = client.get(
        "/v1/strategies/discount-ote-reversal", headers=_auth("token-b")
    )
    assert other.status_code == 404
    assert client.get("/v1/strategies", headers=_auth("token-b")).json() == {
        "versions": []
    }
    anonymous = client.get("/v1/strategies")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "UNAUTHENTICATED"


def test_schema_violations_are_rejected(client: testclient.TestClient) -> None:
    """Code-like nodes, float decimals and malformed ids fail validation."""
    bad = _long_a()
    bad["when"] = {"op": "EVAL", "code": "__import__('os')"}
    assert (
        client.post("/v1/strategies", json=bad, headers=_auth()).status_code
        == 422
    )
    floaty = _long_a()
    floaty["risk"] = {
        "min_rr": 2.0,
        "stop_model": "AOI_INVALIDATION",
        "target_model": "NEAREST_OPPOSING_LIQUIDITY",
    }
    assert (
        client.post("/v1/strategies", json=floaty, headers=_auth()).status_code
        == 422
    )
    assert (
        client.get("/v1/strategies/Bad Id", headers=_auth()).status_code == 422
    )


def test_backtest_endpoint(
    client: testclient.TestClient,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Backtests run over HTTP and return a contract report."""
    client.post("/v1/strategies", json=_long_a(), headers=_auth())
    body = {
        "version": 1,
        "instrument_id": "binance:perp:BTCUSDT",
        "from_ms": frame_testing.BASE_MS,
        "to_ms": frame_testing.BASE_MS + 40 * 300_000,
        "fee_bps": "4",
        "slippage_bps": "2",
        "max_hold_bars": 20,
    }
    response = client.post(
        "/v1/strategies/discount-ote-reversal/backtests",
        json=body,
        headers=_auth(),
    )
    assert response.status_code == 200
    report = response.json()
    contract_validate("strategy.schema.json", "BacktestReport", report)
    assert report["bars_evaluated"] == 40
    assert [s["kind"] for s in report["signals"]] == ["ENTRY_CONFIRMED"]
    bad = client.post(
        "/v1/strategies/discount-ote-reversal/backtests",
        json={**body, "to_ms": 0},
        headers=_auth(),
    )
    assert bad.status_code == 422


def test_signal_listing(
    client: testclient.TestClient,
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """The signal list matches the contract and refuses a zero limit."""
    response = client.get(
        "/v1/signals?kind=ENTRY_CONFIRMED,ENTRY_BLOCKED&limit=10",
        headers=_auth(),
    )
    assert response.status_code == 200
    contract_validate("signal.schema.json", "SignalList", response.json())
    assert client.get("/v1/signals?limit=0", headers=_auth()).status_code == 422
