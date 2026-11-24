/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Executions (fills), the audit log with chain verification,
 * and risk controls (kill switches with separate cancel and flatten
 * choices).
 */

'use client';
import {
  formatUtcDateTime,
  groupThousands,
  planKillSwitch,
  type KillSwitchPlan,
} from '@joseph/chart-trading';
import type {KillSwitchScope, KillSwitchState} from '@joseph/contracts';
import {useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {
  ConfirmDialog,
  EmptyState,
  InstrumentLabel,
  ModeBadge,
  Notice,
  SideText,
} from '../trading/ui/primitives';
import type {PortfolioRuntime} from './portfolio-store';
import styles from './portfolio.module.css';

/** Props shared by the activity views. */
export interface ActivityViewProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Portfolio runtime. */
  runtime: PortfolioRuntime;
}

/**
 * Renders executions (fills), newest first.
 * @param props Props.
 * @return Element.
 */
export function FillsView({runtime}: ActivityViewProps): ReactNode {
  const state = useStore(runtime.state);
  if (state.loaded && state.fills.length === 0) {
    return (
      <EmptyState title="No executions yet">
        Fills reported by the venue appear here with price, quantity and fee.
      </EmptyState>
    );
  }
  return (
    <div className={styles.view}>
      <table className="j-table" aria-label="Executions">
        <thead>
          <tr>
            <th>Time</th>
            <th>Instrument</th>
            <th>Side</th>
            <th className="j-num">Price</th>
            <th className="j-num">Qty</th>
            <th className="j-num">Fee</th>
            <th>Liquidity</th>
            <th>Mode</th>
          </tr>
        </thead>
        <tbody>
          {state.fills.map(fill => (
            <tr key={fill.fill_id}>
              <td className="j-tabular">{formatUtcDateTime(fill.ts_ms)}</td>
              <td>
                <InstrumentLabel instrumentId={fill.instrument_id} />
              </td>
              <td>
                <SideText side={fill.side} />
              </td>
              <td className="j-num">{groupThousands(fill.price)}</td>
              <td className="j-num">{groupThousands(fill.quantity_base)}</td>
              <td className="j-num">
                {fill.fee} {fill.fee_asset}
              </td>
              <td>{fill.liquidity}</td>
              <td>
                <ModeBadge mode={fill.mode} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Renders the audit log with the hash-chain verification result.
 * @param props Props.
 * @return Element.
 */
export function AuditView({runtime}: ActivityViewProps): ReactNode {
  const state = useStore(runtime.state);
  const audit = state.audit;
  return (
    <div className={styles.view}>
      <div className={styles.toolbar}>
        {audit && (
          <span
            className={`j-badge ${audit.chain_valid ? 'j-badge--live' : 'j-badge--stale'}`}
            data-testid="audit-chain"
          >
            {audit.chain_valid
              ? 'Chain verified'
              : 'Chain broken: entries were altered or removed'}
          </span>
        )}
        <span className="j-muted">
          Append-only record of submits, cancels, amendments, kill switches and
          deployments.
        </span>
      </div>
      {audit && audit.events.length === 0 ? (
        <EmptyState title="No audit events yet">
          Every trading action you take is recorded here.
        </EmptyState>
      ) : (
        <table className="j-table" aria-label="Audit log">
          <thead>
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Actor</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {audit?.events.map(event => (
              <tr key={event.event_id}>
                <td className="j-tabular">{formatUtcDateTime(event.ts_ms)}</td>
                <td>{event.action}</td>
                <td>
                  {event.entity_type}{' '}
                  <span className="j-muted">{event.entity_id}</span>
                </td>
                <td>{event.actor}</td>
                <td className="j-muted" title={event.hash}>
                  {event.hash.slice(0, 10)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/**
 * Renders kill-switch controls.
 * @param props Props.
 * @return Element.
 */
export function RiskView({ctx, runtime}: ActivityViewProps): ReactNode {
  const state = useStore(runtime.state);
  const [scope, setScope] = useState<KillSwitchScope>('GLOBAL');
  const [accountId, setAccountId] = useState<string>('');
  const [reason, setReason] = useState('');
  const [cancelOrders, setCancelOrders] = useState(false);
  const [flatten, setFlatten] = useState(false);
  const [plan, setPlan] = useState<KillSwitchPlan | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const target =
    scope === 'ACCOUNT'
      ? accountId || state.accounts[0]?.account_id || null
      : null;
  const engaged = state.killSwitches.filter(s => s.engaged);

  const prepare = (engage: boolean, existing?: KillSwitchState) => {
    const result = planKillSwitch({
      scope: existing?.scope ?? scope,
      targetId: existing ? (existing.target_id ?? null) : target,
      engage,
      cancelOpenOrders: engage && cancelOrders,
      flattenPositions: engage && flatten,
      reason: existing
        ? `Released: ${reason.trim() || existing.reason}`
        : reason,
    });
    if ('error' in result) {
      setFormError(result.error);
      return;
    }
    setFormError(null);
    setPlan(result);
  };
  const run = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      await ctx.clients.execution.setKillSwitch(plan.request);
      ctx.notifier.notify(
        plan.request.engage
          ? 'Kill switch engaged. New opening orders are blocked.'
          : 'Kill switch released.',
        plan.request.engage ? 'warning' : 'success'
      );
      setPlan(null);
      setReason('');
      void runtime.refresh();
    } catch (err) {
      setFormError(errorMessage(err));
      setPlan(null);
    } finally {
      setBusy(false);
    }
  };
  const scopeName = (
    s: KillSwitchState | {scope: KillSwitchScope; target_id: string | null}
  ) =>
    s.scope === 'GLOBAL'
      ? 'global'
      : `${s.scope.toLowerCase()} ${s.target_id ?? ''}`;

  return (
    <div className={`${styles.view} ${styles.risk}`}>
      <section aria-label="Engaged kill switches">
        {engaged.length === 0 ? (
          <p className="j-muted">
            No kill switch is engaged. Strategies and manual orders follow their
            normal risk limits.
          </p>
        ) : (
          <ul className={styles.switches}>
            {engaged.map(s => (
              <li
                key={`${s.scope}:${s.target_id ?? ''}`}
                data-testid="engaged-switch"
              >
                <span className="j-badge j-badge--ask">Engaged</span>{' '}
                {scopeName(s)}: {s.reason}
                <span className="j-muted">
                  {' '}
                  by {s.actor} at {formatUtcDateTime(s.changed_at_ms)}
                </span>
                <button
                  type="button"
                  className="j-btn j-btn--sm"
                  onClick={() => void prepare(false, s)}
                >
                  Release {scopeName(s)} kill switch
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <fieldset className={styles.form}>
        <legend>Engage a kill switch</legend>
        <div className="j-segmented" role="group" aria-label="Scope">
          <button
            type="button"
            aria-pressed={scope === 'GLOBAL'}
            onClick={() => void setScope('GLOBAL')}
          >
            Everything
          </button>
          <button
            type="button"
            aria-pressed={scope === 'ACCOUNT'}
            onClick={() => void setScope('ACCOUNT')}
          >
            One account
          </button>
        </div>
        {scope === 'ACCOUNT' && (
          <label className="j-field">
            <span className="j-label">Account</span>
            <select
              className="j-select"
              value={target ?? ''}
              onChange={e => void setAccountId(e.target.value)}
            >
              {state.accounts.map(a => (
                <option key={a.account_id} value={a.account_id}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="j-field">
          <span className="j-label">Reason (recorded in the audit log)</span>
          <input
            className="j-input"
            value={reason}
            onChange={e => void setReason(e.target.value)}
          />
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={cancelOrders}
            onChange={e => void setCancelOrders(e.target.checked)}
          />
          Also cancel open orders
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={flatten}
            onChange={e => void setFlatten(e.target.checked)}
          />
          Also close positions with reduce-only market orders
        </label>
        <p className="j-muted">
          Engaging always blocks new opening orders. Canceling and closing only
          happen if you tick them.
        </p>
        {formError && <Notice tone="danger">{formError}</Notice>}
        <button
          type="button"
          className="j-btn j-btn--ask"
          onClick={() => void prepare(true)}
        >
          Review kill switch
        </button>
      </fieldset>
      {plan && (
        <ConfirmDialog
          title={
            plan.request.engage ? 'Engage kill switch?' : 'Release kill switch?'
          }
          confirmLabel={`${plan.request.engage ? 'Engage' : 'Release'} ${scopeName({scope: plan.request.scope, target_id: plan.request.target_id ?? null})} kill switch`}
          tone={plan.request.engage ? 'ask' : 'primary'}
          busy={busy}
          onCancel={() => void setPlan(null)}
          onConfirm={() => void run()}
        >
          <ul data-testid="kill-switch-consequences">
            {plan.consequences.map(c => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
    </div>
  );
}
