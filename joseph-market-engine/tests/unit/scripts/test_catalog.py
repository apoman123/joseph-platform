# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Built-in and plugin script catalog and in-process execution."""

from __future__ import annotations

import dataclasses
import importlib.metadata
from typing import Any
from typing import ClassVar
from typing import cast

import pytest

import joseph_script
from joseph.domain import errors
from joseph.scripts import builtin
from joseph.scripts import catalog
from joseph.scripts import models
from joseph.scripts import worker


class Echo(joseph_script.Indicator):
    """Plots the close; a plugin fixture."""

    ID = "echo"
    TITLE = "Echo"
    INPUTS: ClassVar[dict[str, joseph_script.Input]] = {
        "scale": joseph_script.Input(1.0, minimum=0)
    }

    def on_bar(self, ctx: worker.Context) -> None:
        """Plots the scaled close.

        Args:
            ctx: Script context.
        """
        ctx.plot("Echo", ctx.bar["close"] * float(ctx.input("scale")))


class NoId(joseph_script.Filter):
    """Lacks a catalog id; a broken plugin fixture."""

    def matches(self, ctx: worker.Context) -> bool:
        """Accepts everything.

        Args:
            ctx: Script context.

        Returns:
            True.
        """
        del ctx
        return True


@dataclasses.dataclass
class FakeDistribution:
    """Distribution metadata of a fake entry point.

    Attributes:
        name: Distribution name.
        version: Distribution version.
    """

    name: str
    version: str


@dataclasses.dataclass
class FakePoint:
    """An entry point returning a fixed object.

    Attributes:
        name: Entry-point name.
        value: Object returned by ``load``.
        dist: Providing distribution.
    """

    name: str
    value: Any
    dist: FakeDistribution | None = None

    def load(self) -> Any:
        """Returns the configured object.

        Returns:
            The value.
        """
        return self.value


def _bars(closes: list[float]) -> list[dict[str, Any]]:
    """Builds closed one-minute bars.

    Args:
        closes: Close prices.

    Returns:
        Bars with open=high=low=close.
    """
    return [
        {
            "time_ms": index * 60_000,
            "end_ms": (index + 1) * 60_000,
            "is_final": True,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
        for index, close in enumerate(closes)
    ]


def test_builtins_are_valid_tenant_source() -> None:
    """Every built-in is one role subclass whose public source validates."""
    built = catalog.build(catalog.CatalogConfig(packages="none"))
    assert built.entries(), "the platform ships built-in scripts"
    for entry in built.entries():
        assert entry.origin == "builtin"
        assert entry.script_id.startswith("builtin:")
        assert entry.source is not None
        assert "from joseph." not in entry.source.replace(
            "from joseph.scripts import worker", ""
        )
        models.validate_source(
            models.ScriptInput(
                script_id=entry.script_id.split(":")[1].replace("_", "-"),
                kind=entry.kind,  # type: ignore[arg-type]  # A catalog kind is a ScriptKind string.
                name=entry.title,
                source=entry.source,
            )
        )
    assert len(builtin.classes()) == len(built.entries())


def test_run_class_computes_builtin_ema() -> None:
    """The EMA built-in seeds with the first close and smooths after it."""
    cls = builtin.script_class("indicators.ema")
    result = worker.run_class(
        cls,
        {
            "kind": "indicator",
            "bars": _bars([10, 20, 30]),
            "inputs": {
                "length": 3,
                "source": "close",
                "offset": 0,
                "color": "#2962ff",
            },
        },
    )
    values = [point["value"] for point in result["plots"][0]["points"]]
    assert values == pytest.approx([10.0, 15.0, 22.5])


def test_run_class_rejects_wrong_role() -> None:
    """A class only runs in the role it implements."""
    with pytest.raises(ValueError, match="selected role"):
        worker.run_class(Echo, {"kind": "strategy", "bars": _bars([1])})


def test_plugins_load_namespaced_and_isolate_failures() -> None:
    """Plugins get namespaced ids; a broken package is skipped and reported."""
    built = catalog.build(
        catalog.CatalogConfig(),
        points=cast(
            "list[importlib.metadata.EntryPoint]",
            [
                FakePoint(
                    "good", lambda: [Echo], FakeDistribution("pkg", "1.2")
                ),
                FakePoint("bad", lambda: [NoId]),
                FakePoint("class", Echo),
            ],
        ),
    )
    entry = built.get("plugin:good:echo")
    assert entry.source is None
    assert entry.package == "pkg"
    assert entry.package_version == "1.2"
    assert entry.defaults == {"scale": 1.0}
    assert "plugin:class:echo" in built
    assert [failure.entry_point for failure in built.report.failures] == ["bad"]


def test_disabled_and_required_ids() -> None:
    """Disabled ids stay listed but refuse to run; required ids must load."""
    built = catalog.build(
        catalog.CatalogConfig(disabled=("builtin:ema",)), points=[]
    )
    assert "builtin:ema" in built
    with pytest.raises(errors.UnsupportedError):
        built.get("builtin:ema")
    with pytest.raises(errors.ValidationFailedError, match="plugin:x:y"):
        catalog.build(
            catalog.CatalogConfig(required=("plugin:x:y",)), points=[]
        )


def test_catalog_ids_are_distinct_from_tenant_slugs() -> None:
    """Tenant slugs never parse as catalog ids."""
    assert catalog.is_catalog_id("builtin:ema")
    assert catalog.is_catalog_id("plugin:pkg.one:echo")
    assert not catalog.is_catalog_id("my-ema")
