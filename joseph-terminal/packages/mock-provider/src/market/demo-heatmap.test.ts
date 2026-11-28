/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The DEMO heatmap generator: data sets mirror the engine's
 * catalogue, values are deterministic and DEMO-labelled, and unavailable
 * metrics stay unavailable.
 */

import {describe, expect, it} from 'vitest';
import {demoHeatmapCatalog, demoHeatmapSnapshot} from './demo-heatmap';

const NOW = Date.UTC(2026, 10, 27, 15);

describe('demoHeatmapSnapshot', () => {
  it('lists every family with a default data set', () => {
    const catalog = demoHeatmapCatalog();
    expect(catalog.defaults).toEqual({
      stocks: 'us_sp500',
      etfs: 'us_etfs',
      crypto: 'crypto_all',
    });
    expect(catalog.datasets.map(row => row.id)).toContain('tw_tpex');
  });

  it('builds a deterministic, DEMO-labelled S&P 500 stand-in', () => {
    const one = demoHeatmapSnapshot({type: 'stocks'}, NOW);
    const two = demoHeatmapSnapshot({type: 'stocks'}, NOW + 60_000);
    expect(one.tiles).toEqual(two.tiles.map(t => ({...t, as_of_ms: NOW})));
    expect(one.demo).toBe(true);
    expect(one.provenance[0]?.source).toBe('DEMO synthetic data');
    expect(one.tiles.length).toBeGreaterThanOrEqual(60);
    expect(new Set(one.tiles.map(t => t.group)).size).toBeGreaterThan(8);
    const caps = one.tiles.map(t => Number(t.metrics?.market_cap));
    expect(caps).toEqual([...caps].sort((a, b) => b - a));
    expect(one.tiles.find(t => t.symbol === 'ACME')?.instrument_id).toBe(
      'mock:stock:ACME'
    );
  });

  it('keeps ETF AUM and some crypto changes unavailable', () => {
    const etfs = demoHeatmapSnapshot({type: 'etfs', dataset: 'tw_etfs'}, NOW);
    expect(etfs.dataset).toBe('tw_etfs');
    expect(etfs.size_metrics.find(m => m.id === 'aum')?.available).toBe(false);
    expect(etfs.default_size).toBe('turnover_1d');
    const coins = demoHeatmapSnapshot({type: 'crypto'}, NOW);
    expect(coins.color_metrics.find(m => m.id === 'change_4h')).toMatchObject({
      available: false,
    });
    expect(coins.tiles.every(t => t.metrics?.change_4h === undefined)).toBe(
      true
    );
    expect(coins.tiles).toHaveLength(40);
  });

  it('filters the crypto data sets', () => {
    const symbols = (dataset: string) =>
      demoHeatmapSnapshot({type: 'crypto', dataset}, NOW).tiles.map(
        t => t.symbol
      );
    expect(symbols('crypto_ex_btc')).not.toContain('BTC');
    expect(symbols('crypto_ex_stable')).not.toContain('DUSD');
    expect(symbols('crypto_defi')).toContain('LQDX');
    expect(symbols('crypto_defi')).not.toContain('ZNTH');
  });
});
