/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the watchlist and screener.
 */

import {screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {SCREENER_FEATURE} from '../screener';
import {
  createFakeContext,
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {WATCHLIST_FEATURE} from './index';
import {moveItem} from './watchlist-panel';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

describe('watchlist', () => {
  it('shows exact-venue rows with prices and selects on click', async () => {
    rendered = await renderFeature(WATCHLIST_FEATURE, 'side-panel');
    const row = await screen.findByTestId('watch-row-mock:perp:ETHUSD');
    await waitFor(
      () => void expect(within(row).getByText('3,400')).toBeInTheDocument()
    );
    expect(within(row).getByText('+1.25%')).toBeInTheDocument();
    await userEvent.setup().click(row);
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:ETHUSD');
  });

  it('removes rows and persists the list', async () => {
    rendered = await renderFeature(WATCHLIST_FEATURE, 'side-panel');
    await userEvent.setup().click(
      await screen.findByRole('button', {
        name: 'Remove mock:perp:SOLUSD from watchlist',
      })
    );
    expect(
      screen.queryByTestId('watch-row-mock:perp:SOLUSD')
    ).not.toBeInTheDocument();
    expect(rendered.ctx.settings.read({items: []}).items).toEqual([
      'mock:perp:BTCUSD',
      'mock:perp:ETHUSD',
    ]);
  });

  it('adds an instrument from search', async () => {
    const ctx = createFakeContext();
    ctx.settings.write({items: []});
    rendered = await renderFeature(
      WATCHLIST_FEATURE,
      'side-panel',
      undefined,
      ctx
    );
    expect(screen.getByText('Your watchlist is empty')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(
      screen.getByRole('textbox', {name: 'Add instrument'}),
      'SOL'
    );
    await user.click(await screen.findByRole('button', {name: /SOLUSD/}));
    expect(
      await screen.findByTestId('watch-row-mock:perp:SOLUSD')
    ).toBeInTheDocument();
  });

  it('navigates and reorders with the keyboard', async () => {
    rendered = await renderFeature(WATCHLIST_FEATURE, 'side-panel');
    const first = await screen.findByTestId('watch-row-mock:perp:BTCUSD');
    first.focus();
    const user = userEvent.setup();
    await user.keyboard('{ArrowDown}{Enter}');
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:ETHUSD');
    await user.keyboard('{Alt>}{ArrowUp}{/Alt}');
    expect(rendered.ctx.settings.read({items: []}).items[0]).toBe(
      'mock:perp:ETHUSD'
    );
  });

  it('moves items within bounds', () => {
    expect(moveItem(['a', 'b', 'c'], 0, -1)).toEqual(['a', 'b', 'c']);
    expect(moveItem(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b']);
  });
});

describe('screener', () => {
  it('scans the built-in Liquidity qualification filter by default', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    const user = userEvent.setup();
    const run = await screen.findByTestId('screener-run');
    await waitFor(() => void expect(run).toBeEnabled());
    expect(screen.getByLabelText('Filter 1')).toHaveValue(
      'builtin:liquidity-qualification'
    );
    await user.click(run);
    const row = await screen.findByTestId(
      'screener-row-mock:perp:BTCUSD',
      {},
      {timeout: 5000}
    );
    await waitFor(
      () =>
        void expect(screen.getByTestId('screener-progress')).toHaveTextContent(
          /complete/
        ),
      {timeout: 5000}
    );
    expect(
      screen.queryByTestId('screener-row-mock:rate:UST10Y')
    ).not.toBeInTheDocument();
    await user.click(row);
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:BTCUSD');
    expect(screen.getByText(/Missing data is reported as unavailable/)).toBe(
      screen.getByText(/Filters: Liquidity qualification\./)
    );
  });

  it('combines filters with AND and lists missing data separately', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    const user = userEvent.setup();
    const add = await screen.findByTestId('screener-add-filter');
    await waitFor(() => void expect(add).toBeEnabled());
    await user.selectOptions(
      screen.getByLabelText('Filter 1'),
      'builtin:rsi-range'
    );
    await user.click(add);
    await user.selectOptions(
      screen.getByLabelText('Filter 2'),
      'builtin:price'
    );
    const value = screen.getByLabelText('Value', {
      selector: '#screener-1-value',
    });
    await user.clear(value);
    await user.type(value, '1000000000');
    await user.click(screen.getByTestId('screener-run'));
    await waitFor(
      () =>
        void expect(screen.getByTestId('screener-progress')).toHaveTextContent(
          /complete .* 0 matches/
        ),
      {timeout: 5000}
    );
    expect(
      screen.getByText('No instrument matched every filter.')
    ).toBeVisible();
  });
});
