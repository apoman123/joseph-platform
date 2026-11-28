/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Strategy feature: Python Strategy scripts (built-in, plugin
 * or owned) with declared inputs, closed-bar backtests and activation in
 * alert, paper or shadow mode. Automatic live trading cannot be enabled
 * from here.
 */

import {createElement} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {StrategyPanel} from './strategy-panel';
import {createStrategyController} from './strategy-store';

/** The strategy feature. */
export const STRATEGY_FEATURE: WebFeature = {
  manifest: {
    id: 'strategy',
    version: '2.0.0',
    owner: 'terminal-strategy',
    displayName: 'Strategies',
    description:
      'Backtest and activate Python Strategy scripts: the built-ins, installed plugins and your own scripts.',
    uiSlots: ['side-panel'],
    requiredCapabilities: ['strategy'],
    permissions: ['strategy:read', 'strategy:write'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Activations live on the server and keep running until paused or deleted.',
    },
  },
  activate(ctx) {
    const controller = createStrategyController(ctx);
    /**
     * Renders the strategy panel bound to the feature's controller.
     * @param props Slot props.
     * @return Element.
     */
    function StrategySlot(props: WebSlotProps) {
      return createElement(StrategyPanel, {ctx: props.ctx, controller});
    }
    const view: WebView = StrategySlot;
    return ctx.slots.contribute('side-panel', {
      id: 'strategy',
      title: 'Strategy',
      order: 30,
      icon: 'workflow',
      view,
    });
  },
};
