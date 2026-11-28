/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawings on session calendars: anchor bars follow the
 * session grid, position tools are 24 session bars wide, and the series
 * sync records the calendar and the engine's refusal message.
 */

import {
  ApiError,
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
} from '@joseph/contracts';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {createDrawing} from './document';
import {expandPositionAnchors} from './position';
import {barStartAt, seriesStatusMessage} from './series';
import {DrawingSeriesSync, type DrawingSeriesQuery} from './series-sync';
import {DrawingStore, createCommand} from './store';
import {ToolRegistry, type ToolPlugin} from './tool';

const XNYS = createBarCalendar('XNYS');

/**
 * Converts a New York wall-clock time to epoch ms.
 * @param date Local date, `YYYY-MM-DD`.
 * @param hour Local hour, 0-23.
 * @param minute Local minute.
 * @return Epoch ms UTC.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(
    dayNumberOf(date),
    hour * 60 + minute,
    'America/New_York'
  );
}

describe('anchor bars on session calendars', () => {
  it('rounds to the nearest session bar', () => {
    expect(barStartAt(ny('2026-11-23', 9, 50), '1h', XNYS)).toBe(
      ny('2026-11-23', 9, 30)
    );
    expect(barStartAt(ny('2026-11-23', 10, 10), '1h', XNYS)).toBe(
      ny('2026-11-23', 10, 30)
    );
    // The second half of the short last bar rounds to the next session.
    expect(barStartAt(ny('2026-11-20', 15, 50), '1h', XNYS)).toBe(
      ny('2026-11-23', 9, 30)
    );
    // A weekend pointer belongs to Friday's last bar's end: next session.
    expect(barStartAt(ny('2026-11-21', 12), '1h', XNYS)).toBe(
      ny('2026-11-23', 9, 30)
    );
    // UTC bars are unchanged.
    expect(barStartAt(31_000, '1m')).toBe(60_000);
  });

  it('makes position tools 24 session bars wide', () => {
    const entry = {time_ms: ny('2026-11-23', 9, 30), price: '100'};
    const [, target] = expandPositionAnchors(
      'long',
      [entry],
      3_600_000,
      '0.01',
      {
        timeframe: '1h',
        calendar: XNYS,
      }
    );
    // 7 bars per session: 24 bars later is Thursday's 4th bar.
    expect(target?.time_ms).toBe(XNYS.shift('1h', entry.time_ms, 24));
    expect(target?.time_ms).toBe(ny('2026-11-26', 12, 30));
    const [, utc] = expandPositionAnchors('long', [entry], 3_600_000, '0.01');
    expect(utc?.time_ms).toBe(entry.time_ms + 24 * 3_600_000);
  });
});

/** Engine-backed test tool requesting from its anchor bar. */
const TOOL: ToolPlugin = {
  manifest: {
    id: 'engine-line',
    version: 1,
    displayName: 'Engine line',
    group: 'studies',
    icon: 'anchor',
    anchorCount: 1,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
  }),
  render: () => undefined,
  hitTest: () => null,
  seriesRequest: (doc, {timeframe, calendar}) => {
    const anchor = doc.anchors[0];
    if (!anchor) return null;
    const bar = barStartAt(anchor.time_ms, timeframe, calendar);
    return {scriptId: 'builtin:engine_line', fromMs: bar, inputs: {anchor_ms: bar}};
  },
};

describe('series sync on session calendars', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('builds requests on the session grid and keeps the engine refusal', async () => {
    const store = new DrawingStore();
    const tools = new ToolRegistry();
    tools.register(TOOL);
    const queries: DrawingSeriesQuery[] = [];
    const message = 'Anchor is more than 50,000 bars back.';
    const sync = new DrawingSeriesSync(store, tools, {
      fetch: async query => {
        queries.push(query);
        throw new ApiError(422, {
          code: 'VALIDATION_FAILED',
          message,
          trace_id: 't',
          retryable: false,
          field_errors: [],
        });
      },
      instrumentId: () => 'mock:stock:ACME',
      timeframe: () => '1h',
      calendar: () => XNYS,
      onChange: () => undefined,
    });
    store.execute(
      createCommand(
        createDrawing({
          toolId: 'engine-line',
          toolVersion: 1,
          instrumentId: 'mock:stock:ACME',
          timeframe: null,
          anchors: [{time_ms: ny('2026-11-23', 9, 45), price: '100'}],
          style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
          zIndex: 1,
          nowMs: 0,
          newId: () => 'a',
        })
      )
    );
    await vi.advanceTimersByTimeAsync(250);
    expect(queries[0]?.fromMs).toBe(ny('2026-11-23', 9, 30));
    const state = sync.get('a');
    expect(state?.calendar).toBe(XNYS);
    expect(state?.status).toBe('error');
    expect(state?.refusal).toBe(message);
    expect(seriesStatusMessage(state)).toBe(message);
    sync.dispose();
  });
});
