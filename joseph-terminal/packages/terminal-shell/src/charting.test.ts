/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit tests for the charting additions of the terminal shell:
 * indicator layer configuration, comparisons, session separators, chart
 * templates, bar replay, custom timeframes and resizable layout geometry.
 */

import {describe, expect, it} from 'vitest';
import {
  LAYOUT_TRACKS,
  MIN_TRACK_SHARE,
  clampDock,
  gridSizesFor,
  resizeTracks,
  tracksToTemplate,
} from './layout-sizes';
import {
  DEFAULT_REPLAY_LOOKBACK,
  chooseReplayStart,
  nextReplaySpeed,
  replayAsOfMs,
  replayAtEnd,
  replayVisibleBars,
  startReplay,
  stepReplay,
} from './replay';
import {
  TEMPLATE_STORAGE_KEY,
  TemplateRepository,
  createTemplate,
  isChartTemplate,
  presentationOf,
} from './templates';
import {parseTimeframeInput} from './timeframes';
import {
  activeChart,
  createDefaultWorkspace,
  layerSettings,
  layerStyle,
  layerVisible,
  workspaceReducer,
} from './workspace';

/**
 * Creates a fresh workspace.
 * @return Workspace document.
 */
function workspace() {
  return createDefaultWorkspace({
    workspaceId: 'w',
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: '5m',
    nowMs: 1,
  });
}

/** Map-backed storage. */
class MapStorage {
  readonly map = new Map<string, string>();

  /**
   * Reads a key.
   * @param key Key.
   * @return Value or null.
   */
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }

  /**
   * Writes a key.
   * @param key Key.
   * @param value Value.
   */
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

describe('indicator layer configuration', () => {
  it('stores kinds, style and inputs without touching visibility', () => {
    let doc = workspace();
    doc = workspaceReducer(
      doc,
      {
        type: 'setIndicatorLayerConfig',
        chartId: 'chart-1',
        pluginId: 'fvg_ifvg',
        patch: {
          kinds: ['FVG'],
          style: {colors: {FVG: '#112233'}, show_events: false},
          settings: {min_gap_atr: '0.2'},
        },
      },
      2
    );
    const chart = activeChart(doc);
    const layer = chart.indicator_layers.find(l => l.plugin_id === 'fvg_ifvg');
    expect(layer?.visible).toBe(true);
    expect(layer?.kinds).toEqual(['FVG']);
    expect(layerStyle(chart, 'fvg_ifvg').colors).toEqual({FVG: '#112233'});
    expect(layerSettings(chart, 'fvg_ifvg')).toEqual({min_gap_atr: '0.2'});
    doc = workspaceReducer(
      doc,
      {
        type: 'setIndicatorLayerConfig',
        chartId: 'chart-1',
        pluginId: 'fvg_ifvg',
        patch: {kinds: null, settings: null},
      },
      3
    );
    const reset = activeChart(doc).indicator_layers.find(
      l => l.plugin_id === 'fvg_ifvg'
    );
    expect(reset?.kinds).toBeUndefined();
    expect(layerSettings(activeChart(doc), 'fvg_ifvg')).toBeNull();
  });

  it('adds a missing layer and treats unknown layers as visible', () => {
    const doc = workspaceReducer(
      workspace(),
      {
        type: 'setIndicatorLayerConfig',
        chartId: 'chart-1',
        pluginId: 'future_plugin',
        patch: {style: {pane: false}},
      },
      2
    );
    const chart = activeChart(doc);
    expect(layerStyle(chart, 'future_plugin')).toEqual({pane: false});
    expect(layerVisible(chart, 'another_plugin')).toBe(true);
    expect(layerVisible(chart, 'premium_discount_ote')).toBe(false);
  });

  it('ships the market structure layer on new charts', () => {
    expect(layerVisible(activeChart(workspace()), 'market_structure')).toBe(
      true
    );
  });
});

describe('comparisons and separators', () => {
  it('de-duplicates, drops the chart instrument and caps at three', () => {
    const doc = workspaceReducer(
      workspace(),
      {
        type: 'setCompare',
        chartId: 'chart-1',
        instrumentIds: [
          'mock:perp:ETHUSD',
          'mock:perp:ETHUSD',
          'mock:perp:BTCUSD',
          'mock:perp:SOLUSD',
          'mock:perp:XRPUSD',
          'mock:perp:DOGEUSD',
        ],
      },
      2
    );
    expect(activeChart(doc).compare).toEqual([
      'mock:perp:ETHUSD',
      'mock:perp:SOLUSD',
    ]);
    const cleared = workspaceReducer(
      doc,
      {type: 'setCompare', chartId: 'chart-1', instrumentIds: []},
      3
    );
    expect('compare' in activeChart(cleared)).toBe(false);
  });

  it('toggles session separators per chart', () => {
    const doc = workspaceReducer(
      workspace(),
      {type: 'toggleSessionSeparators', chartId: 'chart-1'},
      2
    );
    expect(activeChart(doc).session_separators).toBe(true);
  });
});

describe('chart templates', () => {
  it('captures presentation only and applies it without moving venue', () => {
    let doc = workspace();
    doc = workspaceReducer(
      doc,
      {type: 'setChartType', chartId: 'chart-1', chartType: 'heikin_ashi'},
      2
    );
    const template = createTemplate(activeChart(doc), '  Scalping  ', 't1', 5);
    expect(template.name).toBe('Scalping');
    expect(template.presentation.chart_type).toBe('heikin_ashi');
    expect(JSON.stringify(template)).not.toContain('BTCUSD');
    doc = workspaceReducer(
      doc,
      {
        type: 'setInstrument',
        chartId: 'chart-1',
        instrumentId: 'mock:perp:ETHUSD',
      },
      3
    );
    doc = workspaceReducer(
      doc,
      {type: 'setChartType', chartId: 'chart-1', chartType: 'line'},
      4
    );
    doc = workspaceReducer(
      doc,
      {
        type: 'applyChartPresentation',
        chartId: 'chart-1',
        presentation: template.presentation,
      },
      5
    );
    expect(activeChart(doc).chart_type).toBe('heikin_ashi');
    expect(activeChart(doc).instrument_id).toBe('mock:perp:ETHUSD');
    expect(presentationOf(activeChart(doc))).toEqual(template.presentation);
  });

  it('rejects empty names and validates stored templates', () => {
    expect(() => createTemplate(activeChart(workspace()), ' ', 'x', 1)).toThrow(
      /name/
    );
    expect(isChartTemplate({schema_version: 2})).toBe(false);
  });

  it('saves newest first, replaces equal names and skips corrupt entries', () => {
    const storage = new MapStorage();
    const repo = new TemplateRepository(storage);
    const chart = activeChart(workspace());
    repo.save(createTemplate(chart, 'Swing', 'a', 1));
    repo.save(createTemplate(chart, 'Scalp', 'b', 2));
    repo.save(createTemplate(chart, 'swing', 'c', 3));
    expect(repo.list().map(t => t.template_id)).toEqual(['c', 'b']);
    const raw = JSON.parse(storage.getItem(TEMPLATE_STORAGE_KEY) ?? '[]');
    storage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify([...raw, {bad: 1}]));
    expect(repo.list()).toHaveLength(2);
    expect(repo.remove('c').map(t => t.template_id)).toEqual(['b']);
    storage.setItem(TEMPLATE_STORAGE_KEY, '{not json');
    expect(repo.list()).toEqual([]);
  });
});

describe('bar replay', () => {
  const times = Array.from({length: 300}, (_, i) => i * 60_000);
  const bars = times.map(timeMs => ({timeMs}));

  it('starts by waiting for a bar, defaulting to the lookback', () => {
    const state = startReplay();
    expect(state.phase).toBe('select');
    expect(replayVisibleBars(bars, state)).toHaveLength(300);
    const started = chooseReplayStart(state, times, null);
    expect(started.phase).toBe('paused');
    expect(started.cursorMs).toBe(times[299 - DEFAULT_REPLAY_LOOKBACK]);
  });

  it('snaps a clicked time to a bar and keeps at least one bar hidden', () => {
    const state = startReplay();
    expect(chooseReplayStart(state, times, 90_500).cursorMs).toBe(60_000);
    expect(chooseReplayStart(state, times, 10 ** 12).cursorMs).toBe(times[298]);
    expect(chooseReplayStart(state, [1], 1)).toBe(state);
  });

  it('steps, truncates bars, reports point-in-time and pauses at the end', () => {
    let state = chooseReplayStart(startReplay(), times, times[296] ?? 0);
    state = {...state, phase: 'playing'};
    state = stepReplay(state, times, 1);
    expect(state.cursorMs).toBe(times[297]);
    expect(replayVisibleBars(bars, state)).toHaveLength(298);
    expect(replayAsOfMs(state, 60_000)).toBe((times[297] ?? 0) + 60_000);
    state = stepReplay(state, times, 5);
    expect(state.cursorMs).toBe(times[299]);
    expect(state.phase).toBe('paused');
    expect(replayAtEnd(state, times)).toBe(true);
    state = stepReplay(state, times, -400);
    expect(state.cursorMs).toBe(0);
    expect(replayAsOfMs(null, 60_000)).toBeNull();
  });

  it('cycles playback speed', () => {
    let state = startReplay();
    const speeds = [state.speed];
    for (let i = 0; i < 4; i += 1) {
      state = nextReplaySpeed(state);
      speeds.push(state.speed);
    }
    expect(speeds).toEqual([1, 2, 5, 10, 1]);
  });
});

describe('custom timeframe input', () => {
  it.each([
    ['15', '15m'],
    ['15m', '15m'],
    ['4H', '4h'],
    ['2 hr', '2h'],
    ['1D', '1d'],
    ['D', '1d'],
    ['1w', '1w'],
    ['60', '1h'],
  ])('maps %s to %s', (input, expected) => {
    const parsed = parseTimeframeInput(input);
    expect(parsed).toEqual({ok: true, timeframe: expected});
  });

  it.each([
    ['10m', /not offered/],
    ['45s', /not offered/],
    ['2M', /not offered/],
    ['', /Type a resolution/],
    ['abc', /Type a resolution/],
    ['0m', /Type a resolution/],
  ])('rejects %s', (input, error) => {
    const parsed = parseTimeframeInput(input);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.error).toMatch(error);
  });
});

describe('resizable layout geometry', () => {
  it('falls back to equal tracks for mismatched saved sizes', () => {
    expect(gridSizesFor('4', {cols: [0.7, 0.3], rows: [0.5, 0.5]})).toEqual({
      cols: [0.7, 0.3],
      rows: [0.5, 0.5],
    });
    expect(gridSizesFor('8', {cols: [0.5, 0.5]}).cols).toHaveLength(
      LAYOUT_TRACKS['8'].cols
    );
    expect(gridSizesFor('2h', 'garbage').cols).toEqual([0.5, 0.5]);
  });

  it('moves a splitter within limits and keeps the pair total', () => {
    const resized = resizeTracks([0.5, 0.5], 0, 0.6);
    expect(resized[0]).toBeCloseTo(1 - MIN_TRACK_SHARE);
    expect((resized[0] ?? 0) + (resized[1] ?? 0)).toBeCloseTo(1);
    expect(resizeTracks([0.5, 0.5], 0, -0.1)).toEqual([0.4, 0.5 + 0.1]);
    expect(resizeTracks([1], 0, 0.1)).toEqual([1]);
    expect(tracksToTemplate([0.25, 0.75])).toBe(
      'minmax(0, 0.2500fr) minmax(0, 0.7500fr)'
    );
    expect(clampDock(900, 240, 640)).toBe(640);
    expect(clampDock(10.4, 0, 100)).toBe(10);
  });
});
