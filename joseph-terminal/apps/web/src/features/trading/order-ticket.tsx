'use client';
/**
 * @fileoverview Order ticket: draft → preview → confirm. Every order goes to
 * the execution engine as an intent; the browser never signs or sends venue
 * orders. Submitting shows the venue-reported state, never an assumed fill.
 *
 * Perpetuals keep leverage, reduce-only and attached stop/target. Stocks
 * and ETFs route to a broker account (IBKR for Nasdaq/NYSE, SinoPac or
 * Cathay for TWSE and TPEx) with cash-equity rules: whole shares or
 * Taiwan board/odd lots, tick-ladder prices inside the daily price limit,
 * DAY/GTC, IBKR's outside-RTH flag and market-hours notices from the
 * session calendar.
 * LIVE needs the server's permission, a server-side arming of the account
 * (step-up code verified by the execution engine; the ticket shows the
 * armed-until time from `live_armed_until_ms` and can disarm) and a
 * dedicated confirmation. When the engine answers `LIVE_NOT_ARMED` (the
 * arming expired or was revoked elsewhere) the ticket returns to paper and
 * asks to arm again. Published rates have no ticket.
 */
import {
  accountInstrumentMismatch,
  accountOrderTypes,
  boardLotOf,
  buildIntentRequest,
  canConfirm,
  describeAction,
  equityTimeInForce,
  formatEquityPrice,
  formatFixed,
  formatShares,
  formatStep,
  isBusy,
  isCashEquityId,
  isRateInstrumentId,
  liveArmedUntil,
  liveNotArmed,
  marketHoursNotice,
  modeAvailability,
  needsLimitPrice,
  needsTriggerPrice,
  oddLotAvailable,
  orderStateLabel,
  outsideRthSupport,
  preferredAccount,
  priceLimitBand,
  quantityFromRisk,
  rateNotice,
  rewardToRisk,
  tickAt,
  TIME_IN_FORCE_NAMES,
  tradingVenueName,
  type TicketInputs,
} from '@joseph/chart-trading';
import {
  ApiError,
  Decimal,
  instrumentCalendarId,
  type AccountSummary,
  type OrderMode,
  type OrderType,
  type RiskCheckResult,
  type TimeInForce,
} from '@joseph/contracts';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import {ArmLivePanel, LiveConfirmDialog} from './live-trading';
import styles from './trading.module.css';
import type {TradingRuntime} from './trading-runtime';
import {AccountStatusLine} from './ui/account-status';
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
 * Re-renders when the session calendars load new sessions.
 * @param ctx Feature context.
 * @return Revision counter.
 */
function useCalendarRevision(ctx: WebFeatureContext): number {
  const [revision, setRevision] = useState(0);
  useEffect(
    () => ctx.calendars.subscribe(() => setRevision(n => n + 1)),
    [ctx.calendars]
  );
  return revision;
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
  const armed = useStore(runtime.liveArm);
  // Orders are never previewed or sent from a chart in bar replay.
  const replay = useStore(ctx.selection).replay === true;
  const now = useNow(1000);
  const uid = useId();
  const calendarRevision = useCalendarRevision(ctx);
  // Each poll remembers when it started, so a server answer on arming
  // that arrived later wins until the next poll reports the arming.
  const accounts = usePolling(
    async () => {
      const startedMs = Date.now();
      const list = await ctx.clients.execution.listAccounts();
      return {...list, startedMs};
    },
    'accounts',
    10_000
  );
  const {inputs} = state;
  const equity = isCashEquityId(inputs.instrumentId);
  const rate = isRateInstrumentId(inputs.instrumentId);
  const precision =
    meta.instrumentId === inputs.instrumentId ? meta.precision : null;
  const accountList = useMemo(
    () => accounts.data?.accounts ?? [],
    [accounts.data]
  );
  const account: AccountSummary | null =
    accountList.find(a => a.account_id === inputs.accountId) ??
    preferredAccount(accountList, inputs.instrumentId, null, precision);
  const reference =
    meta.instrumentId === inputs.instrumentId ? (meta.reference ?? null) : null;
  const busy = isBusy(state);

  // Route to an account that trades the instrument: on first load, and when
  // the instrument changes (stocks wait for their registry entry, which
  // carries the calendar).
  const routed = useRef<string | null>(null);
  const routeKey = equity && !precision ? null : inputs.instrumentId;
  useEffect(() => {
    if (busy || accountList.length === 0 || routeKey === null) return;
    const changed = routed.current !== routeKey;
    routed.current = routeKey;
    const preferred = preferredAccount(
      accountList,
      inputs.instrumentId,
      inputs.accountId,
      precision
    );
    if (!preferred || preferred.account_id === inputs.accountId) return;
    if (!inputs.accountId || changed)
      runtime.controller.edit({accountId: preferred.account_id});
  }, [
    accountList,
    routeKey,
    busy,
    inputs.accountId,
    inputs.instrumentId,
    precision,
    runtime.controller,
  ]);

  // Keep equity-only choices valid for the instrument and account.
  const oddAvailable = precision ? oddLotAvailable(account, precision) : false;
  const oddLot =
    equity && oddAvailable && inputs.lotMode === 'odd' && !!precision;
  const tifChoice = useMemo(
    () => equityTimeInForce(account, oddLot ? 'odd' : 'board'),
    [account, oddLot]
  );
  const rth = outsideRthSupport(account);
  const orderTypes = useMemo(() => {
    if (!equity || !account) return TYPE_OPTIONS;
    const offered = accountOrderTypes(
      account,
      TYPE_OPTIONS.map(o => o.type)
    );
    return TYPE_OPTIONS.filter(o => offered.includes(o.type));
  }, [equity, account]);
  // Securities start on DAY (when offered) each time one is selected.
  const tifDefaulted = useRef<string | null>(null);
  useEffect(() => {
    if (busy) return;
    const patch: Partial<TicketInputs> = {};
    const fresh = equity && tifDefaulted.current !== inputs.instrumentId;
    if (equity && account && tifChoice.options.length > 0) {
      tifDefaulted.current = inputs.instrumentId;
      if (fresh && tifChoice.options[0] !== inputs.timeInForce)
        patch.timeInForce = tifChoice.options[0] ?? 'DAY';
    }
    if (!equity) tifDefaulted.current = null;
    if (
      equity &&
      account &&
      tifChoice.options.length > 0 &&
      !tifChoice.options.includes(inputs.timeInForce)
    )
      patch.timeInForce = tifChoice.options[0] ?? 'DAY';
    if (!equity && inputs.timeInForce === 'DAY') patch.timeInForce = 'GTC';
    // Intraday odd lots are limit DAY (ROD) orders only.
    if (oddLot && inputs.orderType !== 'limit') patch.orderType = 'limit';
    if (equity && precision && inputs.lotMode === 'odd' && !oddAvailable)
      patch.lotMode = 'board';
    if (inputs.outsideRth && (!equity || !rth.shown)) patch.outsideRth = false;
    if (
      equity &&
      !oddLot &&
      orderTypes.length > 0 &&
      !orderTypes.some(o => o.type === inputs.orderType)
    )
      patch.orderType = orderTypes[0]?.type ?? 'limit';
    if (equity && (inputs.leverage || inputs.reduceOnly)) {
      patch.leverage = '';
      patch.reduceOnly = false;
    }
    if (Object.keys(patch).length > 0) runtime.controller.edit(patch);
  }, [
    busy,
    equity,
    account,
    tifChoice,
    rth.shown,
    orderTypes,
    oddLot,
    oddAvailable,
    precision,
    inputs.instrumentId,
    inputs.lotMode,
    inputs.timeInForce,
    inputs.outsideRth,
    inputs.orderType,
    inputs.leverage,
    inputs.reduceOnly,
    runtime.controller,
  ]);

  // Cash-equity rules read the account, the previous close and the session
  // calendar (closed-market rules) when the order is previewed.
  const calendar = useMemo(() => {
    void calendarRevision;
    return equity
      ? ctx.calendars.calendar(
          instrumentCalendarId(inputs.instrumentId, precision)
        )
      : null;
  }, [calendarRevision, equity, ctx.calendars, inputs.instrumentId, precision]);
  useEffect(() => {
    runtime.controller.setContext({account, reference, calendar, nowMs: now});
  }, [runtime.controller, account, reference, calendar, now]);

  // The execution engine owns the arming: its newest answer (a grant or
  // revoke response, else the account poll) says until when LIVE is armed.
  // An expired or revoked arm (or another account) drops back to paper.
  const answer = account ? armed[account.account_id] : undefined;
  const armedUntil =
    liveArmedUntil(
      account && answer && answer.atMs >= (accounts.data?.startedMs ?? 0)
        ? {...account, live_armed_until_ms: answer.untilMs}
        : account,
      now
    ) ?? 0;
  const liveArmed = armedUntil > now;
  const rememberArming = (accountId: string, untilMs: number | null) =>
    runtime.liveArm.update(all => ({
      ...all,
      [accountId]: {untilMs, atMs: Date.now()},
    }));
  useEffect(() => {
    if (inputs.mode === 'LIVE' && !liveArmed && !busy) {
      runtime.controller.edit({mode: 'PAPER'});
      ctx.notifier.notify(
        'Live trading was disarmed; the ticket is back in paper mode.',
        'info'
      );
    }
  }, [inputs.mode, liveArmed, busy, runtime.controller, ctx.notifier]);
  const [arming, setArming] = useState(false);
  const [armReason, setArmReason] = useState<string | null>(null);
  const [liveConfirm, setLiveConfirm] = useState(false);

  // The engine refused a LIVE order because the account is not armed: the
  // local view was stale (expired or disarmed elsewhere). Forget it and ask
  // to arm again.
  const notArmedAccount =
    account &&
    inputs.mode === 'LIVE' &&
    ((state.preview !== null &&
      state.previewRevision === state.inputRevision &&
      liveNotArmed(state.preview.risk)) ||
      (state.phase === 'SUBMITTED' && liveNotArmed(state.result?.risk)) ||
      state.error?.code === 'LIVE_NOT_ARMED')
      ? account.account_id
      : null;
  useEffect(() => {
    if (!notArmedAccount) return;
    runtime.liveArm.update(all => ({
      ...all,
      [notArmedAccount]: {untilMs: null, atMs: Date.now()},
    }));
    setArmReason(
      'The execution engine refused this LIVE order: live trading is not armed for this account (the arming expired or was disarmed). Arm again to continue.'
    );
    setArming(true);
  }, [notArmedAccount, runtime.liveArm]);

  const disarm = async (target: AccountSummary) => {
    runtime.controller.edit({mode: 'PAPER'});
    try {
      const summary = await ctx.clients.execution.revokeAccountLiveConsent(
        target.account_id
      );
      rememberArming(target.account_id, summary.live_armed_until_ms ?? null);
      ctx.notifier.notify(`Live trading disarmed for ${target.label}.`, 'info');
    } catch (err) {
      ctx.notifier.notify(
        `The execution engine did not confirm disarming: ${errorMessage(err)}`,
        'error'
      );
    }
    accounts.refresh();
  };

  const modes = useMemo(() => modeAvailability(account), [account]);
  const built = useMemo(
    () =>
      buildIntentRequest(inputs, precision, meta.mark, {
        account,
        reference,
        calendar,
        nowMs: now,
      }),
    [inputs, precision, meta.mark, account, reference, calendar, now]
  );
  const [attempted, setAttempted] = useState(false);
  const touched = (name: string): boolean => {
    if (attempted || name === 'notional' || name === 'instrumentId')
      return true;
    if (
      name === 'accountId' ||
      name === 'timeInForce' ||
      name === 'outsideRth' ||
      name === 'orderType' ||
      name === 'lotMode'
    )
      return true;
    const value = (inputs as unknown as Record<string, unknown>)[name];
    return typeof value === 'string' ? value.trim() !== '' : true;
  };
  const errorsFor = (field: string) =>
    built.issues.filter(
      i => i.field === field && (i.severity === 'warning' || touched(field))
    );
  const edit = (patch: Partial<TicketInputs>) => runtime.controller.edit(patch);
  const previewCurrent =
    state.preview !== null && state.previewRevision === state.inputRevision;
  const base = precision?.key.canonical_base ?? '';
  const currency = precision?.key.quote ?? account?.currency ?? '';

  const [riskPct, setRiskPct] = useState('0.5');
  const riskQty = useMemo(() => {
    if (equity || !account?.equity || !precision || !Decimal.isValid(riskPct))
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
    equity,
    account,
    precision,
    riskPct,
    built.limitPrice,
    built.triggerPrice,
    meta.mark,
    inputs.stopLoss,
  ]);

  const rr = useMemo(() => {
    if (equity) return null;
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
    equity,
    built.limitPrice,
    built.triggerPrice,
    meta.mark,
    inputs.side,
    inputs.stopLoss,
    inputs.takeProfit,
  ]);

  const hoursNotice = useMemo(() => {
    if (!calendar) return null;
    return marketHoursNotice(calendar, now, {
      orderType: inputs.orderType,
      timeInForce: inputs.timeInForce,
      outsideRth: inputs.outsideRth === true,
      queueMarket: account?.capabilities.queue_market_when_closed === true,
      market: precision?.listing_exchange ?? null,
    });
  }, [
    calendar,
    account,
    precision,
    inputs.orderType,
    inputs.timeInForce,
    inputs.outsideRth,
    now,
  ]);

  const fieldId = (name: string) => `${uid}-${name}`;
  const field = (
    name: keyof TicketInputs,
    label: string,
    input: ReactNode,
    hint?: ReactNode
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

  if (rate) {
    return (
      <section
        className={`j-panel ${styles.ticket}`}
        aria-label="Order ticket"
        data-testid="order-ticket"
      >
        <header className="j-panel__header">
          <h2>Order ticket</h2>
          <InstrumentLabel instrumentId={inputs.instrumentId} />
        </header>
        <div className={`j-panel__body ${styles.rate}`}>
          <Notice tone="info">
            <span data-testid="rate-no-ticket">
              {rateNotice(inputs.instrumentId, precision?.name ?? null)}
            </span>
          </Notice>
        </div>
      </section>
    );
  }

  const remainingMs = state.preview ? state.preview.expires_at_ms - now : 0;
  const confirmLabel =
    state.preview && precision
      ? describeAction(state.preview.normalized, precision)
      : 'Confirm order';
  const liveOrder = state.preview?.normalized.mode === 'LIVE';
  const lot = precision ? boardLotOf(precision) : null;
  const lotMode = oddLot ? 'odd' : 'board';
  const sharesHint = (() => {
    if (!equity || !precision) return undefined;
    if (built.quantity && !built.quantity.isZero())
      return `= ${formatShares(precision, built.quantity)}`;
    if (lot && lotMode === 'board')
      return `1 張 = ${groupedShares(lot)} shares`;
    if (lot)
      return `Odd lot: 1 to ${groupedShares(lot.sub(Decimal.parse('1')))} shares`;
    return 'Whole shares';
  })();
  const priceHint = (() => {
    if (!equity || !precision) return undefined;
    const at = built.limitPrice ?? built.triggerPrice ?? meta.mark;
    const parts: string[] = [];
    if (at) {
      const tick = tickAt(precision, at);
      parts.push(
        `Tick ${tick.normalize().toString()}${currency ? ` ${currency}` : ''} at this price${(precision.tick_ladder ?? []).length > 0 ? ` (${precision.listing_exchange ?? 'exchange'} ladder)` : ''}`
      );
    }
    const band = priceLimitBand(precision, reference);
    if (band)
      parts.push(
        `today's limit ${formatEquityPrice(precision, band.lower)} – ${formatEquityPrice(precision, band.upper)} (±${band.pct.toString()}% of ${formatEquityPrice(precision, band.reference)})`
      );
    return parts.length > 0 ? parts.join('; ') : undefined;
  })();

  const accountOptionLabel = (a: AccountSummary) => {
    const mismatch = accountInstrumentMismatch(
      a,
      inputs.instrumentId,
      precision
    );
    // The broker name is in the status line; keep the option short.
    const venue = tradingVenueName(a.venue).split(' ')[0] ?? '';
    const named = a.venue === 'mock' || a.label.includes(venue);
    return `${a.label}${named ? '' : ` · ${venue}`}${mismatch ? ' (not for this instrument)' : ''}`;
  };

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
        {replay && (
          <Notice tone="warning">
            The active chart is in bar replay. Trading is off until you go back
            to live data.
          </Notice>
        )}
        {amend && !replay && <AmendPanel ctx={ctx} runtime={runtime} />}
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
            {!accountList.length && <option value="">No accounts</option>}
            {accountList.map(a => (
              <option key={a.account_id} value={a.account_id}>
                {accountOptionLabel(a)}
              </option>
            ))}
          </select>,
          account && account.venue !== 'mock' ? (
            <AccountStatusLine account={account} />
          ) : undefined
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
                onClick={() => {
                  if (m.mode === 'LIVE' && !liveArmed) {
                    setArmReason(null);
                    setArming(true);
                    return;
                  }
                  setArming(false);
                  edit({mode: m.mode as OrderMode});
                }}
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
          {inputs.mode === 'LIVE' && liveArmed && account && (
            <span className={styles.armed} data-testid="live-armed">
              <span className="j-badge j-badge--ask">Live armed</span>
              <span className="j-field__hint" data-testid="live-armed-until">
                {account.label} until{' '}
                {new Date(armedUntil).toISOString().slice(11, 16)} UTC
              </span>
              <button
                type="button"
                className="j-btn j-btn--sm j-btn--ghost"
                disabled={busy}
                onClick={() => void disarm(account)}
              >
                Disarm
              </button>
            </span>
          )}
        </div>
        {arming && account && (
          <ArmLivePanel
            account={account}
            client={ctx.clients.execution}
            demo={ctx.clients.demo}
            reason={armReason}
            onArmed={summary => {
              rememberArming(
                account.account_id,
                summary.live_armed_until_ms ?? null
              );
              setArming(false);
              setArmReason(null);
              edit({mode: 'LIVE'});
              accounts.refresh();
            }}
            onCancel={() => {
              setArming(false);
              setArmReason(null);
            }}
          />
        )}
        {hoursNotice && (
          <div data-testid="market-hours">
            <Notice tone={hoursNotice.tone}>{hoursNotice.text}</Notice>
          </div>
        )}
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
          {orderTypes.map(option => (
            <button
              key={option.type}
              type="button"
              aria-pressed={inputs.orderType === option.type}
              disabled={busy || (oddLot && option.type !== 'limit')}
              title={
                oddLot && option.type !== 'limit'
                  ? 'Intraday odd lots are limit orders only'
                  : undefined
              }
              onClick={() => edit({orderType: option.type})}
            >
              {option.label}
            </button>
          ))}
        </div>
        {errorsFor('orderType').map(issue => (
          <span
            key={issue.message}
            className="j-field__error"
            data-testid="order-type-error"
          >
            {issue.message}
          </span>
        ))}
        {equity ? (
          <>
            {lot && (
              <div className={styles.lots}>
                <span className="j-label" id={fieldId('lot-label')}>
                  Lot
                </span>
                <div
                  className="j-segmented"
                  role="group"
                  aria-labelledby={fieldId('lot-label')}
                  data-testid="lot-mode"
                >
                  <button
                    type="button"
                    aria-pressed={lotMode === 'board'}
                    disabled={busy}
                    onClick={() => edit({lotMode: 'board', quantity: ''})}
                  >
                    Board lot (張)
                  </button>
                  <button
                    type="button"
                    aria-pressed={lotMode === 'odd'}
                    disabled={busy || !oddAvailable}
                    title={
                      oddAvailable
                        ? undefined
                        : 'This account cannot send intraday odd-lot orders'
                    }
                    onClick={() =>
                      edit({
                        lotMode: 'odd',
                        quantity: '',
                        orderType: 'limit',
                        timeInForce: 'DAY',
                      })
                    }
                  >
                    Odd lot (零股)
                  </button>
                </div>
                <span className="j-field__hint" data-testid="lot-hint">
                  {oddLot
                    ? 'Intraday odd lots are limit orders, DAY (ROD) only.'
                    : oddAvailable
                      ? 'Board lots take any order type; odd lots are limit DAY only.'
                      : 'This account trades board lots only.'}
                </span>
              </div>
            )}
            {field(
              'quantity',
              lot && lotMode === 'board'
                ? `Quantity (張, ${groupedShares(lot)} shares each)`
                : 'Quantity (shares)',
              text('quantity', lot && lotMode === 'board' ? '1' : '100'),
              sharesHint
            )}
            {needsLimitPrice(inputs.orderType) &&
              field(
                'limitPrice',
                `Limit price${currency ? ` (${currency})` : ''}`,
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
                    Use last
                  </button>
                </div>,
                priceHint
              )}
            {needsTriggerPrice(inputs.orderType) &&
              field(
                'triggerPrice',
                `Trigger price${currency ? ` (${currency})` : ''}`,
                text('triggerPrice'),
                priceHint
              )}
            {field(
              'timeInForce',
              'Time in force',
              <select
                id={fieldId('timeInForce')}
                className="j-select"
                value={inputs.timeInForce}
                disabled={busy}
                onChange={e =>
                  edit({timeInForce: e.target.value as TimeInForce})
                }
              >
                {tifChoice.options.map(tif => (
                  <option key={tif} value={tif}>
                    {tif} · {TIME_IN_FORCE_NAMES[tif]}
                  </option>
                ))}
              </select>,
              tifChoice.note ?? undefined
            )}
            {rth.shown && (
              <div className="j-field">
                <label className="j-check">
                  <input
                    type="checkbox"
                    checked={inputs.outsideRth === true}
                    disabled={busy || !rth.enabled}
                    data-testid="outside-rth"
                    onChange={e => edit({outsideRth: e.target.checked})}
                  />
                  Allow outside regular trading hours (IBKR)
                </label>
                <span className={styles.describe}>
                  {errorsFor('outsideRth').map(issue => (
                    <span key={issue.message} className="j-field__error">
                      {issue.message}
                    </span>
                  ))}
                  {errorsFor('outsideRth').length === 0 && (
                    <span className="j-field__hint">
                      {rth.reason ??
                        'Limit orders may fill in pre-market and after-hours sessions.'}
                    </span>
                  )}
                </span>
              </div>
            )}
          </>
        ) : (
          <>
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
                        timeInForce: e.target.value as TimeInForce,
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
              {field(
                'takeProfit',
                'Take-profit',
                text('takeProfit', 'Optional')
              )}
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
                  onClick={() =>
                    riskQty && edit({quantity: riskQty.toString()})
                  }
                >
                  {riskQty && precision
                    ? `Use ${formatStep(riskQty.toString(), precision.lot_size)} ${base}`
                    : 'Set a stop-loss first'}
                </button>
              </div>
              <span className="j-field__hint">
                Uses account equity and the stop distance; the server still
                checks every limit.
              </span>
            </details>
          </>
        )}
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
                {equity && precision
                  ? formatShares(
                      precision,
                      Decimal.parse(state.preview.normalized.quantity_base)
                    )
                  : precision
                    ? `${formatStep(
                        state.preview.normalized.quantity_base,
                        precision.lot_size
                      )} ${base}`
                    : `${state.preview.normalized.quantity_base} ${base}`}
              </dd>
              {state.preview.normalized.limit_price && (
                <>
                  <dt>Limit</dt>
                  <dd className="j-tabular">
                    {equity && precision
                      ? formatEquityPrice(
                          precision,
                          Decimal.parse(state.preview.normalized.limit_price)
                        )
                      : precision
                        ? formatStep(
                            state.preview.normalized.limit_price,
                            precision.tick_size
                          )
                        : state.preview.normalized.limit_price}
                  </dd>
                </>
              )}
              {equity && (
                <>
                  <dt>Time in force</dt>
                  <dd>{state.preview.normalized.time_in_force}</dd>
                </>
              )}
              <dt>Notional</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.notional, 2)}
                {equity && currency ? ` ${currency}` : ''}
              </dd>
              <dt>Estimated fee</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.estimated_fee, 4)}
              </dd>
              <dt>{equity ? 'Cash required' : 'Margin required'}</dt>
              <dd className="j-tabular">
                {formatFixed(state.preview.margin_required, 2)}
              </dd>
              <dt>Estimated slippage</dt>
              <dd className="j-tabular">
                {state.preview.estimated_slippage_pct
                  ? `${state.preview.estimated_slippage_pct}%`
                  : 'None for resting orders'}
              </dd>
              <dt>{equity ? 'Last' : 'Mark'}</dt>
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
              disabled={busy || replay}
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
                replay ||
                (liveOrder && !liveArmed) ||
                (!canConfirm(state, now) &&
                  !(state.phase === 'FAILED' && state.idempotencyKey !== null))
              }
              aria-busy={state.phase === 'SUBMITTING'}
              onClick={() => {
                if (liveOrder && state.phase === 'PREVIEWED') {
                  setLiveConfirm(true);
                  return;
                }
                void runtime.controller.confirm();
              }}
            >
              {state.phase === 'SUBMITTING' || state.phase === 'CONFIRMING'
                ? 'Submitting…'
                : liveOrder
                  ? 'Review LIVE order…'
                  : confirmLabel}
            </button>
          </>
        )}
      </footer>
      {liveConfirm && state.preview && account && precision && (
        <LiveConfirmDialog
          preview={state.preview}
          instrument={precision}
          account={account}
          busy={state.phase === 'SUBMITTING' || state.phase === 'CONFIRMING'}
          onCancel={() => setLiveConfirm(false)}
          onConfirm={() => {
            setLiveConfirm(false);
            void runtime.controller.confirm();
          }}
        />
      )}
    </section>
  );
}

/**
 * Groups a share count with thousands separators.
 * @param shares Shares.
 * @return Text such as `1,000`.
 */
function groupedShares(shares: Decimal): string {
  return formatFixed(shares.toString(), 0);
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
      setError(
        err instanceof ApiError && err.body.code === 'LIVE_NOT_ARMED'
          ? 'Live trading is not armed for this account. Arm it in the order ticket (Live) before amending LIVE orders.'
          : errorMessage(err)
      );
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
