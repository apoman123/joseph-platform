/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Script legends, heatmap tabs and browser control boundaries. */

import {
  createRecordingContext,
  type CoordinateTransform,
} from '@joseph/chart-core';
import type {ScriptResult} from '@joseph/contracts';
import {activeChart} from '@joseph/terminal-shell';
import {act, fireEvent, render, screen, within} from '@testing-library/react';
import {beforeEach, describe, expect, it} from 'vitest';
import {createRuntime} from '../composition/runtime';
import {ChartTabs} from './chart-tabs';
import {IndicatorLegend} from './python-studies';
import {pythonCanvas, pythonColor} from './python-painter';
import {applyTerminalAction, terminalSnapshot} from './mcp-control';
import {RuntimeProvider} from './runtime-context';

/** Explicit synthetic provider; never connects to real notifications or brokers. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

beforeEach(() => void localStorage.clear());

describe('indicator legend semantics', () => {
  it('hides without clearing settings, removes settings, and resets on re-add', async () => {
    const runtime = createRuntime(() => [], DEMO);
    const chartId = activeChart(runtime.workspace.get()).chart_id;
    runtime.dispatch({
      type: 'setIndicatorLayerConfig',
      chartId,
      pluginId: 'ema',
      patch: {settings: {length: 42}},
    });
    /**
     * Renders the currently saved chart after reducer updates.
     * @return Legend bound to the current saved chart.
     */
    function Legend() {
      return (
        <RuntimeProvider runtime={runtime}>
          <IndicatorLegend
            chart={activeChart(runtime.workspace.get())}
            errors={[]}
          />
        </RuntimeProvider>
      );
    }
    const view = render(<Legend />);
    const legend = screen.getByLabelText('Active indicators');
    await within(legend).findByRole('button', {name: 'Hide EMA'});
    fireEvent.click(within(legend).getByRole('button', {name: 'EMA settings'}));
    expect(runtime.indicatorSettings.get()).toEqual({chartId, pluginId: 'ema'});
    fireEvent.click(within(legend).getByRole('button', {name: 'Hide EMA'}));
    view.rerender(<Legend />);
    expect(
      activeChart(runtime.workspace.get()).indicator_layers.find(
        row => row.plugin_id === 'ema'
      )?.settings
    ).toEqual({length: 42});
    expect(
      within(legend).getByRole('button', {name: 'Show EMA'})
    ).toBeVisible();
    fireEvent.click(within(legend).getByRole('button', {name: 'Remove EMA'}));
    view.rerender(<Legend />);
    expect(within(legend).queryByText('EMA')).toBeNull();
    expect(
      activeChart(runtime.workspace.get()).indicator_layers.find(
        row => row.plugin_id === 'ema'
      )?.settings
    ).toBeUndefined();
    act(
      () =>
        void runtime.dispatch({
          type: 'setIndicatorLayer',
          chartId,
          pluginId: 'ema',
          visible: true,
        })
    );
    view.rerender(<Legend />);
    expect(
      within(legend).getByRole('button', {name: 'Hide EMA'})
    ).toBeVisible();
    runtime.flush();
  });

  it('persists the selected heatmap tab type and preserves candlestick tabs', async () => {
    const runtime = createRuntime(() => [], DEMO);
    render(
      <RuntimeProvider runtime={runtime}>
        <ChartTabs />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'Add chart tab'}));
    expect(screen.getAllByRole('tab')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', {name: 'Heatmap'}));
    expect(activeChart(runtime.workspace.get()).view_type).toBe('heatmap');
    runtime.flush();
    const restored = createRuntime(() => [], DEMO);
    expect(activeChart(restored.workspace.get()).view_type).toBe('heatmap');
    expect(restored.workspace.get().charts[0]?.view_type).toBeUndefined();
  });
});

/** Complete deterministic output used to inspect fill-bar arrow placement. */
const RESULT: ScriptResult = {
  script_id: 'test',
  version: 1,
  kind: 'strategy',
  source_hash: 'test-hash',
  instrument_id: 'mock:perp:BTCUSD',
  tf: '1m',
  bars_count: 2,
  feed_state: 'DEMO',
  data_source: 'mock',
  complete: true,
  overlay: true,
  plots: [],
  fills: [
    {
      time_ms: 60000,
      side: 'buy',
      quantity: 1,
      price: 100,
      fee: 0,
      effect: 'entry',
    },
    {
      time_ms: 120000,
      side: 'sell',
      quantity: 1,
      price: 105,
      fee: 0,
      effect: 'exit',
    },
  ],
  equity_curve: [],
  metrics: {},
};
/** Native millisecond/value-to-pixel transform, independent of chart vendor. */
const TRANSFORM: CoordinateTransform = {
  width: 1000,
  height: 600,
  timeToX: value => value / 1000,
  priceToY: value => 600 - value,
  xToTime: value => value * 1000,
  yToPrice: value => 600 - value,
};

describe('Python canvas output', () => {
  it('points entry and exit arrows at their exact actual fill candles', () => {
    const {ctx, calls} = createRecordingContext();
    pythonCanvas('test', RESULT).draw(ctx, TRANSFORM);
    expect(
      calls.some(
        row => row.op === 'lineTo' && row.args[0] === 60 && row.args[1] === 500
      )
    ).toBe(true);
    expect(
      calls.some(
        row => row.op === 'lineTo' && row.args[0] === 120 && row.args[1] === 495
      )
    ).toBe(true);
    expect(
      calls.filter(row => row.op === 'fillText').map(row => row.args[0])
    ).toEqual(['Entry', 'Exit']);
  });
  it('rejects executable colors and renders untrusted text as plain canvas text', () => {
    expect(pythonColor('url(javascript:alert(1))')).toBe('#5cb8e6');
    const {ctx, calls} = createRecordingContext();
    pythonCanvas('safe', {
      ...RESULT,
      fills: [],
      visuals: [
        {
          id: 'label',
          kind: 'label',
          data: {
            time_ms: 60000,
            price: 100,
            text: '<script>unsafe()</script>',
            color: 'url(https://untrusted.test)',
          },
        },
      ],
    }).draw(ctx, TRANSFORM);
    expect(
      calls.some(
        row =>
          row.op === 'fillText' && row.args[0] === '<script>unsafe()</script>'
      )
    ).toBe(true);
    expect(
      calls.some(
        row => row.op.startsWith('set:') && String(row.args[0]).includes('url(')
      )
    ).toBe(false);
  });
});

describe('opted-in terminal actions', () => {
  it('rejects invalid documents and supports validated workspace and theme changes', async () => {
    const runtime = createRuntime(() => [], DEMO);
    await expect(
      applyTerminalAction(runtime, {
        operation: 'workspace',
        arguments: {document: {charts: []}},
      })
    ).rejects.toThrow('schema');
    await expect(
      applyTerminalAction(runtime, {
        operation: 'eval',
        arguments: {source: 'untrusted'},
      })
    ).rejects.toThrow('Arbitrary JavaScript');
    await applyTerminalAction(runtime, {
      operation: 'theme',
      arguments: {theme: 'light'},
    });
    expect(runtime.theme.get()).toBe('light');
    const doc = structuredClone(runtime.workspace.get());
    const first = doc.charts[0];
    if (!first) throw new Error('Missing initial chart');
    first.timeframe = '1h';
    await applyTerminalAction(runtime, {
      operation: 'workspace',
      arguments: {document: doc},
    });
    expect(activeChart(runtime.workspace.get()).timeframe).toBe('1h');
    expect(JSON.stringify(terminalSnapshot(runtime))).not.toContain('devToken');
    runtime.flush();
  });
});
