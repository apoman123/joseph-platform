# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in filter scans and alert jobs without the Docker runtime."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import fastapi
import pytest
import sqlalchemy as sa
from fastapi import testclient

from joseph import bootstrap
from joseph.alerts.adapters import sql as alerts_sql
from joseph.domain import clock
from joseph.domain import identity
from joseph.platform import config
from joseph.scripts import jobs
from joseph.scripts import models

HEADERS = {"Authorization": "Bearer jobs-a"}
"""Ordinary authenticated author."""
OTHER = {"Authorization": "Bearer jobs-b"}
"""Independent tenant used to verify job isolation."""
PRINCIPAL = identity.Principal("tenant-a", "user-a")
"""The owner behind HEADERS."""


@pytest.fixture
def app() -> Iterator[
    tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock]
]:
    """Serves a DEMO engine with the isolated runtime disabled.

    Yields:
        Started client, container and controllable market clock.
    """
    now = clock.ManualClock(1_795_277_530_000)
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        strategy_activations_enabled=False,
        script_runtime="disabled",
        dev_tokens=config.parse_dev_tokens(
            "jobs-a:tenant-a:user-a,jobs-b:tenant-b:user-b"
        ),
    )
    container = bootstrap.build_container(settings, clock=now)
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection, container, now


def _manager(client: testclient.TestClient) -> jobs.ScriptJobs:
    """Returns the application's job coordinator.

    Args:
        client: Started application client.

    Returns:
        The ScriptJobs instance.
    """
    assert isinstance(client.app, fastapi.FastAPI)
    manager: jobs.ScriptJobs = client.app.state.script_jobs
    return manager


def _finish(
    client: testclient.TestClient, job: dict[str, Any]
) -> dict[str, Any]:
    """Polls a scan until it leaves pending/running.

    Args:
        client: Application client.
        job: Submitted job.

    Returns:
        Final job state.
    """
    path = f"/v1/script-scans/{job['job_id']}"
    for _ in range(400):
        job = client.get(path, headers=HEADERS).json()
        if job["status"] not in {"pending", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError(job)


def _scan(
    client: testclient.TestClient, body: dict[str, Any]
) -> dict[str, Any]:
    """Submits a composed scan and waits for it.

    Args:
        client: Application client.
        body: ScanInput body.

    Returns:
        Final job state.
    """
    response = client.post("/v1/script-scans", headers=HEADERS, json=body)
    assert response.status_code == 202, response.text
    return _finish(client, response.json())


def test_liquidity_qualification_scan_needs_no_docker(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """The built-in filter qualifies the DEMO perps and stock in process.

    Args:
        app: DEMO application without the isolated runtime.
    """
    client, _, _ = app
    job = _scan(
        client,
        {
            "venue": "mock",
            "conditions": [{"script_id": "builtin:liquidity-qualification"}],
        },
    )
    models.ScanJob.model_validate(job)
    assert job["status"] == "complete" and job["scanned"] == job["total"]
    assert {match["instrument_id"] for match in job["matches"]} == {
        "mock:perp:BTCUSD",
        "mock:perp:ETHUSD",
        "mock:stock:ACME",
    }
    assert all("order_book" not in m["snapshot"] for m in job["matches"])
    assert all("evaluated_at_ms" not in m["snapshot"] for m in job["matches"])
    assert job["conditions"][0]["version"] == 0
    assert job["conditions"][0]["inputs"]["max_data_age_s"] == 300
    assert client.get(
        f"/v1/script-scans/{job['job_id']}", headers=OTHER
    ).status_code == (404)


def test_conditions_combine_with_and(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """A path filter and body conditions must all match.

    Args:
        app: DEMO application.
    """
    client, _, _ = app
    response = client.post(
        "/v1/scripts/builtin:price/scan",
        headers=HEADERS,
        json={
            "venue": "mock",
            "market_type": "perp",
            "inputs": {"operator": "above", "value": 0},
            "conditions": [
                {
                    "script_id": "builtin:liquidity-qualification",
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    job = _finish(client, response.json())
    assert [c["script_id"] for c in job["conditions"]] == [
        "builtin:price",
        "builtin:liquidity-qualification",
    ]
    assert {m["instrument_id"] for m in job["matches"]} == {
        "mock:perp:BTCUSD",
        "mock:perp:ETHUSD",
    }
    none = _scan(
        client,
        {
            "venue": "mock",
            "conditions": [
                {"script_id": "builtin:liquidity-qualification"},
                {
                    "script_id": "builtin:price",
                    "inputs": {"operator": "above", "value": 1e12},
                },
            ],
        },
    )
    assert none["status"] == "complete" and none["matches"] == []


def test_missing_history_is_unavailable_never_a_match(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """Bar filters without history report every instrument unavailable.

    Args:
        app: DEMO application.
    """
    client, _, _ = app
    job = _scan(
        client,
        {"venue": "mock", "conditions": [{"script_id": "builtin:rsi"}]},
    )
    assert job["status"] == "complete" and job["matches"] == []
    assert len(job["unavailable"]) == job["total"] == 4
    assert all("closed bars" in row["error"] for row in job["unavailable"])
    excluded = _scan(
        client,
        {
            "venue": "mock",
            "conditions": [
                {"script_id": "builtin:rsi"},
                {
                    "script_id": "builtin:price",
                    "inputs": {"operator": "below", "value": 0},
                },
            ],
        },
    )
    assert excluded["unavailable"] == [] and excluded["matches"] == []
    history = _scan(
        client,
        {
            "venue": "mock",
            "history_bars": 60,
            "tf": "1h",
            "conditions": [
                {
                    "script_id": "builtin:rsi",
                    "inputs": {
                        "operator": "between",
                        "value": 0,
                        "value2": 100,
                    },
                }
            ],
        },
    )
    assert len(history["matches"]) == 4 and history["unavailable"] == []


def test_turnover_order_keeps_the_most_active(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """order_by turnover scans the highest 24h turnover first.

    Args:
        app: DEMO application.
    """
    client, _, _ = app
    job = _scan(
        client,
        {
            "venue": "mock",
            "market_type": "perp",
            "order_by": "turnover",
            "max_symbols": 1,
            "conditions": [
                {
                    "script_id": "builtin:turnover",
                    "inputs": {"operator": "above", "value": 0},
                }
            ],
        },
    )
    assert job["universe_count"] == 3 and job["total"] == 1
    assert [m["instrument_id"] for m in job["matches"]] == ["mock:perp:BTCUSD"]


@pytest.mark.parametrize(
    ("body", "status", "fragment"),
    [
        ({"venue": "mock"}, 422, "at least one filter"),
        (
            {"conditions": [{"script_id": "builtin:price-cross"}]},
            422,
            "not a Filter",
        ),
        (
            {
                "conditions": [
                    {"script_id": "builtin:price", "inputs": {"bogus": 1}}
                ]
            },
            422,
            "no input bogus",
        ),
        (
            {
                "conditions": [
                    {
                        "script_id": "builtin:price",
                        "inputs": {"operator": "sideways"},
                    }
                ]
            },
            422,
            "must be one of",
        ),
        (
            {
                "conditions": [
                    {"script_id": "builtin:rsi", "inputs": {"length": 1}}
                ]
            },
            422,
            "between",
        ),
        ({"conditions": [{"script_id": "builtin:nope"}]}, 404, "nope"),
        ({"inputs": {"value": 1}}, 422, "path filter"),
    ],
)
def test_scan_requests_are_validated(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
    body: dict[str, Any],
    status: int,
    fragment: str,
) -> None:
    """Wrong roles, undeclared or out-of-range inputs are refused.

    Args:
        app: DEMO application.
        body: Scan request.
        status: Expected HTTP status.
        fragment: Expected error message fragment.
    """
    client, _, _ = app
    response = client.post("/v1/script-scans", headers=HEADERS, json=body)
    assert response.status_code == status, response.text
    assert fragment in response.text


def test_tenant_filters_still_need_the_isolated_runtime(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """An owned Filter script is refused while Docker is disabled.

    Args:
        app: DEMO application.
    """
    client, _, _ = app
    created = client.post(
        "/v1/scripts",
        headers=HEADERS,
        json={
            "script_id": "mine",
            "kind": "filter",
            "name": "Mine",
            "source": (
                "from joseph_script import Filter\n"
                "class Mine(Filter):\n"
                "    def matches(self, ctx):\n"
                "        return True\n"
            ),
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(
        "/v1/scripts/mine/scan", headers=HEADERS, json={"venue": "mock"}
    )
    assert response.status_code == 422
    assert "Docker" in response.text


def test_cancel_and_restart_keep_partial_results(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """Cancelled scans stay cancelled; unfinished ones fail on restart.

    Args:
        app: DEMO application.
    """
    client, container, _ = app
    manager = _manager(client)
    job = client.post(
        "/v1/script-scans",
        headers=HEADERS,
        json={
            "venue": "mock",
            "conditions": [{"script_id": "builtin:liquidity-qualification"}],
        },
    ).json()
    cancelled = client.post(
        f"/v1/script-scans/{job['job_id']}/cancel", headers=HEADERS
    ).json()
    assert cancelled["status"] in {"cancelled", "complete"}
    assert _finish(client, job)["status"] == cancelled["status"]
    pending = models.ScanJob(
        job_id="abandoned",
        script_id="builtin:price",
        version=0,
        source_hash="x",
        status="running",
        created_at_ms=1,
    )
    manager._insert(PRINCIPAL, pending, "scan")
    jobs.ScriptJobs(
        manager.scripts,
        manager.universe,
        container.engine,
        history=manager.signals,
        subscriptions=manager.subscriptions,
        outbox=manager.outbox,
    )
    restarted = manager.get(PRINCIPAL, "abandoned", "scan")
    assert restarted.model_dump()["status"] == "failed"


def _alert_body(**overrides: Any) -> dict[str, Any]:
    """Builds a built-in alert job request.

    Args:
        **overrides: Field replacements.

    Returns:
        AlertJobInput body for an always-matching channel condition.
    """
    return {
        "script_id": "builtin:channel",
        "version": 0,
        "instrument_id": "mock:perp:BTCUSD",
        "tf": "1m",
        "enabled": True,
        "history_bars": 20,
        "inputs": {"lower": 0, "upper": 1e12, "condition": "inside"},
        **overrides,
    }


def test_builtin_alert_dedupes_checkpoints_and_never_executes(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Future closed bars are recorded once, as ALERT_ONLY custom alerts.

    Args:
        app: DEMO application with a controllable clock.
        contract_validate: Shared schema assertion helper.
    """
    client, container, now = app
    manager = _manager(client)
    assert manager._loop_task is not None
    created = client.post(
        "/v1/script-alerts", headers=HEADERS, json=_alert_body()
    )
    assert created.status_code == 201, created.text
    models.AlertJob.model_validate(created.json())
    assert created.json()["source_hash"] and created.json()["title"]
    job_id = created.json()["job_id"]
    assert client.portal is not None
    client.portal.call(
        manager.evaluate_alert,
        PRINCIPAL,
        models.AlertJob.model_validate(created.json()),
    )
    signals_path = "/v1/signals?kind=CUSTOM_ALERT"
    assert not client.get(signals_path, headers=HEADERS).json()["signals"]
    now.advance(180_000)
    current = manager.get(PRINCIPAL, job_id, "alert")
    assert isinstance(current, models.AlertJob)
    client.portal.call(manager.evaluate_alert, PRINCIPAL, current)
    first = client.get(signals_path, headers=HEADERS).json()["signals"]
    assert first
    for signal in first:
        contract_validate("signal.schema.json", "Signal", signal)
        assert signal["mode"] == "ALERT_ONLY" and signal["direction"] == "NONE"
        assert signal["strategy_id"] == "builtin:channel"
        assert "inside the channel" in signal["message"]
    current = manager.get(PRINCIPAL, job_id, "alert")
    assert isinstance(current, models.AlertJob)
    assert current.last_error is None and current.emitted == len(first)
    client.portal.call(manager.evaluate_alert, PRINCIPAL, current)
    restarted = jobs.ScriptJobs(
        manager.scripts,
        manager.universe,
        container.engine,
        history=manager.signals,
        subscriptions=manager.subscriptions,
        outbox=manager.outbox,
    )
    stored = restarted.get(PRINCIPAL, job_id, "alert")
    assert isinstance(stored, models.AlertJob)
    stored.last_processed_ms = created.json()["last_processed_ms"]
    client.portal.call(restarted.evaluate_alert, PRINCIPAL, stored)
    again = client.get(signals_path, headers=HEADERS).json()["signals"]
    assert {s["signal_id"] for s in again} == {s["signal_id"] for s in first}
    due = alerts_sql.SqlOutbox(container.engine).due(now.now_ms(), 100, 1000)
    assert all(record.channel.value != "EXECUTION" for record in due)
    assert client.get("/v1/script-alerts", headers=OTHER).json() == []
    assert (
        client.delete(f"/v1/script-alerts/{job_id}", headers=OTHER).status_code
        == 404
    )


def test_changed_builtin_source_is_reported_not_evaluated(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
) -> None:
    """A pinned source hash that no longer matches stops evaluation.

    Args:
        app: DEMO application.
    """
    client, container, now = app
    manager = _manager(client)
    job = models.AlertJob.model_validate(
        client.post(
            "/v1/script-alerts", headers=HEADERS, json=_alert_body()
        ).json()
    )
    with container.engine.begin() as connection:
        connection.execute(
            sa.update(jobs.JOBS)
            .where(jobs.JOBS.c.job_id == job.job_id)
            .values(
                body_json=job.model_copy(
                    update={"source_hash": "older"}
                ).model_dump_json()
            )
        )
    now.advance(180_000)
    stored = manager.get(PRINCIPAL, job.job_id, "alert")
    assert isinstance(stored, models.AlertJob)
    assert client.portal is not None
    client.portal.call(manager.evaluate_alert, PRINCIPAL, stored)
    stored = manager.get(PRINCIPAL, job.job_id, "alert")
    assert isinstance(stored, models.AlertJob)
    assert stored.last_error is not None and "changed" in stored.last_error
    assert stored.emitted == 0


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"version": 1}, "version 0"),
        ({"script_id": "builtin:price"}, "cannot publish alert"),
        ({"inputs": {"level": 1}}, "no input level"),
        ({"subscription_ids": ["someone-else"]}, "owned external"),
    ],
)
def test_alert_requests_are_validated(
    app: tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock],
    overrides: dict[str, Any],
    fragment: str,
) -> None:
    """Version pins, roles, inputs and destinations are checked.

    Args:
        app: DEMO application.
        overrides: Invalid field values.
        fragment: Expected message fragment.
    """
    client, _, _ = app
    response = client.post(
        "/v1/script-alerts", headers=HEADERS, json=_alert_body(**overrides)
    )
    assert response.status_code == 422, response.text
    assert fragment in response.text
