# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Tests the engine launcher in ``conftest`` without booting an engine."""

from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import IO
from typing import Any

import pytest

from tests import conftest


def _child_command(
    project: str, uvicorn_args: list[str], env: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Stands in for ``conftest._command``: a short Python child, no uvicorn.

    Args:
        project: Ignored project name.
        uvicorn_args: Ignored uvicorn arguments.
        env: Environment, passed through.

    Returns:
        A command that prints one line and exits, and ``env``.
    """
    del project, uvicorn_args
    return [sys.executable, "-c", "print('child ready')"], env


def test_start_closes_its_log_handle_and_the_process_still_logs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parent's log handle closes; the child keeps writing to the log."""
    handles: list[IO[bytes]] = []
    real_popen = subprocess.Popen

    def _recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        """Starts the process and records the log handle it was given.

        Args:
            *args: ``Popen`` arguments.
            **kwargs: ``Popen`` keyword arguments.

        Returns:
            The started process.
        """
        handles.append(kwargs["stdout"])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(conftest, "_command", _child_command)
    monkeypatch.setattr(subprocess, "Popen", _recording_popen)
    process, log_path = conftest._start(
        "probe", conftest.EXECUTION, "app:factory", 0, {}, tmp_path
    )
    assert [handle.closed for handle in handles] == [True]
    assert process.wait(timeout=30) == 0
    assert log_path.read_text().strip() == "child ready"
