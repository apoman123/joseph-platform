/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart context-menu contribution ("Buy limit at X", ...).
 * Choosing an item only prefills the order ticket; nothing is sent until
 * the user previews and confirms.
 */

'use client';
import {ticketFromMenuItem, type ContextMenuItem} from '@joseph/chart-trading';
import type {ReactNode} from 'react';
import {useStore, type WebFeatureContext} from '../../shell/react-bindings';
import styles from './trading.module.css';
import type {TradingRuntime} from './trading-runtime';

/** Props of the context menu. */
export interface TradingContextMenuProps {
  /** Feature context. */
  ctx: WebFeatureContext;
  /** Trading runtime. */
  runtime: TradingRuntime;
}

/** Command id the shell may implement to reveal a side-panel tab. */
export const SHOW_PANEL_COMMAND = 'shell.showPanel';

/**
 * Prefills the ticket from a menu item, selects the clicked chart's
 * instrument and, when the shell offers `SHOW_PANEL_COMMAND`, reveals the
 * ticket tab.
 * @param ctx Feature context.
 * @param runtime Trading runtime.
 * @param instrumentId Instrument of the clicked chart.
 * @param item Chosen item.
 */
export function applyMenuItem(
  ctx: WebFeatureContext,
  runtime: TradingRuntime,
  instrumentId: string,
  item: ContextMenuItem
): void {
  if (ctx.selection.get().instrumentId !== instrumentId) {
    ctx.select({instrumentId});
  }
  const current = runtime.controller.get().inputs;
  runtime.controller.dispatch({
    type: 'replaceInputs',
    inputs: ticketFromMenuItem(item, {...current, instrumentId}),
  });
  runtime.menu.set(null);
  if (ctx.commands.list().some(c => c.id === SHOW_PANEL_COMMAND)) {
    void ctx.commands.execute(SHOW_PANEL_COMMAND, {
      slot: 'side-panel',
      id: 'trading.ticket',
    });
  }
}

/**
 * Renders trading items for the latest chart right-click.
 * @param props Feature context and trading runtime.
 * @return Menu items, a hint when the instrument cannot be traded (not in
 *     the registry), or nothing when no menu is open.
 */
export function TradingContextMenu({
  ctx,
  runtime,
}: TradingContextMenuProps): ReactNode {
  const menu = useStore(runtime.menu);
  if (!menu) return null;
  if (menu.items.length === 0) {
    return (
      <p className={styles.menuHint}>
        Trading is unavailable for this instrument.
      </p>
    );
  }
  return (
    <div
      className={styles.menu}
      role="group"
      aria-label="Trade at price"
      data-testid="trading-context-menu"
    >
      {menu.items.map(item => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          className={`j-menu__item ${styles.menuItem}`}
          onClick={() =>
            void applyMenuItem(ctx, runtime, menu.instrumentId, item)
          }
        >
          <span className={item.side === 'buy' ? 'j-pos' : 'j-neg'}>
            {item.label}
          </span>
          {item.hint && <span className={styles.menuHint}>{item.hint}</span>}
        </button>
      ))}
    </div>
  );
}
