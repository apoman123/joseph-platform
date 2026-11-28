/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap view settings, value formatting and the mapping of
 * a snapshot to weighted, grouped tiles.
 *
 * Settings persist per tab in the workspace panel `heatmap.<chart id>`.
 * Size and colour metrics the snapshot marks unavailable fall back to the
 * snapshot's defaults. Tiles without a value of the size metric are left
 * out of the map and counted; Mono size gives every tile the same area and
 * keeps the default size metric's order.
 */

import type {
  HeatmapMetric,
  HeatmapSnapshot,
  HeatmapTile,
} from '@joseph/contracts';
import type {SchemeId} from './colors';
import type {GroupInput, IndustryInput} from './treemap';

/** Heatmap family. */
export type HeatmapType = 'stocks' | 'etfs' | 'crypto';

/** Grouping id. */
export type GroupingId = 'sector' | 'asset_class' | 'none';

/** Tile title option. */
export type TitleMode = 'none' | 'ticker' | 'description';

/** Tile value line option. */
export type ValueMode = 'none' | 'color' | 'price' | 'size';

/** Persisted settings of one heatmap tab. */
export interface HeatmapSettings {
  /** Heatmap family. */
  type: HeatmapType;
  /** Data set id, or null for the family default. */
  dataset: string | null;
  /** Size metric id, or null for the snapshot default. */
  size: string | null;
  /** Colour metric id, or null for the snapshot default. */
  color: string | null;
  /** Grouping, or null for the snapshot default. */
  grouping: GroupingId | null;
  /** Tile title. */
  title: TitleMode;
  /** Whether to draw a letter badge. */
  logo: boolean;
  /** Tile value line. */
  value: ValueMode;
  /** Colour scheme. */
  scheme: SchemeId;
  /** Zoomed group name, or null for the whole map. */
  zoom: string | null;
}

/** Default settings: an S&P 500 stock map by sector. */
export const DEFAULT_SETTINGS: HeatmapSettings = {
  type: 'stocks',
  dataset: null,
  size: null,
  color: null,
  grouping: null,
  title: 'ticker',
  logo: false,
  value: 'color',
  scheme: 'classic',
  zoom: null,
};

const GROUPINGS: readonly GroupingId[] = ['sector', 'asset_class', 'none'];

/**
 * Picks a string from a set.
 * @param value Candidate.
 * @param allowed Allowed values.
 * @param fallback Fallback.
 * @return The candidate when allowed, else the fallback.
 */
function pick<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T
): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * Optional string or null.
 * @param value Candidate.
 * @return The string, or null.
 */
function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/**
 * Reads saved settings defensively, including the legacy stock-heatmap
 * shape (`region`: us/taiwan, `size`: equal/volume/turnover/market_cap).
 * @param saved Stored panel value (any shape).
 * @return Complete settings.
 */
export function readSettings(saved: unknown): HeatmapSettings {
  const raw = (saved && typeof saved === 'object' ? saved : {}) as Record<
    string,
    unknown
  >;
  const legacy = 'region' in raw && !('type' in raw);
  const type = pick(raw.type, ['stocks', 'etfs', 'crypto'], 'stocks');
  const legacySize: Record<string, string> = {
    equal: 'mono',
    volume: 'volume_1d',
    turnover: 'turnover_1d',
    market_cap: 'market_cap',
  };
  return {
    type,
    dataset: legacy
      ? raw.region === 'taiwan'
        ? 'tw_twse'
        : 'us_all'
      : text(raw.dataset),
    size: legacy ? (legacySize[String(raw.size)] ?? null) : text(raw.size),
    color: legacy ? null : text(raw.color),
    grouping:
      !legacy && GROUPINGS.includes(raw.grouping as GroupingId)
        ? (raw.grouping as GroupingId)
        : null,
    title: pick(raw.title, ['none', 'ticker', 'description'], 'ticker'),
    logo: raw.logo === true,
    value: pick(raw.value, ['none', 'color', 'price', 'size'], 'color'),
    scheme: pick(raw.scheme, ['classic', 'colorblind', 'mono'], 'classic'),
    zoom: legacy ? null : text(raw.zoom),
  };
}

/**
 * Finds an available metric, else the default.
 * @param options Metric options of the snapshot.
 * @param wanted Requested id, or null.
 * @param fallback Default id.
 * @return The metric to use, or undefined when the list is empty.
 */
export function chooseMetric(
  options: readonly HeatmapMetric[],
  wanted: string | null,
  fallback: string
): HeatmapMetric | undefined {
  return (
    options.find(row => row.id === wanted && row.available) ??
    options.find(row => row.id === fallback) ??
    options.find(row => row.available)
  );
}

/**
 * Chooses the grouping in effect.
 * @param snapshot Snapshot.
 * @param wanted Requested grouping, or null.
 * @return Available grouping.
 */
export function chooseGrouping(
  snapshot: HeatmapSnapshot,
  wanted: GroupingId | null
): GroupingId {
  const found = snapshot.groupings.find(
    row => row.id === wanted && row.available
  );
  return found ? found.id : snapshot.default_grouping;
}

/**
 * Reads a metric value of a tile.
 * @param tile Tile.
 * @param metric Metric id.
 * @return Finite number, or null when unavailable.
 */
export function metricValue(tile: HeatmapTile, metric: string): number | null {
  const raw = tile.metrics?.[metric];
  if (raw === undefined || raw === null || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/**
 * Formats a large amount compactly (1.2T, 345.6B, 12.3M, 4.5K).
 * @param value Amount.
 * @return Compact text.
 */
export function compact(value: number): string {
  const abs = Math.abs(value);
  for (const [limit, suffix] of [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ] as const) {
    if (abs >= limit) {
      const scaled = value / limit;
      return `${scaled.toFixed(Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2)}${suffix}`;
    }
  }
  return abs >= 100 ? value.toFixed(0) : value.toFixed(2);
}

/**
 * Formats a metric value for display.
 * @param metric Metric definition (format decides the style).
 * @param value Value, or null.
 * @param currency Currency of currency-valued metrics.
 * @return Display text; `—` when unavailable.
 */
export function formatMetric(
  metric: Pick<HeatmapMetric, 'format'> | undefined,
  value: number | null,
  currency: string
): string {
  if (value === null || !metric) return '—';
  switch (metric.format) {
    case 'percent':
      return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
    case 'ratio':
      return `${value.toFixed(2)}×`;
    case 'currency':
      return `${compact(value)} ${currency}`;
    case 'number':
      return compact(value);
    default:
      return String(value);
  }
}

/**
 * Formats a price string without losing its source precision.
 * @param price Decimal string, or null.
 * @param currency Currency.
 * @return Display text.
 */
export function formatPrice(
  price: string | null | undefined,
  currency: string
): string {
  if (price === null || price === undefined || price === '') return '—';
  return `${price} ${currency}`;
}

/** Label of a tile's missing group. */
export const UNCLASSIFIED = 'Unclassified';

/** A tile prepared for the layout. */
export interface PreparedTile {
  /** Tile. */
  tile: HeatmapTile;
  /** Index in the snapshot's tiles. */
  index: number;
  /** Size weight (1 with Mono size). */
  weight: number;
  /** Sort key: the size value, or the default size's with Mono size. */
  rank: number;
  /** Colour metric value, or null. */
  color: number | null;
  /** Group name, or null without grouping. */
  group: string | null;
  /** Industry name, or null. */
  industry: string | null;
}

/** The tiles of the current view. */
export interface PreparedView {
  /** Tiles shown, largest first. */
  tiles: PreparedTile[];
  /** Groups for the layout. */
  groups: GroupInput[];
  /** Group names of the whole (unzoomed) map, largest first. */
  allGroups: string[];
  /** Tiles left out for lack of a size value. */
  withoutSize: number;
}

/** Inputs of `prepareView`. */
export interface ViewOptions {
  /** Size metric id. */
  size: string;
  /** Default size metric id (orders Mono size). */
  defaultSize: string;
  /** Colour metric id. */
  color: string;
  /** Grouping in effect. */
  grouping: GroupingId;
  /** Zoomed group name, or null. */
  zoom: string | null;
}

/**
 * Maps a snapshot to weighted tiles and groups.
 * @param snapshot Snapshot.
 * @param options Metrics, grouping and zoom.
 * @return Prepared view.
 */
export function prepareView(
  snapshot: HeatmapSnapshot,
  options: ViewOptions
): PreparedView {
  const mono = options.size === 'mono';
  const order = mono ? options.defaultSize : options.size;
  let withoutSize = 0;
  const all: PreparedTile[] = [];
  snapshot.tiles.forEach((tile, index) => {
    const sizeValue = metricValue(tile, order);
    if (!mono && (sizeValue === null || sizeValue <= 0)) {
      withoutSize += 1;
      return;
    }
    const grouped = options.grouping !== 'none';
    all.push({
      tile,
      index,
      // Mono size: equal areas; the order still follows the default size.
      weight: mono ? 1 : (sizeValue ?? 0),
      rank: sizeValue ?? 0,
      color: metricValue(tile, options.color),
      group: grouped ? tile.group || UNCLASSIFIED : null,
      industry: grouped && tile.industry ? tile.industry : null,
    });
  });
  all.sort((a, b) => b.rank - a.rank || a.index - b.index);
  const groupTotals = new Map<string, number>();
  for (const row of all) {
    if (row.group === null) continue;
    groupTotals.set(row.group, (groupTotals.get(row.group) ?? 0) + row.weight);
  }
  const allGroups = [...groupTotals]
    .sort((a, b) => b[1] - a[1])
    .map(([name]) => name);
  const zoom =
    options.zoom !== null && groupTotals.has(options.zoom)
      ? options.zoom
      : null;
  const tiles = zoom === null ? all : all.filter(row => row.group === zoom);
  return {tiles, groups: groupInputs(tiles), allGroups, withoutSize};
}

/**
 * Groups prepared tiles by group and industry.
 * @param tiles Prepared tiles.
 * @return Layout groups (a single unnamed group without grouping).
 */
function groupInputs(tiles: readonly PreparedTile[]): GroupInput[] {
  const groups = new Map<string | null, Map<string | null, IndustryInput>>();
  tiles.forEach((row, position) => {
    let industries = groups.get(row.group);
    if (!industries) {
      industries = new Map();
      groups.set(row.group, industries);
    }
    let industry = industries.get(row.industry);
    if (!industry) {
      industry = {name: row.industry, tiles: []};
      industries.set(row.industry, industry);
    }
    (industry.tiles as Array<{index: number; weight: number}>).push({
      index: position,
      weight: row.weight,
    });
  });
  return [...groups].map(([name, industries]) => ({
    name,
    industries: [...industries.values()],
  }));
}

/**
 * Lists peers of a tile: other tiles of its industry (or group), by size.
 * @param tiles Prepared tiles of the view.
 * @param target Hovered tile.
 * @param limit Most peers to list.
 * @return Peers, largest first, the target excluded.
 */
export function peersOf(
  tiles: readonly PreparedTile[],
  target: PreparedTile,
  limit = 8
): PreparedTile[] {
  const sameIndustry = target.industry !== null;
  return tiles
    .filter(
      row =>
        row !== target &&
        (sameIndustry
          ? row.industry === target.industry && row.group === target.group
          : row.group === target.group)
    )
    .slice(0, limit);
}
