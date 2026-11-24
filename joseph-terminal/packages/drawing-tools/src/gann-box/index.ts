/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Gann box (Gann square).
 *
 * - Anchors: `[A, B]`, opposite corners of the box. A is the origin corner
 *   that the ratios are measured from.
 * - Geometry (pixels): vertical lines at `A.x + (B.x - A.x) * r` (time
 *   divisions) and horizontal lines at `A.y + (B.y - A.y) * r` (price
 *   divisions) for r = 0, 0.25, 0.382, 0.5, 0.618, 0.75, 1, each spanning
 *   the box; both corner-to-corner diagonals. Ratio 0 and 1 lines are the
 *   border.
 * - Hit-testing: handles first, then any grid line or diagonal, then the
 *   inside when filled.
 * - Handles: A and B (handle index = anchor index).
 * - Style options: color, width, dash, `fill_color` (on by default),
 *   `show_labels` (time ratios along the top edge, price ratios along the
 *   left edge).
 */

import {
  anchorPoints,
  applyStroke,
  distanceToSegment,
  drawHandles,
  drawLabel,
  hitHandles,
  pointInRect,
  rectFromPoints,
  strokePath,
  type Point,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Division ratios of the box. */
export const GANN_BOX_LEVELS = [
  '0',
  '0.25',
  '0.382',
  '0.5',
  '0.618',
  '0.75',
  '1',
];

/** Grid of a Gann box in CSS pixels. */
export interface GannBoxGrid {
  /** Time divisions (vertical lines): ratio and x. */
  vertical: Array<{level: string; x: number}>;
  /** Price divisions (horizontal lines): ratio and y. */
  horizontal: Array<{level: string; y: number}>;
}

/**
 * Computes the division lines measured from the origin corner.
 * @param origin Origin corner (anchor A), in CSS pixels.
 * @param corner Opposite corner (anchor B), in CSS pixels.
 * @param levels Ratios as decimal strings (`GANN_BOX_LEVELS` by default).
 * @return Line positions in `levels` order.
 */
export function gannBoxGrid(
  origin: Point,
  corner: Point,
  levels: readonly string[] = GANN_BOX_LEVELS
): GannBoxGrid {
  return {
    vertical: levels.map(level => ({
      level,
      x: origin.x + (corner.x - origin.x) * Number(level),
    })),
    horizontal: levels.map(level => ({
      level,
      y: origin.y + (corner.y - origin.y) * Number(level),
    })),
  };
}

/**
 * Returns every line segment of the box: grid lines, then the two diagonals.
 * @param a Origin corner, in CSS pixels.
 * @param b Opposite corner, in CSS pixels.
 * @return Segments in CSS pixels.
 */
function segments(a: Point, b: Point): Array<[Point, Point]> {
  const grid = gannBoxGrid(a, b);
  const top = Math.min(a.y, b.y);
  const bottom = Math.max(a.y, b.y);
  const left = Math.min(a.x, b.x);
  const right = Math.max(a.x, b.x);
  return [
    ...grid.vertical.map(({x}): [Point, Point] => [
      {x, y: top},
      {x, y: bottom},
    ]),
    ...grid.horizontal.map(({y}): [Point, Point] => [
      {x: left, y},
      {x: right, y},
    ]),
    [a, b],
    [
      {x: a.x, y: b.y},
      {x: b.x, y: a.y},
    ],
  ];
}

/** Gann box tool. */
export const gannBoxTool: ToolPlugin = {
  manifest: {
    id: 'gann-box',
    version: 1,
    displayName: 'Gann box',
    group: 'gann',
    icon: 'grid-3x3',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    // A hex alpha byte on the `#rrggbb` user color: 0x14, about 8 % opacity.
    fill_color: `${palette.user}14`,
    show_labels: true,
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return;
    const rect = rectFromPoints(a, b);
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
    }
    applyStroke(ctx, doc.style, state);
    for (const segment of segments(a, b)) {
      strokePath(ctx, segment);
    }
    if (doc.style.show_labels !== false) {
      const grid = gannBoxGrid(a, b);
      ctx.font = `11px ${state.palette.fontFamily}`;
      for (const {level, x} of grid.vertical) {
        const width = ctx.measureText(level).width;
        drawLabel(
          ctx,
          level,
          x - width / 2,
          rect.y - 10,
          doc.style.color,
          state
        );
      }
      for (const {level, y} of grid.horizontal) {
        const width = ctx.measureText(level).width;
        drawLabel(ctx, level, rect.x - width - 6, y, doc.style.color, state);
      }
    }
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    const handle = hitHandles([a, b], point, tolerance);
    if (handle) return handle;
    if (
      segments(a, b).some(
        ([p, q]) => distanceToSegment(point, p, q) <= tolerance
      )
    ) {
      return {part: 'body'};
    }
    return doc.style.fill_color && pointInRect(point, rectFromPoints(a, b))
      ? {part: 'body'}
      : null;
  },
  describe: doc =>
    `Gann box ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
