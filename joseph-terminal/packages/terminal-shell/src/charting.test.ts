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
import {parseWorkspace} from './persistence';
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
  migrateWorkspaceV1,
  studiesFromAttachments,
  studiesFromLayers,
} from './study-migration';
import {
  activeChart,
  createDefaultWorkspace,
  nextStudyId,
  workspaceReducer,
  type StudyState,
} from './workspace';

/**
 * Creates a fresh single-chart workspace on `mock:perp:BTCUSD` 5m.
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

/** In-memory `WorkspaceStorage` backed by a map. */
class MapStorage {
  readonly map = new Map<string, string>();

  /**
   * Reads a stored string.
   * @param key Storage key.
   * @return Stored text, or null when absent.
   */
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }

  /**
   * Stores a string.
   * @param key Storage key.
   * @param value Text to store.
   */
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

/** A built-in RSI study. */
const RSI: StudyState = {
  study_id: 'builtin:rsi',
  script_id: 'builtin:rsi',
  version: 0,
  kind: 'indicator',
  name: 'RSI',
  visible: true,
  inputs: {},
  colors: {},
};

describe('studies', () => {
  it('ships EMA, volume delta and pivot points on new charts', () => {
    const chart = activeChart(workspace());
    expect(chart.studies.map(study => study.script_id)).toEqual([
      'builtin:ema',
      'builtin:volume_delta',
      'builtin:pivot_points',
    ]);
    expect(chart.studies.every(study => study.visible)).toBe(true);
    expect(chart.studies.every(study => study.version === 0)).toBe(true);
  });

  it('adds, updates and removes studies by study id', () => {
    let doc = workspaceReducer(
      workspace(),
      {type: 'addStudy', chartId: 'chart-1', study: RSI},
      2
    );
    const second = nextStudyId(activeChart(doc), 'builtin:rsi');
    expect(second).toBe('builtin:rsi#2');
    doc = workspaceReducer(
      doc,
      {
        type: 'addStudy',
        chartId: 'chart-1',
        study: {...RSI, study_id: second, inputs: {length: 7}},
      },
      3
    );
    doc = workspaceReducer(
      doc,
      {
        type: 'updateStudy',
        chartId: 'chart-1',
        studyId: 'builtin:rsi',
        patch: {visible: false, inputs: {length: 21}, colors: {RSI: '#112233'}},
      },
      4
    );
    const rsi = activeChart(doc).studies.filter(
      study => study.script_id === 'builtin:rsi'
    );
    expect(rsi.map(study => [study.visible, study.inputs])).toEqual([
      [false, {length: 21}],
      [true, {length: 7}],
    ]);
    expect(rsi[0]?.colors).toEqual({RSI: '#112233'});
    doc = workspaceReducer(
      doc,
      {type: 'removeStudy', chartId: 'chart-1', studyId: 'builtin:rsi'},
      5
    );
    expect(activeChart(doc).studies.map(study => study.study_id)).toEqual([
      'builtin:ema',
      'builtin:volume_delta',
      'builtin:pivot_points',
      'builtin:rsi#2',
    ]);
  });
});

describe('schema v1 migration', () => {
  it('turns shown and added built-in layers into studies', () => {
    const studies = studiesFromLayers([
      {plugin_id: 'ema', visible: true, settings: {length: 9, tick_size: '1'}},
      {plugin_id: 'sma', visible: false},
      {plugin_id: 'rsi', visible: false, present: true},
      {plugin_id: 'atr', visible: false, removed: true},
      {
        plugin_id: 'volume_delta',
        visible: true,
        settings: {delta_source: 'estimate', min_coverage: '0.9'},
        style: {colors: {delta_pct: '#00ff00', other: '#ffffff'}},
      },
      {plugin_id: 'bollinger_bands', visible: true, settings: {mult: '2.5'}},
      {plugin_id: 'private_zones', visible: true},
    ]);
    expect(
      studies.map(study => [study.script_id, study.visible, study.inputs])
    ).toEqual([
      ['builtin:ema', true, {length: 9}],
      ['builtin:rsi', false, {}],
      [
        'builtin:volume_delta',
        true,
        {use_buy_volume: false, min_coverage: 0.9},
      ],
      ['builtin:bollinger_bands', true, {mult: 2.5}],
    ]);
    expect(studies[2]?.colors).toEqual({'Delta %': '#00ff00'});
  });

  it('keeps attached owned scripts and drops malformed ones', () => {
    expect(
      studiesFromAttachments([
        {
          script_id: 'my-trend',
          kind: 'strategy',
          version: 3,
          name: 'My trend',
          visible: false,
          inputs: {fast: 5},
          colors: {Fast: '#abcdef', Bad: 'red'},
        },
        {script_id: 'Bad Id', version: 1, name: 'x'},
        {script_id: 'no-version', name: 'x'},
      ])
    ).toEqual([
      {
        study_id: 'my-trend',
        script_id: 'my-trend',
        version: 3,
        kind: 'strategy',
        name: 'My trend',
        visible: false,
        inputs: {fast: 5},
        colors: {Fast: '#abcdef'},
      },
    ]);
  });

  it('migrates stored v1 workspaces, layers and attachments', () => {
    const current = workspace();
    const {studies: _studies, ...chart} = current.charts[0]!;
    const v1 = {
      ...current,
      schema_version: 1,
      charts: [
        {
          ...chart,
          indicator_layers: [
            {plugin_id: 'ema', visible: true},
            {plugin_id: 'pivot_points', visible: false, present: true},
          ],
        },
      ],
      panels: {
        'python.chart-1': [
          {script_id: 'mine', version: 2, name: 'Mine', visible: true, inputs: {}, colors: {}},
        ],
        other: {kept: true},
      },
    };
    const migrated = migrateWorkspaceV1(v1) as typeof current;
    expect(migrated.schema_version).toBe(2);
    expect(migrated.panels).toEqual({other: {kept: true}});
    expect(
      migrated.charts[0]?.studies.map(study => [study.study_id, study.visible])
    ).toEqual([
      ['builtin:ema', true],
      ['builtin:pivot_points', false],
      ['mine', true],
    ]);
    const parsed = parseWorkspace(JSON.stringify(v1), 5);
    expect(parsed.ok && parsed.migrated).toBe(true);
    expect(parsed.ok && parsed.workspace.charts[0]?.studies.length).toBe(3);
  });

  it('migrates v1 templates when loading them', () => {
    const storage = new MapStorage();
    storage.setItem(
      TEMPLATE_STORAGE_KEY,
      JSON.stringify([
        {
          schema_version: 1,
          template_id: 't1',
          name: 'Old',
          created_at_ms: 1,
          presentation: {
            chart_type: 'candles',
            price_scale: 'linear',
            show_volume: true,
            indicator_layers: [{plugin_id: 'rsi', visible: true}],
          },
        },
      ])
    );
    const [template] = new TemplateRepository(storage).list();
    expect(template?.schema_version).toBe(2);
    expect(template?.presentation.studies.map(study => study.script_id)).toEqual(
      ['builtin:rsi']
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
    ['7m', /not offered/],
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
