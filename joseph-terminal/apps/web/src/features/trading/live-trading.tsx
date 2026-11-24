/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Live trading steps of the order ticket: arming LIVE for
 * one account and the dedicated LIVE confirmation.
 *
 * Arming is offered only when the execution engine reports LIVE in the
 * account's modes with trading capabilities AVAILABLE (`modeAvailability`);
 * nothing here can grant LIVE by itself. Arming is server-side: the panel
 * sends the step-up code, the risk acknowledgement and the chosen lifetime
 * to `POST /v1/accounts/{account_id}/live-consent` (the same step-up
 * verification as a deployment's AUTO_LIVE consent) and the engine answers
 * with the account summary whose `live_armed_until_ms` is the end of the
 * arming. Until then the engine blocks manual LIVE orders on the account
 * with `LIVE_NOT_ARMED` (risk-reducing orders still pass), and it re-checks
 * every LIVE order (account permissions, risk limits, kill switches).
 */

'use client';
import {
  LIVE_ARM_DEFAULT_MS,
  LIVE_ARM_DURATIONS,
  liveArmDurationLabel,
  liveArmRequest,
  liveArmText,
  liveConfirmation,
  validStepUpCode,
  type LiveInstrument,
} from '@joseph/chart-trading';
import type {
  AccountSummary,
  ExecutionClient,
  OrderPreview,
} from '@joseph/contracts';
import {DEMO_STEP_UP_CODE} from '@joseph/mock-provider';
import {useId, useState, type ReactNode} from 'react';
import styles from './trading.module.css';
import {errorMessage} from './ui/hooks';
import {ConfirmDialog, Notice} from './ui/primitives';

/** Props of `ArmLivePanel`. */
export interface ArmLivePanelProps {
  /** Account to arm. */
  account: AccountSummary;
  /** Execution client (verifies the step-up code and arms the account). */
  client: ExecutionClient;
  /** Whether the clients are the DEMO simulation (shows the DEMO code). */
  demo?: boolean;
  /** Why arming is asked again (e.g. the engine reported LIVE_NOT_ARMED). */
  reason?: string | null;
  /**
   * Called once the engine armed the account.
   * @param summary Account summary carrying `live_armed_until_ms`.
   */
  onArmed: (summary: AccountSummary) => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Message for a refused arming.
 * @param err Thrown value.
 * @return Message.
 */
function armError(err: unknown): string {
  const code =
    err && typeof err === 'object' && 'body' in err
      ? (err as {body?: {code?: unknown}}).body?.code
      : null;
  if (code === 'STEP_UP_REQUIRED') {
    return 'The execution engine did not accept the step-up code. Enter the current code from your authenticator app.';
  }
  return errorMessage(err);
}

/**
 * Consent step before LIVE: names the account and broker, requires an
 * explicit acknowledgement, a lifetime and a step-up code that the
 * execution engine verifies before it arms the account.
 * @param props Props.
 * @return Panel.
 */
export function ArmLivePanel({
  account,
  client,
  demo = false,
  reason = null,
  onArmed,
  onCancel,
}: ArmLivePanelProps): ReactNode {
  const uid = useId();
  const [agreed, setAgreed] = useState(false);
  const [code, setCode] = useState('');
  const [duration, setDuration] = useState(LIVE_ARM_DEFAULT_MS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = agreed && validStepUpCode(code) && !busy;
  const arm = async () => {
    setError(null);
    setBusy(true);
    try {
      const summary = await client.grantAccountLiveConsent(
        account.account_id,
        liveArmRequest(code, duration)
      );
      if (summary.live_armed_until_ms == null) {
        setError('The execution engine did not arm this account.');
        return;
      }
      onArmed(summary);
    } catch (err) {
      setError(armError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className={styles.arm}
      role="group"
      aria-label="Arm live trading"
      data-testid="arm-live"
    >
      <strong className="j-neg">Arm live trading</strong>
      {reason && (
        <div data-testid="arm-live-reason">
          <Notice tone="warning">{reason}</Notice>
        </div>
      )}
      <p className={styles.meta}>{liveArmText(account)}</p>
      <label className="j-check">
        <input
          type="checkbox"
          checked={agreed}
          onChange={e => void setAgreed(e.target.checked)}
        />
        I understand that orders from this ticket use real money in{' '}
        {account.label}.
      </label>
      <div className="j-field">
        <label className="j-label" htmlFor={`${uid}-duration`}>
          Arm for
        </label>
        <select
          id={`${uid}-duration`}
          className="j-select"
          value={duration}
          disabled={busy}
          onChange={e => void setDuration(Number(e.target.value))}
        >
          {LIVE_ARM_DURATIONS.map(option => (
            <option key={option.ms} value={option.ms}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="j-field">
        <label className="j-label" htmlFor={`${uid}-step-up`}>
          Step-up code
        </label>
        <input
          id={`${uid}-step-up`}
          className="j-input j-tabular"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          value={code}
          disabled={busy}
          onChange={e => void setCode(e.target.value)}
        />
        <span className="j-field__hint">
          {demo
            ? `DEMO rehearsal: the simulated execution engine accepts the step-up code ${DEMO_STEP_UP_CODE}.`
            : 'The 6 to 8 digit code from your authenticator app. The execution engine verifies it; the terminal never sees your secret.'}
        </span>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className={styles.inline}>
        <button type="button" className="j-btn j-btn--ghost" onClick={onCancel}>
          Stay in paper
        </button>
        <button
          type="button"
          className="j-btn j-btn--ask"
          disabled={!ready}
          data-testid="arm-live-confirm"
          onClick={() => void arm()}
        >
          {busy
            ? 'Verifying…'
            : `Arm live for ${liveArmDurationLabel(duration)}`}
        </button>
      </div>
    </div>
  );
}

/** Props of `LiveConfirmDialog`. */
export interface LiveConfirmDialogProps {
  /** Previewed order. */
  preview: OrderPreview;
  /** Instrument. */
  instrument: LiveInstrument;
  /** Account. */
  account: AccountSummary;
  /** Whether submission is running. */
  busy: boolean;
  /** Sends the order. */
  onConfirm: () => void;
  /** Closes without sending. */
  onCancel: () => void;
}

/**
 * Dedicated LIVE confirmation naming LIVE money, the broker, the account,
 * the exact action, the quantity (shares and Taiwan lots), price and currency.
 * @param props Props.
 * @return Dialog.
 */
export function LiveConfirmDialog({
  preview,
  instrument,
  account,
  busy,
  onConfirm,
  onCancel,
}: LiveConfirmDialogProps): ReactNode {
  const [checked, setChecked] = useState(false);
  const content = liveConfirmation(
    preview.normalized,
    instrument,
    account,
    preview.notional
  );
  return (
    <ConfirmDialog
      title={content.title}
      confirmLabel={content.confirmLabel}
      tone="ask"
      busy={busy}
      confirmDisabled={!checked}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <div data-testid="live-confirmation" className={styles.live}>
        <Notice tone="danger">{content.warning}</Notice>
        <dl className={styles.facts}>
          {content.rows.map(([label, value]) => (
            <div key={label} className={styles.factRow}>
              <dt>{label}</dt>
              <dd className="j-tabular">{value}</dd>
            </div>
          ))}
        </dl>
        <label className="j-check">
          <input
            type="checkbox"
            checked={checked}
            onChange={e => void setChecked(e.target.checked)}
          />
          Send this order with real money
        </label>
      </div>
    </ConfirmDialog>
  );
}
