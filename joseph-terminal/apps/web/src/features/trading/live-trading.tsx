'use client';
/**
 * @fileoverview Live trading steps of the order ticket: arming LIVE for
 * one account and the dedicated LIVE confirmation.
 *
 * Arming is offered only when the execution engine reports LIVE in the
 * account's modes with trading capabilities AVAILABLE (`modeAvailability`);
 * nothing here can grant LIVE. When the engine offers account-level live
 * consent (`grantAccountLiveConsent`, the same body as the deployments'
 * `POST /v1/deployments/{id}/live-consent`: step-up code, risk
 * acknowledgement, lifetime), arming sends the step-up code there and the
 * engine verifies it. Otherwise the arm is a browser-side acknowledgement
 * and the engine still checks every LIVE order (account permissions, the
 * deployment's live switch, risk limits, kill switches).
 */
import {
  LIVE_ARM_MS,
  liveArmText,
  liveConfirmation,
  supportsLiveArming,
  validStepUpCode,
  type LiveInstrument,
} from '@joseph/chart-trading';
import type {
  AccountSummary,
  ExecutionClient,
  OrderPreview,
} from '@joseph/contracts';
import {useId, useState, type ReactNode} from 'react';
import styles from './trading.module.css';
import {errorMessage} from './ui/hooks';
import {ConfirmDialog, Notice} from './ui/primitives';

/** Props of `ArmLivePanel`. */
export interface ArmLivePanelProps {
  /** Account to arm. */
  account: AccountSummary;
  /** Execution client (may offer account-level live consent). */
  client: ExecutionClient;
  /** Clock. */
  now: () => number;
  /**
   * Called once armed.
   * @param expiresAtMs When the arm ends.
   */
  onArmed: (expiresAtMs: number) => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Consent step before LIVE: names the account and broker, requires an
 * explicit acknowledgement and, when the engine verifies one, a step-up
 * code.
 * @param props Props.
 * @return Panel.
 */
export function ArmLivePanel({
  account,
  client,
  now,
  onArmed,
  onCancel,
}: ArmLivePanelProps): ReactNode {
  const uid = useId();
  const [agreed, setAgreed] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const serverStepUp = supportsLiveArming(client);
  const ready = agreed && (!serverStepUp || validStepUpCode(code)) && !busy;
  const arm = async () => {
    setError(null);
    if (!supportsLiveArming(client)) {
      onArmed(now() + LIVE_ARM_MS);
      return;
    }
    setBusy(true);
    try {
      const grant = await client.grantAccountLiveConsent(account.account_id, {
        step_up_code: code.trim(),
        acknowledge_risk: true,
        duration_ms: LIVE_ARM_MS,
      });
      onArmed(Math.min(grant.expires_at_ms, now() + LIVE_ARM_MS));
    } catch (err) {
      setError(errorMessage(err));
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
      <p className={styles.meta}>{liveArmText(account)}</p>
      <label className="j-check">
        <input
          type="checkbox"
          checked={agreed}
          onChange={e => setAgreed(e.target.checked)}
        />
        I understand that orders from this ticket use real money in{' '}
        {account.label}.
      </label>
      {serverStepUp ? (
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
            onChange={e => setCode(e.target.value)}
          />
          <span className="j-field__hint">
            The 6 to 8 digit code from your authenticator app. The execution
            engine verifies it; the terminal never sees your secret.
          </span>
        </div>
      ) : (
        <p className={styles.meta}>
          This execution engine asks no step-up code for manual orders; it
          checks live permission on every order. Automatic live strategies need
          the step-up consent of their deployment.
        </p>
      )}
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
          {busy ? 'Verifying…' : 'Arm live for 15 minutes'}
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
 * the exact action, the quantity (shares and TWSE lots), price and currency.
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
            onChange={e => setChecked(e.target.checked)}
          />
          Send this order with real money
        </label>
      </div>
    </ConfirmDialog>
  );
}
