'use client';
/**
 * @fileoverview Strategy builder panel: settings, rule tree, risk and
 * notifications, JSON import/export, backtests and versions with
 * activation. Automatic live trading is visibly locked here; it can only be
 * enabled through an execution deployment with explicit consent.
 */
import {formatFixed, formatUtcDateTime} from '@joseph/chart-trading';
import {
  VENUES,
  VENUE_NAMES,
  type ActivateRequest,
  type BacktestReport,
  type StrategyDefinition,
  type StrategyVersion,
  type Timeframe,
  type ValidationIssue,
  type Venue,
} from '@joseph/contracts';
import {
  STRATEGY_TEMPLATES,
  TIMEFRAME_ORDER,
  describeTree,
  diffDefinitions,
  exportDefinition,
  importDefinition,
  nodeIdAtPointer,
  type MigrationReport,
} from '@joseph/strategy-builder';
import {Lock} from 'lucide-react';
import {useMemo, useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import {ConfirmDialog, EmptyState, Notice} from '../trading/ui/primitives';
import {RuleEditor} from './rule-editor';
import type {StrategyEditor} from './strategy-store';
import styles from './strategy.module.css';

/** Props of the strategy panel. */
export interface StrategyPanelProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Editor. */
  editor: StrategyEditor;
}

/** Panel tabs. */
type Tab = 'rules' | 'json' | 'backtest' | 'versions';

/** Plain-language explanation of the AUTO_LIVE lock. */
export const AUTO_LIVE_LOCK_REASON =
  'Automatic live trading is locked. It needs an execution deployment for one account with limits, explicit scoped consent and live trading enabled on the server. This terminal cannot turn it on.';

/**
 * Groups issues by the node they belong to.
 * @param definition Definition.
 * @param issues Issues with JSON pointers.
 * @return Map from node id to issues, plus issues outside the rule tree.
 */
export function issuesByNode(
  definition: StrategyDefinition,
  issues: readonly ValidationIssue[]
): {byNode: Map<string, ValidationIssue[]>; general: ValidationIssue[]} {
  const byNode = new Map<string, ValidationIssue[]>();
  const general: ValidationIssue[] = [];
  for (const issue of issues) {
    const id = nodeIdAtPointer(definition, issue.path);
    if (id) byNode.set(id, [...(byNode.get(id) ?? []), issue]);
    else general.push(issue);
  }
  return {byNode, general};
}

/**
 * Renders the strategy panel.
 * @param props Props.
 * @return Panel element.
 */
export function StrategyPanel({ctx, editor}: StrategyPanelProps): ReactNode {
  const state = useStore(editor.state);
  const [tab, setTab] = useState<Tab>('rules');
  const [editError, setEditError] = useState<string | null>(null);
  const def = state.definition;
  const latest = state.versions[0] ?? null;
  const allIssues = useMemo(
    () => [
      ...state.validation.errors,
      ...state.validation.warnings,
      ...state.serverIssues,
    ],
    [state.validation, state.serverIssues]
  );
  const {byNode, general} = useMemo(
    () => issuesByNode(def, allIssues),
    [def, allIssues]
  );
  const update = (patch: Partial<StrategyDefinition>) =>
    editor.setDefinition({...def, ...patch});

  return (
    <section
      className={`j-panel ${styles.panel}`}
      aria-label="Strategy builder"
    >
      <header className="j-panel__header">
        <h2>Strategy</h2>
        <span className={styles.version} data-testid="strategy-version">
          {latest
            ? `Version ${latest.version}${state.dirty ? ', edited' : ''}`
            : 'Not saved'}
        </span>
        {latest && (
          <span
            className={`j-badge ${latest.state === 'ACTIVE' ? 'j-badge--live' : ''}`}
          >
            {latest.state.toLowerCase()}
          </span>
        )}
        <span className={styles.grow} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
      </header>
      <div className={styles.toolbar}>
        <label className={styles.template}>
          <span className={styles.srOnly}>Start from a template</span>
          <select
            className="j-select"
            value=""
            aria-label="Start from a template"
            onChange={e =>
              e.target.value && editor.applyTemplate(e.target.value)
            }
          >
            <option value="">Start from a template</option>
            {STRATEGY_TEMPLATES.map(t => (
              <option key={t.id} value={t.id} title={t.summary}>
                {t.title}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="j-btn j-btn--sm"
          disabled={state.busy !== null}
          onClick={() => void editor.validateOnServer()}
        >
          {state.busy === 'validate' ? 'Validating…' : 'Validate'}
        </button>
        <button
          type="button"
          className="j-btn j-btn--sm j-btn--primary"
          disabled={state.busy !== null || !state.dirty}
          onClick={() => void editor.save()}
        >
          {state.busy === 'save'
            ? 'Saving…'
            : latest
              ? 'Save new version'
              : 'Save strategy'}
        </button>
      </div>
      <div className="j-tabs" role="tablist" aria-label="Strategy sections">
        {(
          [
            ['rules', 'Rules'],
            ['json', 'JSON'],
            ['backtest', 'Backtest'],
            ['versions', 'Versions'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="j-tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={`j-panel__body ${styles.body}`} role="tabpanel">
        {state.message && (
          <Notice tone={state.message.tone}>{state.message.text}</Notice>
        )}
        {editError && (
          <Notice tone="warning">
            {editError}{' '}
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--sm"
              onClick={() => setEditError(null)}
            >
              Dismiss
            </button>
          </Notice>
        )}
        {tab === 'rules' && (
          <>
            <fieldset className={styles.settings}>
              <legend className={styles.sectionTitle}>Setup</legend>
              <label className="j-field">
                <span className="j-label">Name</span>
                <input
                  className="j-input"
                  value={def.name}
                  onChange={e => update({name: e.target.value})}
                />
              </label>
              <label className="j-field">
                <span className="j-label">Strategy id</span>
                <input
                  className="j-input"
                  value={def.strategy_id}
                  disabled={state.versions.length > 0}
                  onChange={e => update({strategy_id: e.target.value})}
                />
              </label>
              <div className={styles.row2}>
                <div className="j-field">
                  <span className="j-label" id="direction-label">
                    Direction
                  </span>
                  <div
                    className="j-segmented"
                    role="group"
                    aria-labelledby="direction-label"
                  >
                    <button
                      type="button"
                      aria-pressed={def.direction === 'LONG'}
                      onClick={() => update({direction: 'LONG'})}
                    >
                      Long
                    </button>
                    <button
                      type="button"
                      aria-pressed={def.direction === 'SHORT'}
                      onClick={() => update({direction: 'SHORT'})}
                    >
                      Short
                    </button>
                  </div>
                </div>
                <label className="j-field">
                  <span className="j-label">Signal timeframe</span>
                  <select
                    className="j-select"
                    value={def.signal_timeframe}
                    onChange={e =>
                      update({signal_timeframe: e.target.value as Timeframe})
                    }
                  >
                    {TIMEFRAME_ORDER.map(tf => (
                      <option key={tf} value={tf}>
                        {tf}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <fieldset className={styles.checks}>
                <legend className="j-label">Venues</legend>
                {VENUES.map(v => (
                  <label key={v} className="j-check">
                    <input
                      type="checkbox"
                      checked={def.universe.venues.includes(v)}
                      onChange={e => {
                        const venues = e.target.checked
                          ? [...def.universe.venues, v]
                          : def.universe.venues.filter(x => x !== v);
                        update({
                          universe: {
                            ...def.universe,
                            venues: venues as [Venue, ...Venue[]],
                          },
                        });
                      }}
                    />
                    {VENUE_NAMES[v]}
                  </label>
                ))}
              </fieldset>
              <div className={styles.row2}>
                <label className="j-field">
                  <span className="j-label">Universe</span>
                  <select
                    className="j-select"
                    value={def.universe.qualification}
                    onChange={e =>
                      update({
                        universe: {
                          ...def.universe,
                          qualification: e.target.value as
                            'QUALIFIED_ONLY' | 'ANY',
                        },
                      })
                    }
                  >
                    <option value="QUALIFIED_ONLY">
                      Qualified perpetuals only
                    </option>
                    <option value="ANY">Any instrument</option>
                  </select>
                </label>
                <label className="j-field">
                  <span className="j-label">Requested mode</span>
                  <select
                    className="j-select"
                    value={def.execution_mode}
                    onChange={e =>
                      update({
                        execution_mode: e.target
                          .value as StrategyDefinition['execution_mode'],
                      })
                    }
                  >
                    <option value="ALERT_ONLY">Alerts only</option>
                    <option value="PAPER">Paper trading</option>
                    <option value="SHADOW">Shadow (record only)</option>
                    <option value="AUTO_LIVE" disabled>
                      Automatic live (locked)
                    </option>
                  </select>
                </label>
              </div>
              {general.map(issue => (
                <span
                  key={`${issue.path}:${issue.code}`}
                  className={
                    issue.code === 'auto_live'
                      ? 'j-field__hint'
                      : 'j-field__error'
                  }
                >
                  {issue.path}: {issue.message}
                </span>
              ))}
            </fieldset>
            <section aria-label="Entry conditions">
              <h3 className={styles.sectionTitle}>Entry conditions</h3>
              <RuleEditor
                root={def.when}
                onChange={when => update({when})}
                issues={byNode}
                direction={def.direction}
                signalTf={def.signal_timeframe}
                onError={setEditError}
              />
            </section>
            <fieldset className={styles.settings}>
              <legend className={styles.sectionTitle}>Risk and alerts</legend>
              <div className={styles.row2}>
                <label className="j-field">
                  <span className="j-label">Minimum reward to risk</span>
                  <input
                    className="j-input j-tabular"
                    inputMode="decimal"
                    value={def.risk.min_rr}
                    onChange={e =>
                      update({risk: {...def.risk, min_rr: e.target.value}})
                    }
                  />
                </label>
                <label className="j-field">
                  <span className="j-label">Fee estimate (bps)</span>
                  <input
                    className="j-input j-tabular"
                    inputMode="decimal"
                    value={def.risk.fee_bps_estimate ?? ''}
                    placeholder="5"
                    onChange={e =>
                      update({
                        risk: {
                          ...def.risk,
                          fee_bps_estimate: e.target.value || undefined,
                        },
                      })
                    }
                  />
                </label>
                <label className="j-field">
                  <span className="j-label">Stop</span>
                  <select
                    className="j-select"
                    value={def.risk.stop_model}
                    onChange={e =>
                      update({
                        risk: {
                          ...def.risk,
                          stop_model: e.target
                            .value as StrategyDefinition['risk']['stop_model'],
                        },
                      })
                    }
                  >
                    <option value="AOI_INVALIDATION">
                      Beyond the touched area
                    </option>
                    <option value="SWING">Beyond the last swing</option>
                    <option value="ATR">ATR distance</option>
                  </select>
                </label>
                <label className="j-field">
                  <span className="j-label">Target</span>
                  <select
                    className="j-select"
                    value={def.risk.target_model}
                    onChange={e =>
                      update({
                        risk: {
                          ...def.risk,
                          target_model: e.target
                            .value as StrategyDefinition['risk']['target_model'],
                        },
                      })
                    }
                  >
                    <option value="NEAREST_OPPOSING_LIQUIDITY">
                      Nearest opposing liquidity
                    </option>
                    <option value="FIXED_R">Fixed R multiple</option>
                  </select>
                </label>
                <label className="j-field">
                  <span className="j-label">Cooldown (bars)</span>
                  <input
                    className="j-input j-tabular"
                    type="number"
                    min={0}
                    max={10000}
                    value={def.notification.cooldown_bars}
                    onChange={e =>
                      update({
                        notification: {
                          ...def.notification,
                          cooldown_bars: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              </div>
              <label className="j-check">
                <input
                  type="checkbox"
                  checked={def.notification.once_per_aoi}
                  onChange={e =>
                    update({
                      notification: {
                        ...def.notification,
                        once_per_aoi: e.target.checked,
                      },
                    })
                  }
                />
                Alert once per area of interest
              </label>
            </fieldset>
            <details className={styles.summaryBlock}>
              <summary>Read the rule as text</summary>
              <pre className={styles.outline}>{describeTree(def.when)}</pre>
            </details>
          </>
        )}
        {tab === 'json' && <JsonTab editor={editor} definition={def} />}
        {tab === 'backtest' && (
          <BacktestTab ctx={ctx} editor={editor} latest={latest} />
        )}
        {tab === 'versions' && (
          <VersionsTab
            editor={editor}
            versions={state.versions}
            busy={state.busy !== null}
          />
        )}
      </div>
    </section>
  );
}

/**
 * JSON export and import (v2 rules and starter v1 configs).
 * @param props Editor and current definition.
 * @return Element.
 */
function JsonTab({
  editor,
  definition,
}: {
  editor: StrategyEditor;
  definition: StrategyDefinition;
}): ReactNode {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<MigrationReport | null>(null);
  const exported = exportDefinition(definition);
  return (
    <div className={styles.jsonTab}>
      <label className="j-field">
        <span className="j-label">Current rule (schema version 2)</span>
        <textarea
          className={`j-input ${styles.code}`}
          readOnly
          value={exported}
          rows={14}
        />
      </label>
      <button
        type="button"
        className="j-btn j-btn--sm"
        onClick={() => void navigator.clipboard?.writeText(exported)}
      >
        Copy JSON
      </button>
      <label className="j-field">
        <span className="j-label">Import a rule</span>
        <textarea
          className={`j-input ${styles.code}`}
          value={text}
          rows={6}
          placeholder="Paste a version 2 rule or a starter version 1 config"
          onChange={e => setText(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="j-btn j-btn--sm"
        disabled={!text.trim()}
        onClick={() => {
          const result = importDefinition(text);
          if (!result.ok) {
            setError(result.error);
            setReport(null);
            return;
          }
          setError(null);
          setReport(result.migration);
          editor.setDefinition(result.definition);
        }}
      >
        Import rule
      </button>
      {error && <Notice tone="danger">{error}</Notice>}
      {report && (
        <div className={styles.migration} data-testid="migration-report">
          <strong>Migrated from version 1</strong>
          <ul>
            {report.mapped.map(item => (
              <li key={item}>Mapped: {item}</li>
            ))}
            {report.assumptions.map(item => (
              <li key={item} className="j-course">
                Review: {item}
              </li>
            ))}
            {report.unmapped.map(item => (
              <li key={item} className="j-neg">
                Not migrated: {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Backtest form and results.
 * @param props Context, editor and latest version.
 * @return Element.
 */
function BacktestTab({
  ctx,
  editor,
  latest,
}: {
  ctx: WebFeatureContext;
  editor: StrategyEditor;
  latest: StrategyVersion | null;
}): ReactNode {
  const selection = useStore(ctx.selection);
  const [days, setDays] = useState('7');
  const [fee, setFee] = useState('5');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<BacktestReport | null>(null);
  if (!latest) {
    return (
      <EmptyState title="Save the strategy first">
        Backtests run against a saved, immutable version so results stay
        reproducible.
      </EmptyState>
    );
  }
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const to = Date.now();
      const from = to - Math.max(1, Number(days) || 7) * 86_400_000;
      setReport(
        await ctx.clients.strategy.backtest(
          editor.state.get().definition.strategy_id,
          {
            version: latest.version,
            instrument_id: selection.instrumentId,
            from_ms: from,
            to_ms: to,
            fee_bps: fee || undefined,
          }
        )
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const metric = (label: string, value: string | null | number) => (
    <div className={styles.metric}>
      <span className="j-label">{label}</span>
      <strong className="j-tabular">{value ?? '—'}</strong>
    </div>
  );
  return (
    <div className={styles.backtest}>
      <p className="j-muted">
        Version {latest.version} on {selection.instrumentId} using closed bars
        only.
      </p>
      <div className={styles.row2}>
        <label className="j-field">
          <span className="j-label">Days of history</span>
          <input
            className="j-input j-tabular"
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={e => setDays(e.target.value)}
          />
        </label>
        <label className="j-field">
          <span className="j-label">Fees (bps per side)</span>
          <input
            className="j-input j-tabular"
            inputMode="decimal"
            value={fee}
            onChange={e => setFee(e.target.value)}
          />
        </label>
      </div>
      <button
        type="button"
        className="j-btn j-btn--primary j-btn--sm"
        disabled={busy}
        onClick={() => void run()}
      >
        {busy ? 'Running…' : `Run backtest of version ${latest.version}`}
      </button>
      {error && <Notice tone="danger">{error}</Notice>}
      {report && (
        <div data-testid="backtest-report" className={styles.report}>
          {report.feed_state === 'DEMO' && (
            <Notice tone="warning">
              Demo report: synthetic numbers that say nothing about this
              strategy.
            </Notice>
          )}
          <div className={styles.metrics}>
            {metric('Trades', report.metrics.trades)}
            {metric(
              'Win rate',
              report.metrics.win_rate
                ? `${formatFixed(String(Number(report.metrics.win_rate) * 100), 1)}%`
                : null
            )}
            {metric(
              'Expectancy',
              report.metrics.expectancy_r
                ? `${report.metrics.expectancy_r}R`
                : null
            )}
            {metric('Profit factor', report.metrics.profit_factor)}
            {metric('Max drawdown', `${report.metrics.max_drawdown_r}R`)}
            {metric(
              'Average hold',
              report.metrics.avg_hold_bars
                ? `${report.metrics.avg_hold_bars} bars`
                : null
            )}
          </div>
          <table className="j-table" aria-label="Backtest trades">
            <thead>
              <tr>
                <th>Entry</th>
                <th>Exit reason</th>
                <th className="j-num">R</th>
                <th className="j-num">Bars</th>
              </tr>
            </thead>
            <tbody>
              {report.trades.map(trade => (
                <tr key={trade.signal_id}>
                  <td>{formatUtcDateTime(trade.entry_ms)}</td>
                  <td>{trade.exit_reason.toLowerCase().replace(/_/g, ' ')}</td>
                  <td
                    className={`j-num ${trade.r_multiple.startsWith('-') ? 'j-neg' : 'j-pos'}`}
                  >
                    {trade.r_multiple}
                  </td>
                  <td className="j-num">{trade.hold_bars}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className={styles.assumptions}>
            {report.assumptions.map(a => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Version list with activation, pause and diffs. AUTO_LIVE is locked.
 * @param props Editor, versions and busy flag.
 * @return Element.
 */
function VersionsTab({
  editor,
  versions,
  busy,
}: {
  editor: StrategyEditor;
  versions: StrategyVersion[];
  busy: boolean;
}): ReactNode {
  const [mode, setMode] = useState<ActivateRequest['mode']>('ALERT_ONLY');
  const [pending, setPending] = useState<StrategyVersion | null>(null);
  const modeName = {
    ALERT_ONLY: 'alerts-only',
    PAPER: 'paper',
    SHADOW: 'shadow',
  }[mode];
  return (
    <div className={styles.versions}>
      <div className={styles.lock} data-testid="auto-live-lock">
        <Lock size={16} aria-hidden />
        <p>{AUTO_LIVE_LOCK_REASON}</p>
      </div>
      {versions.length === 0 ? (
        <EmptyState title="No saved versions">
          Save the strategy to create version 1. Every save creates a new
          immutable version.
        </EmptyState>
      ) : (
        <>
          <label className="j-field">
            <span className="j-label">Activate in</span>
            <select
              className="j-select"
              value={mode}
              onChange={e => setMode(e.target.value as ActivateRequest['mode'])}
            >
              <option value="ALERT_ONLY">Alerts only</option>
              <option value="PAPER">Paper trading</option>
              <option value="SHADOW">Shadow (record only)</option>
            </select>
          </label>
          <ol className={styles.versionList}>
            {versions.map((version, i) => {
              const previous = versions[i + 1];
              const changes = previous
                ? diffDefinitions(previous.definition, version.definition)
                : [];
              return (
                <li
                  key={version.strategy_version_id}
                  className={styles.versionRow}
                >
                  <div className={styles.versionHead}>
                    <strong>Version {version.version}</strong>
                    <span
                      className={`j-badge ${version.state === 'ACTIVE' ? 'j-badge--live' : ''}`}
                    >
                      {version.state.toLowerCase()}
                    </span>
                    {version.activation && (
                      <span className="j-muted">
                        {version.activation.mode
                          .replace('_', ' ')
                          .toLowerCase()}
                      </span>
                    )}
                    <span className={styles.grow} />
                    <span className="j-muted">
                      {formatUtcDateTime(version.created_at_ms)}
                    </span>
                  </div>
                  <div className={styles.inline}>
                    {version.state === 'ACTIVE' ? (
                      <button
                        type="button"
                        className="j-btn j-btn--sm"
                        disabled={busy}
                        onClick={() => void editor.pause()}
                      >
                        Pause version {version.version}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="j-btn j-btn--sm"
                        disabled={busy}
                        onClick={() => setPending(version)}
                      >
                        Activate version {version.version}
                      </button>
                    )}
                  </div>
                  {changes.length > 0 && (
                    <details>
                      <summary className="j-muted">
                        {changes.length} change{changes.length === 1 ? '' : 's'}{' '}
                        from version {previous?.version}
                      </summary>
                      <ul className={styles.diff}>
                        {changes.slice(0, 30).map(change => (
                          <li key={`${change.path}:${change.kind}`}>
                            {change.kind} {change.path}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
      {pending && (
        <ConfirmDialog
          title={`Activate version ${pending.version}?`}
          confirmLabel={`Activate version ${pending.version} in ${modeName} mode`}
          busy={busy}
          onCancel={() => setPending(null)}
          onConfirm={() => {
            const version = pending;
            setPending(null);
            void editor.activate({version: version.version, mode});
          }}
        >
          <p>
            Signals from this version will be evaluated on closed bars.{' '}
            {mode === 'ALERT_ONLY'
              ? 'You will only receive alerts.'
              : mode === 'PAPER'
                ? 'Paper orders are created only for paper deployments you configure.'
                : 'Hypothetical orders are recorded but never sent.'}
          </p>
          <p>Any other active version of this strategy stops.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
