/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for engine series helpers and `DrawingSeriesSync`:
 * bar mapping, request identity, debounced re-requests, stale responses,
 * refresh on closed bars, errors and point-in-time (replay) filtering.
 */

import type {ScriptResult} from '@joseph/contracts';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {createDrawing} from './document';
import {setDrawingFlags} from './editing';
import {
  barStartAt,
  decimalValue,
  integerValue,
  seriesAsOf,
  seriesPointAt,
  seriesRequestKey,
  seriesStatusMessage,
  type DrawingSeriesState,
} from './series';
import {DrawingSeriesSync, type DrawingSeriesQuery} from './series-sync';
import {DrawingStore, createCommand} from './store';
import {ToolRegistry, type ToolPlugin} from './tool';

/** Minimal engine-backed tool: one anchor, request from its bar. */
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
  seriesRequest: (doc, {timeframe}) => {
    const anchor = doc.anchors[0];
    if (!anchor) return null;
    const bar = barStartAt(anchor.time_ms, timeframe);
    return {scriptId: 'builtin:engine_line', fromMs: bar, inputs: {anchor_ms: bar}};
  },
};

/**
 * Builds a drawing at a time.
 * @param id Drawing id.
 * @param timeMs Anchor time.
 * @return Document.
 */
function drawing(id: string, timeMs: number) {
  return createDrawing({
    toolId: TOOL.manifest.id,
    toolVersion: 1,
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: null,
    anchors: [{time_ms: timeMs, price: '100'}],
    style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
    zIndex: 1,
    nowMs: 0,
    newId: () => id,
  });
}

/**
 * Builds a script run result.
 * @param query Query.
 * @return Result with a `v` plot on two bars from `fromMs`, an `n` plot on
 *     the first and a line ending on the second.
 */
function response(query: DrawingSeriesQuery): ScriptResult {
  const from = query.fromMs;
  return {
    script_id: query.scriptId,
    version: 0,
    kind: 'indicator',
    source_hash: 'test',
    instrument_id: query.instrumentId,
    tf: query.timeframe,
    bars_count: 2,
    feed_state: 'DEMO',
    data_source: 'mock',
    complete: true,
    overlay: true,
    plots: [
      {
        name: 'v',
        points: [
          {time_ms: from + 60_000, value: 2},
          {time_ms: from, value: 1},
        ],
      },
      {name: 'n', points: [{time_ms: from, value: 3}]},
    ],
    fills: [],
    equity_curve: [],
    metrics: {},
    visuals: [
      {
        id: 'centre',
        kind: 'line',
        data: {from_ms: from, from_price: 1.5, to_ms: from + 60_000, to_price: 2.5},
      },
    ],
    bar_closes: {[from]: from + 60_000, [from + 60_000]: from + 120_000},
  };
}

describe('series helpers', () => {
  it('maps an anchor time to the nearest bar', () => {
    expect(barStartAt(0, '1m')).toBe(0);
    expect(barStartAt(29_000, '1m')).toBe(0);
    expect(barStartAt(31_000, '1m')).toBe(60_000);
    expect(barStartAt(Date.UTC(2026, 10, 20, 10, 2), '5m')).toBe(
      Date.UTC(2026, 10, 20, 10, 0)
    );
  });

  it('keys requests independently of settings order', () => {
    const a = {scriptId: 'p', fromMs: 1, inputs: {a: '1', b: 2}};
    const b = {scriptId: 'p', fromMs: 1, inputs: {b: 2, a: '1'}};
    expect(seriesRequestKey(a, '5m')).toBe(seriesRequestKey(b, '5m'));
    expect(seriesRequestKey(a, '5m')).not.toBe(seriesRequestKey(a, '1h'));
  });

  it('reads points and typed values', () => {
    const points = [0, 60_000, 120_000].map(t => ({
      bar_start_ms: t,
      values: {x: t === 0 ? null : String(t / 1000), n: 4},
    }));
    expect(seriesPointAt(points, 60_000)?.values.x).toBe('60');
    expect(seriesPointAt(points, 30_000)).toBeUndefined();
    expect(decimalValue(points[0], 'x')).toBeNull();
    expect(decimalValue(points[2], 'x')).toBe('120');
    expect(integerValue(points[2], 'n')).toBe(4);
    expect(integerValue(points[2], 'x')).toBeNull();
  });

  it('filters to bars closed before the replay cursor', () => {
    const state: DrawingSeriesState = {
      status: 'ready',
      request: {scriptId: 'p', fromMs: 0, inputs: {}},
      timeframe: '1m',
      points: [0, 60_000, 120_000].map(t => ({bar_start_ms: t, values: {}})),
      asOfMs: 180_000,
      error: null,
    };
    expect(seriesAsOf(state, null)).toBe(state);
    expect(seriesAsOf(state, 120_000)?.points.map(p => p.bar_start_ms)).toEqual(
      [0, 60_000]
    );
    expect(seriesAsOf(state, 120_000)?.asOfMs).toBe(120_000);
    expect(seriesStatusMessage(state)).toBeNull();
    expect(
      seriesStatusMessage({...state, status: 'error', error: 'boom'})
    ).toBe('Engine values unavailable: boom');
  });
});

describe('DrawingSeriesSync', () => {
  let store: DrawingStore;
  let tools: ToolRegistry;
  let fetch: ReturnType<
    typeof vi.fn<(q: DrawingSeriesQuery) => Promise<ScriptResult>>
  >;
  let changes: number;

  beforeEach(() => {
    vi.useFakeTimers();
    store = new DrawingStore();
    tools = new ToolRegistry();
    tools.register(TOOL);
    fetch = vi.fn(async (query: DrawingSeriesQuery) => response(query));
    changes = 0;
  });
  afterEach(() => vi.useRealTimers());

  /**
   * Creates a sync on the 1m timeframe.
   * @return Sync.
   */
  function sync(): DrawingSeriesSync {
    return new DrawingSeriesSync(store, tools, {
      fetch,
      instrumentId: () => 'mock:perp:BTCUSD',
      timeframe: () => '1m',
      onChange: () => {
        changes += 1;
      },
    });
  }

  it('requests a new drawing once and exposes sorted points', async () => {
    const s = sync();
    store.execute(createCommand(drawing('a', 125_000)));
    expect(s.get('a')?.status).toBe('loading');
    await vi.runAllTimersAsync();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toEqual({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1m',
      scriptId: 'builtin:engine_line',
      fromMs: 120_000,
      inputs: {anchor_ms: 120_000},
    });
    const state = s.get('a');
    expect(state?.status).toBe('ready');
    expect(state?.points.map(p => p.bar_start_ms)).toEqual([120_000, 180_000]);
    expect(state?.points[0]?.values).toEqual({v: '1', n: '3'});
    expect(state?.points[1]?.values).toEqual({
      v: '2',
      'centre.from_price': '1.5',
      'centre.to_price': '2.5',
      'centre.from_ms': 120_000,
    });
    expect(state?.asOfMs).toBe(240_000);
    expect(changes).toBeGreaterThanOrEqual(2);
    s.dispose();
  });

  it('debounces a drag into one request and drops superseded responses', async () => {
    const s = sync();
    const doc = drawing('a', 0);
    store.execute(createCommand(doc));
    for (const t of [60_000, 120_000, 180_000]) {
      store.update(
        'a',
        d => ({...d, anchors: [{time_ms: t, price: '100'}]}),
        'Move',
        'drag'
      );
      vi.advanceTimersByTime(50);
    }
    await vi.runAllTimersAsync();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0].fromMs).toBe(180_000);
    // A slow response for an old anchor never overwrites the newer one.
    let release: (value: ScriptResult) => void = () => undefined;
    fetch.mockImplementationOnce(
      query =>
        new Promise(resolve => {
          release = () => void resolve(response(query));
        })
    );
    store.update(
      'a',
      d => ({...d, anchors: [{time_ms: 0, price: '1'}]}),
      'Move'
    );
    await vi.advanceTimersByTimeAsync(250);
    store.update(
      'a',
      d => ({...d, anchors: [{time_ms: 300_000, price: '1'}]}),
      'Move'
    );
    await vi.runAllTimersAsync();
    release(response({...(fetch.mock.calls[1]?.[0] as DrawingSeriesQuery)}));
    await vi.runAllTimersAsync();
    expect(s.get('a')?.request.fromMs).toBe(300_000);
    expect(s.get('a')?.points[0]?.bar_start_ms).toBe(300_000);
    s.dispose();
  });

  it('re-requests on refresh and keeps values when a refresh fails', async () => {
    const s = sync();
    store.execute(createCommand(drawing('a', 0)));
    await vi.runAllTimersAsync();
    fetch.mockRejectedValueOnce(new Error('offline'));
    s.refresh();
    await vi.runAllTimersAsync();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(s.get('a')?.status).toBe('ready');
    s.refresh();
    await vi.runAllTimersAsync();
    expect(fetch).toHaveBeenCalledTimes(3);
    s.dispose();
  });

  it('reports a failed first request and skips hidden or deleted drawings', async () => {
    fetch.mockRejectedValueOnce(new Error('engine down'));
    const s = sync();
    store.execute(createCommand(drawing('a', 0)));
    await vi.runAllTimersAsync();
    expect(s.get('a')).toMatchObject({status: 'error', error: 'engine down'});
    setDrawingFlags(store, 'a', {visible: false}, 1);
    expect(s.get('a')).toBeNull();
    store.undo();
    expect(s.get('a')?.status).toBe('loading');
    store.remove('a');
    expect(s.get('a')).toBeNull();
    s.dispose();
    store.execute(createCommand(drawing('b', 0)));
    await vi.runAllTimersAsync();
    expect(s.get('b')).toBeNull();
  });
});
