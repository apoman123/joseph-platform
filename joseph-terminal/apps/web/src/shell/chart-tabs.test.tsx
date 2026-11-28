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
    const add = await within(dialog).findByTestId('add-study-builtin:rsi');
    fireEvent.change(query, {target: {value: 'Relative'}});
    expect(within(dialog).queryByTestId('add-study-builtin:ema')).toBeNull();
    fireEvent.click(add);
    expect(
      activeChart(runtime.workspace.get()).studies.find(
        study => study.script_id === 'builtin:rsi'
      )?.visible
    ).toBe(true);
    fireEvent.click(
      within(dialog).getByRole('button', {
        name: 'Collect Relative Strength Index',
      })
    );
    fireEvent.click(within(dialog).getByRole('tab', {name: /My collection/}));
    expect(within(dialog).getByTestId('add-study-builtin:rsi')).toBeVisible();
    runtime.flush();
    expect(
      indicatorCollection(createRuntime(() => [], DEMO).workspace.get())
    ).toEqual(['builtin:rsi']);
    fireEvent.click(within(dialog).getByRole('tab', {name: 'My scripts'}));
    expect(within(dialog).getByText('No scripts yet')).toBeVisible();
    fireEvent.keyDown(dialog, {key: 'Escape'});
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', {name: 'Indicators'})).toHaveFocus();
    view.unmount();
    runtime.flush();
  });

  it('lists installed plugin indicators under Plugins', async () => {
    const runtime = createRuntime(() => [], DEMO);
    const list = await runtime.clients.studies.catalog('indicator');
    const rsi = list.scripts.find(script => script.script_id === 'builtin:rsi');
    if (!rsi) throw new Error('Missing RSI fixture');
    vi.spyOn(runtime.clients.studies, 'catalog').mockResolvedValue({
      scripts: [
        ...list.scripts,
        {
          ...rsi,
          script_id: 'plugin:acme:momentum',
          origin: 'plugin',
          package: 'acme-indicators',
          title: 'My momentum',
          short_title: 'MOM',
        },
      ],
      failures: [],
    });
    render(
      <RuntimeProvider runtime={runtime}>
        <IndicatorsButton chart={activeChart(runtime.workspace.get())} />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'Indicators'}));
    fireEvent.click(screen.getByRole('tab', {name: 'Plugins'}));
    const add = await screen.findByTestId('add-study-plugin:acme:momentum');
    expect(add).toBeVisible();
    expect(screen.queryByTestId('add-study-builtin:rsi')).toBeNull();
    fireEvent.click(add);
    expect(activeChart(runtime.workspace.get()).studies.at(-1)?.script_id).toBe(
      'plugin:acme:momentum'
    );
  });
});
