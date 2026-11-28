/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component and unit tests for the charting additions:
 * the study settings form and dialog, study runs on the chart, templates
 * and compare menus, replay
 * and screenshot commands, the data window, the style bar and the object
 * list (jsdom; canvas rendering is covered by Playwright).
 */

import {FakeChartPort, type ChartBar} from '@joseph/chart-core';
import type {DrawingDocument} from '@joseph/contracts';
import {createDrawing, createCommand, DrawingStore} from '@joseph/drawing-core';
import {activeChart} from '@joseph/terminal-shell';
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
import {
  IndicatorSettingsDialog,
  collectInputs,
  studyFields,
} from './indicator-settings-dialog';
import {useStudyRuns} from './python-studies';
import {useStore} from './react-bindings';
import {RuntimeProvider} from './runtime-context';
import {savedDockSize} from './slot-host';
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

/** Declared inputs of a study, as the catalog lists them. */
const INPUTS = {
  length: {
    default: 20,
    title: 'Length',
    minimum: 1,
    maximum: 500,
    options: null,
    step: 1,
    group: '',
    tooltip: '',
  },
  source: {
    default: 'close',
    title: 'Source',
    minimum: null,
    maximum: null,
    options: ['close', 'hl2'],
    step: null,
    group: '',
    tooltip: '',
  },
  color: {
    default: '#2962ff',
    title: 'Color',
    minimum: null,
    maximum: null,
    options: null,
    step: null,
    group: 'Style',
    tooltip: '',
  },
};

describe('study settings form helpers', () => {
  it('builds fields from declared inputs or from saved owned inputs', () => {
    expect(studyFields(INPUTS, {}).map(f => f.name)).toEqual([
      'length',
      'source',
      'color',
    ]);
    const owned = studyFields(null, {fast: 5});
    expect(owned.map(f => [f.name, f.spec.default])).toEqual([['fast', 5]]);
  });

  it('validates bounds and colors and keeps only changed values', () => {
    const fields = studyFields(INPUTS, {});
    expect(
      collectInputs(fields, {length: '30', source: 'close', color: '#2962ff'})
    ).toEqual({ok: true, inputs: {length: 30}});
    expect(
      collectInputs(fields, {length: '0', source: 'hl2', color: 'red'})
    ).toEqual({
      ok: false,
      errors: {length: 'At least 1', color: 'Use a #rrggbb color'},
    });
    expect(
      collectInputs(fields, {length: 'x', source: 'hl2', color: '#000000'})
    ).toMatchObject({ok: false, errors: {length: 'Enter a number'}});
  });
});

describe('IndicatorSettingsDialog', () => {
  it('saves validated inputs on the chart study and closes with Escape', async () => {
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
          studyId: 'builtin:ema',
        })
    );
    const dialog = await screen.findByTestId('indicator-settings');
    const length = await within(dialog).findByLabelText('Length');
    fireEvent.change(length, {target: {value: '0'}});
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    expect(within(dialog).getByText('At least 1')).toBeVisible();
    fireEvent.change(length, {target: {value: '9'}});
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    expect(
      activeChart(runtime.workspace.get()).studies.find(
        study => study.study_id === 'builtin:ema'
      )?.inputs
    ).toEqual({length: 9});
    expect(screen.queryByTestId('indicator-settings')).toBeNull();
    act(
      () =>
        void runtime.indicatorSettings.set({
          chartId: 'chart-1',
          studyId: 'builtin:volume_delta',
        })
    );
    await screen.findByTestId('indicator-settings');
    fireEvent.keyDown(window, {key: 'Escape'});
    expect(screen.queryByTestId('indicator-settings')).toBeNull();
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
      await screen.findByTestId('study-settings-builtin:pivot_points')
    );
    expect(runtime.indicatorSettings.get()).toEqual({
      chartId: 'chart-1',
      studyId: 'builtin:pivot_points',
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
        studyRows={[{key: 'delta', label: 'Delta %', value: '−22.50%'}]}
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
    expect(
      groups[1]?.tools
        .filter(t => !t.manifest.id.startsWith('reference.'))
        .map(t => t.manifest.id)
    ).toEqual([
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
    expect(
      groups[7]?.tools
        .filter(t => !t.manifest.id.startsWith('reference.'))
        .map(t => t.manifest.id)
    ).toEqual(['anchored-vwap', 'regression-channel']);
    expect(savedDockSize('5000', 'side')).toBe(720);
    expect(savedDockSize('abc', 'bottom')).toBeNull();
  });

  it('shows tool hotkeys with capitalized modifiers like the top bar', () => {
    expect(hotkeyHint('alt+t')).toBe('Alt + T');
    expect(hotkeyHint('alt+shift+r')).toBe('Alt + Shift + R');
  });
});

/**
 * Runs the active chart's studies on a fake chart port.
 * @param props Runtime and port.
 * @return Nothing; the hook draws on the port.
 */
function Studies({
  runtime,
  port,
}: {
  runtime: TerminalRuntime;
  port: FakeChartPort;
}): null {
  const chart = activeChart(useStore(runtime.workspace));
  useStudyRuns(port, chart, runtime, Date.now());
  return null;
}

describe('chart studies', () => {
  it('draws overlay studies on the price pane and others in panes', async () => {
    const runtime = await demoRuntime();
    const port = new FakeChartPort();
    render(<Studies runtime={runtime} port={port} />);
    // EMA and pivot points overlay the price pane; volume delta has a pane.
    await waitFor(
      () =>
        void expect(port.canvasLayers.has('study:chart-1:builtin:ema')).toBe(
          true
        )
    );
    const delta = 'study:chart-1:builtin:volume_delta';
    await waitFor(() => void expect(port.studyPanes.has(delta)).toBe(true));
    expect(port.studyPanes.get(delta)).toMatchObject({style: 'histogram'});
    expect(port.studyPanes.get(delta)?.points.length).toBeGreaterThan(0);
    act(
      () =>
        void runtime.dispatch({
          type: 'updateStudy',
          chartId: 'chart-1',
          studyId: 'builtin:ema',
          patch: {visible: false},
        })
    );
    await waitFor(
      () =>
        void expect(port.canvasLayers.has('study:chart-1:builtin:ema')).toBe(
          false
        )
    );
    act(
      () =>
        void runtime.dispatch({
          type: 'removeStudy',
          chartId: 'chart-1',
          studyId: 'builtin:volume_delta',
        })
    );
    await waitFor(() => void expect(port.studyPanes.has(delta)).toBe(false));
  });
});
