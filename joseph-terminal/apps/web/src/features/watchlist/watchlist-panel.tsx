'use client';
/**
 * @fileoverview Watchlist of exact-venue instruments with last price, 24h
 * change, normalized funding and data freshness. The same symbol on two
 * venues is two rows; nothing is merged across venues.
 */
import {
  formatAge,
  formatSignedPct,
  groupThousands,
} from '@joseph/chart-trading';
import type {Instrument, MarketSnapshot} from '@joseph/contracts';
import type {WritableStore} from '@joseph/feature-registry';
import {X, ArrowUp, ArrowDown} from 'lucide-react';
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
}

/** Snapshot load result per instrument. */
type SnapshotMap = Record<string, MarketSnapshot | {error: string}>;

/**
 * Moves an item within a list.
 * @param list List.
 * @param index Index to move.
 * @param delta -1 or +1.
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
export function WatchlistPanel({ctx, items}: WatchlistPanelProps): ReactNode {
  const list = useStore(items);
  const selection = useStore(ctx.selection);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[]>([]);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);
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
        response => alive && setResults(response.instruments.slice(0, 12)),
        () => alive && setResults([])
      );
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [ctx.clients.market, query]);

  const add = (instrumentId: string) => {
    if (!list.includes(instrumentId)) items.set([...list, instrumentId]);
    setQuery('');
    setResults([]);
  };
  const remove = (instrumentId: string) =>
    items.set(list.filter(id => id !== instrumentId));
  const move = (index: number, delta: -1 | 1) => {
    items.set(moveItem(list, index, delta));
    requestAnimationFrame(() => rows.current[index + delta]?.focus());
  };
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
      </header>
      <div className={`j-panel__body ${styles.body}`}>
        <div className={styles.search}>
          <label className="j-label" htmlFor="watchlist-search">
            Add instrument
          </label>
          <input
            id="watchlist-search"
            className="j-input"
            placeholder="Search BTC, ETH, SOL"
            value={query}
            autoComplete="off"
            onChange={e => setQuery(e.target.value)}
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
                    onClick={() => add(instrument.instrument_id)}
                  >
                    <InstrumentLabel
                      instrumentId={instrument.instrument_id}
                      showType
                    />
                    {list.includes(instrument.instrument_id) && (
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
          <EmptyState title="Your watchlist is empty">
            Search above to add an instrument from any venue. Each venue&apos;s
            contract is listed separately.
          </EmptyState>
        ) : (
          <table className="j-table" aria-label="Watched instruments">
            <thead>
              <tr>
                <th>Instrument</th>
                <th className="j-num">Last</th>
                <th className="j-num">24h</th>
                <th className="j-num" title="Funding normalized to 8 hours">
                  Fund 8h
                </th>
                <th>Data</th>
                <th>
                  <span className={styles.srOnly}>Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((id, index) => {
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
                    onClick={() => ctx.select({instrumentId: id})}
                    onKeyDown={e => onRowKey(e, index, id)}
                    data-testid={`watch-row-${id}`}
                  >
                    <td>
                      <InstrumentLabel instrumentId={id} />
                    </td>
                    <td className="j-num">
                      {ok?.last ? groupThousands(ok.last) : '—'}
                    </td>
                    <td
                      className={`j-num ${change ? (change.startsWith('-') ? 'j-neg' : 'j-pos') : ''}`}
                    >
                      {formatSignedPct(change)}
                    </td>
                    <td className="j-num">
                      {formatSignedPct(
                        ok?.funding?.normalized_8h_pct ?? null,
                        4
                      )}
                    </td>
                    <td>
                      {ok ? (
                        <FeedBadge
                          state={ok.stale ? 'STALE' : ok.feed_state}
                          detail={
                            ok.stale ? formatAge(ok.data_age_ms) : undefined
                          }
                        />
                      ) : snap ? (
                        <FeedBadge state="UNAVAILABLE" />
                      ) : (
                        <span className="j-muted">…</span>
                      )}
                    </td>
                    <td className={styles.actions}>
                      <button
                        type="button"
                        className="j-btn j-btn--ghost j-btn--sm j-btn--icon"
                        aria-label={`Move ${id} up`}
                        disabled={index === 0}
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
                        disabled={index === list.length - 1}
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
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className={styles.hint}>
          Arrow keys move between rows, Enter opens the chart, Alt+arrows
          reorder, Delete removes.
        </p>
      </div>
    </section>
  );
}
