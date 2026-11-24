/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the strategy builder. The installed
 * indicator plugins come from the shared plugin descriptor fixtures, plus a
 * second publisher of the fixture events.
 */

import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import type {IndicatorPluginInfo} from '@joseph/contracts';
import {STRATEGY_TEMPLATES, exportDefinition} from '@joseph/strategy-builder';
import {fireEvent, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {STRATEGY_FEATURE} from './index';
import {issuesByNode} from './strategy-panel';

const plugins = vi.hoisted((): {infos: readonly IndicatorPluginInfo[]} => ({
  infos: [],
}));

vi.mock('../../shell/indicator-plugins', () => ({
  useIndicatorPlugins: () => plugins.infos,
}));

let rendered: RenderedFeature | null = null;

beforeAll(() => {
  const path = join(
    __dirname,
    '..',
    '..',
    '..',
    '..',
    '..',
    'packages',
    'contracts',
    'fixtures',
    'indicator-plugin.fixture.json'
  );
  // The fixture's valid examples are checked against `IndicatorPluginInfo`
  // by the contracts tests.
  const infos = (
    JSON.parse(readFileSync(path, 'utf8')) as {valid: IndicatorPluginInfo[]}
  ).valid;
  const zones = infos.find(p => p.plugin_id === 'example_zones');
  if (!zones) throw new Error('missing plugin fixture');
  plugins.infos = [
    ...infos,
    {
      ...zones,
      plugin_id: 'other_zones',
      display_name: 'Other zones',
      output_kinds: [],
      display: {...zones.display, kinds: []},
    },
  ];
});

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Renders the strategy panel with the EMA trend template.
 * @return Rendered feature.
 */
async function renderBuilder(): Promise<RenderedFeature> {
  rendered = await renderFeature(STRATEGY_FEATURE, 'side-panel');
  await screen.findByTestId('rule-editor');
  return rendered;
}

/**
 * Adds a condition to the root group from the add-condition menu.
 * @param title Menu item title.
 * @return Promise resolving once the item was chosen.
 */
async function addCondition(title: string): Promise<void> {
  const user = userEvent.setup();
  const root = screen.getByTestId('rule-editor');
  const buttons = within(root).getAllByRole('button', {name: 'Add condition'});
  await user.click(buttons[buttons.length - 1] as HTMLElement);
  await user.click(screen.getByRole('menuitem', {name: title}));
}

/**
 * Opens the inline editor of a condition card.
 * @param summary Card sentence.
 * @return The card's settings group.
 */
async function editCard(summary: string): Promise<HTMLElement> {
  await userEvent
    .setup()
    .click(screen.getByRole('button', {name: `Edit "${summary}"`}));
  const card = screen.getByText(summary).closest('[data-node-id]');
  if (!(card instanceof HTMLElement)) throw new Error('missing card');
  return within(card).getByRole('group', {name: 'Condition settings'});
}

describe('strategy builder', () => {
  it('renders the EMA trend template as plain-language condition cards', async () => {
    await renderBuilder();
    for (const text of [
      'Close crosses above EMA',
      'RSI on 1h is greater than 50',
      'Volume delta delta_pct is greater than 0',
      'Projected reward to risk after costs is at least 2.0R',
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByTestId('risk-target')).toHaveTextContent(
      'Fixed 3R target'
    );
    expect(screen.getByTestId('strategy-version')).toHaveTextContent(
      'Not saved'
    );
  });

  it('offers conditions the installed plugins can feed', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    const root = screen.getByTestId('rule-editor');
    const buttons = within(root).getAllByRole('button', {
      name: 'Add condition',
    });
    await user.click(buttons[buttons.length - 1] as HTMLElement);
    const menu = screen.getByRole('menu', {name: 'Condition types'});
    for (const title of [
      'Indicator state',
      'Price in a zone',
      'Touched an area of interest',
      'Indicator event',
      'Event sequence',
      'Time window',
    ]) {
      expect(
        within(menu).getByRole('menuitem', {name: title})
      ).toBeInTheDocument();
    }
  });

  it('adds, wraps and removes conditions', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await addCondition('Time window');
    expect(
      screen.getByText('Bar starts between 08:00 and 16:00 (UTC)')
    ).toBeInTheDocument();
    await addCondition('Indicator state');
    expect(
      screen.getByText('Volume delta: method is taker_volume on 15m')
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole('button', {
        name: 'Remove "RSI on 1h is greater than 50"',
      })
    );
    expect(
      screen.queryByText('RSI on 1h is greater than 50')
    ).not.toBeInTheDocument();
    const rrCard = screen.getByTestId('node-rr');
    await user.click(within(rrCard).getByRole('button', {name: 'Negate'}));
    expect(
      screen.getByRole('group', {name: 'This is not true'})
    ).toBeInTheDocument();
  });

  it('edits a condition inline and validates on the client', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await addCondition('Event sequence');
    const fields = await editCard('Zone then Break up within 4 bars');
    const bars = within(fields).getByRole('spinbutton', {name: 'Within bars'});
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

  it('edits state, zone and time-window conditions', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await addCondition('Indicator state');
    let fields = await editCard('Volume delta: method is taker_volume on 15m');
    await user.selectOptions(
      within(fields).getByRole('combobox', {name: 'Indicator'}),
      'example_zones'
    );
    expect(
      screen.getByText('Example zones: last_break is up on 15m')
    ).toBeInTheDocument();
    await user.click(within(fields).getByRole('checkbox', {name: 'down'}));
    await user.click(within(fields).getByRole('checkbox', {name: '1h'}));
    expect(
      screen.getByText('Example zones: last_break is up or down on 15m and 1h')
    ).toBeInTheDocument();

    await addCondition('Price in a zone');
    fields = await editCard('Close is inside a bullish P on 15m');
    await user.click(within(fields).getByRole('checkbox', {name: 'S1'}));
    await user.click(within(fields).getByRole('checkbox', {name: 'P'}));
    await user.selectOptions(
      within(fields).getByRole('combobox', {name: 'Side'}),
      ''
    );
    expect(screen.getByText('Close is inside a S1 on 15m')).toBeInTheDocument();

    await addCondition('Time window');
    fields = await editCard('Bar starts between 08:00 and 16:00 (UTC)');
    await user.selectOptions(
      within(fields).getByRole('combobox', {name: 'Time zone'}),
      'America/New_York'
    );
    fireEvent.change(within(fields).getByLabelText('Window 1 from'), {
      target: {value: '09:30'},
    });
    await user.click(within(fields).getByRole('checkbox', {name: 'Sat'}));
    await user.click(within(fields).getByRole('checkbox', {name: 'Sun'}));
    expect(
      screen.getByText(
        'Bar starts between 09:30 and 16:00 (America/New_York), Sat, Sun'
      )
    ).toBeInTheDocument();
    fireEvent.change(within(fields).getByLabelText('Window 1 until'), {
      target: {value: '09:30'},
    });
    expect(await screen.findByText(/it never matches/)).toBeInTheDocument();
  });

  it('chooses an event source when several plugins publish the event', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await addCondition('Indicator event');
    expect(
      screen.getByText(/Several indicators publish EXAMPLE_ZONE_FORMED/)
    ).toHaveClass('j-field__hint');
    const fields = await editCard('Zone confirmed in the last 3 bars');
    await user.selectOptions(
      within(fields).getByRole('combobox', {name: 'Source'}),
      'other_zones'
    );
    expect(
      screen.getByText('Zone confirmed in the last 3 bars (from Other zones)')
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Several indicators publish/)
    ).not.toBeInTheDocument();
  });

  it('targets the nearest zone or level of chosen kinds', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Target'}),
      'NEAREST_AOI'
    );
    expect(
      await screen.findByText(
        '/risk/target_kinds: Choose the AOI kinds the target may use.'
      )
    ).toBeInTheDocument();
    const kinds = screen.getByRole('group', {name: 'Target zones and levels'});
    await user.click(within(kinds).getByRole('checkbox', {name: 'R1'}));
    expect(screen.getByTestId('risk-target')).toHaveTextContent(
      'Nearest R1 beyond the entry'
    );
    expect(screen.queryByText(/Choose the AOI kinds/)).not.toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Target'}),
      'FIXED_R'
    );
    expect(screen.getByTestId('risk-target')).toHaveTextContent(
      'Fixed 3R target'
    );
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
      'EMA trend tuned'
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
      const {versions} = await ctx.strategy.getStrategy('ema-trend-long');
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
          path: '/when/children/1/left/field',
          code: 'server_rule',
          message: 'Server says the value is unknown',
        },
      ],
      warnings: [],
      required_plugins: [],
      required_timeframes: [],
      node_count: 5,
      max_depth: 2,
      definition_hash: null,
    });
    await userEvent
      .setup()
      .click(screen.getByRole('button', {name: 'Validate'}));
    const card = await screen.findByTestId('node-htf');
    expect(
      within(card).getByText('Server says the value is unknown')
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

  it('imports version 2 rules only and checks them against the plugins', async () => {
    await renderBuilder();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: 'JSON'}));
    const box = screen.getByRole('textbox', {name: 'Import a rule'});
    await user.click(box);
    await user.paste(JSON.stringify({version: 1, id: 'old-rule'}));
    await user.click(screen.getByRole('button', {name: 'Import rule'}));
    expect(
      screen.getByText('Expected a Rule AST v2 definition (schema_version 2).')
    ).toBeInTheDocument();
    const pivot = STRATEGY_TEMPLATES.find(t => t.id === 'pivot-bounce-long');
    if (!pivot) throw new Error('missing template');
    const definition = pivot.create();
    if (definition.when.op !== 'ALL') throw new Error('unexpected root');
    definition.when.children[0] = {
      op: 'AOI_TOUCH',
      kinds: ['PIVOT_S1', 'UNKNOWN_LEVEL'],
      side: 'bullish',
      within_bars: 3,
    };
    await user.clear(box);
    await user.click(box);
    await user.paste(exportDefinition(definition));
    await user.click(screen.getByRole('button', {name: 'Import rule'}));
    await user.click(screen.getByRole('tab', {name: 'Rules'}));
    expect(
      screen.getByText(
        'Touched a bullish S1 or UNKNOWN_LEVEL in the last 3 bars (wick)'
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'No installed indicator plugin publishes AOI kind UNKNOWN_LEVEL.'
      )
    ).toBeInTheDocument();
    expect(screen.getByTestId('risk-target')).toHaveTextContent(
      'Nearest P or R1 beyond the entry'
    );
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
