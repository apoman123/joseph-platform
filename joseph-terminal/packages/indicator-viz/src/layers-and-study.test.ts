/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for event segments, per-chart kind filters and
 * colors, replay captions, metadata-driven study panes and price-pane plot
 * lines.
 */

import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {DEFAULT_CHART_THEME} from '@joseph/chart-core';
import type {
  AoiRecord,
  IndicatorPluginInfo,
  IndicatorSeries,
  StructureEvent,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  DEFAULT_VIZ_SETTINGS,
  buildIndicatorLayers,
  eventMetaMs,
  eventToMarker,
  eventToSegment,
  pluginMap,
  type VizSettings,
} from './mapping';
import {
  createPlotLayer,
  paintPlotLines,
  plotLinesFromSeries,
} from './plot-layer';
import {
  DELTA_PLUGIN_ID,
  DELTA_STUDY_ID,
  ESTIMATE_DELTA_OPACITY,
  deltaStudyFromSeries,
  numericValue,
  plotPoints,
  studyPaneFromSeries,
  studyPaneId,
} from './study';

const FIXTURES = join(__dirname, '../../contracts/fixtures');
const INFOS: IndicatorPluginInfo[] = JSON.parse(
  readFileSync(join(FIXTURES, 'indicator-plugin.fixture.json'), 'utf8')
).valid;
const PLUGINS = pluginMap(INFOS);
const AOIS: AoiRecord[] = JSON.parse(
  readFileSync(join(FIXTURES, 'aoi.fixture.json'), 'utf8')
).valid;
const T0 = 1_795_252_800_000;
const BAR = 300_000;
const INSTRUMENT = 'binance:perp:BTCUSDT';

/**
 * Returns a fixture plugin.
 * @param id Plugin id.
 * @return Descriptor.
 * @throws `Error` when the fixture lacks the plugin.
 */
function info(id: string): IndicatorPluginInfo {
  const found = PLUGINS.get(id);
  if (!found) throw new Error(`fixture lacks ${id}`);
  return found;
}

/**
 * Returns a fixture AOI.
 * @param index Example index.
 * @return AOI record.
 * @throws `Error` when the fixture lacks the example.
 */
function aoi(index: number): AoiRecord {
  const found = AOIS[index];
  if (!found) throw new Error(`fixture lacks AOI ${index}`);
  return found;
}

/**
 * Builds a break event of the example zone plugin.
 * @param extra Overrides.
 * @return Event.
 */
function breakEvent(extra: Partial<StructureEvent>): StructureEvent {
  return {
    event_id: 'brk-1',
    instrument_id: INSTRUMENT,
    timeframe: '5m',
    source: 'example_zones',
    name: 'EXAMPLE_BREAK_UP',
    bar_start_ms: T0 + 10 * BAR,
    confirmed_at_ms: T0 + 11 * BAR,
    price: '95000',
    meta: {origin_time_ms: T0 + 4 * BAR},
    ...extra,
  };
}

/**
 * Builds a series from per-bar values.
 * @param pluginId Plugin id.
 * @param values Values per bar.
 * @return Series.
 */
function seriesOf(
  pluginId: string,
  values: Array<IndicatorSeries['points'][number]['values']>
): IndicatorSeries {
  return {
    instrument_id: INSTRUMENT,
    timeframe: '5m',
    plugin_id: pluginId,
    indicator_version: '1.0.0',
    as_of_ms: T0 + values.length * BAR,
    feed_state: 'LIVE',
    points: values.map((v, i) => ({bar_start_ms: T0 + i * BAR, values: v})),
  };
}

describe('event segments', () => {
  const zones = info('example_zones');
  const style = zones.display.events.find(e => e.name === 'EXAMPLE_BREAK_UP');

  it('reads meta times defensively', () => {
    expect(eventMetaMs(breakEvent({}), 'origin_time_ms')).toBe(T0 + 4 * BAR);
    expect(eventMetaMs(breakEvent({meta: {origin_time_ms: 'x'}}), 'x')).toBe(
      null
    );
    expect(eventMetaMs(breakEvent({}), null)).toBeNull();
  });

  it('draws a segment from the meta time to the event bar', () => {
    expect(eventToSegment(breakEvent({}), style)).toEqual({
      kind: 'box',
      id: 'brk-1:segment',
      fromMs: T0 + 4 * BAR,
      toMs: T0 + 10 * BAR,
      lower: '95000',
      upper: '95000',
      tone: 'bid',
      fillOpacity: 0,
      lineStyle: 'dashed',
    });
    // The marker of a marker-and-segment style sits on the meta bar.
    expect(eventToMarker(breakEvent({}), style)?.timeMs).toBe(T0 + 4 * BAR);
  });

  it('skips segments without a style, price or earlier start', () => {
    expect(eventToSegment(breakEvent({}))).toBeNull();
    expect(eventToSegment(breakEvent({price: null}), style)).toBeNull();
    expect(
      eventToSegment(breakEvent({meta: {origin_time_ms: T0 + 10 * BAR}}), style)
    ).toBeNull();
    expect(
      eventToSegment(breakEvent({}), {
        name: 'EXAMPLE_BREAK_UP',
        label: 'Break up',
        render: 'segment',
        time_meta: 'origin_time_ms',
      })
    ).toMatchObject({tone: 'neutral', lineStyle: 'solid'});
    expect(
      eventToMarker(breakEvent({}), {
        name: 'EXAMPLE_BREAK_UP',
        label: 'Break up',
        render: 'segment',
      })
    ).toBeNull();
  });
});

describe('per-chart kind filters, colors and replay captions', () => {
  const settings: VizSettings = {
    ...DEFAULT_VIZ_SETTINGS,
    plugins: {
      pivot_points: {
        visible: true,
        hiddenKinds: [],
        showEvents: true,
        shownKinds: ['PIVOT_R1'],
        colors: {PIVOT_R1: '#aa00ff', PIVOT_S1: '#00ff00'},
      },
      example_zones: {
        visible: true,
        hiddenKinds: [],
        showEvents: true,
        colors: {EXAMPLE_BREAK_UP: '#123abc', bad: 'red'},
      },
    },
  };

  it('keeps only the chosen kinds and applies user colors', () => {
    const layers = buildIndicatorLayers({
      instrumentId: INSTRUMENT,
      timeframe: '5m',
      aois: [
        {...aoi(0), aoi_id: 'a', kind: 'PIVOT_S1'},
        {...aoi(0), aoi_id: 'b', kind: 'PIVOT_R1', side: 'bearish'},
      ],
      events: [breakEvent({})],
      asOfMs: T0,
      feedState: 'LIVE',
      settings,
      plugins: PLUGINS,
    });
    const pivots = layers.find(l => l.layerId === 'indicator-viz:pivot_points');
    expect(pivots?.models.map(m => m.id)).toEqual(['b']);
    expect(pivots?.models[0]).toMatchObject({
      color: '#aa00ff',
      tone: 'ask',
      label: 'R1',
    });
    const zones = layers.find(l => l.layerId === 'indicator-viz:example_zones');
    expect(zones?.models.map(m => m.kind)).toEqual(['marker', 'box']);
    expect(zones?.models.every(m => m.color === '#123abc')).toBe(true);
  });

  it('labels replay captions with the cursor time', () => {
    const [layer] = buildIndicatorLayers({
      instrumentId: INSTRUMENT,
      timeframe: '5m',
      aois: [aoi(0)],
      events: [],
      asOfMs: T0 + 100 * BAR,
      feedState: 'LIVE',
      settings: DEFAULT_VIZ_SETTINGS,
      plugins: PLUGINS,
      atMs: T0,
    });
    expect(layer?.caption).toBe('Pivot points, as of 09:20 UTC (replay)');
  });
});

describe('volume delta pane', () => {
  const series = seriesOf(DELTA_PLUGIN_ID, [
    {delta_pct: '34.5'},
    {delta_pct: '-12.25'},
    {delta_pct: null},
    {delta_pct: 'n/a'},
    {delta_pct: 0},
  ]);

  it('plots signed columns with a zero line', () => {
    const study = deltaStudyFromSeries(series);
    expect(study.id).toBe(DELTA_STUDY_ID);
    expect(studyPaneId(DELTA_PLUGIN_ID, 'delta')).toBe(DELTA_STUDY_ID);
    expect(study.style).toBe('histogram');
    // Without `values.method` a bar is never called true delta.
    const faint = ESTIMATE_DELTA_OPACITY;
    expect(study.points).toEqual([
      {timeMs: T0, value: 34.5, tone: 'bid', opacity: faint},
      {timeMs: T0 + BAR, value: -12.25, tone: 'ask', opacity: faint},
      {timeMs: T0 + 4 * BAR, value: 0, tone: 'bid', opacity: faint},
    ]);
    expect(study.title).toBe('Delta % (estimate)');
    expect(study.levels?.map(l => l.value)).toEqual([0]);
    expect(study.caption).toBe('as of 09:45 UTC (live)');
  });

  it('follows the replay cursor', () => {
    const study = deltaStudyFromSeries(series, {atMs: T0 + 2 * BAR});
    expect(study.points.map(p => p.timeMs)).toEqual([T0, T0 + BAR]);
    expect(study.caption).toContain('(replay)');
  });

  it('reads numeric values only', () => {
    expect(numericValue(Number.NaN)).toBeNull();
    expect(numericValue(' ')).toBeNull();
    expect(numericValue(true)).toBeNull();
    expect(numericValue('1.5')).toBe(1.5);
  });
});

describe('study panes from display metadata', () => {
  const rsi = info('rsi');
  const series = seriesOf('rsi', [{rsi: null}, {rsi: '71.5'}, {rsi: '48'}]);

  it('builds a line pane with the declared levels', () => {
    const pane = rsi.display.panes[0];
    if (!pane) throw new Error('fixture lacks the RSI pane');
    expect(studyPaneFromSeries(rsi, pane, series)).toEqual({
      id: 'rsi:rsi',
      title: 'RSI',
      style: 'line',
      points: [
        {timeMs: T0 + BAR, value: 71.5},
        {timeMs: T0 + 2 * BAR, value: 48},
      ],
      levels: [70, 50, 30].map(value => ({
        value,
        label: String(value),
        tone: 'muted',
        lineStyle: 'dotted',
      })),
      precision: 2,
      caption: 'as of 09:35 UTC (live)',
    });
    expect(
      studyPaneFromSeries(rsi, pane, series, T0 + 2 * BAR).points
    ).toHaveLength(1);
  });

  it('colors histogram columns by sign', () => {
    const delta = info(DELTA_PLUGIN_ID);
    const pane = delta.display.panes[0];
    if (!pane) throw new Error('fixture lacks the delta pane');
    const study = studyPaneFromSeries(
      delta,
      pane,
      seriesOf(DELTA_PLUGIN_ID, [{delta_pct: '-3'}, {delta_pct: '2'}])
    );
    expect(study.style).toBe('histogram');
    expect(study.points.map(p => p.tone)).toEqual(['ask', 'bid']);
  });

  it('reads plot points with gaps and a replay cursor', () => {
    expect(plotPoints(series, 'rsi').map(p => p.value)).toEqual([
      null,
      71.5,
      48,
    ]);
    expect(plotPoints(series, 'rsi', T0 + BAR)).toEqual([
      {timeMs: T0, value: null},
    ]);
  });
});

describe('price-pane plot lines', () => {
  const ema = info('ema');
  const series = seriesOf('ema', [{ema: '100'}, {ema: null}, {ema: '102'}]);

  it('resolves colors from the user, the plot and the tone', () => {
    const [line] = plotLinesFromSeries(ema, series, {
      theme: DEFAULT_CHART_THEME,
    });
    expect(line).toMatchObject({
      id: 'ema',
      color: DEFAULT_CHART_THEME.user,
      lineStyle: 'solid',
      lineWidth: 1,
    });
    expect(line?.points.map(p => p.value)).toEqual([100, null, 102]);
    const [user] = plotLinesFromSeries(ema, series, {
      theme: DEFAULT_CHART_THEME,
      colors: {ema: '#112233'},
      atMs: T0 + BAR,
    });
    expect(user?.color).toBe('#112233');
    expect(user?.points).toHaveLength(1);
    expect(
      plotLinesFromSeries(info('rsi'), series, {theme: DEFAULT_CHART_THEME})
    ).toEqual([]);
  });

  it('breaks the polyline at gaps and missing coordinates', () => {
    const calls: string[] = [];
    const ctx = {
      save: () => void calls.push('save'),
      restore: () => void calls.push('restore'),
      beginPath: () => void calls.push('begin'),
      stroke: () => void calls.push('stroke'),
      moveTo: (x: number) => void calls.push(`move ${x}`),
      lineTo: (x: number) => void calls.push(`line ${x}`),
      setLineDash: (dash: number[]) => void calls.push(`dash ${dash.length}`),
    };
    const transform = {
      timeToX: (t: number) => (t === T0 + 3 * BAR ? null : (t - T0) / BAR),
      priceToY: (p: number) => p,
      xToTime: () => null,
      yToPrice: () => null,
      width: 100,
      height: 100,
    };
    const points = [0, 1, 2, 3, 4, 5].map(i => ({
      timeMs: T0 + i * BAR,
      value: i === 2 ? null : 100 + i,
    }));
    const layer = createPlotLayer({
      id: 'study-lines:ema',
      zIndex: 14,
      lines: () => [
        {id: 'ema', points, color: '#fff', lineStyle: 'dashed', lineWidth: 1},
      ],
    });
    expect(layer).toMatchObject({id: 'study-lines:ema', zIndex: 14});
    // The fake implements only what the painter calls.
    layer.draw(ctx as unknown as CanvasRenderingContext2D, transform);
    expect(calls).toEqual([
      'save',
      'dash 2',
      'begin',
      'move 0',
      'line 1',
      'move 4',
      'line 5',
      'stroke',
      'restore',
    ]);
    calls.length = 0;
    paintPlotLines(ctx as unknown as CanvasRenderingContext2D, transform, []);
    expect(calls).toEqual(['save', 'restore']);
  });
});
