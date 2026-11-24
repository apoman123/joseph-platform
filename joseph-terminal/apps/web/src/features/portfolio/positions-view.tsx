/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Positions with reduce-only close and a two-step reverse
 * (close → confirmed flat → open). Each step is previewed and confirmed
 * separately; nothing assumes venue atomicity.
 */

'use client';
import {
  groupThousands,
  planClose,
  randomKey,
  reverseCloseSubmitted,
  reversePositionUpdated,
  startReverse,
  type ReversePlan,
} from '@joseph/chart-trading';
import type {
  OrderIntentRequest,
  OrderPreview,
  Position,
} from '@joseph/contracts';
import {useEffect, useState, type ReactNode} from 'react';
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

/** Props of the positions view. */
export interface PositionsViewProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Portfolio runtime. */
  runtime: PortfolioRuntime;
}

/**
 * Formats an optional decimal with grouping.
 * @param value Decimal string or null.
 * @return Text.
 */
function num(value: string | null | undefined): string {
  return value ? groupThousands(value) : '—';
}

/**
 * Returns the smallest step matching a decimal's precision (`0.020` → `0.001`).
 * @param value Decimal string.
 * @return Step string.
 */
export function stepOf(value: string): string {
  const fraction = value.split('.')[1] ?? '';
  return fraction.length ? `0.${'0'.repeat(fraction.length - 1)}1` : '1';
}

/**
 * Lists the outcome of every preview risk check.
 * @param props Preview.
 * @return List element.
 */
function PreviewChecks({preview}: {preview: OrderPreview}): ReactNode {
  return (
    <ul className={styles.checks} aria-label="Risk checks">
      {preview.risk.checks.map(check => (
        <li key={check.check}>
          <span
            className={`j-badge ${check.outcome === 'FAIL' ? 'j-badge--stale' : check.outcome === 'PASS' ? 'j-badge--live' : 'j-badge--sodium'}`}
          >
            {check.outcome.toLowerCase()}
          </span>{' '}
          {check.message}
        </li>
      ))}
    </ul>
  );
}

/**
 * Dialog that previews and confirms a reduce-only close.
 * @param props Context, runtime, position and close handler.
 * @return Dialog element.
 */
function CloseDialog({
  ctx,
  runtime,
  position,
  onDone,
}: PositionsViewProps & {position: Position; onDone: () => void}): ReactNode {
  const [fraction, setFraction] = useState('1');
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const symbol = position.instrument_id.split(':')[2] ?? '';
  const plan = planClose(
    position,
    fraction,
    'market',
    stepOf(position.quantity_base),
    symbol
  );
  const load = async (next: string) => {
    setFraction(next);
    setPreview(null);
    setKey(null);
    setError(null);
    try {
      setPreview(
        await ctx.clients.execution.closePositionPreview(position.position_id, {
          fraction: next,
          order_type: 'market',
        })
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  useEffect(() => {
    void load('1');
    // Load the full-close preview once when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const confirm = async () => {
    if (!preview) return;
    const idempotency = key ?? randomKey();
    setKey(idempotency);
    setBusy(true);
    setError(null);
    try {
      const result = await ctx.clients.execution.closePosition(
        position.position_id,
        {fraction, order_type: 'market', preview_token: preview.preview_token},
        idempotency
      );
      ctx.notifier.notify(
        result.orders.length
          ? 'Close sent as a reduce-only order. The position updates when the venue fills it.'
          : 'The close was blocked by risk checks.',
        result.orders.length ? 'success' : 'error'
      );
      void runtime.refresh();
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const summary = 'error' in plan ? plan.error : plan.summary;
  return (
    <ConfirmDialog
      title="Close position"
      confirmLabel={'error' in plan ? 'Close position' : summary}
      tone="ask"
      busy={busy}
      confirmDisabled={!preview?.allowed}
      onCancel={onDone}
      onConfirm={() => void confirm()}
    >
      <p>
        <InstrumentLabel instrumentId={position.instrument_id} />{' '}
        <SideText side={position.side} /> {position.quantity_base}
      </p>
      <div className="j-segmented" role="group" aria-label="Amount to close">
        {[
          ['0.25', '25%'],
          ['0.5', '50%'],
          ['0.75', '75%'],
          ['1', '100%'],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={fraction === value}
            onClick={() => void load(value ?? '1')}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="j-muted">
        Reduce-only market order. It can only shrink this position, never flip
        it.
      </p>
      {preview && <PreviewChecks preview={preview} />}
      {error && <Notice tone="danger">{error}</Notice>}
    </ConfirmDialog>
  );
}

/**
 * Two-step reverse dialog.
 * @param props Context, runtime, position and close handler.
 * @return Dialog element.
 */
function ReverseDialog({
  ctx,
  runtime,
  position,
  onDone,
}: PositionsViewProps & {position: Position; onDone: () => void}): ReactNode {
  const state = useStore(runtime.state);
  const started = startReverse(position);
  const [plan, setPlan] = useState<ReversePlan | null>(
    'error' in started ? null : started
  );
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    'error' in started ? started.error : null
  );
  const [keys] = useState(() => ({close: randomKey(), open: randomKey()}));

  useEffect(() => {
    if (!plan || plan.step !== 'AWAITING_FLAT') return;
    const latest =
      state.positions.find(p => p.instrument_id === plan.instrumentId) ?? null;
    const next = reversePositionUpdated(plan, latest);
    if (next !== plan) setPlan(next);
  }, [plan, state.positions]);

  const openRequest = (current: ReversePlan): OrderIntentRequest => ({
    account_id: position.account_id,
    instrument_id: current.instrumentId,
    side: current.fromSide === 'long' ? 'sell' : 'buy',
    position_effect: 'open',
    order_type: 'market',
    quantity_base: current.quantity,
    time_in_force: 'IOC',
    reduce_only: false,
    mode: position.mode,
    source: 'MANUAL_TICKET',
  });

  useEffect(() => {
    let alive = true;
    setPreview(null);
    if (!plan) return;
    const load =
      plan.step === 'CLOSE'
        ? ctx.clients.execution.closePositionPreview(position.position_id, {
            fraction: '1',
            order_type: 'market',
          })
        : plan.step === 'OPEN'
          ? ctx.clients.execution.previewOrder(openRequest(plan))
          : null;
    load?.then(
      p => alive && void setPreview(p),
      (err: unknown) => alive && void setError(errorMessage(err))
    );
    return () => {
      alive = false;
    };
    // Re-preview only when the step changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan?.step]);

  const confirm = async () => {
    if (!plan || !preview) return;
    setBusy(true);
    setError(null);
    try {
      if (plan.step === 'CLOSE') {
        await ctx.clients.execution.closePosition(
          position.position_id,
          {
            fraction: '1',
            order_type: 'market',
            preview_token: preview.preview_token,
          },
          keys.close
        );
        setPlan(reverseCloseSubmitted(plan));
      } else if (plan.step === 'OPEN') {
        await ctx.clients.execution.submitIntent(
          {...preview.normalized, preview_token: preview.preview_token},
          keys.open
        );
        setPlan({
          ...plan,
          step: 'DONE',
          message:
            'Both steps were sent. Check Orders and Positions for the venue state.',
        });
        ctx.notifier.notify('Reverse completed in two steps.', 'success');
      }
      void runtime.refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const oppositeSide = position.side === 'long' ? 'short' : 'long';
  const label =
    plan?.step === 'CLOSE'
      ? `Step 1: close ${position.side} ${position.quantity_base}`
      : plan?.step === 'OPEN'
        ? `Step 2: open ${oppositeSide} ${plan.quantity}`
        : plan?.step === 'DONE'
          ? 'Done'
          : 'Waiting for flat';
  return (
    <ConfirmDialog
      title="Reverse position"
      confirmLabel={label}
      tone={
        plan?.step === 'OPEN'
          ? oppositeSide === 'long'
            ? 'bid'
            : 'ask'
          : 'ask'
      }
      busy={busy}
      confirmDisabled={
        !preview?.allowed ||
        plan?.step === 'AWAITING_FLAT' ||
        plan?.step === 'DONE'
      }
      onCancel={onDone}
      onConfirm={() => void confirm()}
    >
      <p>
        <InstrumentLabel instrumentId={position.instrument_id} />{' '}
        <SideText side={position.side} /> {position.quantity_base}
      </p>
      <p data-testid="reverse-step">{plan?.message}</p>
      <p className="j-muted">
        Venues do not reverse atomically, so the opposite order is only offered
        after the position is confirmed flat.
      </p>
      {preview && plan?.step !== 'DONE' && <PreviewChecks preview={preview} />}
      {error && <Notice tone="danger">{error}</Notice>}
    </ConfirmDialog>
  );
}

/**
 * Renders the positions table.
 * @param props Props.
 * @return Element.
 */
export function PositionsView({ctx, runtime}: PositionsViewProps): ReactNode {
  const state = useStore(runtime.state);
  const [closing, setClosing] = useState<Position | null>(null);
  const [reversing, setReversing] = useState<Position | null>(null);
  return (
    <div className={styles.view}>
      {state.error && (
        <Notice tone="danger">
          Positions could not be refreshed: {state.error}
        </Notice>
      )}
      {state.loaded && state.positions.length === 0 ? (
        <EmptyState title="No open positions">
          Filled orders open positions here, with entry, mark and unrealized
          PnL.
        </EmptyState>
      ) : (
        <table className="j-table" aria-label="Positions">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Side</th>
              <th className="j-num">Qty</th>
              <th className="j-num">Entry</th>
              <th className="j-num">Mark</th>
              <th className="j-num">Unrealized</th>
              <th className="j-num">Liq. (est.)</th>
              <th>Mode</th>
              <th>
                <span className={styles.srOnly}>Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {state.positions.map(position => (
              <tr
                key={position.position_id}
                data-testid={`position-${position.instrument_id}`}
                className={position.stale ? 'j-row--muted' : undefined}
              >
                <td>
                  <InstrumentLabel instrumentId={position.instrument_id} />
                  {position.stale && (
                    <span className="j-badge j-badge--stale"> stale</span>
                  )}
                </td>
                <td>
                  <SideText side={position.side} />
                </td>
                <td className="j-num">{num(position.quantity_base)}</td>
                <td className="j-num">{num(position.entry_price)}</td>
                <td className="j-num">{num(position.mark_price)}</td>
                <td
                  className={`j-num ${position.unrealized_pnl?.startsWith('-') ? 'j-neg' : 'j-pos'}`}
                >
                  {num(position.unrealized_pnl)}
                </td>
                <td className="j-num">{num(position.liquidation_price)}</td>
                <td>
                  <ModeBadge mode={position.mode} />
                </td>
                <td className={styles.actions}>
                  <button
                    type="button"
                    className="j-btn j-btn--sm"
                    onClick={() => void setClosing(position)}
                  >
                    Close
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--sm j-btn--ghost"
                    onClick={() => void setReversing(position)}
                  >
                    Reverse
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {/* Dialogs stay mounted when the position goes flat (reverse step 2). */}
      {closing && (
        <CloseDialog
          ctx={ctx}
          runtime={runtime}
          position={closing}
          onDone={() => void setClosing(null)}
        />
      )}
      {reversing && (
        <ReverseDialog
          ctx={ctx}
          runtime={runtime}
          position={reversing}
          onDone={() => void setReversing(null)}
        />
      )}
    </div>
  );
}
