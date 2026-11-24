/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared read model of the portfolio views: accounts, orders,
 * positions, fills, kill switches and the audit log. Refreshed on an
 * interval and whenever the authenticated user stream reports a change.
 */

import type {
  AccountSummary,
  AuditEventList,
  Fill,
  KillSwitchState,
  Order,
  Position,
} from '@joseph/contracts';
import {
  createStore,
  type Disposer,
  type WritableStore,
} from '@joseph/feature-registry';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';

/** Portfolio read model. */
export interface PortfolioState {
  /** Connected accounts of the tenant. */
  accounts: readonly AccountSummary[];
  /** Orders newest first. */
  orders: readonly Order[];
  /** Non-flat positions. */
  positions: readonly Position[];
  /** Fills newest first. */
  fills: readonly Fill[];
  /** Kill switches. */
  killSwitches: readonly KillSwitchState[];
  /** Audit events and chain validity. */
  audit: AuditEventList | null;
  /**
   * Message of the last failed refresh, or null after a success; the other
   * fields keep the last loaded values.
   */
  error: string | null;
  /** Whether the first load finished. */
  loaded: boolean;
  /** Time of the last successful refresh (UTC ms). */
  updatedAtMs: number | null;
}

/** Portfolio runtime. */
export interface PortfolioRuntime {
  /** State store. */
  state: WritableStore<PortfolioState>;
  /**
   * Reloads everything. A call during a running refresh schedules one more
   * refresh afterwards and returns the running one.
   * @return Promise resolving when the running refresh is done. It never
   *     rejects: a failure is reported in `state.error`.
   */
  refresh(): Promise<void>;
  /** Stops polling and stream subscriptions. */
  dispose(): void;
}

/**
 * Creates the portfolio runtime and starts refreshing on user-stream events
 * and on an interval.
 * @param ctx Feature context.
 * @param pollMs Refresh interval in milliseconds.
 * @return Runtime; call `dispose` to stop it.
 */
export function createPortfolioRuntime(
  ctx: WebFeatureContext,
  pollMs = 3000
): PortfolioRuntime {
  const state = createStore<PortfolioState>({
    accounts: [],
    orders: [],
    positions: [],
    fills: [],
    killSwitches: [],
    audit: null,
    error: null,
    loaded: false,
    updatedAtMs: null,
  });
  const disposers: Disposer[] = [];
  let inFlight: Promise<void> | null = null;
  // Events can arrive while a refresh is running (e.g. a fill before the
  // position update); coalesce them into one follow-up refresh so the read
  // model never settles on a stale snapshot.
  let rerun = false;
  const runtime: PortfolioRuntime = {
    state,
    refresh() {
      if (inFlight) {
        rerun = true;
        return inFlight;
      }
      const exec = ctx.clients.execution;
      inFlight = Promise.all([
        exec.listAccounts(),
        exec.listOrders(),
        exec.listPositions(),
        exec.listFills(),
        exec.getKillSwitches(),
        exec.listAuditEvents(undefined, 200),
      ])
        .then(([accounts, orders, positions, fills, switches, audit]) => {
          state.set({
            accounts: accounts.accounts,
            orders: orders.orders,
            positions: positions.positions,
            fills: fills.fills,
            killSwitches: switches.switches,
            audit,
            error: null,
            loaded: true,
            updatedAtMs: Date.now(),
          });
        })
        .catch((err: unknown) => {
          state.update(s => ({...s, error: errorMessage(err), loaded: true}));
        })
        .finally(() => {
          inFlight = null;
          if (rerun) {
            rerun = false;
            void runtime.refresh();
          }
        });
      return inFlight;
    },
    dispose() {
      for (const dispose of disposers.splice(0)) {
        dispose();
      }
    },
  };
  disposers.push(
    ctx.clients.userStream.subscribe(
      [
        {channel: 'orders'},
        {channel: 'fills'},
        {channel: 'positions'},
        {channel: 'risk'},
        {channel: 'account'},
      ],
      () => void runtime.refresh()
    )
  );
  const timer = setInterval(() => void runtime.refresh(), pollMs);
  disposers.push(() => void clearInterval(timer));
  void runtime.refresh();
  return runtime;
}
