/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for indicator record to overlay mapping, driven by the
 * plugin display metadata of the shared contract fixtures.
 */

import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import type {
  AoiRecord,
  IndicatorPluginInfo,
  StructureEvent,
} from '@joseph/contracts';
import {FakeChartPort} from '@joseph/chart-core';
import {describe, expect, it} from 'vitest';
import {IndicatorLayerController} from './controller';
import {
  DEFAULT_VIZ_SETTINGS,
  aoiLabel,
  aoiToOverlay,
  buildIndicatorLayers,
  defaultVizSettings,
  drawsRecords,
  eventLabel,
  eventToMarker,
  kindLabel,
  kindStyle,
  layerPlugins,
  limitPerKind,
  pluginMap,
  toneOf,
  type IndicatorLayerInput,
} from './mapping';

/**
 * Reads the valid examples of a contract fixture.
 * @param name Fixture file name.
 * @return Valid examples.
 */
function fixture<T>(name: string): T[] {
  return JSON.parse(
    readFileSync(
      join(__dirname, '../../contracts/fixtures', `${name}.fixture.json`),
      'utf8'
    )
  ).valid;
}

/**
 * Reads one valid example of a contract fixture.
 * @param name Fixture file name.
 * @param index Example index.
 * @return Example.
 * @throws `Error` when the fixture has no such example.
 */
function example<T>(name: string, index: number): T {
  const found = fixture<T>(name)[index];
  if (found === undefined) throw new Error(`${name} lacks example ${index}`);
  return found;
}

const INFOS = fixture<IndicatorPluginInfo>('indicator-plugin');
const PLUGINS = pluginMap(INFOS);
/** Live pivot S1 level. */
const LEVEL = example<AoiRecord>('aoi', 0);
/** Ended example zone (end reason `broken`). */
const ZONE = example<AoiRecord>('aoi', 1);

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
 * Builds a live zone from the shared contract fixture.
 * @param extra Overrides.
 * @return AOI record.
 */
function zone(extra: Partial<AoiRecord> = {}): AoiRecord {
  return {
    ...ZONE,
    state: 'active',
    invalidated_at_ms: null,
    end_reason: null,
    ...extra,
  };
}

/**
 * Builds an event of the example zone plugin.
 * @param extra Overrides.
 * @return Event.
 */
function event(extra: Partial<StructureEvent> = {}): StructureEvent {
  return {
    event_id: 'e1',
    instrument_id: 'binance:perp:BTCUSDT',
    timeframe: '5m',
    source: 'example_zones',
    name: 'EXAMPLE_BREAK_UP',
    bar_start_ms: 1_795_252_800_000,
    confirmed_at_ms: 1_795_253_100_000,
    price: '94800',
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
    aois: [LEVEL, zone()],
    events: [],
    asOfMs: Date.UTC(2026, 10, 20, 12, 5),
    feedState: 'LIVE',
    settings: DEFAULT_VIZ_SETTINGS,
    plugins: PLUGINS,
    ...extra,
  };
}

describe('plugin metadata lookups', () => {
  it('finds kind and event styles and falls back to raw names', () => {
    const pivots = info('pivot_points');
    expect(kindStyle(pivots, 'PIVOT_S1')).toMatchObject({render: 'level'});
    expect(kindLabel(pivots, 'PIVOT_S1')).toBe('S1');
    expect(kindLabel(pivots, 'OTHER_KIND')).toBe('OTHER_KIND');
    expect(kindLabel(undefined, 'PIVOT_S1')).toBe('PIVOT_S1');
    expect(eventLabel(info('example_zones'), 'EXAMPLE_BREAK_UP')).toBe(
      'Break up'
    );
  });

  it('lists layer plugins in registration order', () => {
    expect(layerPlugins(INFOS).map(p => p.plugin_id)).toEqual([
      'ema',
      'rsi',
      'volume_delta',
      'pivot_points',
      'example_zones',
    ]);
    expect(INFOS.filter(drawsRecords).map(p => p.plugin_id)).toEqual([
      'pivot_points',
      'example_zones',
    ]);
  });

  it('takes event defaults from the display metadata', () => {
    const defaults = defaultVizSettings(INFOS);
    expect(defaults.plugins['example_zones']?.showEvents).toBe(false);
    expect(defaults.plugins['pivot_points']?.showEvents).toBe(true);
    expect(DEFAULT_VIZ_SETTINGS.plugins).toEqual({});
  });

  it('resolves display tones', () => {
    expect(toneOf('side', {side: 'bullish'})).toBe('bid');
    expect(toneOf('side', {side: 'bearish'})).toBe('ask');
    expect(toneOf('side', {side: 'neutral'})).toBe('neutral');
    expect(toneOf('side', null)).toBe('neutral');
    expect(toneOf('sign', -1)).toBe('ask');
    expect(toneOf('sign', 0)).toBe('bid');
    expect(toneOf('sign', null)).toBe('neutral');
    expect(toneOf('up', null)).toBe('bid');
    expect(toneOf('down', null)).toBe('ask');
    expect(toneOf('accent', null)).toBe('user');
    expect(toneOf('warning', null)).toBe('warning');
    expect(toneOf('muted', null)).toBe('muted');
  });
});

describe('aoiToOverlay', () => {
  it('draws a level kind as a ray from its creation time', () => {
    const pivots = info('pivot_points');
    expect(aoiToOverlay(LEVEL, kindStyle(pivots, 'PIVOT_S1'))).toEqual({
      kind: 'price-line',
      id: LEVEL.aoi_id,
      price: '93600',
      tone: 'bid',
      lineStyle: 'dashed',
      label: 'S1',
      fromMs: LEVEL.created_at_ms,
    });
  });

  it('draws a box kind with its fill, midline and score', () => {
    const style = kindStyle(info('example_zones'), 'EXAMPLE_ZONE');
    expect(aoiToOverlay(zone(), style)).toMatchObject({
      kind: 'box',
      lower: '94700',
      upper: '94800',
      tone: 'ask',
      fillOpacity: 0.15,
      lineStyle: 'solid',
      midline: true,
      toMs: null,
      label: 'Zone 76',
    });
    const tested = aoiToOverlay(zone({state: 'tested'}), style);
    expect(tested).toMatchObject({label: 'Zone 76, tested'});
    expect(tested.kind === 'box' && tested.fillOpacity).toBeCloseTo(0.09);
  });

  it('draws unstyled kinds as neutral-filled boxes named by kind', () => {
    expect(aoiToOverlay(zone({quality_score: null}))).toMatchObject({
      kind: 'box',
      fillOpacity: 0.1,
      midline: false,
      label: 'EXAMPLE_ZONE',
    });
  });

  it('fades ended AOIs and labels them with the end reason', () => {
    const zones = info('example_zones');
    const ended = ZONE;
    const overlay = aoiToOverlay(
      ended,
      kindStyle(zones, 'EXAMPLE_ZONE'),
      zones.display.end_reasons
    );
    expect(overlay).toMatchObject({
      tone: 'muted',
      lineStyle: 'dotted',
      fillOpacity: 0.04,
      toMs: ended.invalidated_at_ms,
      label: 'Zone 76, broken',
    });
    expect(aoiLabel(ended)).toBe('EXAMPLE_ZONE 76, invalidated');
    expect(aoiLabel({...ended, state: 'expired', end_reason: null})).toBe(
      'EXAMPLE_ZONE 76, expired'
    );
  });

  it('marks pending levels and uses the style color while live', () => {
    const style = {
      kind: 'LEVEL_HIGH',
      label: 'High',
      render: 'level' as const,
      color: '#123456',
      pending_until_ended: true,
    };
    const level = {...LEVEL, kind: 'LEVEL_HIGH'};
    expect(aoiToOverlay(level, style)).toMatchObject({
      lineStyle: 'dashed',
      pending: true,
      color: '#123456',
    });
    const ended = aoiToOverlay({...level, state: 'expired'}, style);
    expect(ended).toMatchObject({lineStyle: 'dotted', tone: 'muted'});
    expect(ended).not.toHaveProperty('pending');
    expect(ended).not.toHaveProperty('color');
  });
});

describe('eventToMarker', () => {
  it('draws unstyled events as neutral circles named by the event', () => {
    expect(eventToMarker(event({name: 'SOMETHING_NEW'}))).toEqual({
      kind: 'marker',
      id: 'e1',
      timeMs: 1_795_252_800_000,
      position: 'above',
      shape: 'circle',
      tone: 'neutral',
      text: 'SOMETHING_NEW',
    });
  });

  it('skips unstyled reserved events and events styled none', () => {
    expect(eventToMarker(event({name: 'AOI_INVALIDATED'}))).toBeNull();
    const zones = info('example_zones');
    expect(
      eventToMarker(
        event({name: 'EXAMPLE_ZONE_FORMED'}),
        zones.display.events[0]
      )
    ).toBeNull();
  });

  it('applies the style and moves the marker to its time_meta bar', () => {
    const style = info('example_zones').display.events[2];
    const marker = eventToMarker(
      event({name: 'EXAMPLE_BREAK_DOWN', meta: {origin_time_ms: 42}}),
      style
    );
    expect(marker).toMatchObject({
      timeMs: 42,
      position: 'above',
      shape: 'arrow-down',
      tone: 'ask',
      text: 'Break down',
      color: '#D9534F',
    });
  });
});

describe('buildIndicatorLayers', () => {
  it('builds one layer per plugin with z order and a caption', () => {
    const layers = buildIndicatorLayers(input({feedState: 'DEMO'}));
    expect(layers.map(l => [l.layerId, l.zIndex])).toEqual([
      ['indicator-viz:pivot_points', 20],
      ['indicator-viz:example_zones', 60],
    ]);
    expect(layers[0]?.caption).toBe(
      'Pivot points, as of 12:05 UTC (demo data)'
    );
  });

  it('names unknown plugins by id and paints them at the default z', () => {
    const layers = buildIndicatorLayers(
      input({aois: [zone({source: 'not_installed'})]})
    );
    expect(layers.map(l => [l.layerId, l.zIndex])).toEqual([
      ['indicator-viz:not_installed', 50],
    ]);
    expect(layers[0]?.caption).toMatch(/^not_installed, as of/);
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
        pivot_points: {
          visible: true,
          hiddenKinds: ['PIVOT_S1'],
          showEvents: true,
        },
        example_zones: {visible: false, hiddenKinds: [], showEvents: true},
      },
    };
    expect(buildIndicatorLayers(input({settings, events: [event()]}))).toEqual(
      []
    );
  });

  it('follows the plugin event default unless the user overrides it', () => {
    const events = [event()];
    // The example plugin hides its events by default.
    expect(buildIndicatorLayers(input({aois: [], events}))).toEqual([]);
    const shown = buildIndicatorLayers(
      input({
        aois: [],
        events,
        settings: {
          ...DEFAULT_VIZ_SETTINGS,
          plugins: {
            example_zones: {visible: true, hiddenKinds: [], showEvents: true},
          },
        },
      })
    );
    expect(shown[0]?.models.map(m => m.kind)).toEqual(['marker']);
  });

  it('hides ended zones unless requested', () => {
    const ended = ZONE;
    expect(buildIndicatorLayers(input({aois: [ended]}))).toEqual([]);
    expect(
      buildIndicatorLayers(
        input({
          aois: [ended],
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
      'indicator-viz:example_zones',
      'indicator-viz:pivot_points',
    ]);
    controller.update(input({aois: [LEVEL]}));
    expect([...port.layers.keys()]).toEqual(['indicator-viz:pivot_points']);
    controller.update(
      input({
        instrumentId: 'hyperliquid:perp:BTC',
        aois: [zone({instrument_id: 'hyperliquid:perp:BTC'})],
      })
    );
    expect(port.layers.size).toBe(1);
    expect(controller.layerIds()).toEqual(['indicator-viz:example_zones']);
    controller.clear();
    expect(port.layers.size).toBe(0);
  });
});

describe('zone decluttering', () => {
  it('keeps the newest zones per plugin, kind and side', () => {
    const base = zone({source: 'example_zones', kind: 'EXAMPLE_ZONE'});
    const aois: AoiRecord[] = [1, 2, 3, 4, 5, 6].map(i => ({
      ...base,
      aoi_id: `up-${i}`,
      side: 'bullish' as const,
      confirmed_at_ms: i,
    }));
    aois.push({...base, aoi_id: 'down-1', side: 'bearish', confirmed_at_ms: 1});
    const kept = limitPerKind(aois, 4).map(a => a.aoi_id);
    expect(kept).toEqual(['up-3', 'up-4', 'up-5', 'up-6', 'down-1']);
    expect(limitPerKind(aois, 0)).toHaveLength(7);
  });
});
