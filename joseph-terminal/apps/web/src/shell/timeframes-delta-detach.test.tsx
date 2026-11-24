/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for seconds/monthly timeframes (grouped
 * menu, venue availability, typed `5S`/`1M`), the generic `delta_source`
 * input, true-delta rows in the data window and the detached-pane
 * placeholder and re-open banner.
 */

import type {ChartBar} from '@joseph/chart-core';
import type {VenueStatusList} from '@joseph/contracts';
import {activeChart, layerSettings} from '@joseph/terminal-shell';
import {DemoMarketDataClient} from '@joseph/mock-provider';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {composeFeatures} from '../composition/features';
import {
  createRuntime,
  type TerminalRuntime,
  type WindowActions,
} from '../composition/runtime';
import {ChartGrid} from './chart-grid';
import {DataWindow} from './data-window';
import {DetachedBanner} from './detached';
import {IndicatorSettingsDialog} from './indicator-settings-dialog';
import {fieldsFromSchema} from './indicator-settings';
import {RuntimeProvider} from './runtime-context';
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

/**
 * Returns a venue list whose DEMO venue publishes only some timeframes.
 * @return Venue status list.
 */
function restrictedVenues(): VenueStatusList {
  return {
    as_of_ms: 0,
    venues: [
      {
        capabilities: {
          venue: 'mock',
          display_name: 'Demo venue',
          market_types: ['perp'],
          market_data: {
            rest_history: true,
            native_ws_candles: true,
            native_ws_trades: false,
            native_ws_book: false,
            book_snapshot: true,
            open_interest: true,
            funding: true,
            trades_history: false,
          },
          timeframes: ['1m', '5m', '15m', '1h', '4h', '1d', '1w', '1M'],
          trade_built_timeframes: ['1s'],
          notes: [],
          docs_url: '',
        },
        health: {
          venue: 'mock',
          state: 'HEALTHY',
          feed_mode: 'AUTO',
          feed_state: 'DEMO',
          transport: 'MOCK',
          last_message_ms: 0,
          lag_ms: 0,
          reconnects: 0,
          gaps_detected: 0,
          last_error: null,
          as_of_ms: 0,
        },
      },
    ],
  };
}

describe('timeframe menu', () => {
  it('groups seconds to months and applies them', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'More'}));
    const menu = screen.getByRole('dialog', {name: 'All timeframes'});
    for (const group of ['Seconds', 'Minutes', 'Hours', 'Days']) {
      expect(within(menu).getByRole('region', {name: group})).toBeVisible();
    }
    expect(within(menu).getByTestId('timeframe-1M')).toHaveAttribute(
      'title',
      '1 month'
    );
    fireEvent.click(within(menu).getByTestId('timeframe-15s'));
    expect(activeChart(runtime.workspace.get()).timeframe).toBe('15s');
  });

  it('accepts typed 5S and 1M and pins them as favorites', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'More'}));
    const input = screen.getByLabelText('Type a resolution');
    fireEvent.change(input, {target: {value: '5S'}});
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(activeChart(runtime.workspace.get()).timeframe).toBe('5s');
    fireEvent.click(screen.getByRole('button', {name: '5s'}));
    const typed = screen.getByLabelText('Type a resolution');
    fireEvent.change(typed, {target: {value: '1M'}});
    fireEvent.submit(typed.closest('form') as HTMLFormElement);
    expect(activeChart(runtime.workspace.get()).timeframe).toBe('1M');
    fireEvent.click(screen.getByRole('button', {name: '1M'}));
    fireEvent.click(screen.getByRole('button', {name: 'Add 1M to favorites'}));
    expect(runtime.workspace.get().favorites?.timeframes).toContain('1M');
  });

  it('disables timeframes the venue does not publish', async () => {
    const runtime = await demoRuntime();
    vi.spyOn(runtime.clients.market, 'listVenues').mockResolvedValue(
      restrictedVenues()
    );
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByRole('button', {name: 'More'}));
    const menu = screen.getByRole('dialog', {name: 'All timeframes'});
    await waitFor(() =>
      expect(within(menu).getByTestId('timeframe-30s')).toBeDisabled()
    );
    expect(within(menu).getByTestId('timeframe-30s')).toHaveAttribute(
      'title',
      expect.stringMatching(/not offered by Demo venue/)
    );
    expect(within(menu).getByTestId('timeframe-1s')).toBeEnabled();
    expect(within(menu).getByTestId('timeframe-1s')).toHaveTextContent(
      'trades'
    );
    expect(within(menu).getByTestId('timeframe-1M')).toBeEnabled();
    const input = within(menu).getByLabelText('Type a resolution');
    fireEvent.change(input, {target: {value: '30S'}});
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(await within(menu).findByRole('alert')).toHaveTextContent(
      'not offered by this venue'
    );
  });
});

describe('delta source input', () => {
  it('comes from the plugin input schema', async () => {
    const {plugins} = await new DemoMarketDataClient().listIndicatorPlugins();
    const flow = plugins.find(p => p.plugin_id === 'order_flow_pressure');
    const field = fieldsFromSchema(flow?.input_schema).find(
      f => f.name === 'delta_source'
    );
    expect(field).toMatchObject({
      kind: 'enum',
      defaultValue: 'auto',
      choices: ['auto', 'estimate'],
    });
  });

  it('is offered generically by the settings dialog and saved', async () => {
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
          pluginId: 'order_flow_pressure',
        })
    );
    const dialog = await screen.findByTestId('indicator-settings');
    const source = await within(dialog).findByLabelText('Delta source');
    expect(
      within(source)
        .getAllByRole('option')
        .map(o => o.textContent)
    ).toEqual(['auto', 'estimate']);
    fireEvent.change(source, {target: {value: 'estimate'}});
    fireEvent.click(within(dialog).getByTestId('indicator-settings-save'));
    await waitFor(
      () => void expect(screen.queryByTestId('indicator-settings')).toBeNull()
    );
    expect(
      layerSettings(activeChart(runtime.workspace.get()), 'order_flow_pressure')
    ).toEqual({delta_source: 'estimate'});
  });
});

describe('data window', () => {
  const bar: ChartBar = {
    timeMs: Date.UTC(2026, 1, 1),
    open: 100,
    high: 110,
    low: 95,
    close: 105,
    volume: 12.5,
    final: true,
  };

  it('labels true delta with buy and sell volume and the month close', () => {
    render(
      <DataWindow
        bar={bar}
        timeframe="1M"
        previousClose={100}
        tickSize="0.1"
        delta={
          new Map([
            [
              bar.timeMs,
              {
                timeMs: bar.timeMs,
                value: 12.5,
                method: 'true' as const,
                buyVolume: '7.03',
                sellVolume: '5.47',
              },
            ],
          ])
        }
        compare={[]}
        label={id => id}
      />
    );
    const panel = screen.getByTestId('data-window');
    expect(panel).toHaveTextContent('Feb 2026');
    // February 2026 closes on March 1st, 28 days later.
    expect(panel).toHaveTextContent('Closes2026-03-01 00:00 UTC');
    expect(panel).toHaveTextContent('Delta sourceTrue (taker volume)');
    expect(panel).toHaveTextContent('Buy volume7.03');
    expect(panel).toHaveTextContent('Sell volume5.47');
  });

  it('labels estimates and shows seconds on sub-minute bars', () => {
    const second = {...bar, timeMs: Date.UTC(2026, 10, 22, 14, 3, 45)};
    render(
      <DataWindow
        bar={second}
        timeframe="5s"
        previousClose={null}
        tickSize="0.1"
        delta={
          new Map([
            [
              second.timeMs,
              {
                timeMs: second.timeMs,
                value: -4,
                method: 'estimate' as const,
                buyVolume: null,
                sellVolume: null,
              },
            ],
          ])
        }
        compare={[]}
        label={id => id}
      />
    );
    const panel = screen.getByTestId('data-window');
    expect(panel).toHaveTextContent('2026-11-22 14:03:45 UTC');
    expect(panel).toHaveTextContent('Closes14:03:50 UTC');
    expect(panel).toHaveTextContent('Delta sourceEstimate');
    expect(panel).not.toHaveTextContent('Buy volume');
  });
});

describe('detached panes in the main window', () => {
  /**
   * Returns fake window actions.
   * @return Actions whose methods are spies.
   */
  function fakeWindows(): WindowActions {
    return {
      supported: true,
      detach: vi.fn(),
      redock: vi.fn(),
      focus: vi.fn(),
      reopen: vi.fn(),
      reopenAll: vi.fn(),
    };
  }

  it('shows a placeholder with Focus window and Bring back while live', async () => {
    const runtime = createRuntime(() => [], DEMO);
    const windows = fakeWindows();
    runtime.windows.set(windows);
    const key = 'chart:chart-1';
    runtime.detached.set({
      panes: [
        {key, ref: {kind: 'chart', chartId: 'chart-1'}, title: 'BTCUSD 5m'},
      ],
      live: {[key]: {seenMs: Date.now()}},
    });
    render(
      <RuntimeProvider runtime={runtime}>
        <ChartGrid />
      </RuntimeProvider>
    );
    const placeholder = screen.getByTestId(`detached-${key}`);
    expect(placeholder).toHaveTextContent('Open in another window');
    expect(placeholder).toHaveTextContent('BTCUSD 5m');
    expect(screen.queryByTestId('chart-pane-chart-1')).toBeNull();
    fireEvent.click(within(placeholder).getByTestId('detached-focus'));
    expect(windows.focus).toHaveBeenCalledWith(key);
    fireEvent.click(within(placeholder).getByTestId('detached-bring-back'));
    expect(windows.redock).toHaveBeenCalledWith(key);
  });

  it('offers to re-open remembered windows after a reload', () => {
    const runtime = createRuntime(() => [], DEMO);
    const windows = fakeWindows();
    runtime.windows.set(windows);
    runtime.detached.set({
      panes: [
        {
          key: 'panel:alerts',
          ref: {kind: 'panel', slot: 'bottom-panel', panelId: 'alerts'},
          title: 'Alerts',
        },
      ],
      live: {},
    });
    render(
      <RuntimeProvider runtime={runtime}>
        <DetachedBanner />
      </RuntimeProvider>
    );
    const banner = screen.getByTestId('detached-banner');
    expect(banner).toHaveTextContent('Alerts was open in another window.');
    fireEvent.click(within(banner).getByTestId('reopen-detached'));
    expect(windows.reopenAll).toHaveBeenCalled();
  });
});
