/**
 * @fileoverview Tests for market-structure rendering, per-kind filters and
 * colors, replay captions and the order-flow Delta % study pane.
 */
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import type {
  AoiRecord,
  IndicatorSeries,
  StructureEvent,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  DEFAULT_VIZ_SETTINGS,
  PLUGIN_NAMES,
  aoiToOverlay,
  buildIndicatorLayers,
  eventPivotMs,
  eventToBreakLine,
  eventToMarker,
  type VizSettings,
} from './mapping';
import {
  DEFAULT_MIN_DELTA_SHARE,
  DELTA_STUDY_ID,
  ESTIMATE_DELTA_OPACITY,
  deltaStudyFromSeries,
  minDeltaShare,
  numericValue,
} from './study';

const BASE: AoiRecord = JSON.parse(
  readFileSync(
    join(__dirname, '../../contracts/fixtures/aoi.fixture.json'),
    'utf8'
  )
).valid[0];
const T0 = 1_795_252_800_000;
const BAR = 300_000;

/**
 * Builds a market-structure event.
 * @param extra Overrides.
 * @return Event.
 */
function structure(extra: Partial<StructureEvent>): StructureEvent {
  return {
    event_id: 'ms-1',
    instrument_id: BASE.instrument_id,
    timeframe: '5m',
    source: 'market_structure',
    name: 'BOS_BULL',
    bar_start_ms: T0 + 10 * BAR,
    confirmed_at_ms: T0 + 11 * BAR,
    price: '95000',
    meta: {pivot_time_ms: T0 + 4 * BAR},
    ...extra,
  };
}

describe('market structure rendering', () => {
  it('is a first-party layer with a display name', () => {
    expect(PLUGIN_NAMES.market_structure).toBe('Market structure');
  });

  it('labels swings at the pivot bar once confirmed', () => {
    const hh = eventToMarker(structure({name: 'SWING_HH'}));
    expect(hh).toMatchObject({
      text: 'HH',
      position: 'above',
      timeMs: T0 + 4 * BAR,
    });
    expect(eventToMarker(structure({name: 'SWING_LL'}))).toMatchObject({
      text: 'LL',
      position: 'below',
      tone: 'ask',
    });
    expect(eventToMarker(structure({name: 'SWING_EQH'}))?.tone).toBe('neutral');
    const noPivot = eventToMarker(structure({name: 'SWING_HL', meta: {}}));
    expect(noPivot?.timeMs).toBe(T0 + 10 * BAR);
  });

  it('calls a change of character MSS for this plugin only', () => {
    expect(eventToMarker(structure({name: 'CHOCH_BEAR'}))?.text).toBe('MSS');
    expect(
      eventToMarker(structure({name: 'CHOCH_BEAR', source: 'daily_bias'}))?.text
    ).toBe('CHoCH');
    expect(eventToMarker(structure({name: 'INITIAL_BREAK_BULL'}))?.text).toBe(
      'Initial break'
    );
  });

  it('draws break lines from the broken pivot to the break bar', () => {
    const line = eventToBreakLine(structure({}));
    expect(line).toMatchObject({
      kind: 'box',
      fromMs: T0 + 4 * BAR,
      toMs: T0 + 10 * BAR,
      lower: '95000',
      upper: '95000',
      fillOpacity: 0,
      lineStyle: 'solid',
    });
    expect(eventToBreakLine(structure({name: 'CHOCH_BULL'}))?.lineStyle).toBe(
      'dashed'
    );
    expect(eventToBreakLine(structure({name: 'SWING_HH'}))).toBeNull();
    expect(eventToBreakLine(structure({price: null}))).toBeNull();
    expect(
      eventToBreakLine(structure({meta: {pivot_time_ms: 'x'}}))
    ).toBeNull();
    expect(eventPivotMs(structure({meta: undefined}))).toBeNull();
  });

  it('draws unbroken swings as dashed pending rays', () => {
    const pending = aoiToOverlay({
      ...BASE,
      source: 'market_structure',
      kind: 'SWING_HIGH',
      side: 'bearish',
      lower: '96000',
      upper: '96000',
      state: 'active',
    });
    expect(pending).toMatchObject({
      kind: 'price-line',
      pending: true,
      lineStyle: 'dashed',
      label: expect.stringMatching(/^Swing high( \d+)?$/),
    });
    const broken = aoiToOverlay({
      ...BASE,
      source: 'market_structure',
      kind: 'SWING_LOW',
      lower: '94000',
      upper: '94000',
      state: 'invalidated',
    });
    expect(broken).toMatchObject({lineStyle: 'dotted'});
    expect('pending' in broken).toBe(false);
  });
});

describe('per-chart kind filters, colors and replay captions', () => {
  const settings: VizSettings = {
    ...DEFAULT_VIZ_SETTINGS,
    plugins: {
      ...DEFAULT_VIZ_SETTINGS.plugins,
      fvg_ifvg: {
        visible: true,
        hiddenKinds: [],
        showEvents: true,
        shownKinds: ['IFVG'],
        colors: {IFVG: '#aa00ff', FVG: '#00ff00'},
      },
      market_structure: {
        visible: true,
        hiddenKinds: [],
        showEvents: true,
        colors: {BOS_BULL: '#123abc', bad: 'red'},
      },
    },
  };

  it('keeps only the chosen kinds and applies user colors', () => {
    const layers = buildIndicatorLayers({
      instrumentId: BASE.instrument_id,
      timeframe: '5m',
      aois: [
        {...BASE, aoi_id: 'a', kind: 'FVG'},
        {...BASE, aoi_id: 'b', kind: 'IFVG'},
      ],
      events: [structure({})],
      asOfMs: T0,
      feedState: 'LIVE',
      settings,
    });
    const fvg = layers.find(l => l.layerId === 'indicator-viz:fvg_ifvg');
    expect(fvg?.models.map(m => m.id)).toEqual(['b']);
    expect(fvg?.models[0]).toMatchObject({color: '#aa00ff'});
    const ms = layers.find(l => l.layerId === 'indicator-viz:market_structure');
    expect(ms?.models.map(m => m.kind)).toEqual(['marker', 'box']);
    expect(ms?.models.every(m => m.color === '#123abc')).toBe(true);
  });

  it('draws a break once when daily bias and structure both report it', () => {
    const events = [
      structure({}),
      structure({event_id: 'db-1', source: 'daily_bias', meta: {}}),
      structure({
        event_id: 'db-2',
        source: 'daily_bias',
        name: 'BOS_BEAR',
        meta: {},
      }),
    ];
    const structureSettings = settings.plugins['market_structure'];
    if (!structureSettings) throw new Error('fixture lacks market_structure');
    const build = (msVisible: boolean) =>
      buildIndicatorLayers({
        instrumentId: BASE.instrument_id,
        timeframe: '5m',
        aois: [],
        events,
        asOfMs: T0,
        feedState: 'LIVE',
        settings: {
          ...settings,
          plugins: {
            ...settings.plugins,
            market_structure: {
              ...structureSettings,
              visible: msVisible,
            },
          },
        },
      });
    const bias = (msVisible: boolean) =>
      build(msVisible)
        .find(l => l.layerId === 'indicator-viz:daily_bias')
        ?.models.map(m => m.id);
    expect(bias(true)).toEqual(['db-2']);
    expect(bias(false)).toEqual(['db-1', 'db-2']);
  });

  it('labels replay captions with the cursor time', () => {
    const [layer] = buildIndicatorLayers({
      instrumentId: BASE.instrument_id,
      timeframe: '5m',
      aois: [],
      events: [structure({})],
      asOfMs: T0 + 100 * BAR,
      feedState: 'LIVE',
      settings: DEFAULT_VIZ_SETTINGS,
      atMs: T0,
    });
    expect(layer?.caption).toBe('Market structure, as of 09:20 UTC (replay)');
  });
});

describe('order-flow Delta % pane', () => {
  const series: IndicatorSeries = {
    instrument_id: BASE.instrument_id,
    timeframe: '5m',
    plugin_id: 'order_flow_pressure',
    indicator_version: '1.0.0',
    as_of_ms: T0 + 4 * BAR,
    feed_state: 'LIVE',
    points: [
      {bar_start_ms: T0, values: {delta_pct: '34.5'}},
      {bar_start_ms: T0 + BAR, values: {delta_pct: '-12.25'}},
      {bar_start_ms: T0 + 2 * BAR, values: {delta_pct: null}},
      {bar_start_ms: T0 + 3 * BAR, values: {delta_pct: 'n/a'}},
      {bar_start_ms: T0 + 4 * BAR, values: {delta_pct: 0}},
    ],
  };

  it('plots signed columns with zero and threshold lines', () => {
    const study = deltaStudyFromSeries(series);
    expect(study.id).toBe(DELTA_STUDY_ID);
    expect(study.style).toBe('histogram');
    // Without `values.method` a bar is never called true delta.
    const faint = ESTIMATE_DELTA_OPACITY;
    expect(study.points).toEqual([
      {timeMs: T0, value: 34.5, tone: 'bid', opacity: faint},
      {timeMs: T0 + BAR, value: -12.25, tone: 'ask', opacity: faint},
      {timeMs: T0 + 4 * BAR, value: 0, tone: 'bid', opacity: faint},
    ]);
    expect(study.title).toBe('Delta % (estimate)');
    expect(study.levels?.map(l => l.value)).toEqual([0, 20, -20]);
    expect(study.caption).toBe('as of 09:40 UTC (live)');
  });

  it('follows the min_delta_share input and the replay cursor', () => {
    const study = deltaStudyFromSeries(series, {
      settings: {min_delta_share: '0.35'},
      atMs: T0 + 2 * BAR,
    });
    expect(study.levels?.map(l => l.label)).toEqual(['', '+35%', '−35%']);
    expect(study.points.map(p => p.timeMs)).toEqual([T0, T0 + BAR]);
    expect(study.caption).toContain('(replay)');
  });

  it('validates threshold inputs and numeric values', () => {
    expect(minDeltaShare({min_delta_share: '1.5'})).toBe(
      DEFAULT_MIN_DELTA_SHARE
    );
    expect(minDeltaShare(null)).toBe(DEFAULT_MIN_DELTA_SHARE);
    expect(numericValue(Number.NaN)).toBeNull();
    expect(numericValue(' ')).toBeNull();
    expect(numericValue(true)).toBeNull();
  });
});
