/**
 * @fileoverview Alerts feature: strategy signals with filters, local
 * acknowledgment, explanation traces and chart focus.
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

/** Persisted alert settings (schema version 1). */
export interface AlertSettings {
  /** Acknowledged signal ids, newest first. */
  acked: string[];
}

/**
 * Merges signals by id, keeping the highest revision, newest bar first.
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
    if (!existing || signal.revision >= existing.revision)
      byId.set(signal.signal_id, signal);
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
    const disposers: Disposer[] = [
      acked.subscribe(ids =>
        ctx.settings.write<AlertSettings>({acked: [...ids]})
      ),
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
    disposers.push(() => clearInterval(timer));
    disposers.push(
      ctx.clients.userStream.subscribe([{channel: 'signals'}], envelope => {
        if (
          envelope.type === 'signal.created' ||
          envelope.type === 'signal.updated'
        ) {
          state.update(s => ({
            ...s,
            signals: mergeSignals(s.signals, [envelope.payload as Signal]),
          }));
        } else if (envelope.type === 'snapshot_required') {
          void load();
        }
      })
    );
    /**
     * Slot view bound to the alert stores.
     * @param props Slot props.
     * @return Element.
     */
    function AlertsSlot(props: WebSlotProps) {
      return createElement(AlertsPanel, {ctx: props.ctx, state, acked});
    }
    const view: WebView = AlertsSlot;
    /**
     * Slot view for notification subscriptions.
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
      for (const dispose of disposers.splice(0).reverse()) dispose();
    };
  },
};
