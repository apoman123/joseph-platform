/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap settings, metric fallbacks, value formatting and
 * the grouping of tiles.
 */

import {demoHeatmapSnapshot} from '@joseph/mock-provider';
import {describe, expect, it} from 'vitest';
import {fitTileText} from './render';
import {
  DEFAULT_SETTINGS,
  chooseGrouping,
  chooseMetric,
  formatMetric,
  peersOf,
  prepareView,
  readSettings,
} from './model';

const NOW = Date.UTC(2026, 10, 27, 15);

describe('settings', () => {
  it('reads defaults from nothing and keeps valid saved values', () => {
    expect(readSettings(undefined)).toEqual(DEFAULT_SETTINGS);
    expect(
      readSettings({
        type: 'crypto',
        dataset: 'crypto_defi',
        size: 'fd_market_cap',
        color: 'perf_1w',
        grouping: 'none',
        title: 'description',
        logo: true,
        value: 'price',
        scheme: 'colorblind',
        zoom: null,
      })
    ).toMatchObject({
      type: 'crypto',
      dataset: 'crypto_defi',
      grouping: 'none',
      logo: true,
      scheme: 'colorblind',
    });
    expect(readSettings({type: 'bonds', scheme: 'neon'})).toMatchObject({
      type: 'stocks',
      scheme: 'classic',
    });
  });

  it('migrates the legacy stock heatmap settings', () => {
    expect(readSettings({region: 'taiwan', size: 'equal'})).toMatchObject({
      type: 'stocks',
      dataset: 'tw_twse',
      size: 'mono',
    });
    expect(readSettings({region: 'us', size: 'market_cap'})).toMatchObject({
      dataset: 'us_all',
      size: 'market_cap',
    });
  });
});

describe('metrics and grouping', () => {
  const etfs = demoHeatmapSnapshot({type: 'etfs'}, NOW);

  it('falls back from an unavailable metric to the default', () => {
    expect(chooseMetric(etfs.size_metrics, 'aum', etfs.default_size)?.id).toBe(
      'turnover_1d'
    );
    expect(
      chooseMetric(etfs.size_metrics, 'volume_1w', etfs.default_size)?.id
    ).toBe('volume_1w');
    expect(chooseGrouping(etfs, null)).toBe('asset_class');
    expect(chooseGrouping(etfs, 'sector')).toBe('asset_class');
  });

  it('groups by sector and industry, largest first, and zooms', () => {
    const stocks = demoHeatmapSnapshot({type: 'stocks'}, NOW);
    const view = prepareView(stocks, {
      size: 'market_cap',
      defaultSize: 'market_cap',
      color: 'change_1d',
      grouping: 'sector',
      zoom: null,
    });
    expect(view.tiles).toHaveLength(stocks.tiles.length);
    expect(view.allGroups[0]).toBe('Technology');
    const weights = view.tiles.map(row => row.weight);
    expect(weights).toEqual([...weights].sort((a, b) => b - a));
    const zoomed = prepareView(stocks, {
      size: 'market_cap',
      defaultSize: 'market_cap',
      color: 'change_1d',
      grouping: 'sector',
      zoom: 'Energy',
    });
    expect(zoomed.groups).toHaveLength(1);
    expect(zoomed.tiles.every(row => row.group === 'Energy')).toBe(true);
    const target = zoomed.tiles.find(row => row.industry === 'Oil & Gas');
    expect(target).toBeDefined();
    if (target) {
      expect(
        peersOf(zoomed.tiles, target).every(row => row.industry === 'Oil & Gas')
      ).toBe(true);
    }
  });

  it('gives Mono size equal areas in the default size order', () => {
    const stocks = demoHeatmapSnapshot({type: 'stocks'}, NOW);
    const view = prepareView(stocks, {
      size: 'mono',
      defaultSize: 'market_cap',
      color: 'change_1d',
      grouping: 'none',
      zoom: null,
    });
    expect(view.tiles[0]?.weight).toBeCloseTo(1, 6);
    expect(view.tiles[0]?.tile.symbol).toBe(stocks.tiles[0]?.symbol);
  });

  it('leaves out tiles without a size value and counts them', () => {
    const stocks = demoHeatmapSnapshot({type: 'stocks'}, NOW);
    const [first, ...rest] = stocks.tiles;
    const broken = {
      ...stocks,
      tiles: [{...first, key: 'x', symbol: 'X', metrics: {}}, ...rest],
    };
    const view = prepareView(broken, {
      size: 'market_cap',
      defaultSize: 'market_cap',
      color: 'change_1d',
      grouping: 'sector',
      zoom: null,
    });
    expect(view.withoutSize).toBe(1);
    expect(view.tiles.some(row => row.tile.key === 'x')).toBe(false);
  });
});

describe('formatting', () => {
  it('formats percentages, amounts and ratios', () => {
    expect(formatMetric({format: 'percent'}, 1.234, 'USD')).toBe('+1.23%');
    expect(formatMetric({format: 'percent'}, -0.5, 'USD')).toBe('-0.50%');
    expect(formatMetric({format: 'currency'}, 2.5e12, 'USD')).toBe('2.50T USD');
    expect(formatMetric({format: 'number'}, 45_600_000, 'USD')).toBe('45.6M');
    expect(formatMetric({format: 'ratio'}, 1.5, 'USD')).toBe('1.50×');
    expect(formatMetric({format: 'percent'}, null, 'USD')).toBe('—');
  });

  it('sizes tile text from the tile and hides it on tiny tiles', () => {
    const big = fitTileText(
      {x: 0, y: 0, w: 200, h: 120},
      'ORBQ',
      '+1.2%',
      true
    );
    expect(big.titleSize).toBeGreaterThan(20);
    expect(big.value).toBe('+1.2%');
    expect(big.badge).toBeGreaterThan(0);
    const small = fitTileText(
      {x: 0, y: 0, w: 40, h: 22},
      'ORBQ',
      '+1.2%',
      true
    );
    expect(small.title).toBe('ORBQ');
    expect(small.value).toBeNull();
    expect(small.badge).toBe(0);
    expect(
      fitTileText({x: 0, y: 0, w: 10, h: 8}, 'ORBQ', '+1.2%', false).title
    ).toBeNull();
    const long = fitTileText(
      {x: 0, y: 0, w: 60, h: 40},
      'Orbitron Semiconductor',
      null,
      false
    );
    expect(long.title?.endsWith('…')).toBe(true);
  });
});
