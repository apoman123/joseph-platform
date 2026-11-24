# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Exports the market engine's OpenAPI document into the contracts package.

Usage, from the ``joseph-market-engine`` directory::

    uv run python scripts/export_openapi.py [--check]

Without arguments two files are regenerated and written to
``joseph-terminal/packages/contracts/openapi/``:

* ``market-engine.openapi.json``, from a DEMO-only application (in-memory
  SQLite, no pipeline, no indicator plugin packages);
* ``builtin-indicator-plugins.json``, the contract ``IndicatorPluginList``
  of the built-in indicators, which the terminal's DEMO mode mirrors.

With ``--check`` nothing is written and the exit status is 1 when a
committed file is missing or differs from the generated one (CI).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

from joseph import bootstrap
from joseph.indicators import catalog
from joseph.platform import config

_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "openapi"
)
_OPENAPI = _DIR / "market-engine.openapi.json"
_BUILTINS = _DIR / "builtin-indicator-plugins.json"


def _openapi() -> str:
    """Generates the OpenAPI document.

    Returns:
        The document as sorted, indented JSON text.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        indicator_plugin_packages="none",
    )
    app = bootstrap.create_application(bootstrap.build_container(settings))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def _builtins() -> str:
    """Generates the built-in plugin list.

    Returns:
        The ``IndicatorPluginList`` of the built-ins as indented JSON text,
        in registration order.
    """
    listing = catalog.build_default_registry().list_infos()
    return json.dumps(listing, indent=2) + "\n"


def main() -> int:
    """Writes or checks the generated files.

    Returns:
        Process exit code: 0 when the files were written or are up to date,
        1 when ``--check`` finds one out of date.
    """
    outputs = ((_OPENAPI, _openapi()), (_BUILTINS, _builtins()))
    if "--check" in sys.argv:
        stale = [
            path.name
            for path, text in outputs
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        for name in stale:
            print(f"{name} is out of date")  # noqa: T201 - CLI script output
        return 1 if stale else 0
    for path, text in outputs:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")  # noqa: T201 - CLI script output
    return 0


if __name__ == "__main__":
    sys.exit(main())
