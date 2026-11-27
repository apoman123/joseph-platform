# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Official SDK negotiation, complete API catalogs and permission boundaries."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import httpx
import mcp
import pytest

from joseph import bootstrap
from joseph.mcp import catalog
from joseph.mcp import gateway
from joseph.mcp import protocol
from joseph.platform import config


@pytest.mark.asyncio
async def test_sdk_negotiates_protocol_and_reads_resources() -> None:
    """Exercises the actual SDK client against tools, resources and prompts."""
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        dev_tokens=config.parse_dev_tokens("protocol-user:tenant-a:user-a"),
    )
    container = bootstrap.build_container(settings)
    app = bootstrap.create_application(container)
    operations = catalog.operations("market", app.openapi())
    async with (
        httpx.AsyncClient(
            base_url="http://market", transport=httpx.ASGITransport(app=app)
        ) as market,
        httpx.AsyncClient(base_url="http://execution") as execution,
    ):
        server = protocol.build_server(
            gateway.Gateway(operations, market, execution),
            token="protocol-user",
        )
        async with mcp.Client(server) as client:
            assert client.protocol_version == "2026-07-28"
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "market_get_scripts" in names
            assert not any("connections" in name for name in names)
            response = await client.call_tool("market_get_heatmap", {})
            assert not response.is_error
            assert response.structured_content is not None
            assert response.structured_content["status"] == 200
            resources = await client.list_resources()
            assert {str(row.uri) for row in resources.resources} == {
                "joseph://capabilities",
                "joseph://scripts/sdk",
            }
            sdk = await client.read_resource("joseph://scripts/sdk")
            assert "Filter.matches" in str(sdk)
            prompts = await client.list_prompts()
            assert {row.name for row in prompts.prompts} == {
                "analyze_market",
                "review_script",
            }
            denied = await client.call_tool(
                "market_post_scripts",
                {
                    "body": {
                        "script_id": "denied",
                        "kind": "library",
                        "name": "Denied",
                        "source": "class X: pass",
                    }
                },
            )
            assert denied.is_error
    await container.aclose()


def test_catalog_covers_user_operations_and_excludes_credentials() -> None:
    """Keeps API coverage exhaustive as new user capabilities are introduced."""
    document: dict[str, Any] = {
        "paths": {
            "/v1/items": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        }
                    }
                }
            },
            "/v1/scripts/{script_id}/scan": {
                "post": {
                    "responses": {
                        "202": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        }
                    }
                }
            },
            "/v1/mcp/connections": {"post": {}},
            "/internal/signals": {"post": {}},
        }
    }
    actions = catalog.operations("market", document)
    assert len(actions) == 2
    assert len({row.tool.name for row in actions}) == 2
    assert all(row.tool.output_schema is not None for row in actions)
    assert not next(
        row for row in actions if row.path.endswith("/scan")
    ).read_only
    assert json.dumps([row.tool.model_dump() for row in actions])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,scopes",
    [
        (
            {
                "body": {
                    "operation": "command",
                    "command_id": "trading.submit",
                    "arguments": {},
                }
            },
            {"read", "write"},
        ),
        (
            {
                "body": {
                    "operation": "command",
                    "command_id": "custom.financial-alias",
                    "arguments": {},
                }
            },
            {"read", "write"},
        ),
        ({"body": {"operation": "workspace", "arguments": {}}}, {"read"}),
    ],
)
async def test_browser_control_cannot_bypass_mcp_trade_and_write_gates(
    arguments: dict[str, Any], scopes: set[str]
) -> None:
    """Rejects financial aliases before any browser or execution request occurs.

    Args:
        arguments: Untrusted browser command or workspace mutation body.
        scopes: Explicit resource-token authority available to this caller.
    """
    definition = {
        "paths": {
            "/v1/terminal/sessions/{session_id}/actions": {
                "post": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "session_id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object"}}
                        },
                    },
                    "responses": {"200": {}},
                }
            }
        }
    }
    actions = catalog.operations("market", definition)
    async with (
        httpx.AsyncClient(base_url="http://market") as market,
        httpx.AsyncClient(base_url="http://execution") as execution,
    ):
        router = gateway.Gateway(actions, market, execution)
        with pytest.raises(gateway.GatewayError, match=r"permission|read-only"):
            await router.call(
                actions[0].tool.name,
                {"path": {"session_id": "owned"}, **arguments},
                token="never-forwarded-on-rejection",
                scopes=frozenset(scopes),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["..", "foo/bar", "foo%2fbar", "foo\\bar", "bad\rheader"]
)
async def test_tool_path_injection_is_rejected_before_forwarding(
    value: str,
) -> None:
    """Rejects dot segments, path escapes and CRLF even when schemas allow text.

    Args:
        value: Untrusted path argument attempting to alter the fixed endpoint.
    """
    definition = {
        "paths": {
            "/v1/items/{id}": {
                "get": {
                    "parameters": [
                        {
                            "in": "path",
                            "name": "id",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {}},
                }
            }
        }
    }
    actions = catalog.operations("market", definition)
    async with (
        httpx.AsyncClient(base_url="http://market") as market,
        httpx.AsyncClient(base_url="http://execution") as execution,
    ):
        router = gateway.Gateway(actions, market, execution)
        with pytest.raises(gateway.GatewayError, match="Path parameters"):
            await router.call(
                actions[0].tool.name,
                {"path": {"id": value}},
                token="test-only",
                scopes=frozenset({"read"}),
            )
