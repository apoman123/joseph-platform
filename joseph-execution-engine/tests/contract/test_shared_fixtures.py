"""Validates the shared contract fixtures with the Python JSON Schema stack.

The same fixtures are validated by the TypeScript suite with ajv, so both
consumers agree on every canonical example (consumer-driven contracts).
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import jsonschema
import pytest

_FIXTURES = (
    pathlib.Path(__file__).resolve().parents[3]
    / "joseph-terminal"
    / "packages"
    / "contracts"
    / "fixtures"
)


def _cases(kind: str) -> list[tuple[str, str, str, object]]:
    """Collects fixture instances of one kind.

    Args:
        kind: ``"valid"`` or ``"invalid"``.

    Returns:
        ``(fixture, schema, definition, instance)`` tuples.
    """
    cases = []
    for path in sorted(_FIXTURES.glob("*.fixture.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        for index, instance in enumerate(body[kind]):
            cases.append(
                (
                    f"{path.stem}[{index}]",
                    body["schema"],
                    body["definition"],
                    instance,
                )
            )
    return cases


@pytest.mark.parametrize(
    ("name", "schema", "definition", "instance"),
    _cases("valid"),
    ids=lambda v: v if isinstance(v, str) and "[" in v else "",
)
def test_valid_fixture_conforms(
    contract_validate: Callable[[str, str, object], None],
    name: str,
    schema: str,
    definition: str,
    instance: object,
) -> None:
    """Every ``valid`` example conforms to its definition."""
    del name
    contract_validate(schema, definition, instance)


@pytest.mark.parametrize(
    ("name", "schema", "definition", "instance"),
    _cases("invalid"),
    ids=lambda v: v if isinstance(v, str) and "[" in v else "",
)
def test_invalid_fixture_is_rejected(
    contract_validate: Callable[[str, str, object], None],
    name: str,
    schema: str,
    definition: str,
    instance: object,
) -> None:
    """Every ``invalid`` example is rejected by its definition."""
    del name
    with pytest.raises(jsonschema.ValidationError):
        contract_validate(schema, definition, instance)
