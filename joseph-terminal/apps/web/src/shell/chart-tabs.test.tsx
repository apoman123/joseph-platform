/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Saved chart tabs and categorized indicator-library behavior. */

import {activeChart} from '@joseph/terminal-shell';
import {act, fireEvent, render, screen, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {createRuntime} from '../composition/runtime';
import {ChartTabs} from './chart-tabs';
import {IndicatorsButton, indicatorCollection} from './indicator-library';
import {RuntimeProvider} from './runtime-context';

/** Local synthetic market configuration, with isolated browser persistence. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

beforeEach(() => void localStorage.clear());

describe('saved chart tabs', () => {
  it('adds beyond eight charts, retains settings, and restores tabs after reload', () => {
    const runtime = createRuntime(() => [], DEMO);
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <ChartTabs />
      </RuntimeProvider>
    );
    for (let index = 0; index < 20; index += 1) {
      fireEvent.click(screen.getByRole('button', {name: 'Add chart tab'}));
      fireEvent.click(screen.getByRole('button', {name: 'Candlestick chart'}));
    }
    expect(screen.getAllByRole('tab')).toHaveLength(21);
    const chartId = activeChart(runtime.workspace.get()).chart_id;
    act(() => {
      runtime.dispatch({
        type: 'setInstrument',
        chartId,
        instrumentId: 'mock:perp:ETHUSD',
      });
      runtime.dispatch({type: 'setTimeframe', chartId, timeframe: '1h'});
    });
    fireEvent.click(screen.getAllByRole('tab')[0] as HTMLElement);
    expect(activeChart(runtime.workspace.get()).instrument_id).toBe(
      'mock:perp:BTCUSD'
    );
    fireEvent.click(screen.getByRole('tab', {name: 'ETHUSD · 1h'}));
    expect(activeChart(runtime.workspace.get()).chart_id).toBe(chartId);
    runtime.flush();
    const restored = createRuntime(() => [], DEMO);
    expect(restored.workspace.get().charts).toHaveLength(21);
    expect(activeChart(restored.workspace.get()).instrument_id).toBe(
      'mock:perp:ETHUSD'
    );
    fireEvent.click(screen.getByRole('button', {name: 'Close ETHUSD · 1h'}));
    expect(screen.getAllByRole('tab')).toHaveLength(20);
    view.unmount();
    runtime.flush();
  });

  it('supports arrow-key selection and protects the last chart', () => {
    const runtime = createRuntime(() => [], DEMO);
    render(
      <RuntimeProvider runtime={runtime}>
        <ChartTabs />
      </RuntimeProvider>
    );
    expect(screen.getByRole('button', {name: /Close BTCUSD/})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: 'Add chart tab'}));
    fireEvent.click(screen.getByRole('button', {name: 'Candlestick chart'}));
    const selected = screen.getByRole('tab', {selected: true});
    fireEvent.keyDown(selected, {key: 'ArrowLeft'});
    expect(activeChart(runtime.workspace.get()).chart_id).toBe('chart-1');
    fireEvent.keyDown(screen.getByRole('tab', {selected: true}), {
      key: 'Delete',
    });
    expect(screen.getAllByRole('tab')).toHaveLength(1);
    runtime.flush();
  });
});

describe('indicator library', () => {
  it('searches defaults, applies indicators, and persists a collection', async () => {
    const runtime = createRuntime(() => [], DEMO);
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorsButton chart={activeChart(runtime.workspace.get())} />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'Indicators'}));
    const dialog = screen.getByRole('dialog', {name: 'Indicators'});
    const query = within(dialog).getByRole('textbox', {
      name: 'Search indicators',
    });
    expect(query).toHaveFocus();
    const rsi = await within(dialog).findByRole('checkbox', {name: 'RSI'});
    fireEvent.change(query, {target: {value: 'RSI'}});
    expect(within(dialog).queryByRole('checkbox', {name: 'EMA'})).toBeNull();
    fireEvent.click(rsi);
    expect(
      activeChart(runtime.workspace.get()).indicator_layers.find(
        layer => layer.plugin_id === 'rsi'
      )?.visible
    ).toBe(true);
    fireEvent.click(within(dialog).getByRole('button', {name: 'Collect RSI'}));
    fireEvent.click(within(dialog).getByRole('tab', {name: /My collection/}));
    expect(within(dialog).getByRole('checkbox', {name: 'RSI'})).toBeChecked();
    runtime.flush();
    expect(
      indicatorCollection(createRuntime(() => [], DEMO).workspace.get())
    ).toEqual(['rsi']);
    fireEvent.click(within(dialog).getByRole('tab', {name: 'My scripts'}));
    expect(within(dialog).getByText('No custom scripts yet')).toBeVisible();
    fireEvent.keyDown(dialog, {key: 'Escape'});
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', {name: 'Indicators'})).toHaveFocus();
    view.unmount();
    runtime.flush();
  });

  it('lists installed custom indicator plugins under My scripts', async () => {
    const runtime = createRuntime(() => [], DEMO);
    const list = await runtime.clients.market.listIndicatorPlugins();
    const rsi = list.plugins.find(plugin => plugin.plugin_id === 'rsi');
    if (!rsi) throw new Error('Missing RSI fixture');
    vi.spyOn(runtime.clients.market, 'listIndicatorPlugins').mockResolvedValue({
      plugins: [
        ...list.plugins,
        {...rsi, plugin_id: 'my_momentum', display_name: 'My momentum'},
      ],
    });
    render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorsButton chart={activeChart(runtime.workspace.get())} />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'Indicators'}));
    fireEvent.click(screen.getByRole('tab', {name: 'My scripts'}));
    expect(
      await screen.findByRole('checkbox', {name: 'My momentum'})
    ).toBeVisible();
    expect(screen.queryByRole('checkbox', {name: 'RSI'})).toBeNull();
  });
});
