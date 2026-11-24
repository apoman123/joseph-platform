/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Data health feature: venue feed status in the status bar
 * and a bottom-panel table with transport, lag, gaps and capabilities.
 */

import type {VenueStatus} from '@joseph/contracts';
import {VENUE_NAMES} from '@joseph/contracts';
import type {ReadableStore} from '@joseph/feature-registry';
import type {TerminalRuntime} from '../../composition/runtime';
import {
  useStore,
  type WebFeature,
  type WebSlotProps,
} from '../../shell/react-bindings';
import {formatAge, formatUtcTime} from '../../shell/format';
import {createLazyStore} from '../shell/lazy-store';
import {shellManifest} from '../shell/manifest';

/** Venue poll interval. */
const POLL_MS = 15_000;

/** Loaded venue data. */
interface VenueData {
  /** Venues, or null while loading. */
  venues: readonly VenueStatus[] | null;
  /** Error message when the request failed. */
  error: string | null;
}

/**
 * Health badge class for a venue.
 * @param status Venue status.
 * @return Badge modifier.
 */
function badgeFor(status: VenueStatus): string {
  const state = status.health.feed_state;
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
 * Data health feature.
 * @param _runtime Runtime (unused; the feature uses its context only).
 * @return Feature.
 */
export function dataHealthFeature(_runtime: TerminalRuntime): WebFeature {
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
       * Bottom-panel table.
       * @param _props Slot props (unused).
       * @return Table view.
       */
      function DataHealthPanel(_props: WebSlotProps) {
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
        return (
          <table className="j-table" aria-label="Venue data health">
            <thead>
              <tr>
                <th>Venue</th>
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
              {venues.map(status => {
                const md = status.capabilities.market_data;
                const native = [
                  md.native_ws_candles && 'candles',
                  md.native_ws_trades && 'trades',
                  md.native_ws_book && 'book',
                ]
                  .filter(Boolean)
                  .join(', ');
                return (
                  <tr key={status.capabilities.venue}>
                    <td>{status.capabilities.display_name}</td>
                    <td>
                      <span className={`j-badge ${badgeFor(status)}`}>
                        {status.health.feed_state}
                      </span>
                    </td>
                    <td>
                      {status.health.transport.replace('_', ' ').toLowerCase()}
                    </td>
                    <td>{status.health.state.toLowerCase()}</td>
                    <td className="j-num">
                      {status.health.lag_ms == null
                        ? '—'
                        : `${status.health.lag_ms} ms`}
                    </td>
                    <td className="j-num">{status.health.reconnects}</td>
                    <td className="j-num">{status.health.gaps_detected}</td>
                    <td>
                      {status.health.last_message_ms
                        ? `${formatAge(now - status.health.last_message_ms)} ago`
                        : '—'}
                    </td>
                    <td>{native || 'none (polling only)'}</td>
                    <td className="j-muted">
                      {status.health.last_error ??
                        status.capabilities.notes[0] ??
                        ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        );
      }

      /**
       * Status-bar venue dots.
       * @param _props Slot props (unused).
       * @return Inline status.
       */
      function VenueDots(_props: WebSlotProps) {
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
            {(venues ?? []).map(status => (
              <span
                key={status.capabilities.venue}
                className={`venue-dot venue-dot--${status.health.feed_state.toLowerCase()}`}
                title={`${VENUE_NAMES[status.capabilities.venue]}: ${status.health.feed_state}, ${status.health.state.toLowerCase()}, as of ${formatUtcTime(status.health.as_of_ms, true)}`}
              >
                {VENUE_NAMES[status.capabilities.venue]}
              </span>
            ))}
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
