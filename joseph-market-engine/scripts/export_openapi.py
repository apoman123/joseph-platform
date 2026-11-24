# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Exports the market engine's OpenAPI document into the contracts package.

Usage: ``uv run python scripts/export_openapi.py [--check]``. With
``--check`` the command fails when the committed document is out of date.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

from joseph import bootstrap
from joseph.platform import config

_OUT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "openapi"
    / "market-engine.openapi.json"
)


def main() -> int:
    """Writes or checks the OpenAPI document.

    Returns:
        Process exit code.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
    )
    app = bootstrap.create_application(bootstrap.build_container(settings))
    text = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not _OUT.exists() or _OUT.read_text(encoding="utf-8") != text:
            print("market-engine OpenAPI is out of date")  # noqa: T201
            return 1
        return 0
    _OUT.write_text(text, encoding="utf-8")
    print(f"wrote {_OUT}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
