# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Optional stdio MCP transport for running backends."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import httpx
from mcp.server import stdio

from joseph.mcp import catalog
from joseph.mcp import gateway
from joseph.mcp import protocol
from joseph.platform import redaction


async def serve(arguments: argparse.Namespace) -> None:
    """Serves official MCP framing over stdio.

    Serves official MCP framing over stdio using an existing user credential.

    Args:
        arguments: Operator-selected fixed backend URLs and permission flags.

    Returns:
        None after the MCP client closes stdin and all HTTP clients are closed.

    Raises:
        ValueError: If the user token is absent or trading lacks write
            permission.
        httpx.HTTPError: If a backend OpenAPI document cannot be loaded.
    """
    token = os.environ.get("JOSEPH_MCP_USER_TOKEN", "")
    if not token:
        raise ValueError("set JOSEPH_MCP_USER_TOKEN to an existing user token")
    if arguments.allow_trading and not arguments.allow_write:
        raise ValueError("--allow-trading also requires --allow-write")
    redaction.register_secret(token)
    scopes = {"read"}
    if arguments.allow_write:
        scopes.add("write")
    if arguments.allow_trading:
        scopes.add("trade")
    async with (
        httpx.AsyncClient(
            base_url=arguments.market_url, timeout=30, follow_redirects=False
        ) as market,
        httpx.AsyncClient(
            base_url=arguments.execution_url, timeout=30, follow_redirects=False
        ) as execution,
    ):
        operations = []
        for name, client in (("market", market), ("execution", execution)):
            response = await client.get(
                "/openapi.json", headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            operations.extend(catalog.operations(name, response.json()))
        server = protocol.build_server(
            gateway.Gateway(operations, market, execution),
            token=token,
            scopes=frozenset(scopes),
        )
        async with stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def main() -> int:
    """Parses local configuration and reserves stdout exclusively.

    Parses local configuration and reserves stdout exclusively for MCP traffic.

    Returns:
        Zero on orderly shutdown, one on a redacted startup failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-url", default="http://localhost:8000")
    parser.add_argument("--execution-url", default="http://localhost:8100")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-trading", action="store_true")
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        asyncio.run(serve(parser.parse_args()))
    except (ValueError, httpx.HTTPError) as exc:
        logging.getLogger(__name__).error(
            "MCP startup failed: %s", type(exc).__name__
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
