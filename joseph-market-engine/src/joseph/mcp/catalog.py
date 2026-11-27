# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Derives stable, validated MCP tools.

Derives stable, validated MCP tools from the actual user-facing OpenAPIs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from mcp import types

_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_READ_POST = ("/validate", "/preview", "/close-preview", "/run")


def _references(value: object) -> Iterator[str]:
    """Finds local component references in a JSON schema tree.

    Args:
        value: JSON-compatible schema tree.

    Yields:
        Referenced component names; unrelated or remote references are ignored.
    """
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            yield reference.removeprefix("#/$defs/")
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _schema(value: Any) -> Any:
    """Copies OpenAPI local references into self-contained JSON Schema syntax.

    Args:
        value: Original OpenAPI JSON-compatible schema tree.

    Returns:
        Equivalent tree whose local component references use $defs.
    """
    if isinstance(value, dict):
        return {
            key: item.replace("#/components/schemas/", "#/$defs/")
            if key == "$ref" and isinstance(item, str)
            else _schema(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_schema(item) for item in value]
    return value


def _with_definitions(
    schema: dict[str, Any], components: Mapping[str, Any]
) -> dict[str, Any]:
    """Adds only reachable component definitions, bounding discovery payloads.

    Args:
        schema: Tool input or output schema using local $defs references.
        components: OpenAPI component dictionary.

    Returns:
        Standalone JSON Schema with transitive referenced definitions.
    """
    pending = list(_references(schema))
    definitions: dict[str, Any] = {}
    while pending:
        name = pending.pop()
        if name not in definitions and name in components:
            definitions[name] = _schema(components[name])
            pending.extend(_references(definitions[name]))
    if definitions:
        schema["$defs"] = definitions
    return schema


@dataclasses.dataclass(frozen=True, slots=True)
class Operation:
    """One allowlisted API action exposed through MCP.

    Attributes:
        tool: Public MCP name, description, schemas and side-effect hints.
        service: market or execution; never a model-provided URL.
        method: Fixed upstream HTTP method.
        path: Fixed path template with individually encoded path parameters.
        read_only: Whether this is a query or explicitly dry-run calculation.
        trading: Whether invoking it can alter orders, risk or trading consent.
    """

    tool: types.Tool
    service: str
    method: str
    path: str
    read_only: bool
    trading: bool


def operations(service: str, document: Mapping[str, Any]) -> list[Operation]:  # noqa: PLR0912 - One bounded OpenAPI traversal keeps schema derivation together.
    """Builds exhaustive tools.

    Builds exhaustive tools for public user APIs, excluding bootstrap secrets.

    Internal service-signal ingress and MCP credential issuance are deliberately
    excluded: neither grants an agent permission to escalate its own authority.

    Args:
        service: Fixed market or execution service identifier.
        document: Trusted project OpenAPI document, never model-supplied input.

    Returns:
        Stable tools for every /v1 user operation except MCP credential
        bootstrap.
    """
    result = []
    components = document.get("components", {}).get("schemas", {})
    for path, methods in document.get("paths", {}).items():
        if not path.startswith("/v1/") or path.startswith(
            "/v1/mcp/connections"
        ):
            continue
        for method, definition in methods.items():
            if method not in _METHODS:
                continue
            suffix = re.sub(
                r"[^a-zA-Z0-9]+", "_", path.removeprefix("/v1/")
            ).strip("_")
            raw_name = f"{service}_{method}_{suffix}"
            name = raw_name
            if len(raw_name) > 64:
                digest = hashlib.sha256(raw_name.encode()).hexdigest()[:12]
                name = f"{raw_name[:51]}_{digest}"
            properties: dict[str, Any] = {}
            required = []
            for location in ("path", "query", "header"):
                parameters = [
                    parameter
                    for parameter in definition.get("parameters", [])
                    if parameter.get("in") == location
                ]
                if not parameters:
                    continue
                nested = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        parameter["name"]: _schema(parameter.get("schema", {}))
                        for parameter in parameters
                    },
                }
                needed = [
                    parameter["name"]
                    for parameter in parameters
                    if parameter.get("required")
                ]
                if needed:
                    nested["required"] = needed
                    required.append(location)
                properties[location] = nested
            request = definition.get("requestBody", {})
            body_schema = (
                request.get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if body_schema is not None:
                properties["body"] = _schema(body_schema)
                if request.get("required"):
                    required.append("body")
            schema = _with_definitions(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": required,
                },
                components,
            )
            read_only = method == "get" or (
                method == "post" and path.endswith(_READ_POST)
            )
            if "/terminal/" in path and path.endswith("/actions"):
                read_only = False
            trading = service == "execution" and not read_only
            description = definition.get(
                "description",
                definition.get("summary", "Joseph user operation"),
            ).split("\n\n")[0]
            if not read_only:
                description = (
                    f"{description} Changes user state; requires "
                    "an explicitly write-enabled MCP connection."
                )
            if trading:
                description = (
                    f"{description} Requires trading permission and "
                    "all existing preview, account, consent "
                    "and live-mode gates."
                )
            outputs = []
            statuses = []
            for status, response in definition.get("responses", {}).items():
                if not str(status).startswith("2"):
                    continue
                statuses.append(int(status))
                outputs.append(
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema", {"type": "null"})
                )
            output = {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "data"],
                "properties": {
                    "status": {"type": "integer", "enum": statuses},
                    "data": {"anyOf": outputs},
                },
            }
            output = _with_definitions(_schema(output), components)
            result.append(
                Operation(
                    tool=types.Tool(
                        name=name,
                        description=description,
                        input_schema=schema,
                        output_schema=output,
                        annotations=types.ToolAnnotations(
                            read_only_hint=read_only,
                            destructive_hint=not read_only,
                            idempotent_hint=method in {"get", "put", "delete"},
                            open_world_hint=True,
                        ),
                    ),
                    service=service,
                    method=method.upper(),
                    path=path,
                    read_only=read_only,
                    trading=trading,
                )
            )
    return sorted(result, key=lambda operation: operation.tool.name)
