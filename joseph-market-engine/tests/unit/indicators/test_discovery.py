# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Unit tests for run-time discovery of indicator plugin packages."""

from __future__ import annotations

import importlib.metadata
import logging

import pytest

from joseph.indicators import builtin
from joseph.indicators import catalog
from joseph.indicators import discovery
from joseph.indicators.sdk import registry as registry_lib
from joseph.platform import config

_GROUP = discovery.ENTRY_POINT_GROUP


def _ep(name: str, value: str) -> importlib.metadata.EntryPoint:
    """Builds an entry point of the plugin group.

    Args:
        name: Entry-point name.
        value: ``module:attribute`` target.

    Returns:
        The entry point (no distribution).
    """
    return importlib.metadata.EntryPoint(name=name, value=value, group=_GROUP)


GOOD = _ep("echo", "joseph.indicators.testing:echo_plugins")
MISSING_MODULE = _ep("absent", "joseph.indicators.absent_module:plugins")
NOT_CALLABLE = _ep("constant", "joseph.indicators.discovery:ENTRY_POINT_GROUP")
NOT_A_SEQUENCE = _ep("single", "joseph.indicators.testing:EchoPlugin")
SHADOWING = _ep("shadow", "joseph.indicators.builtin:builtin_plugins")


def _registry() -> registry_lib.PluginRegistry:
    """Builds a registry of the built-ins.

    Returns:
        The registry.
    """
    return catalog.build_default_registry()


def test_select_filters_and_orders_entry_points() -> None:
    """``*`` takes all, ``none`` none, a list only those; order is by name."""
    eps = [_ep("zeta", "a:b"), _ep("alpha", "a:b"), _ep("mid", "a:b")]
    assert [ep.name for ep in discovery.select(eps, "*")] == [
        "alpha",
        "mid",
        "zeta",
    ]
    assert [ep.name for ep in discovery.select(eps, "")] == [
        "alpha",
        "mid",
        "zeta",
    ]
    assert discovery.select(eps, "none") == []
    assert discovery.select(eps, " NONE ") == []
    assert [ep.name for ep in discovery.select(eps, "zeta, mid")] == [
        "mid",
        "zeta",
    ]


def test_installed_entry_points_are_read_from_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``entry_points`` asks importlib for the plugin group only."""
    groups: list[str] = []

    def fake(*, group: str) -> list[importlib.metadata.EntryPoint]:
        """Records the requested group.

        Args:
            group: Entry-point group.

        Returns:
            Two entry points in reverse name order.
        """
        groups.append(group)
        return [_ep("b", "a:b"), _ep("a", "a:b")]

    monkeypatch.setattr(importlib.metadata, "entry_points", fake)
    assert [ep.name for ep in discovery.entry_points("*")] == ["a", "b"]
    assert groups == [_GROUP]


def test_builtins_load_first_then_entry_points_in_order() -> None:
    """External plugins follow the built-ins in their factory's order."""
    registry = _registry()
    report = discovery.load_external(
        registry, discovery.DiscoveryConfig(), [GOOD]
    )
    assert report.builtin == builtin.BUILTIN_IDS
    assert report.external == ("echo", "echo_zones")
    assert report.failures == ()
    assert registry.ids() == (*builtin.BUILTIN_IDS, "echo", "echo_zones")
    assert registry.kind_owner("ECHO_ZONE") == "echo_zones"


def test_failures_are_isolated_logged_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken entry point or plugin is skipped; the others still load."""
    registry = _registry()
    with caplog.at_level(logging.ERROR, logger=discovery.__name__):
        report = discovery.load_external(
            registry,
            discovery.DiscoveryConfig(),
            [SHADOWING, NOT_A_SEQUENCE, MISSING_MODULE, GOOD, NOT_CALLABLE],
        )
    assert report.external == ("echo", "echo_zones")
    by_entry: dict[str, list[discovery.LoadFailure]] = {}
    for failure in report.failures:
        by_entry.setdefault(failure.entry_point, []).append(failure)
    assert set(by_entry) == {"absent", "constant", "shadow", "single"}
    assert "ModuleNotFoundError" in by_entry["absent"][0].reason
    assert "not callable" in by_entry["constant"][0].reason
    assert "did not return a sequence" in by_entry["single"][0].reason
    assert by_entry["single"][0].plugin_id is None
    shadowed = [failure.plugin_id for failure in by_entry["shadow"]]
    assert shadowed == list(builtin.BUILTIN_IDS)
    assert all("ConflictError" in f.reason for f in by_entry["shadow"])
    assert registry.ids() == (*builtin.BUILTIN_IDS, "echo", "echo_zones")
    assert "indicator plugin absent from" in caplog.text
    assert "indicator plugin sma from" in caplog.text


def test_packages_setting_selects_entry_points() -> None:
    """Only the named entry points load; ``none`` loads none."""
    for packages, expected in (("echo", ("echo", "echo_zones")), ("none", ())):
        registry = _registry()
        report = discovery.load_external(
            registry,
            discovery.DiscoveryConfig(packages=packages),
            [GOOD, MISSING_MODULE],
        )
        assert report.external == expected
        assert report.failures == ()


def test_disabled_plugins_keep_their_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Built-in and external plugins may be disabled by configuration."""
    registry = _registry()
    with caplog.at_level(logging.WARNING, logger=discovery.__name__):
        report = discovery.load_external(
            registry,
            discovery.DiscoveryConfig(disabled=("rsi", "echo", "unknown")),
            [GOOD],
        )
    assert report.disabled == ("rsi", "echo")
    assert "cannot disable indicator plugin unknown" in caplog.text
    for plugin_id in ("rsi", "echo"):
        assert not registry.is_available(plugin_id)
        stored = registry.metadata(plugin_id)
        assert stored is not None
        assert stored["lifecycle"] == "DISABLED"
        assert stored["reason"] == discovery.DISABLED_REASON
    assert "disabled by configuration" in registry.describe_unavailable("rsi")
    infos = registry.infos(include_unavailable=True)
    assert {"rsi", "echo"} <= {info["plugin_id"] for info in infos}


def test_required_plugins_must_end_up_enabled() -> None:
    """A missing or disabled required plugin fails the build."""
    registry, report = discovery.build_registry(
        discovery.DiscoveryConfig(required=("ema", "echo")), [GOOD]
    )
    assert registry.is_available("echo")
    assert report.external == ("echo", "echo_zones")
    with pytest.raises(discovery.PluginLoadError, match="absent_plugin is not"):
        discovery.build_registry(
            discovery.DiscoveryConfig(required=("absent_plugin",)), [GOOD]
        )
    with pytest.raises(discovery.PluginLoadError, match="ema is DISABLED"):
        discovery.build_registry(
            discovery.DiscoveryConfig(disabled=("ema",), required=("ema",)),
            [],
        )
    with pytest.raises(discovery.PluginLoadError, match="ModuleNotFound"):
        discovery.build_registry(
            discovery.DiscoveryConfig(required=("absent",)), [MISSING_MODULE]
        )


def test_catalog_builds_the_registry_from_a_configuration() -> None:
    """``catalog.build_registry`` with no packages holds the built-ins."""
    registry, report = catalog.build_registry(
        discovery.DiscoveryConfig(packages="none", disabled=("atr",))
    )
    assert registry.ids(include_unavailable=True) == builtin.BUILTIN_IDS
    assert report.external == ()
    assert report.disabled == ("atr",)
    assert not registry.is_available("atr")


def test_environment_matches_the_platform_settings() -> None:
    """Discovery reads the same variables as ``platform.config``."""
    env = {
        "JOSEPH_INDICATOR_PLUGIN_PACKAGES": " first, second ",
        "JOSEPH_INDICATOR_PLUGINS_DISABLED": "rsi, ,atr",
        "JOSEPH_INDICATOR_PLUGINS_REQUIRED": "ema",
    }
    settings = config.Settings.from_env(env)
    assert discovery.DiscoveryConfig.from_env(env) == discovery.DiscoveryConfig(
        packages=settings.indicator_plugin_packages,
        disabled=settings.indicator_plugins_disabled,
        required=settings.indicator_plugins_required,
    )
    assert settings.indicator_plugins_disabled == ("rsi", "atr")
    defaults = config.Settings.from_env({})
    assert defaults.indicator_plugin_packages == "*"
    assert discovery.DiscoveryConfig.from_env({}) == discovery.DiscoveryConfig()
    assert defaults.indicator_plugins_disabled == ()
    assert defaults.indicator_plugins_required == ()


def _patch_installed(
    monkeypatch: pytest.MonkeyPatch, eps: list[importlib.metadata.EntryPoint]
) -> None:
    """Makes ``importlib.metadata`` report the given plugin entry points.

    Args:
        monkeypatch: Pytest monkeypatch.
        eps: Installed entry points of the plugin group.
    """

    def fake(*, group: str) -> list[importlib.metadata.EntryPoint]:
        """Returns ``eps`` for the plugin group.

        Args:
            group: Entry-point group.

        Returns:
            The entry points.
        """
        return list(eps) if group == _GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake)
    for name in (
        discovery.PACKAGES_ENV,
        discovery.DISABLED_ENV,
        discovery.REQUIRED_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_check_command_prints_ids_and_fails_on_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--check`` exits 1 on any failure; required plugins always count."""
    _patch_installed(monkeypatch, [GOOD])
    assert discovery.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert f"builtin: {', '.join(builtin.BUILTIN_IDS)}\n" in out
    assert "external: echo, echo_zones\n" in out
    _patch_installed(monkeypatch, [GOOD, MISSING_MODULE])
    assert discovery.main(["--check"]) == 1
    assert "failed: absent (unknown distribution)" in capsys.readouterr().out
    assert discovery.main([]) == 0
    capsys.readouterr()
    monkeypatch.setenv(discovery.REQUIRED_ENV, "absent_plugin")
    assert discovery.main([]) == 1
    assert "error: required indicator plugins" in capsys.readouterr().out
