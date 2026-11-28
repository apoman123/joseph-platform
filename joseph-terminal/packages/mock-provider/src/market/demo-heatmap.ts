/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Synthetic DEMO heatmaps standing in for `GET /v1/heatmap`
 * and `GET /v1/heatmap/catalog`.
 *
 * The data sets, metric ids and labels mirror the engine's catalogue. The
 * values are deterministic per UTC day (so tests and screenshots are
 * stable) and every response is marked `demo: true` with a "DEMO synthetic
 * data" provenance row. AUM stays unavailable, as from the engine's free
 * sources; crypto lacks the 4h/3M/6M/YTD changes, as from its keyless
 * source, so the disabled options look exactly like a live engine's.
 */

import type {
  HeatmapCatalog,
  HeatmapDataset,
  HeatmapGrouping,
  HeatmapMetric,
  HeatmapSnapshot,
  HeatmapTile,
} from '@joseph/contracts';
import {
  DEMO_COINS,
  DEMO_NASDAQ_100,
  DEMO_TW_COMPANIES,
  DEMO_TW_FUNDS,
  DEMO_US_COMPANIES,
  DEMO_US_FUNDS,
  type DemoCoin,
  type DemoCompany,
  type DemoFund,
} from './demo-heatmap-data';

/** Heatmap family. */
export type DemoHeatmapType = 'stocks' | 'etfs' | 'crypto';

/** A size/colour metric definition: id, label, format and colour scale. */
type MetricDef = [
  string,
  string,
  HeatmapMetric['format'],
  ('diverging' | 'sequential')?,
];

const VOLUME_SIZES: MetricDef[] = [
  ['volume_1d', 'Volume 1D', 'number'],
  ['volume_1w', 'Volume 1W', 'number'],
  ['volume_1m', 'Volume 1M', 'number'],
  ['turnover_1d', 'Price × Volume (Turnover) 1D', 'currency'],
  ['turnover_1w', 'Price × Volume (Turnover) 1W', 'currency'],
  ['turnover_1m', 'Price × Volume (Turnover) 1M', 'currency'],
];
const MONO: MetricDef = ['mono', 'Mono size', 'number'];
const SIZES: Record<DemoHeatmapType, MetricDef[]> = {
  stocks: [['market_cap', 'Market cap', 'currency'], ...VOLUME_SIZES, MONO],
  etfs: [['aum', 'Assets under management', 'currency'], ...VOLUME_SIZES, MONO],
  crypto: [
    ['market_cap', 'Market cap', 'currency'],
    ['fd_market_cap', 'Fully diluted market cap', 'currency'],
    ['volume_usd_24h', 'Volume in USD 24h', 'currency'],
    MONO,
  ],
};
const PERFORMANCE: MetricDef[] = [
  ['perf_1w', 'Performance 1W, %', 'percent', 'diverging'],
  ['perf_1m', 'Performance 1M, %', 'percent', 'diverging'],
  ['perf_3m', 'Performance 3M, %', 'percent', 'diverging'],
  ['perf_6m', 'Performance 6M, %', 'percent', 'diverging'],
  ['perf_ytd', 'Performance YTD, %', 'percent', 'diverging'],
  ['perf_1y', 'Performance 1Y, %', 'percent', 'diverging'],
];
const STOCK_COLORS: MetricDef[] = [
  ['change_1d', 'Change 1D, %', 'percent', 'diverging'],
  ['change_1h', 'Change 1h, %', 'percent', 'diverging'],
  ['change_4h', 'Change 4h, %', 'percent', 'diverging'],
  ...PERFORMANCE,
  ['premarket_change', 'Pre-market change, %', 'percent', 'diverging'],
  ['postmarket_change', 'Post-market change, %', 'percent', 'diverging'],
  ['relative_volume', 'Relative volume', 'ratio', 'sequential'],
  ['volatility_1d', 'Volatility 1D, %', 'percent', 'sequential'],
  ['gap', 'Gap, %', 'percent', 'diverging'],
];
const CRYPTO_COLORS: MetricDef[] = [
  ['change_1d', 'Change 24h, %', 'percent', 'diverging'],
  ['change_1h', 'Change 1h, %', 'percent', 'diverging'],
  ['change_4h', 'Change 4h, %', 'percent', 'diverging'],
  ...PERFORMANCE,
  ['volatility_1d', 'Volatility 24h, %', 'percent', 'sequential'],
];
const UNAVAILABLE: Record<DemoHeatmapType, Record<string, string>> = {
  stocks: {},
  etfs: {
    aum: 'Fund assets under management are not available from free sources',
  },
  crypto: {
    change_4h: 'The keyless coin markets source has no 4-hour change',
    perf_3m: 'The keyless coin markets source has no 3-month change',
    perf_6m: 'The keyless coin markets source has no 6-month change',
    perf_ytd: 'The keyless coin markets source has no year-to-date change',
  },
};

/** DEMO data sets in menu order: [id, type, label, section, currency, limit]. */
const DATASETS: ReadonlyArray<
  [string, DemoHeatmapType, string, string, string, number]
> = [
  ['us_sp500', 'stocks', 'S&P 500', 'United States', 'USD', 510],
  ['us_nasdaq100', 'stocks', 'Nasdaq 100', 'United States', 'USD', 110],
  [
    'us_nasdaq_composite',
    'stocks',
    'Nasdaq Composite',
    'United States',
    'USD',
    1000,
  ],
  ['us_nyse', 'stocks', 'NYSE', 'United States', 'USD', 1000],
  ['us_all', 'stocks', 'All US stocks', 'United States', 'USD', 1000],
  ['tw_twse', 'stocks', 'TWSE listed', 'Taiwan', 'TWD', 1000],
  ['tw_tpex', 'stocks', 'TPEx listed', 'Taiwan', 'TWD', 900],
  ['us_etfs', 'etfs', 'US ETFs', 'United States', 'USD', 500],
  ['tw_etfs', 'etfs', 'Taiwan ETFs', 'Taiwan', 'TWD', 400],
  ['crypto_all', 'crypto', 'All coins', 'Crypto', 'USD', 250],
  ['crypto_ex_btc', 'crypto', 'Excluding Bitcoin', 'Crypto', 'USD', 250],
  ['crypto_ex_stable', 'crypto', 'Excluding stablecoins', 'Crypto', 'USD', 250],
  ['crypto_defi', 'crypto', 'DeFi', 'Crypto', 'USD', 250],
];

const DEFAULTS: Record<DemoHeatmapType, string> = {
  stocks: 'us_sp500',
  etfs: 'us_etfs',
  crypto: 'crypto_all',
};
const DEFAULT_SIZE: Record<DemoHeatmapType, string> = {
  stocks: 'market_cap',
  etfs: 'turnover_1d',
  crypto: 'market_cap',
};
const DEFAULT_GROUPING: Record<
  DemoHeatmapType,
  HeatmapSnapshot['default_grouping']
> = {stocks: 'sector', etfs: 'asset_class', crypto: 'none'};
const NO_CHART = 'DEMO: this fictional symbol has no chart';
const DAY_MS = 86_400_000;

/**
 * Returns the DEMO heatmap catalogue.
 * @return Every data set (all available) and the default per family.
 */
export function demoHeatmapCatalog(): HeatmapCatalog {
  return {
    datasets: DATASETS.map(datasetRow),
    defaults: {...DEFAULTS},
  };
}

/**
 * Builds a catalogue row.
 * @param row Data set definition.
 * @return Contract row.
 */
function datasetRow(
  row: (typeof DATASETS)[number]
): HeatmapDataset & {type: DemoHeatmapType} {
  const [id, type, label, section, currency, limit] = row;
  return {
    id,
    type,
    label,
    section,
    currency,
    default_limit: limit,
    available: true,
    reason: null,
  };
}

/**
 * Seeded pseudo-random generator (mulberry32).
 * @param seed 32-bit seed.
 * @return Generator of uniform numbers in [0, 1).
 */
function mulberry(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Hashes a string to 32 bits (FNV-1a).
 * @param text Text.
 * @return Unsigned hash.
 */
function hash(text: string): number {
  let value = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 0x01000193);
  }
  return value >>> 0;
}

/** Deterministic random helpers for one symbol on one day. */
interface Noise {
  /** Uniform number in [0, 1). */
  uniform: () => number;
  /** Approximately standard-normal number. */
  normal: () => number;
}

/**
 * Creates the noise source of a symbol on a day.
 * @param key Symbol key.
 * @param day UTC day number.
 * @return Noise helpers.
 */
function noise(key: string, day: number): Noise {
  const uniform = mulberry(hash(`${key}:${day}`));
  return {
    uniform,
    normal: () => {
      const u = Math.max(uniform(), 1e-9);
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * uniform());
    },
  };
}

/**
 * Formats a number as a decimal string with fixed decimals.
 * @param value Number.
 * @param digits Fraction digits.
 * @return Decimal string.
 */
function dec(value: number, digits = 2): string {
  return value.toFixed(digits);
}

/**
 * Formats a price with precision suited to its magnitude.
 * @param value Price.
 * @return Decimal string.
 */
function priceText(value: number): string {
  if (value >= 100) return dec(value, 2);
  if (value >= 1) return dec(value, 3);
  return value.toPrecision(4);
}

/**
 * Clamps a number.
 * @param value Number.
 * @param low Lower bound.
 * @param high Upper bound.
 * @return Clamped number.
 */
function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * Builds the common price-change metrics.
 * @param n Noise.
 * @param drift Sector or market drift of the day, in percent.
 * @param spread Typical daily move, in percent.
 * @return Metrics and the daily change.
 */
function changes(
  n: Noise,
  drift: number,
  spread: number
): {metrics: Record<string, string>; change: number} {
  const change = clamp(drift + n.normal() * spread, -4 * spread, 4 * spread);
  return {
    change,
    metrics: {
      change_1d: dec(change),
      change_1h: dec(change * 0.2 + n.normal() * spread * 0.25),
      change_4h: dec(change * 0.55 + n.normal() * spread * 0.35),
      perf_1w: dec(change * 1.3 + n.normal() * spread * 1.8),
      perf_1m: dec(1 + n.normal() * spread * 3.5),
      perf_3m: dec(2 + n.normal() * spread * 6),
      perf_6m: dec(4 + n.normal() * spread * 9),
      perf_ytd: dec(6 + n.normal() * spread * 11),
      perf_1y: dec(9 + n.normal() * spread * 14),
      volatility_1d: dec(Math.abs(change) * 0.7 + spread * (0.5 + n.uniform())),
    },
  };
}

/**
 * Adds volume and turnover sizes.
 * @param metrics Metrics to extend.
 * @param n Noise.
 * @param price Last price.
 * @param turnover1d Daily turnover in currency.
 */
function volumes(
  metrics: Record<string, string>,
  n: Noise,
  price: number,
  turnover1d: number
): void {
  const week = turnover1d * (4.3 + n.uniform() * 1.2);
  const month = turnover1d * (19 + n.uniform() * 4);
  metrics.volume_1d = dec(turnover1d / price, 0);
  metrics.volume_1w = dec(week / price, 0);
  metrics.volume_1m = dec(month / price, 0);
  metrics.turnover_1d = dec(turnover1d, 0);
  metrics.turnover_1w = dec(week, 0);
  metrics.turnover_1m = dec(month, 0);
}

/**
 * Builds a company tile.
 * @param row Company.
 * @param day UTC day number.
 * @param drift Drift of the sector, in percent.
 * @param asOf Observation time.
 * @return Tile.
 */
function companyTile(
  row: DemoCompany,
  day: number,
  drift: number,
  asOf: number
): HeatmapTile {
  const n = noise(row.symbol, day);
  const {metrics, change} = changes(n, drift, 1.5);
  const price = row.price * (1 + change / 100);
  const cap = row.capB * 1e9 * (1 + change / 100);
  metrics.market_cap = dec(cap, 0);
  volumes(metrics, n, price, cap * (0.002 + n.uniform() * 0.012));
  metrics.premarket_change = dec(change * 0.3 + n.normal() * 0.6);
  metrics.postmarket_change = dec(n.normal() * 0.5);
  metrics.relative_volume = dec(0.35 + n.uniform() ** 2 * 3.2);
  metrics.gap = dec(change * 0.3 + n.normal() * 0.5);
  return {
    key: row.symbol,
    symbol: row.symbol,
    name: row.name,
    group: row.sector,
    industry: row.industry || null,
    exchange: row.exchange,
    instrument_id: row.instrumentId ?? null,
    chart_unavailable_reason: row.instrumentId ? null : NO_CHART,
    price: priceText(price),
    metrics,
    as_of_ms: asOf,
    error: null,
  };
}

/**
 * Builds a fund tile.
 * @param row Fund.
 * @param day UTC day number.
 * @param drift Market drift, in percent.
 * @param asOf Observation time.
 * @return Tile.
 */
function fundTile(
  row: DemoFund,
  day: number,
  drift: number,
  asOf: number
): HeatmapTile {
  const n = noise(row.symbol, day);
  const spread =
    row.assetClass === 'Fixed income' || row.assetClass === 'Currency'
      ? 0.35
      : row.assetClass === 'Crypto'
        ? 3
        : 1.1;
  const {metrics, change} = changes(
    n,
    row.assetClass === 'Equity' ? drift : 0,
    spread
  );
  const price = row.price * (1 + change / 100);
  volumes(metrics, n, price, row.turnoverM * 1e6 * (0.6 + n.uniform() * 0.9));
  metrics.premarket_change = dec(change * 0.3 + n.normal() * spread * 0.4);
  metrics.postmarket_change = dec(n.normal() * spread * 0.3);
  metrics.relative_volume = dec(0.4 + n.uniform() ** 2 * 2.6);
  metrics.gap = dec(change * 0.3 + n.normal() * spread * 0.3);
  return {
    key: row.symbol,
    symbol: row.symbol,
    name: row.name,
    group: row.assetClass,
    industry: null,
    exchange: row.exchange,
    instrument_id: row.instrumentId ?? null,
    chart_unavailable_reason: row.instrumentId ? null : NO_CHART,
    price: priceText(price),
    metrics,
    as_of_ms: asOf,
    error: null,
  };
}

/**
 * Builds a coin tile.
 * @param row Coin.
 * @param day UTC day number.
 * @param drift Market drift, in percent.
 * @param asOf Observation time.
 * @return Tile.
 */
function coinTile(
  row: DemoCoin,
  day: number,
  drift: number,
  asOf: number
): HeatmapTile {
  const n = noise(row.symbol, day);
  const stable = row.tags?.includes('stablecoin') ?? false;
  const {metrics, change} = stable
    ? changes(n, 0, 0.03)
    : changes(n, drift, 3.2);
  for (const id of ['change_4h', 'perf_3m', 'perf_6m', 'perf_ytd']) {
    delete metrics[id];
  }
  const price = row.price * (1 + change / 100);
  const cap = row.capB * 1e9 * (1 + change / 100);
  metrics.market_cap = dec(cap, 0);
  metrics.fd_market_cap = dec(cap * (1 + n.uniform() * (stable ? 0 : 0.8)), 0);
  metrics.volume_usd_24h = dec(cap * (0.01 + n.uniform() * 0.09), 0);
  return {
    key: row.symbol.toLowerCase(),
    symbol: row.symbol,
    name: row.name,
    group: null,
    industry: null,
    exchange: null,
    instrument_id: row.instrumentId ?? null,
    chart_unavailable_reason: row.instrumentId ? null : NO_CHART,
    price: priceText(price),
    metrics,
    as_of_ms: asOf,
    error: null,
  };
}

/**
 * Builds the metric options of a family.
 * @param defs Definitions.
 * @param unavailable Reasons of unavailable metrics.
 * @return Contract rows.
 */
function metricRows(
  defs: MetricDef[],
  unavailable: Record<string, string>
): HeatmapMetric[] {
  return defs.map(([id, label, format, scale]) => ({
    id,
    label,
    format,
    scale: scale ?? null,
    available: !(id in unavailable),
    reason: unavailable[id] ?? null,
  }));
}

/**
 * Builds the grouping options of a family.
 * @param type Family.
 * @return Contract rows.
 */
function groupingRows(type: DemoHeatmapType): HeatmapGrouping[] {
  const none: HeatmapGrouping = {
    id: 'none',
    label: 'No group',
    available: true,
  };
  if (type === 'stocks') {
    return [{id: 'sector', label: 'Sector', available: true}, none];
  }
  if (type === 'etfs') {
    return [{id: 'asset_class', label: 'Asset class', available: true}, none];
  }
  return [none];
}

/**
 * Selects and builds the tiles of a data set.
 * @param dataset Data set id.
 * @param day UTC day number.
 * @param asOf Observation time.
 * @return Tiles in no particular order.
 */
function datasetTiles(
  dataset: string,
  day: number,
  asOf: number
): HeatmapTile[] {
  const market = noise('market', day);
  const marketDrift = market.normal() * 0.8;
  const sectorDrift = (sector: string) =>
    marketDrift + noise(sector, day).normal() * 0.9;
  const companies = (rows: readonly DemoCompany[]) =>
    rows.map(row => companyTile(row, day, sectorDrift(row.sector), asOf));
  switch (dataset) {
    case 'us_nasdaq100':
      return companies(
        DEMO_US_COMPANIES.filter(row => DEMO_NASDAQ_100.has(row.symbol))
      );
    case 'us_nasdaq_composite':
      return companies(
        DEMO_US_COMPANIES.filter(row => row.exchange === 'Nasdaq')
      );
    case 'us_nyse':
      return companies(
        DEMO_US_COMPANIES.filter(row => row.exchange === 'NYSE')
      );
    case 'us_sp500':
    case 'us_all':
      return companies(DEMO_US_COMPANIES);
    case 'tw_twse':
      return companies(
        DEMO_TW_COMPANIES.filter(row => row.exchange === 'TWSE')
      );
    case 'tw_tpex':
      return companies(
        DEMO_TW_COMPANIES.filter(row => row.exchange === 'TPEx')
      );
    case 'us_etfs':
      return DEMO_US_FUNDS.map(row => fundTile(row, day, marketDrift, asOf));
    case 'tw_etfs':
      return DEMO_TW_FUNDS.map(row => fundTile(row, day, marketDrift, asOf));
    default: {
      const cryptoDrift = marketDrift * 2;
      const coins = DEMO_COINS.filter(row =>
        dataset === 'crypto_ex_btc'
          ? row.symbol !== 'BTC'
          : dataset === 'crypto_ex_stable'
            ? !row.tags?.includes('stablecoin')
            : dataset === 'crypto_defi'
              ? (row.tags?.includes('defi') ?? false)
              : true
      );
      return coins.map(row => coinTile(row, day, cryptoDrift, asOf));
    }
  }
}

/** DEMO heatmap request. */
export interface DemoHeatmapQuery {
  /** Family. */
  type: DemoHeatmapType;
  /** Data set id; the family default when absent or of another family. */
  dataset?: string;
  /** Maximum tiles; the data set default when absent. */
  limit?: number;
}

/**
 * Builds a synthetic DEMO heatmap.
 * @param query Family, data set and limit.
 * @param nowMs Current time (UTC epoch ms); values change once per UTC day.
 * @return DEMO-marked snapshot, largest default size first.
 * @throws Error for an unknown family (never for a known one).
 */
export function demoHeatmapSnapshot(
  query: DemoHeatmapQuery,
  nowMs: number
): HeatmapSnapshot {
  const spec =
    DATASETS.find(row => row[0] === query.dataset && row[1] === query.type) ??
    DATASETS.find(row => row[0] === DEFAULTS[query.type]);
  if (!spec) throw new Error(`Unknown DEMO heatmap family ${query.type}`);
  const [id, type, label, , currency, defaultLimit] = spec;
  const day = Math.floor(nowMs / DAY_MS);
  const limit = Math.max(1, query.limit ?? defaultLimit);
  const size = DEFAULT_SIZE[type];
  const all = datasetTiles(id, day, nowMs).sort(
    (a, b) => Number(b.metrics?.[size] ?? 0) - Number(a.metrics?.[size] ?? 0)
  );
  const tiles = all.slice(0, limit);
  return {
    type,
    dataset: id,
    dataset_label: label,
    currency,
    tiles,
    universe_count: all.length,
    limit,
    priced_count: tiles.filter(tile => tile.price !== null).length,
    size_metrics: metricRows(SIZES[type], UNAVAILABLE[type]),
    color_metrics: metricRows(
      type === 'crypto' ? CRYPTO_COLORS : STOCK_COLORS,
      UNAVAILABLE[type]
    ),
    groupings: groupingRows(type),
    default_size: size,
    default_color: 'change_1d',
    default_grouping: DEFAULT_GROUPING[type],
    provenance: [
      {
        field: 'All values',
        source: 'DEMO synthetic data',
        as_of_ms: nowMs,
        note: 'Fictional symbols and deterministic values; not market data',
      },
    ],
    source_errors: {},
    pending: false,
    generated_at_ms: nowMs,
    demo: true,
  };
}
