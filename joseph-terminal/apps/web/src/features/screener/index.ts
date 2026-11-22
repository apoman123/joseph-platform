/**
 * @fileoverview Screener feature: backend qualification results with every
 * gate's observed value, threshold and outcome.
 */
import {createElement} from 'react';
import type {WebFeature, WebSlotProps} from '../../shell/react-bindings';
import {ScreenerPanel} from './screener-panel';

/**
 * Slot view for the screener.
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
      'Qualified, rejected and missing-data perpetuals per exact venue with gate details.',
    uiSlots: ['side-panel'],
    requiredCapabilities: ['market'],
    permissions: ['market:read'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {
      notes:
        'Stateless view over backend qualification results; removal deletes no data.',
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
