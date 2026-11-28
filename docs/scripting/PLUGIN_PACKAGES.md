# Shipping scripts as a plugin package

A plugin package is an ordinary Python distribution that adds indicators,
strategies, alerts or filters to the market engine's catalog without putting
their source in this repository. Plugin scripts are the same
`joseph_script` subclasses described in the other guides. They run in the
market engine process as trusted operator code, and their source stays
protected.

## Package layout

```
my-scripts/
  pyproject.toml
  src/my_scripts/
    __init__.py        # scripts() factory
    trend_meter.py     # one joseph_script subclass per module (recommended)
  tests/
```

```toml
[project]
name = "my-scripts"
version = "1.0.0"
dependencies = ["joseph-market-engine"]   # provides joseph_script

[project.entry-points."joseph.scripts"]
mine = "my_scripts:scripts"
```

```python
# src/my_scripts/__init__.py
from my_scripts import trend_meter


def scripts():
    """Returns this package's script classes in catalog order."""
    return [trend_meter.TrendMeter]
```

The entry point may name a zero-argument factory that returns a sequence of
classes, or a single class. Every class must:

- inherit exactly one of `Indicator`, `Strategy`, `Library`, `Filter`, `Alert`;
- be concrete (implement the role's method);
- set `ID` to a lowercase slug unique within the package;
- declare inputs as `joseph_script.Input` and requests as `joseph_script.Request`.

The catalog id is `plugin:<entry point name>:<ID>`, for example
`plugin:mine:trend_meter`. Entry-point names are lowercase letters, digits,
`.`, `_` and `-`. A plugin cannot shadow a built-in or another plugin.
Plugins may import any installed library, including private ones.

## Installing

```sh
make plugins PLUGIN_REQUIREMENTS="-e ../my-scripts"     # development
python -m joseph.scripts.catalog --check                # lists ids, exit 1 on failure
```

Container images take the same requirements through `MARKET_PLUGIN_REQUIREMENTS`
([CI/CD](../CI_CD.md)). Settings:

| Variable | Meaning |
| --- | --- |
| `JOSEPH_SCRIPT_PLUGIN_PACKAGES` | `*` (default) loads every entry point, `none` none, or a comma list of entry-point names |
| `JOSEPH_SCRIPT_PLUGINS_DISABLED` | Catalog ids listed but not runnable (built-ins too) |
| `JOSEPH_SCRIPT_PLUGINS_REQUIRED` | Catalog ids that must load, or the engine refuses to start |

A package that fails to import, or a class that fails validation, is skipped
and logged. The rest of the catalog still loads, and the failure is shown in
`GET /v1/scripts/catalog` under `failures`.

## Testing a plugin

Run classes directly, without an engine:

```python
from joseph.scripts import worker

result = worker.run_class(TrendMeter, {
    "kind": "indicator",
    "bars": bars,                       # list of bar dicts, oldest first
    "inputs": {"length": 20},
})
assert result["plots"][0]["name"] == "Trend"
```

To check catalog loading, build a catalog with
`joseph.scripts.catalog.build(...)` and assert that `plugin:<entry>:<ID>` is
present.
