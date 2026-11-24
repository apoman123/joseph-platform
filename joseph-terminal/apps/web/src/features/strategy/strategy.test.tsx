/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the strategy builder.
 */

import {screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {STRATEGY_FEATURE} from './index';
import {issuesByNode} from './strategy-panel';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Renders the strategy panel with the Long A template.
 * @return Rendered feature.
 */
async function renderBuilder(): Promise<RenderedFeature> {
  rendered = await renderFeature(STRATEGY_FEATURE, 'side-panel');
  await screen.findByTestId('rule-editor');
  return rendered;
}

describe('strategy builder', () => {
  it('renders Long A as plain-language condition cards', async () => {
    await renderBuilder();
    expect(screen.getByText('4h bias is bullish')).toBeInTheDocument();
    expect(
      screen.getByText('Close is inside the 1h discount zone (range group A)')
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Touched a bullish FVG, OB or breaker in the last 3 bars (wick)'
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText('SSL sweep then seller failure within 4 bars')
    ).toBeInTheDocument();
    expect(screen.getByTestId('strategy-version')).toHaveTextContent(
      'Not saved'
    );
  });

  it('adds, wraps and removes conditions', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    const root = screen.getByTestId('rule-editor');
    const addButtons = within(root).getAllByRole('button', {
      name: 'Add condition',
    });
    await user.click(addButtons[addButtons.length - 1] as HTMLElement);
    await user.click(screen.getByRole('menuitem', {name: 'Trading session'}));
    expect(
      screen.getByText('Bar starts in the London or New York session')
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole('button', {name: 'Remove "4h bias is bullish"'})
    );
    expect(screen.queryByText('4h bias is bullish')).not.toBeInTheDocument();
    const rrCard = screen.getByTestId('node-rr');
    await user.click(within(rrCard).getByRole('button', {name: 'Negate'}));
    expect(
      screen.getByRole('group', {name: 'This is not true'})
    ).toBeInTheDocument();
  });

  it('edits a condition inline and validates on the client', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', {
        name: 'Edit "SSL sweep then seller failure within 4 bars"',
      })
    );
    const bars = screen.getByRole('spinbutton', {name: 'Within bars'});
    await user.clear(bars);
    await user.type(bars, '1');
    expect(
      await screen.findByText(/the window needs at least 2 bars/)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Save strategy'})).toBeEnabled();
    await user.click(screen.getByRole('button', {name: 'Save strategy'}));
    expect(
      await screen.findByText('Fix the marked problems before saving.')
    ).toBeInTheDocument();
  });

  it('saves immutable versions and shows AUTO_LIVE as locked', async () => {
    const {ctx} = await renderBuilder();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', {name: 'Save strategy'}));
    await waitFor(() =>
      expect(screen.getByTestId('strategy-version')).toHaveTextContent(
        'Version 1'
      )
    );
    await user.clear(screen.getByRole('textbox', {name: 'Name'}));
    await user.type(
      screen.getByRole('textbox', {name: 'Name'}),
      'Long A tuned'
    );
    expect(screen.getByTestId('strategy-version')).toHaveTextContent(
      'Version 1, edited'
    );
    await user.click(screen.getByRole('button', {name: 'Save new version'}));
    await waitFor(() =>
      expect(screen.getByTestId('strategy-version')).toHaveTextContent(
        'Version 2'
      )
    );
    expect(
      screen.getByRole('option', {name: 'Automatic live (locked)'})
    ).toBeDisabled();
    await user.click(screen.getByRole('tab', {name: 'Versions'}));
    expect(screen.getByTestId('auto-live-lock')).toHaveTextContent(
      /Automatic live trading is locked/
    );
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Activate in'}),
      'PAPER'
    );
    await user.click(screen.getByRole('button', {name: 'Activate version 2'}));
    await user.click(
      screen.getByRole('button', {name: 'Activate version 2 in paper mode'})
    );
    await waitFor(async () => {
      const {versions} = await ctx.strategy.getStrategy(
        'discount-ote-reversal'
      );
      expect(versions[0]?.state).toBe('ACTIVE');
      expect(versions[0]?.activation?.mode).toBe('PAPER');
    });
  });

  it('maps server validation errors to the condition', async () => {
    const {ctx} = await renderBuilder();
    ctx.strategy.validate = async () => ({
      valid: false,
      errors: [
        {
          path: '/when/children/4/within_bars',
          code: 'server_rule',
          message: 'Server says the window is too short',
        },
      ],
      warnings: [],
      required_plugins: [],
      required_timeframes: [],
      node_count: 7,
      max_depth: 2,
      definition_hash: null,
    });
    await userEvent
      .setup()
      .click(screen.getByRole('button', {name: 'Validate'}));
    const seq = await screen.findByTestId('node-seq');
    expect(
      within(seq).getByText('Server says the window is too short')
    ).toBeInTheDocument();
  });

  it('runs a backtest of the saved version and labels demo output', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: 'Backtest'}));
    expect(screen.getByText('Save the strategy first')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Save strategy'}));
    await waitFor(() =>
      expect(screen.getByTestId('strategy-version')).toHaveTextContent(
        'Version 1'
      )
    );
    await user.click(
      await screen.findByRole('button', {name: 'Run backtest of version 1'})
    );
    const report = await screen.findByTestId('backtest-report');
    expect(report).toHaveTextContent(/Demo report/);
    expect(
      within(report).getByRole('table', {name: 'Backtest trades'})
    ).toBeInTheDocument();
  });

  it('imports a starter v1 config with a migration report', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: 'JSON'}));
    const v1 = {
      version: 1,
      id: 'v1-rule',
      name: 'Old rule',
      side: 'short',
      timeframe: '15m',
      htfBias: 'bearish',
      requiredZones: ['premium'],
      aoiAny: ['liquidity'],
      triggerSequence: [],
      minRR: 3,
      cooldownBars: 4,
    };
    const box = screen.getByRole('textbox', {name: 'Import a rule'});
    await user.click(box);
    await user.paste(JSON.stringify(v1));
    await user.click(screen.getByRole('button', {name: 'Import rule'}));
    const report = await screen.findByTestId('migration-report');
    expect(report).toHaveTextContent(/Not migrated: aoiAny "liquidity"/);
    await user.click(screen.getByRole('tab', {name: 'Rules'}));
    expect(screen.getByText('4h bias is bearish')).toBeInTheDocument();
  });

  it('groups issues by node id', () => {
    const def = {
      schema_version: 2,
      strategy_id: 'x',
      name: 'x',
      direction: 'LONG',
      signal_timeframe: '5m',
      universe: {qualification: 'ANY', venues: ['mock']},
      when: {
        op: 'ALL',
        id: 'root',
        children: [{op: 'RISK_REWARD', id: 'rr', min_rr: '2'}],
      },
      risk: {min_rr: '2', stop_model: 'ATR', target_model: 'FIXED_R'},
      notification: {cooldown_bars: 0, once_per_aoi: true},
      execution_mode: 'ALERT_ONLY',
    } as const;
    const {byNode, general} = issuesByNode(def as never, [
      {path: '/when/children/0/min_rr', code: 'a', message: 'a'},
      {path: '/risk/fixed_r', code: 'b', message: 'b'},
    ]);
    expect(byNode.get('rr')).toHaveLength(1);
    expect(general).toHaveLength(1);
  });
});
