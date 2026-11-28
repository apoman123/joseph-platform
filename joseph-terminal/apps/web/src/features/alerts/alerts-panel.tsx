/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Alerts: strategy entries and exits and alert-job events
 * (the alert log) newest first with filters, sorting, local
 * acknowledgment, snooze and dismiss, the signal's levels and message, and
 * a jump to the exact venue, instrument and bar on the chart.
 */

'use client';
import {formatUtcDateTime} from '@joseph/chart-trading';
import type {Severity, Signal, SignalKind} from '@joseph/contracts';
import type {WritableStore} from '@joseph/feature-registry';
import {Fragment, useEffect, useMemo, useState, type ReactNode} from 'react';
import {
  SIGNAL_SORTS,
  SNOOZE_MINUTES,
  isSnoozed,
  setDismissed,
  snooze,
  snoozeLabel,
  sortSignals,
  type SignalSort,
  type SignalTriage,
} from './ranking';
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

/** Kind filter groups; `kinds: null` shows every kind. */
const FILTERS: ReadonlyArray<{
  id: string;
  label: string;
  kinds: readonly SignalKind[] | null;
}> = [
  {id: 'all', label: 'All', kinds: null},
  {id: 'entries', label: 'Entries', kinds: ['ENTRY_CONFIRMED']},
  {id: 'exits', label: 'Exits', kinds: ['POSITION_EXITED']},
  {id: 'alerts', label: 'Alert log', kinds: ['CUSTOM_ALERT']},
];

/** Human labels of signal kinds. */
const KIND_LABELS: Readonly<Record<SignalKind, string>> = {
  ENTRY_CONFIRMED: 'Entry',
  POSITION_EXITED: 'Position exited',
  CUSTOM_ALERT: 'Alert',
};

/** Badge class per signal kind. */
const KIND_BADGES: Readonly<Partial<Record<SignalKind, string>>> = {
  ENTRY_CONFIRMED: 'j-badge j-badge--live',
  POSITION_EXITED: 'j-badge j-badge--muted',
};

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
      sort
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
              <option key={option.id} value={option.id}>
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
          Signals appear here when an active strategy or an enabled alert
          evaluates a closed bar. Create an alert in the Alert manager, or
          activate a strategy in alerts-only or paper mode.
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
                        <td colSpan={9}>
                          <div role="group" aria-label="Signal details">
                            <dl className={styles.facts}>
                              {signal.message && (
                                <>
                                  <dt>Message</dt>
                                  <dd>{signal.message}</dd>
                                </>
                              )}
                              {signal.condition && (
                                <>
                                  <dt>Condition</dt>
                                  <dd>{signal.condition}</dd>
                                </>
                              )}
                              {signal.entry_price && (
                                <>
                                  <dt>Levels</dt>
                                  <dd>
                                    Entry {signal.entry_price}, stop{' '}
                                    {signal.stop_price ?? '—'}, target{' '}
                                    {signal.target_price ?? '—'}
                                  </dd>
                                </>
                              )}
                              <dt>Mode</dt>
                              <dd>
                                {signal.mode.toLowerCase().replace('_', ' ')}
                              </dd>
                              <dt>Severity</dt>
                              <dd>{signal.severity.toLowerCase()}</dd>
                            </dl>
                          </div>
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
