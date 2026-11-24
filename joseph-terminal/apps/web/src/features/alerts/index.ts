/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Alerts feature: strategy signals (polled every 15 s and
 * merged with user-stream updates) with filters, local acknowledgment and
 * triage, explanation traces and chart focus, plus the notification
 * subscriptions panel.
 */

import type {Signal} from '@joseph/contracts';
import {createStore, type Disposer} from '@joseph/feature-registry';
import {createElement} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {AlertsPanel, type AlertsState} from './alerts-panel';
import {NotificationsPanel} from './notifications-panel';
import type {SignalTriage} from './ranking';

/** Persisted alert settings (schema version 1). */
export interface AlertSettings {
  /** Acknowledged signal ids, newest first. */
  acked: string[];
  /** Dismissed signal ids (hidden from the list). */
  dismissed?: string[];
  /** Snoozed signal ids with their wake-up time (UTC ms). */
  snoozed?: Record<string, number>;
}

/**
 * Reads the triage part of stored settings defensively.
 * @param settings Stored settings.
 * @return Triage state; entries of the wrong type are dropped.
 */
export function triageFromSettings(settings: AlertSettings): SignalTriage {
  const snoozed: Record<string, number> = {};
  if (settings.snoozed && typeof settings.snoozed === 'object') {
    for (const [id, until] of Object.entries(settings.snoozed)) {
      if (typeof until === 'number' && Number.isFinite(until)) {
        snoozed[id] = until;
      }
    }
  }
  return {
    dismissed: Array.isArray(settings.dismissed)
      ? settings.dismissed.filter((id): id is string => typeof id === 'string')
      : [],
    snoozed,
  };
}

/**
 * Merges signals by id, keeping the highest revision, newest bar first.
 * Polls and the stream can deliver the same signal in any order; comparing
 * revisions keeps a late, older copy from replacing a newer one, and an
 * equal revision simply replaces the stored copy.
 * @param current Existing signals.
 * @param incoming New or updated signals.
 * @return Merged list.
 */
export function mergeSignals(
  current: readonly Signal[],
  incoming: readonly Signal[]
): Signal[] {
  const byId = new Map(current.map(s => [s.signal_id, s]));
  for (const signal of incoming) {
    const existing = byId.get(signal.signal_id);
    if (!existing || signal.revision >= existing.revision) {
      byId.set(signal.signal_id, signal);
    }
  }
  return [...byId.values()].sort(
    (a, b) =>
      b.bar_close_ms - a.bar_close_ms || b.evaluated_at_ms - a.evaluated_at_ms
  );
}

/** The alerts feature. */
export const ALERTS_FEATURE: WebFeature = {
  manifest: {
    id: 'alerts',
    version: '1.0.0',
    owner: 'terminal-strategy',
    displayName: 'Alerts',
    description:
      'Strategy signals with reason trees; click to open the exact venue, instrument, bar and AOI.',
    uiSlots: ['bottom-panel'],
    requiredCapabilities: ['strategy'],
    permissions: ['strategy:read'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Signals stay on the server; only local acknowledgments in feature settings are kept for restoration.',
    },
  },
  activate(ctx) {
    const state = createStore<AlertsState>({
      signals: [],
      error: null,
      loaded: false,
    });
    const settings = ctx.settings.read<AlertSettings>({acked: []});
    const acked = createStore<readonly string[]>(
      Array.isArray(settings.acked) ? settings.acked : []
    );
    const triage = createStore<SignalTriage>(triageFromSettings(settings));
    const persist = () =>
      void ctx.settings.write<AlertSettings>({
        acked: [...acked.get()],
        dismissed: [...triage.get().dismissed],
        snoozed: {...triage.get().snoozed},
      });
    const disposers: Disposer[] = [
      acked.subscribe(persist),
      triage.subscribe(persist),
    ];
    const load = async () => {
      try {
        const {signals} = await ctx.clients.strategy.listSignals({limit: 200});
        state.update(s => ({
          signals: mergeSignals(s.signals, signals),
          error: null,
          loaded: true,
        }));
      } catch (err) {
        state.update(s => ({...s, error: errorMessage(err), loaded: true}));
      }
    };
    void load();
    const timer = setInterval(() => void load(), 15_000);
    disposers.push(() => void clearInterval(timer));
    disposers.push(
      ctx.clients.userStream.subscribe([{channel: 'signals'}], envelope => {
        if (
          envelope.type === 'signal.created' ||
          envelope.type === 'signal.updated'
        ) {
          // Signal events carry a contract `Signal` as their payload.
          state.update(s => ({
            ...s,
            signals: mergeSignals(s.signals, [envelope.payload as Signal]),
          }));
        } else if (envelope.type === 'snapshot_required') {
          // The stream missed events: reload the list.
          void load();
        }
      })
    );
    /**
     * Renders the alerts panel bound to the feature's stores.
     * @param props Slot props.
     * @return Element.
     */
    function AlertsSlot(props: WebSlotProps) {
      return createElement(AlertsPanel, {
        ctx: props.ctx,
        state,
        acked,
        triage,
      });
    }
    const view: WebView = AlertsSlot;
    /**
     * Renders the notification subscriptions panel.
     * @param props Slot props.
     * @return Element.
     */
    function NotificationsSlot(props: WebSlotProps) {
      return createElement(NotificationsPanel, {ctx: props.ctx});
    }
    disposers.push(
      ctx.slots.contribute('bottom-panel', {
        id: 'alerts',
        title: 'Alerts',
        order: 50,
        icon: 'bell',
        view,
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'alerts.notifications',
        title: 'Notifications',
        order: 55,
        icon: 'bell',
        view: NotificationsSlot,
      })
    );
    return () => {
      for (const dispose of disposers.splice(0).reverse()) {
        dispose();
      }
    };
  },
};
