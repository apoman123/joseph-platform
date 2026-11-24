/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Session markets on the time axis: the index has one slot per
 * bar (no overnight or weekend gap), off-data times are counted in session
 * bars, labels and separators use exchange time, and `24x7` charts keep the
 * duration-based arithmetic and UTC labels.
 */

import {
  CONTINUOUS_CALENDAR,
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {sessionBoundaries, sessionLabel} from './session-separators';
import {TimeIndex} from './time-index';
import {
  zoneAbbreviation,
  zonedCrosshairLabel,
  zonedTickLabel,
} from './time-scale';

const NY = 'America/New_York';
const XNYS = createBarCalendar('XNYS');

/**
 * New York wall time.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return Epoch ms.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(dayNumberOf(date), hour * 60 + minute, NY);
}

/** Friday and Monday 1h session bars (7 each; the last is 15:30-16:00). */
const BARS = [
  ...[0, 1, 2, 3, 4, 5, 6].map(i => ({
    timeMs: XNYS.shift('1h', ny('2026-11-20', 9, 30), i),
  })),
  ...[0, 1, 2, 3, 4, 5, 6].map(i => ({
    timeMs: XNYS.shift('1h', ny('2026-11-23', 9, 30), i),
  })),
];

describe('session-aware time index', () => {
  const index = new TimeIndex(BARS, 3_600_000, {
    calendar: XNYS,
    timeframe: '1h',
  });

  it('puts Monday 09:30 right after Friday 15:30 (no weekend gap)', () => {
    expect(index.sessionAware).toBe(true);
    expect(index.toLogical(ny('2026-11-20', 15, 30))).toBe(6);
    expect(index.toLogical(ny('2026-11-23', 9, 30))).toBe(7);
    // Saturday maps to the start of the next bar on the chart.
    expect(index.toLogical(ny('2026-11-21', 12))).toBe(7);
    // The short last bar is half an hour long.
    expect(index.toLogical(ny('2026-11-20', 15, 45))).toBe(6.5);
    expect(index.toTime(6.5)).toBe(ny('2026-11-20', 15, 45));
  });

  it('counts future times in session bars', () => {
    // Tuesday 10:00 is one session (7 bars) plus half a bar after Monday 09:30.
    expect(index.toLogical(ny('2026-11-24', 10))).toBe(13 + 1.5);
    expect(index.toTime(13 + 1.5)).toBe(ny('2026-11-24', 10));
    expect(index.snap(ny('2026-11-24', 10, 10))).toBe(ny('2026-11-24', 9, 30));
    // Thursday after Thanksgiving week's Wednesday: the fallback calendar
    // has no holidays, so Thursday 09:30 is 3 sessions after Monday.
    expect(index.toTime(7 + 21)).toBe(ny('2026-11-26', 9, 30));
  });

  it('counts history before the data in session bars', () => {
    // Thursday 15:30 is the bar right before Friday's open.
    expect(index.toLogical(ny('2026-11-19', 15, 30))).toBe(-1);
    expect(index.toTime(-1)).toBe(ny('2026-11-19', 15, 30));
    expect(index.toTime(-7)).toBe(ny('2026-11-19', 9, 30));
  });

  it('keeps duration arithmetic for 24x7 markets', () => {
    const utc = new TimeIndex([{timeMs: 0}, {timeMs: 60_000}], 60_000, {
      calendar: CONTINUOUS_CALENDAR,
      timeframe: '1m',
    });
    expect(utc.sessionAware).toBe(false);
    expect(utc.toLogical(600_000)).toBe(10);
    expect(utc.toTime(-2)).toBe(-120_000);
    expect(utc.toLogical(30_000)).toBe(0.5);
  });
});

describe('exchange-local labels', () => {
  it('labels ticks and the crosshair in exchange time', () => {
    const open = ny('2026-11-23', 9, 30);
    expect(zonedTickLabel(open, 'time', NY)).toBe('09:30');
    expect(zonedTickLabel(open, 'day', NY)).toBe('23');
    expect(zonedTickLabel(open, 'month', NY)).toBe('Nov');
    expect(zonedTickLabel(open, 'year', NY)).toBe('2026');
    expect(zonedTickLabel(open + 5000, 'seconds', NY)).toBe('09:30:05');
    expect(zonedCrosshairLabel(open, '1h', NY)).toBe("23 Nov '26 09:30 ET");
    expect(zonedCrosshairLabel(open, '1d', NY)).toBe("23 Nov '26");
    expect(zoneAbbreviation('Asia/Taipei')).toBe('TPE');
    expect(zoneAbbreviation('Europe/London')).toBe('London');
  });

  it('starts a separator at every session in exchange time', () => {
    const times = BARS.map(bar => bar.timeMs);
    expect(sessionBoundaries(times, '1h', NY)).toEqual([
      ny('2026-11-23', 9, 30),
    ]);
    expect(sessionLabel(ny('2026-11-23', 9, 30), 'day', NY)).toBe('Mon 23');
    // An NYSE session lies within one UTC day, so UTC keys agree here.
    expect(sessionBoundaries(times, '1h')).toEqual([ny('2026-11-23', 9, 30)]);
    // TWSE: sessions start at 09:00 Taipei (01:00 UTC).
    const tpe = (date: string, hour: number) =>
      zonedTimeToUtc(dayNumberOf(date), hour * 60, 'Asia/Taipei');
    expect(
      sessionBoundaries(
        [tpe('2026-11-20', 13), tpe('2026-11-23', 9)],
        '1h',
        'Asia/Taipei'
      )
    ).toEqual([tpe('2026-11-23', 9)]);
    expect(sessionLabel(tpe('2026-11-23', 9), 'week', 'Asia/Taipei')).toBe(
      'Mon 23 Nov'
    );
  });
});
