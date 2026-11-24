/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Broker and venue account status: connection, credentials
 * (held on the execution server, never entered here), the "awaiting API
 * SDK" state and settled cash, as reported by the execution engine.
 * Shared by the order ticket and the Accounts tab.
 */

'use client';
import {accountStatusView, cashSummary} from '@joseph/chart-trading';
import type {AccountSummary} from '@joseph/contracts';
import type {ReactNode} from 'react';
import styles from '../trading.module.css';

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
  const cash = account.capabilities.cash_account ? cashSummary(account) : null;
  return (
    <span className={styles.accountStatus}>
      <span className="j-field__hint" data-testid="account-status">
        {view.venueName}
        {view.simulation ? ' (simulation)' : ''} ·{' '}
        <AccountStatusBadge account={account} /> ·{' '}
        {view.awaitingSdk ? 'No trading API yet' : view.credentialsText}
      </span>
      {cash && (
        <span className="j-field__hint j-tabular" data-testid="account-cash">
          {cash}
        </span>
      )}
    </span>
  );
}
