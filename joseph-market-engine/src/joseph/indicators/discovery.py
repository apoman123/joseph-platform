# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Run-time discovery of indicator plugin packages.

An installed distribution contributes plugins through an entry point in the
``joseph.indicator_plugins`` group. The entry point names a zero-argument
callable returning a sequence of ``protocol.IndicatorPlugin`` objects, in
display order::

    [project.entry-points."joseph.indicator_plugins"]
    example = "example_plugins.plugins:default_plugins"

Load order is the registration order, the order of the terminal's Layers
menu and the tie-break when several plugins publish one event name: the
built-ins first (``builtin.builtin_plugins``), then the entry points sorted
by entry-point name and distribution name, each in its factory's order.

Each entry point is an isolation point: if loading it, calling its factory
or registering one of its plugins fails, the failure is logged, recorded in
the ``LoadReport`` and skipped, and every other plugin still loads. The
registry rejects a plugin whose id is taken (an external plugin can never
shadow a built-in), that declares an AOI kind another plugin declared,
whose schema version is unsupported or whose metadata breaks the rules of
``sdk.metadata``.

``DiscoveryConfig`` selects the entry points, disables plugins (they keep
their metadata, so strategies that use them get an actionable "unavailable"
error) and names plugins that must end up enabled. Check the installed
plugins with::

    python -m joseph.indicators.discovery --check

which prints the loaded ids and exits with status 1 on any failure.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import logging
import os
import sys
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence

from joseph.domain import errors
from joseph.indicators import builtin
from joseph.indicators.sdk import protocol
from joseph.indicators.sdk import registry as registry_lib

ENTRY_POINT_GROUP = "joseph.indicator_plugins"
"""Entry-point group of indicator plugin factories."""

ALL_PACKAGES = "*"
"""``DiscoveryConfig.packages`` value loading every entry point."""

NO_PACKAGES = "none"
"""``DiscoveryConfig.packages`` value loading no entry point."""

PACKAGES_ENV = "JOSEPH_INDICATOR_PLUGIN_PACKAGES"
"""Environment variable of ``DiscoveryConfig.packages``."""

DISABLED_ENV = "JOSEPH_INDICATOR_PLUGINS_DISABLED"
"""Environment variable of ``DiscoveryConfig.disabled`` (comma list)."""

REQUIRED_ENV = "JOSEPH_INDICATOR_PLUGINS_REQUIRED"
"""Environment variable of ``DiscoveryConfig.required`` (comma list)."""

DISABLED_REASON = "disabled by configuration"
"""Lifecycle reason of plugins listed in ``DiscoveryConfig.disabled``."""

_LOG = logging.getLogger(__name__)


def split_list(raw: str | None) -> tuple[str, ...]:
    """Splits a comma-separated setting into its non-empty items.

    Args:
        raw: Setting text, or ``None`` when unset.

    Returns:
        The stripped items, in order.
    """
    return tuple(
        part.strip() for part in (raw or "").split(",") if part.strip()
    )


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Which plugin packages load and which plugins are disabled or required.

    Attributes:
        packages: ``"*"`` loads every entry point, ``"none"`` none, and a
            comma-separated list only the entry points of those names.
        disabled: Plugin ids registered but disabled ("disabled by
            configuration"); built-ins may be disabled too.
        required: Plugin ids that must end up enabled, else
            ``build_registry`` raises ``PluginLoadError``.
    """

    packages: str = ALL_PACKAGES
    disabled: tuple[str, ...] = ()
    required: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DiscoveryConfig:
        """Reads the configuration from the three environment variables.

        Args:
            env: Mapping to read; defaults to ``os.environ``.

        Returns:
            The configuration (an unset or blank package list is ``"*"``).
        """
        source = os.environ if env is None else env
        return cls(
            packages=(source.get(PACKAGES_ENV) or "").strip() or ALL_PACKAGES,
            disabled=split_list(source.get(DISABLED_ENV)),
            required=split_list(source.get(REQUIRED_ENV)),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class LoadFailure:
    """An entry point or plugin that failed to load and was skipped.

    Attributes:
        entry_point: Entry-point name.
        distribution: Name of the distribution providing it, if known.
        plugin_id: Plugin that failed to register; ``None`` when the entry
            point itself failed.
        reason: What went wrong.
    """

    entry_point: str
    distribution: str | None
    plugin_id: str | None
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class LoadReport:
    """Outcome of loading the plugins.

    Attributes:
        builtin: Built-in plugin ids, in registration order.
        external: Plugin ids registered from entry points, in order.
        disabled: Plugin ids disabled by configuration.
        failures: Entry points and plugins that were skipped.
    """

    builtin: tuple[str, ...]
    external: tuple[str, ...]
    disabled: tuple[str, ...]
    failures: tuple[LoadFailure, ...]


class PluginLoadError(errors.UnsupportedError):
    """Required indicator plugins that are missing or not enabled."""


def _distribution(entry_point: importlib.metadata.EntryPoint) -> str | None:
    """Returns the name of the distribution providing an entry point.

    Args:
        entry_point: Entry point.

    Returns:
        Distribution name, or ``None`` when unknown.
    """
    dist = entry_point.dist
    return None if dist is None else dist.name


def select(
    candidates: Iterable[importlib.metadata.EntryPoint], packages: str
) -> list[importlib.metadata.EntryPoint]:
    """Filters and orders entry points by a ``packages`` setting.

    Args:
        candidates: Entry points of the group.
        packages: ``"*"``, ``"none"`` or comma-separated entry-point names.

    Returns:
        The selected entry points, sorted by name and distribution name.
    """
    wanted = packages.strip() or ALL_PACKAGES
    if wanted.lower() == NO_PACKAGES:
        return []
    names = None if wanted == ALL_PACKAGES else frozenset(split_list(wanted))
    chosen = [ep for ep in candidates if names is None or ep.name in names]
    return sorted(chosen, key=lambda ep: (ep.name, _distribution(ep) or ""))


def entry_points(packages: str) -> list[importlib.metadata.EntryPoint]:
    """Lists the installed plugin entry points a ``packages`` setting selects.

    Args:
        packages: ``"*"``, ``"none"`` or comma-separated entry-point names.

    Returns:
        Entry points sorted by name and distribution name.
    """
    return select(
        importlib.metadata.entry_points(group=ENTRY_POINT_GROUP), packages
    )


def _describe(err: BaseException) -> str:
    """Formats an exception for logs and reports.

    Args:
        err: The exception.

    Returns:
        ``"<type>: <message>"``.
    """
    return f"{type(err).__name__}: {err}"


def _record(
    failures: list[LoadFailure],
    entry_point: importlib.metadata.EntryPoint,
    plugin_id: str | None,
    err: Exception,
) -> None:
    """Logs a failure and adds it to the report.

    Args:
        failures: Failures so far, appended to.
        entry_point: Entry point being loaded.
        plugin_id: Plugin that failed, or ``None`` for the entry point.
        err: The exception.
    """
    dist = _distribution(entry_point)
    name = entry_point.name if plugin_id is None else plugin_id
    _LOG.error(
        "indicator plugin %s from %s failed: %s",
        name,
        dist or entry_point.value,
        _describe(err),
    )
    failures.append(
        LoadFailure(entry_point.name, dist, plugin_id, _describe(err))
    )


def _plugins(
    entry_point: importlib.metadata.EntryPoint,
) -> list[protocol.IndicatorPlugin]:
    """Loads an entry point and calls its factory.

    Args:
        entry_point: Entry point naming a zero-argument factory.

    Returns:
        The objects the factory returned (``register`` checks them).

    Raises:
        TypeError: If the entry point is not callable or its factory does
            not return a sequence.
    """
    factory = entry_point.load()
    if not callable(factory):
        raise TypeError(f"{entry_point.value} is not callable")
    plugins = factory()
    if not isinstance(plugins, Sequence) or isinstance(plugins, (str, bytes)):
        raise TypeError(f"{entry_point.value}() did not return a sequence")
    return list(plugins)


def _label(plugin: object) -> str:
    """Names a plugin object for reports, even a broken one.

    Args:
        plugin: Object returned by a plugin factory.

    Returns:
        Its ``plugin_id`` when that is a string, else its type name.
    """
    try:
        plugin_id = getattr(plugin, "plugin_id", None)
    except Exception:  # noqa: BLE001 - isolation point: a broken plugin object is reported, not raised
        plugin_id = None
    return plugin_id if isinstance(plugin_id, str) else type(plugin).__name__


def load_external(
    registry: registry_lib.PluginRegistry,
    config: DiscoveryConfig,
    eps: Iterable[importlib.metadata.EntryPoint] | None = None,
) -> LoadReport:
    """Registers the plugins of entry points, then applies ``disabled``.

    Every plugin already in ``registry`` counts as built-in.

    Args:
        registry: Registry holding the built-ins; plugins are added to it.
        config: Discovery configuration.
        eps: Candidate entry points (the installed ones of
            ``ENTRY_POINT_GROUP`` when ``None``); ``config.packages``
            selects and orders them.

    Returns:
        What was registered, disabled and skipped.
    """
    builtin_ids = registry.ids(include_unavailable=True)
    selected = (
        entry_points(config.packages)
        if eps is None
        else select(eps, config.packages)
    )
    external: list[str] = []
    failures: list[LoadFailure] = []
    for entry_point in selected:
        try:
            plugins = _plugins(entry_point)
        except Exception as err:  # noqa: BLE001 - isolation point: a broken plugin package must not stop the others
            _record(failures, entry_point, None, err)
            continue
        for plugin in plugins:
            label = _label(plugin)
            try:
                registry.register(plugin)
            except Exception as err:  # noqa: BLE001 - isolation point: one invalid plugin must not stop its siblings
                _record(failures, entry_point, label, err)
                continue
            external.append(label)
    disabled: list[str] = []
    for plugin_id in config.disabled:
        if registry.lifecycle(plugin_id) is None:
            _LOG.warning(
                "cannot disable indicator plugin %s: not installed", plugin_id
            )
            continue
        registry.disable(plugin_id, DISABLED_REASON)
        disabled.append(plugin_id)
    return LoadReport(
        builtin=builtin_ids,
        external=tuple(external),
        disabled=tuple(disabled),
        failures=tuple(failures),
    )


def require(
    registry: registry_lib.PluginRegistry,
    config: DiscoveryConfig,
    report: LoadReport,
) -> None:
    """Checks that every required plugin ended up enabled.

    Args:
        registry: Populated registry.
        config: Discovery configuration naming the required plugins.
        report: Report of the load, quoted for missing plugins.

    Raises:
        PluginLoadError: If a required plugin is not installed or not
            enabled.
    """
    problems: list[str] = []
    for plugin_id in config.required:
        lifecycle = registry.lifecycle(plugin_id)
        if lifecycle is registry_lib.Lifecycle.ENABLED:
            continue
        if lifecycle is None:
            causes = [
                f"{failure.entry_point}: {failure.reason}"
                for failure in report.failures
                if failure.plugin_id in (None, plugin_id)
            ]
            detail = f" ({'; '.join(causes)})" if causes else ""
            problems.append(f"{plugin_id} is not installed{detail}")
        else:
            problems.append(f"{plugin_id} is {lifecycle.value}")
    if problems:
        raise PluginLoadError(
            f"required indicator plugins are unavailable: {'; '.join(problems)}"
        )


def build_registry(
    config: DiscoveryConfig,
    eps: Iterable[importlib.metadata.EntryPoint] | None = None,
) -> tuple[registry_lib.PluginRegistry, LoadReport]:
    """Registers the built-ins and the configured plugin packages.

    Args:
        config: Discovery configuration.
        eps: Candidate entry points (the installed ones when ``None``).

    Returns:
        The registry and the load report.

    Raises:
        PluginLoadError: If a required plugin is not installed or not
            enabled.
    """
    registry = registry_lib.PluginRegistry()
    for plugin in builtin.builtin_plugins():
        registry.register(plugin)
    report = load_external(registry, config, eps)
    require(registry, config, report)
    return registry, report


def format_report(report: LoadReport) -> str:
    """Renders a load report for the check command.

    Args:
        report: Load report.

    Returns:
        One line per group and one per failure, newline-terminated.
    """
    lines = [
        f"builtin: {', '.join(report.builtin) or '-'}",
        f"external: {', '.join(report.external) or '-'}",
        f"disabled: {', '.join(report.disabled) or '-'}",
    ]
    lines.extend(
        f"failed: {failure.plugin_id or failure.entry_point} "
        f"({failure.distribution or 'unknown distribution'}): "
        f"{failure.reason}"
        for failure in report.failures
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Loads the plugins as the engine would and prints the outcome.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` when ``None``).

    Returns:
        0 on success; 1 when a required plugin is unavailable, or with
        ``--check`` when any entry point or plugin failed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m joseph.indicators.discovery",
        description="Lists the indicator plugins the market engine loads.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit with status 1 when any plugin package fails to load",
    )
    args = parser.parse_args(argv)
    config = DiscoveryConfig.from_env()
    registry = registry_lib.PluginRegistry()
    for plugin in builtin.builtin_plugins():
        registry.register(plugin)
    report = load_external(registry, config)
    sys.stdout.write(format_report(report))
    try:
        require(registry, config, report)
    except PluginLoadError as err:
        sys.stdout.write(f"error: {err.message}\n")
        return 1
    return 1 if args.check and report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
