# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Permission-aware MCP gateway for fixed backend origins."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

import httpx
import jsonschema

from joseph.mcp import catalog


class GatewayError(Exception):
    """An actionable, secret-free MCP tool failure returned to its caller."""


class Gateway:
    """Forwards allowlisted user operations with a resource-bound identity.

    Attributes:
        operations: Stable public names mapped to trusted API definitions.
        market: Market HTTP client, optionally using same-process ASGI
            transport.
        execution: Execution HTTP client; user tokens never become service
            tokens.
    """

    def __init__(
        self,
        operations: list[catalog.Operation],
        market: httpx.AsyncClient,
        execution: httpx.AsyncClient,
    ) -> None:
        """Stores API definitions and shared clients.

        Stores API definitions and shared clients without exposing credentials.

        Args:
            operations: Trusted user-operation catalog.
            market: Fixed-origin market client owned by the MCP application.
            execution: Fixed-origin execution client owned by the application.
        """
        self.operations = {
            operation.tool.name: operation for operation in operations
        }
        self.market = market
        self.execution = execution

    async def call(  # noqa: PLR0912, PLR0915 - Ordered validation gates must finish before forwarding one request.
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        token: str,
        scopes: frozenset[str],
    ) -> dict[str, object]:
        """Validates one invocation and forwards it.

        Validates one invocation and forwards it without redirects or retries.

        Args:
            name: Allowlisted stable MCP tool name.
            arguments: Path/query/header/body values matching the tool schema.
            token: Server-resolved upstream user credential, never an MCP token.
            scopes: Resource-bound MCP read/write/trade permissions.

        Returns:
            HTTP status and structured upstream data for this authenticated
            user.

        Raises:
            GatewayError: For invalid arguments, unavailable services, rejected
                permissions or an upstream error; credential values are omitted.
        """
        operation = self.operations.get(name)
        if operation is None:
            raise GatewayError("Unknown tool; refresh the capability catalog.")
        try:
            if len(json.dumps(dict(arguments))) > 2_000_000:
                raise GatewayError("Tool input exceeds two MB.")
            jsonschema.Draft202012Validator(
                operation.tool.input_schema
            ).validate(dict(arguments))
        except jsonschema.ValidationError as exc:
            path = ".".join(str(item) for item in exc.absolute_path)
            raise GatewayError(
                f"Invalid arguments at {path or 'root'}; "
                "use the published input schema."
            ) from None
        if not operation.read_only and "write" not in scopes:
            raise GatewayError(
                "This connection is read-only. Enable write permission "
                "in the terminal's MCP settings."
            )
        if operation.trading and "trade" not in scopes:
            raise GatewayError(
                "Trading permission is required; existing previews and "
                "live consent remain mandatory."
            )
        body = arguments.get("body")
        if "/terminal/" in operation.path and isinstance(body, dict):
            command = str(body.get("command_id", ""))
            if (
                body.get("operation") == "command"
                and not re.match(
                    r"^(layout|sync|workspace|theme|palette|drawing|chart|indicator|python|shell|alerts)[.:-]",
                    command,
                )
                and "trade" not in scopes
            ):
                raise GatewayError(
                    "Trading permission is required for unclassified "
                    "terminal commands."
                )
            if (
                re.match(
                    r"^(trading|ticket|order|position|risk|deployment|account)[.:-]",
                    command,
                )
                and "trade" not in scopes
            ):
                raise GatewayError(
                    "Trading permission is required for this terminal command."
                )
        path = operation.path
        for key, value in arguments.get("path", {}).items():
            if str(value) in {".", ".."} or any(
                char in str(value) for char in ("/", "\\", "%", "\r", "\n")
            ):
                raise GatewayError(
                    "Path parameters must be single canonical identifiers."
                )
            path = path.replace(
                f"{{{key}}}", urllib.parse.quote(str(value), safe="")
            )
        if "{" in path or "}" in path:
            raise GatewayError("Required path arguments are missing.")
        headers = {"Authorization": "Bearer " + token}
        for key, value in arguments.get("header", {}).items():
            if key.lower() in {
                "authorization",
                "cookie",
                "host",
                "x-service-token",
            }:
                raise GatewayError(
                    "Authentication and routing headers cannot be overridden."
                )
            headers[key] = str(value)
        query: list[tuple[str, str | int | float | bool | None]] = []
        for key, value in arguments.get("query", {}).items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is not None:
                    encoded = str(item)
                    if isinstance(item, bool):
                        encoded = encoded.lower()
                    query.append((key, encoded))
        client = (
            self.market if operation.service == "market" else self.execution
        )
        try:
            response = await client.request(
                operation.method,
                path,
                params=query,
                json=body,
                headers=headers,
                follow_redirects=False,
                timeout=45,
            )
        except httpx.HTTPError:
            raise GatewayError(
                "Backend unavailable or timed out. Mutations were not "
                "retried; reconcile their state before trying again."
            ) from None
        if response.is_redirect:
            raise GatewayError(
                "Backend redirects are refused; configure the service's "
                "canonical origin."
            )
        if len(response.content) > 16_000_000:
            raise GatewayError(
                "Response exceeds sixteen MB; request a smaller result window."
            )
        if response.status_code == 204:
            data = None
        else:
            try:
                data = response.json()
            except ValueError:
                raise GatewayError(
                    "Backend returned a non-JSON response."
                ) from None
        if response.status_code >= 400:
            code = (
                str(data.get("code", "BACKEND_ERROR"))
                if isinstance(data, dict)
                else "BACKEND_ERROR"
            )
            message = (
                str(data.get("message", "Request rejected"))
                if isinstance(data, dict)
                else "Request rejected"
            )
            raise GatewayError(f"{code}: {message.replace(token, '***')[:400]}")
        if (
            "/terminal/" in operation.path
            and operation.path.endswith("/actions")
            and isinstance(data, dict)
            and data.get("ok") is False
        ):
            message = str(data.get("error", "browser action failed"))
            raise GatewayError(message.replace(token, "***")[:400])
        return {"status": response.status_code, "data": data}
