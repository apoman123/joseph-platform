/**
 * @fileoverview Watchlist feature: exact-venue instruments with live
 * prices; selecting a row changes the active chart.
 */
import {createStore} from '@joseph/feature-registry';
import {createElement} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {WatchlistPanel} from './watchlist-panel';

/** Persisted watchlist settings (schema version 1). */
export interface WatchlistSettings {
  /** Ordered instrument ids. */
  items: string[];
}

/** Default rows in DEMO mode. */
export const DEMO_WATCHLIST: readonly string[] = [
  'mock:perp:BTCUSD',
  'mock:perp:ETHUSD',
  'mock:perp:SOLUSD',
];

/** Default rows with a live backend (one contract per venue, never merged). */
export const LIVE_WATCHLIST: readonly string[] = [
  'binance:perp:BTCUSDT',
  'hyperliquid:perp:BTC',
  'binance:perp:ETHUSDT',
  'hyperliquid:perp:ETH',
  'bitget:perp:BTCUSDT',
];

/** The watchlist feature. */
export const WATCHLIST_FEATURE: WebFeature = {
  manifest: {
    id: 'watchlist',
    version: '1.0.0',
    owner: 'terminal-trading',
    displayName: 'Watchlist',
    description:
      'Exact-venue instruments with last price, 24h change, funding and data freshness.',
    uiSlots: ['side-panel'],
    requiredCapabilities: ['market'],
    permissions: ['market:read'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Saved watchlist rows remain in feature settings and reappear if the feature is restored.',
    },
  },
  activate(ctx) {
    const fallback: WatchlistSettings = {
      items: [...(ctx.clients.demo ? DEMO_WATCHLIST : LIVE_WATCHLIST)],
    };
    const stored = ctx.settings.read<WatchlistSettings>(fallback);
    const items = createStore<readonly string[]>(
      Array.isArray(stored.items) ? stored.items : fallback.items
    );
    const off = items.subscribe(value =>
      ctx.settings.write<WatchlistSettings>({items: [...value]})
    );
    /**
     * Slot view bound to the feature's item store.
     * @param props Slot props.
     * @return Element.
     */
    function WatchlistSlot(props: WebSlotProps) {
      return createElement(WatchlistPanel, {ctx: props.ctx, items});
    }
    const view: WebView = WatchlistSlot;
    const remove = ctx.slots.contribute('side-panel', {
      id: 'watchlist',
      title: 'Watchlist',
      order: 10,
      icon: 'list',
      view,
    });
    return () => {
      remove();
      off();
    };
  },
};
