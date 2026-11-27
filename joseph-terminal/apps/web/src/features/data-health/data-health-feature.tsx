/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Data health feature: provider status in the status bar
 * and a bottom-panel table with transport, lag, gaps and capabilities.
 */

import type {VenueStatus} from '@joseph/contracts';
import type {ReadableStore} from '@joseph/feature-registry';
import {useStore, type WebFeature} from '../../shell/react-bindings';
import {formatAge, formatUtcTime} from '../../shell/format';
import {createLazyStore} from '../shell/lazy-store';
import {shellManifest} from '../shell/manifest';

/** Venue status poll interval in milliseconds. */
const POLL_MS = 3_000;

/** Loaded venue data. */
interface VenueData {
  /** Venues, or null while loading. */
  venues: readonly VenueStatus[] | null;
  /** Error message when the request failed. */
  error: string | null;
}

/**
 * Returns the badge modifier class for a venue's feed state.
 * @param status Venue status.
 * @return A `j-badge--*` modifier; missing keys and unavailable venues
 *     read as stale, and any other unlisted state as muted.
 */
function badgeFor(status: VenueStatus): string {
  if (
    status.capabilities.requires_credentials &&
    !status.capabilities.credentials_configured
  ) {
    return 'j-badge--stale';
  }
  const state = visibleFeedState(status);
  if (state === 'DEMO') return 'j-badge--demo';
  if (state === 'LIVE') return 'j-badge--live';
  if (state === 'POLL') return 'j-badge--poll';
  if (state === 'REPLAY') return 'j-badge--replay';
  if (state === 'STALE' || status.health.state === 'UNAVAILABLE') {
    return 'j-badge--stale';
  }
  return 'j-badge--muted';
}

/**
 * Names the configured market-data source behind a venue row.
 * @param status Venue capability and health record.
 * @return Provider name, including optional free-key sources when active.
 */
export function feedSourceFor(status: VenueStatus): string {
  const venue = status.capabilities.venue;
  if (venue === 'mock') return 'Synthetic demo';
  if (
    venue === 'binance' ||
    venue === 'hyperliquid' ||
    venue === 'lighter' ||
    venue === 'bitget' ||
    venue === 'mexc'
  ) {
    return status.capabilities.display_name.startsWith('CCXT')
      ? 'CCXT'
      : `${venue === 'binance' ? 'Binance' : status.capabilities.display_name} public API`;
  }
  if (['bybit', 'okx', 'coinbase', 'kraken', 'deriv'].includes(venue)) {
    return status.capabilities.display_name;
  }
  if (venue === 'nasdaq' || venue === 'nyse' || venue === 'cboe') {
    return 'Alpaca IEX';
  }
  if (venue === 'twse' || venue === 'tpex') {
    return 'Fugle';
  }
  if (venue === 'treasury') {
    return status.capabilities.notes.some(note => note.startsWith('FRED_'))
      ? 'U.S. Treasury · FRED'
      : 'U.S. Treasury';
  }
  return 'Unknown data feed';
}

/**
 * Keeps only selected providers and demo when no real feed is available.
 * @param venues Status records returned by the market engine.
 * @return Selected provider records, or demo in demo-only mode.
 */
export function displayedFeeds(
  venues: readonly VenueStatus[]
): readonly VenueStatus[] {
  const allowed = venues.filter(row => {
    const venue = row.capabilities.venue;
    if (venue === 'mock') return false;
    if (venue === 'twse' || venue === 'tpex') {
      return row.capabilities.display_name.includes('Fugle');
    }
    return feedSourceFor(row) !== 'Unknown data feed';
  });
  return allowed.length > 0
    ? allowed
    : venues.filter(row => row.capabilities.venue === 'mock');
}

/** A data provider and its underlying market venues. */
interface ProviderFeed {
  /** Provider label displayed once in the Dashboard. */
  name: string;
  /** Venue statuses represented by this provider. */
  statuses: readonly VenueStatus[];
}

/**
 * Groups venue health by the actual data provider.
 * @param venues Raw health returned by the market engine.
 * @return One row per supported provider.
 */
export function groupedFeeds(venues: readonly VenueStatus[]): ProviderFeed[] {
  const groups = new Map<string, VenueStatus[]>();
  for (const status of displayedFeeds(venues)) {
    const name = feedSourceFor(status);
    groups.set(name, [...(groups.get(name) ?? []), status]);
  }
  return [...groups].map(([name, statuses]) => ({name, statuses}));
}

/**
 * Gives an idle REST provider a truthful status until data is requested.
 * @param status One venue health record.
 * @return Visible status text.
 */
export function visibleFeedState(status: VenueStatus): string {
  if (
    status.capabilities.requires_credentials &&
    !status.capabilities.credentials_configured
  ) {
    return 'KEY REQUIRED';
  }
  if (
    status.health.feed_state === 'CONNECTING' &&
    status.health.last_message_ms == null &&
    status.health.transport === 'NONE'
  ) {
    return 'IDLE';
  }
  if (
    status.health.feed_state === 'LIVE' &&
    status.health.transport === 'REST_POLL'
  ) {
    return 'POLL';
  }
  return status.health.feed_state;
}

/**
 * Summarizes the asset class covered by a configured provider.
 * @param name Provider label.
 * @return Short market coverage shown in the health table.
 */
function coverageFor(name: string): string {
  if (name === 'CCXT') return 'Crypto and RWA perpetuals · 5 exchanges';
  if (name.startsWith('Deriv')) {
    return 'FX, commodities, indices and crypto reference quotes';
  }
  if (name.includes('public API')) return 'Exchange markets · public source';
  if (name === 'Fugle') return 'Taiwan stocks and ETFs';
  if (name === 'Alpaca IEX') return 'US stocks and ETFs';
  if (name.startsWith('U.S. Treasury')) return 'US Treasury rates';
  return 'Demo instruments';
}

/**
 * Selects the most recently active venue for a provider status row.
 * @param feed Provider group with at least one venue.
 * @return Venue with the newest health message, or its first venue.
 * @throws Error if the provider has no venue health records.
 */
function currentStatus(feed: ProviderFeed): VenueStatus {
  const current = [...feed.statuses].sort(
    (a, b) => (b.health.last_message_ms ?? 0) - (a.health.last_message_ms ?? 0)
  )[0];
  if (!current) throw new Error('Data feed has no venue health');
  return current;
}

/**
 * Creates the data health feature. It polls the venue list every
 * `POLL_MS` while a view is mounted.
 * @return Feature contributing the bottom-panel table and status-bar dots.
 */
export function dataHealthFeature(): WebFeature {
  return {
    manifest: shellManifest(
      'data-health',
      'Data health',
      'Feed transport, lag, gaps and capabilities per venue.',
      {
        uiSlots: ['bottom-panel', 'status-bar'],
        requiredCapabilities: ['market'],
        permissions: ['market:read'],
      }
    ),
    activate(ctx) {
      const data: ReadableStore<VenueData> = createLazyStore<VenueData>(
        {venues: null, error: null},
        set => {
          let alive = true;
          const load = () =>
            ctx.clients.market
              .listVenues()
              .then(
                list => alive && void set({venues: list.venues, error: null})
              )
              .catch(
                (error: unknown) =>
                  alive &&
                  void set({
                    venues: null,
                    error:
                      error instanceof Error ? error.message : 'Request failed',
                  })
              );
          void load();
          const timer = setInterval(() => void load(), POLL_MS);
          return () => {
            alive = false;
            clearInterval(timer);
          };
        }
      );

      /**
       * Renders the bottom-panel venue table.
       * @return The table, a loading line, or an alert when the request
       *     failed.
       */
      function DataHealthPanel() {
        const {venues, error} = useStore(data);
        if (error) {
          return (
            <div className="j-empty" role="alert">
              <strong>Venue status is unavailable</strong>
              <p>
                {error}. Check that the market engine is running and reachable
                from this browser.
              </p>
            </div>
          );
        }
        if (!venues) {
          return (
            <p className="j-muted" style={{padding: 12}}>
              Loading venue status…
            </p>
          );
        }
        const now = Date.now();
        const feeds = groupedFeeds(venues);
        return (
          <table className="j-table" aria-label="Venue data health">
            <thead>
              <tr>
                <th>Markets</th>
                <th>Data feed</th>
                <th>Feed</th>
                <th>Transport</th>
                <th>Health</th>
                <th className="j-num">Lag</th>
                <th className="j-num">Reconnects</th>
                <th className="j-num">Gaps</th>
                <th>Last message</th>
                <th>Native streams</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {feeds.map(feed => {
                const status = currentStatus(feed);
                const md = status.capabilities.market_data;
                const native = [
                  md.native_ws_candles && 'candles',
                  md.native_ws_trades && 'trades',
                  md.native_ws_book && 'book',
                ]
                  .filter(Boolean)
                  .join(', ');
                return (
                  <tr key={feed.name}>
                    <td>{coverageFor(feed.name)}</td>
                    <td>{feed.name}</td>
                    <td>
                      <span className={`j-badge ${badgeFor(status)}`}>
                        {visibleFeedState(status)}
                      </span>
                    </td>
                    <td>
                      {visibleFeedState(status) === 'IDLE'
                        ? 'on demand'
                        : status.health.transport
                            .replace('_', ' ')
                            .toLowerCase()}
                    </td>
                    <td>
                      {visibleFeedState(status) === 'IDLE'
                        ? 'idle'
                        : status.health.state.toLowerCase()}
                    </td>
                    <td className="j-num">
                      {status.health.lag_ms == null
                        ? '—'
                        : `${status.health.lag_ms} ms`}
                    </td>
                    <td className="j-num">
                      {feed.statuses.reduce(
                        (sum, row) => sum + row.health.reconnects,
                        0
                      )}
                    </td>
                    <td className="j-num">
                      {feed.statuses.reduce(
                        (sum, row) => sum + row.health.gaps_detected,
                        0
                      )}
                    </td>
                    <td>
                      {status.health.last_message_ms
                        ? `${formatAge(now - status.health.last_message_ms)} ago`
                        : '—'}
                    </td>
                    <td>{native || 'none (polling only)'}</td>
                    <td className="j-muted">
                      {status.capabilities.requires_credentials &&
                      !status.capabilities.credentials_configured
                        ? 'Configure the data-feed key in the market engine.'
                        : visibleFeedState(status) === 'IDLE'
                          ? 'Starts when market data is requested.'
                          : status.health.last_error
                            ? status.health.state === 'HEALTHY'
                              ? `Previous issue: ${status.health.last_error}`
                              : status.health.last_error
                            : (status.capabilities.notes[0] ?? '')}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        );
      }

      /**
       * Renders one status-bar dot per venue, coloured by feed state.
       * @return Inline status; empty while loading.
       */
      function VenueDots() {
        const {venues, error} = useStore(data);
        if (error) {
          return (
            <span className="statusbar__item j-neg">
              Market engine unreachable
            </span>
          );
        }
        return (
          <span className="statusbar__item" aria-label="Venue feeds">
            {groupedFeeds(venues ?? []).map(feed => {
              const status = currentStatus(feed);
              const state = visibleFeedState(status);
              return (
                <span
                  key={feed.name}
                  className={`venue-dot venue-dot--${state.toLowerCase().replace(' ', '-')}`}
                  title={`${feed.name}: ${state}, ${state === 'IDLE' ? 'waiting for a request' : status.health.state.toLowerCase()}, as of ${formatUtcTime(status.health.as_of_ms, true)}`}
                >
                  {feed.name}
                </span>
              );
            })}
          </span>
        );
      }

      ctx.slots.contribute('bottom-panel', {
        id: 'data-health',
        title: 'Data health',
        order: 90,
        icon: 'activity',
        view: DataHealthPanel,
      });
      ctx.slots.contribute('status-bar', {
        id: 'venue-dots',
        title: 'Venue feeds',
        order: 10,
        view: VenueDots,
      });
      return () => undefined;
    },
  };
}
