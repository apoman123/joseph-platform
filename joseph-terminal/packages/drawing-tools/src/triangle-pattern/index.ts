/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Triangle pattern (symmetrical, ascending or descending
 * consolidation).
 *
 * - Anchors: four, placed in order A, B, C, D as a zig-zag (time/price).
 * - Geometry: solid zig-zag A-B-C-D; boundary lines through A-C and B-D.
 *   Each boundary starts at its first anchor (A or B) and runs to the apex
 *   (the boundaries' intersection) when the apex lies right of the last
 *   anchor and within three pattern widths of it; otherwise it ends at the
 *   x of the right-most anchor. The area between the boundaries is
 *   optionally filled.
 * - Hit-testing: handles first, then any zig-zag segment, then either
 *   boundary, then the inside of the filled area.
 * - Handles: the four anchors (handle index = anchor index).
 * - Style options: color, width, dash, fill (default on) and labels.
 */

import {
  createPatternTool,
  lineIntersection,
  type PatternSpec,
  type Point,
} from '@joseph/drawing-core';

/**
 * Furthest the apex may lie beyond the right-most anchor, in pattern widths.
 * A pattern width is the anchors' x span; past the limit the boundaries stop
 * at the right-most anchor's x.
 */
const APEX_LIMIT_WIDTHS = 3;

/**
 * Returns the boundary lines of the triangle.
 * @param points Anchor pixels A, B, C, D.
 * @return Boundaries through A-C and B-D in CSS pixels, each from its first
 *     anchor to the end x, or null when an anchor is missing.
 */
export function triangleBoundaries(
  points: readonly Point[]
): [[Point, Point], [Point, Point]] | null {
  const [a, b, c, d] = points;
  if (!a || !b || !c || !d) return null;
  const xs = [a.x, b.x, c.x, d.x];
  const first = Math.min(...xs);
  const last = Math.max(...xs);
  // At least 1 px, so anchors stacked on one x still allow an apex.
  const width = Math.max(1, last - first);
  const apex = lineIntersection(a, c, b, d);
  const endX =
    apex && apex.x > last && apex.x <= last + APEX_LIMIT_WIDTHS * width
      ? apex.x
      : last;
  /**
   * Extends a line from `from` through `through` to `endX`.
   * @param from Start anchor, in CSS pixels.
   * @param through Second anchor, in CSS pixels.
   * @return Segment from `from` to `endX`; `[from, through]` when the line
   *     is vertical.
   */
  const extend = (from: Point, through: Point): [Point, Point] => {
    const dx = through.x - from.x;
    if (dx === 0) return [from, through];
    const slope = (through.y - from.y) / dx;
    return [from, {x: endX, y: from.y + slope * (endX - from.x)}];
  };
  return [extend(a, c), extend(b, d)];
}

/** Pattern geometry: labels, boundary guides and the filled area. */
export const TRIANGLE_PATTERN_SPEC: PatternSpec = {
  labels: ['A', 'B', 'C', 'D'],
  guides: points =>
    (triangleBoundaries(points) ?? []).map(line => ({
      points: line,
      dashed: false,
    })),
  fillShapes: points => {
    const lines = triangleBoundaries(points);
    if (!lines) return [];
    const [[a, upperEnd], [b, lowerEnd]] = lines;
    return [[a, upperEnd, lowerEnd, b]];
  },
  describe: anchors =>
    `Triangle ${anchors[0]?.price ?? '?'} to ${anchors[3]?.price ?? '?'}`,
};

/** Triangle pattern tool. */
export const trianglePatternTool = createPatternTool(
  {
    id: 'triangle-pattern',
    version: 1,
    displayName: 'Triangle pattern',
    group: 'patterns',
    icon: 'triangle-right',
    anchorCount: 4,
  },
  TRIANGLE_PATTERN_SPEC,
  'on'
);
