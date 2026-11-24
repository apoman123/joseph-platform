/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Replay point-in-time and the bar countdown on session
 * calendars: the cursor bar closes at the calendar's bar end, the countdown
 * runs to the bar close in session and to the next open between sessions,
 * and `24x7` charts keep today's results.
 */

import {
  CONTINUOUS_CALENDAR,
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {replayAsOfMs} from './replay';
import {barCountdown, msUntilBarClose} from './timeframes';

// New York Stock Exchange calendar (market identifier code XNYS).
const XNYS = createBarCalendar('XNYS');

/**
 * Converts a New York wall-clock time to UTC.
 * @param date Calendar date, `YYYY-MM-DD`.
 * @param hour Hour of the day, 0 to 23.
 * @param minute Minute of the hour.
 * @return UTC epoch ms.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(
    dayNumberOf(date),
    hour * 60 + minute,
    'America/New_York'
  );
}

describe('replay on session calendars', () => {
  it('closes the cursor bar at the session close', () => {
    const state = {
      phase: 'paused' as const,
      cursorMs: ny('2026-11-23', 15, 30),
      speed: 1,
    };
    expect(replayAsOfMs(state, '1h', XNYS)).toBe(ny('2026-11-23', 16));
    expect(replayAsOfMs(state, '1h')).toBe(ny('2026-11-23', 16, 30));
    expect(replayAsOfMs(state, '1h', CONTINUOUS_CALENDAR)).toBe(
      ny('2026-11-23', 16, 30)
    );
    expect(
      replayAsOfMs({...state, cursorMs: ny('2026-11-23', 9, 30)}, '1d', XNYS)
    ).toBe(ny('2026-11-23', 16));
    expect(replayAsOfMs({...state, cursorMs: null}, '1h', XNYS)).toBeNull();
    expect(replayAsOfMs(state, 60_000, XNYS)).toBe(ny('2026-11-23', 15, 31));
  });
});

describe('bar countdown on session calendars', () => {
  it('counts to the bar close in session and the next open after it', () => {
    expect(msUntilBarClose('1h', ny('2026-11-23', 15, 50), XNYS)).toBe(600_000);
    expect(barCountdown('1h', ny('2026-11-23', 15, 50), XNYS)).toEqual({
      kind: 'closes',
      ms: 600_000,
    });
    // Friday 17:00: next open is Monday 09:30.
    const friday = ny('2026-11-20', 17);
    expect(msUntilBarClose('1h', friday, XNYS)).toBe(0);
    expect(barCountdown('5m', friday, XNYS)).toEqual({
      kind: 'opens',
      ms: ny('2026-11-23', 9, 30) - friday,
    });
    // The weekly bar closed on Friday, so Saturday counts to Monday's open;
    // mid-week after the close it still counts to Friday's close.
    expect(barCountdown('1w', ny('2026-11-21', 12), XNYS).kind).toBe('opens');
    expect(barCountdown('1w', ny('2026-11-18', 20), XNYS)).toEqual({
      kind: 'closes',
      ms: ny('2026-11-20', 16) - ny('2026-11-18', 20),
    });
  });

  it('keeps UTC bars for 24x7 markets', () => {
    expect(barCountdown('1h', 3_600_000 * 5 + 60_000)).toEqual({
      kind: 'closes',
      ms: 3_540_000,
    });
    expect(msUntilBarClose('1h', 60_000, CONTINUOUS_CALENDAR)).toBe(3_540_000);
  });
});
