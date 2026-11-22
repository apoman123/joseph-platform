"""Explanation trace produced for every evaluated rule node."""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Mapping

from joseph.domain import decimals
from joseph.strategy.domain import tristate


def _jsonable(value: object) -> object:
    """Converts evidence values to JSON-compatible values.

    Args:
        value: Evidence value (decimal, enum, mapping, sequence, scalar).

    Returns:
        JSON-compatible equivalent with decimals as strings.
    """
    if isinstance(value, decimal.Decimal):
        return decimals.format_decimal(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(v) for v in value]
        if isinstance(value, (set, frozenset)):
            return sorted(items, key=str)
        return items
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class ReasonNode:
    """Why one node evaluated to its result (contract ``ReasonNode``).

    Attributes:
        node_path: JSON pointer of the node inside the definition.
        node_id: Editor id of the node, if any.
        op: Node operator.
        result: Truth value.
        detail: Plain-language explanation.
        evidence: Supporting values (AOI ids, range ids, observed values).
        children: Traces of evaluated children.
    """

    node_path: str
    node_id: str | None
    op: str
    result: tristate.TriState
    detail: str
    evidence: Mapping[str, object] = dataclasses.field(default_factory=dict)
    children: tuple[ReasonNode, ...] = ()

    def to_contract(self) -> dict[str, object]:
        """Serializes to the contract ``ReasonNode`` object.

        Returns:
            JSON-compatible dict.
        """
        return {
            "node_path": self.node_path,
            "node_id": self.node_id,
            "op": self.op,
            "result": self.result.value,
            "detail": self.detail,
            "evidence": _jsonable(dict(self.evidence)),
            "children": [child.to_contract() for child in self.children],
        }
