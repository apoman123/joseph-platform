# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Services exposed to the API layer (assembled by the composition root)."""

from __future__ import annotations

import dataclasses

from joseph_execution.accounts import service as account_service
from joseph_execution.audit import service as audit_service
from joseph_execution.oms import deployments
from joseph_execution.oms import kill_switch
from joseph_execution.oms import service as oms_service
from joseph_execution.oms import signal_intake
from joseph_execution.platform import config
from joseph_execution.platform import metrics
from joseph_execution.platform import streams
from joseph_execution.ports import repositories
from joseph_execution.ports import services as service_ports
from joseph_execution.ports import trading
from joseph_execution.reconciliation import service as reconcile_service


@dataclasses.dataclass(slots=True)
class ApiServices:
    """Everything the routers may call.

    Attributes:
        settings: Settings.
        clock: Time source.
        uow_factory: Unit of work factory (audit reads).
        oms: OMS.
        accounts: Account service.
        kill_switches: Kill switch service.
        deployments: Deployment service.
        intake: Signal intake.
        reconciler: Reconciler.
        audit: Audit log.
        hub: User stream hub.
        metrics: Metrics registry.
        resolver: Adapter resolver (health reporting).
    """

    settings: config.Settings
    clock: service_ports.Clock
    uow_factory: repositories.UnitOfWorkFactory
    oms: oms_service.OmsService
    accounts: account_service.AccountService
    kill_switches: kill_switch.KillSwitchService
    deployments: deployments.DeploymentService
    intake: signal_intake.SignalIntakeService
    reconciler: reconcile_service.Reconciler
    audit: audit_service.AuditLog
    hub: streams.UserStreamHub
    metrics: metrics.MetricsRegistry
    resolver: trading.AdapterResolver
