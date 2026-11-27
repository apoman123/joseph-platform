# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Opt-in user-scoped browser control; never executes arbitrary JavaScript."""

import asyncio
import contextlib
import dataclasses
import json
import uuid
from collections.abc import Callable
from typing import Annotated
from typing import Literal

import fastapi
import pydantic

from joseph.domain import errors
from joseph.domain import identity
from joseph.platform import auth


class TerminalAction(pydantic.BaseModel):
    """One explicit operation on a connected, user-enabled browser.

    Attributes:
        operation: Registered command, validated workspace replacement, drawing
            edit, replay, theme, export or screenshot operation.
        command_id: Exact registered command id, required for command
            operations.
        arguments: JSON arguments for the selected operation, never source code.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    operation: Literal[
        "command",
        "workspace",
        "drawing",
        "replay",
        "theme",
        "export",
        "screenshot",
        "template",
    ]
    command_id: Annotated[str, pydantic.Field(max_length=160)] | None = None
    arguments: dict[str, pydantic.JsonValue] = pydantic.Field(
        default_factory=dict
    )


@dataclasses.dataclass
class Session:
    """Ephemeral browser session isolated by both tenant and user.

    Attributes:
        session_id: Random browser-session identity, valid until disconnect.
        principal: Authenticated browser owner, never an MCP service identity.
        websocket: Accepted first-frame-authenticated connection.
        state: Explicit browser context without tokens, cookies or client
            objects.
        pending: Bounded outstanding request ids mapped to completion futures.
        send_lock: Serializes outbound ASGI frames for concurrent callers.
    """

    session_id: str
    principal: identity.Principal
    websocket: fastapi.WebSocket
    state: dict[str, pydantic.JsonValue] = dataclasses.field(
        default_factory=dict
    )
    pending: dict[str, asyncio.Future[dict[str, pydantic.JsonValue]]] = (
        dataclasses.field(default_factory=dict)
    )
    send_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)


class TerminalHub:
    """Coordinates explicit browser actions.

    Coordinates explicit browser actions without replaying uncertain mutations.

    Attributes:
        authenticator: Existing user authenticator, excluding service tokens.
        origins: Explicit browser origin allowlist.
        sessions: Ephemeral opt-in browser sessions, not persisted across
            restart.
    """

    def __init__(
        self, authenticator: auth.TokenAuthenticator, origins: tuple[str, ...]
    ) -> None:
        """Initializes an empty control hub without starting network listeners.

        Args:
            authenticator: Existing ordinary-user token verifier.
            origins: Allowed browser origins; missing/foreign origins are
                refused.
        """
        self.authenticator = authenticator
        self.origins = origins
        self.sessions: dict[str, Session] = {}

    def owned(self, principal: identity.Principal, session_id: str) -> Session:
        """Resolves a connected browser belonging to the same tenant and user.

        Args:
            principal: Authenticated control caller.
            session_id: Opaque ephemeral browser identity.

        Returns:
            Owned connected browser session.

        Raises:
            errors.NotFoundError: If absent, disconnected or owned by another
                user.
        """
        session = self.sessions.get(session_id)
        if session is None or (
            session.principal.tenant_id,
            session.principal.user_id,
        ) != (principal.tenant_id, principal.user_id):
            raise errors.NotFoundError(
                "no owned browser session; enable MCP browser control in"
                " the terminal"
            )
        return session

    async def connect(self, websocket: fastapi.WebSocket) -> None:  # noqa: PLR0912, GPY013 - The bounded transport state machine closes on validation failures.
        """Authenticates the first frame.

        Authenticates the first frame and accepts bounded state/result updates.

        Args:
            websocket: Browser connection whose Origin must match the allowlist.

        Returns:
            None after disconnect; pending actions fail without automatic
            retries.
        """
        if websocket.headers.get("origin") not in self.origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session = None
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), 5)
            if len(raw) > 4096:
                raise ValueError("authentication frame too large")
            authentication = json.loads(raw)
            if not isinstance(authentication, dict):
                raise ValueError("authentication frame must be an object")
            principal = self.authenticator.authenticate(
                authentication.get("token")
            )
            if len(self.sessions) >= 100:
                raise ValueError("browser session capacity reached")
            session = Session(uuid.uuid4().hex, principal, websocket)
            self.sessions[session.session_id] = session
            await websocket.send_json(
                {"type": "connected", "session_id": session.session_id}
            )
            while True:
                raw = await asyncio.wait_for(websocket.receive_text(), 60)
                if len(raw.encode()) > 8_000_000:
                    raise ValueError("browser message too large")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("expected a browser message object")
                if message.get("type") == "state":
                    state = message.get("state")
                    if not isinstance(state, dict):
                        raise ValueError("state must be an object")
                    session.state = state
                elif message.get("type") == "result":
                    pending = session.pending.get(
                        str(message.get("request_id"))
                    )
                    if pending is not None and not pending.done():
                        pending.set_result(message)
        except (
            fastapi.WebSocketDisconnect,
            TimeoutError,
            ValueError,
            errors.JosephError,
        ):
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1008)
        finally:
            if session is not None:
                self.sessions.pop(session.session_id, None)
                for pending in session.pending.values():
                    if not pending.done():
                        pending.set_exception(
                            errors.MissingDataError(
                                "browser disconnected; inspect state "
                                "before retrying a mutation"
                            )
                        )

    async def execute(
        self,
        principal: identity.Principal,
        session_id: str,
        action: TerminalAction,
    ) -> dict[str, pydantic.JsonValue]:
        """Requests one bounded browser action without retrying on timeout.

        Args:
            principal: Authenticated owner with user-data write permission.
            session_id: Explicit connected browser identity.
            action: Allowlisted operation and JSON arguments, never JavaScript.

        Returns:
            Browser-reported completion or actionable gesture/validation error.

        Raises:
            errors.JosephError: On absent ownership, capacity, disconnect or
                timeout.
        """
        if principal.scopes and not principal.has_scope("strategy:write"):
            raise errors.ForbiddenError(
                "strategy:write is required for terminal control"
            )
        session = self.owned(principal, session_id)
        if (
            len(session.pending) >= 8
            or len(action.model_dump_json()) > 2_000_000
        ):
            raise errors.ConflictError(
                "browser control request capacity exceeded"
            )
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, pydantic.JsonValue]] = (
            asyncio.get_running_loop().create_future()
        )
        session.pending[request_id] = future
        try:
            async with session.send_lock:
                await session.websocket.send_json(
                    {
                        "type": "action",
                        "request_id": request_id,
                        **action.model_dump(),
                    }
                )
            return await asyncio.wait_for(future, 30)
        except TimeoutError:
            raise errors.MissingDataError(
                "browser action timed out; completion is uncertain, "
                "inspect browser state before retrying"
            ) from None
        finally:
            session.pending.pop(request_id, None)


def build_router(
    hub: TerminalHub, get_principal: Callable[..., identity.Principal]
) -> fastapi.APIRouter:
    """Exposes opted-in browser capabilities as normal authenticated user APIs.

    Args:
        hub: Ephemeral user-isolated browser-control coordinator.
        get_principal: Existing ordinary-user bearer authentication dependency.

    Returns:
        Browser session list/state/action routes automatically included in MCP.
    """
    router = fastapi.APIRouter(prefix="/v1/terminal", tags=["terminal-control"])
    owner = Annotated[identity.Principal, fastapi.Depends(get_principal)]

    @router.websocket("/control")
    async def control(websocket: fastapi.WebSocket) -> None:
        """Runs a first-frame-authenticated browser control connection.

        Args:
            websocket: Origin-checked browser connection, no token in its URL.

        Returns:
            None after disconnection and cleanup of pending requests.
        """
        await hub.connect(websocket)

    @router.get("/sessions")
    def sessions(principal: owner) -> dict[str, object]:
        """Lists only browser sessions this user explicitly enabled.

        Args:
            principal: Authenticated caller.

        Returns:
            User-owned browser identities and explicit capability/state
            snapshots.
        """
        return {
            "sessions": [
                {"session_id": session.session_id, "state": session.state}
                for session in hub.sessions.values()
                if session.principal.tenant_id == principal.tenant_id
                and session.principal.user_id == principal.user_id
            ]
        }

    @router.get("/sessions/{session_id}")
    def state(session_id: str, principal: owner) -> dict[str, object]:
        """Reads the latest explicitly published state of an owned browser.

        Args:
            session_id: Opaque browser identity.
            principal: Authenticated owner.

        Returns:
            Workspace, drawings and registered capabilities without credentials.
        """
        return {
            "session_id": session_id,
            "state": hub.owned(principal, session_id).state,
        }

    @router.post("/sessions/{session_id}/actions")
    async def action(
        session_id: str, body: TerminalAction, principal: owner
    ) -> dict[str, pydantic.JsonValue]:
        """Requests one explicit browser action.

        Requests one explicit browser action with bounded acknowledgment time.

        Args:
            session_id: User-selected connected browser identity.
            body: Allowlisted operation and JSON arguments.
            principal: Authenticated owner with mutation permission.

        Returns:
            Completion/error reported by the browser; mutations are never
            retried.
        """
        return await hub.execute(principal, session_id, body)

    return router
