/**
 * @fileoverview Portfolio feature: positions, open orders, executions,
 * order history, audit log and risk controls as bottom-panel tabs, all
 * backed by one shared read model.
 */
import type {Disposer} from '@joseph/feature-registry';
import {createElement, type ComponentType} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {
  AuditView,
  FillsView,
  RiskView,
  type ActivityViewProps,
} from './activity-views';
import {AccountsView} from './accounts-view';
import {OrdersView} from './orders-view';
import {createPortfolioRuntime, type PortfolioRuntime} from './portfolio-store';
import {PositionsView} from './positions-view';

/**
 * Binds a view component to the runtime as a slot view.
 * @param component View component.
 * @param runtime Portfolio runtime.
 * @param extra Extra props.
 * @return Slot view.
 */
function bind<P extends ActivityViewProps>(
  component: ComponentType<P>,
  runtime: PortfolioRuntime,
  extra: Omit<P, keyof ActivityViewProps>
): WebView {
  /**
   * Slot view rendering a portfolio tab.
   * @param props Slot props.
   * @return Element.
   */
  function PortfolioSlot(props: WebSlotProps) {
    return createElement(component, {...(extra as P), ctx: props.ctx, runtime});
  }
  return PortfolioSlot;
}

/** The portfolio feature. */
export const PORTFOLIO_FEATURE: WebFeature = {
  manifest: {
    id: 'portfolio',
    version: '1.0.0',
    owner: 'terminal-trading',
    displayName: 'Portfolio and risk',
    description:
      'Positions, orders, executions, history, audit log and kill switches reconciled with the execution engine.',
    uiSlots: ['bottom-panel'],
    requiredCapabilities: ['execution'],
    permissions: [
      'orders:read',
      'orders:intent',
      'risk:kill-switch',
      'audit:read',
    ],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'View-only over execution-engine state; removing it deletes no orders, positions or audit records.',
    },
  },
  activate(ctx) {
    const runtime = createPortfolioRuntime(ctx);
    const disposers: Disposer[] = [() => runtime.dispose()];
    disposers.push(
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.positions',
        title: 'Positions',
        order: 10,
        icon: 'wallet',
        view: bind(PositionsView, runtime, {}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.orders',
        title: 'Orders',
        order: 20,
        icon: 'list',
        view: bind(OrdersView, runtime, {mode: 'open'}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.fills',
        title: 'Executions',
        order: 30,
        icon: 'check',
        view: bind(FillsView, runtime, {}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.history',
        title: 'Order history',
        order: 40,
        icon: 'history',
        view: bind(OrdersView, runtime, {mode: 'history'}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.audit',
        title: 'Audit',
        order: 60,
        icon: 'shield',
        view: bind(AuditView, runtime, {}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.risk',
        title: 'Risk controls',
        order: 70,
        icon: 'octagon',
        view: bind(RiskView, runtime, {}),
      }),
      ctx.slots.contribute('bottom-panel', {
        id: 'portfolio.accounts',
        title: 'Accounts',
        order: 80,
        icon: 'wallet',
        view: bind(AccountsView, runtime, {}),
      })
    );
    return () => {
      for (const dispose of disposers.splice(0).reverse()) dispose();
    };
  },
};
