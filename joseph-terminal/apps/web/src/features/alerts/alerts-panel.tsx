/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Alerts: strategy signals newest first with filters, sorting,
 * local acknowledgment, snooze and dismiss, the explanation trace (reason
 * tree) and a jump to the exact venue, instrument, bar and AOI (area of
 * interest) on the chart.
 */

'use client';
import {formatUtcDateTime} from '@joseph/chart-trading';
import type {ReasonNode, Severity, Signal, SignalKind} from '@joseph/contracts';
import type {WritableStore} from '@joseph/feature-registry';
import {Fragment, useEffect, useMemo, useState, type ReactNode} from 'react';
import {
  BAR_VOLUME_HELP,
  SIGNAL_SORTS,
  SNOOZE_MINUTES,
  aoiDistancePct,
  type AoiZone,
  barQuoteVolumeOf,
  confluenceOf,
  isSnoozed,
  setDismissed,
  snooze,
  snoozeLabel,
  sortSignals,
  type SignalSort,
  type SignalTriage,
} from './ranking';
import {formatCompact, formatDecimal} from '../../shell/format';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {
  EmptyState,
  FeedBadge,
  InstrumentLabel,
  Notice,
  SideText,
} from '../trading/ui/primitives';
import styles from './alerts.module.css';

/** Alert feature state shared by views. */
export interface AlertsState {
  /** Signals newest first. */
  signals: readonly Signal[];
  /** Message of the last failed load, or null. */
  error: string | null;
  /** Whether the first load finished. */
  loaded: boolean;
}

/** Props of the alerts panel. */
export interface AlertsPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Signal state. */
  state: WritableStore<AlertsState>;
  /** Acknowledged signal ids. */
  acked: WritableStore<readonly string[]>;
  /** Snoozed and dismissed signals. */
  triage: WritableStore<SignalTriage>;
}

/**
 * Number of newest signals whose AOI distance is measured; each distinct
 * series and instrument costs an AOI and a snapshot request.
 */
const DISTANCE_LIMIT = 60;

/**
 * Measures the distance from the latest price (mark, else last) to each
 * signal's AOIs, again every 30 s. Prices and AOIs come from the market
 * engine; nothing is inferred.
 * @param ctx Feature context.
 * @param signals Signals (newest first).
 * @return Percent distance by signal id (null when unknown); signals past
 *     `DISTANCE_LIMIT` are absent.
 */
export function useAoiDistances(
  ctx: WebFeatureContext,
  signals: readonly Signal[]
): Readonly<Record<string, number | null>> {
  const [distances, setDistances] = useState<Record<string, number | null>>({});
  const subset = signals.slice(0, DISTANCE_LIMIT);
  const key = subset.map(s => `${s.signal_id}:${s.revision}`).join('|');
  useEffect(() => {
    if (!key) {
      setDistances({});
      return;
    }
    let alive = true;
    const load = async () => {
      const series = new Map<
        string,
        {instrumentId: string; timeframe: Signal['timeframe']}
      >();
      for (const signal of subset) {
        if (signal.aoi_ids.length > 0) {
          series.set(`${signal.instrument_id}|${signal.timeframe}`, {
            instrumentId: signal.instrument_id,
            timeframe: signal.timeframe,
          });
        }
      }
      const instruments = [
        ...new Set([...series.values()].map(s => s.instrumentId)),
      ];
      // The engine no longer publishes AOIs (indicators are joseph_script
      // classes drawing their own levels), so referenced zones stay unknown.
      const snapshotResults = await Promise.allSettled(
        instruments.map(id => ctx.clients.market.getSnapshot(id))
      );
      const aois = new Map<string, AoiZone>();
      const prices = new Map<string, number>();
      snapshotResults.forEach((result, i) => {
        const id = instruments[i];
        if (!id || result.status !== 'fulfilled') return;
        const price = Number(result.value.mark ?? result.value.last ?? NaN);
        if (Number.isFinite(price)) prices.set(id, price);
      });
      const next: Record<string, number | null> = {};
      for (const signal of subset) {
        const referenced = signal.aoi_ids
          .map(id => aois.get(id))
          .filter((aoi): aoi is AoiZone => aoi !== undefined);
        next[signal.signal_id] = aoiDistancePct(
          prices.get(signal.instrument_id) ?? null,
          referenced
        );
      }
      if (alive) setDistances(next);
    };
    void load().catch(() => undefined);
    const timer = setInterval(() => void load().catch(() => undefined), 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `subset` is a new array every render; `key` (ids and revisions) stands for its content.
  }, [ctx, key]);
  return distances;
}

/** Kind filter groups; `kinds: null` shows every kind. */
const FILTERS: ReadonlyArray<{
  id: string;
  label: string;
  kinds: readonly SignalKind[] | null;
}> = [
  {id: 'all', label: 'All', kinds: null},
  {id: 'entries', label: 'Entries', kinds: ['ENTRY_CONFIRMED']},
  {id: 'blocked', label: 'Blocked', kinds: ['ENTRY_BLOCKED']},
  {
    id: 'setup',
    label: 'Setup',
    kinds: [
      'WATCHING_AOI',
      'PRE_SIGNAL',
      'AOI_INVALIDATED',
      'SETUP_EXPIRED',
      'QUALIFICATION_CHANGED',
      'POSITION_EXITED',
      'CUSTOM_ALERT',
    ],
  },
];

/** Human labels of signal kinds. */
const KIND_LABELS: Readonly<Record<SignalKind, string>> = {
  WATCHING_AOI: 'Watching AOI',
  PRE_SIGNAL: 'Setup forming',
  ENTRY_CONFIRMED: 'Entry',
  ENTRY_BLOCKED: 'Entry blocked',
  AOI_INVALIDATED: 'AOI invalidated',
  SETUP_EXPIRED: 'Setup expired',
  QUALIFICATION_CHANGED: 'Qualification changed',
  POSITION_EXITED: 'Position exited',
  CUSTOM_ALERT: 'Python alert',
};

/** Badge class per signal kind. */
const KIND_BADGES: Readonly<Partial<Record<SignalKind, string>>> = {
  ENTRY_CONFIRMED: 'j-badge j-badge--live',
  ENTRY_BLOCKED: 'j-badge j-badge--stale',
  PRE_SIGNAL: 'j-badge j-badge--sodium',
};

/**
 * Renders a reason tree with TRUE / FALSE / UNKNOWN per node.
 * @param props Props with the root node.
 * @return Nested list.
 */
export function ReasonTree({node}: {node: ReasonNode}): ReactNode {
  const badge = {
    TRUE: 'j-badge j-badge--live',
    FALSE: 'j-badge j-badge--muted',
    UNKNOWN: 'j-badge j-badge--sodium',
  }[node.result];
  return (
    <li className={styles.reason} data-result={node.result}>
      <div className={styles.reasonLine}>
        <span className={badge}>{node.result.toLowerCase()}</span>
        <span className={styles.op}>{node.op}</span>
        <span>{node.detail}</span>
      </div>
      {node.children.length > 0 && (
        <ul className={styles.reasonChildren}>
          {node.children.map(child => (
            <ReasonTree key={child.node_path} node={child} />
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * Renders the alerts panel.
 * @param props Feature context and the feature's signal, acknowledgment and
 *     triage stores.
 * @return Panel element.
 */
export function AlertsPanel({
  ctx,
  state,
  acked,
  triage,
}: AlertsPanelProps): ReactNode {
  const {signals, error, loaded} = useStore(state);
  const ackedIds = useStore(acked);
  const triaged = useStore(triage);
  const [filter, setFilter] = useState('all');
  const [severity, setSeverity] = useState<Severity | ''>('');
  const [showAcked, setShowAcked] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [sort, setSort] = useState<SignalSort>('newest');
  const [open, setOpen] = useState<string | null>(null);
  // A coarse clock: snoozes wake within 30 s of their time.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => void setNow(Date.now()), 30_000);
    return () => void clearInterval(timer);
  }, []);
  const distances = useAoiDistances(ctx, signals);
  const hiddenCount = signals.filter(
    s =>
      triaged.dismissed.includes(s.signal_id) ||
      isSnoozed(triaged, s.signal_id, now)
  ).length;
  const visible = useMemo(() => {
    const kinds = FILTERS.find(f => f.id === filter)?.kinds ?? null;
    return sortSignals(
      signals.filter(
        s =>
          (!kinds || kinds.includes(s.kind)) &&
          (!severity || s.severity === severity) &&
          (showAcked || !ackedIds.includes(s.signal_id)) &&
          (showHidden ||
            (!triaged.dismissed.includes(s.signal_id) &&
              !isSnoozed(triaged, s.signal_id, now)))
      ),
      sort,
      distances
    );
  }, [
    signals,
    filter,
    severity,
    showAcked,
    showHidden,
    ackedIds,
    triaged,
    now,
    sort,
    distances,
  ]);

  const focus = (signal: Signal) => {
    ctx.select({
      instrumentId: signal.instrument_id,
      timeframe: signal.timeframe,
    });
    ctx.events.emit('signal.focus', {
      instrumentId: signal.instrument_id,
      timeframe: signal.timeframe,
      timeMs: signal.bar_start_ms,
      aoiIds: signal.aoi_ids,
    });
  };
  // Only the newest 500 acknowledgments are kept, so settings stay bounded.
  const acknowledge = (id: string) =>
    void acked.set([id, ...ackedIds.filter(x => x !== id)].slice(0, 500));

  return (
    <section className={styles.panel} aria-label="Alerts">
      <div className={styles.toolbar}>
        <div className="j-segmented" role="group" aria-label="Signal kind">
          {FILTERS.map(f => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => void setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className={styles.inline}>
          <span className="j-label">Severity</span>
          <select
            className="j-select"
            value={severity}
            onChange={e => void setSeverity(e.target.value as Severity | '')}
          >
            <option value="">Any</option>
            <option value="ENTRY">Entry</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
            <option value="CRITICAL">Critical</option>
          </select>
        </label>
        <label className={styles.inline}>
          <span className="j-label">Sort</span>
          <select
            className="j-select"
            value={sort}
            data-testid="alerts-sort"
            onChange={e => void setSort(e.target.value as SignalSort)}
          >
            {SIGNAL_SORTS.map(option => (
              <option
                key={option.id}
                value={option.id}
                title={option.id === 'volume' ? BAR_VOLUME_HELP : undefined}
              >
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={showAcked}
            onChange={e => void setShowAcked(e.target.checked)}
          />
          Show acknowledged
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={showHidden}
            data-testid="alerts-show-hidden"
            onChange={e => void setShowHidden(e.target.checked)}
          />
          Show snoozed and dismissed{hiddenCount > 0 ? ` (${hiddenCount})` : ''}
        </label>
        <span className={styles.grow} />
        <span className="j-muted">{visible.length} shown</span>
      </div>
      {error && (
        <Notice tone="danger">Signals could not be loaded: {error}</Notice>
      )}
      {loaded && visible.length === 0 ? (
        <EmptyState title="No signals to show">
          Signals appear here when an active strategy evaluates a closed bar.
          Activate a strategy in alerts-only or paper mode to start.
        </EmptyState>
      ) : (
        <div className={styles.scroll}>
          <table className="j-table" aria-label="Signals">
            <thead>
              <tr>
                <th>Bar close</th>
                <th>Signal</th>
                <th>Instrument</th>
                <th>Side</th>
                <th>TF</th>
                <th className="j-num">R:R</th>
                <th
                  className="j-num"
                  title="Conditions satisfied in the reason tree"
                >
                  Conf.
                </th>
                <th className="j-num" title={BAR_VOLUME_HELP}>
                  Bar vol.
                </th>
                <th
                  className="j-num"
                  title="Distance from the latest price to the signal's AOI"
                >
                  To AOI
                </th>
                <th>Strategy</th>
                <th>Data</th>
                <th>
                  <span className={styles.srOnly}>Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map(signal => {
                const expanded = open === signal.signal_id;
                const isAcked = ackedIds.includes(signal.signal_id);
                const isDismissed = triaged.dismissed.includes(
                  signal.signal_id
                );
                const snoozedUntil = isSnoozed(triaged, signal.signal_id, now)
                  ? triaged.snoozed[signal.signal_id]
                  : undefined;
                const distance = distances[signal.signal_id];
                return (
                  <Fragment key={signal.signal_id}>
                    <tr
                      className={isAcked ? 'j-row--muted' : undefined}
                      data-testid={`signal-${signal.signal_id}`}
                    >
                      <td className="j-tabular">
                        {formatUtcDateTime(signal.bar_close_ms)}
                      </td>
                      <td>
                        <button
                          type="button"
                          className={styles.expand}
                          aria-expanded={expanded}
                          onClick={() =>
                            void setOpen(expanded ? null : signal.signal_id)
                          }
                        >
                          <span
                            className={KIND_BADGES[signal.kind] ?? 'j-badge'}
                          >
                            {KIND_LABELS[signal.kind]}
                          </span>
                        </button>
                      </td>
                      <td>
                        <InstrumentLabel instrumentId={signal.instrument_id} />
                      </td>
                      <td>
                        <SideText
                          side={
                            signal.direction === 'NONE'
                              ? 'flat'
                              : signal.direction
                          }
                        />
                      </td>
                      <td>{signal.timeframe}</td>
                      <td className="j-num">{signal.rr ?? '—'}</td>
                      <td className="j-num">
                        {confluenceOf(signal.reason_tree)}
                      </td>
                      <td
                        className="j-num j-tabular"
                        data-testid="signal-volume"
                        title={
                          signal.bar_quote_volume == null
                            ? BAR_VOLUME_HELP
                            : `Quote turnover of the signal bar: ${formatDecimal(signal.bar_quote_volume, 2)} (venue-published)`
                        }
                      >
                        {barQuoteVolumeOf(signal) === null
                          ? '—'
                          : formatCompact(signal.bar_quote_volume)}
                      </td>
                      <td className="j-num" data-testid="signal-distance">
                        {distance === undefined || distance === null
                          ? '—'
                          : distance === 0
                            ? 'Inside'
                            : `${distance.toFixed(2)}%`}
                      </td>
                      <td
                        className={styles.strategy}
                        title={signal.strategy_version_id}
                      >
                        {signal.strategy_version_id}
                      </td>
                      <td>
                        <FeedBadge state={signal.data_source} />
                      </td>
                      <td className={styles.actions}>
                        <button
                          type="button"
                          className="j-btn j-btn--sm"
                          onClick={() => void focus(signal)}
                        >
                          Show on chart
                        </button>
                        {!isAcked && (
                          <button
                            type="button"
                            className="j-btn j-btn--sm j-btn--ghost"
                            onClick={() => void acknowledge(signal.signal_id)}
                          >
                            Acknowledge
                          </button>
                        )}
                        {snoozedUntil !== undefined ? (
                          <button
                            type="button"
                            className="j-btn j-btn--sm j-btn--ghost"
                            title={`Snoozed ${snoozeLabel(snoozedUntil, now)}`}
                            onClick={() =>
                              void triage.update(t => {
                                const {[signal.signal_id]: _gone, ...rest} =
                                  t.snoozed;
                                return {...t, snoozed: rest};
                              })
                            }
                          >
                            Wake
                          </button>
                        ) : (
                          <label className={styles.snooze}>
                            <span className={styles.srOnly}>Snooze signal</span>
                            <select
                              className="j-select"
                              aria-label="Snooze"
                              // Always back on the prompt: the select acts
                              // as a menu of durations.
                              value=""
                              data-testid="signal-snooze"
                              onChange={e => {
                                const minutes = Number(e.target.value);
                                if (minutes > 0) {
                                  triage.update(t =>
                                    snooze(
                                      t,
                                      signal.signal_id,
                                      minutes,
                                      Date.now()
                                    )
                                  );
                                }
                              }}
                            >
                              <option value="">Snooze</option>
                              {SNOOZE_MINUTES.map(minutes => (
                                <option key={minutes} value={minutes}>
                                  {minutes >= 60
                                    ? `${minutes / 60} h`
                                    : `${minutes} min`}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        <button
                          type="button"
                          className="j-btn j-btn--sm j-btn--ghost"
                          data-testid="signal-dismiss"
                          onClick={() =>
                            void triage.update(t =>
                              setDismissed(t, signal.signal_id, !isDismissed)
                            )
                          }
                        >
                          {isDismissed ? 'Restore' : 'Dismiss'}
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr className={styles.detail}>
                        <td colSpan={12}>
                          {signal.message && (
                            <p className={styles.message}>{signal.message}</p>
                          )}
                          {signal.entry_price && (
                            <p className={styles.message}>
                              Entry {signal.entry_price}, stop{' '}
                              {signal.stop_price ?? '—'}, target{' '}
                              {signal.target_price ?? '—'}. Risk check{' '}
                              {signal.risk_evaluation.status
                                .toLowerCase()
                                .replace('_', ' ')}
                              .
                            </p>
                          )}
                          <ul
                            className={styles.tree}
                            aria-label="Why this signal"
                          >
                            <ReasonTree node={signal.reason_tree} />
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
    </section>
  );
}
