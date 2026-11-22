"""Checkpoint codec for external indicator objects.

The ported indicators are ordinary mutable Python objects. Checkpoints use
``pickle`` because it round-trips them exactly (floats, deques, slotted
dataclasses) and quickly. Loading is restricted to an allowlist of modules:
an unpickler that can only build *classes defined in* ``smc_indicators`` or
this package, plus a few inert standard-library types, cannot reach
functions, modules or builtins such as ``getattr``, so a tampered checkpoint
fails instead of running code. ``ZoneInfo`` objects are reduced to their key
so they never need ``getattr`` either.
"""

from __future__ import annotations

import base64
import io
import pickle
import zoneinfo

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "smc_indicators.",
    "joseph.indicators.packaged.",
)
"""Module prefixes whose classes may be reconstructed."""

_ALLOWED_GLOBALS: frozenset[tuple[str, str]] = frozenset(
    {
        ("builtins", "set"),
        ("builtins", "frozenset"),
        ("builtins", "dict"),
        ("builtins", "list"),
        ("builtins", "tuple"),
        ("builtins", "float"),
        ("builtins", "int"),
        ("builtins", "str"),
        ("builtins", "bool"),
        ("builtins", "bytes"),
        ("collections", "deque"),
        ("collections", "OrderedDict"),
        ("zoneinfo", "ZoneInfo"),
        ("decimal", "Decimal"),
        ("datetime", "date"),
        ("datetime", "datetime"),
        ("datetime", "time"),
        ("datetime", "timedelta"),
        ("datetime", "timezone"),
    }
)
"""Exact standard-library globals the payloads may reference."""


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that only resolves allowlisted globals."""

    def find_class(self, module: str, name: str) -> type:
        """Resolves a global if it is allowlisted.

        Args:
            module: Module name recorded in the pickle.
            name: Attribute name.

        Returns:
            The class.

        Raises:
            pickle.UnpicklingError: If the global is not allowed.
        """
        if (module, name) in _ALLOWED_GLOBALS:
            exact: type = super().find_class(module, name)
            return exact
        if module.startswith(_ALLOWED_PREFIXES) and "." not in name:
            found = super().find_class(module, name)
            if isinstance(found, type) and found.__module__ == module:
                return found
        raise pickle.UnpicklingError(f"forbidden global {module}.{name}")


class _Pickler(pickle.Pickler):
    """Pickler reducing ``ZoneInfo`` to a constructor call by key."""

    def reducer_override(self, obj: object) -> object:
        """Reduces time zones without classmethod lookups.

        Args:
            obj: Object being pickled.

        Returns:
            A reduce tuple for ``ZoneInfo``; ``NotImplemented`` otherwise.
        """
        if isinstance(obj, zoneinfo.ZoneInfo):
            return zoneinfo.ZoneInfo, (obj.key,)
        return NotImplemented


def dumps(value: object) -> bytes:
    """Serializes an indicator payload.

    Args:
        value: Payload object.

    Returns:
        Pickle bytes.
    """
    buffer = io.BytesIO()
    _Pickler(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(value)
    return buffer.getvalue()


def loads(blob: bytes) -> object:
    """Deserializes a payload produced by ``dumps``.

    Args:
        blob: Pickle bytes.

    Returns:
        The payload object.

    Raises:
        pickle.UnpicklingError: If the data references a forbidden global.
    """
    return _RestrictedUnpickler(io.BytesIO(blob)).load()


def encode(blob: bytes) -> str:
    """Encodes payload bytes as JSON-safe text.

    Args:
        blob: Payload bytes.

    Returns:
        Base64 text.
    """
    return base64.b64encode(blob).decode("ascii")


def decode(text: str) -> bytes:
    """Decodes text produced by ``encode``.

    Args:
        text: Base64 text.

    Returns:
        Payload bytes.
    """
    return base64.b64decode(text.encode("ascii"), validate=True)
