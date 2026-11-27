# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Automatic MCP lifecycle, resource-bound authentication.

Automatic MCP lifecycle, resource-bound authentication and permission tests.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
from collections.abc import Iterator

import pytest
from fastapi import testclient
from starlette import websockets

from joseph import bootstrap
from joseph.platform import config


@pytest.fixture
def client() -> Iterator[testclient.TestClient]:
    """Starts the ordinary backend and its automatically mounted MCP transport.

    Yields:
        Full application client with two independent tenant identities.
    """
    settings = dataclasses.replace(
        config.Settings(),
        demo_only=True,
        database_url="sqlite://",
        pipeline_enabled=False,
        dev_tokens=config.parse_dev_tokens(
            "mcp-test-a:tenant-a:user-a,mcp-test-b:tenant-b:user-b"
        ),
    )
    app = bootstrap.create_application(bootstrap.build_container(settings))
    with testclient.TestClient(app) as connection:
        yield connection


@pytest.mark.parametrize("payload", ["null", "[]", "42", '"token"'])
def test_browser_authentication_rejects_nonobject_frames(
    client: testclient.TestClient, payload: str
) -> None:
    """Closes malformed authentication frames without a server exception.

    Args:
        client: Full application fixture with the browser-control endpoint.
        payload: Valid JSON that is not an authentication object.

    Returns:
        None after checking that the connection closes with policy code 1008.
    """
    with client.websocket_connect(
        "/v1/terminal/control", headers={"origin": "http://localhost:3000"}
    ) as browser:
        browser.send_text(payload)
        with pytest.raises(websockets.WebSocketDisconnect) as closed:
            browser.receive_json()
        assert closed.value.code == 1008


def _grant(
    client: testclient.TestClient, *, write: bool = False
) -> dict[str, object]:
    """Creates an explicit MCP grant using the authenticated application API.

    Args:
        client: Running application client.
        write: Whether the user explicitly permits state mutations.

    Returns:
        One-time resource token and revocation metadata.
    """
    response = client.post(
        "/v1/mcp/connections",
        headers={"Authorization": "Bearer mcp-test-a"},
        json={"name": "Test agent", "write": write},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _call(
    client: testclient.TestClient,
    token: str,
    method: str,
    params: dict[str, object] | None = None,
) -> object:
    """Calls the real legacy-compatible HTTP JSON-RPC transport.

    Args:
        client: Running application client.
        token: Resource-scoped MCP bearer token.
        method: JSON-RPC MCP method name.
        params: Optional method parameters.

    Returns:
        Raw HTTP response for protocol and permission assertions.
    """
    return client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": "2025-11-25",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
    )


def test_mcp_starts_with_backend_and_rejects_ordinary_api_tokens(
    client: testclient.TestClient,
) -> None:
    """Accepts resource-scoped MCP credentials only.

    Args:
        client: Running backend with MCP already mounted.

    Returns:
        None after verifying liveness, audience separation and server discovery.
    """
    assert client.get("/health").status_code == 200
    rejected = client.post(
        "/mcp",
        headers={"Authorization": "Bearer mcp-test-a"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert rejected.status_code == 401
    grant = _grant(client)
    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + str(grant["token"]),
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": "2025-11-25",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "joseph-platform"


def test_current_protocol_envelope_and_http_routing_headers(
    client: testclient.TestClient,
) -> None:
    """Accepts current-revision envelopes with matching HTTP method routing.

    Args:
        client: Running full application with an automatically mounted MCP app.

    Returns:
        None after checking real tool discovery and an authenticated data read.
    """
    grant = _grant(client)
    headers = {
        "Authorization": f"Bearer {grant['token']}",
        "Accept": "application/json, text/event-stream",
        "Mcp-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
    }
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": meta},
        },
    )
    assert response.status_code == 200, response.text
    names = {item["name"] for item in response.json()["result"]["tools"]}
    assert "market_get_venues" in names
    headers.update(
        {"Mcp-Method": "tools/call", "Mcp-Name": "market_get_venues"}
    )
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "_meta": meta,
                "name": "market_get_venues",
                "arguments": {},
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError")
    assert result["structuredContent"]["status"] == 200


def test_grants_are_private_and_revocable(
    client: testclient.TestClient,
) -> None:
    """Prevents another tenant.

    Prevents another tenant from listing or revoking the caller's connection.

    Args:
        client: Running application with two tenant identities.

    Returns:
        None after checking secret-free metadata and immediate revocation.
    """
    grant = _grant(client)
    token = str(grant["token"])
    listed = client.get(
        "/v1/mcp/connections", headers={"Authorization": "Bearer mcp-test-a"}
    ).json()
    assert token not in str(listed)
    assert listed["connections"][0]["scopes"] == ["read"]
    other = client.get(
        "/v1/mcp/connections", headers={"Authorization": "Bearer mcp-test-b"}
    ).json()
    assert other["connections"] == []
    path = "/v1/mcp/connections/" + str(grant["connection_id"])
    assert (
        client.delete(
            path, headers={"Authorization": "Bearer mcp-test-b"}
        ).status_code
        == 404
    )
    assert (
        client.delete(
            path, headers={"Authorization": "Bearer mcp-test-a"}
        ).status_code
        == 204
    )
    denied = client.post(
        "/mcp",
        headers={"Authorization": "Bearer " + token},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert denied.status_code == 401


def test_opted_in_browser_control_is_tenant_scoped_and_acknowledged(
    client: testclient.TestClient,
) -> None:
    """Requires an opted-in browser and reports explicit action completion.

    Args:
        client: Running two-tenant application with automatic MCP.
    """
    headers = {"Authorization": "Bearer mcp-test-a"}
    with client.websocket_connect(
        "/v1/terminal/control", headers={"origin": "http://localhost:3000"}
    ) as browser:
        browser.send_json({"token": "mcp-test-a"})
        connected = browser.receive_json()
        session_id = connected["session_id"]
        browser.send_json(
            {
                "type": "state",
                "state": {
                    "commands": [{"id": "layout.1", "title": "Single chart"}]
                },
            }
        )
        assert (
            client.get(
                f"/v1/terminal/sessions/{session_id}",
                headers={"Authorization": "Bearer mcp-test-b"},
            ).status_code
            == 404
        )
        assert client.get("/v1/terminal/sessions", headers=headers).json()[
            "sessions"
        ]
        assert (
            client.post(
                f"/v1/terminal/sessions/{session_id}/actions",
                headers=headers,
                json={
                    "operation": "eval",
                    "arguments": {"source": "untrusted"},
                },
            ).status_code
            == 422
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post,
                f"/v1/terminal/sessions/{session_id}/actions",
                headers=headers,
                json={"operation": "theme", "arguments": {"theme": "light"}},
            )
            request = browser.receive_json()
            assert request["operation"] == "theme"
            browser.send_json(
                {
                    "type": "result",
                    "request_id": request["request_id"],
                    "ok": True,
                    "result": {"theme": "light"},
                }
            )
            response = pending.result(timeout=5)
            assert response.status_code == 200, response.text
            assert response.json()["ok"] is True
    assert not client.get("/v1/terminal/sessions", headers=headers).json()[
        "sessions"
    ]
