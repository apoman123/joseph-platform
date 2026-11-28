# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Catalog of built-in and installed plugin scripts.

Every default indicator, strategy, filter and alert of the platform is a
concrete ``joseph_script`` subclass in ``joseph.scripts.builtin``. Installed
distributions add more through an entry point in the ``joseph.scripts``
group naming a zero-argument callable that returns a sequence of such
classes (or naming one class directly)::

    [project.entry-points."joseph.scripts"]
    example = "example_scripts:scripts"

Catalog ids are ``builtin:<ID>`` and ``plugin:<entry point>:<ID>``, so a plugin
can never shadow a built-in or a tenant script (tenant slugs contain no
colon). Built-in source is public and can be copied into the editor; plugin
source is protected. Built-in and plugin classes are trusted operator code:
the host runs them in process, while tenant source runs only in the isolated
runner.

Each entry point is an isolation point: a failure to import it, call its
factory or validate one of its classes is logged, recorded in the
``LoadReport`` and skipped. ``CatalogConfig`` selects the entry points,
disables ids and names ids that must load. Check an installation with::

    python -m joseph.scripts.catalog --check
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import inspect
import logging
import os
import re
import sys
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import pydantic

import joseph_script
from joseph.domain import errors
from joseph.scripts import models

ENTRY_POINT_GROUP = "joseph.scripts"
"""Entry-point group of plugin script factories."""

ALL_PACKAGES = "*"
"""``CatalogConfig.packages`` value loading every entry point."""

NO_PACKAGES = "none"
"""``CatalogConfig.packages`` value loading no entry point."""

PACKAGES_ENV = "JOSEPH_SCRIPT_PLUGIN_PACKAGES"
"""Environment variable of ``CatalogConfig.packages``."""

DISABLED_ENV = "JOSEPH_SCRIPT_PLUGINS_DISABLED"
"""Environment variable of ``CatalogConfig.disabled`` (comma list)."""

REQUIRED_ENV = "JOSEPH_SCRIPT_PLUGINS_REQUIRED"
"""Environment variable of ``CatalogConfig.required`` (comma list)."""

CATALOG_ID = re.compile(
    r"^(?:builtin|plugin:[a-z0-9_.-]{1,64}):[a-z][a-z0-9_-]{0,63}$"
)
"""Built-in or plugin catalog id."""

PERIOD_TIMEFRAMES = frozenset({"1d", "1w", "1M"})
"""Calendar periods a script may declare in ``PERIODS``."""

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTRY = re.compile(r"^[a-z0-9_.-]{1,64}$")
_LOG = logging.getLogger(__name__)


def split_list(raw: str | None) -> tuple[str, ...]:
    """Splits a comma-separated setting into its non-empty items.

    Args:
        raw: Setting text, or None when unset.

    Returns:
        The stripped items, in order.
    """
    return tuple(
        part.strip() for part in (raw or "").split(",") if part.strip()
    )


def is_catalog_id(script_id: str) -> bool:
    """Tells a built-in or plugin id from a tenant script slug.

    Args:
        script_id: Any script identifier.

    Returns:
        True for ``builtin:...`` and ``plugin:...:...`` ids.
    """
    return bool(CATALOG_ID.fullmatch(script_id))


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogConfig:
    """Which plugin packages load and which catalog ids are off or required.

    Attributes:
        packages: "*" loads every entry point, "none" none, and a
            comma-separated list only the entry points of those names.
        disabled: Catalog ids listed but not runnable; built-ins too.
        required: Catalog ids that must end up enabled, else ``build``
            raises ``errors.ValidationFailedError``.
    """

    packages: str = ALL_PACKAGES
    disabled: tuple[str, ...] = ()
    required: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CatalogConfig:
        """Reads the configuration from the three environment variables.

        Args:
            env: Mapping to read; defaults to ``os.environ``.

        Returns:
            The configuration (an unset or blank package list is "*").
        """
        source = os.environ if env is None else env
        return cls(
            packages=(source.get(PACKAGES_ENV) or "").strip() or ALL_PACKAGES,
            disabled=split_list(source.get(DISABLED_ENV)),
            required=split_list(source.get(REQUIRED_ENV)),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One runnable built-in or plugin script class.

    Attributes:
        script_id: ``builtin:<ID>`` or ``plugin:<entry point>:<ID>``.
        kind: indicator, strategy, library, filter or alert.
        cls: The trusted concrete script class.
        origin: "builtin" or "plugin".
        package: Distribution that provides a plugin, otherwise None.
        package_version: That distribution's version, otherwise None.
        title: Display name.
        short_title: Compact legend name.
        description: Catalog description.
        category: Optional catalog section.
        overlay: Whether plots share the price pane.
        inputs: Declared inputs as JSON objects, in display order.
        requests: Declared additional series as JSON objects.
        periods: Declared calendar periods read with ``ctx.period``.
        source: Public module source for built-ins; None (protected) for
            plugins.
        source_hash: SHA-256 identifying the exact installed code.
        enabled: False when disabled by configuration.
        metrics: Market data a filter needs beyond the snapshot
            (``joseph_script.FILTER_METRICS`` names); empty otherwise.
    """

    script_id: str
    kind: str
    cls: type
    origin: str
    package: str | None
    package_version: str | None
    title: str
    short_title: str
    description: str
    category: str
    overlay: bool
    inputs: dict[str, dict[str, Any]]
    requests: dict[str, dict[str, Any]]
    source: str | None
    source_hash: str
    enabled: bool = True
    periods: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()

    @property
    def defaults(self) -> dict[str, Any]:
        """Default value of every declared input.

        Returns:
            Input name to default JSON value.
        """
        return {name: spec["default"] for name, spec in self.inputs.items()}

    def describe(self) -> dict[str, Any]:
        """Public JSON description without the class object.

        Returns:
            Catalog fields; ``source_available`` instead of the source text.
        """
        return {
            "script_id": self.script_id,
            "kind": self.kind,
            "origin": self.origin,
            "package": self.package,
            "package_version": self.package_version,
            "title": self.title,
            "short_title": self.short_title,
            "description": self.description,
            "category": self.category,
            "overlay": self.overlay,
            "inputs": self.inputs,
            "requests": self.requests,
            "source_available": self.source is not None,
            "source_hash": self.source_hash,
            "enabled": self.enabled,
            "metrics": list(self.metrics),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class LoadFailure:
    """An entry point or class that failed to load and was skipped.

    Attributes:
        entry_point: Entry-point name, or "builtin".
        distribution: Providing distribution, if known.
        reason: What went wrong.
    """

    entry_point: str
    distribution: str | None
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class LoadReport:
    """Outcome of building the catalog.

    Attributes:
        loaded: Catalog ids that loaded, in catalog order.
        failures: Entry points or classes that were skipped.
    """

    loaded: tuple[str, ...]
    failures: tuple[LoadFailure, ...]


def _module_source(cls: type) -> str:
    """Reads the source text of the module defining a built-in class.

    Args:
        cls: Built-in script class.

    Returns:
        The module's source text.

    Raises:
        errors.ValidationFailedError: If the source cannot be read.
    """
    module = sys.modules.get(cls.__module__)
    try:
        return inspect.getsource(module) if module else inspect.getsource(cls)
    except (OSError, TypeError):
        raise errors.ValidationFailedError(
            f"built-in script {cls.__qualname__} has no readable source"
        ) from None


def _check_inputs(cls: type) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validates and serializes a class's declared inputs and requests.

    Args:
        cls: Script class.

    Returns:
        Inputs and requests as JSON mappings.

    Raises:
        errors.ValidationFailedError: For malformed declarations.
    """
    inputs: dict[str, Any] = {}
    for name, spec in dict(getattr(cls, "INPUTS", {})).items():
        if not isinstance(spec, joseph_script.Input) or not (
            isinstance(name, str) and name.isidentifier()
        ):
            raise errors.ValidationFailedError(
                f"{cls.__qualname__}.INPUTS needs identifier keys and Input"
                " values"
            )
        inputs[name] = spec.to_json()
    requests: dict[str, Any] = {}
    for alias, spec in dict(getattr(cls, "REQUESTS", {})).items():
        if not isinstance(spec, joseph_script.Request) or not (
            isinstance(alias, str) and alias.isidentifier()
        ):
            raise errors.ValidationFailedError(
                f"{cls.__qualname__}.REQUESTS needs identifier keys and"
                " Request values"
            )
        requests[alias] = spec.to_json()
        try:
            models.SeriesRequest.model_validate(requests[alias], strict=False)
        except pydantic.ValidationError:
            raise errors.ValidationFailedError(
                f"{cls.__qualname__}.REQUESTS[{alias!r}] is invalid"
            ) from None
    if len(requests) > 4:
        raise errors.ValidationFailedError(
            f"{cls.__qualname__} declares more than four requests"
        )
    return inputs, requests


def _check_periods(cls: type) -> tuple[str, ...]:
    """Validates a class's declared calendar periods.

    Args:
        cls: Script class.

    Returns:
        The declared periods.

    Raises:
        errors.ValidationFailedError: For a period other than 1d, 1w or 1M.
    """
    periods = tuple(getattr(cls, "PERIODS", ()))
    if any(period not in PERIOD_TIMEFRAMES for period in periods):
        raise errors.ValidationFailedError(
            f"{cls.__qualname__}.PERIODS accepts only 1d, 1w and 1M"
        )
    return periods


def make_entry(
    cls: type,
    *,
    origin: str,
    entry_point: str | None = None,
    package: str | None = None,
    package_version: str | None = None,
) -> CatalogEntry:
    """Validates one trusted script class and describes it.

    Args:
        cls: Concrete subclass of exactly one ``joseph_script`` role.
        origin: "builtin" or "plugin".
        entry_point: Entry-point name for plugins.
        package: Providing distribution for plugins.
        package_version: That distribution's version.

    Returns:
        The catalog entry.

    Raises:
        errors.ValidationFailedError: If the class is not a concrete script
            of one role or its declarations are invalid.
    """
    kind = joseph_script.role_of(cls)
    if kind is None or inspect.isabstract(cls):
        raise errors.ValidationFailedError(
            f"{getattr(cls, '__qualname__', cls)!r} is not a concrete"
            " subclass of exactly one joseph_script role"
        )
    slug = str(getattr(cls, "ID", "") or "")
    if not _SLUG.fullmatch(slug):
        raise errors.ValidationFailedError(
            f"{cls.__qualname__}.ID must be a lowercase slug"
        )
    inputs, requests = _check_inputs(cls)
    metrics = tuple(getattr(cls, "METRICS", ()) or ())
    if (kind != "filter" and metrics) or not set(metrics) <= set(
        joseph_script.FILTER_METRICS
    ):
        raise errors.ValidationFailedError(
            f"{cls.__qualname__}.METRICS must name filter metrics: "
            + ", ".join(joseph_script.FILTER_METRICS)
        )
    if origin == "builtin":
        script_id = f"builtin:{slug}"
        text = _module_source(cls)
        source: str | None = text
        digest = hashlib.sha256(text.encode()).hexdigest()
    else:
        if entry_point is None or not _ENTRY.fullmatch(entry_point):
            raise errors.ValidationFailedError(
                "plugin entry-point names must be lowercase slugs"
            )
        script_id = f"plugin:{entry_point}:{slug}"
        source = None
        digest = hashlib.sha256(
            f"{package}=={package_version}:{cls.__module__}."
            f"{cls.__qualname__}".encode()
        ).hexdigest()
    title = str(getattr(cls, "TITLE", "") or cls.__name__)
    return CatalogEntry(
        script_id=script_id,
        kind=kind,
        cls=cls,
        origin=origin,
        package=package,
        package_version=package_version,
        title=title[:120],
        short_title=str(getattr(cls, "SHORT_TITLE", "") or title)[:40],
        description=str(getattr(cls, "DESCRIPTION", ""))[:500],
        category=str(getattr(cls, "CATEGORY", ""))[:60],
        overlay=bool(getattr(cls, "OVERLAY", True)),
        inputs=inputs,
        requests=requests,
        source=source,
        source_hash=digest,
        periods=_check_periods(cls),
        metrics=metrics,
    )


def _classes(loaded: object) -> list[type]:
    """Turns an entry point's object into script classes.

    Args:
        loaded: A class, or a zero-argument factory returning classes.

    Returns:
        The classes in factory order.

    Raises:
        TypeError: If the object yields something other than classes.
    """
    if isinstance(loaded, type):
        return [loaded]
    if not callable(loaded):
        raise TypeError("entry point is neither a class nor a factory")
    produced = loaded()
    if isinstance(produced, type) or not isinstance(produced, Iterable):
        raise TypeError("factory must return a sequence of classes")
    classes = list(produced)
    if not all(isinstance(item, type) for item in classes):
        raise TypeError("factory must return a sequence of classes")
    return classes


def entry_points(packages: str) -> list[importlib.metadata.EntryPoint]:
    """Lists the selected entry points of the script group, sorted by name.

    Args:
        packages: "*", "none" or a comma-separated list of names.

    Returns:
        Selected entry points ordered by name and distribution.
    """
    if packages.strip().lower() == NO_PACKAGES:
        return []
    found = list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    wanted = set(split_list(packages))
    if packages.strip() != ALL_PACKAGES:
        found = [point for point in found if point.name in wanted]
    return sorted(
        found,
        key=lambda point: (
            point.name,
            point.dist.name if point.dist is not None else "",
        ),
    )


class ScriptCatalog:
    """Immutable lookup of the installed built-in and plugin scripts.

    Attributes:
        report: What loaded and what was skipped.
    """

    def __init__(
        self, entries: Sequence[CatalogEntry], report: LoadReport
    ) -> None:
        """Indexes validated entries.

        Args:
            entries: Entries in catalog order; ids must be unique.
            report: Load outcome.

        Raises:
            errors.ValidationFailedError: If two entries share an id.
        """
        self._entries: dict[str, CatalogEntry] = {}
        for entry in entries:
            if entry.script_id in self._entries:
                raise errors.ValidationFailedError(
                    f"duplicate script id {entry.script_id}"
                )
            self._entries[entry.script_id] = entry
        self.report = report

    def entries(self, kind: str | None = None) -> list[CatalogEntry]:
        """Lists entries in catalog order.

        Args:
            kind: Optional role filter.

        Returns:
            Matching entries, enabled or not.
        """
        return [
            entry
            for entry in self._entries.values()
            if kind is None or entry.kind == kind
        ]

    def get(self, script_id: str) -> CatalogEntry:
        """Finds an enabled entry.

        Args:
            script_id: Catalog id.

        Returns:
            The entry.

        Raises:
            errors.NotFoundError: If no such script is installed.
            errors.UnsupportedError: If it is disabled by configuration.
        """
        entry = self._entries.get(script_id)
        if entry is None:
            raise errors.NotFoundError(f"script {script_id} is not installed")
        if not entry.enabled:
            raise errors.UnsupportedError(
                f"script {script_id} is disabled by configuration"
            )
        return entry

    def __contains__(self, script_id: object) -> bool:
        """Tells whether an id is installed (enabled or not).

        Args:
            script_id: Candidate id.

        Returns:
            True when installed.
        """
        return script_id in self._entries


def build(
    config: CatalogConfig | None = None,
    *,
    builtins: Sequence[type] | None = None,
    points: Sequence[importlib.metadata.EntryPoint] | None = None,
) -> ScriptCatalog:
    """Builds the catalog: built-ins first, then plugins by entry point.

    Args:
        config: Package selection, disabled and required ids.
        builtins: Built-in classes; defaults to ``joseph.scripts.builtin``.
        points: Entry points; defaults to the installed selection.

    Returns:
        The catalog.

    Raises:
        errors.ValidationFailedError: If a built-in is invalid or a required
            id is missing or disabled.
    """
    settings = config or CatalogConfig()
    if builtins is None:
        from joseph.scripts import builtin  # noqa: PLC0415 - Defer built-in imports to catalog construction.

        builtins = builtin.classes()
    entries: list[CatalogEntry] = [
        make_entry(cls, origin="builtin") for cls in builtins
    ]
    taken = {entry.script_id for entry in entries}
    failures: list[LoadFailure] = []
    for point in entry_points(settings.packages) if points is None else points:
        distribution = point.dist.name if point.dist is not None else None
        version = point.dist.version if point.dist is not None else None
        try:
            classes = _classes(point.load())
            batch = [
                make_entry(
                    cls,
                    origin="plugin",
                    entry_point=point.name,
                    package=distribution,
                    package_version=version,
                )
                for cls in classes
            ]
            ids = [entry.script_id for entry in batch]
            if len(set(ids)) != len(ids) or taken & set(ids):
                raise errors.ValidationFailedError("duplicate script ids")
        except Exception as exc:  # noqa: BLE001 - One plugin package must not stop the engine.
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            _LOG.warning(
                "script plugin %s skipped: %s",
                point.name,
                reason,
            )
            failures.append(LoadFailure(point.name, distribution, reason))
            continue
        entries.extend(batch)
        taken.update(ids)
    disabled = set(settings.disabled)
    entries = [
        dataclasses.replace(entry, enabled=entry.script_id not in disabled)
        for entry in entries
    ]
    enabled = {entry.script_id for entry in entries if entry.enabled}
    missing = [item for item in settings.required if item not in enabled]
    if missing:
        raise errors.ValidationFailedError(
            "required scripts did not load: " + ", ".join(missing)
        )
    report = LoadReport(
        tuple(entry.script_id for entry in entries), tuple(failures)
    )
    return ScriptCatalog(entries, report)


def format_report(catalog: ScriptCatalog) -> str:
    """Formats a catalog for operators.

    Args:
        catalog: Built catalog.

    Returns:
        One line per script and per failure.
    """
    lines = [
        f"{entry.script_id}\t{entry.kind}\t"
        f"{'enabled' if entry.enabled else 'disabled'}"
        for entry in catalog.entries()
    ]
    lines.extend(
        f"FAILED {failure.entry_point} ({failure.distribution}): "
        f"{failure.reason}"
        for failure in catalog.report.failures
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Prints the installed catalog; exit status 1 on any failure.

    Args:
        argv: Command-line arguments (``--check``).

    Returns:
        Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.parse_args(argv)
    try:
        catalog = build(CatalogConfig.from_env())
    except errors.JosephError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    sys.stdout.write(format_report(catalog) + "\n")
    return 1 if catalog.report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
