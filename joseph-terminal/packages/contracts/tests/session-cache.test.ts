/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Session-list cache: chunked, de-duplicated loading from the
 * engine endpoint, merged coverage, prefetch margins, failure back-off,
 * update notifications and the regular-hours fallback.
 */

import {describe, expect, it, vi} from 'vitest';
import {
  CONTINUOUS_CALENDAR,
  EXCHANGE_HOURS,
  MAX_REQUEST_DAYS,
  SESSION_CHUNK_MS,
  SESSION_RETRY_MS,
  SessionCalendarCache,
  dayNumberOf,
  dayText,
  weekdayOf,
  zonedTimeToUtc,
  type SessionList,
  type SessionQuery,
  type TradingSession,
} from '../src/index';

/**
 * NYSE session list for a window, closed on 2026-11-26 (Thanksgiving).
 * @param query Calendar and window.
 * @return Session list.
 */
function nyseList(query: SessionQuery): SessionList {
  const hours = EXCHANGE_HOURS.XNYS;
  const sessions: TradingSession[] = [];
  const first = Math.floor(query.fromMs / 86_400_000) - 1;
  const last = Math.floor(query.toMs / 86_400_000) + 1;
  for (let day = first; day <= last; day += 1) {
    const weekday = weekdayOf(day);
    if (weekday === 0 || weekday === 6 || dayText(day) === '2026-11-26') {
      continue;
    }
    const open = zonedTimeToUtc(day, hours.openMinute, hours.timeZone);
    const close = zonedTimeToUtc(day, hours.closeMinute, hours.timeZone);
    if (close > query.fromMs && open < query.toMs) {
      sessions.push({day: dayText(day), open_ms: open, close_ms: close});
    }
  }
  return {
    calendar: query.calendar,
    sessions,
    from_ms: query.fromMs,
    to_ms: query.toMs,
  };
}

const THANKSGIVING_10AM = zonedTimeToUtc(
  dayNumberOf('2026-11-26'),
  600,
  'America/New_York'
);

describe('session calendar cache', () => {
  it('answers 24x7 without fetching and falls back to regular hours', async () => {
    const fetcher = vi.fn(async (query: SessionQuery) => nyseList(query));
    const cache = new SessionCalendarCache(fetcher);
    expect(cache.calendar('24x7')).toBe(CONTINUOUS_CALENDAR);
    expect(await cache.ensure('24x7', 0, 1e12)).toBe(CONTINUOUS_CALENDAR);
    expect(cache.covered('24x7', 0, 1)).toBe(true);
    // Before loading, Thanksgiving looks like a regular weekday.
    expect(cache.calendar('XNYS').isOpen(THANKSGIVING_10AM)).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('loads epoch-aligned chunks once and applies the list', async () => {
    const fetcher = vi.fn(async (query: SessionQuery) => nyseList(query));
    const cache = new SessionCalendarCache(fetcher);
    const updates: string[] = [];
    cache.subscribe(id => updates.push(id));
    const from = THANKSGIVING_10AM - 5 * 86_400_000;
    const to = THANKSGIVING_10AM + 5 * 86_400_000;
    const [first, second] = await Promise.all([
      cache.ensure('XNYS', from, to),
      cache.ensure('XNYS', from, to),
    ]);
    expect(first).toBe(second);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const query = fetcher.mock.calls[0]?.[0];
    expect((query?.fromMs ?? 1) % SESSION_CHUNK_MS).toBe(0);
    expect((query?.toMs ?? 1) % SESSION_CHUNK_MS).toBe(0);
    expect(cache.covered('XNYS', from, to)).toBe(true);
    expect(cache.calendar('XNYS').isOpen(THANKSGIVING_10AM)).toBe(false);
    expect(updates).toEqual(['XNYS']);
    await cache.ensure('XNYS', from, to);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('splits long windows into requests the engine serves', async () => {
    const fetcher = vi.fn(async (query: SessionQuery) => nyseList(query));
    const cache = new SessionCalendarCache(fetcher);
    await cache.ensure('XNYS', 0, 3 * MAX_REQUEST_DAYS * 86_400_000);
    for (const [query] of fetcher.mock.calls) {
      expect(query.toMs - query.fromMs).toBeLessThanOrEqual(
        MAX_REQUEST_DAYS * 86_400_000
      );
    }
    expect(fetcher.mock.calls.length).toBeGreaterThan(2);
  });

  it('backs off after a failure and retries later', async () => {
    let now = 1_000_000;
    const fetcher = vi.fn(async (query: SessionQuery) => {
      if (fetcher.mock.calls.length === 1) throw new Error('offline');
      return nyseList(query);
    });
    const cache = new SessionCalendarCache(fetcher, {now: () => now});
    const calendar = await cache.ensure('XNYS', 0, 1000);
    expect(calendar.continuous).toBe(false);
    expect(cache.covered('XNYS', 0, 1000)).toBe(false);
    await cache.ensure('XNYS', 0, 1000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    now += SESSION_RETRY_MS + 1;
    await cache.ensure('XNYS', 0, 1000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(cache.covered('XNYS', 0, 1000)).toBe(true);
  });

  it('prefetches with a margin and isolates faulty listeners', async () => {
    const fetcher = vi.fn(async (query: SessionQuery) => nyseList(query));
    const cache = new SessionCalendarCache(fetcher);
    const off = cache.subscribe(() => {
      throw new Error('listener bug');
    });
    const seen: string[] = [];
    cache.subscribe(id => seen.push(id));
    cache.prefetch('USBOND', SESSION_CHUNK_MS * 10, SESSION_CHUNK_MS * 10 + 1);
    await vi.waitFor(() => void expect(seen).toEqual(['USBOND']));
    const query = fetcher.mock.calls[0]?.[0];
    expect(query?.fromMs).toBeLessThan(SESSION_CHUNK_MS * 10);
    off();
    expect(new SessionCalendarCache(null).calendar('XTAI').id).toBe('XTAI');
    expect(
      await new SessionCalendarCache(null).ensure('XTAI', 0, 1)
    ).toBeDefined();
  });
});
