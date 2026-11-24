# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Authenticated caller identity."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller; every query and mutation is tenant scoped.

    Attributes:
        tenant_id: Tenant the caller acts for.
        user_id: Acting user (or service principal) within the tenant.
    """

    tenant_id: str
    user_id: str

    @property
    def actor(self) -> str:
        """The audit actor label, ``user:<user_id>``."""
        return f"user:{self.user_id}"
