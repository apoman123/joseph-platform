# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""The platform's default scripts.

Every default indicator, strategy, filter and alert is one module here that
defines exactly one concrete subclass of a ``joseph_script`` role. A module
imports only ``joseph_script`` and the Python standard library, so its public
source is also valid tenant source that can be copied into the editor and run
in the isolated runner unchanged.

Modules are listed per role in catalog order; ``classes`` imports them.
"""

from __future__ import annotations

import importlib
import inspect

import joseph_script

MODULES: dict[str, tuple[str, ...]] = {
    "indicators": ("sma", "ema"),
    "strategies": (
        "ema_trend_long",
        "rsi_reversion_short",
        "pivot_bounce_long",
    ),
    "filters": (),
    "alerts": (),
}
"""Built-in module names per role package, in catalog order."""


def script_class(module_name: str) -> type:
    """Imports one built-in module and returns its single script class.

    Args:
        module_name: Dotted module path below this package, such as
            "indicators.ema".

    Returns:
        The module's concrete ``joseph_script`` subclass.

    Raises:
        TypeError: If the module defines zero or several script classes.
    """
    module = importlib.import_module(f"{__name__}.{module_name}")
    found = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and joseph_script.role_of(value) is not None
        and not inspect.isabstract(value)
    ]
    if len(found) != 1:
        raise TypeError(f"{module_name} must define exactly one script class")
    return found[0]


def classes() -> list[type]:
    """Imports every built-in script class in catalog order.

    Returns:
        Built-in classes, indicators first, then strategies, filters, alerts.
    """
    found: list[type] = []
    for package, names in MODULES.items():
        found.extend(script_class(f"{package}.{name}") for name in names)
    return found
