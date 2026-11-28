/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Expected closures, opening grace and genuine feed starvation. */

import {createBarCalendar, type BarCalendar} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  chartFeedState,
  type ChartFeedState,
  type ChartFeedStatusInput,
} from './feed-status';

/** Taiwan's regular sessions; fetched holiday overrides are tested below. */
const TAIWAN = createBarCalendar('XTAI');
/** Exchange-confirmed Thanksgiving closure and the following early close. */
const US_HOLIDAY = createBarCalendar('XNYS', {
  sessions: [
    {
      day: '2026-11-27',
      open_ms: Date.parse('2026-11-27T14:30:00Z'),
      close_ms: Date.parse('2026-11-27T18:00:00Z'),
    },
  ],
  coverage: [
    {
      fromMs: Date.parse('2026-11-26T05:00:00Z'),
      toMs: Date.parse('2026-11-29T05:00:00Z'),
    },
  ],
});

/**
 * Derives a real-session display state for a quiet, otherwise healthy chart.
 * @param calendar Instrument's actual session calendar.
 * @param now ISO timestamp with a UTC offset for the simulated clock.
 * @param received Last update as an ISO timestamp with a UTC offset.
 * @param overrides Optional transport, readiness or replay changes.
 * @return Derived presentation state; no backend risk state is changed.
 */
function stateAt(
  calendar: BarCalendar,
  now: string,
  received: string,
  overrides: Partial<ChartFeedStatusInput> = {}
): ChartFeedState {
  return chartFeedState({
    feedState: 'POLL',
    ready: true,
    replaying: false,
    calendar,
    nowMs: Date.parse(now),
    lastUpdateMs: Date.parse(received),
    ...overrides,
  });
}

describe('session-aware chart freshness', () => {
  it.each([
    '2026-11-27T05:30:00Z',
    '2026-11-27T12:00:00Z',
    '2026-11-28T02:00:00Z',
    '2026-11-30T00:59:00Z',
  ])(
    'treats the close, overnight, weekend and pre-open as expected silence at %s',
    now => {
      expect(stateAt(TAIWAN, now, '2026-11-27T05:29:00Z')).toBe('CLOSED');
    }
  );

  it('honors fetched holiday and early-close sessions instead of weekday hours alone', () => {
    expect(
      stateAt(US_HOLIDAY, '2026-11-26T16:00:00Z', '2026-11-25T21:00:00Z')
    ).toBe('CLOSED');
    expect(
      stateAt(US_HOLIDAY, '2026-11-27T18:01:00Z', '2026-11-27T17:59:00Z')
    ).toBe('CLOSED');
    expect(
      stateAt(US_HOLIDAY, '2026-11-27T17:00:00Z', '2026-11-27T16:58:00Z')
    ).toBe('STALE');
  });

  it('allows the first minute after reopening, then warns until a new update arrives', () => {
    const previousClose = '2026-11-27T05:29:00Z';
    expect(stateAt(TAIWAN, '2026-11-30T01:00:30Z', previousClose)).toBe(
      'CONNECTING'
    );
    expect(stateAt(TAIWAN, '2026-11-30T01:01:01Z', previousClose)).toBe(
      'STALE'
    );
    expect(
      stateAt(TAIWAN, '2026-11-30T01:01:10Z', '2026-11-30T01:01:05Z')
    ).toBe('POLL');
  });

  it('keeps stale-data detection on continuous markets even on weekends', () => {
    const crypto = createBarCalendar('24x7');
    expect(
      stateAt(crypto, '2026-11-28T02:00:00Z', '2026-11-28T01:58:00Z')
    ).toBe('STALE');
    expect(
      stateAt(crypto, '2026-11-28T02:00:00Z', '2026-11-28T01:59:30Z', {
        feedState: 'LIVE',
      })
    ).toBe('LIVE');
  });

  it('preserves replay, synthetic data and genuine unavailable/loading states', () => {
    const now = '2026-11-28T02:00:00Z';
    const received = '2026-11-27T05:29:00Z';
    expect(stateAt(TAIWAN, now, received, {feedState: 'STALE'})).toBe('CLOSED');
    expect(stateAt(TAIWAN, now, received, {feedState: 'CONNECTING'})).toBe(
      'CLOSED'
    );
    expect(stateAt(TAIWAN, now, received, {feedState: 'UNAVAILABLE'})).toBe(
      'UNAVAILABLE'
    );
    expect(
      stateAt(TAIWAN, now, received, {ready: false, feedState: 'CONNECTING'})
    ).toBe('CONNECTING');
    expect(stateAt(TAIWAN, now, received, {feedState: 'DEMO'})).toBe('DEMO');
    expect(stateAt(TAIWAN, now, received, {replaying: true})).toBe('REPLAY');
    expect(
      stateAt(TAIWAN, '2026-11-30T02:00:00Z', '2026-11-30T01:59:50Z', {
        feedState: 'STALE',
      })
    ).toBe('STALE');
  });
});
