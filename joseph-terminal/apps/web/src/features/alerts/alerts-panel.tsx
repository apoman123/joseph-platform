'use client';
/**
 * @fileoverview Alerts: strategy signals newest first with filters, local
 * acknowledgment, the explanation trace (reason tree) and a jump to the
 * exact venue, instrument, bar and AOI on the chart.
 */
import {formatUtcDateTime} from '@joseph/chart-trading';
import type {ReasonNode, Severity, Signal, SignalKind} from '@joseph/contracts';
import type {WritableStore} from '@joseph/feature-registry';
import {Fragment, useMemo, useState, type ReactNode} from 'react';
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
  /** Load error. */
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
}

/** Kind filter groups. */
const FILTERS: readonly {
  id: string;
  label: string;
  kinds: readonly SignalKind[] | null;
}[] = [
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
 * @param props Props.
 * @return Panel element.
 */
export function AlertsPanel({ctx, state, acked}: AlertsPanelProps): ReactNode {
  const {signals, error, loaded} = useStore(state);
  const ackedIds = useStore(acked);
  const [filter, setFilter] = useState('all');
  const [severity, setSeverity] = useState<Severity | ''>('');
  const [showAcked, setShowAcked] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const visible = useMemo(() => {
    const kinds = FILTERS.find(f => f.id === filter)?.kinds ?? null;
    return signals.filter(
      s =>
        (!kinds || kinds.includes(s.kind)) &&
        (!severity || s.severity === severity) &&
        (showAcked || !ackedIds.includes(s.signal_id))
    );
  }, [signals, filter, severity, showAcked, ackedIds]);

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
  const acknowledge = (id: string) =>
    acked.set([id, ...ackedIds.filter(x => x !== id)].slice(0, 500));

  return (
    <section className={styles.panel} aria-label="Alerts">
      <div className={styles.toolbar}>
        <div className="j-segmented" role="group" aria-label="Signal kind">
          {FILTERS.map(f => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
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
            onChange={e => setSeverity(e.target.value as Severity | '')}
          >
            <option value="">Any</option>
            <option value="ENTRY">Entry</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
            <option value="CRITICAL">Critical</option>
          </select>
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={showAcked}
            onChange={e => setShowAcked(e.target.checked)}
          />
          Show acknowledged
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
                            setOpen(expanded ? null : signal.signal_id)
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
                        <SideText side={signal.direction} />
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
                          onClick={() => focus(signal)}
                        >
                          Show on chart
                        </button>
                        {!isAcked && (
                          <button
                            type="button"
                            className="j-btn j-btn--sm j-btn--ghost"
                            onClick={() => acknowledge(signal.signal_id)}
                          >
                            Acknowledge
                          </button>
                        )}
                      </td>
                    </tr>
                    {expanded && (
                      <tr className={styles.detail}>
                        <td colSpan={9}>
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
