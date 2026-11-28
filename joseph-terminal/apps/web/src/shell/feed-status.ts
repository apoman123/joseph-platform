/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Session-aware chart freshness without changing execution gates. */

import {
  marketStatus,
  type BarCalendar,
  type FeedState,
} from '@joseph/contracts';

/** Display provenance; CLOSED is presentation only, never a backend feed state. */
export type ChartFeedState = FeedState | 'CLOSED';

/** Allowed quiet time during an open session, including the opening grace. */
export const CHART_STALE_AFTER_MS = 60_000;

/** Inputs for the displayed freshness of one chart. */
export interface ChartFeedStatusInput {
  /** Backend-reported provenance, including real transport failures. */
  feedState: FeedState;
  /** Instrument's exchange calendar, including fetched holidays and sessions. */
  calendar: BarCalendar;
  /** Current UTC epoch milliseconds. */
  nowMs: number;
  /** Last received chart update in UTC milliseconds; null before data arrives. */
  lastUpdateMs: number | null;
  /** Whether chart candles or the market snapshot have loaded successfully. */
  ready: boolean;
  /** Whether the chart is replaying historical bars. */
  replaying: boolean;
}

/**
 * Classifies expected session silence separately from stale open-market data.
 * @param input Current chart, clock and session information.
 * @return Display state; the underlying quote and execution state is unchanged.
 */
export function chartFeedState(input: ChartFeedStatusInput): ChartFeedState {
  const {feedState, calendar, nowMs, lastUpdateMs, ready, replaying} = input;
  if (replaying) return 'REPLAY';
  if (!ready || ['DEMO', 'UNAVAILABLE'].includes(feedState)) {
    return feedState;
  }
  const status = marketStatus(calendar, nowMs);
  if (!status.open) return 'CLOSED';
  if (
    status.session &&
    (lastUpdateMs === null || lastUpdateMs < status.session.open_ms)
  ) {
    return nowMs - status.session.open_ms <= CHART_STALE_AFTER_MS
      ? 'CONNECTING'
      : 'STALE';
  }
  if (lastUpdateMs === null || nowMs - lastUpdateMs > CHART_STALE_AFTER_MS) {
    return 'STALE';
  }
  return feedState;
}
