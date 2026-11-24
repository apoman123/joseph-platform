/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Strategy feature: visual builder for the rule AST (abstract
 * syntax tree) with server validation, immutable versions, backtests and
 * activation in alert, paper or shadow mode. Automatic live trading cannot
 * be enabled from here.
 */

import {createElement} from 'react';
import type {
  WebFeature,
  WebSlotProps,
  WebView,
} from '../../shell/react-bindings';
import {StrategyPanel} from './strategy-panel';
import {createStrategyEditor} from './strategy-store';

/** The strategy feature. */
export const STRATEGY_FEATURE: WebFeature = {
  manifest: {
    id: 'strategy',
    version: '1.0.0',
    owner: 'terminal-strategy',
    displayName: 'Strategy builder',
    description:
      'Build typed entry rules, validate them on the server, save versions, backtest and activate without code.',
    uiSlots: ['side-panel'],
    requiredCapabilities: ['strategy'],
    permissions: ['strategy:read', 'strategy:write'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Saved strategies live on the server and keep running according to their activation; the local draft stays in feature settings.',
    },
  },
  activate(ctx) {
    const editor = createStrategyEditor(ctx);
    /**
     * Renders the strategy panel bound to the feature's editor.
     * @param props Slot props.
     * @return Element.
     */
    function StrategySlot(props: WebSlotProps) {
      return createElement(StrategyPanel, {ctx: props.ctx, editor});
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
