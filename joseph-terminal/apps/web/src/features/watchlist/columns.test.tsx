/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for watchlist columns, sorting and the qualified
 * source, and for screener presets.
 */

import type {MarketSnapshot, OpenInterest} from '@joseph/contracts';
import {DEMO_SCRIPT_CATALOG} from '@joseph/mock-provider';
import {fireEvent, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {SCREENER_FEATURE} from '../screener';
import {
  LIQUIDITY_FILTER,
  historyBarsFor,
  normalizePresets,
  savePreset,
  scanRequest,
} from '../screener/screener-panel';
import {
  DEFAULT_COLUMNS,
  nextSort,
  normalizeColumns,
  sortRows,
  sortValue,
} from './columns';
import {WATCHLIST_FEATURE, prefsFromSettings} from './index';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Builds a live, fresh snapshot of instrument `x`.
 * @param extra Field overrides.
 * @return Snapshot.
 */
function snap(extra: Partial<MarketSnapshot>): MarketSnapshot {
  return {
    instrument_id: 'x',
    feed_state: 'LIVE',
    source: 't',
    as_of_ms: 0,
    data_age_ms: 0,
    stale: false,
    ...extra,
  };
}

describe('watchlist columns and sorting', () => {
  it('normalizes stored columns and preferences', () => {
    expect(normalizeColumns(['oi', 'bogus', 'last'])).toEqual(['last', 'oi']);
    expect(normalizeColumns('x')).toEqual([...DEFAULT_COLUMNS]);
    expect(normalizeColumns([])).toEqual([...DEFAULT_COLUMNS]);
    expect(
      prefsFromSettings({
        items: [],
        sort: {key: 'last', dir: 'up' as never},
        source: 'other' as never,
      })
    ).toEqual({
      columns: [...DEFAULT_COLUMNS],
      sort: null,
      source: 'mine',
    });
  });

  it('cycles header clicks and keeps missing values last', () => {
    expect(nextSort(null, 'change')).toEqual({key: 'change', dir: 'desc'});
    expect(nextSort({key: 'change', dir: 'desc'}, 'change')).toEqual({
      key: 'change',
      dir: 'asc',
    });
    expect(nextSort({key: 'change', dir: 'asc'}, 'change')).toBeNull();
    expect(nextSort(null, 'symbol')).toEqual({key: 'symbol', dir: 'asc'});
    const snapshots = {
      a: snap({change_24h_pct: '1.5'}),
      b: snap({change_24h_pct: '-2'}),
      c: null,
    };
    expect(
      sortRows(['c', 'a', 'b'], {key: 'change', dir: 'desc'}, snapshots)
    ).toEqual(['a', 'b', 'c']);
    expect(
      sortRows(['c', 'a', 'b'], {key: 'change', dir: 'asc'}, snapshots)
    ).toEqual(['b', 'a', 'c']);
    expect(sortRows(['b', 'a'], {key: 'symbol', dir: 'asc'}, {})).toEqual([
      'a',
      'b',
    ]);
    expect(sortRows(['b', 'a'], null, {})).toEqual(['b', 'a']);
    const openInterest: OpenInterest = {
      instrument_id: 'x',
      base_quantity: '1',
      notional_usd: '12',
      source: 't',
      as_of_ms: 0,
    };
    expect(sortValue(snap({open_interest: openInterest}), 'oi')).toBe(12);
    expect(sortValue(snap({stale: true}), 'data')).toBe(1);
  });

  it('adds columns, sorts by header and shows the qualified list', async () => {
    rendered = await renderFeature(WATCHLIST_FEATURE, 'side-panel');
    await screen.findByTestId('watch-row-mock:perp:ETHUSD');
    fireEvent.click(screen.getByTestId('watch-columns'));
    fireEvent.click(screen.getByTestId('watch-column-mark'));
    expect(screen.getByTestId('watch-sort-mark')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('watch-sort-last'));
    expect(screen.getByRole('columnheader', {name: /Last/})).toHaveAttribute(
      'aria-sort',
      'descending'
    );
    expect(
      screen.getAllByRole('button', {name: /Move .* up/})[0]
    ).toBeDisabled();
    fireEvent.click(screen.getByTestId('watch-source-qualified'));
    await waitFor(
      () =>
        void expect(
          screen.getByText(/pass the built-in Liquidity/)
        ).toBeInTheDocument()
    );
    const stored = rendered.ctx.settings.read<{
      columns: string[];
      source: string;
    }>({columns: [], source: ''});
    expect(stored.columns).toContain('mark');
    expect(stored.source).toBe('qualified');
  });
});

describe('screener presets', () => {
  it('validates presets and migrates the earlier qualification presets', () => {
    const presets = normalizePresets([
      {name: 'A', venue: '', status: 'QUALIFIED', sort: 'rank'},
      {name: 'B', venue: 'nowhere', status: 'ALL', sort: 'rank'},
      {
        name: 'C',
        venue: 'mock',
        marketType: 'perp',
        tf: '1h',
        maxSymbols: 50,
        conditions: [{script_id: 'builtin:rsi-range', inputs: {value: 30}}, 5],
      },
      3,
    ]);
    expect(presets).toEqual([
      {
        name: 'A',
        venue: '',
        marketType: '',
        tf: '1d',
        maxSymbols: 200,
        conditions: [{script_id: LIQUIDITY_FILTER, inputs: {}}],
      },
      {
        name: 'C',
        venue: 'mock',
        marketType: 'perp',
        tf: '1h',
        maxSymbols: 50,
        conditions: [{script_id: 'builtin:rsi-range', inputs: {value: 30}}],
      },
    ]);
    const [first] = presets;
    if (!first) throw new Error('missing preset');
    expect(
      savePreset(presets, {...first, name: ' c '}).map(p => p.name)
    ).toEqual(['c', 'A']);
  });

  it('derives history bars and the scan request from the filters', () => {
    const catalog = new Map(
      DEMO_SCRIPT_CATALOG.map(entry => [entry.script_id, entry])
    );
    expect(
      historyBarsFor([{script_id: LIQUIDITY_FILTER, inputs: {}}], catalog)
    ).toBe(0);
    expect(
      historyBarsFor(
        [
          {script_id: 'builtin:rsi-range', inputs: {}},
          {script_id: 'builtin:gap', inputs: {}},
        ],
        catalog
      )
    ).toBe(15);
    expect(
      scanRequest(
        {
          venue: 'mock',
          marketType: '',
          tf: '1d',
          maxSymbols: 10,
          conditions: [
            {script_id: 'builtin:price-vs-ma', inputs: {length: 600}},
            {script_id: 'mine', version: 2, inputs: {}},
          ],
        },
        catalog
      )
    ).toEqual({
      venue: 'mock',
      max_symbols: 10,
      order_by: 'turnover',
      history_bars: 500,
      tf: '1d',
      conditions: [
        {script_id: 'builtin:price-vs-ma', inputs: {length: 600}},
        {script_id: 'mine', version: 2, inputs: {}},
      ],
    });
  });

  it('saves the current screen and applies it later', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    const add = await screen.findByTestId('screener-add-filter');
    await waitFor(() => void expect(add).toBeEnabled());
    fireEvent.click(add);
    fireEvent.change(screen.getByLabelText('Filter 2'), {
      target: {value: 'builtin:rsi-range'},
    });
    fireEvent.change(screen.getByLabelText('Preset name'), {
      target: {value: 'Liquid oversold'},
    });
    fireEvent.click(screen.getByTestId('screener-preset-save'));
    fireEvent.click(screen.getByRole('button', {name: 'Remove filter 2'}));
    expect(screen.queryByLabelText('Filter 2')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('screener-preset'), {
      target: {value: 'Liquid oversold'},
    });
    expect(screen.getByLabelText('Filter 2')).toHaveValue('builtin:rsi-range');
    const stored = rendered.ctx.settings.read<{
      presets: Array<{conditions: unknown[]}>;
    }>({presets: []}).presets;
    expect(stored).toHaveLength(1);
    expect(stored[0]?.conditions).toHaveLength(2);
  });
});
