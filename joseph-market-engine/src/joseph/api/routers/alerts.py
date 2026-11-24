# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Alert subscription routes (``/v1/alerts/subscriptions``).

No postponed annotations: FastAPI resolves the injected dependency.
"""

from collections.abc import Callable
from typing import Annotated
from typing import Any

import fastapi

from joseph.alerts.app import subscriptions
from joseph.domain import identity


def build_router(
    *,
    service: subscriptions.SubscriptionService,
    get_principal: Callable[..., identity.Principal],
) -> fastapi.APIRouter:
    """Builds the alert subscription router.

    Both routes require a bearer token; ``get_principal`` answers 401
    without a valid one.

    Args:
        service: Subscription service.
        get_principal: Authentication dependency.

    Returns:
        Router serving ``GET`` and ``PUT`` on ``/v1/alerts/subscriptions``.
    """
    router = fastapi.APIRouter(tags=["alerts"])
    principal_dep = Annotated[
        identity.Principal, fastapi.Depends(get_principal)
    ]

    @router.get("/v1/alerts/subscriptions")
    def list_subscriptions(principal: principal_dep) -> dict[str, object]:
        """Lists the caller's subscriptions.

        Args:
            principal: Caller; only their tenant's subscriptions are listed.

        Returns:
            Contract ``AlertSubscriptionList``: subscriptions on
            user-managed channels and the channels with server credentials.
        """
        return service.list(principal)

    @router.put("/v1/alerts/subscriptions/{subscription_id}")
    def put_subscription(
        principal: principal_dep,
        subscription_id: str,
        body: Annotated[dict[str, Any], fastapi.Body()],
    ) -> dict[str, object]:
        """Creates or replaces a subscription.

        Args:
            principal: Caller; the subscription belongs to their tenant.
            subscription_id: Identifier from the path.
            body: Contract ``AlertSubscription``.

        Returns:
            Stored contract ``AlertSubscription``.

        Raises:
            errors.ValidationFailedError: If the id or a body field is
                invalid (422).
        """
        return service.upsert(principal, subscription_id, body)

    return router
