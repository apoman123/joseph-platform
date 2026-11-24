/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Head and shoulders pattern (also inverse head and
 * shoulders when drawn upside down).
 *
 * - Anchors: seven, placed in order: start, left shoulder, neck 1, head,
 *   neck 2, right shoulder, end (time/price).
 * - Geometry: a solid zig-zag through the anchors labelled "Left
 *   shoulder", "Head" and "Right shoulder"; a dashed neckline through
 *   neck 1 and neck 2, drawn across the pattern from the start anchor's x
 *   to the end anchor's x. Optional fill of the polygon start..end.
 * - Hit-testing: handles first, then any segment, then the neckline, then
 *   the inside of the polygon when filled.
 * - Handles: the seven anchors (handle index = anchor index).
 * - Style options: color, width, dash, fill (default off) and labels.
 */

import {
  createPatternTool,
  type PatternSpec,
  type Point,
} from '@joseph/drawing-core';

/**
 * Neckline through neck 1 and neck 2 spanning the start-to-end x range.
 * @param points Anchor pixels (start, LS, N1, head, N2, RS, end).
 * @return Neckline endpoints, or null when a point is missing.
 */
export function necklineSegment(
  points: readonly Point[]
): [Point, Point] | null {
  const [start, , neck1, , neck2, , end] = points;
  if (!start || !neck1 || !neck2 || !end) return null;
  const dx = neck2.x - neck1.x;
  if (dx === 0) return [neck1, neck2];
  const slope = (neck2.y - neck1.y) / dx;
  const at = (x: number): Point => ({x, y: neck1.y + slope * (x - neck1.x)});
  const left = Math.min(start.x, neck1.x, neck2.x);
  const right = Math.max(end.x, neck1.x, neck2.x);
  return [at(left), at(right)];
}

/** Pattern geometry: labels, neckline guide and optional fill. */
export const HEAD_AND_SHOULDERS_SPEC: PatternSpec = {
  labels: ['', 'Left shoulder', '', 'Head', '', 'Right shoulder', ''],
  fills: [[0, 1, 2, 3, 4, 5, 6]],
  guides: points => {
    const neckline = necklineSegment(points);
    return neckline ? [{points: neckline, dashed: true}] : [];
  },
  describe: anchors =>
    `Head ${anchors[3]?.price ?? '?'}, neckline ${anchors[2]?.price ?? '?'} to ${anchors[4]?.price ?? '?'}`,
};

/** Head and shoulders tool. */
export const headAndShouldersTool = createPatternTool(
  {
    id: 'head-and-shoulders',
    version: 1,
    displayName: 'Head and shoulders',
    group: 'patterns',
    icon: 'mountain-snow',
    anchorCount: 7,
  },
  HEAD_AND_SHOULDERS_SPEC,
  'off'
);
