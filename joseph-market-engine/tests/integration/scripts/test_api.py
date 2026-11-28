# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Five-role authoring, real Docker execution, filters and alert delivery."""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

import fastapi
import pytest
from fastapi import testclient

from joseph import bootstrap
from joseph.alerts.adapters import sql as alerts_sql
from joseph.domain import clock
from joseph.domain import identity
from joseph.platform import config
from joseph.scripts import jobs
from joseph.scripts import models

HEADERS = {"Authorization": "Bearer scripts-a"}
"""Ordinary authenticated author, never a service or exchange credential."""
OTHER = {"Authorization": "Bearer scripts-b"}
"""Independent tenant used to verify private source and job isolation."""


def _application(
    *, docker: bool = False
) -> tuple[bootstrap.Container, clock.ManualClock]:
    """Builds an isolated DEMO application without external credentials.

    Args:
        docker: Whether to use the actual prebuilt isolated Python runtime.

    Returns:
        Unstarted container and deterministic market clock.
    """
    now = clock.ManualClock(1_795_277_530_000)
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        script_runtime="docker" if docker else "disabled",
        dev_tokens=config.parse_dev_tokens(
            "scripts-a:tenant-a:user-a,scripts-b:tenant-b:user-b"
        ),
    )
    return bootstrap.build_container(settings, clock=now), now


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Serves private script authoring with runtime disabled.

    Yields:
        Fully started application, including its automatic MCP endpoint.
    """
    container, _ = _application()
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection


@pytest.fixture
def docker_client() -> Iterator[
    tuple[testclient.TestClient, bootstrap.Container, clock.ManualClock]
]:
    """Starts the real isolated runtime only under explicit Docker test opt-in.

    Yields:
        Application client, service container and controllable market clock.
    """
    if os.environ.get("JOSEPH_SCRIPT_TESTS") != "1":
        pytest.skip("set JOSEPH_SCRIPT_TESTS=1 after building the runner image")
    container, now = _application(docker=True)
    with testclient.TestClient(
        bootstrap.create_application(container)
    ) as connection:
        yield connection, container, now


def _source(
    kind: str, body: str | None = None, *, slug: str | None = None
) -> dict[str, Any]:
    """Builds a concrete source declaration with one independent abstract base.

    Args:
        kind: One of the five public script roles.
        body: Optional indented concrete method implementation.
        slug: Optional owned slug, default role-derived test name.

    Returns:
        JSON source request for the public script API.
    """
    methods = {
        "indicator": (
            "    def on_bar(self, ctx):\n        ctx.plot('Close', "
            "ctx.bar['close'])\n"
        ),
        "strategy": (
            "    def on_bar(self, ctx):\n        if ctx.bar_index == "
            "0: ctx.entry('long')\n        if ctx.bar_index == 2: "
            "ctx.close()\n"
        ),
        "filter": (
            "    def matches(self, ctx):\n        return "
            "float(ctx.snapshot.get('last') or 0) > 0\n"
        ),
        "alert": (
            "    def on_bar(self, ctx):\n        ctx.alert('Closed "
            "bar condition', name='closed')\n"
        ),
        "library": (
            "    def exports(self):\n        return {'twice': lambda "
            "value: value * 2}\n"
        ),
    }
    base = kind.title()
    return {
        "script_id": slug or f"test-{kind}",
        "kind": kind,
        "name": f"Test {kind}",
        "source": (
            f"from joseph_script import {base}\n"
            f"class Test{base}({base}):\n{body or methods[kind]}"
        ),
    }


@pytest.mark.parametrize(
    "kind", ["indicator", "strategy", "library", "filter", "alert"]
)
def test_five_private_kinds_and_immutable_versions(
    client: testclient.TestClient, kind: str
) -> None:
    """Keeps source private and preserves pinned versions after archive.

    Args:
        client: Running authoring application.
        kind: Independently inherited public script role.
    """
    body = _source(kind)
    created = client.post("/v1/scripts", headers=HEADERS, json=body)
    assert created.status_code == 201, created.text
    first = created.json()
    slug = body["script_id"]
    assert client.get(f"/v1/scripts/{slug}", headers=OTHER).status_code == 404
    edited = client.post(
        f"/v1/scripts/{slug}/versions",
        headers=HEADERS,
        json={**body, "name": "Renamed", "expected_version": 1},
    )
    assert edited.status_code == 201, edited.text
    assert edited.json()["version"] == 2
    assert (
        client.post(
            f"/v1/scripts/{slug}/versions",
            headers=HEADERS,
            json={**body, "expected_version": 1},
        ).status_code
        == 409
    )
    assert (
        client.delete(f"/v1/scripts/{slug}", headers=HEADERS).status_code == 204
    )
    assert client.get(f"/v1/scripts/{slug}", headers=HEADERS).status_code == 404
    assert (
        client.get(f"/v1/scripts/{slug}/versions/1", headers=HEADERS).json()
        == first
    )


def test_validation_never_executes_source_and_requires_inheritance(
    client: testclient.TestClient,
) -> None:
    """Rejects declarations while safely accepting unexecuted top-level errors.

    Args:
        client: Running authoring application with runtime disabled.
    """
    body = _source("indicator")
    body["source"] += "\nraise RuntimeError('must never run in API host')\n"
    response = client.post("/v1/scripts/validate", headers=HEADERS, json=body)
    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "kind": "indicator",
        "executed": False,
    }
    body["source"] = "def run(ctx): pass"
    assert (
        client.post(
            "/v1/scripts/validate", headers=HEADERS, json=body
        ).status_code
        == 422
    )
    assert (
        client.post("/v1/scripts", json=_source("library")).status_code == 401
    )


def test_real_library_import_and_fill_bar_provenance(
    docker_client: tuple[
        testclient.TestClient, bootstrap.Container, clock.ManualClock
    ],
) -> None:
    """Runs actual containers and verifies immutable imports and next-bar fills.

    Args:
        docker_client: Explicitly enabled isolated Docker application.
    """
    client, _, _ = docker_client
    assert (
        client.post(
            "/v1/scripts", headers=HEADERS, json=_source("library")
        ).status_code
        == 201
    )
    body = _source(
        "indicator",
        (
            "    def on_bar(self, ctx):\n        ctx.plot('Double', "
            "twice(ctx.bar['close']))\n"
        ),
    )
    body["source"] = "from libraries.math import twice\n" + body["source"]
    body["libraries"] = {"math": {"script_id": "test-library", "version": 1}}
    assert (
        client.post("/v1/scripts", headers=HEADERS, json=body).status_code
        == 201
    )
    request = {"instrument_id": "mock:perp:BTCUSD", "tf": "1m", "limit": 10}
    output = client.post(
        "/v1/scripts/test-indicator/run", headers=HEADERS, json=request
    )
    assert output.status_code == 200, output.text
    assert len(output.json()["plots"][0]["points"]) >= 9
    assert output.json()["feed_state"] == "DEMO"
    assert (
        client.post(
            "/v1/scripts", headers=HEADERS, json=_source("strategy")
        ).status_code
        == 201
    )
    result = client.post(
        "/v1/scripts/test-strategy/run",
        headers=HEADERS,
        json={**request, "initial_equity": 1000000},
    )
    assert result.status_code == 200, result.text
    fills = result.json()["fills"]
    bars = client.get(
        "/v1/candles",
        params={
            "instrument_id": str(request["instrument_id"]),
            "tf": "1m",
            "limit": 10,
        },
    ).json()["candles"]
    assert fills[0]["time_ms"] == bars[1]["start_ms"]
    assert fills[0]["price"] == float(bars[1]["open"])
    assert fills[0]["effect"] == "entry" and fills[1]["effect"] == "exit"
    assert (
        client.post(
            "/v1/scripts/test-library/run", headers=HEADERS, json=request
        ).status_code
        == 422
    )


def test_real_market_filter_and_private_progress(
    docker_client: tuple[
        testclient.TestClient, bootstrap.Container, clock.ManualClock
    ],
) -> None:
    """Scans actual mock instruments with isolated per-symbol filter instances.

    Args:
        docker_client: Explicitly enabled isolated Docker application.
    """
    client, _, _ = docker_client
    assert (
        client.post(
            "/v1/scripts", headers=HEADERS, json=_source("filter")
        ).status_code
        == 201
    )
    response = client.post(
        "/v1/scripts/test-filter/scan",
        headers=HEADERS,
        json={"venue": "mock", "market_type": "perp", "max_symbols": 2},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    path = f"/v1/script-scans/{job['job_id']}"
    assert client.get(path, headers=OTHER).status_code == 404
    for _ in range(100):
        job = client.get(path, headers=HEADERS).json()
        if job["status"] not in {"pending", "running"}:
            break
        time.sleep(0.05)
    assert job["status"] == "complete", job
    assert job["scanned"] == 2 and len(job["matches"]) == 2
    assert job["universe_count"] == 3


def test_real_alert_dedupes_and_uses_only_notification_outbox(
    docker_client: tuple[
        testclient.TestClient, bootstrap.Container, clock.ManualClock
    ],
    contract_validate: Callable[[str, str, object], None],
) -> None:
    """Records future closed bars exactly once without any execution forwarding.

    Args:
        docker_client: Running isolated app and controllable real provider
            clock.
        contract_validate: Canonical shared schema assertion helper.
    """
    client, container, now = docker_client
    assert (
        client.post(
            "/v1/scripts", headers=HEADERS, json=_source("alert")
        ).status_code
        == 201
    )
    body = {
        "script_id": "test-alert",
        "version": 1,
        "instrument_id": "mock:perp:BTCUSD",
        "tf": "1m",
        "enabled": True,
        "history_bars": 10,
    }
    created = client.post("/v1/script-alerts", headers=HEADERS, json=body)
    assert created.status_code == 201, created.text
    assert isinstance(client.app, fastapi.FastAPI)
    manager: jobs.ScriptJobs = client.app.state.script_jobs
    principal = identity.Principal("tenant-a", "user-a")
    job_id = created.json()["job_id"]
    initial = models.AlertJob.model_validate(created.json())
    assert client.portal is not None
    client.portal.call(manager.evaluate_alert, principal, initial)
    assert not client.get("/v1/signals", headers=HEADERS).json()["signals"]
    now.advance(120000)
    current = manager.get(principal, job_id, "alert")
    assert isinstance(current, models.AlertJob)
    client.portal.call(manager.evaluate_alert, principal, current)
    first = client.get("/v1/signals", headers=HEADERS).json()["signals"]
    assert first and all(row["kind"] == "CUSTOM_ALERT" for row in first)
    for signal in first:
        contract_validate("signal.schema.json", "Signal", signal)
        assert signal["direction"] == "NONE" and signal["mode"] == "ALERT_ONLY"
    current = manager.get(principal, job_id, "alert")
    assert isinstance(current, models.AlertJob)
    client.portal.call(manager.evaluate_alert, principal, current)
    assert client.get("/v1/signals", headers=HEADERS).json()["signals"] == first
    outbox = alerts_sql.SqlOutbox(container.engine)
    due = outbox.due(now.now_ms(), 100, 1000)
    assert all(record.channel.value != "EXECUTION" for record in due)
    assert (
        client.put(
            f"/v1/script-alerts/{job_id}", headers=OTHER, json=body
        ).status_code
        == 404
    )


def test_library_dataclasses_support_future_annotations(
    docker_client: tuple[
        testclient.TestClient, bootstrap.Container, clock.ManualClock
    ],
) -> None:
    """Loads standard-library dataclasses under distinct immutable module names.

    Args:
        docker_client: Running Docker-backed source and execution application.
    """
    client, _, _ = docker_client
    library = _source("library", slug="typed-library")
    library["source"] = """from __future__ import annotations
from dataclasses import dataclass
from joseph_script import Library
@dataclass
class Value:
    price: float
class TypedLibrary(Library):
    def exports(self):
        return {"Value": Value}
"""
    assert (
        client.post("/v1/scripts", headers=HEADERS, json=library).status_code
        == 201
    )
    indicator = _source(
        "indicator",
        (
            "    def on_bar(self, ctx):\n        ctx.plot('Typed', "
            "Value(ctx.bar['close']).price)\n"
        ),
        slug="typed-consumer",
    )
    indicator["source"] = (
        "from libraries.typed import Value\n" + indicator["source"]
    )
    indicator["libraries"] = {
        "typed": {"script_id": "typed-library", "version": 1}
    }
    assert (
        client.post("/v1/scripts", headers=HEADERS, json=indicator).status_code
        == 201
    )
    response = client.post(
        "/v1/scripts/typed-consumer/run",
        headers=HEADERS,
        json={"instrument_id": "mock:perp:BTCUSD", "tf": "1m", "limit": 5},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plots"][0]["points"]
