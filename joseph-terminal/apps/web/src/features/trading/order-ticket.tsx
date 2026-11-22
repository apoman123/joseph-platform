'use client';
/**
 * @fileoverview Order ticket: draft → preview → confirm. Every order goes to
 * the execution engine as an intent; the browser never signs or sends venue
 * orders. Submitting shows the venue-reported state, never an assumed fill.
 */
import {
  buildIntentRequest,
  canConfirm,
  describeAction,
  formatFixed,
  formatStep,
  isBusy,
  modeAvailability,
  needsLimitPrice,
  needsTriggerPrice,
  orderStateLabel,
  quantityFromRisk,
  rewardToRisk,
  type TicketInputs,
} from '@joseph/chart-trading';
import {
  Decimal,
  type AccountSummary,
  type OrderMode,
  type OrderType,
  type RiskCheckResult,
} from '@joseph/contracts';
import {useEffect, useId, useMemo, useState, type ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import styles from './trading.module.css';
import type {TradingRuntime} from './trading-runtime';
import {errorMessage, useNow, usePolling} from './ui/hooks';
import {FeedBadge, InstrumentLabel, ModeBadge, Notice} from './ui/primitives';

/** Props of the order ticket. */
export interface OrderTicketProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Trading runtime. */
  runtime: TradingRuntime;
}

/** Order type buttons. */
const TYPE_OPTIONS: readonly {type: OrderType; label: string}[] = [
  {type: 'market', label: 'Market'},
  {type: 'limit', label: 'Limit'},
  {type: 'stop_market', label: 'Stop'},
];

/**
 * Outcome badge class for a risk check.
 * @param outcome Check outcome.
 * @return Badge modifier class.
 */
function outcomeClass(outcome: RiskCheckResult['outcome']): string {
  return {
    PASS: 'j-badge j-badge--live',
    FAIL: 'j-badge j-badge--stale',
    WARN: 'j-badge j-badge--sodium',
    SKIP: 'j-badge j-badge--muted',
    UNKNOWN: 'j-badge j-badge--sodium',
  }[outcome];
}

/**
 * Renders the order ticket.
 * @param props Props.
 * @return Ticket element.
 */
export function OrderTicket({ctx, runtime}: OrderTicketProps): ReactNode {
  const state = useStore(runtime.controller);
  const meta = useStore(runtime.meta);
  const amend = useStore(runtime.amend);
  const now = useNow(1000);
  const uid = useId();
  const accounts = usePolling(
    () => ctx.clients.execution.listAccounts(),
    'accounts',
    10_000
  );
  const {inputs} = state;
  const account: AccountSummary | null =
    accounts.data?.accounts.find(a => a.account_id === inputs.accountId) ??
    accounts.data?.accounts[0] ??
    null;

  useEffect(() => {
    if (!inputs.accountId && account)
      runtime.controller.edit({accountId: account.account_id});
  }, [account, inputs.accountId, runtime.controller]);

  const modes = useMemo(() => modeAvailability(account), [account]);
  const built = useMemo(
    () =>
      buildIntentRequest(
        inputs,
        meta.instrumentId === inputs.instrumentId ? meta.precision : null,
        meta.mark
      ),
    [inputs, meta]
  );
  const precision =
    meta.instrumentId === inputs.instrumentId ? meta.precision : null;
  const [attempted, setAttempted] = useState(false);
  const touched = (name: string): boolean => {
    if (attempted || name === 'notional' || name === 'instrumentId')
      return true;
    const value = (inputs as unknown as Record<string, unknown>)[name];
    return typeof value === 'string' ? value.trim() !== '' : true;
  };
  const errorsFor = (field: string) =>
    built.issues.filter(
      i => i.field === field && (i.severity === 'warning' || touched(field))
    );
  const edit = (patch: Partial<TicketInputs>) => runtime.controller.edit(patch);
  const busy = isBusy(state);
  const previewCurrent =
    state.preview !== null && state.previewRevision === state.inputRevision;
  const base = precision?.key.canonical_base ?? '';

  const [riskPct, setRiskPct] = useState('0.5');
  const riskQty = useMemo(() => {
    if (!account?.equity || !precision || !Decimal.isValid(riskPct))
      return null;
    const entry = built.limitPrice ?? built.triggerPrice ?? meta.mark;
    const stopText = inputs.stopLoss.replace(/,/g, '').trim();
    if (!entry || !Decimal.isValid(stopText)) return null;
    return quantityFromRisk({
      equity: Decimal.parse(account.equity),
      riskPct: Decimal.parse(riskPct),
      entry,
      stop: Decimal.parse(stopText),
      lotSize: Decimal.parse(precision.lot_size),
    });
  }, [
    account,
    precision,
    riskPct,
    built.limitPrice,
    built.triggerPrice,
    meta.mark,
    inputs.stopLoss,
  ]);

  const rr = useMemo(() => {
    const entry = built.limitPrice ?? built.triggerPrice ?? meta.mark;
    const sl = inputs.stopLoss.replace(/,/g, '').trim();
    const tp = inputs.takeProfit.replace(/,/g, '').trim();
    if (!entry || !Decimal.isValid(sl) || !Decimal.isValid(tp)) return null;
    return rewardToRisk(
      inputs.side,
      entry,
      Decimal.parse(sl),
      Decimal.parse(tp)
    );
  }, [
    built.limitPrice,
    built.triggerPrice,
    meta.mark,
    inputs.side,
    inputs.stopLoss,
    inputs.takeProfit,
  ]);

  const fieldId = (name: string) => `${uid}-${name}`;
  const field = (
    name: keyof TicketInputs,
    label: string,
    input: ReactNode,
    hint?: string
  ) => {
    const issues = errorsFor(name);
    return (
      <div className="j-field">
        <label className="j-label" htmlFor={fieldId(name)}>
          {label}
        </label>
        {input}
        <span id={`${fieldId(name)}-desc`} className={styles.describe}>
          {issues.map(issue => (
            <span
              key={issue.message}
              className={
                issue.severity === 'error' ? 'j-field__error' : 'j-field__hint'
              }
            >
              {issue.message}
            </span>
          ))}
          {hint && issues.length === 0 && (
            <span className="j-field__hint">{hint}</span>
          )}
        </span>
      </div>
    );
  };

  const text = (
    name:
      | 'quantity'
      | 'limitPrice'
      | 'triggerPrice'
      | 'leverage'
      | 'stopLoss'
      | 'takeProfit',
    placeholder = ''
  ) => (
    <input
      id={fieldId(name)}
      aria-describedby={`${fieldId(name)}-desc`}
      className="j-input j-tabular"
      inputMode="decimal"
      value={inputs[name]}
      placeholder={placeholder}
      aria-invalid={errorsFor(name).some(i => i.severity === 'error')}
      disabled={busy}
      onChange={e => edit({[name]: e.target.value})}
    />
  );

  const remainingMs = state.preview ? state.preview.expires_at_ms - now : 0;
  const confirmLabel =
    state.preview && precision
      ? describeAction(state.preview.normalized, precision)
      : 'Confirm order';

  return (
    <section
      className={`j-panel ${styles.ticket}`}
      aria-label="Order ticket"
      data-testid="order-ticket"
    >
      <header className="j-panel__header">
        <h2>Order ticket</h2>
        <InstrumentLabel instrumentId={inputs.instrumentId} />
        <span className={styles.grow} />
        <ModeBadge mode={inputs.mode} />
        {ctx.clients.demo && (
          <span className="j-badge j-badge--demo">Demo</span>
        )}
      </header>
      <div className={`j-panel__body ${styles.body}`}>
        {amend && <AmendPanel ctx={ctx} runtime={runtime} />}
        {meta.error && <Notice tone="warning">{meta.error}</Notice>}
        {accounts.error && (
          <Notice tone="danger">
            Accounts could not be loaded: {accounts.error}
          </Notice>
        )}
        {field(
          'accountId',
          'Account',
          <select
            id={fieldId('accountId')}
            className="j-select"
            value={inputs.accountId ?? ''}
            disabled={busy}
            onChange={e => edit({accountId: e.target.value || null})}
          >
            {!accounts.data?.accounts.length && (
              <option value="">No accounts</option>
            )}
            {accounts.data?.accounts.map(a => (
              <option key={a.account_id} value={a.account_id}>
                {a.label}
              </option>
            ))}
          </select>
        )}
        <div className="j-field">
          <span className="j-label" id="ticket-mode-label">
            Mode
          </span>
          <div
            className="j-segmented"
            role="group"
            aria-labelledby="ticket-mode-label"
          >
            {modes.map(m => (
              <button
                key={m.mode}
                type="button"
                aria-pressed={inputs.mode === m.mode}
                disabled={!m.available || busy}
                title={m.reason ?? undefined}
                onClick={() => edit({mode: m.mode as OrderMode})}
              >
                {m.mode === 'LIVE'
                  ? 'Live'
                  : m.mode === 'PAPER'
                    ? 'Paper'
                    : 'Shadow'}
              </button>
            ))}
          </div>
          {modes.find(m => m.mode === 'LIVE' && !m.available) && (
            <span className="j-field__hint" data-testid="live-disabled-reason">
              {modes.find(m => m.mode === 'LIVE')?.reason}
            </span>
          )}
        </div>
        <div className={styles.sides} role="group" aria-label="Side">
          <button
            type="button"
            className={`j-btn ${inputs.side === 'buy' ? 'j-btn--bid' : ''}`}
            aria-pressed={inputs.side === 'buy'}
            disabled={busy}
            onClick={() => edit({side: 'buy'})}
          >
            Buy
          </button>
          <button
            type="button"
            className={`j-btn ${inputs.side === 'sell' ? 'j-btn--ask' : ''}`}
            aria-pressed={inputs.side === 'sell'}
            disabled={busy}
            onClick={() => edit({side: 'sell'})}
          >
            Sell
          </button>
        </div>
        <div className="j-segmented" role="group" aria-label="Order type">
          {TYPE_OPTIONS.map(option => (
            <button
              key={option.type}
              type="button"
              aria-pressed={inputs.orderType === option.type}
              disabled={busy}
              onClick={() => edit({orderType: option.type})}
            >
              {option.label}
            </button>
          ))}
        </div>
        {field(
          'quantity',
          `Quantity${base ? ` (${base})` : ''}`,
          text('quantity', precision ? precision.lot_size : ''),
          precision
            ? `Lot size ${precision.lot_size}, minimum ${precision.min_qty}`
            : undefined
        )}
        {needsLimitPrice(inputs.orderType) &&
          field(
            'limitPrice',
            'Limit price',
            <div className={styles.inline}>
              {text('limitPrice', meta.mark ? meta.mark.toString() : '')}
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--ghost"
                disabled={!meta.mark || busy}
                onClick={() =>
                  meta.mark && edit({limitPrice: meta.mark.toString()})
                }
              >
                Use mark
              </button>
            </div>
          )}
        {needsTriggerPrice(inputs.orderType) && (
          <>
            {field('triggerPrice', 'Trigger price', text('triggerPrice'))}
            {field(
              'triggerBasis',
              'Trigger on',
              <select
                id={fieldId('triggerBasis')}
                className="j-select"
                value={inputs.triggerBasis}
                disabled={busy}
                onChange={e =>
                  edit({
                    triggerBasis: e.target
                      .value as TicketInputs['triggerBasis'],
                  })
                }
              >
                <option value="mark">Mark price</option>
                <option value="last">Last price</option>
              </select>
            )}
          </>
        )}
        <div className={styles.row2}>
          {inputs.orderType !== 'market' &&
            field(
              'timeInForce',
              'Time in force',
              <select
                id={fieldId('timeInForce')}
                className="j-select"
                value={inputs.timeInForce}
                disabled={busy}
                onChange={e =>
                  edit({
                    timeInForce: e.target.value as TicketInputs['timeInForce'],
                  })
                }
              >
                <option value="GTC">Good till canceled</option>
                <option value="IOC">Immediate or cancel</option>
                <option value="POST_ONLY">Post only</option>
              </select>
            )}
          {field('leverage', 'Leverage', text('leverage', 'Venue default'))}
        </div>
        <label className="j-check">
          <input
            type="checkbox"
            checked={inputs.reduceOnly}
            disabled={busy}
            onChange={e => edit({reduceOnly: e.target.checked})}
          />
          Reduce only
        </label>
        <div className={styles.row2}>
          {field('stopLoss', 'Stop-loss', text('stopLoss', 'Optional'))}
          {field('takeProfit', 'Take-profit', text('takeProfit', 'Optional'))}
        </div>
        {rr && (
          <p className={styles.meta}>
            Reward to risk {rr.toFixed(2)}R at the entry price
          </p>
        )}
        <details className={styles.helper}>
          <summary>Size from risk</summary>
          <div className={styles.inline}>
            <label className="j-field">
              <span className="j-label">Risk % of equity</span>
              <input
                className="j-input j-tabular"
                inputMode="decimal"
                value={riskPct}
                onChange={e => setRiskPct(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="j-btn j-btn--sm"
              disabled={!riskQty || riskQty.isZero() || busy}
              onClick={() => riskQty && edit({quantity: riskQty.toString()})}
            >
              {riskQty && precision
                ? `Use ${formatStep(riskQty.toString(), precision.lot_size)} ${base}`
                : 'Set a stop-loss first'}
            </button>
          </div>
          <span className="j-field__hint">
            Uses account equity and the stop distance; the server still checks
            every limit.
          </span>
        </details>
        {errorsFor('notional').map(issue => (
          <span key={issue.message} className="j-field__error">
            {issue.message}
          </span>
        ))}
        {errorsFor('instrumentId').map(issue => (
          <Notice key={issue.message} tone="warning">
            {issue.message}
          </Notice>
        ))}

        {previewCurrent && state.preview && (
          <div
            className={styles.preview}
            data-testid="ticket-preview"
            aria-live="polite"
          >
            <h3>Preview</h3>
            <dl className={styles.facts}>
              <dt>Quantity</dt>
              <dd className="j-tabular">
                {precision
                  ? formatStep(
                      state.preview.normalized.quantity_base,
                      precision.lot_size
                    )
                  : state.preview.normalized.quantity_base}{' '}
                {base}
              </dd>
              {state.preview.normalized.limit_price && (
                <>
                  <dt>Limit</dt>
                  <dd className="j-tabular">
                    {precision
                      ? formatStep(
                          state.preview.normalized.limit_price,
                          precision.tick_size
                        )
                      : state.preview.normalized.limit_price}
                  </dd>
                </>
              )}
              <dt>Notional</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.notional, 2)}
              </dd>
              <dt>Estimated fee</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.estimated_fee, 4)}
              </dd>
              <dt>Margin required</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.margin_required, 2)}
              </dd>
              <dt>Estimated slippage</dt>
              <dd className="j-tabular">
                {state.preview.estimated_slippage_pct
                  ? `${state.preview.estimated_slippage_pct}%`
                  : 'None for resting orders'}
              </dd>
              <dt>Mark</dt>
              <dd className="j-tabular">
                {state.preview.mark_price ?? '—'}{' '}
                {meta.feedState && <FeedBadge state={meta.feedState} />}
              </dd>
            </dl>
            <p className={styles.meta}>
              {state.preview.protection.note ||
                (state.preview.protection.native_stop_loss
                  ? 'Stop-loss is held natively by the venue.'
                  : 'The venue has no native stop-loss; any stop is software-managed and not guaranteed.')}
            </p>
            <ul className={styles.checks} aria-label="Risk checks">
              {state.preview.risk.checks.map(check => (
                <li key={check.check}>
                  <span className={outcomeClass(check.outcome)}>
                    {check.outcome.toLowerCase()}
                  </span>
                  <span>{check.message}</span>
                </li>
              ))}
            </ul>
            {state.preview.warnings.map(w => (
              <p key={w} className={styles.meta}>
                {w}
              </p>
            ))}
            {!state.preview.allowed && (
              <Notice tone="danger">
                This order is {state.preview.risk.decision.toLowerCase()}ed by
                risk checks. Fix the failing checks and preview again.
              </Notice>
            )}
            {state.preview.allowed &&
              remainingMs > 0 &&
              state.phase === 'PREVIEWED' && (
                <p className={styles.meta}>
                  Preview valid for {Math.ceil(remainingMs / 1000)}s
                </p>
              )}
          </div>
        )}

        {state.error && (
          <Notice
            tone={state.error.code === 'PREVIEW_EXPIRED' ? 'warning' : 'danger'}
          >
            {state.error.message}
            {state.phase === 'FAILED' &&
              state.idempotencyKey &&
              state.error.retryable && (
                <>
                  {' '}
                  <button
                    type="button"
                    className="j-btn j-btn--sm"
                    onClick={() => void runtime.controller.confirm()}
                  >
                    Retry the same order
                  </button>
                </>
              )}
          </Notice>
        )}

        {state.phase === 'SUBMITTED' && state.result && (
          <div data-testid="ticket-result">
            {state.result.orders.length === 0 ? (
              <Notice tone="danger">
                Not placed:{' '}
                {state.result.risk.checks.find(c => c.outcome === 'FAIL')
                  ?.message ?? `risk decision ${state.result.risk.decision}`}
              </Notice>
            ) : (
              <Notice tone="info">
                Sent{' '}
                {state.result.replayed
                  ? '(already received, not duplicated) '
                  : ''}
                as{' '}
                {state.result.orders
                  .map(o => orderStateLabel(o.state).toLowerCase())
                  .join(', ')}
                . The final state comes from the venue and appears under Orders.
              </Notice>
            )}
          </div>
        )}
      </div>
      <footer className={`j-panel__footer ${styles.footer}`}>
        {state.phase === 'SUBMITTED' ? (
          <button
            type="button"
            className="j-btn"
            onClick={() => runtime.controller.dispatch({type: 'reset'})}
          >
            New order
          </button>
        ) : (
          <>
            <button
              type="button"
              className="j-btn"
              disabled={busy}
              onClick={() => {
                setAttempted(true);
                void runtime.controller.preview();
              }}
            >
              {state.phase === 'PREVIEWING'
                ? 'Previewing…'
                : previewCurrent
                  ? 'Preview again'
                  : 'Preview order'}
            </button>
            <button
              type="button"
              className={`j-btn ${inputs.side === 'buy' ? 'j-btn--bid' : 'j-btn--ask'}`}
              data-testid="confirm-order"
              disabled={
                !canConfirm(state, now) &&
                !(state.phase === 'FAILED' && state.idempotencyKey !== null)
              }
              aria-busy={state.phase === 'SUBMITTING'}
              onClick={() => void runtime.controller.confirm()}
            >
              {state.phase === 'SUBMITTING' || state.phase === 'CONFIRMING'
                ? 'Submitting…'
                : confirmLabel}
            </button>
          </>
        )}
      </footer>
    </section>
  );
}

/**
 * Confirmation panel for an order line dragged on the chart.
 * @param props Props.
 * @return Panel element.
 */
function AmendPanel({ctx, runtime}: OrderTicketProps): ReactNode {
  const draft = useStore(runtime.amend);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!draft) return null;
  const precision = runtime.precisionFor(draft.order.instrument_id);
  const fmt = (value: string) =>
    precision ? formatStep(value, precision.tick_size) : value;
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await ctx.clients.execution.amendOrder(draft.order.order_id, {
        [draft.field]: draft.price,
        expected_version: draft.order.version,
      });
      ctx.notifier.notify(
        `Amendment sent. The order moves to ${fmt(draft.price)} once the venue confirms.`,
        'success'
      );
      runtime.amend.set(null);
      void runtime.refreshOrders();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className={styles.amend}
      role="group"
      aria-label="Amend order"
      data-testid="amend-panel"
    >
      <p>
        Move {draft.order.side} {draft.order.order_type.replace('_', '-')}{' '}
        {draft.order.quantity_base} from {fmt(draft.from)} to{' '}
        <strong className="j-course">{fmt(draft.price)}</strong>?
      </p>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className={styles.inline}>
        <button
          type="button"
          className="j-btn j-btn--ghost"
          onClick={() => runtime.amend.set(null)}
          disabled={busy}
        >
          Keep current price
        </button>
        <button
          type="button"
          className="j-btn j-btn--primary"
          onClick={() => void confirm()}
          disabled={busy}
        >
          {busy ? 'Sending…' : `Move order to ${fmt(draft.price)}`}
        </button>
      </div>
    </div>
  );
}
