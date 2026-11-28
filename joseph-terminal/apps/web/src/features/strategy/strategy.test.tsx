/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the Python strategy panel against the
 * DEMO strategy client.
 */

import {screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {STRATEGY_FEATURE} from './index';
import {AUTO_LIVE_LOCK_REASON} from './strategy-panel';
import {defaultInputs} from './strategy-store';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Renders the panel and waits for the built-in strategies.
 * @return Rendered feature.
 */
async function renderPanel(): Promise<RenderedFeature> {
  rendered = await renderFeature(STRATEGY_FEATURE, 'side-panel');
  await screen.findByRole('option', {name: 'EMA Trend Long'});
  return rendered;
}

describe('strategy panel', () => {
  it('lists built-in strategies and edits their declared inputs', async () => {
    await renderPanel();
    const user = userEvent.setup();
    const picker = screen.getByRole('combobox', {name: 'Strategy script'});
    expect(within(picker).getByRole('group', {name: 'Built-in'})).toBeTruthy();
    const length = screen.getByRole('spinbutton', {name: 'EMA length'});
    expect(length).toHaveValue(20);
    await user.clear(length);
    await user.type(length, '34');
    expect(screen.getByRole('spinbutton', {name: 'EMA length'})).toHaveValue(
      34
    );
    await user.selectOptions(picker, 'builtin:rsi-reversion-short');
    expect(
      screen.getByRole('spinbutton', {name: 'RSI crosses below'})
    ).toHaveValue(70);
    await user.click(screen.getByRole('button', {name: 'Reset to defaults'}));
  });

  it('runs a labelled DEMO backtest with metrics and trades', async () => {
    await renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: 'Backtest'}));
    await user.click(screen.getByRole('button', {name: 'Run backtest'}));
    const report = await screen.findByTestId('backtest-report');
    expect(within(report).getByText(/Demo report/)).toBeInTheDocument();
    expect(within(report).getByText('Win rate')).toBeInTheDocument();
    const trades = within(report).getByRole('table', {name: 'Backtest trades'});
    expect(within(trades).getAllByRole('row').length).toBeGreaterThan(1);
  });

  it('activates, pauses and deletes with AUTO_LIVE locked', async () => {
    const {ctx} = await renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: 'Activate'}));
    const mode = screen.getByRole('combobox', {name: 'Activation mode'});
    expect(
      within(mode).getByRole('option', {name: 'Automatic live (locked)'})
    ).toBeDisabled();
    expect(screen.getByTestId('auto-live-lock')).toHaveTextContent(
      AUTO_LIVE_LOCK_REASON
    );
    await user.selectOptions(mode, 'PAPER');
    await user.click(screen.getByRole('button', {name: 'Activate'}));
    const list = await screen.findByRole('list', {name: 'Activations'});
    expect(within(list).getByText('active')).toBeInTheDocument();
    expect(
      within(list).getByText('builtin:ema-trend-long:vdemo')
    ).toBeInTheDocument();
    await user.click(within(list).getByRole('button', {name: 'Pause'}));
    await within(list).findByText('paused');
    await user.click(within(list).getByRole('button', {name: 'Delete'}));
    await screen.findByText('No activations');
    await expect(
      ctx.strategy.createActivation({
        script_id: 'builtin:ema-trend-long',
        instrument_ids: ['mock:perp:BTCUSD'],
        tf: '5m',
        mode: 'AUTO_LIVE',
      })
    ).rejects.toMatchObject({body: {code: 'TRADE_MODE_DISABLED'}});
  });

  it('shows recent signals and copies a built-in to owned scripts', async () => {
    await renderPanel();
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Strategy script'}),
      'builtin:pivot-bounce-long'
    );
    await user.click(screen.getByRole('tab', {name: 'Signals'}));
    const signals = await screen.findByRole('list', {name: 'Strategy signals'});
    expect(within(signals).getAllByRole('listitem')).toHaveLength(1);
    await user.click(screen.getByRole('tab', {name: 'Source'}));
    await user.click(screen.getByRole('button', {name: 'Open source'}));
    expect(await screen.findByLabelText('Strategy source')).toHaveTextContent(
      '# DEMO'
    );
    await user.click(screen.getByRole('button', {name: 'Copy to my scripts'}));
    await waitFor(() => {
      expect(
        within(
          screen.getByRole('combobox', {name: 'Strategy script'})
        ).getByRole('group', {name: 'My scripts'})
      ).toBeTruthy();
    });
  });

  it('reads input defaults from declarations', () => {
    expect(defaultInputs(undefined)).toEqual({});
  });
});
