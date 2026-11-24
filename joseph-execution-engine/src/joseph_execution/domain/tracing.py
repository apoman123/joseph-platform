# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Request trace id propagation via ``contextvars``."""

from __future__ import annotations

import contextvars

_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "joseph_trace_id", default="no-trace"
)


def current_trace_id() -> str:
    """Returns the trace id bound to the current context.

    Returns:
        Trace id or ``"no-trace"``.
    """
    return _TRACE_ID.get()


def bind_trace_id(trace_id: str) -> contextvars.Token[str]:
    """Binds a trace id to the current context.

    Args:
        trace_id: Trace identifier.

    Returns:
        Token to restore the previous value.
    """
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: contextvars.Token[str]) -> None:
    """Restores the previous trace id.

    Args:
        token: Token from ``bind_trace_id``.
    """
    _TRACE_ID.reset(token)
