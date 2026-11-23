'use client';
/**
 * @fileoverview Broker and venue account status: connection, credentials
 * and the "awaiting API SDK" state, as reported by the execution engine.
 * Shared by the order ticket and the Accounts tab.
 */
import {accountStatusView} from '@joseph/chart-trading';
import type {AccountSummary} from '@joseph/contracts';
import type {ReactNode} from 'react';

/** Props of `AccountStatusBadge`. */
export interface AccountStatusBadgeProps {
  /** Account. */
  account: AccountSummary;
}

/**
 * Connection badge of an account (`Connected`, `Disconnected`, `Awaiting
 * API SDK`, ...).
 * @param props Props.
 * @return Badge.
 */
export function AccountStatusBadge({
  account,
}: AccountStatusBadgeProps): ReactNode {
  const view = accountStatusView(account);
  return (
    <span
      className={`j-badge j-badge--${view.tone}`}
      data-testid="account-connection"
      data-connection={view.connection}
    >
      {view.connectionText}
    </span>
  );
}

/**
 * One-line status under the ticket's account picker: broker, connection
 * and credentials.
 * @param props Props.
 * @return Status line.
 */
export function AccountStatusLine({
  account,
}: AccountStatusBadgeProps): ReactNode {
  const view = accountStatusView(account);
  return (
    <span className="j-field__hint" data-testid="account-status">
      {view.venueName} · <AccountStatusBadge account={account} /> ·{' '}
      {view.awaitingSdk ? 'No trading API yet' : view.credentialsText}
    </span>
  );
}
