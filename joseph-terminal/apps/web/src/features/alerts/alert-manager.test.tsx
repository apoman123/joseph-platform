/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the alert manager: creating a built-in alert job
 * through the dialog, pausing, editing and deleting it.
 */

import {screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {catalogCondition} from './alert-manager';
import {ALERTS_FEATURE} from './index';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered?.unmount();
  rendered = null;
});

describe('alert manager', () => {
  it('creates, pauses, edits and deletes a built-in alert', async () => {
    rendered = await renderFeature(
      ALERTS_FEATURE,
      'bottom-panel',
      'alerts.manager'
    );
    const user = userEvent.setup();
    const create = await screen.findByTestId('alert-create');
    await waitFor(() => void expect(create).toBeEnabled());
    expect(screen.getByText('No alerts yet')).toBeInTheDocument();
    await user.click(create);
    const dialog = screen.getByRole('dialog', {name: 'Create alert'});
    expect(within(dialog).getByTestId('alert-symbol')).toHaveValue(
      'mock:perp:BTCUSD'
    );
    await user.selectOptions(
      within(dialog).getByTestId('alert-condition'),
      'builtin:rsi-level@0'
    );
    const overbought = within(dialog).getByLabelText('Overbought');
    await user.clear(overbought);
    await user.type(overbought, '80');
    await user.click(within(dialog).getByTestId('alert-save'));
    const table = await screen.findByRole('table', {name: 'Alert jobs'});
    const row = within(table).getAllByRole('row')[1];
    if (!row) throw new Error('missing alert row');
    expect(row).toHaveTextContent('RSI overbought / oversold');
    expect(row).toHaveTextContent('Active');
    const [job] = await rendered.ctx.clients.scripts.alerts();
    expect(job?.inputs).toEqual({overbought: 80});
    expect(job?.version).toBe(0);
    await user.click(within(row).getByRole('button', {name: 'Pause'}));
    await waitFor(() => void expect(row).toHaveTextContent('Paused'));
    await user.click(within(row).getByRole('button', {name: 'Edit'}));
    const edit = screen.getByRole('dialog', {name: 'Edit alert'});
    expect(within(edit).getByLabelText('Overbought')).toHaveValue(80);
    await user.click(within(edit).getByRole('button', {name: 'Cancel'}));
    await user.click(within(row).getByRole('button', {name: 'Delete'}));
    expect(await screen.findByText('No alerts yet')).toBeInTheDocument();
  });

  it('describes catalog alerts as version 0 conditions', () => {
    const condition = catalogCondition({
      script_id: 'builtin:price-cross',
      kind: 'alert',
      origin: 'builtin',
      title: 'Price crossing',
      short_title: 'Cross',
      description: 'Crosses a level',
      category: 'Price',
      inputs: {},
      source_hash: 'x',
      enabled: true,
    });
    expect(condition).toEqual({
      scriptId: 'builtin:price-cross',
      version: 0,
      title: 'Price crossing',
      description: 'Crosses a level',
      inputs: {},
    });
  });
});
