/**
 * @fileoverview Component and unit tests for the charting additions:
 * indicator settings form and dialog, templates and compare menus, replay
 * and screenshot commands, the data window, the style bar and the object
 * list (jsdom; canvas rendering is covered by Playwright).
 */
import type {ChartBar} from '@joseph/chart-core';
import {createDrawing, createCommand, DrawingStore} from '@joseph/drawing-core';
import {activeChart, layerSettings, layerStyle} from '@joseph/terminal-shell';
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
import {groupTools} from './drawing-toolbar';
import {IndicatorSettingsDialog} from './indicator-settings-dialog';
import {
  collectOverrides,
  fieldLabel,
  fieldsFromSchema,
  initialValues,
  parseFieldValue,
} from './indicator-settings';
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
 * Creates and activates a DEMO runtime.
 * @return Runtime.
 */
async function demoRuntime(): Promise<TerminalRuntime> {
  const runtime = createRuntime(composeFeatures, DEMO);
  await runtime.registry.activateAll();
  return runtime;
}

const SCHEMA = {
  type: 'object',
  properties: {
    swing_length: {
      type: 'integer',
      default: 3,
      minimum: 2,
      maximum: 20,
      description: 'Pivot bars.',
    },
    min_delta_share: {
      type: 'string',
      default: '0.20',
      minimum: '0',
      maximum: '1',
      description: 'Share.',
    },
    intrabar_tf: {
      type: 'string',
      enum: ['1m', '5m'],
      default: '1m',
      description: 'Lower timeframe.',
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
      ['swing_length', 'integer'],
      ['min_delta_share', 'decimal'],
      ['intrabar_tf', 'enum'],
      ['enabled', 'boolean'],
    ]);
    expect(fieldsFromSchema(null)).toEqual([]);
    expect(fieldLabel('min_delta_share')).toBe('Min delta share');
  });

  it('validates bounds, decimals and choices', () => {
    const [swing, share, tf] = fields;
    if (!swing || !share || !tf) throw new Error('fields');
    expect(parseFieldValue(swing, '1')).toEqual({
      ok: false,
      error: 'At least 2',
    });
    expect(parseFieldValue(swing, '2.5')).toMatchObject({ok: false});
    expect(parseFieldValue(swing, '21')).toEqual({
      ok: false,
      error: 'At most 20',
    });
    expect(parseFieldValue(share, '1.01')).toEqual({
      ok: false,
      error: 'At most 1',
    });
    expect(parseFieldValue(share, '.5')).toMatchObject({ok: false});
    expect(parseFieldValue(share, '0.35')).toEqual({ok: true, value: '0.35'});
    expect(parseFieldValue(tf, '3m')).toMatchObject({ok: false});
  });

  it('keeps only values that differ from the defaults', () => {
    const values = initialValues(fields, {swing_length: 5});
    expect(values.swing_length).toBe('5');
    expect(
      collectOverrides(fields, {...values, min_delta_share: '0.2'})
    ).toEqual({
      ok: true,
      overrides: {swing_length: 5},
    });
    expect(
      collectOverrides(fields, {...values, swing_length: 'x'})
    ).toMatchObject({
      ok: false,
      errors: {swing_length: 'Enter a whole number'},
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
    act(() =>
      runtime.indicatorSettings.set({
        chartId: 'chart-1',
        pluginId: 'order_flow_pressure',
      })
    );
    const dialog = await screen.findByTestId('indicator-settings');
    const share = await within(dialog).findByLabelText('Min delta share');
    fireEvent.change(share, {target: {value: '2'}});
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    expect(await within(dialog).findByText('At most 1')).toBeInTheDocument();
    fireEvent.change(share, {target: {value: '0.35'}});
    fireEvent.click(within(dialog).getByTestId('indicator-pane-toggle'));
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    await waitFor(() =>
      expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
    const chart = activeChart(runtime.workspace.get());
    expect(layerSettings(chart, 'order_flow_pressure')).toEqual({
      min_delta_share: '0.35',
    });
    expect(layerStyle(chart, 'order_flow_pressure').pane).toBe(false);
  });

  it('filters kinds per chart and closes with Escape', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorSettingsDialog />
      </RuntimeProvider>
    );
    act(() =>
      runtime.indicatorSettings.set({chartId: 'chart-1', pluginId: 'fvg_ifvg'})
    );
    const dialog = await screen.findByTestId('indicator-settings');
    fireEvent.click(
      await within(dialog).findByRole('checkbox', {name: 'IFVG'})
    );
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    await waitFor(() =>
      expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
    const layer = activeChart(runtime.workspace.get()).indicator_layers.find(
      l => l.plugin_id === 'fvg_ifvg'
    );
    expect(layer?.kinds).toEqual(['FVG']);
    act(() =>
      runtime.indicatorSettings.set({chartId: 'chart-1', pluginId: 'fvg_ifvg'})
    );
    await screen.findByTestId('indicator-settings');
    fireEvent.keyDown(window, {key: 'Escape'});
    await waitFor(() =>
      expect(screen.queryByTestId('indicator-settings')).toBeNull()
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
    await waitFor(() =>
      expect(runtime.replay.get()['chart-1']?.phase).toBe('select')
    );
    expect(runtime.selection.get().replay).toBe(true);
    fireEvent.click(screen.getByTestId('replay-toggle'));
    await waitFor(() => expect(runtime.selection.get().replay).toBe(false));

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
    fireEvent.click(screen.getByTestId('indicator-settings-market_structure'));
    expect(runtime.indicatorSettings.get()).toEqual({
      chartId: 'chart-1',
      pluginId: 'market_structure',
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
    fireEvent.change(input, {target: {value: '10m'}});
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
        previousClose={100}
        tickSize="0.1"
        delta={new Map([[bar.timeMs, -22.5]])}
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
 * Seeds a store with one drawing.
 * @param runtime Runtime.
 * @param toolId Tool.
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
    expect(
      describeDrawing(runtime, {
        ...(store.get().drawings[0] ?? {}),
        tool_id: 'gone',
      } as never).name
    ).toBe('Unsupported (gone)');
  });

  it('groups toolbar tools and restores saved dock sizes within limits', async () => {
    const runtime = await demoRuntime();
    const groups = groupTools(runtime.tools.list());
    expect(groups.map(g => g.group)).toEqual([
      'lines',
      'shapes',
      'fibonacci',
      'trading',
      'measure',
      'annotation',
    ]);
    expect(groups[1]?.tools.map(t => t.manifest.id)).toEqual([
      'rectangle',
      'zone-box',
      'polyline',
    ]);
    expect(savedDockSize('5000', 'side')).toBe(720);
    expect(savedDockSize('abc', 'bottom')).toBeNull();
  });
});
