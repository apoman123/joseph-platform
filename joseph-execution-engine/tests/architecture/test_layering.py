# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Runs import-linter layer and independence contracts as a test."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

_PROJECT = pathlib.Path(__file__).resolve().parents[2]


def test_import_linter_contracts_hold() -> None:
    """The import-linter layer and independence contracts all hold."""
    executable = shutil.which("lint-imports")
    assert executable is not None, "import-linter is not installed"
    result = subprocess.run(
        [executable, "--config", str(_PROJECT / ".importlinter")],
        cwd=_PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
