"""Boots both Joseph engines as real processes for system tests.

The market engine runs with only the deterministic ``mock`` venue so the
suite is reproducible offline; the execution engine runs with LIVE trading
disabled (its default). Both use throwaway SQLite databases.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import socket
import subprocess
import time
from collections.abc import Callable
from collections.abc import Iterator

import httpx
import jsonschema
import pytest
import referencing

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "joseph-terminal" / "packages" / "contracts" / "schemas"
BASE_URI = "https://schemas.joseph.local/v1/"
SERVICE_TOKEN = "system-test-service-token"
TOKEN_A = "system-token-a"
TOKEN_B = "system-token-b"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _free_port() -> int:
    """Returns an unused localhost TCP port.

    Returns:
        Port number.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(
    url: str,
    process: subprocess.Popen[bytes],
    log_path: pathlib.Path,
    timeout_s: float = 90,
) -> None:
    """Polls ``url`` until it returns 200 or the process exits.

    Args:
        url: Health endpoint.
        process: Server process.
        log_path: Server log, shown on failure.
        timeout_s: Maximum wait in seconds.

    Raises:
        RuntimeError: If the server never becomes healthy.
    """
    started = time.monotonic()
    deadline = started + timeout_s
    last_error = "no response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as err:
            last_error = f"{type(err).__name__}: {err}"
        time.sleep(0.5)
    log = log_path.read_text(errors="replace")[-4000:]
    raise RuntimeError(
        f"server at {url} not healthy after "
        f"{time.monotonic() - started:.1f}s (exit code {process.poll()}, "
        f"last error {last_error}):\n{log}"
    )


def _start(
    project: str,
    app: str,
    port: int,
    env: dict[str, str],
    log_dir: pathlib.Path,
) -> tuple[subprocess.Popen[bytes], pathlib.Path]:
    """Starts one engine with uvicorn inside its own uv project.

    Args:
        project: Project directory name under the repository root.
        app: Uvicorn application factory path.
        port: Port to bind.
        env: Environment variables.
        log_dir: Directory for the server log.

    Returns:
        The process and its log path.
    """
    log_path = log_dir / f"{project}.log"
    log_file = log_path.open("wb")
    process = subprocess.Popen(  # noqa: S603 - fixed local command.
        [
            "uv",
            "run",
            "--frozen",
            "uvicorn",
            app,
            "--factory",  # noqa: S607
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT / project,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return process, log_path


@pytest.fixture(scope="session")
def services(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[dict[str, str]]:
    """Starts the market and execution engines for the whole session.

    Args:
        tmp_path_factory: Pytest temporary directory factory.

    Yields:
        Mapping with ``market`` and ``execution`` base URLs.
    """
    work = tmp_path_factory.mktemp("services")
    market_port, exec_port = _free_port(), _free_port()
    market_url = f"http://127.0.0.1:{market_port}"
    exec_url = f"http://127.0.0.1:{exec_port}"
    common = {
        **os.environ,
        "JOSEPH_DEV_TOKENS": (
            f"{TOKEN_A}:{TENANT_A}:user-a,{TOKEN_B}:{TENANT_B}:user-b"
        ),
        "JOSEPH_SERVICE_TOKEN": SERVICE_TOKEN,
        "JOSEPH_LIVE_TRADING_ENABLED": "false",
        "JOSEPH_CORS_ORIGINS": "http://localhost:3000",
    }
    market_env = {
        **common,
        "JOSEPH_DATABASE_URL": f"sqlite:///{work / 'market.db'}",
        "JOSEPH_EXECUTION_URL": exec_url,
        "JOSEPH_ENABLED_VENUES": "mock",
        "JOSEPH_DEMO_ONLY": "true",
        "JOSEPH_PIPELINE_INTERVAL_S": "2",
    }
    exec_env = {
        **common,
        "JOSEPH_DATABASE_URL": f"sqlite:///{work / 'execution.db'}",
        "JOSEPH_MARKET_URL": market_url,
        "JOSEPH_VAULT_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(),
        "JOSEPH_PREVIEW_SECRET": base64.b64encode(os.urandom(32)).decode(),
        "JOSEPH_MARKET_GATEWAY": "http",
        "JOSEPH_BACKGROUND_TASKS": "true",
        "JOSEPH_TICK_INTERVAL_MS": "500",
    }
    market, market_log = _start(
        "joseph-market-engine",
        "joseph.bootstrap:create_application",
        market_port,
        market_env,
        work,
    )
    execution, exec_log = _start(
        "joseph-execution-engine",
        "joseph_execution.bootstrap:create_application",
        exec_port,
        exec_env,
        work,
    )
    try:
        _wait_healthy(f"{market_url}/health", market, market_log)
        _wait_healthy(f"{exec_url}/health", execution, exec_log)
        yield {
            "market": market_url,
            "execution": exec_url,
            "market_log": str(market_log),
            "exec_log": str(exec_log),
        }
    finally:
        for process in (market, execution):
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            if process.poll() is None:
                process.kill()


def _auth(token: str) -> dict[str, str]:
    """Builds a bearer authorization header.

    Args:
        token: Dev token.

    Returns:
        Header mapping.
    """
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def market(services: dict[str, str]) -> Iterator[httpx.Client]:
    """HTTP client for the market engine authenticated as tenant A.

    Args:
        services: Running service URLs.

    Yields:
        Client.
    """
    with httpx.Client(
        base_url=services["market"], headers=_auth(TOKEN_A), timeout=60
    ) as client:
        yield client


@pytest.fixture(scope="session")
def execution(services: dict[str, str]) -> Iterator[httpx.Client]:
    """HTTP client for the execution engine authenticated as tenant A.

    Args:
        services: Running service URLs.

    Yields:
        Client.
    """
    with httpx.Client(
        base_url=services["execution"], headers=_auth(TOKEN_A), timeout=30
    ) as client:
        yield client


@pytest.fixture(scope="session")
def execution_b(services: dict[str, str]) -> Iterator[httpx.Client]:
    """HTTP client for the execution engine authenticated as tenant B.

    Args:
        services: Running service URLs.

    Yields:
        Client.
    """
    with httpx.Client(
        base_url=services["execution"], headers=_auth(TOKEN_B), timeout=30
    ) as client:
        yield client


@pytest.fixture(scope="session")
def anonymous(services: dict[str, str]) -> Iterator[dict[str, httpx.Client]]:
    """Unauthenticated clients for both services.

    Args:
        services: Running service URLs.

    Yields:
        Mapping ``market``/``execution`` to clients without credentials.
    """
    with (
        httpx.Client(base_url=services["market"], timeout=30) as m,
        httpx.Client(base_url=services["execution"], timeout=30) as e,
    ):
        yield {"market": m, "execution": e}


@pytest.fixture(scope="session")
def validate() -> Callable[[str, str, object], None]:
    """Provides ``validate(schema_file, definition, instance)``.

    Returns:
        Validator raising ``jsonschema.ValidationError`` on mismatch.
    """
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append(
            (contents["$id"], referencing.Resource.from_contents(contents))
        )
    registry = referencing.Registry().with_resources(resources)

    def _validate(schema_file: str, definition: str, instance: object) -> None:
        """Validates one instance against a contract definition.

        Args:
            schema_file: Schema file name.
            definition: ``$defs`` entry name.
            instance: Value to validate.
        """
        schema = {"$ref": f"{BASE_URI}{schema_file}#/$defs/{definition}"}
        jsonschema.Draft202012Validator(schema, registry=registry).validate(
            instance
        )

    return _validate
