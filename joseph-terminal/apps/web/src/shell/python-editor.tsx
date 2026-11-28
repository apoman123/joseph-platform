/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Five-role Python authoring, simulations, market scans and alerts. */

'use client';
import {
  type AlertJob,
  type AlertSubscription,
  type ScriptInput,
  type ScriptResult,
  type ScriptVersion,
  type ScanJob,
  type Venue,
  type MarketType,
} from '@joseph/contracts';
import {activeChart} from '@joseph/terminal-shell';
import {
  createElement,
  useCallback,
  useEffect,
  useState,
  type ReactElement,
} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {useStore, type WebFeature} from './react-bindings';
import {attachPython, pythonState} from './python-state';
import {shellManifest} from '../features/shell/manifest';
import styles from './script-ui.module.css';

/** Supported independent source roles in editor order. */
export const PYTHON_KINDS = [
  'indicator',
  'strategy',
  'library',
  'filter',
  'alert',
] as const;
/** Independent script role. */
type Kind = (typeof PYTHON_KINDS)[number];

/**
 * Creates a concrete, executable example inheriting the role's own abstract base.
 * @param kind Indicator, strategy, library, filter or alert role.
 * @return Python source with one concrete class and the required override.
 */
export function pythonTemplate(kind: Kind): string {
  const base = kind.charAt(0).toUpperCase() + kind.slice(1);
  const implementations = {
    indicator:
      '    def on_bar(self, ctx):\n        """Plots a configurable average using closed source bars."""\n        length = ctx.input("length", 20, minimum=1, maximum=500)\n        ctx.plot("Average", ctx.sma(length=int(length)))\n',
    strategy:
      '    def on_bar(self, ctx):\n        """Simulates next-bar-open fills; never places live orders."""\n        fast = ctx.sma(length=10)\n        slow = ctx.sma(length=30)\n        if fast is not None and slow is not None:\n            ctx.plot("Fast", fast)\n            ctx.plot("Slow", slow, color="#e66fd2")\n            if fast > slow and ctx.position_size == 0:\n                ctx.entry("long", quantity=1)\n            elif fast < slow and ctx.position_size > 0:\n                ctx.close()\n',
    library:
      '    def exports(self):\n        """Exports reusable objects to explicitly pinned importers."""\n        def mean(values):\n            """Returns the mean of a nonempty numeric sequence."""\n            return sum(values) / len(values)\n        return {"mean": mean}\n',
    filter:
      '    def matches(self, ctx):\n        """Selects actual market quotes above the configured threshold."""\n        price = ctx.snapshot.get("last")\n        minimum = ctx.input("minimum_price", 10, minimum=0)\n        return price is not None and float(price) >= minimum\n',
    alert:
      '    def on_bar(self, ctx):\n        """Reports a condition; live jobs deliver only future closed bars."""\n        threshold = ctx.input("threshold", 200, minimum=0)\n        if ctx.bar["close"] > threshold:\n            ctx.alert("Price closed above threshold", name="above-threshold")\n',
  };
  return `from joseph_script import ${base}\n\n\nclass My${base}(${base}):\n    """User-owned ${kind} implementation."""\n\n${implementations[kind]}`;
}

/**
 * Parses a JSON object while refusing arrays, null and primitive values.
 * @param value Editor JSON text.
 * @return Object suitable for strict server validation.
 * @throws Error If parsing fails or the root is not an object.
 */
function object(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Expected a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

/**
 * Renders immutable Python authoring and all five role-specific workflows.
 * @param props Authenticated terminal runtime.
 * @return Editor, real execution reports, filter progress and alert controls.
 */
export function PythonEditor({
  runtime,
}: {
  runtime: TerminalRuntime;
}): ReactElement {
  const state = pythonState(runtime);
  const catalog = useStore(state.catalog);
  const editing = useStore(state.editing);
  const workspace = useStore(runtime.workspace);
  const chart = activeChart(workspace);
  const [kind, setKind] = useState<Kind>('indicator');
  const [slug, setSlug] = useState('my-indicator');
  const [name, setName] = useState('My indicator');
  const [source, setSource] = useState(pythonTemplate('indicator'));
  const [defaults, setDefaults] = useState('{}');
  const [libraries, setLibraries] = useState('{}');
  const [requests, setRequests] = useState('{}');
  const [overlay, setOverlay] = useState(true);
  const [saved, setSaved] = useState<ScriptVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<ScriptResult | null>(null);
  const [scan, setScan] = useState<ScanJob | null>(null);
  const [market, setMarket] = useState<Venue>('nasdaq');
  const [marketType, setMarketType] = useState<MarketType>('stock');
  const [maximum, setMaximum] = useState(100);
  const [history, setHistory] = useState(0);
  const [alerts, setAlerts] = useState<AlertJob[]>([]);
  const [subscriptions, setSubscriptions] = useState<AlertSubscription[]>([]);
  const [targets, setTargets] = useState<string[]>([]);
  const [selectedVersion, setSelectedVersion] = useState(1);
  const [equity, setEquity] = useState(10000);
  const [commission, setCommission] = useState(0);
  const [slippage, setSlippage] = useState(0);
  const load = useCallback(async () => {
    if (runtime.config.demo) return;
    state.catalog.set(await state.client.list());
    setAlerts(await state.client.alerts());
    setSubscriptions(
      (await runtime.clients.strategy.listAlertSubscriptions()).subscriptions
    );
  }, [runtime, state]);
  useEffect(() => {
    void load().catch(error => void setFailure(String(error)));
  }, [load]);
  const adopt = (version: ScriptVersion) => {
    setSaved(version);
    setSelectedVersion(version.version);
    setKind(version.kind);
    setSlug(version.script_id);
    setName(version.name);
    setSource(version.source);
    setDefaults(JSON.stringify(version.defaults ?? {}, null, 2));
    setLibraries(JSON.stringify(version.libraries ?? {}, null, 2));
    setRequests(JSON.stringify(version.requests ?? {}, null, 2));
    setOverlay(version.overlay ?? true);
    setResult(null);
    setMessage(null);
    setFailure(null);
  };
  useEffect(() => {
    if (editing) adopt(editing);
  }, [editing]);
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setFailure(null);
    setMessage(null);
    try {
      await work();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  const draft = (): ScriptInput => ({
    script_id: slug,
    kind,
    name,
    source,
    defaults: object(defaults),
    libraries: object(libraries) as ScriptInput['libraries'],
    requests: object(requests) as ScriptInput['requests'],
    overlay,
    ...(saved ? {expected_version: saved.version} : {}),
  });
  const ensureSaved = () => {
    if (
      !saved ||
      source !== saved.source ||
      slug !== saved.script_id ||
      name !== saved.name ||
      overlay !== saved.overlay ||
      JSON.stringify(object(defaults)) !==
        JSON.stringify(saved.defaults ?? {}) ||
      JSON.stringify(object(libraries)) !==
        JSON.stringify(saved.libraries ?? {}) ||
      JSON.stringify(object(requests)) !== JSON.stringify(saved.requests ?? {})
    ) {
      throw new Error(
        'Save this source and its configuration before executing the pinned version.'
      );
    }
    return saved;
  };
  const fresh = (role: Kind) => {
    setSaved(null);
    setKind(role);
    setSlug(`my-${role}`);
    setName(`My ${role}`);
    setSource(pythonTemplate(role));
    setDefaults('{}');
    setLibraries('{}');
    setRequests('{}');
    setOverlay(true);
    setResult(null);
    setScan(null);
    setMessage(null);
    setFailure(null);
  };
  useEffect(() => {
    if (!scan || !['pending', 'running'].includes(scan.status)) return;
    let disposed = false;
    const timer = setTimeout(() => {
      void state.client.scanStatus(scan.job_id).then(
        value => {
          if (!disposed) setScan(value);
        },
        error => {
          if (!disposed) {
            setFailure(String(error));
            setScan(null);
          }
        }
      );
    }, 1000);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [state, scan]);
  const runtimeEnabled = catalog?.runtime_enabled === true;
  return (
    <section className={styles.editor} aria-label="Python scripts">
      <div className={styles.toolbar}>
        {PYTHON_KINDS.map(role => (
          <button
            key={role}
            type="button"
            className="j-btn"
            disabled={busy}
            onClick={() => void fresh(role)}
          >
            New {role}
          </button>
        ))}
        <label>
          My scripts{' '}
          <select
            className="j-select"
            value={saved?.script_id ?? ''}
            onChange={event => {
              const row = catalog?.scripts.find(
                item => item.script_id === event.target.value
              );
              if (row) adopt(row);
            }}
          >
            <option value="">Unsaved source</option>
            {catalog?.scripts.map(row => (
              <option key={row.script_id} value={row.script_id}>
                {row.name} · {row.kind} · v{row.version}
              </option>
            ))}
          </select>
        </label>
        {catalog?.next_offset != null && (
          <button
            className="j-btn"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const next = await state.client.list(catalog.next_offset ?? 0);
                state.catalog.set({
                  ...next,
                  scripts: [...catalog.scripts, ...next.scripts],
                });
              })
            }
          >
            Load more scripts
          </button>
        )}
        {saved && (
          <>
            <label>
              Version{' '}
              <input
                className="j-input"
                type="number"
                min={1}
                value={selectedVersion}
                onChange={event =>
                  void setSelectedVersion(Number(event.target.value))
                }
              />
            </label>
            <button
              className="j-btn"
              disabled={busy}
              onClick={() =>
                void run(
                  async () =>
                    void adopt(
                      await state.client.get(saved.script_id, selectedVersion)
                    )
                )
              }
            >
              Load version
            </button>
          </>
        )}
      </div>
      <p className={styles.notice}>
        {runtime.config.demo
          ? 'Connect the market engine to save or execute Python. DEMO never fabricates executed script results.'
          : runtimeEnabled
            ? 'Isolated Python runtime enabled · closed bars · strategy fills are simulations. Live notifications require an explicitly enabled alert job.'
            : 'Authoring is available. Execution requires JOSEPH_SCRIPT_RUNTIME=docker and the prebuilt runner image (README).'}{' '}
        {chart.instrument_id} · {chart.timeframe}
      </p>
      <div className={styles.fields}>
        <label className={styles.field}>
          Script ID
          <input
            className="j-input"
            value={slug}
            disabled={saved !== null}
            onChange={event => void setSlug(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          Name
          <input
            className="j-input"
            value={name}
            onChange={event => void setName(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          Kind
          <input className="j-input" readOnly value={kind} />
        </label>
        <label className="j-check">
          <input
            type="checkbox"
            checked={overlay}
            onChange={event => void setOverlay(event.target.checked)}
          />
          Overlay price chart
        </label>
      </div>
      <label className={styles.field}>
        Python source
        <textarea
          className={styles.source}
          aria-label="Python source"
          spellCheck={false}
          value={source}
          onChange={event => void setSource(event.target.value)}
          rows={16}
        />
      </label>
      <div className={styles.fields}>
        <label className={styles.field}>
          Default inputs (JSON)
          <textarea
            className="j-input"
            value={defaults}
            onChange={event => void setDefaults(event.target.value)}
            rows={4}
          />
        </label>
        <label className={styles.field}>
          Pinned libraries (JSON)
          <textarea
            className="j-input"
            placeholder='{"math": {"script_id": "math-tools", "version": 1}}'
            value={libraries}
            onChange={event => void setLibraries(event.target.value)}
            rows={4}
          />
        </label>
        <label className={styles.field}>
          Other timeframes / symbols (JSON)
          <textarea
            className="j-input"
            placeholder='{"daily": {"tf": "1d", "limit": 500}}'
            value={requests}
            onChange={event => void setRequests(event.target.value)}
            rows={4}
          />
        </label>
      </div>
      <div className={styles.toolbar}>
        <button
          className="j-btn"
          disabled={busy || runtime.config.demo}
          onClick={() =>
            void run(async () => {
              await state.client.validate(draft());
              setMessage(
                'Source and dependency pins are valid. No code was executed.'
              );
            })
          }
        >
          Validate
        </button>
        <button
          className="j-btn"
          disabled={busy || runtime.config.demo}
          onClick={() =>
            void run(async () => {
              const version = await state.client.save(draft());
              adopt(version);
              await load();
              setMessage(`Saved immutable v${version.version}.`);
            })
          }
        >
          Save version
        </button>
        {saved && (
          <button
            className="j-btn"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await state.client.archive(saved.script_id);
                fresh(kind);
                await load();
              })
            }
          >
            Archive source
          </button>
        )}
        {kind !== 'library' && kind !== 'filter' && (
          <>
            <button
              className="j-btn"
              disabled={busy || !runtimeEnabled}
              onClick={() =>
                void run(async () => {
                  const version = ensureSaved();
                  const output = await state.client.run(version.script_id, {
                    version: version.version,
                    instrument_id: chart.instrument_id,
                    tf: chart.timeframe,
                    limit: 2000,
                    initial_equity: equity,
                    commission_bps: commission,
                    slippage_bps: slippage,
                  });
                  setResult(output);
                })
              }
            >
              {kind === 'strategy' ? 'Run backtest' : 'Preview on closed bars'}
            </button>
            <button
              className="j-btn"
              disabled={
                busy ||
                !saved ||
                !runtimeEnabled ||
                chart.view_type === 'heatmap'
              }
              onClick={() =>
                void run(async () => {
                  const version = ensureSaved();
                  attachPython(runtime, chart, version);
                  setMessage(
                    'Pinned source added to this chart. New closed bars refresh the study.'
                  );
                })
              }
            >
              Add to chart
            </button>
          </>
        )}
      </div>
      {kind === 'strategy' && (
        <div className={styles.toolbar}>
          <label>
            Initial equity
            <input
              className="j-input"
              type="number"
              min={1}
              value={equity}
              onChange={event => void setEquity(Number(event.target.value))}
            />
          </label>
          <label>
            Commission (bps)
            <input
              className="j-input"
              type="number"
              min={0}
              value={commission}
              onChange={event => void setCommission(Number(event.target.value))}
            />
          </label>
          <label>
            Slippage (bps)
            <input
              className="j-input"
              type="number"
              min={0}
              value={slippage}
              onChange={event => void setSlippage(Number(event.target.value))}
            />
          </label>
        </div>
      )}
      {kind === 'filter' && (
        <fieldset>
          <legend>Market filter</legend>
          <div className={styles.toolbar}>
            <label>
              Market
              <select
                className="j-select"
                value={market}
                onChange={event => void setMarket(event.target.value as Venue)}
              >
                {[
                  'nasdaq',
                  'nyse',
                  'cboe',
                  'twse',
                  'tpex',
                  'binance',
                  'hyperliquid',
                  'lighter',
                  'bitget',
                  'mexc',
                  'mock',
                ].map(value => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Category
              <select
                className="j-select"
                value={marketType}
                onChange={event =>
                  void setMarketType(event.target.value as MarketType)
                }
              >
                {['stock', 'etf', 'spot', 'perp', 'rate'].map(value => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              Maximum symbols
              <input
                className="j-input"
                type="number"
                min={1}
                max={10000}
                value={maximum}
                onChange={event => void setMaximum(Number(event.target.value))}
              />
            </label>
            <label>
              History bars (0 = snapshots)
              <input
                className="j-input"
                type="number"
                min={0}
                max={500}
                value={history}
                onChange={event => void setHistory(Number(event.target.value))}
              />
            </label>
            <button
              className="j-btn"
              disabled={
                busy ||
                !runtimeEnabled ||
                (!!scan && ['pending', 'running'].includes(scan.status))
              }
              onClick={() =>
                void run(async () => {
                  const version = ensureSaved();
                  setScan(
                    await state.client.scan(version.script_id, {
                      version: version.version,
                      venue: market,
                      market_type: marketType,
                      max_symbols: maximum,
                      history_bars: history,
                      tf: chart.timeframe,
                      inputs: object(defaults),
                    })
                  );
                })
              }
            >
              Scan market
            </button>
            {scan && ['pending', 'running'].includes(scan.status) && (
              <button
                className="j-btn"
                onClick={() =>
                  void run(
                    async () =>
                      void setScan(await state.client.cancelScan(scan.job_id))
                  )
                }
              >
                Cancel scan
              </button>
            )}
          </div>
          {scan && (
            <>
              <p role="status">
                {scan.status} · {scan.scanned}/{scan.total} evaluated ·{' '}
                {scan.universe_count} eligible · {(scan.matches ?? []).length}{' '}
                matches · {(scan.errors ?? []).length} errors {scan.error}
              </p>
              <div className={styles.toolbar}>
                {(scan.matches ?? []).map((match, index) => (
                  <button
                    key={index}
                    className="j-btn"
                    onClick={() => {
                      if (typeof match.instrument_id === 'string') {
                        runtime.dispatch({
                          type: 'setInstrument',
                          chartId: chart.chart_id,
                          instrumentId: match.instrument_id,
                        });
                      }
                    }}
                  >
                    {String(match.instrument_id)}
                  </button>
                ))}
              </div>
              <details>
                <summary>Per-symbol errors</summary>
                <pre>{JSON.stringify(scan.errors, null, 2)}</pre>
              </details>
            </>
          )}
        </fieldset>
      )}
      {['alert', 'indicator', 'strategy'].includes(kind) && (
        <fieldset>
          <legend>Live Python alerts</legend>
          <p className={styles.notice}>
            Enabling pins this saved version and evaluates only future closed
            bars. Browser/UI is included; select external destinations below.
          </p>
          <div className={styles.toolbar}>
            {subscriptions
              .filter(sub => !['UI', 'EXECUTION'].includes(sub.channel))
              .map(sub => (
                <label key={sub.subscription_id} className="j-check">
                  <input
                    type="checkbox"
                    checked={targets.includes(sub.subscription_id)}
                    onChange={event =>
                      void setTargets(current =>
                        event.target.checked
                          ? [...current, sub.subscription_id]
                          : current.filter(id => id !== sub.subscription_id)
                      )
                    }
                  />
                  {sub.channel} · {sub.subscription_id}
                </label>
              ))}
            <button
              className="j-btn"
              disabled={busy || !runtimeEnabled}
              onClick={() =>
                void run(async () => {
                  const version = ensureSaved();
                  await state.client.saveAlert({
                    script_id: version.script_id,
                    version: version.version,
                    instrument_id: chart.instrument_id,
                    tf: chart.timeframe,
                    inputs: object(defaults),
                    subscription_ids: targets,
                    enabled: true,
                  });
                  await load();
                  setMessage('Future-bar Python alert enabled.');
                })
              }
            >
              Enable new live alert
            </button>
          </div>
          {alerts.map(job => (
            <div key={job.job_id} className={styles.row}>
              <span>
                {job.script_id} v{job.version} · {job.instrument_id} {job.tf} ·{' '}
                {job.enabled ? 'Enabled' : 'Disabled'} · {job.emitted} alerts{' '}
                {job.last_error}
              </span>
              <button
                className="j-btn"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const {
                      job_id,
                      created_at_ms,
                      last_processed_ms,
                      last_error,
                      last_checked_ms,
                      emitted,
                      ...body
                    } = job;
                    void created_at_ms;
                    void last_processed_ms;
                    void last_error;
                    void last_checked_ms;
                    void emitted;
                    await state.client.saveAlert(
                      {...body, enabled: !job.enabled},
                      job_id
                    );
                    await load();
                  })
                }
              >
                {job.enabled ? 'Disable' : 'Enable'}
              </button>
              <button
                className="j-btn"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await state.client.deleteAlert(job.job_id);
                    await load();
                  })
                }
              >
                Delete alert
              </button>
            </div>
          ))}
        </fieldset>
      )}
      {busy && <p role="status">Working…</p>}
      {message && <p role="status">{message}</p>}
      {failure && <p role="alert">{failure}</p>}
      {result && (
        <section aria-label="Python execution report">
          <p>
            {result.data_source} ({result.feed_state}) · {result.bars_count}{' '}
            closed bars ·{' '}
            {result.complete
              ? 'Complete source window'
              : 'Partial source coverage'}{' '}
            · {result.source_hash}
          </p>
          {kind === 'strategy' && (
            <>
              <div className={styles.toolbar}>
                {Object.entries(result.metrics).map(([key, value]) => (
                  <span key={key}>
                    {key.replaceAll('_', ' ')}: {Number(value).toFixed(2)}
                  </span>
                ))}
              </div>
              <table className="j-table">
                <thead>
                  <tr>
                    <th>Fill bar (UTC)</th>
                    <th>Effect</th>
                    <th>Side</th>
                    <th>Price</th>
                    <th>Quantity</th>
                    <th>Fee</th>
                  </tr>
                </thead>
                <tbody>
                  {result.fills.map((fill, index) => (
                    <tr key={index}>
                      <td>{new Date(fill.time_ms).toISOString()}</td>
                      <td>{fill.effect}</td>
                      <td>{fill.side}</td>
                      <td>{fill.price}</td>
                      <td>{fill.quantity}</td>
                      <td>{fill.fee}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <p>
            {result.plots.length} plots · {result.visuals?.length ?? 0} drawings
            · {result.alerts?.length ?? 0} preview conditions (not delivered)
          </p>
        </section>
      )}
    </section>
  );
}

/**
 * Registers the Python editor as a discoverable bottom-panel contribution.
 * @param runtime Current terminal runtime; source remains private to the user.
 * @return Shell feature with an editor command and removable panel.
 */
export function pythonFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.python',
      'Python scripts',
      'Indicators, strategies, libraries, market filters and alert conditions.',
      {uiSlots: ['bottom-panel']}
    ),
    activate(ctx) {
      ctx.commands.register({
        id: 'python.editor',
        title: 'Open Python scripts editor',
        run: () =>
          ctx.commands.execute('shell.showPanel', {
            slot: 'bottom-panel',
            id: 'shell.python',
          }),
      });
      return ctx.slots.contribute('bottom-panel', {
        id: 'shell.python',
        title: 'Python editor',
        order: 20,
        icon: 'code',
        view: () => createElement(PythonEditor, {runtime}),
      });
    },
  };
}
