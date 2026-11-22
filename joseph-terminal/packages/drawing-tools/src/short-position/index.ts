/**
 * @fileoverview Short position planner: entry, target and stop boxes with
 * reward/risk. A planning aid only; it never places orders.
 */
import {createPositionTool} from '@joseph/drawing-core';

/** Short position tool. */
export const shortPositionTool = createPositionTool('short', {
  id: 'short-position',
  version: 1,
  displayName: 'Short position',
  group: 'trading',
  icon: 'arrow-down-right',
  anchorCount: 1,
});
