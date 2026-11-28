/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Screener feature: market scans of built-in and owned
 * filter scripts combined with AND, run on the market engine.
 */

import {createElement} from 'react';
import type {WebFeature, WebSlotProps} from '../../shell/react-bindings';
import {ScreenerPanel} from './screener-panel';

/**
 * Renders the screener panel with the slot's context.
 * @param props Slot props.
 * @return Element.
 */
function ScreenerSlot(props: WebSlotProps) {
  return createElement(ScreenerPanel, {ctx: props.ctx});
}

/** The screener feature. */
export const SCREENER_FEATURE: WebFeature = {
  manifest: {
    id: 'screener',
    version: '1.0.0',
    owner: 'terminal-trading',
    displayName: 'Screener',
    description:
      'Market screener: built-in and owned filter scripts combined with AND, scanned on real snapshots and closed bars.',
    uiSlots: ['side-panel'],
    requiredCapabilities: ['market'],
    permissions: ['market:read'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Scans run on the market engine; removal deletes only saved screener presets.',
    },
  },
  activate(ctx) {
    return ctx.slots.contribute('side-panel', {
      id: 'screener',
      title: 'Screener',
      order: 20,
      icon: 'filter',
      view: ScreenerSlot,
    });
  },
};
