/**
 * @fileoverview Tests for watchlist columns, sorting and the qualified
 * source, and for screener presets.
 */
import type {MarketSnapshot} from '@joseph/contracts';
import {fireEvent, screen, waitFor, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {SCREENER_FEATURE} from '../screener';
import {normalizePresets, savePreset} from '../screener/screener-panel';
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
 * Builds a snapshot.
 * @param extra Overrides.
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
  } as MarketSnapshot;
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
    expect(
      sortValue(snap({open_interest: {notional_usd: '12'} as never}), 'oi')
    ).toBe(12);
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
    await waitFor(() =>
      expect(screen.getByText(/currently marks Qualified/)).toBeInTheDocument()
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
  it('validates and replaces presets by name', () => {
    expect(
      normalizePresets([
        {name: 'A', venue: '', status: 'QUALIFIED', sort: 'rank'},
        {name: 'B', venue: 'nowhere', status: 'ALL', sort: 'rank'},
        3,
      ])
    ).toHaveLength(1);
    const list = savePreset(
      [{name: 'Hot', venue: '', status: 'ALL', sort: 'rank'}],
      {name: ' hot ', venue: '', status: 'QUALIFIED', sort: 'turnover'}
    );
    expect(list).toEqual([
      {name: 'hot', venue: '', status: 'QUALIFIED', sort: 'turnover'},
    ]);
  });

  it('saves the current filters and applies them later', async () => {
    rendered = await renderFeature(SCREENER_FEATURE, 'side-panel');
    await screen.findByTestId('screener-row-mock:perp:BTCUSD');
    fireEvent.click(screen.getByRole('button', {name: 'Qualified'}));
    fireEvent.change(screen.getByLabelText('Preset name'), {
      target: {value: 'Qualified only'},
    });
    fireEvent.click(screen.getByTestId('screener-preset-save'));
    expect(screen.getByTestId('screener-preset')).toHaveValue('Qualified only');
    fireEvent.click(screen.getByRole('button', {name: 'All'}));
    expect(screen.getByTestId('screener-preset')).toHaveValue('');
    fireEvent.change(screen.getByTestId('screener-preset'), {
      target: {value: 'Qualified only'},
    });
    expect(
      within(screen.getByRole('group', {name: 'Status'})).getByRole('button', {
        name: 'Qualified',
      })
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      rendered.ctx.settings.read<{presets: unknown[]}>({presets: []}).presets
    ).toHaveLength(1);
  });
});
