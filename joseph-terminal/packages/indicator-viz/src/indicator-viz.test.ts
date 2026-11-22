/**
 * @fileoverview Tests for indicator record to overlay mapping.
 */
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import type {AoiRecord, StructureEvent} from '@joseph/contracts';
import {FakeChartPort} from '@joseph/chart-core';
import {describe, expect, it} from 'vitest';
import {IndicatorLayerController} from './controller';
import {
  DEFAULT_VIZ_SETTINGS,
  aoiLabel,
  aoiToOverlay,
  buildIndicatorLayers,
  eventToMarker,
  type IndicatorLayerInput,
  limitPerKind,
} from './mapping';

const BASE: AoiRecord = JSON.parse(
  readFileSync(
    join(__dirname, '../../contracts/fixtures/aoi.fixture.json'),
    'utf8'
  )
).valid[0];

/**
 * Builds an AOI from the shared contract fixture.
 * @param extra Overrides.
 * @return AOI record.
 */
function aoi(extra: Partial<AoiRecord> = {}): AoiRecord {
  return {...BASE, ...extra};
}

/**
 * Builds a structure event.
 * @param extra Overrides.
 * @return Event.
 */
function event(extra: Partial<StructureEvent> = {}): StructureEvent {
  return {
    event_id: 'e1',
    instrument_id: 'binance:perp:BTCUSDT',
    timeframe: '5m',
    source: 'liquidity_sweeps',
    name: 'SSL_SWEEP',
    bar_start_ms: 1_795_252_800_000,
    confirmed_at_ms: 1_795_253_100_000,
    ...extra,
  };
}

/**
 * Builds layer input.
 * @param extra Overrides.
 * @return Input.
 */
function input(extra: Partial<IndicatorLayerInput> = {}): IndicatorLayerInput {
  return {
    instrumentId: 'binance:perp:BTCUSDT',
    timeframe: '5m',
    aois: [aoi()],
    events: [event()],
    asOfMs: Date.UTC(2026, 10, 20, 12, 5),
    feedState: 'LIVE',
    settings: DEFAULT_VIZ_SETTINGS,
    ...extra,
  };
}

describe('aoiToOverlay', () => {
  it('maps an active bullish FVG to a bid box with a midline', () => {
    expect(aoiToOverlay(aoi())).toMatchObject({
      kind: 'box',
      lower: '94700',
      upper: '94800',
      tone: 'bid',
      midline: true,
      toMs: null,
      label: 'FVG 76',
    });
  });

  it('maps liquidity levels to rays from their creation time', () => {
    const overlay = aoiToOverlay(
      aoi({
        kind: 'SSL',
        lower: '94000',
        upper: '94000',
        side: 'bullish',
        source: 'liquidity_sweeps',
        quality_score: null,
      })
    );
    expect(overlay).toMatchObject({
      kind: 'price-line',
      price: '94000',
      fromMs: BASE.created_at_ms,
      lineStyle: 'dashed',
    });
  });

  it('fades ended zones and labels their state', () => {
    const ended = aoi({state: 'invalidated', invalidated_at_ms: BASE.as_of_ms});
    expect(aoiToOverlay(ended)).toMatchObject({
      tone: 'muted',
      lineStyle: 'dotted',
      toMs: BASE.as_of_ms,
    });
    expect(aoiLabel(ended)).toBe('FVG 76, invalidated');
    expect(
      aoiLabel(
        aoi({kind: 'OTE', range_id: 'range-1h-9f3a', quality_score: null})
      )
    ).toBe('OTE (range 9f3a)');
  });
});

describe('eventToMarker', () => {
  it('maps sweeps and structure breaks', () => {
    expect(eventToMarker(event())).toMatchObject({
      shape: 'circle',
      position: 'below',
      text: 'SSL sweep',
    });
    expect(eventToMarker(event({name: 'BOS_BEAR'}))).toMatchObject({
      shape: 'arrow-down',
      tone: 'ask',
    });
    expect(eventToMarker(event({name: 'FVG_FORMED'}))).toBeNull();
  });
});

describe('buildIndicatorLayers', () => {
  it('builds one layer per plugin with a provenance caption', () => {
    const layers = buildIndicatorLayers(input({feedState: 'DEMO'}));
    expect(layers.map(l => l.layerId).sort()).toEqual([
      'indicator-viz:fvg_ifvg',
      'indicator-viz:liquidity_sweeps',
    ]);
    expect(layers[0]?.caption).toMatch(/as of 12:05 UTC \(demo data\)$/);
  });

  it('drops records from other instruments (venue switch safety)', () => {
    const layers = buildIndicatorLayers(
      input({instrumentId: 'hyperliquid:perp:BTC'})
    );
    expect(layers).toEqual([]);
  });

  it('honours plugin, kind and event visibility settings', () => {
    const settings = {
      ...DEFAULT_VIZ_SETTINGS,
      plugins: {
        ...DEFAULT_VIZ_SETTINGS.plugins,
        fvg_ifvg: {
          visible: true,
          hiddenKinds: ['FVG' as const],
          showEvents: true,
        },
        liquidity_sweeps: {visible: true, hiddenKinds: [], showEvents: false},
      },
    };
    expect(buildIndicatorLayers(input({settings}))).toEqual([]);
  });

  it('hides ended zones unless requested', () => {
    const ended = aoi({state: 'swept'});
    expect(buildIndicatorLayers(input({aois: [ended], events: []}))).toEqual(
      []
    );
    expect(
      buildIndicatorLayers(
        input({
          aois: [ended],
          events: [],
          settings: {...DEFAULT_VIZ_SETTINGS, showEnded: true},
        })
      )
    ).toHaveLength(1);
  });
});

describe('IndicatorLayerController', () => {
  it('removes stale layers and clears on instrument switch', () => {
    const port = new FakeChartPort();
    const controller = new IndicatorLayerController(port);
    controller.update(input());
    expect([...port.layers.keys()].sort()).toEqual([
      'indicator-viz:fvg_ifvg',
      'indicator-viz:liquidity_sweeps',
    ]);
    controller.update(input({events: []}));
    expect([...port.layers.keys()]).toEqual(['indicator-viz:fvg_ifvg']);
    controller.update(
      input({
        instrumentId: 'hyperliquid:perp:BTC',
        aois: [aoi({instrument_id: 'hyperliquid:perp:BTC'})],
        events: [],
      })
    );
    expect(port.layers.size).toBe(1);
    expect(controller.layerIds()).toEqual(['indicator-viz:fvg_ifvg']);
    controller.clear();
    expect(port.layers.size).toBe(0);
  });
});

describe('zone decluttering', () => {
  it('keeps the newest zones per plugin, kind and side', () => {
    const base = {
      schema_version: 1 as const,
      type: 'aoi.updated' as const,
      instrument_id: 'mock:perp:BTCUSD',
      timeframe: '5m' as const,
      source: 'fvg_ifvg',
      kind: 'FVG' as const,
      state: 'active' as const,
      lower: '100',
      upper: '101',
      created_at_ms: 0,
      as_of_ms: 0,
      indicator_version: '1.0.0',
      method: 'pine_parity' as const,
    };
    const aois: AoiRecord[] = [1, 2, 3, 4, 5, 6].map(i => ({
      ...base,
      aoi_id: `bull-${i}`,
      side: 'bullish' as const,
      confirmed_at_ms: i,
    }));
    aois.push({...base, aoi_id: 'bear-1', side: 'bearish', confirmed_at_ms: 1});
    const kept = limitPerKind(aois, 4).map(a => a.aoi_id);
    expect(kept).toEqual(['bull-3', 'bull-4', 'bull-5', 'bull-6', 'bear-1']);
    expect(limitPerKind(aois, 0)).toHaveLength(7);
  });
});
