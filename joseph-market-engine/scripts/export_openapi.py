# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Exports the market engine's OpenAPI document into the contracts package.

Usage, from the ``joseph-market-engine`` directory::

    uv run python scripts/export_openapi.py [--check]

Without arguments the OpenAPI and built-in catalog are written to
``joseph-terminal/packages/contracts/openapi/``:

* ``market-engine.openapi.json``, from a DEMO-only application (in-memory
  SQLite, no pipeline, no indicator plugin packages);
* ``builtin-scripts.json``, the ``CatalogList`` of the built-in scripts
  (without source), which the terminal's DEMO mode serves;
* ``fixtures/builtin-indicator-outputs.json`` (next to ``openapi/``),
  every built-in indicator's output on fixed bars, which the DEMO study
  evaluator must reproduce (parity test).
* ``builtin-script-catalog.json``, the ``GET /v1/scripts/catalog`` listing
  of the built-in filters and alerts (without plugins), which the
  terminal's DEMO screener and alert dialog mirror.

Managed Python script, catalog, strategy activation and heatmap model
schemas are also regenerated in
``joseph-terminal/packages/contracts/schemas/python-scripts.schema.json``.

With ``--check`` nothing is written and the exit status is 1 when a
committed file is missing or differs from the generated one (CI).
"""

from __future__ import annotations

import dataclasses
import json
import math
import pathlib
import sys
from typing import Any

import pydantic

from joseph import bootstrap
from joseph.api.routers import scripts as script_routes
from joseph.market_data.app import heatmap_models
from joseph.platform import config
from joseph.scripts import activations
from joseph.scripts import builtin
from joseph.scripts import catalog
from joseph.scripts import models as scripts
from joseph.scripts import service
from joseph.scripts import worker

_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "openapi"
)
_OPENAPI = _DIR / "market-engine.openapi.json"
_BUILTINS = _DIR / "builtin-scripts.json"
_OUTPUTS = _DIR.parent / "fixtures" / "builtin-indicator-outputs.json"
_PARITY_INSTRUMENT = "mock:perp:BTCUSD"
_PARITY_START_MS = 1_794_787_200_000
_PARITY_BARS = 160
_QUARTER_MS = 900_000
_MINUTE_MS = 60_000
_DAY_MS = 86_400_000
_SCRIPTS = _DIR.parent / "schemas" / "python-scripts.schema.json"
_SCRIPT_CATALOG = _DIR / "builtin-script-catalog.json"


def _script_contracts() -> str:
    """Exports managed-script and heatmap models using canonical common refs.

    Returns:
        JSON Schema text whose names are unique across the shared contract set.
    """
    models: list[type[pydantic.BaseModel]] = [
        scripts.ScriptInput,
        scripts.ScriptVersion,
        scripts.ScriptRun,
        scripts.ScriptResult,
        scripts.ScanInput,
        scripts.ScanJob,
        scripts.AlertJobInput,
        scripts.AlertJob,
        scripts.ScriptBatchRun,
        scripts.ScriptBatchResult,
        script_routes.CatalogScript,
        script_routes.CatalogList,
        activations.StrategyActivationInput,
        activations.StrategyActivation,
        activations.StrategyActivationList,
        heatmap_models.HeatmapSnapshot,
        heatmap_models.HeatmapCatalog,
    ]
    definitions = {}
    for model in models:
        schema = model.model_json_schema()
        definitions.update(schema.pop("$defs", {}))
        definitions[model.__name__] = schema
    common = {"Timeframe", "Venue", "MarketType"}
    for name in common:
        definitions.pop(name, None)
    text = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "python-scripts.schema.json",
            "$defs": definitions,
        },
        indent=2,
        sort_keys=True,
    )
    for name in common:
        text = text.replace(
            f'"#/$defs/{name}"', f'"common.schema.json#/$defs/{name}"'
        )
    return text + "\n"


def _openapi() -> str:
    """Generates the OpenAPI document.

    Returns:
        The document as sorted, indented JSON text.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        strategy_activations_enabled=False,
        script_plugin_packages="none",
    )
    app = bootstrap.create_application(bootstrap.build_container(settings))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def _builtins() -> str:
    """Generates the built-in script catalog.

    Returns:
        The ``CatalogList`` of the built-ins as indented JSON text, in
        catalog order.
    """
    built = catalog.build(catalog.CatalogConfig(packages=catalog.NO_PACKAGES))
    listing = script_routes.CatalogList(
        scripts=[
            script_routes.CatalogScript.model_validate(entry.describe())
            for entry in built.entries()
        ],
        failures=[],
    )
    return json.dumps(listing.model_dump(mode="json"), indent=2) + "\n"


def _bar(start: int, length: int, index: int) -> dict[str, Any]:
    """Builds one deterministic parity bar.

    Args:
        start: Bar start in UTC milliseconds.
        length: Bar duration in milliseconds.
        index: Position, which fixes the prices and volume.

    Returns:
        Script bar without a buy volume.
    """
    close = round(100 + 10 * math.sin(index / 9) + 3 * math.cos(index / 4), 4)
    opened = round(close - 1.5 * math.sin(index / 3), 4)
    return {
        "time_ms": start,
        "end_ms": start + length,
        "is_final": True,
        "open": opened,
        "high": round(max(opened, close) + 0.5 + index % 3 * 0.25, 4),
        "low": round(min(opened, close) - 0.5 - index % 2 * 0.25, 4),
        "close": close,
        "volume": float(10 + index * 7 % 13),
        "quote_turnover": None,
        "trade_count": None,
        "taker_buy_volume": None,
        "taker_buy_method": None,
    }


def _parity_series() -> dict[str, Any]:
    """Builds the fixed parity bars, intrabars and daily bars.

    Returns:
        15m bars (the first half with taker buy volume), five 1m intrabars
        per later bar, and the completed UTC days.
    """
    bars = []
    for index in range(_PARITY_BARS):
        bar = _bar(_PARITY_START_MS + index * _QUARTER_MS, _QUARTER_MS, index)
        if index < _PARITY_BARS // 2:
            bar["taker_buy_volume"] = round(
                bar["volume"] * (0.3 + index % 5 / 10), 4
            )
            bar["taker_buy_method"] = "taker"
        bars.append(bar)
    intrabars = []
    for index, bar in enumerate(bars[_PARITY_BARS // 2 :]):
        for minute in range(5):
            intrabars.append(
                _bar(
                    int(bar["time_ms"]) + minute * _MINUTE_MS,
                    _MINUTE_MS,
                    index * 5 + minute,
                )
            )
    days = []
    for start in range(_PARITY_START_MS, int(bars[-1]["end_ms"]), _DAY_MS):
        inside = [
            bar
            for bar in bars
            if start <= int(bar["time_ms"]) < start + _DAY_MS
        ]
        if len(inside) * _QUARTER_MS < _DAY_MS:
            continue
        days.append(
            {
                "time_ms": start,
                "end_ms": start + _DAY_MS,
                "is_final": True,
                "open": inside[0]["open"],
                "high": max(bar["high"] for bar in inside),
                "low": min(bar["low"] for bar in inside),
                "close": inside[-1]["close"],
                "volume": sum(bar["volume"] for bar in inside),
            }
        )
    return {"bars": bars, "intrabars": intrabars, "days": days}


def _rounded(value: Any) -> Any:
    """Rounds floats in JSON data to ten significant digits.

    Args:
        value: JSON value.

    Returns:
        The value with rounded floats.
    """
    if isinstance(value, float):
        return float(f"{value:.10g}")
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def _parity_cases(
    bars: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Lists the parity cases: every built-in with defaults plus anchors.

    Args:
        bars: Parity bars.

    Returns:
        Module name and input overrides per case.
    """
    anchor = int(bars[50]["time_ms"]) + 1
    cases: list[tuple[str, dict[str, Any]]] = [
        (name, {}) for name in builtin.MODULES["indicators"]
    ]
    cases.append(
        (
            "anchored_vwap",
            {"anchor": "time", "anchor_ms": anchor, "band_2": 2.0},
        )
    )
    cases.append(
        (
            "regression_channel",
            {"anchor_ms": anchor, "end_ms": int(bars[130]["time_ms"])},
        )
    )
    return cases


def _outputs() -> str:
    """Runs every built-in indicator on the fixed parity bars.

    Returns:
        Bars, extra series and each case's plots, styles, drawings and
        alerts as JSON text.
    """
    series = _parity_series()
    bars = series["bars"]
    extra = {"intrabars": series["intrabars"], "period": series["days"]}
    results = []
    for name, overrides in _parity_cases(bars):
        cls = builtin.script_class(f"indicators.{name}")
        inputs = {
            key: spec.default
            for key, spec in dict(getattr(cls, "INPUTS", {})).items()
        } | overrides
        result = worker.run_class(
            cls,
            {
                "kind": "indicator",
                "bars": bars,
                "inputs": inputs,
                "instrument_id": _PARITY_INSTRUMENT,
                "timeframe": "15m",
                "extra_series": {
                    alias: extra[alias]
                    for alias in dict(getattr(cls, "REQUESTS", {}))
                },
                "periods": service.period_bounds(
                    _PARITY_INSTRUMENT, bars, tuple(getattr(cls, "PERIODS", ()))
                ),
            },
        )
        results.append(
            {
                "script_id": f"builtin:{name}",
                "inputs": overrides,
                "plots": result["plots"],
                "plot_styles": result["plot_styles"],
                "visuals": result["visuals"],
                "alerts": result["alerts"],
            }
        )
    body = {
        "instrument_id": _PARITY_INSTRUMENT,
        "timeframe": "15m",
        **series,
        "cases": results,
    }
    return json.dumps(_rounded(body), separators=(",", ":")) + "\n"


def _script_catalog() -> str:
    """Generates the built-in filter and alert catalog listing.

    Returns:
        ``{"scripts": [...]}`` as the catalog route describes the built-in
        filters and alerts, as indented JSON text in catalog order.
    """
    built = catalog.build(catalog.CatalogConfig(packages=catalog.NO_PACKAGES))
    listing = [
        entry.describe()
        for entry in built.entries()
        if entry.kind in {"filter", "alert"}
    ]
    return json.dumps({"scripts": listing}, indent=2) + "\n"


def main() -> int:
    """Writes or checks the generated files.

    Returns:
        Process exit code: 0 when the files were written or are up to date,
        1 when ``--check`` finds one out of date.
    """
    outputs = (
        (_OPENAPI, _openapi()),
        (_BUILTINS, _builtins()),
        (_OUTPUTS, _outputs()),
        (_SCRIPTS, _script_contracts()),
        (_SCRIPT_CATALOG, _script_catalog()),
    )
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
