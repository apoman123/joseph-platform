'use client';
/**
 * @fileoverview Small presentational primitives shared by the trading,
 * watchlist, screener, strategy, alerts and portfolio features. They only
 * compose the shared `j-` component classes.
 */
import {
  VENUE_NAMES,
  parseInstrumentId,
  type FeedState,
  type OrderMode,
  type OrderState,
  type Venue,
} from '@joseph/contracts';
import {orderStateLabel} from '@joseph/chart-trading';
import {useEffect, useRef, type ReactNode} from 'react';
import styles from './primitives.module.css';

/** Short venue codes for dense rows. */
const VENUE_CODES: Readonly<Record<Venue, string>> = {
  binance: 'BIN',
  hyperliquid: 'HL',
  lighter: 'LTR',
  bitget: 'BG',
  mexc: 'MEXC',
  twse: 'TWSE',
  tpex: 'TPEx',
  nasdaq: 'NSDQ',
  nyse: 'NYSE',
  treasury: 'UST',
  mock: 'DEMO',
};

/** Props of `InstrumentLabel`. */
export interface InstrumentLabelProps {
  /** Canonical instrument id. */
  instrumentId: string;
  /** Show the market type after the symbol. */
  showType?: boolean;
}

/**
 * Renders an exact-venue instrument: venue chip plus native symbol.
 * The same symbol on two venues always renders as two different labels.
 * @param props Props.
 * @return Label element.
 */
export function InstrumentLabel({
  instrumentId,
  showType = false,
}: InstrumentLabelProps): ReactNode {
  let venue: Venue | null = null;
  let symbol = instrumentId;
  let type = '';
  try {
    const parts = parseInstrumentId(instrumentId);
    venue = parts.venue;
    symbol = parts.venueMarketId;
    type = parts.marketType;
  } catch {
    // Non-canonical ids render verbatim.
  }
  return (
    <span className={styles.instrument} title={instrumentId}>
      {venue && (
        <span
          className={`j-badge${venue === 'mock' ? ' j-badge--demo' : ''}`}
          title={VENUE_NAMES[venue]}
        >
          {VENUE_CODES[venue]}
        </span>
      )}{' '}
      <strong>{symbol}</strong>
      {showType && type && <span className="j-muted"> {type}</span>}
    </span>
  );
}

/** Props of `FeedBadge`. */
export interface FeedBadgeProps {
  /** Provenance state. */
  state: FeedState;
  /** Optional suffix such as a data age. */
  detail?: string;
}

/**
 * Renders a provenance badge (LIVE, POLL, REPLAY, STALE, DEMO, ...).
 * @param props Props.
 * @return Badge element.
 */
export function FeedBadge({state, detail}: FeedBadgeProps): ReactNode {
  const modifier = {
    LIVE: 'live',
    POLL: 'poll',
    REPLAY: 'replay',
    STALE: 'stale',
    DEMO: 'demo',
    UNAVAILABLE: 'unavailable',
    CONNECTING: 'unavailable',
  }[state];
  const text = {
    LIVE: 'Live',
    POLL: 'Polled',
    REPLAY: 'Replay',
    STALE: 'Stale',
    DEMO: 'Demo',
    UNAVAILABLE: 'Unavailable',
    CONNECTING: 'Connecting',
  }[state];
  return (
    <span className={`j-badge j-badge--${modifier}`} data-feed-state={state}>
      {text}
      {detail ? ` ${detail}` : ''}
    </span>
  );
}

/**
 * Renders the order mode badge (live uses the danger tone).
 * @param props Props with the mode.
 * @return Badge element.
 */
export function ModeBadge({mode}: {mode: OrderMode}): ReactNode {
  const text = {PAPER: 'Paper', SHADOW: 'Shadow', LIVE: 'Live money'}[mode];
  return (
    <span
      className={`j-badge${mode === 'LIVE' ? ' j-badge--ask' : ''}`}
      data-testid="mode-badge"
    >
      {text}
    </span>
  );
}

/**
 * Renders an order state badge; unknown outcomes are never shown as canceled.
 * @param props Props with the state.
 * @return Badge element.
 */
export function OrderStateBadge({state}: {state: OrderState}): ReactNode {
  const modifier =
    state === 'FILLED'
      ? ' j-badge--live'
      : state === 'REJECTED' || state === 'FAILED'
        ? ' j-badge--stale'
        : state === 'UNKNOWN_RECONCILING' ||
            state === 'SUBMITTING' ||
            state === 'CANCEL_PENDING'
          ? ' j-badge--sodium'
          : state === 'CANCELED' || state === 'EXPIRED'
            ? ' j-badge--muted'
            : '';
  return (
    <span className={`j-badge${modifier}`} data-order-state={state}>
      {orderStateLabel(state)}
    </span>
  );
}

/**
 * Renders a direction word with the bid/ask color.
 * @param props Props with the side.
 * @return Text element.
 */
export function SideText({
  side,
}: {
  side: 'buy' | 'sell' | 'long' | 'short' | 'LONG' | 'SHORT' | 'flat';
}): ReactNode {
  const lower = side.toLowerCase();
  const positive = lower === 'buy' || lower === 'long';
  const label = lower.charAt(0).toUpperCase() + lower.slice(1);
  if (lower === 'flat') return <span className="j-muted">Flat</span>;
  return <span className={positive ? 'j-pos' : 'j-neg'}>{label}</span>;
}

/** Props of `ConfirmDialog`. */
export interface ConfirmDialogProps {
  /** Dialog title. */
  title: string;
  /** Body content. */
  children: ReactNode;
  /** Confirm button label naming the exact action. */
  confirmLabel: string;
  /** Confirm button tone. */
  tone?: 'primary' | 'bid' | 'ask';
  /** Whether the confirm action is running. */
  busy?: boolean;
  /** Whether confirming is currently allowed. */
  confirmDisabled?: boolean;
  /** Called on confirm. */
  onConfirm: () => void;
  /** Called on cancel or Escape. */
  onCancel: () => void;
}

/**
 * Modal confirmation dialog. Escape cancels; focus starts on Cancel so an
 * accidental Enter never confirms a trading action.
 * @param props Props.
 * @return Dialog element.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  tone = 'primary',
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): ReactNode {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  return (
    <div
      className={styles.backdrop}
      role="presentation"
      onKeyDown={e => e.key === 'Escape' && onCancel()}
    >
      <div
        className="j-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="j-confirm-title"
      >
        <h2 id="j-confirm-title" className={styles.title}>
          {title}
        </h2>
        <div className={styles.body}>{children}</div>
        <div className="j-dialog__actions">
          <button
            ref={cancelRef}
            type="button"
            className="j-btn j-btn--ghost"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className={`j-btn j-btn--${tone}`}
            onClick={onConfirm}
            disabled={busy || confirmDisabled}
            aria-busy={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Props of `EmptyState`. */
export interface EmptyStateProps {
  /** Headline. */
  title: string;
  /** Explanation and next step. */
  children?: ReactNode;
  /** Optional action button. */
  action?: ReactNode;
}

/**
 * Empty state inviting the next action.
 * @param props Props.
 * @return Element.
 */
export function EmptyState({
  title,
  children,
  action,
}: EmptyStateProps): ReactNode {
  return (
    <div className="j-empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

/** Props of `Notice`. */
export interface NoticeProps {
  /** Tone. */
  tone?: 'info' | 'warning' | 'danger';
  /** Content. */
  children: ReactNode;
}

/**
 * Inline notice; danger notices are announced as alerts.
 * @param props Props.
 * @return Element.
 */
export function Notice({tone = 'info', children}: NoticeProps): ReactNode {
  return (
    <div
      className={`j-notice j-notice--${tone}`}
      role={tone === 'danger' ? 'alert' : 'status'}
    >
      <div>{children}</div>
    </div>
  );
}
