/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Alert manager: create, edit, enable and delete alert jobs.
 * A condition is a built-in alert script (price crossing, channel, moving
 * average cross, RSI, volume spike, percent change, breakout) or an owned
 * alert, indicator or strategy script, evaluated by the market engine on
 * every newly closed bar of one symbol and timeframe. Matches go to the
 * alert log and the selected notification destinations, never to trading.
 */

'use client';
import {formatUtcDateTime} from '@joseph/chart-trading';
import type {
  AlertJob,
  AlertJobInput,
  AlertSubscription,
  CatalogInput,
  CatalogScript,
  JsonValue,
  ScriptVersion,
  Timeframe,
} from '@joseph/contracts';
import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {EmptyState, InstrumentLabel, Notice} from '../trading/ui/primitives';
import {ScriptInputs} from '../trading/ui/script-inputs';
import styles from './alerts.module.css';

/** Timeframes offered for alert conditions. */
const ALERT_TIMEFRAMES: readonly Timeframe[] = [
  '1m',
  '5m',
  '15m',
  '1h',
  '4h',
  '1d',
];

/** A condition the dialog can choose. */
export interface AlertCondition {
  /** Catalog id or owned slug. */
  scriptId: string;
  /** 0 for catalog scripts, else the pinned owned version. */
  version: number;
  /** Display title. */
  title: string;
  /** Description for the choice list. */
  description: string;
  /** Declared inputs (owned scripts: their default values). */
  inputs: Record<string, CatalogInput | undefined>;
}

/**
 * Describes a catalog alert as a dialog choice.
 * @param entry Catalog script.
 * @return Condition choice.
 */
export function catalogCondition(entry: CatalogScript): AlertCondition {
  return {
    scriptId: entry.script_id,
    version: 0,
    title: entry.title,
    description: entry.description,
    inputs: entry.inputs,
  };
}

/**
 * Describes an owned script as a dialog choice; its defaults become
 * free-form inputs.
 * @param script Owned alert, indicator or strategy version.
 * @return Condition choice.
 */
export function ownedCondition(script: ScriptVersion): AlertCondition {
  return {
    scriptId: script.script_id,
    version: script.version,
    title: `${script.name} (v${script.version})`,
    description: `Your ${script.kind} script`,
    inputs: Object.fromEntries(
      Object.entries(script.defaults ?? {}).map(([name, value]) => [
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
    ),
  };
}

/** Props of the alert dialog. */
interface AlertDialogProps {
  /** Choices of conditions. */
  conditions: readonly AlertCondition[];
  /** External notification destinations. */
  destinations: readonly AlertSubscription[];
  /** Job being edited, or null for a new alert. */
  editing: AlertJob | null;
  /** Default symbol. */
  instrumentId: string;
  /** Default timeframe. */
  timeframe: Timeframe;
  /**
   * Saves the alert.
   * @param body Alert job request.
   * @return Resolves after saving; rejects with the server's reason.
   */
  onSave(body: AlertJobInput): Promise<void>;
  /** Closes the dialog. */
  onClose(): void;
}

/**
 * Renders the create/edit alert dialog.
 * @param props Choices, defaults and callbacks.
 * @return Dialog element.
 */
function AlertDialog({
  conditions,
  destinations,
  editing,
  instrumentId,
  timeframe,
  onSave,
  onClose,
}: AlertDialogProps): ReactNode {
  const first = conditions[0];
  const [key, setKey] = useState(
    editing
      ? `${editing.script_id}@${editing.version}`
      : first
        ? `${first.scriptId}@${first.version}`
        : ''
  );
  const [symbol, setSymbol] = useState(editing?.instrument_id ?? instrumentId);
  const [tf, setTf] = useState<Timeframe>(editing?.tf ?? timeframe);
  const [inputs, setInputs] = useState<Record<string, JsonValue>>(
    editing?.inputs ?? {}
  );
  const [targets, setTargets] = useState<string[]>(
    editing?.subscription_ids ?? []
  );
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [history, setHistory] = useState(editing?.history_bars ?? 300);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const symbolRef = useRef<HTMLInputElement>(null);
  useEffect(() => symbolRef.current?.focus(), []);
  const condition = conditions.find(c => `${c.scriptId}@${c.version}` === key);
  const builtins = conditions.filter(c => c.version === 0);
  const owned = conditions.filter(c => c.version > 0);
  const submit = async () => {
    if (!condition) return;
    setBusy(true);
    setFailure(null);
    try {
      await onSave({
        script_id: condition.scriptId,
        version: condition.version,
        instrument_id: symbol.trim(),
        tf,
        inputs,
        subscription_ids: targets,
        enabled,
        history_bars: history,
      });
      onClose();
    } catch (err) {
      setFailure(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className={styles.backdrop}
      role="presentation"
      onKeyDown={e => e.key === 'Escape' && void onClose()}
    >
      <form
        className={`j-dialog ${styles.dialog}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-dialog-title"
        onSubmit={e => {
          e.preventDefault();
          void submit();
        }}
      >
        <h2 id="alert-dialog-title" className={styles.dialogTitle}>
          {editing ? 'Edit alert' : 'Create alert'}
        </h2>
        <label className="j-field">
          <span className="j-label">Condition</span>
          <select
            className="j-select"
            data-testid="alert-condition"
            value={key}
            onChange={e => {
              setKey(e.target.value);
              setInputs({});
            }}
          >
            <optgroup label="Built-in alerts">
              {builtins.map(c => (
                <option
                  key={c.scriptId}
                  value={`${c.scriptId}@0`}
                  title={c.description}
                >
                  {c.title}
                </option>
              ))}
            </optgroup>
            {owned.length > 0 && (
              <optgroup label="My scripts">
                {owned.map(c => (
                  <option key={c.scriptId} value={`${c.scriptId}@${c.version}`}>
                    {c.title}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {condition && <p className={styles.hint}>{condition.description}</p>}
        <div className={styles.dialogGrid}>
          <label className="j-field">
            <span className="j-label">Symbol</span>
            <input
              ref={symbolRef}
              className="j-input"
              data-testid="alert-symbol"
              value={symbol}
              onChange={e => void setSymbol(e.target.value)}
              placeholder="venue:type:SYMBOL"
            />
          </label>
          <label className="j-field">
            <span className="j-label">Timeframe</span>
            <select
              className="j-select"
              value={tf}
              onChange={e => void setTf(e.target.value as Timeframe)}
            >
              {ALERT_TIMEFRAMES.map(t => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          {condition && (
            <ScriptInputs
              idPrefix="alert-input"
              inputs={condition.inputs}
              values={inputs}
              onChange={(name, value) =>
                void setInputs(current => ({...current, [name]: value}))
              }
            />
          )}
          <label className="j-field" title="Closed bars of context per check">
            <span className="j-label">History bars</span>
            <input
              className="j-input j-num"
              type="number"
              min={2}
              max={2000}
              value={history}
              onChange={e => {
                const value = Math.floor(Number(e.target.value));
                if (value >= 2) setHistory(Math.min(value, 2000));
              }}
            />
          </label>
        </div>
        <fieldset className={styles.destinations}>
          <legend className="j-label">Notify</legend>
          <p className={styles.hint}>
            Every match is listed in the alert log. External destinations are
            the notification subscriptions you set up under Notifications.
          </p>
          {destinations.map(sub => (
            <label key={sub.subscription_id} className="j-check">
              <input
                type="checkbox"
                checked={targets.includes(sub.subscription_id)}
                onChange={e =>
                  void setTargets(list =>
                    e.target.checked
                      ? [...list, sub.subscription_id]
                      : list.filter(id => id !== sub.subscription_id)
                  )
                }
              />
              {sub.channel.toLowerCase().replace('_', ' ')} · {sub.target}
            </label>
          ))}
        </fieldset>
        <label className="j-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={e => void setEnabled(e.target.checked)}
          />
          Enabled (evaluates only bars that close from now on)
        </label>
        {failure && <Notice tone="danger">{failure}</Notice>}
        <div className="j-dialog__actions">
          <button
            type="button"
            className="j-btn j-btn--ghost"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="j-btn j-btn--primary"
            data-testid="alert-save"
            disabled={busy || !condition || !symbol.trim()}
            aria-busy={busy}
          >
            {busy ? 'Saving…' : editing ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}

/** Props of the alert manager. */
export interface AlertManagerProps {
  /** Feature context. */
  ctx: WebFeatureContext;
}

/**
 * Renders the alert jobs with create, edit, enable/disable and delete.
 * @param props Feature context.
 * @return Panel element.
 */
export function AlertManager({ctx}: AlertManagerProps): ReactNode {
  const selection = useStore(ctx.selection);
  const scripts = ctx.clients.scripts;
  const [jobs, setJobs] = useState<AlertJob[] | null>(null);
  const [conditions, setConditions] = useState<AlertCondition[]>([]);
  const [destinations, setDestinations] = useState<AlertSubscription[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<AlertJob | 'new' | null>(null);
  const reload = useCallback(async () => {
    try {
      setJobs(await scripts.alerts());
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [scripts]);
  useEffect(() => {
    let alive = true;
    void reload();
    Promise.all([
      scripts.catalog('alert'),
      scripts.list().catch(() => ({scripts: [] as ScriptVersion[]})),
      ctx.clients.strategy
        .listAlertSubscriptions()
        .catch(() => ({subscriptions: [] as AlertSubscription[]})),
    ]).then(
      ([catalog, mine, subs]) => {
        if (!alive) return;
        setConditions([
          ...catalog.scripts.filter(s => s.enabled).map(catalogCondition),
          ...mine.scripts
            .filter(s => ['alert', 'indicator', 'strategy'].includes(s.kind))
            .map(ownedCondition),
        ]);
        setDestinations(subs.subscriptions);
      },
      (err: unknown) => alive && void setError(errorMessage(err))
    );
    const timer = setInterval(() => void reload(), 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [ctx, scripts, reload]);
  const save = async (body: AlertJobInput, id?: string) => {
    await scripts.saveAlert(body, id);
    await reload();
  };
  const toggle = async (job: AlertJob) => {
    try {
      await save(
        {
          script_id: job.script_id,
          version: job.version,
          instrument_id: job.instrument_id,
          tf: job.tf,
          inputs: job.inputs ?? {},
          subscription_ids: job.subscription_ids ?? [],
          enabled: !job.enabled,
          history_bars: job.history_bars ?? 500,
        },
        job.job_id
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const remove = async (job: AlertJob) => {
    try {
      await scripts.deleteAlert(job.job_id);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <section className={styles.panel} aria-label="Alert manager">
      <div className={styles.toolbar}>
        <button
          type="button"
          className="j-btn j-btn--sm j-btn--primary"
          data-testid="alert-create"
          disabled={!conditions.length}
          onClick={() => void setDialog('new')}
        >
          Create alert
        </button>
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
        <span className={styles.grow} />
        <span className="j-muted">{jobs?.length ?? 0} alerts</span>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      {jobs !== null && jobs.length === 0 ? (
        <EmptyState title="No alerts yet">
          Create an alert to be notified when a condition is met on a newly
          closed bar: price crossings, channels, moving-average crosses, RSI
          levels, volume spikes, percent moves, breakouts or your own scripts.
        </EmptyState>
      ) : (
        <div className={styles.scroll}>
          <table className="j-table" aria-label="Alert jobs">
            <thead>
              <tr>
                <th>Condition</th>
                <th>Instrument</th>
                <th>TF</th>
                <th>Status</th>
                <th className="j-num">Fired</th>
                <th>Last check</th>
                <th>
                  <span className={styles.srOnly}>Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {(jobs ?? []).map(job => (
                <tr key={job.job_id} data-testid={`alert-job-${job.job_id}`}>
                  <td title={job.script_id}>{job.title || job.script_id}</td>
                  <td>
                    <InstrumentLabel instrumentId={job.instrument_id} />
                  </td>
                  <td>{job.tf}</td>
                  <td>
                    <span
                      className={
                        job.last_error
                          ? 'j-badge j-badge--stale'
                          : job.enabled
                            ? 'j-badge j-badge--live'
                            : 'j-badge'
                      }
                      title={job.last_error ?? undefined}
                    >
                      {job.last_error
                        ? 'Error'
                        : job.enabled
                          ? 'Active'
                          : 'Paused'}
                    </span>
                    {job.last_error && (
                      <span className={styles.error}> {job.last_error}</span>
                    )}
                  </td>
                  <td className="j-num">{job.emitted ?? 0}</td>
                  <td className="j-tabular">
                    {job.last_checked_ms
                      ? formatUtcDateTime(job.last_checked_ms)
                      : '—'}
                  </td>
                  <td className={styles.actions}>
                    <button
                      type="button"
                      className="j-btn j-btn--sm"
                      onClick={() => void toggle(job)}
                    >
                      {job.enabled ? 'Pause' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      className="j-btn j-btn--sm j-btn--ghost"
                      onClick={() => void setDialog(job)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="j-btn j-btn--sm j-btn--ghost"
                      onClick={() => void remove(job)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog !== null && (
        <AlertDialog
          conditions={conditions}
          destinations={destinations}
          editing={dialog === 'new' ? null : dialog}
          instrumentId={selection.instrumentId ?? 'mock:perp:BTCUSD'}
          timeframe={selection.timeframe ?? '5m'}
          onSave={body =>
            save(body, dialog === 'new' ? undefined : dialog.job_id)
          }
          onClose={() => void setDialog(null)}
        />
      )}
    </section>
  );
}
