/**
 * @fileoverview Watchlist columns and sorting (pure). Values come from the
 * market engine's snapshots; a missing value sorts last and shows a dash.
 */
import type {MarketSnapshot} from '@joseph/contracts';

/** Column ids. */
export type WatchColumn =
  | 'last'
  | 'mark'
  | 'change'
  | 'turnover'
  | 'spread'
  | 'funding'
  | 'oi'
  | 'data';

/** Sortable keys: columns plus the instrument name. */
export type WatchSortKey = WatchColumn | 'symbol';

/** Sort state; `null` keeps the user's manual order. */
export type WatchSort = {key: WatchSortKey; dir: 'asc' | 'desc'} | null;

/** View preferences of the watchlist. */
export interface WatchlistPrefs {
  /** Visible columns. */
  columns: readonly WatchColumn[];
  /** Sort. */
  sort: WatchSort;
  /** Row source: the user's list or the engine's qualified perpetuals. */
  source: 'mine' | 'qualified';
}

/** Column metadata in display order. */
export const WATCH_COLUMNS: readonly {
  id: WatchColumn;
  label: string;
  title: string;
}[] = [
  {id: 'last', label: 'Last', title: 'Last traded price'},
  {id: 'mark', label: 'Mark', title: 'Mark price'},
  {id: 'change', label: '24h', title: 'Change over 24 hours'},
  {id: 'turnover', label: 'Turnover', title: '24h quote turnover'},
  {id: 'spread', label: 'Spread', title: 'Best bid/ask spread'},
  {id: 'funding', label: 'Fund 8h', title: 'Funding normalized to 8 hours'},
  {id: 'oi', label: 'OI', title: 'Open interest'},
  {id: 'data', label: 'Data', title: 'Data provenance and freshness'},
];

/** Columns shown by default (the original watchlist). */
export const DEFAULT_COLUMNS: readonly WatchColumn[] = [
  'last',
  'change',
  'funding',
  'data',
];

/**
 * Keeps only known column ids, in display order.
 * @param value Stored value (untrusted).
 * @return Columns, or the defaults when nothing valid remains.
 */
export function normalizeColumns(value: unknown): WatchColumn[] {
  if (!Array.isArray(value)) return [...DEFAULT_COLUMNS];
  const wanted = new Set(value);
  const columns = WATCH_COLUMNS.map(c => c.id).filter(id => wanted.has(id));
  return columns.length > 0 ? columns : [...DEFAULT_COLUMNS];
}

/**
 * Numeric sort value of a snapshot column.
 * @param snapshot Snapshot or null when unavailable.
 * @param key Column.
 * @return Number, or null when missing.
 */
export function sortValue(
  snapshot: MarketSnapshot | null,
  key: WatchColumn
): number | null {
  if (!snapshot) return null;
  const raw: string | null | undefined = {
    last: snapshot.last,
    mark: snapshot.mark,
    change: snapshot.change_24h_pct,
    turnover: snapshot.turnover_24h_quote,
    spread: snapshot.spread_pct,
    funding: snapshot.funding?.normalized_8h_pct,
    oi: snapshot.open_interest?.notional_usd,
    data: snapshot.stale ? '1' : '0',
  }[key];
  if (raw == null) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/**
 * Cycles a header click: descending, ascending, then manual order.
 * @param current Current sort.
 * @param key Clicked key.
 * @return Next sort.
 */
export function nextSort(current: WatchSort, key: WatchSortKey): WatchSort {
  if (!current || current.key !== key)
    return {key, dir: key === 'symbol' ? 'asc' : 'desc'};
  if (current.dir === (key === 'symbol' ? 'asc' : 'desc'))
    return {key, dir: key === 'symbol' ? 'desc' : 'asc'};
  return null;
}

/**
 * Sorts instrument ids for display; missing values always sort last.
 * @param ids Instrument ids in manual order.
 * @param sort Sort state.
 * @param snapshots Snapshot per id (null when unavailable).
 * @return Sorted copy (the manual order when `sort` is null).
 */
export function sortRows(
  ids: readonly string[],
  sort: WatchSort,
  snapshots: Readonly<Record<string, MarketSnapshot | null>>
): string[] {
  if (!sort) return [...ids];
  const factor = sort.dir === 'asc' ? 1 : -1;
  return [...ids].sort((a, b) => {
    if (sort.key === 'symbol') return factor * a.localeCompare(b);
    const va = sortValue(snapshots[a] ?? null, sort.key);
    const vb = sortValue(snapshots[b] ?? null, sort.key);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    return factor * (va - vb);
  });
}
