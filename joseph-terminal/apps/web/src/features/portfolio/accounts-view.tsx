/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Accounts tab: every trading account the execution engine
 * reports — paper accounts, crypto venue accounts and securities broker
 * accounts (IBKR, SinoPac, Cathay) — with connection, server-side
 * credential and trading status, the exchanges each account routes to,
 * permitted modes, equity, settled and unsettled cash and the engine's
 * notes. Read-only: broker credentials live on the execution server
 * (secret files) and are never entered in the browser; connecting or
 * enabling live trading happens on the execution engine.
 */

'use client';
import {
  accountExchanges,
  accountStatusView,
  formatFixed,
  formatUtcDateTime,
} from '@joseph/chart-trading';
import type {AccountSummary} from '@joseph/contracts';
import type {ReactNode} from 'react';
import {useStore} from '../../shell/react-bindings';
import {AccountStatusBadge} from '../trading/ui/account-status';
import {EmptyState, Notice} from '../trading/ui/primitives';
import type {ActivityViewProps} from './activity-views';
import styles from './portfolio.module.css';

/** Display names of exchange venues. */
const EXCHANGE_NAMES: Readonly<Record<string, string>> = {
  twse: 'TWSE',
  tpex: 'TPEx',
  nasdaq: 'Nasdaq',
  nyse: 'NYSE',
  cboe: 'Cboe BZX',
};

/**
 * Formats an optional amount in the account currency.
 * @param account Account.
 * @param value Decimal string or null.
 * @return Text or an em dash.
 */
function money(account: AccountSummary, value: string | null | undefined) {
  return value
    ? `${formatFixed(value, 2).replace('-', '−')} ${account.currency}`
    : '—';
}

/**
 * What an account trades: its exchanges, crypto on its own venue, or both
 * for the USD paper account.
 * @param account Account.
 * @return Text such as `Nasdaq, NYSE, Cboe BZX` or `Crypto, Nasdaq, NYSE,
 *     Cboe BZX`.
 */
function tradesText(account: AccountSummary): string {
  const exchanges = accountExchanges(account).map(
    venue => EXCHANGE_NAMES[venue] ?? venue
  );
  const crypto =
    account.venue !== 'ibkr' &&
    account.venue !== 'sinopac' &&
    account.venue !== 'cathay' &&
    (account.venue !== 'mock' || account.currency === 'USD');
  return [...(crypto ? ['Crypto'] : []), ...exchanges].join(', ') || '—';
}

/**
 * Renders the accounts list.
 * @param props Props.
 * @return Element.
 */
export function AccountsView({runtime}: ActivityViewProps): ReactNode {
  const state = useStore(runtime.state);
  if (state.loaded && state.accounts.length === 0) {
    return (
      <EmptyState title="No trading accounts">
        Accounts connected on the execution engine appear here: paper accounts,
        crypto venues and brokers (Interactive Brokers, SinoPac, Cathay).
      </EmptyState>
    );
  }
  return (
    <div className={styles.view}>
      {state.error && <Notice tone="danger">{state.error}</Notice>}
      <table className="j-table" aria-label="Accounts">
        <thead>
          <tr>
            <th>Account</th>
            <th>Broker or venue</th>
            <th>Trades</th>
            <th>Connection</th>
            <th>Credentials</th>
            <th>Modes</th>
            <th className="j-num">Equity</th>
            <th className="j-num">Settled cash</th>
            <th className="j-num">Unsettled</th>
            <th>Last sync</th>
          </tr>
        </thead>
        <tbody>
          {state.accounts.map(account => {
            const view = accountStatusView(account);
            return [
              <tr
                key={account.account_id}
                data-testid={`account-row-${account.account_id}`}
              >
                <td>
                  <strong>{account.label}</strong>
                  {view.simulation && (
                    <span className="j-badge j-badge--muted"> Simulation</span>
                  )}
                </td>
                <td>{view.venueName}</td>
                <td>{tradesText(account)}</td>
                <td>
                  <AccountStatusBadge account={account} />
                </td>
                <td data-testid="account-credentials">
                  {view.awaitingSdk
                    ? 'No trading API yet'
                    : view.credentialsText}
                </td>
                <td>
                  {account.modes.length > 0
                    ? account.modes
                        .map(mode =>
                          mode === 'LIVE'
                            ? 'Live'
                            : mode.charAt(0) + mode.slice(1).toLowerCase()
                        )
                        .join(', ')
                    : 'None'}
                </td>
                <td className="j-num">{money(account, account.equity)}</td>
                <td className="j-num" data-testid="account-settled">
                  {money(account, account.settled_cash)}
                </td>
                <td className="j-num">
                  {money(account, account.unsettled_cash)}
                </td>
                <td className="j-tabular">
                  {account.last_sync_ms
                    ? formatUtcDateTime(account.last_sync_ms)
                    : '—'}
                </td>
              </tr>,
              view.notes.length > 0 ? (
                <tr key={`${account.account_id}:notes`} className={styles.note}>
                  <td colSpan={10}>{view.notes.join(' ')}</td>
                </tr>
              ) : null,
            ];
          })}
        </tbody>
      </table>
      <p className="j-muted">
        Broker credentials are configured on the execution server, never in the
        browser. Live orders need the execution engine to enable LIVE for an
        account; the ticket then asks you to arm it and confirms each LIVE
        order.
      </p>
    </div>
  );
}
