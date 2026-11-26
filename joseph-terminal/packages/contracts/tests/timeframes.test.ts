/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Bar boundary arithmetic mirroring the engine's
 * `joseph.domain.timeframes`: seconds, weeks and calendar months
 * (February, leap years, December to January).
 */

import {describe, expect, it} from 'vitest';
import {
  TIMEFRAMES,
  TIMEFRAME_MS,
  barsBetween,
  floorToTimeframe,
  isCalendarTimeframe,
  isSecondsTimeframe,
  shiftTimeframe,
  timeframeEnd,
} from '../src/identity';

const DAY = 86_400_000;

describe('timeframe list', () => {
  it('orders seconds to the calendar month and keeps 1m and 1M apart', () => {
    expect(TIMEFRAMES[0]).toBe('1s');
    expect(TIMEFRAMES.slice(0, 5)).toEqual(['1s', '5s', '15s', '30s', '1m']);
    expect(TIMEFRAMES.at(-1)).toBe('1M');
    expect(TIMEFRAME_MS['1m']).toBe(60_000);
    expect(TIMEFRAME_MS['1M']).toBe(31 * DAY);
    const durations = TIMEFRAMES.map(tf => TIMEFRAME_MS[tf]);
    expect([...durations].sort((a, b) => a - b)).toEqual(durations);
  });

  it('classifies seconds and calendar timeframes', () => {
    expect(TIMEFRAMES.filter(isSecondsTimeframe)).toEqual([
      '1s',
      '5s',
      '15s',
      '30s',
    ]);
    expect(TIMEFRAMES.filter(isCalendarTimeframe)).toEqual(['1M']);
  });
});

describe('fixed-length bars', () => {
  it('floors seconds and minutes to UTC-aligned starts', () => {
    const t = Date.UTC(2026, 10, 22, 14, 3, 47, 250);
    expect(floorToTimeframe('1s', t)).toBe(Date.UTC(2026, 10, 22, 14, 3, 47));
    expect(floorToTimeframe('15s', t)).toBe(Date.UTC(2026, 10, 22, 14, 3, 45));
    expect(floorToTimeframe('30s', t)).toBe(Date.UTC(2026, 10, 22, 14, 3, 30));
    expect(floorToTimeframe('5m', t)).toBe(Date.UTC(2026, 10, 22, 14, 0));
    expect(floorToTimeframe('10m', t)).toBe(Date.UTC(2026, 10, 22, 14, 0));
    expect(floorToTimeframe('45m', t)).toBe(Date.UTC(2026, 10, 22, 13, 30));
    expect(floorToTimeframe('8h', t)).toBe(Date.UTC(2026, 10, 22, 8));
    expect(timeframeEnd('5s', Date.UTC(2026, 10, 22, 14, 3, 45))).toBe(
      Date.UTC(2026, 10, 22, 14, 3, 50)
    );
  });

  it('anchors weeks on Monday, including before the epoch', () => {
    const monday = Date.UTC(2026, 10, 16);
    expect(floorToTimeframe('1w', monday + 6 * DAY + 1)).toBe(monday);
    expect(floorToTimeframe('1w', Date.UTC(1969, 11, 31))).toBe(
      Date.UTC(1969, 11, 29)
    );
    expect(floorToTimeframe('1h', -1)).toBe(-3_600_000);
  });

  it('shifts and counts fixed bars', () => {
    const start = Date.UTC(2026, 0, 1);
    expect(shiftTimeframe('1h', start, -3)).toBe(start - 3 * 3_600_000);
    expect(barsBetween('15s', start, start + 60_000)).toBe(4);
    expect(barsBetween('15s', start, start + 60_001)).toBe(5);
    expect(barsBetween('1m', start, start)).toBe(0);
  });
});

describe('calendar months (1M)', () => {
  it('floors any instant to the 1st at 00:00 UTC', () => {
    expect(floorToTimeframe('1M', Date.UTC(2026, 1, 17, 9, 30))).toBe(
      Date.UTC(2026, 1, 1)
    );
    expect(floorToTimeframe('1M', Date.UTC(2026, 2, 1))).toBe(
      Date.UTC(2026, 2, 1)
    );
    expect(floorToTimeframe('1M', Date.UTC(2026, 2, 1) - 1)).toBe(
      Date.UTC(2026, 1, 1)
    );
  });

  it('ends February after 28 days, or 29 in a leap year', () => {
    const feb2026 = Date.UTC(2026, 1, 1);
    expect(timeframeEnd('1M', feb2026) - feb2026).toBe(28 * DAY);
    const feb2024 = Date.UTC(2024, 1, 1);
    expect(timeframeEnd('1M', feb2024)).toBe(Date.UTC(2024, 2, 1));
    expect(timeframeEnd('1M', feb2024) - feb2024).toBe(29 * DAY);
    // Century rule: 2100 is not a leap year, 2000 is.
    expect(timeframeEnd('1M', Date.UTC(2100, 1, 1))).toBe(
      Date.UTC(2100, 1, 1) + 28 * DAY
    );
    expect(timeframeEnd('1M', Date.UTC(2000, 1, 1))).toBe(
      Date.UTC(2000, 1, 1) + 29 * DAY
    );
  });

  it('gives 30 and 31 day months their own length', () => {
    expect(timeframeEnd('1M', Date.UTC(2026, 3, 1))).toBe(
      Date.UTC(2026, 3, 1) + 30 * DAY
    );
    expect(timeframeEnd('1M', Date.UTC(2026, 6, 1))).toBe(
      Date.UTC(2026, 6, 1) + 31 * DAY
    );
  });

  it('rolls December into January of the next year and back', () => {
    const dec = Date.UTC(2026, 11, 1);
    expect(timeframeEnd('1M', dec)).toBe(Date.UTC(2027, 0, 1));
    expect(shiftTimeframe('1M', Date.UTC(2027, 0, 1), -1)).toBe(dec);
    expect(shiftTimeframe('1M', dec, 14)).toBe(Date.UTC(2028, 1, 1));
    expect(shiftTimeframe('1M', Date.UTC(1970, 0, 1), -1)).toBe(
      Date.UTC(1969, 11, 1)
    );
  });

  it('counts month starts in a range', () => {
    const jan = Date.UTC(2026, 0, 1);
    expect(barsBetween('1M', jan, Date.UTC(2027, 0, 1))).toBe(12);
    expect(barsBetween('1M', jan, Date.UTC(2026, 0, 1) + 1)).toBe(1);
    expect(
      barsBetween('1M', Date.UTC(2025, 11, 1), Date.UTC(2026, 2, 15))
    ).toBe(4);
    expect(barsBetween('1M', jan, jan)).toBe(0);
  });
});
