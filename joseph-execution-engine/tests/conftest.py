"""Shared pytest fixtures for the execution engine test suite."""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable

import jsonschema
import pytest
import referencing

_DEFAULT_CONTRACTS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "schemas"
)
_BASE_URI = "https://schemas.joseph.local/v1/"


def _contracts_dir() -> pathlib.Path:
    """Returns the directory holding the canonical JSON Schemas.

    Returns:
        ``$JOSEPH_CONTRACTS_DIR`` or the sibling public terminal package.
    """
    return pathlib.Path(
        os.environ.get("JOSEPH_CONTRACTS_DIR", str(_DEFAULT_CONTRACTS))
    )


def _build_registry() -> referencing.Registry:
    """Loads every schema file into a ``referencing`` registry.

    Returns:
        Registry keyed by each schema's ``$id``.
    """
    resources = []
    for path in sorted(_contracts_dir().glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append(
            (contents["$id"], referencing.Resource.from_contents(contents))
        )
    return referencing.Registry().with_resources(resources)


@pytest.fixture(scope="session")
def contract_validate() -> Callable[[str, str, object], None]:
    """Provides ``validate(schema_file, definition, instance)``.

    The callable raises ``jsonschema.ValidationError`` when ``instance`` does
    not conform to ``{schema_file}#/$defs/{definition}``.

    Returns:
        The validation callable.
    """
    registry = _build_registry()

    def validate(schema_file: str, definition: str, instance: object) -> None:
        """Validates ``instance`` against one contract definition.

        Args:
            schema_file: File name such as ``market.schema.json``.
            definition: ``$defs`` entry such as ``Candle``.
            instance: JSON-compatible value to validate.
        """
        schema = {"$ref": f"{_BASE_URI}{schema_file}#/$defs/{definition}"}
        validator = jsonschema.Draft202012Validator(
            schema,
            registry=registry,
            format_checker=jsonschema.FormatChecker(),
        )
        validator.validate(instance)

    return validate
