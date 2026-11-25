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
  it('renders gates and never shows missing data as a pass', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    const row = await screen.findByTestId('screener-row-mock:perp:SOLUSD');
    expect(within(row).getByText('Missing data')).toBeInTheDocument();
    const missing = row.querySelectorAll('[data-gate-state="missing"]');
    expect(missing).toHaveLength(2);
    missing.forEach(cell => void expect(cell).toHaveTextContent('Missing'));
    expect(row.querySelector('[data-gate-state="fail"]')).toHaveTextContent(
      '0.120%'
    );
    expect(within(row).getByText('Watch')).toBeInTheDocument();
  });

  it('filters by status and selects on click', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    const user = userEvent.setup();
    await screen.findByTestId('screener-row-mock:perp:BTCUSD');
    await user.click(screen.getByRole('button', {name: 'Qualified'}));
    await waitFor(
      () =>
        void expect(
          screen.queryByTestId('screener-row-mock:perp:SOLUSD')
        ).not.toBeInTheDocument()
    );
    await user.click(screen.getByTestId('screener-row-mock:perp:BTCUSD'));
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:BTCUSD');
    expect(screen.getByText(/Open interest in USD:/)).toBeInTheDocument();
    expect(screen.getByText(/Policy demo-policy-1/)).toBeInTheDocument();
  });
});
