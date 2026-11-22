/**
 * @fileoverview Long position planner: entry, target and stop boxes with
 * reward/risk. A planning aid only; it never places orders.
 */
import {createPositionTool} from '@joseph/drawing-core';

/** Long position tool. */
export const longPositionTool = createPositionTool('long', {
  id: 'long-position',
  version: 1,
  displayName: 'Long position',
  group: 'trading',
  icon: 'arrow-up-right',
  anchorCount: 1,
});
