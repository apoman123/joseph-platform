"""JSON-compatible checkpoint encoding for immutable plugin state.

Plugin states are frozen dataclasses built from tuples, decimals, enums and
domain value objects. ``dump`` turns them into plain JSON data with type
tags; ``load`` rebuilds identical objects given the set of dataclass and enum
types that may appear. ``load(dump(x)) == x`` holds for every supported
state, which is what makes checkpoint/restore replay deterministic.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from joseph.domain import instruments

_DC = "__dc__"
_DEC = "__dec__"
_ENUM = "__enum__"
_INST = "__instrument__"
_TUPLE = "__tuple__"
_MAP = "__map__"


def dump(value: object) -> object:  # noqa: PLR0911 - type dispatch.
    """Encodes a state value into JSON-compatible data.

    Args:
        value: Dataclass, tuple, mapping, decimal, enum or JSON scalar.

    Returns:
        JSON-compatible representation with type tags.

    Raises:
        TypeError: If an unsupported type is encountered.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, decimal.Decimal):
        return {_DEC: str(value)}
    if isinstance(value, enum.Enum):
        return {_ENUM: type(value).__name__, "v": value.value}
    if isinstance(value, instruments.InstrumentId):
        return {_INST: str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {
            field.name: dump(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
        return {_DC: type(value).__name__, "f": fields}
    if isinstance(value, tuple):
        return {_TUPLE: [dump(item) for item in value]}
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, Mapping):
        return {_MAP: [[dump(k), dump(v)] for k, v in value.items()]}
    raise TypeError(f"cannot checkpoint value of type {type(value)!r}")


def load(data: object, known_types: Iterable[type]) -> Any:
    """Decodes data produced by ``dump``.

    Args:
        data: JSON-compatible representation.
        known_types: Dataclass and enum classes that may appear.

    Returns:
        The reconstructed value.

    Raises:
        TypeError: If a tag references an unknown type.
    """
    registry = {cls.__name__: cls for cls in known_types}
    return _load(data, registry)


def _load(  # noqa: PLR0911 - tag dispatch.
    data: object, registry: Mapping[str, type]
) -> Any:
    """Recursive worker for ``load``.

    Args:
        data: Encoded value.
        registry: Class name to class mapping.

    Returns:
        Decoded value.

    Raises:
        TypeError: On unknown tags.
    """
    if isinstance(data, list):
        return [_load(item, registry) for item in data]
    if not isinstance(data, dict):
        return data
    if _DEC in data:
        return decimal.Decimal(data[_DEC])
    if _INST in data:
        return instruments.InstrumentId.parse(data[_INST])
    if _TUPLE in data:
        return tuple(_load(item, registry) for item in data[_TUPLE])
    if _MAP in data:
        return {_load(k, registry): _load(v, registry) for k, v in data[_MAP]}
    if _ENUM in data:
        cls = _lookup(registry, data[_ENUM])
        return cls(data["v"])
    if _DC in data:
        cls = _lookup(registry, data[_DC])
        kwargs = {name: _load(raw, registry) for name, raw in data["f"].items()}
        return cls(**kwargs)
    return {key: _load(value, registry) for key, value in data.items()}


def _lookup(registry: Mapping[str, type], name: str) -> type:
    """Finds a registered class by name.

    Args:
        registry: Class name to class mapping.
        name: Encoded class name.

    Returns:
        The class.

    Raises:
        TypeError: If the class is not registered.
    """
    cls = registry.get(name)
    if cls is None:
        raise TypeError(f"unknown checkpoint type {name!r}")
    return cls
