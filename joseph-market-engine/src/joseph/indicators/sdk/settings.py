# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Typed plugin settings with JSON Schema generation and validation.

Plugins declare their settings as ``SettingSpec`` tuples. The same
declaration produces the ``input_schema`` published to clients and validates
user-supplied overrides, so the schema can never drift from the code.
Decimal settings travel as strings (contract ``DecimalString``).
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TypeAlias

from joseph.domain import decimals
from joseph.domain import errors

SettingValue: TypeAlias = int | bool | str | decimal.Decimal
"""Resolved setting value type."""


class SettingKind(enum.StrEnum):
    """Supported setting value kinds."""

    INT = "int"
    DECIMAL = "decimal"
    BOOL = "bool"
    ENUM = "enum"


@dataclasses.dataclass(frozen=True, slots=True)
class SettingSpec:
    """Declaration of one plugin setting.

    Attributes:
        name: Setting key.
        kind: Value kind.
        default: Default value (``Decimal`` for decimal settings).
        description: Human-readable meaning.
        minimum: Optional inclusive lower bound (int/decimal kinds).
        maximum: Optional inclusive upper bound (int/decimal kinds).
        choices: Allowed values for ``ENUM`` settings.
    """

    name: str
    kind: SettingKind
    default: SettingValue
    description: str
    minimum: int | decimal.Decimal | None = None
    maximum: int | decimal.Decimal | None = None
    choices: tuple[str, ...] = ()

    def json_schema(self) -> dict[str, object]:
        """Builds the JSON Schema fragment describing this setting.

        Returns:
            JSON Schema object for the property.
        """
        schema: dict[str, object] = {"description": self.description}
        if self.kind is SettingKind.INT:
            schema["type"] = "integer"
            schema["default"] = self.default
            if self.minimum is not None:
                schema["minimum"] = int(self.minimum)
            if self.maximum is not None:
                schema["maximum"] = int(self.maximum)
        elif self.kind is SettingKind.DECIMAL:
            schema["type"] = "string"
            schema["pattern"] = r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"
            if not isinstance(self.default, decimal.Decimal):
                raise AssertionError(
                    f"decimal setting {self.name!r} needs a Decimal default"
                )
            schema["default"] = decimals.format_decimal(self.default)
            # Bounds travel as decimal strings, like the value they bound.
            if self.minimum is not None:
                schema["minimum"] = decimals.format_decimal(
                    decimal.Decimal(self.minimum)
                )
            if self.maximum is not None:
                schema["maximum"] = decimals.format_decimal(
                    decimal.Decimal(self.maximum)
                )
        elif self.kind is SettingKind.BOOL:
            schema["type"] = "boolean"
            schema["default"] = self.default
        else:
            schema["type"] = "string"
            schema["enum"] = list(self.choices)
            schema["default"] = self.default
        return schema

    def coerce(self, raw: object) -> SettingValue:
        """Validates and converts a user-supplied value.

        Args:
            raw: Value from configuration or an API request.

        Returns:
            The typed value.

        Raises:
            ValidationFailedError: If the value has the wrong type or is out
                of range.
        """
        value: SettingValue
        if self.kind is SettingKind.INT:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise errors.ValidationFailedError(
                    f"setting {self.name!r} must be an integer"
                )
            value = raw
        elif self.kind is SettingKind.DECIMAL:
            if isinstance(raw, bool) or not isinstance(
                raw, (str, int, decimal.Decimal)
            ):
                raise errors.ValidationFailedError(
                    f"setting {self.name!r} must be a decimal string"
                )
            try:
                value = decimals.to_decimal(raw)
            except ValueError as err:
                raise errors.ValidationFailedError(
                    f"setting {self.name!r} is not a decimal"
                ) from err
        elif self.kind is SettingKind.BOOL:
            if not isinstance(raw, bool):
                raise errors.ValidationFailedError(
                    f"setting {self.name!r} must be a boolean"
                )
            value = raw
        else:
            if raw not in self.choices:
                raise errors.ValidationFailedError(
                    f"setting {self.name!r} must be one of {self.choices}"
                )
            if not isinstance(raw, str):
                raise AssertionError(f"choices of {self.name!r} are strings")
            value = raw
        self._check_range(value)
        return value

    def _check_range(self, value: SettingValue) -> None:
        """Enforces ``minimum``/``maximum`` bounds.

        Args:
            value: Typed value.

        Raises:
            ValidationFailedError: If out of range.
        """
        if isinstance(value, (bool, str)):
            return
        if self.minimum is not None and value < self.minimum:
            raise errors.ValidationFailedError(
                f"setting {self.name!r} must be >= {self.minimum}"
            )
        if self.maximum is not None and value > self.maximum:
            raise errors.ValidationFailedError(
                f"setting {self.name!r} must be <= {self.maximum}"
            )


def json_schema(specs: Sequence[SettingSpec]) -> dict[str, object]:
    """Builds the JSON Schema object for a plugin's settings.

    Args:
        specs: Setting declarations.

    Returns:
        JSON Schema (draft 2020-12) with ``additionalProperties: false``.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {spec.name: spec.json_schema() for spec in specs},
    }


def defaults(specs: Sequence[SettingSpec]) -> dict[str, SettingValue]:
    """Returns the default value of every setting.

    Args:
        specs: Setting declarations.

    Returns:
        Mapping of setting name to default.
    """
    return {spec.name: spec.default for spec in specs}


def resolve(
    specs: Sequence[SettingSpec], overrides: Mapping[str, object] | None
) -> dict[str, SettingValue]:
    """Merges overrides onto defaults with validation.

    Args:
        specs: Setting declarations.
        overrides: User-supplied values (may be ``None``).

    Returns:
        Fully resolved settings.

    Raises:
        ValidationFailedError: On unknown keys or invalid values.
    """
    by_name = {spec.name: spec for spec in specs}
    resolved = defaults(specs)
    for key, raw in (overrides or {}).items():
        spec = by_name.get(key)
        if spec is None:
            raise errors.ValidationFailedError(f"unknown setting {key!r}")
        resolved[key] = spec.coerce(raw)
    return resolved


def get_int(settings: Mapping[str, SettingValue], name: str) -> int:
    """Reads an integer setting.

    Args:
        settings: Resolved settings.
        name: Setting key.

    Returns:
        The integer value.

    Raises:
        TypeError: If the setting is not an integer.
    """
    value = settings[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"setting {name!r} is not an int")
    return value


def get_decimal(
    settings: Mapping[str, SettingValue], name: str
) -> decimal.Decimal:
    """Reads a decimal setting.

    Args:
        settings: Resolved settings.
        name: Setting key.

    Returns:
        The decimal value.

    Raises:
        TypeError: If the setting is not a decimal.
    """
    value = settings[name]
    if not isinstance(value, decimal.Decimal):
        raise TypeError(f"setting {name!r} is not a Decimal")
    return value


def get_str(settings: Mapping[str, SettingValue], name: str) -> str:
    """Reads an enum/string setting.

    Args:
        settings: Resolved settings.
        name: Setting key.

    Returns:
        The string value.

    Raises:
        TypeError: If the setting is not a string.
    """
    value = settings[name]
    if not isinstance(value, str):
        raise TypeError(f"setting {name!r} is not a str")
    return value


def get_bool(settings: Mapping[str, SettingValue], name: str) -> bool:
    """Reads a boolean setting.

    Args:
        settings: Resolved settings.
        name: Setting key.

    Returns:
        The boolean value.

    Raises:
        TypeError: If the setting is not a bool.
    """
    value = settings[name]
    if not isinstance(value, bool):
        raise TypeError(f"setting {name!r} is not a bool")
    return value
