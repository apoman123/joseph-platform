/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Screener: pick a market, combine built-in filter scripts
 * (or an owned Filter script) with AND, and scan it on the market engine.
 * Results list real snapshot columns; instruments without the data a
 * filter needs are listed as unavailable, never as matches.
 */

'use client';
import {
  formatCompactUsd,
  formatFixed,
  formatSignedPct,
} from '@joseph/chart-trading';
import {
  VENUES,
  VENUE_NAMES,
  type CatalogInput,
  type CatalogScript,
  type JsonValue,
  type MarketType,
  type ScanCondition,
  type ScanInput,
  type ScriptVersion,
  type Timeframe,
  type Venue,
} from '@joseph/contracts';
import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {formatCompact} from '../../shell/format';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {EmptyState, InstrumentLabel, Notice} from '../trading/ui/primitives';
import {useScanJob} from '../trading/ui/scan';
import {effectiveInputs, ScriptInputs} from '../trading/ui/script-inputs';
import styles from './screener.module.css';

/** Props of the screener panel. */
export interface ScreenerPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
}

/** The built-in filter the screener starts with. */
export const LIQUIDITY_FILTER = 'builtin:liquidity-qualification';

/** Market types offered by the market selector. */
const MARKET_TYPES: ReadonlyArray<{value: MarketType | ''; label: string}> = [
  {value: '', label: 'All types'},
  {value: 'perp', label: 'Perpetuals'},
  {value: 'spot', label: 'Spot'},
  {value: 'future', label: 'Futures'},
  {value: 'stock', label: 'Stocks'},
  {value: 'etf', label: 'ETFs'},
];

/** Bar resolutions for filters that read closed bars. */
const SCAN_TIMEFRAMES: readonly Timeframe[] = ['15m', '1h', '4h', '1d', '1w'];

/** One filter condition of the screen. */
export interface ScreenCondition {
  /** Catalog id or owned Filter slug. */
  script_id: string;
  /** Owned script version; omitted for catalog filters. */
  version?: number;
  /** Input overrides. */
  inputs: Record<string, JsonValue>;
}

/** A saved screen (preset). */
export interface ScreenerPreset {
  /** Name shown in the menu. */
  name: string;
  /** Venue ('' = all venues). */
  venue: Venue | '';
  /** Market type ('' = all types). */
  marketType: MarketType | '';
  /** Resolution of bar-based filters. */
  tf: Timeframe;
  /** Filters combined with AND. */
  conditions: ScreenCondition[];
  /** Maximum instruments scanned. */
  maxSymbols: number;
}

/** Persisted screener settings (schema version 1). */
export interface ScreenerSettings {
  /** Saved presets, newest first. */
  presets: ScreenerPreset[];
}

/**
 * Reads one stored condition.
 * @param value Untrusted stored value.
 * @return Condition, or null when malformed.
 */
function conditionOf(value: unknown): ScreenCondition | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Partial<ScreenCondition>;
  if (typeof row.script_id !== 'string' || !row.script_id) return null;
  const inputs =
    row.inputs && typeof row.inputs === 'object' && !Array.isArray(row.inputs)
      ? row.inputs
      : {};
  return {
    script_id: row.script_id,
    ...(typeof row.version === 'number' ? {version: row.version} : {}),
    inputs,
  };
}

/**
 * Keeps well-formed presets and migrates the earlier qualification presets
 * (`{name, venue, status, sort}`) to a Liquidity qualification screen.
 * @param value Stored value (untrusted).
 * @return Presets; empty when `value` is not an array.
 */
export function normalizePresets(value: unknown): ScreenerPreset[] {
  if (!Array.isArray(value)) return [];
  const presets: ScreenerPreset[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue;
    // The cast only reads fields; the checks below establish the type.
    const row = raw as Record<string, unknown>;
    const name = typeof row.name === 'string' ? row.name.trim() : '';
    const venue =
      row.venue === '' || (VENUES as readonly unknown[]).includes(row.venue)
        ? (row.venue as Venue | '')
        : null;
    if (!name || venue === null) continue;
    const conditions = Array.isArray(row.conditions)
      ? row.conditions
          .map(conditionOf)
          .filter((c): c is ScreenCondition => c !== null)
      : [{script_id: LIQUIDITY_FILTER, inputs: {}}];
    if (!conditions.length) continue;
    presets.push({
      name,
      venue,
      marketType: MARKET_TYPES.some(t => t.value === row.marketType)
        ? (row.marketType as MarketType | '')
        : '',
      tf: SCAN_TIMEFRAMES.includes(row.tf as Timeframe)
        ? (row.tf as Timeframe)
        : '1d',
      conditions,
      maxSymbols:
        typeof row.maxSymbols === 'number' && row.maxSymbols >= 1
          ? Math.min(Math.floor(row.maxSymbols), 10000)
          : 200,
    });
  }
  return presets;
}

/**
 * Adds or replaces a preset by name (case-insensitive), newest first.
 * @param presets Existing presets.
 * @param preset New preset; its name is trimmed to at most 40 characters.
 * @return Updated list (at most 20).
 */
export function savePreset(
  presets: readonly ScreenerPreset[],
  preset: ScreenerPreset
): ScreenerPreset[] {
  const name = preset.name.trim().slice(0, 40);
  return [
    {...preset, name},
    ...presets.filter(p => p.name.toLowerCase() !== name.toLowerCase()),
  ].slice(0, 20);
}

/**
 * Closed bars the conditions need: a `length` input needs that many bars
 * plus one, the gap filter two; filters reading only snapshots need none.
 * @param conditions Conditions.
 * @param catalog Filter declarations by id.
 * @return History bars to request, at most 500.
 */
export function historyBarsFor(
  conditions: readonly ScreenCondition[],
  catalog: ReadonlyMap<string, CatalogScript>
): number {
  let bars = 0;
  for (const condition of conditions) {
    const entry = catalog.get(condition.script_id);
    if (!entry) {
      // Owned filters may read bars; give them a year of daily bars.
      bars = Math.max(bars, 250);
      continue;
    }
    const inputs = effectiveInputs(entry.inputs, condition.inputs);
    if (typeof inputs.length === 'number') {
      bars = Math.max(bars, inputs.length + 1);
    }
    if (entry.script_id === 'builtin:gap') bars = Math.max(bars, 2);
  }
  return Math.min(bars, 500);
}

/**
 * Builds the scan request of a screen.
 * @param preset Screen settings.
 * @param catalog Filter declarations by id.
 * @return ScanInput with every condition.
 */
export function scanRequest(
  preset: Omit<ScreenerPreset, 'name'>,
  catalog: ReadonlyMap<string, CatalogScript>
): ScanInput {
  const conditions: ScanCondition[] = preset.conditions.map(c => ({
    script_id: c.script_id,
    ...(c.version ? {version: c.version} : {}),
    inputs: c.inputs,
  }));
  return {
    ...(preset.venue ? {venue: preset.venue} : {}),
    ...(preset.marketType ? {market_type: preset.marketType} : {}),
    max_symbols: preset.maxSymbols,
    order_by: 'turnover',
    history_bars: historyBarsFor(preset.conditions, catalog),
    tf: preset.tf,
    // The generated contract spells the ten-condition bound as tuples.
    conditions: conditions.slice(0, 10) as ScanInput['conditions'],
  };
}

/** Snapshot fields shown as result columns. */
interface SnapshotRow {
  last?: string | null;
  change_24h_pct?: string | null;
  volume_24h_base?: string | null;
  turnover_24h_quote?: string | null;
  spread_pct?: string | null;
  funding?: {normalized_8h_pct?: string | null} | null;
  open_interest?: {notional_usd?: string | null} | null;
  feed_state?: string;
}

/** Props of `ConditionRow`. */
interface ConditionRowProps {
  /** Position in the screen. */
  index: number;
  /** The condition. */
  condition: ScreenCondition;
  /** Built-in and plugin filters. */
  filters: readonly CatalogScript[];
  /** Owned Filter scripts. */
  owned: readonly ScriptVersion[];
  /**
   * Replaces the condition.
   * @param next New condition.
   */
  onChange(next: ScreenCondition): void;
  /** Removes the condition. */
  onRemove(): void;
}

/**
 * Renders one condition: the filter choice and its inputs.
 * @param props Condition, choices and callbacks.
 * @return Element.
 */
function ConditionRow({
  index,
  condition,
  filters,
  owned,
  onChange,
  onRemove,
}: ConditionRowProps): ReactNode {
  const entry = filters.find(f => f.script_id === condition.script_id);
  const own = owned.find(s => s.script_id === condition.script_id);
  const ownInputs: Record<string, CatalogInput | undefined> =
    Object.fromEntries(
      Object.entries(own?.defaults ?? {}).map(([name, value]) => [
        name,
        {
          default: value,
          title: name,
          minimum: null,
          maximum: null,
          options: null,
          step: null,
          group: '',
          tooltip: '',
        },
      ])
    );
  return (
    <fieldset
      className={styles.condition}
      data-testid={`screener-condition-${index}`}
    >
      <legend className={styles.srOnly}>Filter {index + 1}</legend>
      <div className={styles.conditionHead}>
        <select
          className="j-select"
          aria-label={`Filter ${index + 1}`}
          value={own ? `${own.script_id}@${own.version}` : condition.script_id}
          onChange={e => {
            const [id = '', version] = e.target.value.split('@');
            onChange({
              script_id: id,
              ...(version ? {version: Number(version)} : {}),
              inputs: {},
            });
          }}
        >
          <optgroup label="Built-in filters">
            {filters.map(f => (
              <option
                key={f.script_id}
                value={f.script_id}
                title={f.description}
              >
                {f.title}
              </option>
            ))}
          </optgroup>
          {owned.length > 0 && (
            <optgroup label="My filter scripts">
              {owned.map(s => (
                <option key={s.script_id} value={`${s.script_id}@${s.version}`}>
                  {s.name} (v{s.version})
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <button
          type="button"
          className="j-btn j-btn--sm j-btn--ghost"
          onClick={onRemove}
          aria-label={`Remove filter ${index + 1}`}
        >
          Remove
        </button>
      </div>
      {entry && <p className={styles.hint}>{entry.description}</p>}
      <div className={styles.inputs}>
        <ScriptInputs
          idPrefix={`screener-${index}`}
          inputs={entry?.inputs ?? ownInputs}
          values={condition.inputs}
          onChange={(name, value) =>
            void onChange({
              ...condition,
              inputs: {...condition.inputs, [name]: value},
            })
          }
        />
      </div>
    </fieldset>
  );
}

/**
 * Renders the screener: market selection, AND-combined filters, saved
 * presets, scan progress and results.
 * @param props Feature context.
 * @return Panel element.
 */
export function ScreenerPanel({ctx}: ScreenerPanelProps): ReactNode {
  const selection = useStore(ctx.selection);
  const scripts = ctx.clients.scripts;
  const [filters, setFilters] = useState<CatalogScript[]>([]);
  const [owned, setOwned] = useState<ScriptVersion[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [venue, setVenue] = useState<Venue | ''>('');
  const [marketType, setMarketType] = useState<MarketType | ''>('');
  const [tf, setTf] = useState<Timeframe>('1d');
  const [maxSymbols, setMaxSymbols] = useState(200);
  const [conditions, setConditions] = useState<ScreenCondition[]>([
    {script_id: LIQUIDITY_FILTER, inputs: {}},
  ]);
  const [presets, setPresets] = useState<ScreenerPreset[]>(() =>
    normalizePresets(ctx.settings.read<ScreenerSettings>({presets: []}).presets)
  );
  const [presetName, setPresetName] = useState('');
  const scan = useScanJob(scripts);
  useEffect(() => {
    let alive = true;
    Promise.all([
      scripts.catalog('filter'),
      scripts.list().catch(() => ({scripts: [] as ScriptVersion[]})),
    ]).then(
      ([catalog, mine]) => {
        if (!alive) return;
        setFilters(catalog.scripts.filter(s => s.enabled));
        setOwned(mine.scripts.filter(s => s.kind === 'filter'));
      },
      (err: unknown) => alive && void setLoadError(errorMessage(err))
    );
    return () => {
      alive = false;
    };
  }, [scripts]);
  const byId = useMemo(
    () => new Map(filters.map(f => [f.script_id, f])),
    [filters]
  );
  const screen = {venue, marketType, tf, conditions, maxSymbols};
  const storePresets = (next: ScreenerPreset[]) => {
    setPresets(next);
    ctx.settings.write<ScreenerSettings>({presets: next});
  };
  const apply = (preset: ScreenerPreset) => {
    setVenue(preset.venue);
    setMarketType(preset.marketType);
    setTf(preset.tf);
    setConditions(preset.conditions);
    setMaxSymbols(preset.maxSymbols);
  };
  const job = scan.job;
  return (
    <section className="j-panel" aria-label="Screener">
      <header className="j-panel__header">
        <h2>Screener</h2>
        <span className={styles.grow} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
      </header>
      <div className={`j-panel__body ${styles.body}`}>
        <div className={styles.filters}>
          <label className="j-field">
            <span className="j-label">Venue</span>
            <select
              className="j-select"
              value={venue}
              onChange={e => void setVenue(e.target.value as Venue | '')}
            >
              <option value="">All venues</option>
              {VENUES.map(v => (
                <option key={v} value={v}>
                  {VENUE_NAMES[v]}
                </option>
              ))}
            </select>
          </label>
          <label className="j-field">
            <span className="j-label">Market</span>
            <select
              className="j-select"
              value={marketType}
              onChange={e =>
                void setMarketType(e.target.value as MarketType | '')
              }
            >
              {MARKET_TYPES.map(t => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="j-field" title="Resolution of bar-based filters">
            <span className="j-label">Bars</span>
            <select
              className="j-select"
              value={tf}
              onChange={e => void setTf(e.target.value as Timeframe)}
            >
              {SCAN_TIMEFRAMES.map(t => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label
            className="j-field"
            title="The most active instruments by 24h turnover are scanned first"
          >
            <span className="j-label">Max symbols</span>
            <input
              className="j-input j-num"
              type="number"
              min={1}
              max={10000}
              value={maxSymbols}
              onChange={e => {
                const value = Math.floor(Number(e.target.value));
                if (value >= 1) setMaxSymbols(Math.min(value, 10000));
              }}
            />
          </label>
        </div>
        {loadError && (
          <Notice tone="danger">
            Filters could not be loaded: {loadError}
          </Notice>
        )}
        <div className={styles.conditions} aria-label="Filters">
          {conditions.map((condition, index) => (
            <ConditionRow
              key={index}
              index={index}
              condition={condition}
              filters={filters}
              owned={owned}
              onChange={next =>
                void setConditions(list =>
                  list.map((c, i) => (i === index ? next : c))
                )
              }
              onRemove={() =>
                void setConditions(list => list.filter((_, i) => i !== index))
              }
            />
          ))}
          <div className={styles.actions}>
            <button
              type="button"
              className="j-btn j-btn--sm"
              data-testid="screener-add-filter"
              disabled={!filters.length || conditions.length >= 10}
              onClick={() =>
                void setConditions(list => [
                  ...list,
                  {script_id: filters[0]?.script_id ?? '', inputs: {}},
                ])
              }
            >
              Add filter
            </button>
            <span className={styles.grow} />
            {scan.running ? (
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--ghost"
                onClick={() => void scan.cancel()}
              >
                Cancel scan
              </button>
            ) : (
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--primary"
                data-testid="screener-run"
                disabled={!conditions.length}
                onClick={() => void scan.start(scanRequest(screen, byId))}
              >
                Scan
              </button>
            )}
          </div>
        </div>
        <div className={styles.presets}>
          <label className="j-field">
            <span className="j-label">Preset</span>
            <select
              className="j-select"
              value=""
              data-testid="screener-preset"
              onChange={e => {
                const preset = presets.find(p => p.name === e.target.value);
                if (preset) apply(preset);
              }}
            >
              <option value="">
                {presets.length ? 'Load a preset' : 'No saved presets'}
              </option>
              {presets.map(p => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <form
            className={styles.presetForm}
            onSubmit={e => {
              e.preventDefault();
              if (!presetName.trim()) return;
              storePresets(savePreset(presets, {name: presetName, ...screen}));
              setPresetName('');
            }}
          >
            <label className="sr-only" htmlFor="screener-preset-name">
              Preset name
            </label>
            <input
              id="screener-preset-name"
              className="j-input"
              placeholder="Save screen as…"
              maxLength={40}
              value={presetName}
              onChange={e => void setPresetName(e.target.value)}
            />
            <button
              type="submit"
              className="j-btn j-btn--sm"
              data-testid="screener-preset-save"
              disabled={!presetName.trim()}
            >
              Save
            </button>
            {presets.some(p => p.name === presetName.trim()) && (
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--ghost"
                onClick={() =>
                  void storePresets(
                    presets.filter(p => p.name !== presetName.trim())
                  )
                }
              >
                Delete “{presetName.trim()}”
              </button>
            )}
          </form>
        </div>
        {scan.error && (
          <Notice tone="danger">The scan failed: {scan.error}</Notice>
        )}
        {job && (
          <div className={styles.progress} data-testid="screener-progress">
            <progress
              value={job.scanned ?? 0}
              max={Math.max(job.total ?? 0, 1)}
              aria-label="Scan progress"
            />
            <span>
              {job.status} · {job.scanned ?? 0}/{job.total ?? 0} scanned of{' '}
              {job.universe_count ?? 0} · {(job.matches ?? []).length} matches ·{' '}
              {(job.unavailable ?? []).length} unavailable
              {(job.errors ?? []).length
                ? ` · ${(job.errors ?? []).length} errors`
                : ''}
            </span>
            {job.error && <Notice tone="danger">{job.error}</Notice>}
          </div>
        )}
        {!job ? (
          <EmptyState title="Build a screen">
            Choose a market, combine filters (all must match) and run the scan.
            Filters run on the market engine on real snapshots and closed bars;
            nothing is inferred in the browser.
          </EmptyState>
        ) : (
          <div className={styles.scroll}>
            <table className="j-table" aria-label="Screener results">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th className="j-num">Last</th>
                  <th className="j-num">Chg 24h</th>
                  <th className="j-num">Volume</th>
                  <th className="j-num">Turnover</th>
                  <th className="j-num">Spread</th>
                  <th className="j-num" title="Funding normalized to 8 hours">
                    Fund 8h
                  </th>
                  <th className="j-num" title="Open interest in USD">
                    OI
                  </th>
                </tr>
              </thead>
              <tbody>
                {(job.matches ?? []).map(match => {
                  const id = String(match.instrument_id);
                  // Matches carry the engine's snapshot contract.
                  const snap = (match.snapshot ?? {}) as SnapshotRow;
                  const open = () => void ctx.select({instrumentId: id});
                  return (
                    <tr
                      key={id}
                      tabIndex={0}
                      className={styles.row}
                      aria-selected={selection.instrumentId === id}
                      data-testid={`screener-row-${id}`}
                      title="Open on the chart"
                      onClick={open}
                      onKeyDown={e => e.key === 'Enter' && void open()}
                    >
                      <td>
                        <InstrumentLabel instrumentId={id} />
                      </td>
                      <td className="j-num">{formatFixed(snap.last, 4)}</td>
                      <td className="j-num">
                        {formatSignedPct(snap.change_24h_pct)}
                      </td>
                      <td className="j-num">
                        {formatCompact(snap.volume_24h_base)}
                      </td>
                      <td className="j-num">
                        {formatCompactUsd(snap.turnover_24h_quote)}
                      </td>
                      <td className="j-num">
                        {snap.spread_pct == null
                          ? '—'
                          : `${formatFixed(snap.spread_pct, 3)}%`}
                      </td>
                      <td className="j-num">
                        {formatSignedPct(snap.funding?.normalized_8h_pct, 4)}
                      </td>
                      <td className="j-num">
                        {formatCompactUsd(snap.open_interest?.notional_usd)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!scan.running && (job.matches ?? []).length === 0 && (
              <p className={styles.footnote}>
                No instrument matched every filter.
              </p>
            )}
            {[...(job.unavailable ?? []), ...(job.errors ?? [])].length > 0 && (
              <details className={styles.unavailable}>
                <summary>
                  {(job.unavailable ?? []).length} unavailable ·{' '}
                  {(job.errors ?? []).length} errors
                </summary>
                <ul className={styles.reasons}>
                  {(job.unavailable ?? []).map(row => (
                    <li key={`u-${row.instrument_id}`}>
                      <strong>{row.instrument_id}</strong>: missing data —{' '}
                      {row.error}
                    </li>
                  ))}
                  {(job.errors ?? []).map(row => (
                    <li key={`e-${row.instrument_id}`}>
                      <strong>{row.instrument_id}</strong>: filter error —{' '}
                      {row.error}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <p className={styles.footnote}>
              Filters: {(job.conditions ?? []).map(c => c.title).join(' AND ')}.
              Missing data is reported as unavailable, never as a match.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
