'use client';
/**
 * @fileoverview Screener: backend qualification results per exact venue
 * instrument. Every gate shows observed value, threshold and outcome; a
 * missing value is shown as missing, never as a pass.
 */
import {
  formatCompactUsd,
  formatFixed,
  formatSignedPct,
  formatUtcDateTime,
} from '@joseph/chart-trading';
import {
  VENUES,
  VENUE_NAMES,
  type GateName,
  type GateResult,
  type QualificationRecord,
  type QualificationStatus,
  type Venue,
} from '@joseph/contracts';
import {Fragment, useMemo, useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {usePolling} from '../trading/ui/hooks';
import {EmptyState, InstrumentLabel, Notice} from '../trading/ui/primitives';
import styles from './screener.module.css';

/** Props of the screener panel. */
export interface ScreenerPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
}

/** Gate columns in display order. */
const GATES: readonly {gate: GateName; label: string; title: string}[] = [
  {gate: 'TURNOVER_24H', label: 'Turnover', title: '24h quote turnover'},
  {gate: 'OPEN_INTEREST_USD', label: 'OI', title: 'Open interest in USD'},
  {
    gate: 'TURNOVER_OI_RATIO',
    label: 'Vol/OI',
    title: '24h turnover divided by open interest',
  },
  {gate: 'SPREAD_PCT', label: 'Spread', title: 'Best bid/ask spread'},
  {
    gate: 'SLIPPAGE_50K_PCT',
    label: 'Slip 50K',
    title: 'Worst-side slippage for a USD 50,000 order',
  },
];

/** Status filter options. */
const STATUS_FILTERS: readonly {
  value: QualificationStatus | 'ALL';
  label: string;
}[] = [
  {value: 'ALL', label: 'All'},
  {value: 'QUALIFIED', label: 'Qualified'},
  {value: 'REJECTED', label: 'Rejected'},
  {value: 'INSUFFICIENT_DATA', label: 'Missing data'},
];

/** Sort keys. */
export type SortKey = 'rank' | 'status' | 'turnover';

/** A saved filter preset. */
export interface ScreenerPreset {
  /** Name shown in the menu. */
  name: string;
  /** Venue filter ('' = all venues). */
  venue: Venue | '';
  /** Status filter. */
  status: QualificationStatus | 'ALL';
  /** Sort key. */
  sort: SortKey;
}

/** Persisted screener settings (schema version 1). */
export interface ScreenerSettings {
  /** Saved presets, newest first. */
  presets: ScreenerPreset[];
}

/**
 * Keeps only well-formed presets.
 * @param value Stored value (untrusted).
 * @return Presets.
 */
export function normalizePresets(value: unknown): ScreenerPreset[] {
  if (!Array.isArray(value)) return [];
  const statuses = ['ALL', 'QUALIFIED', 'REJECTED', 'INSUFFICIENT_DATA'];
  const sorts = ['rank', 'status', 'turnover'];
  return value.filter(
    (p): p is ScreenerPreset =>
      !!p &&
      typeof p === 'object' &&
      typeof (p as ScreenerPreset).name === 'string' &&
      (p as ScreenerPreset).name.trim() !== '' &&
      ((p as ScreenerPreset).venue === '' ||
        (VENUES as readonly string[]).includes((p as ScreenerPreset).venue)) &&
      statuses.includes((p as ScreenerPreset).status) &&
      sorts.includes((p as ScreenerPreset).sort)
  );
}

/**
 * Adds or replaces a preset by name (case-insensitive), newest first.
 * @param presets Existing presets.
 * @param preset New preset.
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
 * Formats a gate value by unit.
 * @param gate Gate result.
 * @param value Decimal string.
 * @return Display text.
 */
export function formatGateValue(
  gate: GateResult,
  value: string | null
): string {
  if (value === null) return 'Missing';
  if (gate.unit === 'USD') return formatCompactUsd(value);
  if (gate.unit === 'pct') return `${formatFixed(value, 3)}%`;
  return formatFixed(value, 2);
}

/**
 * Describes a gate outcome in words.
 * @param gate Gate result.
 * @return Sentence such as `52.3M > 50.0M: pass`.
 */
export function describeGate(gate: GateResult): string {
  const op = gate.comparator === 'gt' ? '>' : '<';
  const outcome =
    gate.passed === null ? 'missing data' : gate.passed ? 'pass' : 'fail';
  return `${formatGateValue(gate, gate.observed)} ${op} ${formatGateValue(gate, gate.threshold)} required: ${outcome}. ${gate.reason}`;
}

/** Status badge classes. */
const STATUS_BADGE: Readonly<
  Record<QualificationStatus, {className: string; label: string}>
> = {
  QUALIFIED: {className: 'j-badge j-badge--live', label: 'Qualified'},
  REJECTED: {className: 'j-badge j-badge--stale', label: 'Rejected'},
  INSUFFICIENT_DATA: {
    className: 'j-badge j-badge--sodium',
    label: 'Missing data',
  },
};

/**
 * Renders one gate cell.
 * @param props Props with the gate.
 * @return Table cell.
 */
function GateCell({gate}: {gate: GateResult | undefined}): ReactNode {
  if (!gate) return <td className="j-num j-muted">—</td>;
  const state =
    gate.passed === null ? 'missing' : gate.passed ? 'pass' : 'fail';
  return (
    <td
      className={`j-num ${styles[state] ?? ''}`}
      title={describeGate(gate)}
      data-gate-state={state}
    >
      {formatGateValue(gate, gate.observed)}
      <span
        className={styles.mark}
        aria-label={state === 'missing' ? 'missing data' : state}
      >
        {state === 'pass' ? '✓' : state === 'fail' ? '✕' : '?'}
      </span>
    </td>
  );
}

/**
 * Renders the screener.
 * @param props Props.
 * @return Panel element.
 */
export function ScreenerPanel({ctx}: ScreenerPanelProps): ReactNode {
  const selection = useStore(ctx.selection);
  const [venue, setVenue] = useState<Venue | ''>('');
  const [status, setStatus] = useState<QualificationStatus | 'ALL'>('ALL');
  const [sort, setSort] = useState<SortKey>('rank');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [presets, setPresets] = useState<ScreenerPreset[]>(() =>
    normalizePresets(ctx.settings.read<ScreenerSettings>({presets: []}).presets)
  );
  const [presetName, setPresetName] = useState('');
  const storePresets = (next: ScreenerPreset[]) => {
    setPresets(next);
    ctx.settings.write<ScreenerSettings>({presets: next});
  };
  const activePreset =
    presets.find(
      p => p.venue === venue && p.status === status && p.sort === sort
    )?.name ?? '';
  const report = usePolling(
    () =>
      ctx.clients.market.getQualification(
        venue || undefined,
        status === 'ALL' ? undefined : status
      ),
    `${venue}|${status}`,
    60_000
  );
  const records = useMemo(() => {
    const list = [...(report.data?.records ?? [])];
    const order: Record<QualificationStatus, number> = {
      QUALIFIED: 0,
      INSUFFICIENT_DATA: 1,
      REJECTED: 2,
    };
    const turnover = (r: QualificationRecord) =>
      Number(r.gates.find(g => g.gate === 'TURNOVER_24H')?.observed ?? '-1');
    list.sort((a, b) => {
      if (sort === 'status') return order[a.status] - order[b.status];
      if (sort === 'turnover') return turnover(b) - turnover(a);
      return (
        (a.rank ?? Number.MAX_SAFE_INTEGER) -
          (b.rank ?? Number.MAX_SAFE_INTEGER) ||
        (b.heat_score ?? -1) - (a.heat_score ?? -1)
      );
    });
    return list;
  }, [report.data, sort]);

  return (
    <section className="j-panel" aria-label="Screener">
      <header className="j-panel__header">
        <h2>Screener</h2>
        <span className={styles.grow} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
        <button
          type="button"
          className="j-btn j-btn--sm j-btn--ghost"
          onClick={report.refresh}
        >
          Refresh
        </button>
      </header>
      <div className={`j-panel__body ${styles.body}`}>
        <div className={styles.filters}>
          <label className="j-field">
            <span className="j-label">Venue</span>
            <select
              className="j-select"
              value={venue}
              onChange={e => setVenue(e.target.value as Venue | '')}
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
            <span className="j-label">Sort by</span>
            <select
              className="j-select"
              value={sort}
              onChange={e => setSort(e.target.value as SortKey)}
            >
              <option value="rank">Rank</option>
              <option value="status">Status</option>
              <option value="turnover">Turnover</option>
            </select>
          </label>
        </div>
        <div className={styles.presets}>
          <label className="j-field">
            <span className="j-label">Preset</span>
            <select
              className="j-select"
              value={activePreset}
              data-testid="screener-preset"
              onChange={e => {
                const preset = presets.find(p => p.name === e.target.value);
                if (!preset) return;
                setVenue(preset.venue);
                setStatus(preset.status);
                setSort(preset.sort);
              }}
            >
              <option value="">
                {presets.length ? 'Choose a preset' : 'No saved presets'}
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
              storePresets(
                savePreset(presets, {name: presetName, venue, status, sort})
              );
              setPresetName('');
            }}
          >
            <label className="sr-only" htmlFor="screener-preset-name">
              Preset name
            </label>
            <input
              id="screener-preset-name"
              className="j-input"
              placeholder="Save filters as…"
              maxLength={40}
              value={presetName}
              onChange={e => setPresetName(e.target.value)}
            />
            <button
              type="submit"
              className="j-btn j-btn--sm"
              data-testid="screener-preset-save"
              disabled={!presetName.trim()}
            >
              Save
            </button>
            {activePreset && (
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--ghost"
                onClick={() =>
                  storePresets(presets.filter(p => p.name !== activePreset))
                }
              >
                Delete “{activePreset}”
              </button>
            )}
          </form>
        </div>
        <div className="j-segmented" role="group" aria-label="Status">
          {STATUS_FILTERS.map(option => (
            <button
              key={option.value}
              type="button"
              aria-pressed={status === option.value}
              onClick={() => setStatus(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {report.error && (
          <Notice tone="danger">
            Qualification results could not be loaded: {report.error}
          </Notice>
        )}
        {!report.loading && records.length === 0 && !report.error ? (
          <EmptyState title="No qualification results">
            The screener lists results computed by the market engine; nothing is
            inferred in the browser. Try another venue or status filter.
          </EmptyState>
        ) : (
          <div className={styles.scroll}>
            <table className="j-table" aria-label="Qualification results">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Status</th>
                  {GATES.map(g => (
                    <th key={g.gate} className="j-num" title={g.title}>
                      {g.label}
                    </th>
                  ))}
                  <th className="j-num" title="Funding normalized to 8 hours">
                    Fund 8h
                  </th>
                </tr>
              </thead>
              <tbody>
                {records.map(record => {
                  const open = expanded === record.instrument_id;
                  return (
                    <Fragment key={record.instrument_id}>
                      <tr
                        tabIndex={0}
                        aria-selected={
                          selection.instrumentId === record.instrument_id
                        }
                        aria-expanded={open}
                        className={styles.row}
                        data-testid={`screener-row-${record.instrument_id}`}
                        onClick={() => {
                          ctx.select({instrumentId: record.instrument_id});
                          setExpanded(open ? null : record.instrument_id);
                        }}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            ctx.select({instrumentId: record.instrument_id});
                            setExpanded(open ? null : record.instrument_id);
                          }
                        }}
                      >
                        <td>
                          <InstrumentLabel
                            instrumentId={record.instrument_id}
                          />
                        </td>
                        <td>
                          <span
                            className={STATUS_BADGE[record.status].className}
                          >
                            {STATUS_BADGE[record.status].label}
                          </span>
                          {record.risk_tags.map(tag => (
                            <span
                              key={tag}
                              className={`j-badge ${tag === 'HIGH_RISK' ? 'j-badge--ask' : 'j-badge--sodium'} ${styles.tag}`}
                            >
                              {tag === 'HIGH_RISK' ? 'High risk' : 'Watch'}
                            </span>
                          ))}
                        </td>
                        {GATES.map(g => (
                          <GateCell
                            key={g.gate}
                            gate={record.gates.find(x => x.gate === g.gate)}
                          />
                        ))}
                        <td
                          className={`j-num ${record.funding.risk === 'HIGH_RISK' ? 'j-neg' : ''}`}
                          title={`Funding risk ${record.funding.risk.toLowerCase().replace('_', ' ')}`}
                        >
                          {formatSignedPct(record.funding.normalized_8h_pct, 4)}
                        </td>
                      </tr>
                      {open && (
                        <tr className={styles.detail}>
                          <td colSpan={GATES.length + 3}>
                            <ul className={styles.reasons}>
                              {record.gates.map(g => (
                                <li key={g.gate}>
                                  <strong>
                                    {GATES.find(x => x.gate === g.gate)
                                      ?.title ?? g.gate}
                                    :
                                  </strong>{' '}
                                  {describeGate(g)}
                                </li>
                              ))}
                              {record.reasons.map(reason => (
                                <li key={reason}>{reason}</li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {report.data && (
          <p className={styles.footnote}>
            Policy {report.data.policy_version}, as of{' '}
            {formatUtcDateTime(report.data.as_of_ms)}. Strict thresholds: a
            value equal to a threshold fails.
          </p>
        )}
      </div>
    </section>
  );
}
