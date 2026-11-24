/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview ABCD pattern (AB = CD measured move).
 *
 * - Anchors: four, placed in order A, B, C, D (time/price).
 * - Geometry: solid legs A-B-C-D; dashed connectors A-C and B-D carry the
 *   ratios computed from the anchor prices with exact decimals (pure
 *   geometry): BC/AB = |C-B| / |B-A| (on A-C) and CD/BC = |D-C| / |C-B|
 *   (on B-D).
 * - Hit-testing: handles first, then any leg or connector within the
 *   tolerance.
 * - Handles: the four anchors (handle index = anchor index).
 * - Style options: color, width, dash and labels (letters and ratios).
 *   No fill.
 */

import {createPatternTool, type PatternSpec} from '@joseph/drawing-core';

/** Pattern geometry: labels, BC/AB on A-C and CD/BC on B-D. */
export const ABCD_SPEC: PatternSpec = {
  labels: ['A', 'B', 'C', 'D'],
  ratios: [
    {leg: [1, 2], of: [0, 1], along: [0, 2]},
    {leg: [2, 3], of: [1, 2], along: [1, 3]},
  ],
};

/** ABCD pattern tool. */
export const abcdPatternTool = createPatternTool(
  {
    id: 'abcd-pattern',
    version: 1,
    displayName: 'ABCD pattern',
    group: 'patterns',
    icon: 'route',
    anchorCount: 4,
  },
  ABCD_SPEC
);
