# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated caller identity shared by every bounded context."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated subject of a request.

    Every tenant-owned read or write is scoped by ``tenant_id``; services
    must never return or mutate data belonging to another tenant.

    Attributes:
        tenant_id: Owning tenant.
        user_id: Acting user.
        scopes: Granted scopes such as ``strategy:write``.
    """

    tenant_id: str
    user_id: str
    scopes: frozenset[str] = frozenset()

    def has_scope(self, scope: str) -> bool:
        """Checks whether the principal holds a scope.

        Args:
            scope: Scope name.

        Returns:
            True when granted.
        """
        return scope in self.scopes
