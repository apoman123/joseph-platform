'use client';
/**
 * @fileoverview Accounts tab: every trading account the execution engine
 * reports — crypto venue accounts and securities broker accounts (IBKR,
 * SinoPac, Cathay) — with connection, credential and trading status,
 * permitted modes, equity and the engine's notes. Read-only: connecting or
 * enabling live trading happens on the execution engine, never here.
 */
import {
  accountStatusView,
  formatFixed,
  formatUtcDateTime,
} from '@joseph/chart-trading';
import type {ReactNode} from 'react';
import {useStore} from '../../shell/react-bindings';
import {AccountStatusBadge} from '../trading/ui/account-status';
import {EmptyState, Notice} from '../trading/ui/primitives';
import type {ActivityViewProps} from './activity-views';
import styles from './portfolio.module.css';

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
        Accounts connected on the execution engine appear here: crypto venues
        and brokers (Interactive Brokers, SinoPac, Cathay).
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
            <th>Connection</th>
            <th>Credentials</th>
            <th>Modes</th>
            <th className="j-num">Equity</th>
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
                </td>
                <td>{view.venueName}</td>
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
                <td className="j-num">
                  {account.equity
                    ? `${formatFixed(account.equity, 2)} ${account.currency}`
                    : '—'}
                </td>
                <td className="j-tabular">
                  {account.last_sync_ms
                    ? formatUtcDateTime(account.last_sync_ms)
                    : '—'}
                </td>
              </tr>,
              view.notes.length > 0 ? (
                <tr key={`${account.account_id}:notes`} className={styles.note}>
                  <td colSpan={7}>{view.notes.join(' ')}</td>
                </tr>
              ) : null,
            ];
          })}
        </tbody>
      </table>
      <p className="j-muted">
        Live orders need the execution engine to enable LIVE for an account; the
        ticket then asks you to arm it and confirms each LIVE order.
      </p>
    </div>
  );
}
