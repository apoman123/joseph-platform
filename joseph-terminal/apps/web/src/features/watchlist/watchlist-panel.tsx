/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Watchlist of exact-venue instruments with last price, 24h
 * change, normalized funding and data freshness. The same symbol on two
 * venues is two rows; nothing is merged across venues.
 */

'use client';
import {
  formatAge,
  formatCompactUsd,
  formatFixed,
  formatSignedPct,
  groupThousands,
} from '@joseph/chart-trading';
import type {Instrument, MarketSnapshot} from '@joseph/contracts';
import type {WritableStore} from '@joseph/feature-registry';
import {X, ArrowUp, ArrowDown, Columns3, Plus} from 'lucide-react';
import {
  WATCH_COLUMNS,
  nextSort,
  sortRows,
  type WatchColumn,
  type WatchSortKey,
  type WatchlistPrefs,
} from './columns';
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {usePolling} from '../trading/ui/hooks';
import {
  EmptyState,
  FeedBadge,
  InstrumentLabel,
  Notice,
} from '../trading/ui/primitives';
import styles from './watchlist.module.css';

/** Props of the watchlist panel. */
export interface WatchlistPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Ordered instrument ids (persisted by the feature). */
  items: WritableStore<readonly string[]>;
  /** Columns, sort and row source (persisted by the feature). */
  prefs: WritableStore<WatchlistPrefs>;
}

/**
 * Renders one snapshot cell.
 * @param column Column id.
 * @param snap Snapshot, or null while loading or unavailable.
 * @param failed Whether the snapshot request failed.
 * @return Cell content.
 */
function cellContent(
  column: WatchColumn,
  snap: MarketSnapshot | null,
  failed: boolean
): ReactNode {
  if (column === 'data') {
    if (snap) {
      return (
        <FeedBadge
          state={snap.stale ? 'STALE' : snap.feed_state}
          detail={snap.stale ? formatAge(snap.data_age_ms) : undefined}
        />
      );
    }
    return failed ? (
      <FeedBadge state="UNAVAILABLE" />
    ) : (
      <span className="j-muted">…</span>
    );
  }
  if (!snap) return '—';
  switch (column) {
    case 'last':
      return snap.last ? groupThousands(snap.last) : '—';
    case 'mark':
      return snap.mark ? groupThousands(snap.mark) : '—';
    case 'change':
      return formatSignedPct(snap.change_24h_pct ?? null);
    case 'turnover':
      return formatCompactUsd(snap.turnover_24h_quote ?? null);
    case 'spread':
      return snap.spread_pct ? `${formatFixed(snap.spread_pct, 3)}%` : '—';
    case 'funding':
      return formatSignedPct(snap.funding?.normalized_8h_pct ?? null, 4);
    case 'oi':
      return formatCompactUsd(snap.open_interest?.notional_usd ?? null);
    default:
      return '—';
  }
}

/** Snapshot load result per instrument. */
type SnapshotMap = Record<string, MarketSnapshot | {error: string}>;

/**
 * Moves an item within a list.
 * @param list List.
 * @param index Index to move.
 * @param delta 1 or +1.
 * @return New list.
 */
export function moveItem<T>(
  list: readonly T[],
  index: number,
  delta: -1 | 1
): readonly T[] {
  const target = index + delta;
  if (target < 0 || target >= list.length) return list;
  const next = [...list];
  const [item] = next.splice(index, 1);
  if (item !== undefined) next.splice(target, 0, item);
  return next;
}

/**
 * Renders the watchlist.
 * @param props Props.
 * @return Panel element.
 */
export function WatchlistPanel({
  ctx,
  items,
  prefs,
}: WatchlistPanelProps): ReactNode {
  const mine = useStore(items);
  const view = useStore(prefs);
  const qualifiedSource = view.source === 'qualified';
  const qualified = usePolling(
    async () =>
      qualifiedSource
        ? (await ctx.clients.market.getQualification(undefined, 'QUALIFIED'))
            .records
        : [],
    `qualified|${qualifiedSource}`,
    60_000
  );
  const qualifiedIds = (qualified.data ?? [])
    .filter(record => record.status === 'QUALIFIED')
    .map(record => record.instrument_id);
  const list = qualifiedSource ? qualifiedIds : mine;
  const [showColumns, setShowColumns] = useState(false);
  const selection = useStore(ctx.selection);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[]>([]);
  const rows = useRef<Array<HTMLTableRowElement | null>>([]);
  const snapshots = usePolling<SnapshotMap>(
    async () => {
      const settled = await Promise.allSettled(
        list.map(id => ctx.clients.market.getSnapshot(id))
      );
      const map: SnapshotMap = {};
      settled.forEach((result, i) => {
        const id = list[i];
        if (!id) return;
        map[id] =
          result.status === 'fulfilled' ? result.value : {error: 'Unavailable'};
      });
      return map;
    },
    list.join('|'),
    5000
  );

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      ctx.clients.market.listInstruments({q}).then(
        response => alive && void setResults(response.instruments.slice(0, 12)),
        () => alive && void setResults([])
      );
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [ctx.clients.market, query]);

  const add = (instrumentId: string) => {
    if (!mine.includes(instrumentId)) items.set([...mine, instrumentId]);
    setQuery('');
    setResults([]);
  };
  const remove = (instrumentId: string) => {
    if (!qualifiedSource) items.set(mine.filter(id => id !== instrumentId));
  };
  const manual = view.sort === null && !qualifiedSource;
  const move = (index: number, delta: -1 | 1) => {
    if (!manual) return;
    items.set(moveItem(mine, index, delta));
    requestAnimationFrame(() => rows.current[index + delta]?.focus());
  };
  const snapshotById: Record<string, MarketSnapshot | null> = {};
  for (const id of list) {
    const snap = snapshots.data?.[id];
    snapshotById[id] = snap && !('error' in snap) ? snap : null;
  }
  const ordered = sortRows(list, view.sort, snapshotById);
  const columns = WATCH_COLUMNS.filter(c => view.columns.includes(c.id));
  const sortBy = (key: WatchSortKey) =>
    void prefs.update(p => ({...p, sort: nextSort(p.sort, key)}));
  const ariaSort = (key: WatchSortKey) =>
    view.sort?.key === key
      ? view.sort.dir === 'asc'
        ? 'ascending'
        : 'descending'
      : undefined;
  const onRowKey = (
    event: KeyboardEvent<HTMLTableRowElement>,
    index: number,
    id: string
  ) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      if (event.altKey) move(index, delta);
      else rows.current[index + delta]?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      ctx.select({instrumentId: id});
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      remove(id);
    }
  };

  return (
    <section className="j-panel" aria-label="Watchlist">
      <header className="j-panel__header">
        <h2>Watchlist</h2>
        <span className={styles.grow} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
        <button
          type="button"
          className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
          aria-label="Choose columns"
          aria-expanded={showColumns}
          data-testid="watch-columns"
          onClick={() => void setShowColumns(open => !open)}
        >
          <Columns3 size={14} aria-hidden="true" />
        </button>
      </header>
      <div className={`j-panel__body ${styles.body}`}>
        <div className="j-segmented" role="group" aria-label="Watchlist source">
          <button
            type="button"
            aria-pressed={!qualifiedSource}
            onClick={() => void prefs.update(p => ({...p, source: 'mine'}))}
          >
            My list
          </button>
          <button
            type="button"
            aria-pressed={qualifiedSource}
            data-testid="watch-source-qualified"
            onClick={() =>
              void prefs.update(p => ({...p, source: 'qualified'}))
            }
          >
            Qualified perpetuals
          </button>
        </div>
        {showColumns && (
          <fieldset className={styles.columns} aria-label="Columns">
            <legend className="j-label">Columns</legend>
            {WATCH_COLUMNS.map(column => (
              <label key={column.id} className="j-check" title={column.title}>
                <input
                  type="checkbox"
                  checked={view.columns.includes(column.id)}
                  data-testid={`watch-column-${column.id}`}
                  onChange={event =>
                    void prefs.update(p => {
                      const next = event.target.checked
                        ? [...p.columns, column.id]
                        : p.columns.filter(c => c !== column.id);
                      return next.length > 0 ? {...p, columns: next} : p;
                    })
                  }
                />
                {column.label}
              </label>
            ))}
          </fieldset>
        )}
        {qualifiedSource && (
          <p className={styles.hint}>
            Exact-venue perpetuals the engine currently marks Qualified. Missing
            data is never counted as qualified.
          </p>
        )}
        {qualified.error && qualifiedSource && (
          <Notice tone="danger">
            Qualified list unavailable: {qualified.error}
          </Notice>
        )}
        <div className={styles.search} hidden={qualifiedSource}>
          <label className="j-label" htmlFor="watchlist-search">
            Add instrument
          </label>
          <input
            id="watchlist-search"
            className="j-input"
            placeholder="Search BTC, ETH, SOL"
            value={query}
            autoComplete="off"
            onChange={e => void setQuery(e.target.value)}
            onKeyDown={e => {
              const first = results[0];
              if (e.key === 'Enter' && first) add(first.instrument_id);
              if (e.key === 'Escape') setQuery('');
            }}
          />
          {results.length > 0 && (
            <ul
              className={styles.results}
              role="listbox"
              aria-label="Search results"
            >
              {results.map(instrument => (
                <li
                  key={instrument.instrument_id}
                  role="option"
                  aria-selected={false}
                >
                  <button
                    type="button"
                    className="j-menu__item"
                    onClick={() => void add(instrument.instrument_id)}
                  >
                    <InstrumentLabel
                      instrumentId={instrument.instrument_id}
                      showType
                    />
                    {mine.includes(instrument.instrument_id) && (
                      <span className="j-muted">Added</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {snapshots.error && (
          <Notice tone="warning">
            Prices could not be refreshed: {snapshots.error}
          </Notice>
        )}
        {list.length === 0 ? (
          qualifiedSource ? (
            <EmptyState title="No qualified perpetuals">
              {qualified.loading
                ? 'Loading the qualification results…'
                : 'The engine reports no instrument as Qualified right now.'}
            </EmptyState>
          ) : (
            <EmptyState title="Your watchlist is empty">
              Search above to add an instrument from any venue. Each
              venue&apos;s contract is listed separately.
            </EmptyState>
          )
        ) : (
          <table className="j-table" aria-label="Watched instruments">
            <thead>
              <tr>
                <th aria-sort={ariaSort('symbol')}>
                  <button
                    type="button"
                    className={styles.sortable}
                    onClick={() => void sortBy('symbol')}
                  >
                    Instrument
                  </button>
                </th>
                {columns.map(column => (
                  <th
                    key={column.id}
                    className={column.id === 'data' ? undefined : 'j-num'}
                    title={column.title}
                    aria-sort={ariaSort(column.id)}
                  >
                    <button
                      type="button"
                      className={styles.sortable}
                      data-testid={`watch-sort-${column.id}`}
                      onClick={() => void sortBy(column.id)}
                    >
                      {column.label}
                      {view.sort?.key === column.id
                        ? view.sort.dir === 'asc'
                          ? ' ▲'
                          : ' ▼'
                        : ''}
                    </button>
                  </th>
                ))}
                <th>
                  <span className={styles.srOnly}>Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((id, index) => {
                const snap = snapshots.data?.[id];
                const ok = snap && !('error' in snap) ? snap : null;
                const change = ok?.change_24h_pct ?? null;
                return (
                  <tr
                    key={id}
                    ref={el => {
                      rows.current[index] = el;
                    }}
                    tabIndex={0}
                    aria-selected={selection.instrumentId === id}
                    className={styles.row}
                    onClick={() => void ctx.select({instrumentId: id})}
                    onKeyDown={e => void onRowKey(e, index, id)}
                    data-testid={`watch-row-${id}`}
                  >
                    <td>
                      <InstrumentLabel instrumentId={id} />
                    </td>
                    {columns.map(column => (
                      <td
                        key={column.id}
                        className={
                          column.id === 'data'
                            ? undefined
                            : column.id === 'change' && change
                              ? `j-num ${change.startsWith('-') ? 'j-neg' : 'j-pos'}`
                              : 'j-num'
                        }
                      >
                        {cellContent(column.id, ok, Boolean(snap) && !ok)}
                      </td>
                    ))}
                    <td className={styles.actions}>
                      {qualifiedSource ? (
                        <button
                          type="button"
                          className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
                          aria-label={`Add ${id} to my list`}
                          disabled={mine.includes(id)}
                          onClick={e => {
                            e.stopPropagation();
                            add(id);
                          }}
                        >
                          <Plus size={14} aria-hidden />
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
                            aria-label={`Move ${id} up`}
                            disabled={index === 0 || !manual}
                            onClick={e => {
                              e.stopPropagation();
                              move(index, -1);
                            }}
                          >
                            <ArrowUp size={14} aria-hidden />
                          </button>
                          <button
                            type="button"
                            className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
                            aria-label={`Move ${id} down`}
                            disabled={index === list.length - 1 || !manual}
                            onClick={e => {
                              e.stopPropagation();
                              move(index, 1);
                            }}
                          >
                            <ArrowDown size={14} aria-hidden />
                          </button>
                          <button
                            type="button"
                            className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
                            aria-label={`Remove ${id} from watchlist`}
                            onClick={e => {
                              e.stopPropagation();
                              remove(id);
                            }}
                          >
                            <X size={14} aria-hidden />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className={styles.hint}>
          Arrow keys move between rows, Enter opens the chart
          {manual ? ', Alt+arrows reorder, Delete removes' : ''}. Click a column
          to sort; click it again to return to your order.
        </p>
      </div>
    </section>
  );
}
