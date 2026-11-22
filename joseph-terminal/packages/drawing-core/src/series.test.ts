/**
 * @fileoverview Tests for engine series helpers and `DrawingSeriesSync`:
 * bar mapping, request identity, debounced re-requests, stale responses,
 * refresh on closed bars, errors and point-in-time (replay) filtering.
 */
import type {IndicatorSeries, IndicatorSeriesQuery} from '@joseph/contracts';
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
import {DrawingSeriesSync} from './series-sync';
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
    return {pluginId: 'engine_line', fromMs: bar, settings: {anchor_ms: bar}};
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
 * Builds a series response.
 * @param query Query.
 * @return Series with one point at `fromMs`.
 */
function response(query: IndicatorSeriesQuery): IndicatorSeries {
  return {
    instrument_id: query.instrumentId,
    timeframe: query.timeframe,
    plugin_id: query.pluginId,
    indicator_version: 'test',
    points: [
      {bar_start_ms: (query.fromMs ?? 0) + 60_000, values: {v: '2'}},
      {bar_start_ms: query.fromMs ?? 0, values: {v: '1', n: 3}},
    ],
    as_of_ms: (query.fromMs ?? 0) + 120_000,
    feed_state: 'DEMO',
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
    const a = {pluginId: 'p', fromMs: 1, settings: {a: '1', b: 2}};
    const b = {pluginId: 'p', fromMs: 1, settings: {b: 2, a: '1'}};
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
      request: {pluginId: 'p', fromMs: 0, settings: {}},
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
    typeof vi.fn<(q: IndicatorSeriesQuery) => Promise<IndicatorSeries>>
  >;
  let changes: number;

  beforeEach(() => {
    vi.useFakeTimers();
    store = new DrawingStore();
    tools = new ToolRegistry();
    tools.register(TOOL);
    fetch = vi.fn(async (query: IndicatorSeriesQuery) => response(query));
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
      pluginId: 'engine_line',
      fromMs: 120_000,
      settings: {anchor_ms: 120_000},
    });
    const state = s.get('a');
    expect(state?.status).toBe('ready');
    expect(state?.points.map(p => p.bar_start_ms)).toEqual([120_000, 180_000]);
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
    let release: (value: IndicatorSeries) => void = () => undefined;
    fetch.mockImplementationOnce(
      query =>
        new Promise(resolve => {
          release = () => resolve(response(query));
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
    release(response({...(fetch.mock.calls[1]?.[0] as IndicatorSeriesQuery)}));
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
