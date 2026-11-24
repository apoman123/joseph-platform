# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Boots the Joseph engines as real processes for system tests.

The market engine runs with only the deterministic ``mock`` venue so the
suite is reproducible offline; the execution engine runs with LIVE trading
disabled (its default). Both use throwaway SQLite databases.

Further execution-engine instances start on first use, all against the
same market engine:

* ``clocked`` and ``clocked_queue``: the real engine with a settable clock
  (``launchers/clocked_execution.py``), so exchange-session rules are
  tested at pinned New York times instead of the wall clock; the second
  queues closed-market orders (``JOSEPH_EQUITY_CLOSED_MARKET_ORDERS``).
* ``live``: the stock entry point with ``JOSEPH_LIVE_TRADING_ENABLED=true``,
  a step-up TOTP secret and no venue or broker credentials.

The market engine caps anchored studies at ``MAX_ANCHORED_BARS`` (the
smallest value it accepts) so the cap is cheap to exceed.

Engines run inside their own uv projects (``uv run --frozen``). Set
``JOSEPH_SYSTEM_ENGINE_VENVS`` to a checkout whose ``<engine>/.venv``
directories are already synced (e.g. the main checkout, from a git
worktree) to run this checkout's engine sources with those environments.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import socket
import subprocess
import time
import zoneinfo
from collections.abc import Callable
from collections.abc import Iterator

import httpx
import jsonschema
import pytest
import referencing

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "joseph-terminal" / "packages" / "contracts" / "schemas"
LAUNCHERS = ROOT / "system-tests" / "launchers"
BASE_URI = "https://schemas.joseph.local/v1/"
SERVICE_TOKEN = "system-test-service-token"
TOKEN_A = "system-token-a"
TOKEN_B = "system-token-b"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
MAX_ANCHORED_BARS = 2_000
"""Anchored-study cap of the system-test market engine (engine minimum)."""
STEP_UP_SECRET = "NJXXGZLQNAWXG6LTORSW2LLUMVZXILJR"
"""Base32 TOTP secret of the ``live`` execution engine."""
ENGINE_VENVS = "JOSEPH_SYSTEM_ENGINE_VENVS"
"""Optional checkout holding pre-synced ``<engine>/.venv`` environments."""
MARKET = "joseph-market-engine"
EXECUTION = "joseph-execution-engine"
EXECUTION_APP = "joseph_execution.bootstrap:create_application"
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")


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


def _command(
    project: str, uvicorn_args: list[str], env: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Builds the command line and environment that run one engine.

    By default the engine runs in its own uv project. With
    ``JOSEPH_SYSTEM_ENGINE_VENVS`` set, the Python of
    ``<that checkout>/<project>/.venv`` runs this checkout's sources
    (``PYTHONPATH`` precedes the environment's editable install).

    Args:
        project: Project directory name under the repository root.
        uvicorn_args: Arguments after ``uvicorn``.
        env: Base environment.

    Returns:
        Command and environment.
    """
    # A uv environment chosen for the system-test project must not leak
    # into the engines, which are separate uv projects.
    run_env = {k: v for k, v in env.items() if k != "UV_PROJECT_ENVIRONMENT"}
    venvs = os.environ.get(ENGINE_VENVS)
    if not venvs:
        return ["uv", "run", "--frozen", "uvicorn", *uvicorn_args], run_env
    python = pathlib.Path(venvs) / project / ".venv" / "bin" / "python"
    sources = str(ROOT / project / "src")
    run_env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (sources, env.get("PYTHONPATH")))
    )
    return [str(python), "-m", "uvicorn", *uvicorn_args], run_env


def _start(
    name: str,
    project: str,
    app: str,
    port: int,
    env: dict[str, str],
    log_dir: pathlib.Path,
    app_dir: pathlib.Path | None = None,
) -> tuple[subprocess.Popen[bytes], pathlib.Path]:
    """Starts one engine with uvicorn.

    Args:
        name: Instance name (log file stem).
        project: Project directory name under the repository root.
        app: Uvicorn application factory path.
        port: Port to bind.
        env: Environment variables.
        log_dir: Directory for the server log.
        app_dir: Extra directory searched for ``app`` (launchers).

    Returns:
        The process and its log path.
    """
    log_path = log_dir / f"{name}.log"
    log_file = log_path.open("wb")
    uvicorn_args = [
        app,
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    if app_dir is not None:
        uvicorn_args += ["--app-dir", str(app_dir)]
    command, run_env = _command(project, uvicorn_args, env)
    process = subprocess.Popen(
        command,
        cwd=ROOT / project,
        env=run_env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return process, log_path


@contextlib.contextmanager
def _serving(
    name: str,
    project: str,
    app: str,
    env: dict[str, str],
    log_dir: pathlib.Path,
    app_dir: pathlib.Path | None = None,
    port: int | None = None,
) -> Iterator[tuple[str, pathlib.Path]]:
    """Runs one engine until the context exits.

    Args:
        name: Instance name (log file stem).
        project: Project directory name under the repository root.
        app: Uvicorn application factory path.
        env: Environment variables.
        log_dir: Directory for the server log.
        app_dir: Extra directory searched for ``app`` (launchers).
        port: Port to bind; a free one when omitted.

    Yields:
        Base URL and log path.
    """
    port = port or _free_port()
    url = f"http://127.0.0.1:{port}"
    process, log_path = _start(name, project, app, port, env, log_dir, app_dir)
    try:
        _wait_healthy(f"{url}/health", process, log_path)
        yield url, log_path
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        if process.poll() is None:
            process.kill()


def _common_env() -> dict[str, str]:
    """Returns the environment shared by every engine instance.

    Returns:
        Environment mapping.
    """
    return {
        **os.environ,
        "JOSEPH_DEV_TOKENS": (
            f"{TOKEN_A}:{TENANT_A}:user-a,{TOKEN_B}:{TENANT_B}:user-b"
        ),
        "JOSEPH_SERVICE_TOKEN": SERVICE_TOKEN,
        "JOSEPH_LIVE_TRADING_ENABLED": "false",
        "JOSEPH_CORS_ORIGINS": "http://localhost:3000",
    }


def _execution_env(
    database: pathlib.Path, market_url: str, **extra: str
) -> dict[str, str]:
    """Builds an execution-engine environment against the market engine.

    Args:
        database: SQLite database file.
        market_url: Market engine base URL.
        **extra: Variables to add or override.

    Returns:
        Environment mapping.
    """
    return {
        **_common_env(),
        "JOSEPH_DATABASE_URL": f"sqlite:///{database}",
        "JOSEPH_MARKET_URL": market_url,
        "JOSEPH_VAULT_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(),
        "JOSEPH_PREVIEW_SECRET": base64.b64encode(os.urandom(32)).decode(),
        "JOSEPH_MARKET_GATEWAY": "http",
        "JOSEPH_BACKGROUND_TASKS": "true",
        "JOSEPH_TICK_INTERVAL_MS": "500",
        **extra,
    }


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
    market_env = {
        **_common_env(),
        "JOSEPH_DATABASE_URL": f"sqlite:///{work / 'market.db'}",
        "JOSEPH_EXECUTION_URL": exec_url,
        "JOSEPH_ENABLED_VENUES": "mock",
        "JOSEPH_DEMO_ONLY": "true",
        "JOSEPH_PIPELINE_INTERVAL_S": "2",
        "JOSEPH_MAX_ANCHORED_BARS": str(MAX_ANCHORED_BARS),
    }
    exec_env = _execution_env(work / "execution.db", market_url)
    with contextlib.ExitStack() as stack:
        _, market_log = stack.enter_context(
            _serving(
                MARKET,
                MARKET,
                "joseph.bootstrap:create_application",
                market_env,
                work,
                port=market_port,
            )
        )
        _, exec_log = stack.enter_context(
            _serving(
                EXECUTION,
                EXECUTION,
                EXECUTION_APP,
                exec_env,
                work,
                port=exec_port,
            )
        )
        yield {
            "market": market_url,
            "execution": exec_url,
            "market_log": str(market_log),
            "exec_log": str(exec_log),
        }


def _auth(token: str) -> dict[str, str]:
    """Builds a bearer authorization header.

    Args:
        token: Dev token.

    Returns:
        Header mapping.
    """
    return {"Authorization": f"Bearer {token}"}


def ny_ms(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> int:
    """Returns the UTC epoch milliseconds of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=NEW_YORK)
    return int(moment.timestamp() * 1000)


@dataclasses.dataclass(frozen=True, slots=True)
class ClockedEngine:
    """An execution engine running on a clock the tests set.

    Attributes:
        client: HTTP client authenticated as tenant A.
        log: Server log path.
    """

    client: httpx.Client
    log: str

    def set_clock(self, now_ms: int) -> None:
        """Pins the engine clock.

        Pinned times must not lie after the wall clock: the market engine
        stamps quotes with the wall clock, so a later engine time would
        make every quote stale.

        Args:
            now_ms: UTC epoch milliseconds.
        """
        assert now_ms <= time.time() * 1000, "pin a time in the past"
        response = self.client.put(
            "/system-test/clock",
            json={"now_ms": now_ms},
            headers={"X-Joseph-Service-Token": SERVICE_TOKEN},
        )
        assert response.status_code == 200, response.text
        assert response.json()["now_ms"] == now_ms


@contextlib.contextmanager
def _clocked_engine(
    name: str, market_url: str, work: pathlib.Path, **extra: str
) -> Iterator[ClockedEngine]:
    """Runs an execution engine on a settable clock.

    Same configuration as the main instance (LIVE off, paper simulator,
    HTTP market gateway) unless ``extra`` overrides it; its time comes
    from ``PUT /system-test/clock`` (``launchers/clocked_execution.py``).

    Args:
        name: Instance name (log file stem).
        market_url: Market engine base URL.
        work: Directory for the database and the log.
        **extra: Environment overrides.

    Yields:
        The running engine.
    """
    env = _execution_env(
        work / "execution.db",
        market_url,
        JOSEPH_SYSTEM_TEST_CLOCK_MS=str(int(time.time() * 1000)),
        **extra,
    )
    with (
        _serving(
            name,
            EXECUTION,
            "clocked_execution:create_application",
            env,
            work,
            app_dir=LAUNCHERS,
        ) as (url, log_path),
        httpx.Client(
            base_url=url, headers=_auth(TOKEN_A), timeout=30
        ) as client,
    ):
        yield ClockedEngine(client, str(log_path))


@pytest.fixture(scope="session")
def clocked(
    services: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ClockedEngine]:
    """Execution engine on a settable clock (closed markets: ``reject``).

    Args:
        services: Running market engine (and main execution engine).
        tmp_path_factory: Pytest temporary directory factory.

    Yields:
        The running engine.
    """
    with _clocked_engine(
        "execution-clocked",
        services["market"],
        tmp_path_factory.mktemp("clocked"),
    ) as engine:
        yield engine


@pytest.fixture(scope="session")
def clocked_queue(
    services: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ClockedEngine]:
    """Execution engine on a settable clock (closed markets: ``queue``).

    Args:
        services: Running market engine (and main execution engine).
        tmp_path_factory: Pytest temporary directory factory.

    Yields:
        The running engine.
    """
    with _clocked_engine(
        "execution-queue",
        services["market"],
        tmp_path_factory.mktemp("queue"),
        JOSEPH_EQUITY_CLOSED_MARKET_ORDERS="queue",
    ) as engine:
        yield engine


@pytest.fixture(scope="session")
def live(
    services: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[httpx.Client]:
    """Execution engine with the global LIVE gate open (tenant A client).

    The stock entry point with ``JOSEPH_LIVE_TRADING_ENABLED=true`` and no
    venue or broker credentials: brokers stay disabled (gated, no SDK
    client, no network), private venue streams are off and step-up codes
    are TOTP codes of ``STEP_UP_SECRET``.

    Args:
        services: Running market engine (and main execution engine).
        tmp_path_factory: Pytest temporary directory factory.

    Yields:
        Client.
    """
    work = tmp_path_factory.mktemp("live")
    env = _execution_env(
        work / "execution.db",
        services["market"],
        JOSEPH_LIVE_TRADING_ENABLED="true",
        JOSEPH_STEP_UP_TOTP_SECRET=STEP_UP_SECRET,
        JOSEPH_LIVE_STREAMS="false",
    )
    with (
        _serving("execution-live", EXECUTION, EXECUTION_APP, env, work) as (
            url,
            _,
        ),
        httpx.Client(
            base_url=url, headers=_auth(TOKEN_A), timeout=30
        ) as client,
    ):
        yield client


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
