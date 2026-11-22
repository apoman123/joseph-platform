"""Authenticated caller identity."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated subject; every query and mutation is tenant scoped.

    Attributes:
        tenant_id: Owning tenant.
        user_id: Acting user.
    """

    tenant_id: str
    user_id: str

    @property
    def actor(self) -> str:
        """Returns the audit actor label ``user:<id>``."""
        return f"user:{self.user_id}"
