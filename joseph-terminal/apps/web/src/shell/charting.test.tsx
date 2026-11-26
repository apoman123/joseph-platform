/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component and unit tests for the charting additions:
 * indicator settings form and dialog, metadata-driven indicator layers,
 * lines and study panes, templates and compare menus, replay
 * and screenshot commands, the data window, the style bar and the object
 * list (jsdom; canvas rendering is covered by Playwright).
 */

import {FakeChartPort, type ChartBar} from '@joseph/chart-core';
import type {DrawingDocument} from '@joseph/contracts';
import {createDrawing, createCommand, DrawingStore} from '@joseph/drawing-core';
import {
  activeChart,
  layerSettings,
  layerStyle,
  layerVisible,
} from '@joseph/terminal-shell';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {describe, expect, it} from 'vitest';
import {composeFeatures} from '../composition/features';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {ObjectsPanel, describeDrawing} from '../features/drawing/objects-panel';
import {
  screenshotCaption,
  screenshotFileName,
} from '../features/shell/chart-tools-feature';
import {DataWindow} from './data-window';
import {DrawingStyleBar} from './drawing-style-bar';
import {groupTools, hotkeyHint} from './drawing-toolbar';
import {IndicatorSettingsDialog} from './indicator-settings-dialog';
import {
  collectOverrides,
  fieldLabel,
  fieldsFromSchema,
  initialValues,
  parseFieldValue,
} from './indicator-settings';
import {useStore} from './react-bindings';
import {RuntimeProvider} from './runtime-context';
import {savedDockSize} from './slot-host';
import {useStudyLines, useStudyPanes} from './study-hooks';
import {TopBar} from './top-bar';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

/**
 * Creates and activates a DEMO runtime on a fresh workspace (an earlier
 * test's debounced autosave must not leak into the next test).
 * @return Runtime.
 */
async function demoRuntime(): Promise<TerminalRuntime> {
  window.localStorage.clear();
  const runtime = createRuntime(composeFeatures, DEMO);
  await runtime.registry.activateAll();
  return runtime;
}

const SCHEMA = {
  type: 'object',
  properties: {
    length: {
      type: 'integer',
      default: 20,
      minimum: 1,
      maximum: 500,
      description: 'Number of bars.',
    },
    min_coverage: {
      type: 'string',
      default: '0.8',
      minimum: '0.5',
      maximum: '1',
      description: 'Share.',
    },
    source: {
      type: 'string',
      enum: ['close', 'hl2'],
      default: 'close',
      description: 'Price.',
    },
    enabled: {type: 'boolean', default: true, description: 'On.'},
    tick_size: {type: 'string', default: '0', description: 'Platform.'},
    weird: {type: 'array'},
  },
};

describe('indicator settings form helpers', () => {
  const fields = fieldsFromSchema(SCHEMA);

  it('reads fields from the engine schema and hides platform inputs', () => {
    expect(fields.map(f => [f.name, f.kind])).toEqual([
      ['length', 'integer'],
      ['min_coverage', 'decimal'],
      ['source', 'enum'],
      ['enabled', 'boolean'],
    ]);
    expect(fieldsFromSchema(null)).toEqual([]);
    expect(fieldLabel('min_coverage')).toBe('Min coverage');
  });

  it('validates bounds, decimals and choices', () => {
    const [length, coverage, source] = fields;
    if (!length || !coverage || !source) throw new Error('fields');
    expect(parseFieldValue(length, '0')).toEqual({
      ok: false,
      error: 'At least 1',
    });
    expect(parseFieldValue(length, '2.5')).toMatchObject({ok: false});
    expect(parseFieldValue(length, '501')).toEqual({
      ok: false,
      error: 'At most 500',
    });
    expect(parseFieldValue(coverage, '1.01')).toEqual({
      ok: false,
      error: 'At most 1',
    });
    expect(parseFieldValue(coverage, '0.4')).toEqual({
      ok: false,
      error: 'At least 0.5',
    });
    expect(parseFieldValue(coverage, '.5')).toMatchObject({ok: false});
    expect(parseFieldValue(coverage, '0.9')).toEqual({ok: true, value: '0.9'});
    expect(parseFieldValue(source, 'ohlc5')).toMatchObject({ok: false});
  });

  it('keeps only values that differ from the defaults', () => {
    const values = initialValues(fields, {length: 50});
    expect(values.length).toBe('50');
    expect(collectOverrides(fields, {...values, min_coverage: '0.8'})).toEqual({
      ok: true,
      overrides: {length: 50},
    });
    expect(collectOverrides(fields, {...values, length: 'x'})).toMatchObject({
      ok: false,
      errors: {length: 'Enter a whole number'},
    });
  });
});

describe('IndicatorSettingsDialog', () => {
  it('saves validated inputs, kinds and colors on the chart layer', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorSettingsDialog />
      </RuntimeProvider>
    );
    act(
      () =>
        void runtime.indicatorSettings.set({
          chartId: 'chart-1',
          pluginId: 'volume_delta',
        })
    );
    const dialog = await screen.findByTestId('indicator-settings');
    const coverage = await within(dialog).findByLabelText('Min coverage');
    fireEvent.change(coverage, {target: {value: '1.5'}});
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    expect(await within(dialog).findByText('At most 1')).toBeInTheDocument();
    fireEvent.change(coverage, {target: {value: '0.9'}});
    fireEvent.click(within(dialog).getByTestId('indicator-pane-toggle-delta'));
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    await waitFor(
      () => void expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
    const chart = activeChart(runtime.workspace.get());
    expect(layerSettings(chart, 'volume_delta')).toEqual({
      min_coverage: '0.9',
    });
    expect(layerStyle(chart, 'volume_delta').panes).toEqual({delta: false});
  });

  it('filters kinds per chart and closes with Escape', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorSettingsDialog />
      </RuntimeProvider>
    );
    act(
      () =>
        void runtime.indicatorSettings.set({
          chartId: 'chart-1',
          pluginId: 'pivot_points',
        })
    );
    const dialog = await screen.findByTestId('indicator-settings');
    // Kinds are labelled from the plugin's display metadata.
    fireEvent.click(await within(dialog).findByRole('checkbox', {name: 'R3'}));
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    await waitFor(
      () => void expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
    const layer = activeChart(runtime.workspace.get()).indicator_layers.find(
      l => l.plugin_id === 'pivot_points'
    );
    expect(layer?.kinds).toEqual([
      'PIVOT_P',
      'PIVOT_R1',
      'PIVOT_R2',
      'PIVOT_S1',
      'PIVOT_S2',
      'PIVOT_S3',
    ]);
    act(
      () =>
        void runtime.indicatorSettings.set({
          chartId: 'chart-1',
          pluginId: 'pivot_points',
        })
    );
    await screen.findByTestId('indicator-settings');
    fireEvent.keyDown(window, {key: 'Escape'});
    await waitFor(
      () => void expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
  });
});

describe('TopBar chart tools', () => {
  it('toggles replay, saves and applies a template and opens settings', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByTestId('replay-toggle'));
    await waitFor(
      () => void expect(runtime.replay.get()['chart-1']?.phase).toBe('select')
    );
    expect(runtime.selection.get().replay).toBe(true);
    fireEvent.click(screen.getByTestId('replay-toggle'));
    await waitFor(
      () => void expect(runtime.selection.get().replay).toBe(false)
    );

    runtime.dispatch({
      type: 'setChartType',
      chartId: 'chart-1',
      chartType: 'heikin_ashi',
    });
    fireEvent.click(screen.getByTestId('templates-menu'));
    fireEvent.change(screen.getByLabelText('Template name'), {
      target: {value: 'HA swing'},
    });
    fireEvent.click(screen.getByTestId('template-save'));
    expect(runtime.templates.list().map(t => t.name)).toEqual(['HA swing']);
    runtime.dispatch({
      type: 'setChartType',
      chartId: 'chart-1',
      chartType: 'line',
    });
    fireEvent.click(await screen.findByTestId('template-apply-HA swing'));
    expect(activeChart(runtime.workspace.get()).chart_type).toBe('heikin_ashi');

    fireEvent.click(screen.getByTestId('indicators-menu'));
    fireEvent.click(
      await screen.findByTestId('indicator-settings-pivot_points')
    );
    expect(runtime.indicatorSettings.get()).toEqual({
      chartId: 'chart-1',
      pluginId: 'pivot_points',
    });
    fireEvent.click(screen.getByTestId('session-separators-toggle'));
    expect(activeChart(runtime.workspace.get()).session_separators).toBe(true);
  });

  it('applies a typed custom resolution and explains unsupported ones', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'More'}));
    const input = screen.getByLabelText('Type a resolution');
    fireEvent.change(input, {target: {value: '7m'}});
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(await screen.findByRole('alert')).toHaveTextContent('not offered');
    fireEvent.change(input, {target: {value: '4H'}});
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(activeChart(runtime.workspace.get()).timeframe).toBe('4h');
  });

  it('adds and removes compared symbols', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByTestId('compare-menu'));
    fireEvent.change(screen.getByLabelText('Add a symbol'), {
      target: {value: 'ETH'},
    });
    fireEvent.click(await screen.findByTestId('compare-add-mock:perp:ETHUSD'));
    expect(activeChart(runtime.workspace.get()).compare).toEqual([
      'mock:perp:ETHUSD',
    ]);
    fireEvent.click(
      screen.getByRole('button', {name: 'Stop comparing ETHUSD'})
    );
    expect(activeChart(runtime.workspace.get()).compare).toBeUndefined();
  });
});

describe('screenshots', () => {
  it('names files and captions provenance', async () => {
    const runtime = await demoRuntime();
    const chart = activeChart(runtime.workspace.get());
    const now = Date.UTC(2026, 10, 22, 15, 4);
    expect(screenshotFileName(chart, now)).toBe(
      'joseph-BTCUSD-5m-20261122-1504.png'
    );
    expect(screenshotCaption(chart, 'DEMO', now)).toBe(
      'BTCUSD · Demo venue · 5m · Demo data · 2026-11-22 15:04 UTC · Joseph Terminal'
    );
  });

  it('warns instead of downloading when no chart is mounted', async () => {
    const runtime = await demoRuntime();
    await runtime.commands.execute('chart.screenshot');
    expect(runtime.toasts.get().at(-1)?.message).toMatch(/not ready/);
  });
});

describe('DataWindow', () => {
  it('shows OHLCV, change, Delta % and compared closes for the hovered bar', () => {
    const bar: ChartBar = {
      timeMs: Date.UTC(2026, 0, 2, 3, 5),
      open: 100,
      high: 110,
      low: 95,
      close: 105,
      volume: 12.5,
      final: true,
    };
    render(
      <DataWindow
        bar={bar}
        timeframe="5m"
        previousClose={100}
        tickSize="0.1"
        delta={
          new Map([
            [
              bar.timeMs,
              {
                timeMs: bar.timeMs,
                value: -22.5,
                method: 'estimate' as const,
                buyVolume: null,
                sellVolume: null,
              },
            ],
          ])
        }
        compare={[
          {
            instrumentId: 'mock:perp:ETHUSD',
            color: '#fff',
            values: new Map([[bar.timeMs, 3400.25]]),
            error: null,
          },
        ]}
        label={() => 'ETHUSD'}
      />
    );
    const panel = screen.getByTestId('data-window');
    expect(panel).toHaveTextContent('2026-01-02 03:05 UTC');
    expect(panel).toHaveTextContent('+5.00%');
    expect(panel).toHaveTextContent('−22.50%');
    expect(panel).toHaveTextContent('3,400.2500');
  });
});

/**
 * Seeds a store with one drawing and registers it for chart-1.
 * @param runtime Runtime to register the store with.
 * @param toolId Tool id of the drawing.
 * @return Store and drawing id.
 */
function seed(runtime: TerminalRuntime, toolId: string) {
  const store = new DrawingStore();
  const doc = createDrawing({
    toolId,
    toolVersion: 1,
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: null,
    anchors: [
      {time_ms: 0, price: '1'},
      {time_ms: 60_000, price: '2'},
    ],
    style: {
      color: '#e66fd2',
      line_width: 1,
      line_style: 'solid',
      fill_color: '#e66fd226',
    },
    zIndex: 1,
    nowMs: 1,
    newId: () => 'd1',
  });
  store.execute(createCommand(doc));
  runtime.drawing.stores.set('chart-1', store);
  return {store, id: doc.drawing_id};
}

describe('drawing editing UI', () => {
  it('restyles the selected drawing through undoable commands', async () => {
    const runtime = await demoRuntime();
    const {store, id} = seed(runtime, 'rectangle');
    runtime.drawing.selection.set({chartId: 'chart-1', drawingId: id});
    render(
      <RuntimeProvider runtime={runtime}>
        <DrawingStyleBar chartId="chart-1" />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'Color #3cc79a'}));
    expect(store.find(id)?.style).toMatchObject({
      color: '#3cc79a',
      fill_color: '#3cc79a26',
    });
    fireEvent.change(screen.getByLabelText('Line width'), {
      target: {value: '3'},
    });
    fireEvent.click(screen.getByRole('checkbox', {name: 'Fill'}));
    expect(store.find(id)?.style.fill_color).toBeNull();
    fireEvent.click(screen.getByTestId('style-lock'));
    expect(store.find(id)?.locked).toBe(true);
    expect(screen.getByRole('button', {name: 'Delete drawing'})).toBeDisabled();
    store.undo();
    store.undo();
    expect(store.find(id)?.style.line_width).toBe(3);
  });

  it('lists objects with visibility, lock and delete', async () => {
    const runtime = await demoRuntime();
    const {store, id} = seed(runtime, 'trendline');
    render(
      <RuntimeProvider runtime={runtime}>
        <ObjectsPanel runtime={runtime} />
      </RuntimeProvider>
    );
    const row = screen.getByTestId('object-trendline');
    fireEvent.click(within(row).getByRole('button', {name: 'Hide Trendline'}));
    expect(store.find(id)?.visible).toBe(false);
    fireEvent.click(
      within(screen.getByTestId('object-trendline')).getByRole('button', {
        name: /^Trendline/,
      })
    );
    expect(runtime.drawing.selection.get()).toEqual({
      chartId: 'chart-1',
      drawingId: id,
    });
    fireEvent.click(
      within(screen.getByTestId('object-trendline')).getByRole('button', {
        name: 'Delete Trendline',
      })
    );
    expect(store.find(id)).toBeUndefined();
    // Deliberately malformed: a drawing whose tool is not registered.
    const orphan = {
      ...(store.get().drawings[0] ?? {}),
      tool_id: 'gone',
    } as unknown as DrawingDocument;
    expect(describeDrawing(runtime, orphan).name).toBe('Unsupported (gone)');
  });

  it('groups toolbar tools and restores saved dock sizes within limits', async () => {
    const runtime = await demoRuntime();
    const groups = groupTools(runtime.tools.list());
    expect(groups.map(g => g.group)).toEqual([
      'lines',
      'shapes',
      'fibonacci',
      'gann',
      'patterns',
      'trading',
      'measure',
      'studies',
      'annotation',
    ]);
    expect(groups[1]?.tools.map(t => t.manifest.id)).toEqual([
      'rectangle',
      'zone-box',
      'polyline',
      'ellipse',
      'circle',
      'triangle',
      'rotated-rectangle',
      'arc',
      'curve',
    ]);
    expect(groups[7]?.tools.map(t => t.manifest.id)).toEqual([
      'anchored-vwap',
      'regression-channel',
    ]);
    expect(savedDockSize('5000', 'side')).toBe(720);
    expect(savedDockSize('abc', 'bottom')).toBeNull();
  });

  it('shows tool hotkeys with capitalized modifiers like the top bar', () => {
    expect(hotkeyHint('alt+t')).toBe('Alt + T');
    expect(hotkeyHint('alt+shift+r')).toBe('Alt + Shift + R');
  });
});

/**
 * Mounts the study hooks of the active chart on a fake chart port.
 * @param props Runtime and port.
 * @return Nothing; the hooks draw on the port.
 */
function Studies({
  runtime,
  port,
}: {
  runtime: TerminalRuntime;
  port: FakeChartPort;
}): null {
  const chart = activeChart(useStore(runtime.workspace));
  useStudyPanes(port, chart, runtime, null);
  useStudyLines(port, chart, runtime, null);
  return null;
}

describe('indicator studies from plugin metadata', () => {
  it('registers record layers and toggle commands from the catalogue', async () => {
    const runtime = await demoRuntime();
    await waitFor(
      () =>
        void expect(runtime.commands.list().map(c => c.id)).toContain(
          'indicators.toggle.pivot_points'
        )
    );
    const ids = runtime.commands.list().map(c => c.id);
    // Plot-only plugins get a toggle; drawing-only plugins get none.
    expect(ids).toContain('indicators.toggle.ema');
    expect(ids).not.toContain('indicators.toggle.regression_channel');
    await runtime.commands.execute('indicators.toggle.rsi');
    expect(layerVisible(activeChart(runtime.workspace.get()), 'rsi')).toBe(
      true
    );
  });

  it('draws price lines and declared panes for visible plugins', async () => {
    const runtime = await demoRuntime();
    const port = new FakeChartPort();
    render(<Studies runtime={runtime} port={port} />);
    // EMA is visible by default and plots on the price pane.
    await waitFor(
      () => void expect(port.canvasLayers.has('study-lines:ema')).toBe(true)
    );
    await waitFor(() => void expect(port.invalidations).toBeGreaterThan(0));
    expect(port.studyPanes.has('rsi:rsi')).toBe(false);
    act(
      () =>
        void runtime.dispatch({
          type: 'setIndicatorLayer',
          chartId: 'chart-1',
          pluginId: 'rsi',
          visible: true,
        })
    );
    await waitFor(
      () =>
        void expect(
          port.studyPanes.get('rsi:rsi')?.points.length
        ).toBeGreaterThan(0)
    );
    expect(port.studyPanes.get('rsi:rsi')).toMatchObject({
      title: 'RSI',
      style: 'line',
    });
    expect(port.studyPanes.get('rsi:rsi')?.levels?.map(l => l.value)).toEqual([
      70, 50, 30,
    ]);
    act(
      () =>
        void runtime.dispatch({
          type: 'setIndicatorLayerConfig',
          chartId: 'chart-1',
          pluginId: 'rsi',
          patch: {style: {panes: {rsi: false}}},
        })
    );
    await waitFor(
      () => void expect(port.studyPanes.has('rsi:rsi')).toBe(false)
    );
    act(
      () =>
        void runtime.dispatch({
          type: 'setIndicatorLayer',
          chartId: 'chart-1',
          pluginId: 'ema',
          visible: false,
        })
    );
    await waitFor(
      () => void expect(port.canvasLayers.has('study-lines:ema')).toBe(false)
    );
  });
});
