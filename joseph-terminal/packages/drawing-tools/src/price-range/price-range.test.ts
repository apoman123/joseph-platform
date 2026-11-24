/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the measure tool.
 */

import {
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {measure, priceRangeTool as tool} from '.';

describe('price-range', () => {
  it('has a valid manifest', () =>
    void expect(manifestProblems(tool)).toEqual([]));
  it('measures price, percent, bars and duration', () => {
    expect(
      measure(
        {time_ms: 0, price: '100'},
        {time_ms: 90 * 60_000, price: '102.5'},
        60_000
      )
    ).toEqual({
      delta: '2.5',
      pct: '2.50',
      bars: 90,
      duration: '1h 30m',
    });
  });
  it('counts only session bars on exchange calendars', () => {
    const ny = (date: string, hour: number, minute = 0) =>
      zonedTimeToUtc(dayNumberOf(date), hour * 60 + minute, 'America/New_York');
    const from = {time_ms: ny('2026-11-20', 14, 30), price: '100'};
    const to = {time_ms: ny('2026-11-23', 10, 30), price: '101'};
    // Fri 14:30, 15:30, Mon 09:30 → Mon 10:30 is 3 bars, not 68 hours.
    expect(
      measure(from, to, 3_600_000, {
        timeframe: '1h',
        calendar: createBarCalendar('XNYS'),
      }).bars
    ).toBe(3);
    expect(measure(from, to, 3_600_000).bars).toBe(68);
  });
  it('labels a falling range with a minus sign', () => {
    const doc = makeDoc(tool, [
      {time_ms: 0, price: '160'},
      {time_ms: 600_000, price: '150'},
    ]);
    expect(texts(renderOps(tool, doc))[0]).toBe('−10.0 (-6.25%)');
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 225}, 6)).toEqual({
      part: 'body',
    });
  });
});
