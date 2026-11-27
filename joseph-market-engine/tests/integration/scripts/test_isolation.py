# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Real-container tests for the user-code trust boundary and resource limits."""

from __future__ import annotations

import os

import pytest

from joseph.domain import errors
from joseph.scripts import runner

pytestmark = pytest.mark.skipif(
    os.environ.get("JOSEPH_SCRIPT_TESTS") != "1",
    reason="opt in after building joseph-python-runner:1",
)


@pytest.mark.asyncio
async def test_runner_has_no_host_credentials_files_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs real source against Docker's filesystem and network boundaries.

    Args:
        monkeypatch: Adds a host-only credential that must not enter the runner.
    """
    monkeypatch.setenv("JOSEPH_TEST_HOST_SECRET", "host-only-test-value")
    source = """from joseph_script import Indicator
import os
import socket
from pathlib import Path
class Sandboxed(Indicator):
    def on_bar(self, ctx):
        assert os.environ.get("JOSEPH_TEST_HOST_SECRET") is None
        assert not Path("/home/apoman123/joseph-platform/README.md").exists()
        memory = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        assert memory == "268435456"
        assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "32"
        try:
            Path("/forbidden-write").write_text("blocked")
        except OSError:
            pass
        else:
            raise RuntimeError("root filesystem was writable")
        sock = socket.socket()
        sock.settimeout(0.2)
        try:
            sock.connect(("1.1.1.1", 443))
        except OSError:
            pass
        else:
            raise RuntimeError("network was reachable")
        finally:
            sock.close()
        print("user stdout must not corrupt the result" * 1000)
        ctx.plot("Isolated", 1)
"""
    result = await runner.DockerRunner(enabled=True).run(
        {
            "script_id": "isolation",
            "source_hash": "test-isolation",
            "kind": "indicator",
            "source": source,
            "bars": [
                {
                    "time_ms": 0,
                    "open": 1,
                    "close": 1,
                    "high": 1,
                    "low": 1,
                    "volume": 0,
                }
            ],
        }
    )
    assert result["plots"][0]["points"] == [
        {"time_ms": 0, "value": 1.0, "color": "#5cb8e6"}
    ]


@pytest.mark.asyncio
async def test_runner_terminates_infinite_user_code() -> None:
    """Bounds wall-clock time and removes a nonterminating user process."""
    source = (
        "from joseph_script import Indicator\nclass "
        "Infinite(Indicator):\n    def on_bar(self, ctx):"
        "\n        while True:\n            pass\n"
    )
    runtime = runner.DockerRunner(enabled=True, timeout_s=1)
    with pytest.raises(errors.ValidationFailedError, match="wall-clock"):
        await runtime.run(
            {
                "script_id": "infinite",
                "source_hash": "test-infinite",
                "kind": "indicator",
                "source": source,
                "bars": [{"time_ms": 0, "open": 1, "close": 1}],
            }
        )
