/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Real stock heatmap, dated fundamentals and explicit coverage. */

'use client';
import {
  PythonClient,
  type ChartState,
  type HeatmapSnapshot,
  type HeatmapTile,
  type CompanyMetadataBatch,
} from '@joseph/contracts';
import {useEffect, useMemo, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {ids} from './ids';
import {useStore} from './react-bindings';
import styles from './script-ui.module.css';

/** A weighted rectangle in normalized SVG coordinates. */
export interface HeatRect<T> {
  /** Original item carried through layout. */
  item: T;
  /** Horizontal pixel offset. */
  x: number;
  /** Vertical pixel offset. */
  y: number;
  /** Positive rectangle width. */
  width: number;
  /** Positive rectangle height. */
  height: number;
}

/**
 * Partitions all weighted items into bounded rectangles without overlap.
 * @param rows Items and positive finite weights; invalid weights become one.
 * @param box Available normalized rectangle.
 * @return One rectangle per item; input arrays are never mutated.
 */
export function heatmapLayout<T>(
  rows: ReadonlyArray<{item: T; weight: number}>,
  box = {x: 0, y: 0, width: 1000, height: 600}
): Array<HeatRect<T>> {
  if (!rows.length) return [];
  const firstRow = rows[0];
  if (!firstRow) return [];
  if (rows.length === 1) return [{item: firstRow.item, ...box}];
  const weights = rows.map(row =>
    Number.isFinite(row.weight) && row.weight > 0 ? row.weight : 1
  );
  const total = weights.reduce((a, b) => a + b, 0);
  let split = 1;
  let left = weights[0] ?? 1;
  while (
    split < rows.length - 1 &&
    Math.abs(left + (weights[split] ?? 1) - total / 2) <
      Math.abs(left - total / 2)
  ) {
    left += weights[split] ?? 1;
    split += 1;
  }
  const ratio = left / total;
  const horizontal = box.width >= box.height;
  const first = {
    ...box,
    width: horizontal ? box.width * ratio : box.width,
    height: horizontal ? box.height : box.height * ratio,
  };
  const second = {
    ...box,
    x: horizontal ? box.x + first.width : box.x,
    y: horizontal ? box.y : box.y + first.height,
    width: horizontal ? box.width - first.width : box.width,
    height: horizontal ? box.height : box.height - first.height,
  };
  return [
    ...heatmapLayout(rows.slice(0, split), first),
    ...heatmapLayout(rows.slice(split), second),
  ];
}

/**
 * Maps daily return to a legible diverging color; missing remains neutral.
 * @param change Daily percentage string, or null when unavailable.
 * @return Bounded RGB fill without accepting arbitrary CSS from data.
 */
export function heatmapColor(change: string | null): string {
  if (change === null || !Number.isFinite(Number(change))) return '#344754';
  const amount = Math.min(Math.abs(Number(change)) / 3, 1);
  const target = Number(change) >= 0 ? [29, 135, 102] : [181, 52, 52];
  return `rgb(${target.map(value => Math.round(47 + (value - 47) * amount)).join(' ')})`;
}

/**
 * Produces explicitly synthetic heatmap data from the existing DEMO provider.
 * @param runtime Terminal with DEMO market clients.
 * @param region Stock region to select.
 * @param query Optional symbol/company search.
 * @return DEMO-marked snapshot with no fabricated sector/capitalization values.
 */
async function demoSnapshot(
  runtime: TerminalRuntime,
  region: 'us' | 'taiwan',
  query: string
): Promise<HeatmapSnapshot> {
  const listing = await runtime.clients.market.listInstruments({
    marketType: 'stock',
    q: query,
    limit: 1000,
  });
  const rows = listing.instruments.filter(
    row => row.calendar === (region === 'us' ? 'XNYS' : 'XTAI')
  );
  const actual = await Promise.all(
    rows.map(async row => {
      const quote = await runtime.clients.market.getSnapshot(row.instrument_id);
      return {
        instrument_id: row.instrument_id,
        symbol: row.display_symbol,
        name: row.local_name ?? null,
        exchange: row.listing_exchange ?? 'DEMO',
        last: quote.last ?? null,
        change_pct: quote.change_24h_pct ?? null,
        volume: quote.volume_24h_base ?? null,
        turnover: quote.turnover_24h_quote ?? null,
        market_cap: null,
        sector: null,
        metadata_as_of_ms: null,
        feed_state: 'DEMO',
        source: 'DEMO',
        as_of_ms: quote.as_of_ms,
        error: null,
      };
    })
  );
  return {
    tiles: actual,
    universe_count: rows.length,
    quote_count: actual.length,
    generated_at_ms: Date.now(),
    region,
    currency: region === 'us' ? 'USD' : 'TWD',
    available_metrics: ['equal', 'volume', 'turnover', 'change_pct'],
    missing_metadata: true,
  };
}

/**
 * Renders a saved stock heatmap tab with real metrics and drill-down controls.
 * @param props Chart identity, selection status and terminal runtime.
 * @return Responsive heatmap, provenance, coverage and missing-data diagnostics.
 */
export function StockHeatmap({
  chart,
  active,
  runtime,
}: {
  chart: ChartState;
  active: boolean;
  runtime: TerminalRuntime;
}): ReactElement {
  const workspace = useStore(runtime.workspace);
  const key = `heatmap.${chart.chart_id}`;
  const saved = (workspace.panels as Record<string, unknown> | undefined)?.[
    key
  ] as {region?: 'us' | 'taiwan'; size?: string; group?: string} | undefined;
  const region = saved?.region === 'taiwan' ? 'taiwan' : 'us';
  const size = saved?.size ?? 'equal';
  const grouping = saved?.group ?? 'exchange';
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [snapshot, setSnapshot] = useState<HeatmapSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<HeatmapTile | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const client = useMemo(
    () =>
      new PythonClient({
        baseUrl: runtime.config.marketApiUrl ?? '',
        getToken: () => runtime.config.devToken,
      }),
    [runtime]
  );
  const save = (patch: Record<string, unknown>) =>
    void runtime.dispatch({
      type: 'setPanel',
      featureId: key,
      state: {...saved, ...patch},
    });
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setSnapshot(null);
    setError(null);
    setFocus(null);
    setGroup(null);
    const load = async () => {
      try {
        const data = await (runtime.config.demo
          ? demoSnapshot(runtime, region, search)
          : client.heatmap(region, search, 1000, controller.signal));
        if (!disposed) {
          setSnapshot(data);
          setError(null);
        }
      } catch (failure) {
        if (!disposed) {
          setError(
            failure instanceof Error
              ? failure.message
              : 'Heatmap data unavailable'
          );
        }
      }
      if (!disposed) timer = setTimeout(() => void load(), 30000);
    };
    void load();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [runtime, client, region, search, revision]);
  const groupName = (tile: HeatmapTile) =>
    grouping === 'sector' ? (tile.sector ?? 'Unclassified') : tile.exchange;
  const metric = (tile: HeatmapTile) => {
    if (size === 'equal') return 1;
    const value =
      size === 'market_cap'
        ? tile.market_cap
        : size === 'turnover'
          ? tile.turnover
          : tile.volume;
    return value === null || !Number.isFinite(Number(value))
      ? 0
      : Number(value);
  };
  const groups = new Map<string, HeatmapTile[]>();
  for (const tile of snapshot?.tiles ?? []) {
    const name = groupName(tile);
    if (group && name !== group) continue;
    groups.set(name, [...(groups.get(name) ?? []), tile]);
  }
  const available = snapshot?.available_metrics ?? ['equal'];
  const sized = [...groups].map(([name, rows]) => ({
    item: {name, rows},
    weight: rows.reduce((sum, tile) => sum + metric(tile), 0),
  }));
  const blocks = heatmapLayout(sized);
  const open = (tile: HeatmapTile) =>
    void runtime.dispatch({
      type: 'addChart',
      chartId: `chart-${ids.next()}`,
      instrumentId: tile.instrument_id,
      viewType: 'candles',
    });
  return (
    <section
      className={styles.heatmap}
      aria-label="Stock heatmap"
      onPointerDown={() => {
        if (!active) {
          runtime.dispatch({type: 'setActiveChart', chartId: chart.chart_id});
        }
      }}
    >
      <header className={styles.toolbar}>
        <strong>Stock heatmap</strong>
        <label>
          Market{' '}
          <select
            className="j-select"
            value={region}
            onChange={event => void save({region: event.target.value})}
          >
            <option value="us">US stocks</option>
            <option value="taiwan">Taiwan stocks</option>
          </select>
        </label>
        <label>
          Size{' '}
          <select
            className="j-select"
            value={available.includes(size) ? size : 'equal'}
            onChange={event => void save({size: event.target.value})}
          >
            {['equal', 'volume', 'turnover', 'market_cap'].map(value => (
              <option
                key={value}
                value={value}
                disabled={!available.includes(value)}
              >
                {value.replace('_', ' ')}
                {!available.includes(value) ? ' — unavailable' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          Group{' '}
          <select
            className="j-select"
            value={grouping}
            onChange={event => {
              save({group: event.target.value});
              setGroup(null);
            }}
          >
            <option value="exchange">Exchange</option>
            <option value="sector" disabled={!available.includes('sector')}>
              Sector{!available.includes('sector') ? ' — unavailable' : ''}
            </option>
          </select>
        </label>
        <button
          type="button"
          className="j-btn"
          onClick={() => void setRevision(n => n + 1)}
        >
          Refresh
        </button>
        {group && (
          <button
            type="button"
            className="j-btn"
            onClick={() => void setGroup(null)}
          >
            All groups
          </button>
        )}
      </header>
      <div className={styles.toolbar}>
        <form
          onSubmit={event => {
            event.preventDefault();
            setSearch(query.trim());
          }}
        >
          <label>
            Search stocks{' '}
            <input
              className="j-input"
              value={query}
              onChange={event => void setQuery(event.target.value)}
            />
          </label>
          <button className="j-btn" type="submit">
            Search
          </button>
        </form>
        <span className={styles.notice}>
          Color: daily return · red −3% / neutral 0% / green +3% · missing =
          gray
        </span>
      </div>
      {!snapshot ? (
        <p role={error ? 'alert' : 'status'}>
          {error ?? 'Loading stock quotes…'}
        </p>
      ) : !snapshot.tiles.length ? (
        <p role="status">No stocks available in this configured market.</p>
      ) : (
        <svg
          className={styles.map}
          viewBox="0 0 1000 600"
          preserveAspectRatio="none"
          aria-label="Daily stock performance"
        >
          {blocks.map(block => (
            <g key={block.item.name}>
              <rect
                x={block.x}
                y={block.y}
                width={block.width}
                height={block.height}
                fill="#122431"
              />
              <g
                role="button"
                tabIndex={0}
                aria-label={`Focus ${block.item.name}`}
                onClick={() => void setGroup(block.item.name)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setGroup(block.item.name);
                  }
                }}
              >
                <text x={block.x + 5} y={block.y + 15} fontSize={12}>
                  {block.item.name}
                </text>
              </g>
              {heatmapLayout(
                block.item.rows.map(item => ({item, weight: metric(item)})),
                {
                  x: block.x + 1,
                  y: block.y + 21,
                  width: Math.max(0, block.width - 2),
                  height: Math.max(0, block.height - 22),
                }
              ).map(cell => (
                <g
                  key={cell.item.instrument_id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Open ${cell.item.symbol}, ${cell.item.change_pct ?? 'unavailable'} percent`}
                  onMouseEnter={() => void setFocus(cell.item)}
                  onFocus={() => void setFocus(cell.item)}
                  onClick={() => void open(cell.item)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      open(cell.item);
                    }
                  }}
                >
                  <title>{`${cell.item.symbol} · ${cell.item.name ?? ''}\n${cell.item.last ?? 'unavailable'} ${snapshot.currency} | ${cell.item.change_pct ?? 'unavailable'}%\n${cell.item.source ?? 'No quote'} | ${cell.item.error ?? ''}`}</title>
                  <rect
                    x={cell.x + 0.5}
                    y={cell.y + 0.5}
                    width={Math.max(0, cell.width - 1)}
                    height={Math.max(0, cell.height - 1)}
                    fill={heatmapColor(cell.item.change_pct)}
                  />
                  {cell.width > 35 && cell.height > 20 && (
                    <text
                      x={cell.x + cell.width / 2}
                      y={cell.y + cell.height / 2}
                      textAnchor="middle"
                      fontSize={Math.min(20, cell.width / 6)}
                    >
                      {cell.item.symbol}
                    </text>
                  )}
                  {cell.width > 65 && cell.height > 50 && (
                    <text
                      x={cell.x + cell.width / 2}
                      y={cell.y + cell.height / 2 + 20}
                      textAnchor="middle"
                      fontSize={12}
                    >
                      {cell.item.change_pct === null
                        ? 'No quote'
                        : `${Number(cell.item.change_pct).toFixed(2)}%`}
                    </text>
                  )}
                </g>
              ))}
            </g>
          ))}
        </svg>
      )}
      <footer className={styles.notice}>
        {error && <p role="alert">{error}</p>}
        {Object.entries(snapshot?.source_errors ?? {}).map(
          ([source, message]) => (
            <p role="status" key={source}>
              {source}: {message}
            </p>
          )
        )}
        {snapshot && (
          <p>
            {runtime.config.demo ? 'DEMO · ' : ''}
            {snapshot.quote_count}/{snapshot.tiles.length} quotes ·{' '}
            {snapshot.universe_count} eligible listings · {snapshot.currency} ·
            as of {new Date(snapshot.generated_at_ms).toISOString()} · refresh
            30s. Configured feed coverage applies (Alpaca IEX is not
            consolidated SIP).
          </p>
        )}
        {focus && (
          <p role="status">
            {focus.symbol} · {focus.name} · {focus.last ?? 'No price'} ·{' '}
            {focus.change_pct ?? 'No daily change'}% ·{' '}
            {focus.source ?? 'Unavailable'} ({focus.feed_state}) · quote{' '}
            {focus.as_of_ms === null
              ? 'unavailable'
              : new Date(focus.as_of_ms).toISOString()}{' '}
            {focus.error}
            {focus.metadata_as_of_ms !== null
              ? ` · User metadata ${new Date(focus.metadata_as_of_ms).toISOString()}`
              : ''}
          </p>
        )}
        <p>
          Market cap and sector require explicit dated company metadata. Missing
          size values use equal fallback weight; gray means no daily return.
        </p>
        {!runtime.config.demo && (
          <label>
            Import dated company metadata (JSON){' '}
            <input
              type="file"
              accept="application/json,.json"
              onChange={event => {
                const file = event.target.files?.[0];
                if (!file) return;
                if (file.size > 2000000) {
                  setError('Metadata file exceeds 2 MB');
                  return;
                }
                void file
                  .text()
                  .then(text =>
                    client.metadata(JSON.parse(text) as CompanyMetadataBatch)
                  )
                  .then(
                    () => void setRevision(n => n + 1),
                    failure =>
                      void setError(
                        failure instanceof Error
                          ? failure.message
                          : 'Invalid metadata'
                      )
                  );
                event.target.value = '';
              }}
            />
          </label>
        )}
      </footer>
    </section>
  );
}
