/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Python strategy panel: pick a built-in, plugin or owned
 * Strategy script, edit its declared inputs, backtest it on closed bars,
 * activate or pause it and review its signals. Automatic live trading is
 * visibly locked here; it can only be enabled through an execution
 * deployment with explicit consent.
 */

'use client';
import {formatFixed, formatUtcDateTime} from '@joseph/chart-trading';
import {
  TIMEFRAMES,
  type CatalogScript,
  type ScriptResult,
  type Timeframe,
} from '@joseph/contracts';
import {createStore} from '@joseph/feature-registry';
import {Lock} from 'lucide-react';
import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {pythonState} from '../../shell/python-state';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {useOptionalRuntime} from '../../shell/runtime-context';
import {EmptyState, Notice} from '../trading/ui/primitives';
import type {ActivationMode, StrategyController} from './strategy-store';
import styles from './strategy.module.css';

/** Props of the strategy panel. */
export interface StrategyPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Panel controller. */
  controller: StrategyController;
}

/** Panel tabs. */
type Tab = 'inputs' | 'backtest' | 'activate' | 'signals' | 'source';

/** Plain-language explanation of the AUTO_LIVE lock. */
export const AUTO_LIVE_LOCK_REASON =
  'Automatic live trading is locked. It needs an execution deployment for one account with limits, explicit scoped consent and live trading enabled on the server. This terminal cannot turn it on.';

/** Notification channels an activation can use besides execution. */
const CHANNELS = ['UI', 'TELEGRAM', 'LINE', 'EMAIL', 'WEBHOOK', 'WEB_PUSH'];

/** Labels of the catalog origins. */
const ORIGINS = [
  ['builtin', 'Built-in'],
  ['plugin', 'Plugins'],
  ['tenant', 'My scripts'],
] as const;

/**
 * Renders the strategy panel.
 * @param props Feature context and controller.
 * @return Panel element.
 */
export function StrategyPanel({
  ctx,
  controller,
}: StrategyPanelProps): ReactNode {
  const state = useStore(controller.state);
  const selection = useStore(ctx.selection);
  const [tab, setTab] = useState<Tab>('inputs');
  const [tf, setTf] = useState<Timeframe>(selection.timeframe);
  useEffect(() => {
    void controller.load();
  }, [controller]);
  const script = state.scripts.find(row => row.script_id === state.selectedId);
  return (
    <section className={`j-panel ${styles.panel}`} aria-label="Strategy">
      <header className="j-panel__header">
        <h2>Strategy</h2>
        <span className={styles.grow} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
      </header>
      <div className={styles.toolbar}>
        <label className="j-field">
          <span className="j-label">Strategy script</span>
          <select
            className="j-select"
            aria-label="Strategy script"
            value={state.selectedId ?? ''}
            onChange={e => void controller.select(e.target.value)}
          >
            {ORIGINS.map(([origin, label]) => {
              const rows = state.scripts.filter(row => row.origin === origin);
              return rows.length ? (
                <optgroup key={origin} label={label}>
                  {rows.map(row => (
                    <option
                      key={row.script_id}
                      value={row.script_id}
                      disabled={!row.enabled}
                    >
                      {row.title}
                      {row.version ? ` (v${row.version})` : ''}
                    </option>
                  ))}
                </optgroup>
              ) : null;
            })}
          </select>
        </label>
        {script?.description && (
          <p className={styles.description}>{script.description}</p>
        )}
        <div className={styles.row2}>
          <label className="j-field">
            <span className="j-label">Timeframe</span>
            <select
              className="j-select"
              value={tf}
              onChange={e => void setTf(e.target.value as Timeframe)}
            >
              {TIMEFRAMES.map(value => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="j-field">
            <span className="j-label">Closed bars</span>
            <input
              className="j-input j-tabular"
              type="number"
              min={50}
              max={2000}
              value={state.bars}
              onChange={e => void controller.setBars(Number(e.target.value))}
            />
          </label>
        </div>
        <span className="j-muted">{selection.instrumentId}</span>
      </div>
      <div className="j-tabs" role="tablist" aria-label="Strategy sections">
        {(
          [
            ['inputs', 'Inputs'],
            ['backtest', 'Backtest'],
            ['activate', 'Activate'],
            ['signals', 'Signals'],
            ['source', 'Source'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="j-tab"
            aria-selected={tab === id}
            onClick={() => void setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={`j-panel__body ${styles.body}`} role="tabpanel">
        {state.message && (
          <Notice tone={state.message.tone}>{state.message.text}</Notice>
        )}
        {!script && state.busy !== 'load' && (
          <EmptyState title="No strategies">
            The market engine lists its built-in and installed strategies here,
            together with your own Strategy scripts.
          </EmptyState>
        )}
        {script && tab === 'inputs' && (
          <InputsTab
            inputs={script.inputs}
            values={state.inputs}
            controller={controller}
          />
        )}
        {script && tab === 'backtest' && (
          <BacktestTab
            controller={controller}
            result={state.backtest}
            busy={state.busy === 'backtest'}
            instrumentId={selection.instrumentId}
            tf={tf}
          />
        )}
        {script && tab === 'activate' && (
          <ActivateTab
            controller={controller}
            instrumentId={selection.instrumentId}
            tf={tf}
          />
        )}
        {script && tab === 'signals' && <SignalsTab controller={controller} />}
        {script && tab === 'source' && (
          <SourceTab
            controller={controller}
            available={script.source_available}
          />
        )}
      </div>
    </section>
  );
}

/**
 * Renders one declared input.
 * @param props Name, declaration, value and change handler.
 * @return Field element.
 */
function InputField({
  name,
  spec,
  value,
  onChange,
}: {
  name: string;
  spec: NonNullable<CatalogScript['inputs'][string]>;
  value: unknown;
  onChange: (value: unknown) => void;
}): ReactNode {
  const label = spec.title || name;
  if (typeof spec.default === 'boolean') {
    return (
      <label className="j-check" title={spec.tooltip || undefined}>
        <input
          type="checkbox"
          checked={value === true}
          onChange={e => void onChange(e.target.checked)}
        />
        {label}
      </label>
    );
  }
  if (spec.options) {
    return (
      <label className="j-field">
        <span className="j-label">{label}</span>
        <select
          className="j-select"
          value={String(value)}
          onChange={e =>
            void onChange(
              spec.options?.find(option => String(option) === e.target.value)
            )
          }
        >
          {spec.options.map(option => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (typeof spec.default === 'number') {
    return (
      <label className="j-field" title={spec.tooltip || undefined}>
        <span className="j-label">{label}</span>
        <input
          className="j-input j-tabular"
          type="number"
          min={spec.minimum ?? undefined}
          max={spec.maximum ?? undefined}
          step={spec.step ?? 'any'}
          value={typeof value === 'number' ? value : ''}
          onChange={e =>
            void onChange(
              e.target.value === '' ? undefined : Number(e.target.value)
            )
          }
        />
      </label>
    );
  }
  return (
    <label className="j-field">
      <span className="j-label">{label}</span>
      <input
        className="j-input"
        value={typeof value === 'string' ? value : ''}
        onChange={e => void onChange(e.target.value)}
      />
    </label>
  );
}

/**
 * Renders the declared inputs, grouped by section.
 * @param props Declarations, values and controller.
 * @return Element.
 */
function InputsTab({
  inputs,
  values,
  controller,
}: {
  inputs: CatalogScript['inputs'];
  values: Record<string, unknown>;
  controller: StrategyController;
}): ReactNode {
  const entries = Object.entries(inputs).flatMap(([name, spec]) =>
    spec ? [{name, spec}] : []
  );
  const groups = [...new Set(entries.map(entry => entry.spec.group))];
  if (!entries.length) {
    return (
      <EmptyState title="No inputs">
        This strategy declares no inputs.
      </EmptyState>
    );
  }
  return (
    <>
      {groups.map(group => (
        <fieldset key={group || 'general'} className={styles.section}>
          <legend className={styles.sectionTitle}>{group || 'Settings'}</legend>
          {entries
            .filter(entry => entry.spec.group === group)
            .map(({name, spec}) => (
              <InputField
                key={name}
                name={name}
                spec={spec}
                value={values[name]}
                onChange={value => void controller.setInput(name, value)}
              />
            ))}
        </fieldset>
      ))}
      <div className={styles.actions}>
        <button
          type="button"
          className="j-btn j-btn--sm"
          onClick={() => void controller.resetInputs()}
        >
          Reset to defaults
        </button>
      </div>
    </>
  );
}

/**
 * Formats a number with a fixed number of decimals.
 * @param value Number, or undefined.
 * @param digits Decimals.
 * @return Text, or an em dash.
 */
function fixed(value: number | null | undefined, digits: number): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? '—'
    : formatFixed(String(value), digits);
}

/**
 * Draws the equity curve as a small SVG line.
 * @param props Result.
 * @return SVG element, or null without points.
 */
function EquityCurve({result}: {result: ScriptResult}): ReactNode {
  const values = result.equity_curve.flatMap(point =>
    point.value === null ? [] : [point.value]
  );
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  const points = values
    .map(
      (value, i) =>
        `${(i / (values.length - 1)) * 100},${40 - ((value - min) / span) * 40}`
    )
    .join(' ');
  return (
    <svg
      className={styles.equity}
      viewBox="0 -2 100 44"
      preserveAspectRatio="none"
      role="img"
      aria-label="Equity curve"
    >
      <polyline points={points} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * Renders the backtest controls and report.
 * @param props Controller, last result, busy flag and target.
 * @return Element.
 */
function BacktestTab({
  controller,
  result,
  busy,
  instrumentId,
  tf,
}: {
  controller: StrategyController;
  result: ScriptResult | null;
  busy: boolean;
  instrumentId: string;
  tf: Timeframe;
}): ReactNode {
  const runtime = useOptionalRuntime();
  const preview = runtime ? pythonState(runtime).strategyPreview : null;
  const fallback = useMemo(() => createStore<ScriptResult | null>(null), []);
  const shown = useStore(preview ?? fallback);
  const onChart = preview !== null && shown === result && result !== null;
  const metric = (label: string, value: string) => (
    <div className={styles.metric}>
      <span className="j-label">{label}</span>
      <strong className="j-tabular">{value}</strong>
    </div>
  );
  return (
    <>
      <p className="j-muted">
        Simulates {instrumentId} {tf} on closed bars: orders fill at the next
        bar&apos;s open; stops and targets close at the next open after a bar
        trades through them.
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className="j-btn j-btn--primary j-btn--sm"
          disabled={busy}
          onClick={() => void controller.runBacktest(instrumentId, tf)}
        >
          {busy ? 'Running…' : 'Run backtest'}
        </button>
        {preview && result && (
          <button
            type="button"
            className="j-btn j-btn--sm"
            aria-pressed={onChart}
            onClick={() => void preview.set(onChart ? null : result)}
          >
            {onChart ? 'Hide from chart' : 'Show on chart'}
          </button>
        )}
      </div>
      {result && (
        <div data-testid="backtest-report" className={styles.section}>
          {result.data_source === 'DEMO' && (
            <Notice tone="warning">
              Demo report: synthetic numbers generated in your browser. They say
              nothing about this strategy; connect the market engine to run it.
            </Notice>
          )}
          <div className={styles.metrics}>
            {metric('Net profit', fixed(result.metrics.net_profit, 2))}
            {metric('Return', `${fixed(result.metrics.return_pct, 2)}%`)}
            {metric(
              'Max drawdown',
              `${fixed(result.metrics.max_drawdown_pct, 2)}%`
            )}
            {metric('Trades', fixed(result.metrics.trade_count, 0))}
            {metric('Win rate', `${fixed(result.metrics.win_rate_pct, 1)}%`)}
            {metric('Fees', fixed(result.metrics.fees, 2))}
          </div>
          <EquityCurve result={result} />
          <table className="j-table" aria-label="Backtest trades">
            <thead>
              <tr>
                <th>Entry</th>
                <th>Side</th>
                <th className="j-num">Entry price</th>
                <th className="j-num">Exit price</th>
                <th className="j-num">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {(result.trades ?? []).map(trade => (
                <tr key={`${trade.entry_time_ms}-${trade.direction}`}>
                  <td>{formatUtcDateTime(trade.entry_time_ms)}</td>
                  <td>{trade.direction.toLowerCase()}</td>
                  <td className="j-num">{fixed(trade.entry_price, 2)}</td>
                  <td className="j-num">
                    {trade.exit_price === null || trade.exit_price === undefined
                      ? 'open'
                      : fixed(trade.exit_price, 2)}
                  </td>
                  <td
                    className={`j-num ${(trade.pnl ?? 0) < 0 ? 'j-neg' : 'j-pos'}`}
                  >
                    {fixed(trade.pnl, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * Renders activation controls and the activation list.
 * @param props Controller and target.
 * @return Element.
 */
function ActivateTab({
  controller,
  instrumentId,
  tf,
}: {
  controller: StrategyController;
  instrumentId: string;
  tf: Timeframe;
}): ReactNode {
  const state = useStore(controller.state);
  const [mode, setMode] = useState<ActivationMode>('ALERT_ONLY');
  const [channels, setChannels] = useState<string[]>(['UI']);
  return (
    <>
      <fieldset className={styles.section}>
        <legend className={styles.sectionTitle}>New activation</legend>
        <label className="j-field">
          <span className="j-label">Mode</span>
          <select
            className="j-select"
            aria-label="Activation mode"
            value={mode}
            onChange={e => void setMode(e.target.value as ActivationMode)}
          >
            <option value="ALERT_ONLY">Alerts only</option>
            <option value="PAPER">Paper</option>
            <option value="SHADOW">Shadow</option>
            <option value="AUTO_LIVE" disabled>
              Automatic live (locked)
            </option>
          </select>
        </label>
        <fieldset className={styles.checks}>
          <legend className="j-label">Notify</legend>
          {CHANNELS.map(channel => (
            <label key={channel} className="j-check">
              <input
                type="checkbox"
                checked={channels.includes(channel)}
                onChange={e =>
                  void setChannels(
                    e.target.checked
                      ? [...channels, channel]
                      : channels.filter(c => c !== channel)
                  )
                }
              />
              {channel.replace('_', ' ').toLowerCase()}
            </label>
          ))}
        </fieldset>
        <p className="j-muted">
          Evaluates {instrumentId} {tf} on every newly closed bar; history is
          never replayed.{' '}
          {mode === 'ALERT_ONLY'
            ? 'Entries are notifications only.'
            : state.forwardingEnabled
              ? 'Entries go to execution deployments bound to this strategy version.'
              : 'Execution forwarding is not configured on this server.'}
        </p>
        <button
          type="button"
          className="j-btn j-btn--primary j-btn--sm"
          disabled={state.busy !== null}
          onClick={() =>
            void controller.activate({instrumentId, tf, mode, channels})
          }
        >
          {state.busy === 'activate' ? 'Activating…' : 'Activate'}
        </button>
      </fieldset>
      <div className={styles.lock} data-testid="auto-live-lock">
        <Lock size={16} aria-hidden />
        <p>{AUTO_LIVE_LOCK_REASON}</p>
      </div>
      <h3 className={styles.sectionTitle}>Activations</h3>
      {state.activations.length === 0 ? (
        <EmptyState title="No activations">
          Activate a strategy to receive its signals.
        </EmptyState>
      ) : (
        <ul className={styles.list} aria-label="Activations">
          {state.activations.map(activation => (
            <li
              key={activation.activation_id}
              className={styles.row}
              data-testid={`activation-${activation.activation_id}`}
            >
              <div className={styles.rowHead}>
                <strong>{activation.name}</strong>
                <span
                  className={`j-badge ${activation.state === 'ACTIVE' ? 'j-badge--live' : ''}`}
                >
                  {activation.state.toLowerCase()}
                </span>
                <span className="j-badge">
                  {(activation.mode ?? 'ALERT_ONLY').toLowerCase()}
                </span>
              </div>
              <span className="j-muted">
                {activation.instrument_ids.join(', ')} · {activation.tf} ·{' '}
                {activation.emitted ?? 0} signals
              </span>
              <span className={styles.mono}>
                {activation.strategy_version_id}
              </span>
              {activation.last_error && (
                <Notice tone="warning">{activation.last_error}</Notice>
              )}
              {Object.entries(activation.instruments ?? {}).map(
                ([id, status]) =>
                  status?.last_error ? (
                    <Notice key={id} tone="warning">
                      {id}: {status.last_error}
                    </Notice>
                  ) : null
              )}
              <div className={styles.actions}>
                <button
                  type="button"
                  className="j-btn j-btn--sm"
                  disabled={state.busy !== null}
                  onClick={() =>
                    void controller.change(
                      activation.activation_id,
                      activation.state === 'ACTIVE' ? 'pause' : 'resume'
                    )
                  }
                >
                  {activation.state === 'ACTIVE' ? 'Pause' : 'Resume'}
                </button>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--sm"
                  disabled={state.busy !== null}
                  onClick={() =>
                    void controller.change(activation.activation_id, 'delete')
                  }
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/**
 * Lists the selected strategy's recent signals.
 * @param props Controller.
 * @return Element.
 */
function SignalsTab({controller}: {controller: StrategyController}): ReactNode {
  const {signals} = useStore(controller.state);
  if (!signals.length) {
    return (
      <EmptyState title="No signals yet">
        Signals appear when an activation sees an entry or exit on a newly
        closed bar.
      </EmptyState>
    );
  }
  return (
    <ul className={styles.list} aria-label="Strategy signals">
      {signals.map(signal => (
        <li key={signal.signal_id} className={styles.row}>
          <div className={styles.rowHead}>
            <strong>{signal.kind.replace(/_/g, ' ').toLowerCase()}</strong>
            <span className="j-badge">{signal.direction.toLowerCase()}</span>
            <span className="j-muted">
              {signal.instrument_id} · {formatUtcDateTime(signal.bar_close_ms)}
            </span>
          </div>
          {signal.message && <span>{signal.message}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Shows the source and copies it into an owned script.
 * @param props Controller and source availability.
 * @return Element.
 */
function SourceTab({
  controller,
  available,
}: {
  controller: StrategyController;
  available: boolean;
}): ReactNode {
  const state = useStore(controller.state);
  const [newId, setNewId] = useState('my-strategy');
  if (!available) {
    return (
      <Notice tone="info">
        This plugin strategy is installed by the operator and does not publish
        its source.
      </Notice>
    );
  }
  return (
    <>
      <div className={styles.actions}>
        <button
          type="button"
          className="j-btn j-btn--sm"
          disabled={state.busy !== null}
          onClick={() => void controller.loadSource()}
        >
          {state.busy === 'source' ? 'Opening…' : 'Open source'}
        </button>
      </div>
      {state.source !== null && (
        <pre className={`j-input ${styles.code}`} aria-label="Strategy source">
          {state.source}
        </pre>
      )}
      <fieldset className={styles.section}>
        <legend className={styles.sectionTitle}>Copy to my scripts</legend>
        <label className="j-field">
          <span className="j-label">New script id</span>
          <input
            className="j-input"
            value={newId}
            onChange={e => void setNewId(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="j-btn j-btn--sm"
          disabled={state.busy !== null || !newId}
          onClick={() => void controller.copy(newId)}
        >
          {state.busy === 'copy' ? 'Copying…' : 'Copy to my scripts'}
        </button>
      </fieldset>
    </>
  );
}
